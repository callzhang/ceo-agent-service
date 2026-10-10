import json
from collections import OrderedDict
from pathlib import Path

import pytest

from app.workbench import native_events
from app.workbench.store import WorkbenchStore


def _turn_with_tools(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Native replay", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="request-1")
    assert store.claim_next_turn(owner="worker", lease_seconds=60) is not None
    started = {
        "tool_call_id": "tool-call-1", "kind": "command", "name": "pwd",
        "native_id": "item_2", "status": "running", "command": "pwd",
        "cwd": "/private/source", "provider_item": {"id": "item_2", "command": "pwd"},
    }
    completed = {
        **started, "status": "completed", "exit_code": 0,
        "output": "/private/source", "provider_item": {
            "id": "item_2", "command": "pwd", "aggregated_output": "/private/source",
        },
    }
    first = store.append_event(turn.id, sequence=2, event_type="tool_started", payload=started, owner="worker")
    second = store.append_event(turn.id, sequence=3, event_type="tool_completed", payload=completed, owner="worker")
    return store, turn.id, first, second


def _forget_live(monkeypatch):
    monkeypatch.setattr(native_events, "_live_payloads", OrderedDict())
    monkeypatch.setattr(native_events, "_live_bytes", 0)


def test_workbench_tool_rows_store_only_native_ordinal_and_replay_exact_native_range(tmp_path: Path, monkeypatch):
    store, turn_id, first, second = _turn_with_tools(tmp_path)
    with store._connect() as db:
        rows = db.execute(
            "select payload_json from workbench_events where id in (?,?) order by id",
            (first.id, second.id),
        ).fetchall()
    assert all("/private/source" not in row["payload_json"] for row in rows)
    assert all("provider_item" not in row["payload_json"] for row in rows)
    assert [json.loads(row["payload_json"]) for row in rows] == [
        {"native_ordinal": 1}, {"native_ordinal": 1},
    ]
    assert store.events_after(turn_id)[-1].payload["output"] == "/private/source"

    native_path = tmp_path / "native.jsonl"
    native_path.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": "exec-other-id", "command": "pwd",
            "cwd": "/private/source", "aggregated_output": "/private/source", "exit_code": 0,
        }}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": "exec-next-turn", "command": "pwd",
            "aggregated_output": "WRONG TURN", "exit_code": 0,
        }}},
    ]) + "\n")
    with store._connect() as db:
        db.execute(
            "insert into agent_runtime_attempts "
            "(workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_start,transcript_end) "
            "values ('workbench',?,1,'primary','codex_cli','oauth','model','completed','session-id',0,2)",
            (turn_id,),
        )
    monkeypatch.setattr(native_events, "find_codex_session_path", lambda *_args, **_kwargs: native_path)
    _forget_live(monkeypatch)
    replay = WorkbenchStore(store.path).events_after(turn_id)
    assert replay[-2].payload["command"] == "pwd"
    assert replay[-1].payload["output"] == "/private/source"
    assert replay[-1].payload["native_id"] == "exec-other-id"
    assert replay[-1].payload["provider_item"]["id"] == "exec-other-id"
    assert "WRONG TURN" not in json.dumps([event.payload for event in replay])


@pytest.mark.parametrize("native_items", [None, [], ["mcp", "mcp"]])
def test_workbench_replay_marks_missing_or_mismatched_native_detail(
    tmp_path: Path, monkeypatch, native_items: list[str] | None
):
    store, turn_id, _, _ = _turn_with_tools(tmp_path)
    native_path = tmp_path / "native.jsonl"
    rows = [{"type": "session_meta", "payload": {}}]
    for index, _ in enumerate(native_items or []):
        rows.append({"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "McpToolCall", "id": f"exec-other-{index}", "server": "other", "tool": "read",
        }}})
    native_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with store._connect() as db:
        db.execute(
            "insert into agent_runtime_attempts "
            "(workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_start,transcript_end) "
            "values ('workbench',?,1,'primary','codex_cli','oauth','model','completed','session-id',0,?)",
            (turn_id, len(rows)),
        )
    monkeypatch.setattr(
        native_events, "find_codex_session_path",
        lambda *_args, **_kwargs: native_path if native_items is not None else None,
    )
    _forget_live(monkeypatch)
    replay = WorkbenchStore(store.path).events_after(turn_id)
    assert "不可用" in replay[-1].payload["summary"]
    assert "output" not in replay[-1].payload


def test_agent_event_rows_and_final_text_are_native_only(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Private", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="user input", client_request_id="r1")
    assert store.claim_next_turn(owner="worker") is not None
    for sequence, event_type, payload in (
        (2, "text_delta", {"text": "SECRET_TEXT"}),
        (3, "thinking_summary", {"text": "SECRET_THINKING"}),
        (4, "file_changed", {"filename": "SECRET_FILE", "path": "/SECRET_PATH"}),
        (5, "tool_started", {"tool_call_id": "tool-call-1", "kind": "command", "name": "SECRET_NAME", "native_id": "SECRET_NATIVE", "status": "running", "command": "SECRET_ARGS"}),
        (6, "tool_completed", {"tool_call_id": "tool-call-1", "kind": "command", "name": "SECRET_NAME", "native_id": "SECRET_NATIVE", "status": "completed", "output": "SECRET_OUTPUT", "provider_item": {"secret": "SECRET_META"}}),
    ):
        store.append_event(turn.id, sequence=sequence, event_type=event_type, payload=payload, owner="worker")
    store.complete_turn(turn.id, status="completed", final_text="SECRET_FINAL", owner="worker")
    with store._connect() as db:
        rows = db.execute("select payload_json from workbench_events where turn_id=?", (turn.id,)).fetchall()
        final = db.execute("select final_text from workbench_turns where id=?", (turn.id,)).fetchone()[0]
    stored = " ".join(row[0] for row in rows)
    for secret in ("SECRET_TEXT", "SECRET_THINKING", "SECRET_FILE", "SECRET_PATH", "SECRET_NAME", "SECRET_NATIVE", "SECRET_ARGS", "SECRET_OUTPUT", "SECRET_META", "SECRET_FINAL"):
        assert secret not in stored
    assert final == ""
    assert not any(path == str(store.path.resolve()) for path, _ in native_events._live_payloads)
    restored = WorkbenchStore(store.path).get_turn(turn.id)
    assert restored.status.value == "completed"
    assert "不可用" in restored.final_text


def test_out_of_order_completed_tools_replay_from_native_completion_ordinals(tmp_path: Path, monkeypatch):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Native order", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="r1")
    assert store.claim_next_turn(owner="worker") is not None
    for sequence, event_type, call, command in (
        (2, "tool_started", "tool-call-1", "first"),
        (3, "tool_started", "tool-call-2", "second"),
        (4, "tool_completed", "tool-call-2", "second"),
        (5, "tool_completed", "tool-call-1", "first"),
    ):
        store.append_event(turn.id, sequence=sequence, event_type=event_type, payload={
            "tool_call_id": call, "kind": "command", "name": command,
            "native_id": f"stdout-{call}", "status": "running" if event_type == "tool_started" else "completed",
            "command": command, "output": f"{command}-output",
        }, owner="worker")
    native_path = tmp_path / "native.jsonl"
    native_path.write_text("\n".join(json.dumps(row) for row in [
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": "native-second", "command": "second", "aggregated_output": "second-output"}}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "id": "native-first", "command": "first", "status": "failed", "exit_code": None, "aggregated_output": "first-output"}}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]) + "\n")
    with store._connect() as db:
        db.execute("insert into agent_runtime_attempts (workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_start,transcript_end) values ('workbench',?,1,'primary','codex_cli','oauth','model','completed','session-id',0,4)", (turn.id,))
    monkeypatch.setattr(native_events, "find_codex_session_path", lambda *_args, **_kwargs: native_path)
    store.complete_turn(turn.id, status="completed", owner="worker")
    _forget_live(monkeypatch)
    replay = WorkbenchStore(store.path).events_after(turn.id)
    assert [(event.event_type, event.payload.get("command")) for event in replay[1:5]] == [
        ("tool_started", "first"), ("tool_started", "second"),
        ("tool_completed", "second"), ("tool_completed", "first"),
    ]
    assert replay[4].payload["status"] == "failed"
    assert replay[4].payload["exit_code"] is None
    assert [(event.id, event.sequence) for event in replay] == [(event.id, index) for index, event in enumerate(replay, 1)]


def test_native_tool_start_inputs_survive_completion_with_only_outputs(tmp_path: Path, monkeypatch):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Split native tools", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="r1")
    assert store.claim_next_turn(owner="worker") is not None
    for sequence, event_type, call_id in (
        (2, "tool_started", "stdout-command"),
        (3, "tool_started", "stdout-mcp"),
        (4, "tool_completed", "stdout-mcp"),
        (5, "tool_completed", "stdout-command"),
    ):
        store.append_event(turn.id, sequence=sequence, event_type=event_type, payload={
            "tool_call_id": call_id, "command": "PRIVATE COMMAND",
            "arguments": {"private": "ARGUMENTS"}, "output": "PRIVATE OUTPUT",
        }, owner="worker")
    native_path = tmp_path / "native.jsonl"
    records = [
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {"type": "item_started", "item": {
            "type": "CommandExecution", "id": "native-command", "command": "pwd", "cwd": "/native/cwd",
        }}},
        {"type": "event_msg", "payload": {"type": "item_started", "item": {
            "type": "McpToolCall", "id": "native-mcp", "server": "memory", "tool": "recall",
            "arguments": {"query": "native arguments"},
        }}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "McpToolCall", "id": "native-mcp", "result": {"content": "native result"},
        }}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": "native-command", "aggregated_output": "native output", "exit_code": 0,
        }}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]
    native_path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    with store._connect() as db:
        db.execute("insert into agent_runtime_attempts (workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_start,transcript_end) values ('workbench',?,1,'primary','codex_cli','oauth','model','completed','session-id',0,?)", (turn.id, len(records)))
    monkeypatch.setattr(native_events, "find_codex_session_path", lambda *_args, **_kwargs: native_path)
    store.complete_turn(turn.id, status="completed", owner="worker")
    replay = WorkbenchStore(store.path).events_after(turn.id)
    command_start, mcp_start, mcp_complete, command_complete = (event.payload for event in replay[1:5])
    assert command_start["command"] == "pwd"
    assert command_start["cwd"] == "/native/cwd"
    assert "output" not in command_start
    assert mcp_start["name"] == "memory.recall"
    assert mcp_start["arguments"] == {"query": "native arguments"}
    assert "result" not in mcp_start
    assert mcp_complete["arguments"] == {"query": "native arguments"}
    assert mcp_complete["result"] == {"content": "native result"}
    assert mcp_complete["provider_item"]["server"] == "memory"
    assert command_complete["command"] == "pwd"
    assert command_complete["output"] == "native output"
    assert command_complete["provider_item"]["cwd"] == "/native/cwd"


def test_native_messages_replay_as_completed_messages_with_stable_event_pages(tmp_path: Path, monkeypatch):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Messages", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="r1")
    assert store.claim_next_turn(owner="worker") is not None
    for sequence, ordinal, text in ((2, 1, "partial-"), (3, 1, "first"), (4, 2, "final")):
        store.append_event(turn.id, sequence=sequence, event_type="text_delta",
                           payload={"text": text, "native_ordinal": ordinal}, owner="worker")
    native_path = tmp_path / "native.jsonl"
    native_path.write_text("\n".join(json.dumps(row) for row in [
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "AgentMessage", "text": "native first"}}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "AgentMessage", "text": "native final"}}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "AgentMessage", "text": "WRONG TURN"}}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]) + "\n")
    with store._connect() as db:
        db.execute("insert into agent_runtime_attempts (workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_start,transcript_end) values ('workbench',?,1,'primary','codex_cli','oauth','model','completed','session-id',0,4)", (turn.id,))
    monkeypatch.setattr(native_events, "find_codex_session_path", lambda *_args, **_kwargs: native_path)
    from app import native_trajectory
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: native_path)
    store.complete_turn(turn.id, status="completed", final_text="PRIVATE FINAL", owner="worker")
    _forget_live(monkeypatch)
    replay_store = WorkbenchStore(store.path)
    pages = []
    after = 0
    while page := replay_store.events_after(turn.id, after_id=after, limit=2):
        pages.extend(page)
        after = page[-1].id
    assert [event.id for event in pages] == sorted(event.id for event in pages)
    assert [event.sequence for event in pages] == list(range(1, len(pages) + 1))
    assert [event.payload["text"] for event in pages if event.event_type == "text_delta"] == [
        "", "native first", "native final",
    ]
    assert replay_store.get_turn(turn.id).final_text == "native final"
    assert replay_store.list_turns(task.id)[0].final_text == "native final"
    assert replay_store.timeline_snapshot(task.id)[1][0].final_text == "native final"
    assert "WRONG TURN" not in json.dumps([event.payload for event in pages])
    with replay_store._connect() as db:
        db.execute(
            "update agent_runtime_attempts set transcript_end=7 "
            "where workload_kind='workbench' and workload_key=?", (turn.id,)
        )
    assert "不可用" in replay_store.get_turn(turn.id).final_text
    assert "不可用" in replay_store.events_after(turn.id)[-2].payload["text"]
    with replay_store._connect() as db:
        db.execute(
            "update agent_runtime_attempts set transcript_end=4 "
            "where workload_kind='workbench' and workload_key=?", (turn.id,)
        )
        db.execute(
            "update workbench_events set payload_json='{}' "
            "where turn_id=? and event_type='text_delta'", (turn.id,)
        )
    legacy = WorkbenchStore(store.path).events_after(turn.id)
    assert [event.payload["text"] for event in legacy if event.event_type == "text_delta"] == [
        "", "", "native first\n\nnative final",
    ]


@pytest.mark.parametrize("source", ["missing", "malformed", "truncated"])
def test_completed_turn_keeps_business_status_and_marks_native_unavailable(
    tmp_path: Path, monkeypatch, source: str
):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Unavailable", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="r1")
    assert store.claim_next_turn(owner="worker") is not None
    store.append_event(turn.id, sequence=2, event_type="text_delta",
                       payload={"text": "PRIVATE", "native_ordinal": 1}, owner="worker")
    native_path = tmp_path / "native.jsonl"
    native_path.write_text("{bad}\n" if source == "malformed" else
                           json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
    end = 2 if source == "truncated" else 1
    with store._connect() as db:
        db.execute("insert into agent_runtime_attempts (workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_start,transcript_end) values ('workbench',?,1,'primary','codex_cli','oauth','model','completed','session-id',0,?)", (turn.id, end))
    path = None if source == "missing" else native_path
    monkeypatch.setattr(native_events, "find_codex_session_path", lambda *_args, **_kwargs: path)
    from app import native_trajectory
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: path)
    store.complete_turn(turn.id, status="completed", final_text="PRIVATE FINAL", owner="worker")
    replay = WorkbenchStore(store.path)
    result = replay.get_turn(turn.id)
    assert result.status.value == "completed"
    assert "不可用" in result.final_text
    assert "不可用" in replay.events_after(turn.id)[1].payload["text"]


def test_friday_final_artifact_is_read_after_sqlite_snapshot(tmp_path: Path, monkeypatch):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Friday", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="r1")
    assert store.claim_next_turn(owner="worker") is not None
    with store._connect() as db:
        db.execute("insert into agent_runtime_attempts (workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_reference) values ('workbench',?,1,'primary','friday_runtime','oauth','model','completed','friday_thread:thread-1','friday_operation:op-1')", (turn.id,))
    store.complete_turn(turn.id, status="completed", final_text="PRIVATE FINAL", owner="worker")
    from app import agent_runtime_config, friday_runtime_adapter
    monkeypatch.setattr(agent_runtime_config, "load_runtime_config", lambda _env: object())
    class Reader:
        def __init__(self, _config):
            pass
        def read_final_artifact(self, ref):
            assert ref == "thread-1"
            with store._connect() as db:
                db.execute("begin immediate")
            return "Native Friday answer"
    monkeypatch.setattr(friday_runtime_adapter, "FridayRuntimeAdapter", Reader)
    assert store.get_turn(turn.id).final_text == "Native Friday answer"
    assert store.list_turns(task.id)[0].final_text == "Native Friday answer"
    assert store.timeline_snapshot(task.id)[1][0].final_text == "Native Friday answer"


def test_native_reasoning_and_file_change_recover_without_sqlite_copy(tmp_path: Path, monkeypatch):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Details", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="r1")
    assert store.claim_next_turn(owner="worker") is not None
    store.append_event(turn.id, sequence=2, event_type="thinking_summary",
                       payload={"summary": "PRIVATE THINKING"}, owner="worker")
    store.append_event(turn.id, sequence=3, event_type="file_changed",
                       payload={"filename": "PRIVATE FILE", "path": "/private/old"}, owner="worker")
    native_path = tmp_path / "native.jsonl"
    native_path.write_text("\n".join(json.dumps(row) for row in [
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "Reasoning", "summary": [{"text": "Native reasoning"}]}}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "FileChange", "status": "completed", "changes": [{"path": "/private/new.txt", "kind": "add"}]}}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]) + "\n")
    with store._connect() as db:
        db.execute("insert into agent_runtime_attempts (workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,session_id,transcript_start,transcript_end) values ('workbench',?,1,'primary','codex_cli','oauth','model','failed','session-id',0,4)", (turn.id,))
    monkeypatch.setattr(native_events, "find_codex_session_path", lambda *_args, **_kwargs: native_path)
    store.complete_turn(turn.id, status="failed", error_code="runtime_failure", owner="worker")
    _forget_live(monkeypatch)
    events = WorkbenchStore(store.path).events_after(turn.id)
    assert events[1].payload == {"summary": "Native reasoning"}
    assert events[2].payload == {
        "status": "completed", "path": "/private/new.txt", "filename": "new.txt", "change": "add",
    }
    with store._connect() as db:
        rows = db.execute("select payload_json from workbench_events where turn_id=? and event_type in ('thinking_summary','file_changed') order by id", (turn.id,)).fetchall()
    assert [json.loads(row[0]) for row in rows] == [{"native_ordinal": 1}, {"native_ordinal": 1}]


def test_invalid_tool_completion_sequence_keeps_started_slot_for_retry(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Retry", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="r1")
    assert store.claim_next_turn(owner="worker") is not None
    started = store.append_event(turn.id, sequence=2, event_type="tool_started",
                                 payload={"tool_call_id": "stdout-id", "command": "pwd"}, owner="worker")
    with pytest.raises(ValueError, match="event sequence must be next"):
        store.append_event(turn.id, sequence=4, event_type="tool_completed",
                           payload={"tool_call_id": "stdout-id", "output": "private"}, owner="worker")
    store.append_event(turn.id, sequence=3, event_type="tool_completed",
                       payload={"tool_call_id": "stdout-id", "output": "private"}, owner="worker")
    with store._connect() as db:
        row = db.execute("select payload_json from workbench_events where id=?", (started.id,)).fetchone()
    assert json.loads(row[0]) == {"native_ordinal": 1}
    store.request_stop(turn.id)
    assert not any(path == str(store.path.resolve()) for path, _ in native_events._live_payloads)


def test_terminal_cache_release_follows_commit_and_rollback_keeps_live_observation(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    task = store.create_task(title="Rollback", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Inspect", client_request_id="r1")
    assert store.claim_next_turn(owner="worker") is not None
    event = store.append_event(turn.id, sequence=2, event_type="text_delta",
                               payload={"text": "observed"}, owner="worker")
    key = (str(store.path.resolve()), event.id)
    assert key in native_events._live_payloads
    with pytest.raises(RuntimeError, match="rollback"):
        with store._connect() as db:
            db.execute("begin immediate")
            db.execute("update workbench_turns set status='failed' where id=?", (turn.id,))
            raise RuntimeError("rollback")
    assert store.get_turn(turn.id).status.value == "running"
    assert store.events_after(turn.id)[1].payload["text"] == "observed"
    assert key in native_events._live_payloads
    store.complete_turn(turn.id, status="failed", error_code="runtime_failure", owner="worker")
    assert key not in native_events._live_payloads
