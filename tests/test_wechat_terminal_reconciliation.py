"""A completed no-action review can reconcile an earlier proven pre-send failure."""
import json
from datetime import datetime, timezone

import pytest

from app import wechat_reconciliation
from app.agent_contracts import ConsumerAgentResult
from app.quality_gate import _check_external_delivery_queues
from app.system_executor import SystemExecutor
from app.system_action_handlers import WechatSendMessageHandler
from app.wechat.accessibility import SenderExecutionError
from tests.wechat.support import review_candidate
from tests.wechat.test_send_message_handler import _setup, _account, Runner
from tests.test_wechat_failed_reconciliation import _Reader, _message


def _finished_task(tmp_path):
    store, task, row, action, handler, runner, prepared = _setup(
        tmp_path, Runner(error=SenderExecutionError('not ready', action_may_have_started=False)))
    attempt = store.record_reply_attempt(
        channel='wechat', conversation_id=task.conversation_id,
        conversation_title=task.conversation_title, trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender, trigger_text=task.trigger_text,
        action='send_reply', sensitivity_kind='normal', send_status='pending')
    result = SystemExecutor(store, handlers={('wechat', 'send_message'): handler}).execute(
        task, row['candidate_id'], row['review_id'])
    assert result.outcome == 'failed'
    store.fail_reply_task(task.id, 'external_action_failed', expected_execution_generation=task.execution_generation)
    delivery = store.get_wechat_delivery_for_task(task.id)
    new = store.enqueue_manual_rerun_reply_task(
        channel='wechat', conversation_id=task.conversation_id,
        conversation_title=task.conversation_title, single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id, trigger_create_time=task.trigger_create_time,
        trigger_sender=task.trigger_sender, trigger_text=task.trigger_text,
        trigger_message_json=task.trigger_message_json, attempt_id=attempt,
        expected_execution_generation=task.execution_generation)
    current = store.claim_reply_tasks(1, channel='wechat')[0]
    candidate = ConsumerAgentResult.model_validate({
        'outcome': 'no_action', 'summary': 'Principal already replied', 'proposal': None,
        'error': {'code': '', 'retryable': False, 'authorization_required': False},
        'risk': 'low', 'confidence': 1.0, 'rule_coverage': 1.0, 'information_completeness': 1.0})
    review_candidate(store, current, candidate)
    store.complete_reply_task(task.id, expected_execution_generation=new.execution_generation)
    return store, attempt, delivery, row, runner, prepared


def _failures(store):
    issues = []
    with store._connect() as db:
        _check_external_delivery_queues(db, datetime.now(timezone.utc), issues, [])
    return sum(i.count for i in issues if i.source == 'wechat_deliveries')


@pytest.mark.parametrize('readback,expected', [
    ('absent', 'done'), ('present', 'failed'), ('incomplete', 'failed'), ('wrong_scope', 'failed'),
])
def test_done_task_old_delivery_requires_no_send_receipt(tmp_path, monkeypatch, readback, expected):
    store, attempt, delivery, old, runner, prepared = _finished_task(tmp_path)
    terminal = store.get_reply_task(delivery.task_id)
    messages = []
    if readback == 'present':
        messages = [_message(body=prepared.final_body, conversation_id='u9', account_id='acct-1',
                             sent_at='2099-01-01T00:00:00+00:00')]
    if readback == 'incomplete':
        messages = [_message(body='other', message_id=str(i), conversation_id='u9', account_id='acct-1')
                    for i in range(200)]
    if readback == 'wrong_scope':
        messages = [_message(body='other', conversation_id='other', account_id='acct-1')]
    reader = _Reader(messages)
    monkeypatch.setattr(wechat_reconciliation, 'WechatSendMessageHandler', lambda store:
        WechatSendMessageHandler(store, reader_factory=lambda: reader, account_factory=_account,
            sender_factory=lambda: pytest.fail('read-only reconciliation must never call Sender')))
    assert _failures(store) == 1
    before = len(runner.calls)
    result = wechat_reconciliation.reconcile_failed_wechat_attempt(store, attempt)
    assert result['outcome'] == expected
    assert store.get_reply_task(delivery.task_id) == terminal
    assert store.get_wechat_delivery_by_id(delivery.id) == delivery
    assert store.get_candidate_execution(old['candidate_id'])['status'] == 'failed'
    assert len(runner.calls) == before
    receipts = store.list_candidate_action_attempts(old['candidate_id'])
    assert receipts[0]['status'] == ('confirmed_no_effect' if expected == 'done' else 'failed')
    assert _failures(store) == (0 if expected == 'done' else 1)
    if expected == 'done':
        proof = json.loads(receipts[0]['result_json'])
        assert proof['delivery_id'] == delivery.id
        assert proof['readback']['window_complete'] is True
        assert proof['readback']['match_count'] == 0
        assert proof['completion_review_id'] > old['review_id']
        assert wechat_reconciliation.reconcile_failed_wechat_attempt(store, attempt)['outcome'] == 'done'


@pytest.mark.parametrize('change', ['sender_unknown', 'task_generation', 'review_invalidated', 'delivery_target'])
def test_terminal_reconciliation_refuses_changed_evidence(tmp_path, monkeypatch, change):
    from app import task_entity_storage
    from tests.support.action_fixtures import update_delivery_fixture
    store, attempt, delivery, old, runner, prepared = _finished_task(tmp_path)
    reader = _Reader([])
    def read(*args, **kwargs):
        with store._connect() as db:
            if change == 'task_generation':
                task_entity_storage.update_task_cas(db, task_id=delivery.task_id,
                    expected={'status': 'done'}, changes={'execution_generation': 'changed'})
            elif change == 'review_invalidated':
                db.execute("update run_adopted_results set invalidated_at='changed' where consumer_run_id=?",
                           (store.current_reviewed_candidate(delivery.task_id,
                            store.get_reply_task(delivery.task_id).execution_generation)['candidate_id'],))
            else:
                update_delivery_fixture(db, 'wechat_deliveries', delivery.id,
                    {'pre_action_failure': 0} if change == 'sender_unknown' else {'target_id': 'different'})
        return []
    reader.read_messages = read
    monkeypatch.setattr(wechat_reconciliation, 'WechatSendMessageHandler', lambda store:
        WechatSendMessageHandler(store, reader_factory=lambda: reader, account_factory=_account))
    result = wechat_reconciliation.reconcile_failed_wechat_attempt(store, attempt)
    assert result['outcome'] == 'failed'
    assert store.list_candidate_action_attempts(old['candidate_id'])[0]['status'] == 'failed'
    assert _failures(store) == 1


def test_no_effect_receipt_does_not_hide_a_later_unknown_delivery(tmp_path, monkeypatch):
    from tests.support.action_fixtures import update_delivery_fixture
    store, attempt, delivery, old, runner, prepared = _finished_task(tmp_path)
    monkeypatch.setattr(wechat_reconciliation, 'WechatSendMessageHandler', lambda store:
        WechatSendMessageHandler(store, reader_factory=lambda: _Reader([]), account_factory=_account))
    assert wechat_reconciliation.reconcile_failed_wechat_attempt(store, attempt)['outcome'] == 'done'
    with store._connect() as db:
        update_delivery_fixture(db, 'wechat_deliveries', delivery.id,
                                {'status': 'send_unknown', 'pre_action_failure': 0})
    assert _failures(store) == 1


@pytest.mark.parametrize('field,value', [
    ('window_complete', False), ('match_count', True), ('account_id', 'other'),
    ('conversation_id', 'other'), ('since', ''), ('text_sha256', 'wrong'),
])
def test_store_refuses_unbound_no_effect_proof(tmp_path, field, value):
    from hashlib import sha256
    store, attempt, delivery, old, runner, prepared = _finished_task(tmp_path)
    task = store.get_reply_task(delivery.task_id)
    review = store.current_reviewed_candidate(task.id, task.execution_generation)
    proof = {'window_complete': True, 'match_count': 0, 'account_id': delivery.account_id,
        'conversation_id': delivery.conversation_id, 'conversation_type': delivery.target_type,
        'since': delivery.action_started_at, 'text_sha256': sha256(delivery.reply_text.encode()).hexdigest()}
    proof[field] = value
    assert not store.reconcile_completed_wechat_delivery(task, review, delivery,
        {'delivery_id': delivery.id, 'readback': proof})
    assert store.list_candidate_action_attempts(old['candidate_id'])[0]['status'] == 'failed'


def test_done_task_requires_an_approved_no_action_review(tmp_path, monkeypatch):
    store, attempt, delivery, old, runner, prepared = _finished_task(tmp_path)
    task = store.get_reply_task(delivery.task_id)
    review = store.current_reviewed_candidate(task.id, task.execution_generation)
    with store._connect() as db:
        db.execute("update run_adopted_reviews set decision='return' where id=?", (review['review_id'],))
    monkeypatch.setattr(wechat_reconciliation, 'WechatSendMessageHandler',
        lambda store: pytest.fail('missing approval must not proceed to Reader'))
    assert wechat_reconciliation.reconcile_failed_wechat_attempt(store, attempt)['outcome'] == 'failed'
    assert _failures(store) == 1
