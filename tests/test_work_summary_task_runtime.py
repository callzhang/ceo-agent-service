import sqlite3
from contextvars import ContextVar

from app import run_entity_definitions, task_run_entity_definitions
from app.store import AutoReplyStore


def _native_store(tmp_path):
    path = tmp_path / "native.sqlite3"
    db = sqlite3.connect(path)
    objects = task_run_entity_definitions.task_run_entity_objects()
    run_objects = run_entity_definitions.run_entity_objects()
    for name in ("tasks", "task_inputs", "work_summary_inputs"):
        db.execute(objects[name])
    for name in ("runs", "run_provenance", "run_projections", "runtime_attempts"):
        db.execute(run_objects[name])
    db.commit()
    db.close()
    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar("test_snapshot", default=None)
    store._reused_connection = ContextVar("test_reused", default=None)
    return store


def test_work_summary_uses_canonical_task_id_and_keeps_terminal_input_immutable(tmp_path):
    store = _native_store(tmp_path)
    first = store.enqueue_work_summary_input("local_file", "42", '{"summary":"first"}')
    assert first == 1 and first != 42
    assert store.enqueue_work_summary_input("local_file", "42", '{"summary":"replacement"}') == first
    [claimed] = store.claim_work_summary_inputs(1)
    assert claimed.id == first
    assert claimed.status == "processing"
    assert claimed.attempts == 1
    assert claimed.payload_json == '{"summary":"replacement"}'

    run_id = store.claim_professional_run(claimed.id)
    assert store.get_professional_run(run_id)["task_id"] == claimed.id
    store.finish_professional_run(run_id, status="completed", session_id="fixture-session")
    finished = store.get_professional_run(run_id)
    assert (finished["status"], finished["session_id"]) == ("completed", "fixture-session")

    assert store.enqueue_work_summary_input("local_file", "42", '{"summary":"late"}') == first
    assert store.get_work_summary_input(first).payload_json == '{"summary":"replacement"}'
    store.mark_work_summary_input_done(first)
    terminal = store.get_work_summary_input(first)
    assert terminal.status == "done"
    assert terminal.body_compacted == 1
    assert terminal.body_sha256
    assert terminal.body_bytes == len('{"summary":"replacement"}'.encode())
    assert terminal.payload_json == "{}"

    skipped_id = store.enqueue_work_summary_input("local_file", "skip-me", '{"summary":"skip"}')
    store.claim_work_summary_inputs(1)
    store.mark_work_summary_input_skipped(skipped_id, "no actionable decision")
    skipped = store.get_work_summary_input(skipped_id)
    assert (skipped.status, skipped.error, skipped.body_compacted, skipped.payload_json) == (
        "skipped", "no actionable decision", 1, "{}"
    )


def test_work_summary_retry_and_capacity_keep_native_task_lifecycle(tmp_path):
    store = _native_store(tmp_path)
    task_id = store.enqueue_work_summary_input("local_file", "source-B", "{}")
    store.claim_work_summary_inputs(1)
    store.mark_work_summary_input_failed(task_id, "temporary")
    assert store.requeue_failed_work_summary_input(task_id, "reviewed")
    pending = store.get_work_summary_input(task_id)
    assert (pending.status, pending.attempts, pending.error) == ("pending", 0, "reviewed")
    store.claim_work_summary_inputs(1)
    store.defer_work_summary_input_for_capacity(task_id, "busy", available_at="2099-01-01 00:00:00")
    deferred = store.get_work_summary_input(task_id)
    assert (deferred.status, deferred.attempts, deferred.error) == ("pending", 0, "busy")
