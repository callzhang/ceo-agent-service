from pathlib import Path

import pytest

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore


def _failed_task(store: AutoReplyStore, status: str):
    task = store.ensure_reply_task(
        conversation_id=f"cid-{status}",
        conversation_title="Query plan regression",
        single_chat=True,
        trigger_message_id=f"msg-{status}",
        trigger_sender="sender",
        trigger_text="Please handle this request.",
        trigger_create_time="2026-10-06T00:00:00Z",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set status=?, error='service failure' where id=?",
            (status, task.id),
        )
    return task


def test_attention_selects_failed_ids_without_scanning_task_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _failed_task(store, "failed")
    statements: list[str] = []
    original_open = store._open_connection

    def traced_connection():
        db = original_open()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "_open_connection", traced_connection)
    with store.read_snapshot():
        rows = _queue_attention_rows(store)
    assert any(row["id"] == str(task.id) for row in rows)
    query = next(
        sql for sql in statements
        if "select reply_tasks.id, reply_tasks.channel" in sql
    )
    with store._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + query)]
    assert any("SEARCH reply_tasks USING INTEGER PRIMARY KEY" in step for step in plan), plan
    assert any("COVERING INDEX idx_reply_tasks_status" in step for step in plan), plan
    assert "SCAN reply_tasks" not in plan, plan


@pytest.mark.parametrize("status", ["failed", "FAILED", "FaIlEd"])
def test_indexed_attention_keeps_case_insensitive_matching_and_fresh_reads(
    tmp_path: Path, status: str
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = _failed_task(store, status)
    with store.read_snapshot():
        rows = _queue_attention_rows(store, limit=1)
    assert [(row["id"], row["status"]) for row in rows] == [(str(task.id), status)]

    with store._connect() as db:
        db.execute("update reply_tasks set status='done' where id=?", (task.id,))
    with store.read_snapshot():
        assert _queue_attention_rows(store, limit=1) == []


def test_indexed_attention_applies_order_and_limit_after_failed_selection(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    tasks = [_failed_task(store, status) for status in ("failed", "FAILED", "FaIlEd")]
    with store._connect() as db:
        db.execute("update reply_tasks set updated_at='2026-10-06T00:00:00Z'")
        db.execute("update reply_tasks set status='done' where id=?", (tasks[1].id,))
    for limit, expected in ((None, [tasks[2], tasks[0]]), (1, [tasks[2]])):
        with store.read_snapshot():
            rows = _queue_attention_rows(store, limit=limit)
        assert [row["id"] for row in rows] == [str(task.id) for task in expected]
