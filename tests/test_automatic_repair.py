import json
from types import SimpleNamespace

import pytest

from app.automatic_repair import repair_presentation
from app.automatic_repair_store import enqueue_repair, get_repair
from app.external_retry import MAX_AUTOMATIC_ATTEMPTS
from tests.test_automatic_repair_gate import coordinator, finish, store as store


def test_agent_claim_of_pr_without_remote_receipt_continues(store, tmp_path):
    store.record_error(None, None, 'test_failure', 'failed')
    error = store.list_errors(limit=1)[0]
    coordinator(store, tmp_path).run_once()
    entry = get_repair(store, 'errors', error.id)
    finish(store, entry['turn_ids'][0], json.dumps({'outcome': 'pr_created', 'summary': 'done'}))
    coordinator(store, tmp_path).run_once()
    assert len(get_repair(store, 'errors', error.id)['turn_ids']) == 2


def test_continuation_is_bounded_and_explicit_stop_is_respected(store, tmp_path):
    store.record_error(None, None, 'test_failure', 'failed')
    error = store.list_errors(limit=1)[0]
    c = coordinator(store, tmp_path)
    c.run_once()
    for _ in range(MAX_AUTOMATIC_ATTEMPTS):
        entry = get_repair(store, 'errors', error.id)
        finish(store, entry['turn_ids'][-1])
        c.run_once()
    entry = get_repair(store, 'errors', error.id)
    assert entry['status'] == 'failed'
    assert len(entry['turn_ids']) == MAX_AUTOMATIC_ATTEMPTS
    store.record_error(None, None, 'another_failure', 'failed')
    error2 = store.list_errors(limit=1)[0]
    c.run_once()
    entry2 = get_repair(store, 'errors', error2.id)
    store.request_stop(entry2['turn_ids'][0])
    c.run_once()
    assert get_repair(store, 'errors', error2.id)['status'] == 'stopped'


def test_blocked_or_non_code_diagnosis_stops_with_reason(store, tmp_path):
    store.record_error(None, None, 'test_failure', 'failed')
    error = store.list_errors(limit=1)[0]
    coordinator(store, tmp_path).run_once()
    entry = get_repair(store, 'errors', error.id)
    finish(store, entry['turn_ids'][0], json.dumps({'outcome': 'blocked', 'summary': 'Dependency login expired'}))
    coordinator(store, tmp_path).run_once()
    assert get_repair(store, 'errors', error.id)['status'] == 'blocked'
    assert repair_presentation(store, 'errors', error.id)['summary'] == 'Dependency login expired'


def test_pr_lookup_failure_retains_source_and_conversation(store, tmp_path):
    store.record_error(None, None, 'test_failure', 'failed')
    error = store.list_errors(limit=1)[0]
    c = coordinator(store, tmp_path)
    c.run_once()
    entry = get_repair(store, 'errors', error.id)
    finish(store, entry['turn_ids'][0])
    def unavailable(branch):
        raise RuntimeError('GitHub unavailable')
    c.pr_reader = unavailable
    c.run_once()
    result = get_repair(store, 'errors', error.id)
    assert result['status'] == 'awaiting_pr' and result['task_id'] == entry['task_id']
    assert 'GitHub unavailable' in result['summary']
    from app.automatic_repair_store import save_repair
    result['next_readback_at'] = 0
    save_repair(store, result)
    receipt = {'url': 'https://github.com/example/service/pull/10', 'headRefOid': 'def', 'state': 'OPEN'}
    coordinator(store, tmp_path, [receipt]).run_once()
    assert get_repair(store, 'errors', error.id)['pr_url'] == receipt['url']
    assert len(get_repair(store, 'errors', error.id)['turn_ids']) == 1


def test_failed_notification_enqueues_but_business_decision_does_not(store, monkeypatch):
    from app.worker import DingTalkAutoReplyWorker
    from tests.test_audit_web import seed_attempt
    attempt_id = seed_attempt(store)
    worker = object.__new__(DingTalkAutoReplyWorker)
    worker.store = store
    worker.dry_run = False
    monkeypatch.setattr(worker, '_problem_notification_content', lambda *a, **k: ('title', 'message'))
    monkeypatch.setattr(worker, '_notification_url', lambda *a, **k: '')
    monkeypatch.setattr('app.worker.send_browser_notification', lambda **k: True)
    task = SimpleNamespace(channel='dingtalk', conversation_id='c', trigger_message_id='m',
                           conversation_title='title', single_chat=True)
    worker._notify_problem_attempt(task, attempt_id=attempt_id, send_status='needs_human', message='decision')
    assert get_repair(store, 'reply_attempts', attempt_id) is None
    worker._notify_problem_attempt(task, attempt_id=attempt_id, send_status='failed', message='failure')
    assert get_repair(store, 'reply_attempts', attempt_id)['status'] == 'queued'


def test_error_and_outbox_roll_back_together(store, monkeypatch):
    before = store.count_errors()
    def fail(*args):
        raise RuntimeError('outbox failure')
    monkeypatch.setattr('app.automatic_repair_store.enqueue_in_transaction', fail)
    with pytest.raises(RuntimeError, match='outbox failure'):
        store.record_error(None, None, 'failure', 'detail')
    assert store.count_errors() == before


def test_failed_attempt_is_enqueued_in_its_own_transaction(store):
    from tests.test_audit_web import seed_attempt
    attempt_id = seed_attempt(store)
    store.update_reply_attempt(attempt_id, send_status='failed', send_error='runtime failure')
    assert get_repair(store, 'reply_attempts', attempt_id)['status'] == 'queued'


def test_conversation_creation_is_atomic_and_does_not_fork(store, tmp_path):
    from app.automatic_repair_store import ensure_conversation
    store.record_error(None, None, 'failure', 'detail')
    error = store.list_errors(limit=1)[0]
    entry = get_repair(store, 'errors', error.id)
    first = ensure_conversation(store, entry)
    second = ensure_conversation(store, entry)
    assert first['task_id'] == second['task_id']
    assert len(store.list_tasks()) == 1


def test_source_detail_api_contains_persisted_links(store, tmp_path):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.web_api.registration import register_console_routes
    from app.automatic_repair_store import save_repair
    from app.web_api.attempts import build_attempt_detail
    from tests.test_audit_web import seed_attempt
    attempt_id = seed_attempt(store)
    enqueue_repair(store, 'reply_attempts', attempt_id)
    entry = get_repair(store, 'reply_attempts', attempt_id)
    entry.update(task_id='123', status='pr_created', pr_url='https://github.com/example/service/pull/1')
    save_repair(store, entry)
    assert build_attempt_detail(store, attempt_id)[1]['repair']['pr_url'] == entry['pr_url']
    store.record_error(None, None, 'failure', 'detail')
    error = store.list_errors(limit=1)[0]
    entry = get_repair(store, 'errors', error.id)
    entry.update(task_id='456')
    save_repair(store, entry)
    app = FastAPI()
    register_console_routes(
        app, lambda: store, status_payload_factory=lambda: {},
        feedback_backlog_factory=lambda: {}, attention_rows_factory=lambda: [],
    )
    response = TestClient(app).get(f'/api/console/history/errors/{error.id}')
    assert response.json()['item']['repair']['conversation_url'] == '/?task=456'


def test_waiting_pr_readback_does_not_starve_later_repair(store, tmp_path):
    store.record_error(None, None, 'first_failure', 'first')
    first_id = store.list_errors(limit=1)[0].id
    c = coordinator(store, tmp_path)
    c.run_once()
    finish(store, get_repair(store, 'errors', first_id)['turn_ids'][0])
    def unavailable(branch):
        raise RuntimeError('offline')
    c.pr_reader = unavailable
    c.run_once()
    assert get_repair(store, 'errors', first_id)['status'] == 'awaiting_pr'
    store.record_error(None, None, 'second_failure', 'second')
    second_id = store.list_errors(limit=1)[0].id
    c.run_once()
    assert get_repair(store, 'errors', second_id)['status'] == 'running'
    assert get_repair(store, 'errors', first_id)['status'] == 'awaiting_pr'
