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
        assert db.execute("select count(*) from agent_run_events where agent_run_id=?", (run.id,)).fetchone()[0] == 0


def test_oversized_event_uses_native_after_live_cache_limit(tmp_path, monkeypatch):
    from collections import OrderedDict
    from app import native_trajectory
    monkeypatch.setattr(native_trajectory, "_LIVE_EVENTS", OrderedDict())
    monkeypatch.setattr(native_trajectory, "_LIVE_BYTES", 0)
    monkeypatch.setattr(native_trajectory, "_LIVE_LIMIT_BYTES", 200)
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    for call_id, output in (("A", "small"), ("B", "large" * 200)):
        store.append_agent_run_event(run.id, {"type": "item.completed", "item": {"type": "command_execution", "id": call_id, "exit_code": 0, "aggregated_output": output}}, owner="consumer")
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": call_id, "exit_code": 0, "aggregated_output": output}}}) for call_id, output in (("A", "small"), ("B", "large" * 200))) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *a, **k: path)
    with store._connect() as db:
        db.execute("update agent_runs set codex_session_id='native-session',transcript_end_line=2 where id=?", (run.id,))
    assert [event["item"]["id"] for event in store.get_agent_run(run.id).tool_events] == ["A", "B"]


def test_restart_preserves_receipts_before_attempt_finishes(tmp_path, monkeypatch):
    from collections import OrderedDict
    from app import native_trajectory
    from app.agent_effect_guard import provider_receipts
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    event = {"type": "item.completed", "item": {"type": "command_execution", "id": "call-1", "exit_code": 0, "aggregated_output": '{"result":{"openTaskId":"receipt-before-crash"},"private":"large-output"}'}}
    store.append_agent_run_event(run.id, event, owner="consumer")
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": {**event["item"], "type": "CommandExecution"}}}) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *a, **k: path)
    with store._connect() as db:
        db.execute("update agent_runs set codex_session_id='native-session',transcript_end_line=1 where id=?", (run.id,))
    monkeypatch.setattr(native_trajectory, "_LIVE_EVENTS", OrderedDict())
    assert provider_receipts(store.get_agent_run(run.id).tool_events) == ("receipt-before-crash",)
    later = {"type": "item.completed", "item": {"type": "command_execution", "id": "call-2", "exit_code": 0, "aggregated_output": '{"result":{"openMessageId":"receipt-after-restart"}}'}}
    store.append_agent_run_event(run.id, later, owner="consumer")
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": {**later["item"], "type": "CommandExecution"}}}) + "\n")
    assert provider_receipts(store.get_agent_run(run.id).tool_events) == ("receipt-before-crash", "receipt-after-restart")
    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from agent_run_events").fetchone()[0] == 0


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


def test_zero_native_range_does_not_read_reused_session(tmp_path, monkeypatch):
    from app import native_trajectory
    store = AutoReplyStore(tmp_path / "runs.sqlite3")
    run = _claim_consumer(store, _task(store)).run
    with store._connect() as db:
        db.execute("update agent_runs set codex_session_id='reused' where id=?", (run.id,))
        row = db.execute("select * from agent_runs where id=?", (run.id,)).fetchone()
        monkeypatch.setattr(native_trajectory, "read_codex_events", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read unrelated later turn")))
        assert native_trajectory.read_run_events(db, row) == []


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
    assert events[0]["item"]["arguments"] == {"command": "private"}


def test_delivery_reconciliation_after_restart_reads_interrupted_native_turn(tmp_path, monkeypatch):
    from collections import OrderedDict
    from app import native_trajectory
    from tests.test_store import test_reconcile_failed_agent_message_requires_send_receipt_and_readback
    original = AutoReplyStore.reconcile_failed_agent_message_delivery
    append_event = AutoReplyStore.append_agent_run_event
    paths = {}
    captured_events = {}

    def capture_event(store, run_id, event, **kwargs):
        result = append_event(store, run_id, event, **kwargs)
        captured_events.setdefault(run_id, []).append(event)
        return result

    monkeypatch.setattr(AutoReplyStore, 'append_agent_run_event', capture_event)

    def restart_then_reconcile(store, **arguments):
        send = store.get_agent_run(arguments["send_run_id"])
        consumer_id = send.parent_agent_run_id
        task = store.get_reply_task(send.reply_task_id)
        consumer_wire = {
            "outcome": "proposal", "summary": "Send the reviewed text.",
            "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
            "information_completeness": 1.0,
            "error_code": "", "error_retryable": False,
            "error_authorization_required": False,
            "durable_memories": [], "decision_options": [],
            "proposal": {
                "objective": "Deliver the message", "sourced_facts": [],
                "authored_judgment": "Send the reviewed text.",
                "actions": [{
                    "description": "Send message", "action_identity": "reply",
                    "capability": "dingtalk-chat", "operation": "send_group_message",
                    "target": {"conversation_id": task.conversation_id},
                    "payload": {"content": "Delivered text."}, "effect": "external",
                }],
            },
        }
        consumer_session = f"run-{consumer_id}"
        consumer_path = tmp_path / f"{consumer_session}.jsonl"
        consumer_path.write_text(json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": json.dumps(consumer_wire)}
            ],
        }}) + "\n")
        paths[consumer_session] = consumer_path
        with store._connect() as db:
            db.execute("update agent_runs set codex_session_id=?, transcript_end_line=1 where id=?", (consumer_session, consumer_id))
        for run_id in (arguments["send_run_id"], arguments["readback_run_id"]):
            events = captured_events.get(run_id, [])
            session_id = f"run-{run_id}"
            path = tmp_path / f"{session_id}.jsonl"
            records = [{"type": "event_msg", "payload": {"type": "task_started"}}]
            for event in events:
                item = event.get("item", {})
                kind = {"command_execution": "CommandExecution", "agent_message": "AgentMessage"}.get(item.get("type"))
                if kind:
                    records.append({"type": "event_msg", "payload": {"type": "item_completed", "item": {**item, "type": kind}}})
            records.append({"type": "event_msg", "payload": {"type": "task_complete"}})
            native_end = len(records)
            # A later turn in the same native session must remain outside the read.
            records.extend([{"type": "event_msg", "payload": {"type": "task_started"}}, {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "command": "unrelated-later-turn", "exit_code": 0}}}])
            path.write_text("\n".join(json.dumps(record) for record in records))
            paths[session_id] = path
            with store._connect() as db:
                db.execute("insert into agent_runtime_attempts(agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_reference,transcript_end) values (?,'agent_run',?,1,'codex_oauth','codex_cli','oauth','test','failed',?,?,?)", (run_id, str(run_id), session_id, f"codex_session:{session_id}", native_end))
        monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda session_id, **kwargs: paths.get(session_id))
        monkeypatch.setattr(native_trajectory, "_LIVE_EVENTS", OrderedDict())
        for run_id in (arguments["send_run_id"], arguments["readback_run_id"]):
            assert "unrelated-later-turn" not in json.dumps(store.get_agent_run(run_id).tool_events)
        return original(store, **arguments)

    monkeypatch.setattr(AutoReplyStore, "reconcile_failed_agent_message_delivery", restart_then_reconcile)
    test_reconcile_failed_agent_message_requires_send_receipt_and_readback(
        tmp_path, "content", "Delivered text.", "Delivered text.", "Delivered text.", True,
    )
