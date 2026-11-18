"""Explicit native-evidence recovery, with no dispatch or Sender dependency."""
import json

from app import shared_action_lifecycle
from app.action_storage import (
    NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL, NATIVE_EXTERNAL_ACTION_RESULT_SQL,
    REPLY_ATTEMPT_COMMON_SQL, candidate_action_attempt_row, insert_error_action, update_action,
)
from app.agent_contracts import ProposedAction
from app.dws_client import message_validation_rejected
from app.external_action_identity import expected_external_action
from app.store import NATIVE_REPLY_TASK_CURRENT_SQL
from app.task_entity_storage import update_task_cas
from app.wechat_reconciliation import reconcile_failed_wechat_attempt


def _completed_review(store, db, candidate_id, review_id):
    candidate, review = store._review_context(db, candidate_id, review_id)
    valid = db.execute("""select 1 from runs consumer join runs audit on audit.parent_id=consumer.id
        where consumer.id=? and audit.id=? and consumer.status='completed'
        and audit.status='completed' and consumer.role='consumer' and audit.role='audit'
        and consumer.task_id=audit.task_id and consumer.execution_generation=audit.execution_generation""",
        (candidate_id, review['audit_run_id'])).fetchone()
    if valid is None:
        raise ValueError('reply_review_not_completed')
    return candidate


def _reconcile_validation_rejection(store, attempt):
    snapshot = store.get_reply_task_by_trigger_message_id(attempt.trigger_message_id, channel=attempt.channel)
    if snapshot is None or snapshot.conversation_id != attempt.conversation_id:
        raise ValueError('reply_attempt_task_mismatch')
    with store._immediate_write_transaction() as db:
        task = db.execute(f'select * from ({NATIVE_REPLY_TASK_CURRENT_SQL}) where id=?', (snapshot.id,)).fetchone()
        original = db.execute(f'select * from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?', (attempt.id,)).fetchone()
        if (task is None or task['channel'] != 'dingtalk' or task['locked_at']
                or task['status'] not in ('failed', 'done')
                or task['execution_generation'] != snapshot.execution_generation
                or original is None or original['channel'] != task['channel']
                or original['conversation_id'] != task['conversation_id']
                or original['trigger_message_id'] != task['trigger_message_id']):
            raise ValueError('reply_task_not_reconcilable')
        review = store.current_reviewed_candidate(task['id'], task['execution_generation'])
        if review is None or json.loads(review['candidate_json']).get('outcome') != 'no_action':
            raise ValueError('reply_no_action_review_missing')
        _completed_review(store, db, review['candidate_id'], review['review_id'])
        store._check_historical_runtime_refusal(db, task['business_object_key'])
        rows = db.execute(f"""select receipt.* from ({NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL}) receipt
            join runs owner on owner.id=receipt.execution_id
            where owner.task_id=? and owner.execution_generation=? order by receipt.id""",
            (task['id'], task['execution_generation'])).fetchall()
        if not rows:
            raise ValueError('reply_validation_receipt_missing')
        changes = []
        for row in rows:
            prior = candidate_action_attempt_row(row)
            execution = store._native_execution(db, prior['execution_id'])
            if (execution is None or execution['status'] not in ('failed', 'uncertain')
                    or execution['lease_owner'] or prior['provider_receipt_id']
                    or prior['execution_id'] >= review['candidate_id']):
                raise ValueError('reply_prior_execution_not_reconcilable')
            candidate = _completed_review(store, db, prior['execution_id'], execution['review_id'])
            selection = store._native_selection(db, prior['execution_id']) if execution['selection_id'] else None
            actions = shared_action_lifecycle.reviewed_action_steps(db, candidate, selection=selection)
            if not 0 <= prior['action_index'] < len(actions):
                raise ValueError('reply_prior_action_missing')
            action = ProposedAction.model_validate(actions[prior['action_index']])
            identity = expected_external_action(action, action_index=prior['action_index'],
                business_object_key=task['business_object_key'])
            if (action.capability != 'dingtalk-chat'
                    or identity['external_action_key'] != prior['external_action_key']
                    or json.loads(prior['target']) != action.target
                    or db.execute(f'select 1 from ({NATIVE_EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?',
                        (prior['external_action_key'],)).fetchone() is not None):
                raise ValueError('reply_prior_action_identity_or_effect_conflict')
            result = json.loads(prior['result_json'])
            if prior['status'] == 'confirmed_no_effect':
                if (result.get('reconciliation_kind') != 'dws_validation_rejection'
                        or result.get('completion_review_id') != review['review_id']
                        or result.get('completion_candidate_id') != review['candidate_id']):
                    raise ValueError('reply_no_effect_receipt_mismatch')
                continue
            error = result.get('provider_error')
            if (task['status'] != 'failed' or prior['status'] not in ('failed', 'uncertain')
                    or result.get('exception_type') != 'DwsError'
                    or result.get('reason') != 'provider_exception' or not isinstance(error, dict)
                    or not message_validation_rejected(error, server_key=result.get('source', ''))):
                raise ValueError('reply_not_a_proven_validation_rejection')
            changes.append((prior, {
                'reconciliation_kind': 'dws_validation_rejection', 'previous_result': result,
                'completion_review_id': review['review_id'], 'completion_candidate_id': review['candidate_id'],
                'execution_generation': task['execution_generation'],
            }))
        now = db.execute('select current_timestamp').fetchone()[0]
        for prior, result in changes:
            shared_action_lifecycle.record_reviewed_action_outcome(db, attempt=prior,
                status='confirmed_no_effect', result_json=json.dumps(result, ensure_ascii=False, sort_keys=True),
                provider_receipt_id=None, agent_run_id=prior['execution_id'], updated_at=now)
        if task['status'] == 'failed':
            updated = update_task_cas(db, task_id=task['id'], expected={
                'status': 'failed', 'error': task['error'], 'execution_generation': task['execution_generation']},
                changes={'status': 'done', 'error': '', 'locked_at': None, 'available_at': '', 'updated_at': now})
            if updated is None:
                raise ValueError('reply_reconciliation_conflict')
            insert_error_action(db, {'conversation_id': task['conversation_id'],
                'message_id': task['trigger_message_id'], 'kind': 'reply_validation_reconciled',
                'detail': json.dumps({'review_id': review['review_id'], 'action_receipt_ids': [r['id'] for r in rows]}),
                'resolution': 'native parameter validation rejected before dispatch; current no_action approved'},
                resolved_at_now=True)
            if original['send_status'] == 'failed' and not original['resolved_at']:
                update_action(db, 'reply_attempts', attempt.id, {
                    'resolved_at': now,
                    'resolution': '原生参数校验拒绝，已确认未派发；后续审核无需动作，任务已结案。'
                        + ' Action receipts: ' + ','.join(str(r['id']) for r in rows),
                })
            store._compact_settled_reply_task_input(db, task['id'])
        return {'outcome': 'done', 'task_id': task['id'], 'reconciliation_status': 'confirmed_no_effect'}


def reconcile_reply_attempt(store, attempt_id, **arguments):
    attempt = store.get_reply_attempt(attempt_id)
    if attempt is None:
        raise ValueError('reply_attempt_missing')
    if attempt.channel == 'wechat':
        return reconcile_failed_wechat_attempt(store, attempt_id, **arguments)
    if attempt.channel == 'dingtalk' and not arguments:
        return _reconcile_validation_rejection(store, attempt)
    raise ValueError('reply_reconciliation_not_applicable')
