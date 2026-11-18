"""Recover retained parameter-validation failures without dispatching anything."""
import json

import pytest

from app.agent_contracts import ConsumerAgentResult, AuditAgentResult
from app.dws_client import DwsError
from app.store import AutoReplyStore, AgentRole
from app.reviewed_candidates import candidate_digest
from app.system_executor import SystemExecutor, ActionOutcome
from tests.wechat.support import review_candidate
from tests.test_system_executor import _candidate
from tests.test_console_web_api import _client


def _failed_then_reviewed(tmp_path, *, stage='tool_validation', reason='invalid_request',
                          approved=True, no_action=True, provider_sent=False):
    store = AutoReplyStore(tmp_path / 'worker.sqlite3')
    store.enqueue_reply_task(channel='dingtalk', conversation_id='queue-alias',
        conversation_title='Example', single_chat=True, trigger_message_id='trigger',
        trigger_create_time='2026-11-18T01:00:00Z', trigger_sender='Example', trigger_text='Question')
    task = store.claim_reply_tasks(1)[0]
    result = _candidate(actions=[{
        'description': 'Reply', 'action_identity': 'reply', 'capability': 'dingtalk-chat',
        'operation': 'reply_to_message',
        'target': {'conversation_id': 'native-chat', 'message_id': 'trigger'},
        'payload': {'content': 'Prepared reply'},
    }])
    candidate, review = review_candidate(store, task, result)
    provider = {'reason': reason, 'stage': stage, 'server_key': 'chat',
                'server_error_code': 'PARAM_ERROR', 'category': 'api', 'message': 'invalid argument'}

    class HistoricalHandler:
        def dispatch(self, *args, **kwargs):
            if provider_sent:
                return ActionOutcome('verified', {'provider_result': {'success': True,
                    'result': {'openMessageId': 'sent'}}, 'verification': {'state': 'sent'}})
            # The old adapter failed to set pre_action_failure on this response.
            raise DwsError('validation rejected', server_key='chat', provider_error=provider)
        def reconcile(self, *args, **kwargs):
            pytest.fail('the historical fixture has no prior action')

    execution = SystemExecutor(store, handlers={('dingtalk-chat', 'reply_to_message'):
        HistoricalHandler()}).execute(task, candidate['id'], review['id'])
    assert execution.outcome == ('executed' if provider_sent else 'failed')
    later = result
    if no_action:
        later = ConsumerAgentResult.model_validate({
            'outcome': 'no_action', 'summary': 'Principal has replied; no further response.',
            'proposal': None, 'error': {'code': '', 'retryable': False, 'authorization_required': False},
            'risk': 'low', 'confidence': 1.0, 'rule_coverage': 1.0, 'information_completeness': 1.0})
    consumer = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=review['audit_run_id'],
        operation_id='', owner='consumer').run
    store.complete_agent_run(consumer.id, later.model_dump(mode='json'), owner='consumer')
    current = store.persist_review_candidate(task, consumer, later)
    audit = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id='audit', owner='audit').run
    body = AuditAgentResult(outcome='approve' if approved else 'return', summary='Reviewed',
        proposal_revision=1, candidate_digest=candidate_digest(later), evidence_refs=(),
        feedback=None if approved else {'rule': 'Review', 'observation': 'Not ready',
            'requested_revision': 'Revise'}, error={'code': '', 'retryable': False},
        risk='low', confidence=1.0, rule_coverage=1.0, information_completeness=1.0).model_dump(mode='json')
    store.complete_agent_run(audit.id, body, owner='audit')
    verdict = store.record_candidate_review(current['id'], audit.id, body)
    store.fail_reply_task(task.id, 'external_action_uncertain',
                          expected_execution_generation=task.execution_generation)
    attempt = store.record_reply_attempt(channel='dingtalk', conversation_id=task.conversation_id,
        conversation_title=task.conversation_title, trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender, trigger_text=task.trigger_text,
        action='send_reply', sensitivity_kind='normal', send_status='failed')
    return store, task, attempt, candidate, current, verdict


def test_history_retry_reconciles_old_validation_after_no_action_review(tmp_path):
    store, task, attempt, old, current, verdict = _failed_then_reviewed(tmp_path)
    original_attempt = store.get_reply_attempt(attempt)
    before = store.list_candidate_action_attempts(old['id'])[0]
    with _client(tmp_path) as client:
        response = client.post(f'/api/console/history/{attempt}/retry')
        assert response.status_code == 200, response.json()
        assert response.json()['item']['outcome'] == 'done'
        again = client.post(f'/api/console/history/{attempt}/retry')
        assert again.status_code == 200
    assert store.get_reply_task(task.id).status == 'done'
    assert store.get_reply_task(task.id).execution_generation == task.execution_generation
    assert store.get_reply_attempt(attempt).send_status == original_attempt.send_status == 'failed'
    assert store.get_candidate_execution(old['id'])['status'] == 'uncertain'
    assert store.get_reply_attempt(attempt).resolved_at
    after = store.list_candidate_action_attempts(old['id'])[0]
    assert after['status'] == 'confirmed_no_effect'
    proof = json.loads(after['result_json'])
    assert proof['previous_result'] == json.loads(before['result_json'])
    assert proof['completion_review_id'] == verdict['id']
    assert proof['completion_candidate_id'] == current['id']
    assert store.get_candidate_external_action(after['external_action_key']) is None
    from app.quality_gate import scan_hourly_quality
    assert not [i for i in scan_hourly_quality(store.path).violations
                if i.source in ('reply_tasks', 'reply_attempts')]


@pytest.mark.parametrize('options', [
    {'stage': 'dispatch'}, {'reason': 'network_error'}, {'approved': False}, {'no_action': False},
    {'provider_sent': True},
])
def test_history_retry_preserves_unknown_or_unreviewed_work(tmp_path, options):
    store, task, attempt, old, *_ = _failed_then_reviewed(tmp_path, **options)
    before = store.list_candidate_action_attempts(old['id'])
    with _client(tmp_path) as client:
        assert client.post(f'/api/console/history/{attempt}/retry').status_code == 409
    assert store.get_reply_task(task.id).status == 'failed'
    assert store.list_candidate_action_attempts(old['id']) == before


@pytest.mark.parametrize('change', ['generation', 'active_task', 'old_target', 'audit_failed', 'exception_type', 'missing_error'])
def test_recovery_refuses_changed_identity_or_incomplete_proof(tmp_path, change):
    from app import task_entity_storage
    store, task, attempt, old, current, verdict = _failed_then_reviewed(tmp_path)
    with store._connect() as db:
        if change == 'generation':
            task_entity_storage.update_task_cas(db, task_id=task.id,
                expected={'status': 'failed'}, changes={'execution_generation': 'new-generation'})
        elif change == 'active_task':
            task_entity_storage.update_task_cas(db, task_id=task.id,
                expected={'status': 'failed'}, changes={'status': 'processing'})
        elif change == 'old_target':
            db.execute('update action_receipts set target=? where execution_run_id=?',
                       (json.dumps({'conversation_id': 'other', 'message_id': 'trigger'}), old['id']))
        elif change == 'audit_failed':
            db.execute("update runs set status='failed' where id=?", (verdict['audit_run_id'],))
        else:
            prior = store.list_candidate_action_attempts(old['id'])[0]
            value = json.loads(prior['result_json'])
            if change == 'exception_type':
                value['exception_type'] = 'TimeoutError'
            if change == 'missing_error':
                value.pop('provider_error')
            db.execute('update action_receipts set outcome_json=? where execution_run_id=?',
                       (json.dumps(value), old['id']))
    before = store.list_candidate_action_attempts(old['id'])
    with _client(tmp_path) as client:
        assert client.post(f'/api/console/history/{attempt}/retry').status_code == 409
    assert store.get_reply_task(task.id).status != 'done'
    assert store.list_candidate_action_attempts(old['id']) == before


def test_unknown_effect_without_formal_resolution_remains_a_quality_failure(tmp_path):
    from app.quality_gate import scan_hourly_quality
    store, task, attempt, *_ = _failed_then_reviewed(tmp_path, stage='dispatch')
    with _client(tmp_path) as client:
        assert client.post(f'/api/console/history/{attempt}/retry').status_code == 409
    assert not store.get_reply_attempt(attempt).resolved_at
    assert any(i.source == 'reply_attempts' and i.code == 'unresolved_latest_attempt'
               for i in scan_hourly_quality(store.path).violations)
