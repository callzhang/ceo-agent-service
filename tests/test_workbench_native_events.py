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


def test_workbench_tool_rows_store_metadata_and_replay_exact_native_range(tmp_path: Path, monkeypatch):
    store, turn_id, first, second = _turn_with_tools(tmp_path)
    with store._connect() as db:
        rows = db.execute(
            "select payload_json from workbench_events where id in (?,?) order by id",
            (first.id, second.id),
        ).fetchall()
    assert all("/private/source" not in row["payload_json"] for row in rows)
    assert all("provider_item" not in row["payload_json"] for row in rows)
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
    assert replay[-1].payload["native_id"] == "item_2"
    assert replay[-1].payload["provider_item"]["id"] == "exec-other-id"
    assert "WRONG TURN" not in json.dumps([event.payload for event in replay])


@pytest.mark.parametrize("native_items", [None, [], ["mcp"]])
def test_workbench_replay_marks_missing_or_mismatched_native_detail(
    tmp_path: Path, monkeypatch, native_items: list[str] | None
):
    store, turn_id, _, _ = _turn_with_tools(tmp_path)
    native_path = tmp_path / "native.jsonl"
    rows = [{"type": "session_meta", "payload": {}}]
    if native_items:
        rows.append({"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "McpToolCall", "id": "exec-other", "server": "other", "tool": "read",
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
