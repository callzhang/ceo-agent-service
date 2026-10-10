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


def test_oversized_event_invalidates_partial_live_cache(tmp_path, monkeypatch):
    from collections import OrderedDict
    from app import native_trajectory
    monkeypatch.setattr(native_trajectory, "_LIVE_EVENTS", OrderedDict())
    monkeypatch.setattr(native_trajectory, "_LIVE_BYTES", 0)
    monkeypatch.setattr(native_trajectory, "_LIVE_LIMIT_BYTES", 200)
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    for call_id, output in (("A", "small"), ("B", "large" * 200)):
        store.append_agent_run_event(run.id, {"type": "item.completed", "item": {"type": "command_execution", "id": call_id, "exit_code": 0, "aggregated_output": output}}, owner="consumer")
    assert [event["item"]["id"] for event in store.get_agent_run(run.id).tool_events] == ["A", "B"]


def test_restart_preserves_receipts_before_attempt_finishes(tmp_path, monkeypatch):
    from collections import OrderedDict
    from app import native_trajectory
    from app.agent_effect_guard import provider_receipts
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    event = {"type": "item.completed", "item": {"type": "command_execution", "id": "call-1", "exit_code": 0, "aggregated_output": '{"result":{"openTaskId":"receipt-before-crash"},"private":"large-output"}'}}
    store.append_agent_run_event(run.id, event, owner="consumer")
    monkeypatch.setattr(native_trajectory, "_LIVE_EVENTS", OrderedDict())
    assert provider_receipts(store.get_agent_run(run.id).tool_events) == ("receipt-before-crash",)
    later = {"type": "item.completed", "item": {"type": "command_execution", "id": "call-2", "exit_code": 0, "aggregated_output": '{"result":{"openMessageId":"receipt-after-restart"}}'}}
    store.append_agent_run_event(run.id, later, owner="consumer")
    assert provider_receipts(store.get_agent_run(run.id).tool_events) == ("receipt-before-crash", "receipt-after-restart")
    with sqlite3.connect(store.path) as db:
        assert "large-output" not in db.execute("select event_json from agent_run_events order by id").fetchone()[0]


def test_native_merge_preserves_unmatched_durable_receipt():
    from app.native_trajectory import event_metadata, merge_native_events
    from app.agent_effect_guard import provider_receipts
    events = [{"type": "item.completed", "item": {"type": "command_execution", "id": name, "exit_code": 0,
               "aggregated_output": json.dumps({"result": {"openMessageId": name}})}} for name in ("A", "B")]
    merged = merge_native_events([events[0]], [event_metadata(event) for event in events])
    assert provider_receipts(merged) == ("A", "B")
    events[1]["item"]["id"] = events[0]["item"]["id"]
    assert provider_receipts(merge_native_events([events[0]], [event_metadata(event) for event in events])) == ("A", "B")


def test_generation_list_restores_prior_write_after_restart(tmp_path, monkeypatch):
    from collections import OrderedDict
    from app import native_trajectory
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    task = _task(store)
    run = _claim_consumer(store, task).run
    event = {"type": "item.completed", "item": {"type": "command_execution", "id": "stream-1", "command": "dws calendar create --title meeting", "exit_code": 0, "aggregated_output": '{"result":{"eventId":"calendar-write-1"}}'}}
    store.append_agent_run_event(run.id, event, owner="consumer")
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": {**event["item"], "id": "native-1", "type": "CommandExecution"}}}))
    with store._connect() as db:
        db.execute("update agent_runs set status='completed',codex_session_id='session',transcript_start_line=0,transcript_end_line=1 where id=?", (run.id,))
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *a, **kw: path)
    monkeypatch.setattr(native_trajectory, "_LIVE_EVENTS", OrderedDict())
    earlier = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)[0]
    assert earlier.tool_events[0]["item"]["command"] == event["item"]["command"]
    assert "calendar-write-1" in json.dumps(earlier.tool_events)


def test_native_codex_reader_restores_completed_tool_and_exact_window(tmp_path, monkeypatch):
    from app import native_trajectory
    path = tmp_path / "rollout.jsonl"
    records = [
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": "outside", "command": "old", "exit_code": 0, "aggregated_output": "old"}}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": "selected", "command": "dws chat message send --content hello", "exit_code": 0, "status": "completed", "aggregated_output": '{"result":{"openMessageId":"receipt-1"}}'}}},
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
        db.execute("update agent_runs set status='completed',codex_session_id='test-session' where id=?", (run.id,))
        db.execute("update agent_run_events set event_json=? where agent_run_id=?", (json.dumps(event), run.id))
    monkeypatch.setattr(storage_maintenance, "native_run_available", lambda *args: False)
    monkeypatch.setattr(storage_maintenance, "native_codex_sessions", lambda: {"test-session"})
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


def test_delivery_reconciliation_after_restart_reads_interrupted_native_turn(tmp_path, monkeypatch):
    from collections import OrderedDict
    from app import native_trajectory
    from tests.test_store import test_reconcile_failed_agent_message_requires_send_receipt_and_readback
    original = AutoReplyStore.reconcile_failed_agent_message_delivery
    paths = {}

    def restart_then_reconcile(store, **arguments):
        for run_id in (arguments["send_run_id"], arguments["readback_run_id"]):
            events = store.get_agent_run(run_id).tool_events
            session_id = f"run-{run_id}"
            path = tmp_path / f"{session_id}.jsonl"
            records = [{"type": "event_msg", "payload": {"type": "task_started"}}]
            for event in events:
                item = event.get("item", {})
                kind = {"command_execution": "CommandExecution", "agent_message": "AgentMessage"}.get(item.get("type"))
                if kind:
                    records.append({"type": "event_msg", "payload": {"type": "item_completed", "item": {**item, "type": kind}}})
            records.append({"type": "event_msg", "payload": {"type": "task_complete"}})
            # A later turn in the same native session must remain outside the read.
            records.extend([{"type": "event_msg", "payload": {"type": "task_started"}}, {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "command": "unrelated-later-turn", "exit_code": 0}}}])
            path.write_text("\n".join(json.dumps(record) for record in records))
            paths[session_id] = path
            with store._connect() as db:
                db.execute("insert into agent_runtime_attempts(agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_reference) values (?,'agent_run',?,1,'codex_oauth','codex_cli','oauth','test','failed',?,?)", (run_id, str(run_id), session_id, f"codex_session:{session_id}"))
        monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda session_id, **kwargs: paths.get(session_id))
        monkeypatch.setattr(native_trajectory, "_LIVE_EVENTS", OrderedDict())
        for run_id in (arguments["send_run_id"], arguments["readback_run_id"]):
            assert "unrelated-later-turn" not in json.dumps(store.get_agent_run(run_id).tool_events)
        return original(store, **arguments)

    monkeypatch.setattr(AutoReplyStore, "reconcile_failed_agent_message_delivery", restart_then_reconcile)
    test_reconcile_failed_agent_message_requires_send_receipt_and_readback(
        tmp_path, "content", "Delivered text.", "Delivered text.", "Delivered text.", True,
    )
