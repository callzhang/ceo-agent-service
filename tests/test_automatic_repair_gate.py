"""Fast native repair contracts retained in the fixed deployment gate."""

from types import SimpleNamespace

import pytest

from app.automatic_repair import AutomaticRepairCoordinator, repair_presentation
from app.automatic_repair_store import enqueue_repair, get_repair, list_repairs
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


def test_checkpoint_interruption_reuses_the_already_created_turn(store, tmp_path, monkeypatch):
    import app.automatic_repair as module
    store.record_error(None, None, 'failure', 'detail')
    error = store.list_errors(limit=1)[0]
    real_save = module.save_repair
    calls = 0
    def interrupted(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('checkpoint interrupted')
        return real_save(*args)
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

