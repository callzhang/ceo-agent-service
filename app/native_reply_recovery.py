"""Read-only recovery for a native reply rejected by its original local sender guard."""
import hashlib
import json

from app import native_prompt_input, shared_action_lifecycle
from app.action_storage import NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL, NATIVE_EXTERNAL_ACTION_RESULT_SQL, candidate_action_attempt_row
from app.agent_contracts import ProposedAction
from app.dingtalk_models import DingTalkConversation
from app.dws_client import DwsClient, DwsError
from app.external_action_identity import expected_external_action
from app.service_message_sender import agent_message_delivery_key
from app.store import NATIVE_REPLY_TASK_CURRENT_SQL


def _bound_prior(store, db, task, action_attempt_id):
    prior = candidate_action_attempt_row(db.execute(
        f'select * from ({NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL}) where id=?', (action_attempt_id,)).fetchone())
    if prior is None:
        raise ValueError('native_reply_attempt_missing')
    execution = store._native_execution(db, prior['execution_id'])
    candidate = store._adopted_candidate_row(db, prior['execution_id'])
    review = db.execute('''select review.*, audit.status as audit_status, consumer.status as consumer_status
        from run_adopted_reviews review join runs audit on audit.id=review.audit_run_id
        join runs consumer on consumer.id=review.consumer_run_id
        where review.id=? and audit.parent_id=consumer.id and audit.task_id=consumer.task_id
        and audit.execution_generation=consumer.execution_generation''',
        (execution['review_id'],)).fetchone() if execution else None
    if (candidate is None or candidate['task_id'] != task['id']
            or candidate['business_object_key'] != task['business_object_key']
            or execution['status'] not in ('uncertain', 'failed') or execution['lease_owner']
            or execution['selection_id'] is not None or review is None
            or review['consumer_run_id'] != candidate['id'] or review['decision'] != 'approve'
            or review['candidate_digest'] != candidate['candidate_digest']
            or review['audit_status'] != 'completed' or review['consumer_status'] != 'completed'):
        raise ValueError('native_reply_original_review_mismatch')
    actions = json.loads(candidate['candidate_json']).get('proposal', {}).get('actions', [])
    if not 0 <= prior['action_index'] < len(actions):
        raise ValueError('native_reply_original_action_missing')
    action = ProposedAction.model_validate(actions[prior['action_index']])
    identity = expected_external_action(action, business_object_key=task['business_object_key'])
    if (action.capability != 'dingtalk-chat' or action.operation != 'reply_to_message'
            or identity['external_action_key'] != prior['external_action_key']
            or json.loads(prior['target']) != action.target or prior['provider_receipt_id']
            or db.execute(f'select 1 from ({NATIVE_EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?',
                          (prior['external_action_key'],)).fetchone() is not None):
        raise ValueError('native_reply_original_effect_conflict')
    return prior, dict(candidate), action, dict(review)


def reconcile_native_reply_preflight(store, attempt, *, action_attempt_id, execution_generation):
    task = store.get_reply_task_by_trigger_message_id(attempt.trigger_message_id, channel='dingtalk')
    if task is None or task.conversation_id != attempt.conversation_id:
        raise ValueError('native_reply_task_mismatch')
    with store._connect() as db:
        snapshot = dict(db.execute(f'select * from ({NATIVE_REPLY_TASK_CURRENT_SQL}) where id=?', (task.id,)).fetchone())
        if (snapshot['status'] != 'failed' or snapshot['locked_at']
                or snapshot['execution_generation'] != execution_generation):
            raise ValueError('native_reply_task_not_reconcilable')
        store._check_historical_runtime_refusal(db, snapshot['business_object_key'])
        prior, candidate, action, review = _bound_prior(store, db, snapshot, action_attempt_id)
    result = json.loads(prior['result_json'])
    if prior['status'] == 'confirmed_no_effect' and result.get('reconciliation_kind') == 'native_reply_sender_preflight':
        return {'outcome': 'reconciled', 'task_id': task.id, 'action_attempt_id': prior['id']}
    if (prior['status'] != 'uncertain' or result.get('exception_type') != 'DwsError'
            or result.get('reason') != 'provider_exception' or result.get('provider_error') != {}
            or result.get('pre_action_failure') is not False or result.get('source')
            or result.get('source_code') or 'provider_result' in result
            or result.get('provider_error_message', 'missing trigger senderOpenDingTalkId for native reply')
                != 'missing trigger senderOpenDingTalkId for native reply'):
        raise ValueError('native_reply_not_a_local_sender_failure')
    runtimes = store.list_agent_runtime_attempts(prior['execution_id'])
    if len(runtimes) != 1 or runtimes[0].status != 'completed' or runtimes[0].parent_id != prior['execution_id']:
        raise ValueError('native_reply_original_input_unavailable')
    try:
        saved = native_prompt_input.read_attempt_input(runtimes[0])
    except (OSError, UnicodeError) as exc:
        raise ValueError('native_reply_original_input_unavailable') from exc
    prompt = saved['task_prompt'] if saved else ''
    _, separator, source = prompt.partition('\nOriginal trigger\n')
    if not separator:
        raise ValueError('native_reply_original_trigger_unavailable')
    trigger, end = json.JSONDecoder().raw_decode(source)
    if not source[end:].startswith('\n\nRecent conversation context\n'):
        raise ValueError('native_reply_original_trigger_boundary_mismatch')
    raw = trigger.get('raw_payload') if isinstance(trigger, dict) else None
    if (not isinstance(trigger, dict) or not isinstance(raw, dict)
            or not isinstance(raw.get('senderId'), str) or not raw['senderId'].strip()
            or trigger.get('task_id') != task.id
            or trigger.get('channel') != 'dingtalk' or trigger.get('sender_open_dingtalk_id')
            or not trigger.get('raw_payload', {}).get('senderId')
            or action.target != {'conversation_id': trigger.get('conversation_id'), 'message_id': trigger.get('message_id')}):
        raise ValueError('native_reply_original_trigger_mismatch')
    delivery_key = agent_message_delivery_key(business_object_key=task.business_object_key,
        action_identity=action.action_identity, execution_generation=candidate['execution_generation'],
        proposal_revision=candidate['proposal_revision'])
    prepared = store.get_outbound_postfix('dingtalk', delivery_key)
    if (prepared is None or prepared.final_body != action.payload.get('content')
            or store.get_outbound_postfix_receipt('dingtalk', delivery_key) is not None):
        raise ValueError('native_reply_preparation_or_receipt_conflict')
    dws = DwsClient()
    conversation = DingTalkConversation(open_conversation_id=action.target['conversation_id'],
        title=task.conversation_title, single_chat=task.single_chat, unread_point=0)
    current = dws.read_message_by_id(conversation, action.target['message_id'])
    if (current is None or current.open_conversation_id != trigger['conversation_id']
            or current.open_message_id != trigger['message_id'] or current.single_chat != trigger.get('single_chat')
            or current.sender_name != trigger.get('sender') or current.create_time != trigger.get('create_time')
            or current.content != trigger.get('text')
            or current.raw_payload.get('senderId') != trigger['raw_payload']['senderId']
            or not current.sender_open_dingtalk_id):
        raise ValueError('native_reply_current_source_mismatch')
    principal = dws.get_current_user_id()
    try:
        readback = dws.reconcile_reply_to_trigger(conversation, current, prepared.final_body)
    except DwsError as exc:
        raise ValueError('native_reply_readback_unavailable') from exc
    proof = readback.get('readbackVerification', {}) if isinstance(readback, dict) else {}
    if (not principal or not isinstance(readback, dict)
            or readback.get('errorCode') != 'complete_readback_no_exact_match'
            or proof.get('completeWindowReadback') is not True or proof.get('exactMatchCount') != 0
            or proof.get('conversationId') != current.open_conversation_id
            or proof.get('quotedMessageId') != current.open_message_id):
        raise ValueError('native_reply_no_send_readback_missing')
    evidence = {'reconciliation_kind': 'native_reply_sender_preflight', 'previous_result': result,
        'runtime_attempt_id': runtimes[0].id, 'native_task_input_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
        'original_candidate_id': candidate['id'], 'original_review_id': review['id'],
        'execution_generation': execution_generation, 'principal_user_id': principal, 'readback': readback}
    with store._immediate_write_transaction() as db:
        latest = dict(db.execute(f'select * from ({NATIVE_REPLY_TASK_CURRENT_SQL}) where id=?', (task.id,)).fetchone())
        if latest != snapshot:
            raise ValueError('native_reply_task_changed_during_readback')
        checked, _, _, _ = _bound_prior(store, db, latest, action_attempt_id)
        if checked != prior or store.get_outbound_postfix_receipt('dingtalk', delivery_key) is not None:
            raise ValueError('native_reply_receipt_changed_during_readback')
        store._check_historical_runtime_refusal(db, latest['business_object_key'])
        now = db.execute('select current_timestamp').fetchone()[0]
        shared_action_lifecycle.record_reviewed_action_outcome(db, attempt=prior, status='confirmed_no_effect',
            result_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True), provider_receipt_id=None,
            agent_run_id=prior['execution_id'], updated_at=now)
    return {'outcome': 'reconciled', 'task_id': task.id, 'action_attempt_id': prior['id']}
