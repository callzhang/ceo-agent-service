"""Legacy native replies require original input and complete no-send proof, never a retry guess."""
import json
from types import SimpleNamespace

import pytest

from app.dingtalk_models import DingTalkMessage
from app.dws_client import DwsClient, DwsError
from app.service_message_sender import ServiceMessageSender, agent_message_delivery_key
from app.store import AutoReplyStore
from app.system_executor import SystemExecutor
from tests.test_system_executor import _candidate
from tests.wechat.support import review_candidate
from tests.test_console_web_api import _client


def fixture(tmp_path, monkeypatch, invalid=None):
    store = AutoReplyStore(tmp_path / 'worker.sqlite3')
    trigger = DingTalkMessage(open_conversation_id='native-chat', open_message_id='trigger',
        conversation_title='Example', single_chat=True, sender_name='Sender',
        create_time='2026-11-20 21:34:39', content='Original request', raw_payload={'senderId': 'open-sender'})
    task_id = store.enqueue_reply_task(channel='dingtalk', conversation_id='native-chat',
        conversation_title='Example', single_chat=True, trigger_message_id='trigger',
        trigger_sender='Sender', trigger_create_time=trigger.create_time,
        trigger_text=trigger.content, trigger_message_json=trigger.model_dump_json())
    task = store.claim_reply_task(task_id)
    prepared = ServiceMessageSender(store=store).prepare(channel='dingtalk',
        delivery_key=agent_message_delivery_key(business_object_key=task.business_object_key,
            action_identity='archive-reminder', execution_generation=task.execution_generation,
            proposal_revision=0), body='Please archive the original in the company knowledge base and send its link.')
    proposal = _candidate(actions=[{'description': 'Archive reminder',
        'action_identity': 'archive-reminder', 'capability': 'dingtalk-chat',
        'operation': 'reply_to_message', 'target': {'conversation_id': 'native-chat', 'message_id': 'trigger'},
        'payload': {'content': prepared.final_body}}])
    candidate, review = review_candidate(store, task, proposal)

    class OldHandler:
        def dispatch(self, *args, **kwargs):
            raise DwsError('missing trigger senderOpenDingTalkId for native reply')

    SystemExecutor(store, {('dingtalk-chat', 'reply_to_message'): OldHandler()}).execute(
        task, candidate['id'], review['id'])
    store.fail_reply_task(task.id, 'external_action_uncertain', expected_execution_generation=task.execution_generation)
    attempt_id = store.record_reply_attempt(channel='dingtalk', conversation_id=task.conversation_id,
        conversation_title='Example', trigger_message_id='trigger', trigger_sender='Sender',
        trigger_text=trigger.content, action='agent_run', sensitivity_kind='general',
        send_status='failed')
    prior = store.list_candidate_action_attempts(candidate['id'])[0]
    native = {'task_id': task.id, 'channel': 'dingtalk', 'conversation_id': 'native-chat',
        'single_chat': True, 'message_id': 'trigger', 'sender': 'Sender', 'sender_user_id': '',
        'sender_open_dingtalk_id': '', 'create_time': trigger.create_time, 'text': trigger.content,
        'raw_payload': trigger.raw_payload}
    if invalid == 'original-had-sender':
        native['sender_open_dingtalk_id'] = 'open-sender'
    if invalid == 'wrong-task':
        native['task_id'] += 1
    if invalid == 'wrong-trigger':
        native['message_id'] = 'other'
    monkeypatch.setattr(AutoReplyStore, 'list_agent_runtime_attempts', lambda _, run: [SimpleNamespace(
        id=123, parent_id=run, status='completed' if invalid != 'runtime-failed' else 'failed')])
    monkeypatch.setattr('app.native_prompt_input.read_attempt_input', lambda _: None if invalid == 'missing-input' else {
        'task_prompt': 'Context\n\nOriginal trigger\n' + json.dumps(native) + '\n\nRecent conversation context\n[]'})
    current = trigger.model_copy(update={'sender_user_id': 'user-sender', 'sender_open_dingtalk_id': 'open-sender'})
    if invalid == 'sender-changed':
        current = current.model_copy(update={'raw_payload': {'senderId': 'other'}})
    if invalid == 'text-changed':
        current = current.model_copy(update={'content': 'Changed request'})
    monkeypatch.setattr(DwsClient, 'read_message_by_id', lambda *_: current)
    monkeypatch.setattr(DwsClient, 'get_current_user_id', lambda _: 'self')
    def messages(*args):
        if invalid == 'partial':
            raise DwsError('incomplete native message window')
        if invalid == 'already-sent':
            return [current.model_copy(update={'open_message_id': 'sent', 'quoted_message_id': 'trigger',
                'sender_user_id': 'self', 'content': '@Sender ' + prepared.final_body})]
        return []
    monkeypatch.setattr(DwsClient, 'read_complete_group_messages_in_window', messages)
    monkeypatch.setattr(DwsClient, 'send_reply_to_trigger', lambda *_: pytest.fail('recovery must never send'))
    if invalid in ('active', 'wrong-error'):
        from app.task_entity_storage import update_task_cas
        with store._connect() as db:
            if invalid == 'active':
                update_task_cas(db, task_id=task.id, expected={'status': 'failed'}, changes={'status': 'processing'})
            else:
                result = json.loads(prior['result_json'])
                if invalid == 'wrong-error':
                    result['exception_type'] = 'TimeoutError'
                db.execute('update action_receipts set outcome_json=? where id=?',
                           (json.dumps(result), prior['id']))
    if invalid == 'has-receipt':
        store.record_outbound_postfix_receipt('dingtalk', prepared.delivery_key,
            {'success': True, 'result': {'openTaskId': 'accepted'}}, operation='send_reply_to_trigger')
    return store, task, attempt_id, prior


def test_formal_retry_reconciles_without_completing_or_resending_task(tmp_path, monkeypatch):
    store, task, attempt, prior = fixture(tmp_path, monkeypatch)
    original = store.get_reply_attempt(attempt)
    body = {'action_attempt_id': prior['id'], 'execution_generation': task.execution_generation}
    with _client(tmp_path) as client:
        response = client.post(f'/api/console/history/{attempt}/retry', json=body)
        assert response.status_code == 200, response.json()
        assert response.json()['item']['outcome'] == 'reconciled'
        assert client.post(f'/api/console/history/{attempt}/retry', json=body).status_code == 200
    after = store.list_candidate_action_attempts(prior['execution_id'])[0]
    assert after['status'] == 'confirmed_no_effect'
    proof = json.loads(after['result_json'])
    assert proof['previous_result'] == json.loads(prior['result_json'])
    assert proof['runtime_attempt_id'] == 123
    assert proof['readback']['readbackVerification']['exactMatchCount'] == 0
    assert store.get_reply_task(task.id).status == 'failed'
    assert store.get_reply_attempt(attempt) == original
    assert store.get_candidate_execution(prior['execution_id'])['status'] == 'uncertain'
    assert store.get_candidate_external_action(prior['external_action_key']) is None


@pytest.mark.parametrize('invalid', ['original-had-sender', 'wrong-task', 'wrong-trigger',
    'runtime-failed', 'missing-input', 'sender-changed', 'text-changed', 'partial',
    'already-sent', 'active', 'wrong-error', 'has-receipt', 'generation'])
def test_formal_retry_refuses_incomplete_or_conflicting_proof(tmp_path, monkeypatch, invalid):
    store, task, attempt, prior = fixture(tmp_path, monkeypatch, invalid)
    with _client(tmp_path) as client:
        response = client.post(f'/api/console/history/{attempt}/retry', json={
            'action_attempt_id': prior['id'],
            'execution_generation': 'changed' if invalid == 'generation' else task.execution_generation})
        assert response.status_code == 409, response.json()
    assert store.list_candidate_action_attempts(prior['execution_id'])[0]['status'] == 'uncertain'


def test_formal_retry_recovers_an_older_generation_without_completing_current_task(tmp_path, monkeypatch):
    from app.task_entity_storage import update_task_cas
    store, task, attempt, prior = fixture(tmp_path, monkeypatch)
    with store._connect() as db:
        update_task_cas(db, task_id=task.id, expected={'status': 'failed'},
                        changes={'execution_generation': 'new-generation'})
    with _client(tmp_path) as client:
        response = client.post(f'/api/console/history/{attempt}/retry', json={
            'action_attempt_id': prior['id'], 'execution_generation': 'new-generation'})
        assert response.status_code == 200, response.json()
    assert store.get_reply_task(task.id).execution_generation == 'new-generation'
    assert store.get_reply_task(task.id).status == 'failed'
    assert store.list_candidate_action_attempts(prior['execution_id'])[0]['status'] == 'confirmed_no_effect'


def test_formal_retry_refuses_a_task_change_during_external_readback(tmp_path, monkeypatch):
    from app.task_entity_storage import update_task_cas
    store, task, attempt, prior = fixture(tmp_path, monkeypatch)
    def changed(*args):
        with store._connect() as db:
            update_task_cas(db, task_id=task.id, expected={'status': 'failed'},
                            changes={'execution_generation': 'changed-during-readback'})
        return []
    monkeypatch.setattr(DwsClient, 'read_complete_group_messages_in_window', changed)
    with _client(tmp_path) as client:
        response = client.post(f'/api/console/history/{attempt}/retry', json={
            'action_attempt_id': prior['id'], 'execution_generation': task.execution_generation})
        assert response.status_code == 409, response.json()
    assert store.list_candidate_action_attempts(prior['execution_id'])[0]['status'] == 'uncertain'


def test_reconciled_action_runs_once_only_after_a_new_independent_review(tmp_path, monkeypatch):
    from app.system_executor import ActionOutcome
    store, task, attempt, prior = fixture(tmp_path, monkeypatch)
    with _client(tmp_path) as client:
        response = client.post(f'/api/console/history/{attempt}/retry', json={
            'action_attempt_id': prior['id'], 'execution_generation': task.execution_generation})
        assert response.status_code == 200, response.json()
        response = client.post(f'/api/console/history/{attempt}/rerun', json={})
        assert response.status_code == 200, response.json()
    current = store.claim_reply_task(task.id)
    assert current.execution_generation != task.execution_generation
    result = _candidate(actions=[{'description': 'Archive reminder',
        'action_identity': 'archive-reminder', 'capability': 'dingtalk-chat',
        'operation': 'reply_to_message', 'target': {'conversation_id': 'native-chat', 'message_id': 'trigger'},
        'payload': {'content': 'Reviewed updated archival request'}}])
    candidate, review = review_candidate(store, current, result)
    sends = []
    class Handler:
        def dispatch(self, action, **kwargs):
            sends.append(action)
            return ActionOutcome('verified', {'verification': {'state': 'sent', 'message_id': 'new-message'}})
    executor = SystemExecutor(store, {('dingtalk-chat', 'reply_to_message'): Handler()})
    assert executor.execute(current, candidate['id'], review['id']).outcome == 'executed'
    assert executor.execute(current, candidate['id'], review['id']).outcome == 'executed'
    assert len(sends) == 1
    assert store.list_candidate_action_attempts(prior['execution_id'])[0]['status'] == 'confirmed_no_effect'


def test_formal_retry_preserves_a_completed_task_while_reconciling_its_old_action(tmp_path, monkeypatch):
    store, task, attempt, prior = fixture(tmp_path, monkeypatch)
    from app.task_entity_storage import update_task_cas
    with store._connect() as db:
        update_task_cas(db, task_id=task.id, expected={'status': 'failed'}, changes={'status': 'processing'})
    store.complete_reply_task(task.id, expected_execution_generation=task.execution_generation)
    before = store.get_reply_task(task.id)
    with _client(tmp_path) as client:
        response = client.post(f'/api/console/history/{attempt}/retry', json={
            'action_attempt_id': prior['id'], 'execution_generation': task.execution_generation})
        assert response.status_code == 200, response.json()
    assert store.get_reply_task(task.id) == before
    assert store.list_candidate_action_attempts(prior['execution_id'])[0]['status'] == 'confirmed_no_effect'
