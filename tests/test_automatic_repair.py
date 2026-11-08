import json
from types import SimpleNamespace

import pytest

from app.automatic_repair import AutomaticRepairCoordinator, repair_presentation
from app.automatic_repair_store import enqueue_repair, get_repair, list_repairs
from app.external_retry import MAX_AUTOMATIC_ATTEMPTS
from app.workbench.store import WorkbenchStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    wb = WorkbenchStore(tmp_path / 'service.sqlite3')
    wb.test_native_text = {}
    original = wb.get_turn
    def native_turn(turn_id):
        turn = original(turn_id)
        if turn is not None:
            turn.final_text = wb.test_native_text.get(turn_id, '')
        return turn
    monkeypatch.setattr(wb, 'get_turn', native_turn)
    return wb


def test_recorded_error_transactionally_enqueues_once(store):
    store.record_error(None, None, 'test_failure', 'actual diagnostic failure')
    error = store.list_errors(limit=1)[0]
    entry = get_repair(store, 'errors', error.id)
    assert entry['status'] == 'queued'
    enqueue_repair(store, 'errors', error.id)
    assert len(list_repairs(store)) == 1


def coordinator(store, tmp_path, prs=None):
    return AutomaticRepairCoordinator(
        store, repository=tmp_path / 'dev',
        pr_reader=lambda branch: prs or [], interval_seconds=0,
    )


def finish(store, turn_id, text='I will implement next', status='completed'):
    store.test_native_text[turn_id] = text
    turn = store.claim_next_turn(owner='test', lease_seconds=300)
    assert turn.id == turn_id
    store.complete_turn(turn.id, owner='test', status=status, final_text=text)


def test_restart_resumes_same_conversation_and_uses_idempotent_turn(store, tmp_path):
    store.record_error(None, None, 'test_failure', 'failed')
    error = store.list_errors(limit=1)[0]
    first = coordinator(store, tmp_path)
    first.run_once()
    entry = get_repair(store, 'errors', error.id)
    assert entry['task_id'] and len(entry['turn_ids']) == 1
    coordinator(store, tmp_path).run_once()
    assert get_repair(store, 'errors', error.id) == entry
    finish(store, entry['turn_ids'][0])
    coordinator(store, tmp_path).run_once()
    continued = get_repair(store, 'errors', error.id)
    assert continued['task_id'] == entry['task_id']
    assert len(continued['turn_ids']) == 2
    assert store.get_turn(continued['turn_ids'][1]).client_request_id.endswith(':2')
    assert '/history/errors/' in store.get_turn(continued['turn_ids'][1]).user_text


def test_actual_remote_pr_is_linked_even_if_agent_final_was_interrupted(store, tmp_path):
    store.record_error(None, None, 'test_failure', 'failed')
    error = store.list_errors(limit=1)[0]
    coordinator(store, tmp_path).run_once()
    entry = get_repair(store, 'errors', error.id)
    finish(store, entry['turn_ids'][0])
    receipt = {'url': 'https://github.com/example/service/pull/9',
               'headRefOid': 'abc', 'state': 'OPEN'}
    coordinator(store, tmp_path, [receipt]).run_once()
    result = repair_presentation(store, 'errors', error.id)
    assert result['status'] == 'pr_created'
    assert result['pr_url'] == receipt['url']
    assert result['conversation_url'] == f"/?task={entry['task_id']}"
    assert len(get_repair(store, 'errors', error.id)['turn_ids']) == 1
    assert not store.get_error(error.id).resolved_at


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
    from app.audit_web import create_audit_app
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
    response = TestClient(create_audit_app(store.path)).get(f'/api/console/history/errors/{error.id}')
    assert response.json()['item']['repair']['conversation_url'] == '/?task=456'


def test_checkpoint_interruption_reuses_the_already_created_turn(store, tmp_path, monkeypatch):
    import app.automatic_repair as module
    store.record_error(None, None, 'failure', 'detail')
    error = store.list_errors(limit=1)[0]
    real_save = module.save_repair
    def interrupted(*args):
        raise RuntimeError('checkpoint interrupted')
    monkeypatch.setattr(module, 'save_repair', interrupted)
    with pytest.raises(RuntimeError, match='checkpoint interrupted'):
        coordinator(store, tmp_path).run_once()
    entry = get_repair(store, 'errors', error.id)
    with store._connect() as db:
        assert db.execute('select count(*) from workbench_turns').fetchone()[0] == 1
    monkeypatch.setattr(module, 'save_repair', real_save)
    coordinator(store, tmp_path).run_once()
    assert get_repair(store, 'errors', error.id)['task_id'] == entry['task_id']
    with store._connect() as db:
        assert db.execute('select count(*) from workbench_turns').fetchone()[0] == 1


def test_slow_repair_readback_does_not_block_ordinary_workbench_dispatch():
    import threading
    from app.workbench.api import WorkbenchScheduler
    entered, release, dispatched = threading.Event(), threading.Event(), threading.Event()
    def maintenance():
        entered.set()
        release.wait(2)
    scheduler = WorkbenchScheduler(SimpleNamespace(run_once=dispatched.set),
                                   interval_seconds=0.01, before_run=maintenance)
    scheduler.start()
    try:
        assert entered.wait(1)
        assert dispatched.wait(0.2)
    finally:
        release.set()
        scheduler.close()


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
