import json
import sqlite3

from app.store import AutoReplyStore
from tests.test_agent_turn_store import _task, _claim_consumer


def test_live_events_keep_payload_in_memory_only(tmp_path):
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    event = {"type": "item.completed", "item": {"type": "command_execution", "id": "call-1", "command": "echo private", "exit_code": 0, "aggregated_output": "large-private-output"}}
    store.append_agent_run_event(run.id, event, owner="consumer")
    assert store.get_agent_run(run.id).tool_events == [event]
    with sqlite3.connect(store.path) as db:
        stored = db.execute("select event_json from agent_run_events where agent_run_id=?", (run.id,)).fetchone()[0]
        assert "large-private-output" not in stored
        assert "echo private" not in stored


def test_native_codex_reader_restores_completed_tool_and_exact_window(tmp_path, monkeypatch):
    from app import native_trajectory
    path = tmp_path / "rollout.jsonl"
    records = [
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": "outside", "command": "old", "exit_code": 0, "aggregated_output": "old"}}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": "selected", "command": "dws chat message send --content hello", "exit_code": 0, "status": "completed", "aggregated_output": '{"openMessageId":"receipt-1"}'}}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": "after", "command": "new", "exit_code": 0, "aggregated_output": "new"}}},
    ]
    path.write_text("\n".join(json.dumps(x) for x in records))
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *a, **kw: path)
    events = native_trajectory.read_codex_events("session", start_line=1, end_line=2)
    assert len(events) == 1
    assert events[0]["item"]["id"] == "selected"
    from app.agent_effect_guard import provider_receipts
    assert provider_receipts(events) == ("receipt-1",)


def test_compaction_preserves_only_copy_and_removes_verified_duplicate(tmp_path, monkeypatch):
    from app import storage_maintenance
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    event = {"type": "item.completed", "item": {"type": "command_execution", "id": "call-1", "command": "private", "exit_code": 0, "aggregated_output": "private-output"}}
    store.append_agent_run_event(run.id, event, owner="consumer")
    with sqlite3.connect(store.path) as db:
        db.execute("update agent_runs set status='completed' where id=?", (run.id,))
        db.execute("update agent_run_events set event_json=? where agent_run_id=?", (json.dumps(event), run.id))
    monkeypatch.setattr(storage_maintenance, "native_run_available", lambda *args: False)
    assert storage_maintenance.compact_native_duplicates(store.path)["runs_retained"] == 1
    with sqlite3.connect(store.path) as db:
        assert "private-output" in db.execute("select event_json from agent_run_events").fetchone()[0]
    monkeypatch.setattr(storage_maintenance, "native_run_available", lambda *args: True)
    monkeypatch.setattr(storage_maintenance, "read_run_events", lambda *args: [event])
    assert storage_maintenance.compact_native_duplicates(store.path)["runs_compacted"] == 1
    with sqlite3.connect(store.path) as db:
        assert "private-output" not in db.execute("select event_json from agent_run_events").fetchone()[0]


def test_zero_native_range_does_not_read_reused_session(tmp_path, monkeypatch):
    from app import native_trajectory
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    with store._connect() as db:
        db.execute("update agent_runs set codex_session_id='reused' where id=?", (run.id,))
        row = db.execute("select * from agent_runs where id=?", (run.id,)).fetchone()
        monkeypatch.setattr(native_trajectory, "read_codex_events", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read unrelated later turn")))
        assert native_trajectory.read_run_events(db, row) == []
        assert not native_trajectory.native_run_available(db, row)


def test_claude_reader_uses_native_line_bounds_for_resumed_session(tmp_path, monkeypatch):
    from app import native_trajectory
    path = tmp_path / "claude.jsonl"
    records = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": name, "name": "shell", "input": {"command": "private"}}]}}
        for name in ("before", "selected", "after")
    ]
    path.write_text("\n".join(json.dumps(record) for record in records))
    monkeypatch.setattr(native_trajectory, "claude_session_path", lambda *args: path)
    events = native_trajectory.read_claude_events("session", start_line=1, end_line=2)
    assert len(events) == 1
    assert events[0]["item"]["id"] == "selected"
    assert "private" not in json.dumps(events)
