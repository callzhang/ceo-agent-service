"""Project active Workbench events from RAM and historical events from native turns.

An empty text payload references the whole bounded native turn: its final text
event displays all completed assistant messages. A native_ordinal references
one completed assistant message or tool completion (one-based).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.agent_result import ResultParseError, parse_agent_text_result
from app.codex_history import _content_text
from app.codex_runner import _codex_home
from app.native_trajectory import (
    _codex_turn_records,
    find_codex_session_path,
    read_claude_events,
    read_native_result_stream,
)
from app.workbench.codex_runtime import _native_tool_failed
from app.workbench.models import WorkbenchEvent, WorkbenchTurn

AGENT_EVENTS = {"text_delta", "thinking_summary", "tool_started", "tool_completed", "file_changed"}
UNAVAILABLE = "原生 Agent 记录不可用。"
_LIVE_LIMIT_BYTES = 64 * 1024 * 1024
_live_payloads: OrderedDict[tuple[str, int], tuple[int, dict[str, Any]]] = OrderedDict()
_live_turn_events: dict[tuple[str, str], set[int]] = {}
_live_tool_starts: dict[tuple[str, str, str], int] = {}
_live_bytes = 0
_live_lock = threading.Lock()


def stored_payload(event_type: str, payload: dict[str, Any], *, ordinal: int = 0) -> dict[str, Any]:
    if event_type not in AGENT_EVENTS:
        return payload
    return {"native_ordinal": ordinal} if ordinal > 0 else {}


def remember_live_payload(
    db_path: Path, turn_id: str, event_id: int, event_type: str, payload: dict[str, Any]
) -> None:
    global _live_bytes
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > _LIVE_LIMIT_BYTES:
        return
    path = str(db_path.resolve())
    with _live_lock:
        key = (path, event_id)
        prior = _live_payloads.pop(key, None)
        if prior:
            _live_bytes -= prior[0]
        _live_payloads[key] = (size, payload)
        _live_turn_events.setdefault((path, turn_id), set()).add(event_id)
        if event_type == "tool_started" and isinstance(payload.get("tool_call_id"), str):
            _live_tool_starts[(path, turn_id, payload["tool_call_id"])] = event_id
        _live_bytes += size
        while _live_bytes > _LIVE_LIMIT_BYTES:
            old_key, (old_size, _) = _live_payloads.popitem(last=False)
            _live_bytes -= old_size
            for (turn_path, _), ids in _live_turn_events.items():
                if turn_path == old_key[0]:
                    ids.discard(old_key[1])


def started_tool_event_id(db_path: Path, turn_id: str, call_id: str) -> int | None:
    with _live_lock:
        return _live_tool_starts.get((str(db_path.resolve()), turn_id, call_id))


def forget_live_turn(db_path: Path, turn_id: str) -> None:
    global _live_bytes
    path = str(db_path.resolve())
    with _live_lock:
        for event_id in _live_turn_events.pop((path, turn_id), set()):
            previous = _live_payloads.pop((path, event_id), None)
            if previous:
                _live_bytes -= previous[0]
        for key in [key for key in _live_tool_starts if key[:2] == (path, turn_id)]:
            del _live_tool_starts[key]


def terminal_cached_turn_ids(db: sqlite3.Connection, db_path: Path) -> list[str]:
    path = str(db_path.resolve())
    with _live_lock:
        ids = [turn_id for turn_path, turn_id in _live_turn_events if turn_path == path]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return [row["id"] for row in db.execute(
        f"select id from workbench_turns where id in ({placeholders}) "
        "and status in ('completed','stopped','failed','waiting_confirmation')", ids
    )]


def _attempts(db: sqlite3.Connection, turn_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "select runtime_kind,session_id,transcript_start,transcript_end,status "
        "from agent_runtime_attempts where workload_kind='workbench' and workload_key=? order by id",
        (turn_id,),
    ).fetchall()


def hydrate_turn(db: sqlite3.Connection, turn: WorkbenchTurn) -> WorkbenchTurn:
    if turn.status.value == "failed" and not turn.error_detail:
        from app.workbench.executor import _public_runtime_failure_detail

        turn.error_detail = _public_runtime_failure_detail(turn.error_code or "runtime_failure")
    if turn.status.value != "completed":
        return turn
    attempts = _attempts(db, turn.id)
    raw = ""
    if attempts:
        last = attempts[-1]
        if last["runtime_kind"] in {"codex_cli", "claude_cli"}:
            raw = read_native_result_stream(
                last["runtime_kind"], last["session_id"],
                last["transcript_start"], last["transcript_end"],
            )
    try:
        turn.final_text = parse_agent_text_result(raw) if raw else UNAVAILABLE
    except ResultParseError:
        turn.final_text = UNAVAILABLE
    return turn


def friday_refs(db: sqlite3.Connection, turns: list[WorkbenchTurn]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for turn in turns:
        if turn.status.value != "completed":
            continue
        attempts = _attempts(db, turn.id)
        if attempts and attempts[-1]["runtime_kind"] == "friday_runtime":
            refs[turn.id] = attempts[-1]["session_id"]
    return refs


def hydrate_friday_turns(turns: list[WorkbenchTurn], refs: dict[str, str]) -> None:
    if not refs:
        return
    from app.agent_runtime_config import load_runtime_config
    from app.friday_runtime_adapter import FridayRuntimeAdapter

    adapter = None
    for turn in turns:
        ref = refs.get(turn.id, "")
        if not ref:
            continue
        try:
            if adapter is None:
                adapter = FridayRuntimeAdapter(load_runtime_config(os.environ))
            text = adapter.read_final_artifact(ref.removeprefix("friday_thread:"))
            turn.final_text = text if text.strip() else UNAVAILABLE
        except (OSError, RuntimeError, ValueError):
            turn.final_text = UNAVAILABLE


def hydrate_events(
    db: sqlite3.Connection, db_path: Path, events: list[WorkbenchEvent]
) -> list[WorkbenchEvent]:
    turn_ids = sorted({event.turn_id for event in events if event.event_type in AGENT_EVENTS})
    if not turn_ids:
        return events
    placeholders = ",".join("?" for _ in turn_ids)
    statuses = {row["id"]: row["status"] for row in db.execute(
        f"select id,status from workbench_turns where id in ({placeholders})", turn_ids
    )}
    path = str(db_path.resolve())
    pending: dict[str, list[WorkbenchEvent]] = {}
    with _live_lock:
        for event in events:
            if event.event_type not in AGENT_EVENTS:
                continue
            live = _live_payloads.get((path, event.id)) if statuses.get(event.turn_id) == "running" else None
            if live:
                event.payload = live[1]
            else:
                pending.setdefault(event.turn_id, []).append(event)
    for turn_id, selected in pending.items():
        native = _native_events_for_turn(db, turn_id)
        tools, messages, reasoning, files = native if native is not None else (None, None, None, None)
        count = db.execute(
            "select count(*) from workbench_events where turn_id=? and event_type='tool_completed'",
            (turn_id,),
        ).fetchone()[0]
        if tools is not None and len(tools) != count:
            tools = None
        text_ends: dict[int, int] = {}
        whole_turn_last_text_id = 0
        for row in db.execute(
            "select id,json_extract(payload_json,'$.native_ordinal') as ordinal "
            "from workbench_events where turn_id=? and event_type='text_delta' order by id",
            (turn_id,),
        ):
            if isinstance(row["ordinal"], int):
                text_ends[row["ordinal"]] = row["id"]
            else:
                whole_turn_last_text_id = row["id"]
        for event in selected:
            ordinal = event.payload.get("native_ordinal")
            if event.event_type in {"tool_started", "tool_completed"}:
                event.payload = (
                    _tool_payload(
                        tools[ordinal - 1][0] if event.event_type == "tool_started"
                        else tools[ordinal - 1][1], ordinal, event.event_type
                    )
                    if isinstance(ordinal, int) and tools is not None and 0 < ordinal <= len(tools)
                    else {"tool_call_id": f"event-{event.id}", "summary": UNAVAILABLE}
                )
            elif event.event_type == "text_delta":
                if (isinstance(ordinal, int) and messages is not None
                    and 0 < ordinal <= len(messages) and text_ends.get(ordinal) == event.id):
                    event.payload = {"text": messages[ordinal - 1]}
                elif not isinstance(ordinal, int) and event.id == whole_turn_last_text_id:
                    event.payload = {"text": "\n\n".join(messages) if messages else UNAVAILABLE}
                else:
                    event.payload = {"text": UNAVAILABLE if text_ends.get(ordinal) == event.id else ""}
            elif event.event_type == "thinking_summary":
                event.payload = (
                    {"summary": reasoning[ordinal - 1]}
                    if isinstance(ordinal, int) and reasoning is not None and 0 < ordinal <= len(reasoning)
                    else {"summary": UNAVAILABLE}
                )
            else:
                event.payload = (
                    _file_payload(files[ordinal - 1])
                    if isinstance(ordinal, int) and files is not None and 0 < ordinal <= len(files)
                    else {"change": UNAVAILABLE}
                )
    return events


def _native_events_for_turn(
    db: sqlite3.Connection, turn_id: str
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[str], list[str], list[dict[str, Any]]] | None:
    attempts = _attempts(db, turn_id)
    if not attempts:
        return None
    tools: list[tuple[dict[str, Any], dict[str, Any]]] = []
    messages: list[str] = []
    reasoning: list[str] = []
    files: list[dict[str, Any]] = []
    for attempt in attempts:
        if not attempt["session_id"] or attempt["transcript_end"] <= attempt["transcript_start"]:
            return None
        if attempt["runtime_kind"] == "codex_cli":
            path = find_codex_session_path(attempt["session_id"], codex_home=_codex_home())
            records = _codex_turn_records(path, attempt["transcript_start"], attempt["transcript_end"])
            if not records:
                return None
            completed_messages: list[str] = []
            fallback_messages: list[str] = []
            started_tools: dict[str, dict[str, Any]] = {}
            for record in records:
                body = record.get("payload")
                if not isinstance(body, dict):
                    continue
                if record.get("type") == "event_msg" and body.get("type") == "item_started":
                    item = body.get("item")
                    if isinstance(item, dict) and item.get("type") in {"CommandExecution", "McpToolCall"}:
                        native_id = item.get("id")
                        if isinstance(native_id, str) and native_id:
                            started_tools[native_id] = item
                    continue
                if record.get("type") == "event_msg" and body.get("type") == "item_completed":
                    item = body.get("item")
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") in {"CommandExecution", "McpToolCall"}:
                        native_id = item.get("id")
                        started = started_tools.pop(native_id, None) if isinstance(native_id, str) else None
                        if started is None or started.get("type") != item.get("type"):
                            started = item
                        tools.append((started, {**started, **item}))
                    elif item.get("type") == "AgentMessage":
                        value = item.get("text") or _content_text(item.get("content"))
                        if isinstance(value, str) and value:
                            completed_messages.append(value)
                    elif item.get("type") == "Reasoning":
                        value = _content_text(item.get("summary"))
                        if value:
                            reasoning.append(value)
                    elif item.get("type") == "FileChange":
                        files.append(item)
                elif (record.get("type") == "response_item" and body.get("type") == "message"
                      and body.get("role") == "assistant"):
                    value = _content_text(body.get("content"))
                    if value:
                        fallback_messages.append(value)
                elif record.get("type") == "response_item" and body.get("type") == "reasoning":
                    value = _content_text(body.get("summary"))
                    if value and value not in reasoning:
                        reasoning.append(value)
            messages.extend(completed_messages or fallback_messages)
        elif attempt["runtime_kind"] == "claude_cli":
            native = read_claude_events(
                attempt["session_id"], start_line=attempt["transcript_start"],
                end_line=attempt["transcript_end"],
            )
            if not native:
                return None
            started_tools: dict[str, dict[str, Any]] = {}
            for event in native:
                if event.get("type") == "item.started":
                    item = event.get("item")
                    if isinstance(item, dict) and item.get("type") in {"command_execution", "mcp_tool_call"}:
                        native_id = item.get("id")
                        if isinstance(native_id, str) and native_id:
                            started_tools[native_id] = item
                    continue
                if event.get("type") != "item.completed":
                    continue
                item = event.get("item")
                if isinstance(item, dict):
                    if item.get("type") in {"command_execution", "mcp_tool_call"}:
                        native_id = item.get("id")
                        started = started_tools.pop(native_id, None) if isinstance(native_id, str) else None
                        if started is None or started.get("type") != item.get("type"):
                            started = item
                        tools.append((started, {**started, **item}))
                    elif item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                        messages.append(item["text"])
                    elif item.get("type") == "reasoning":
                        value = _content_text(item.get("summary"))
                        if value:
                            reasoning.append(value)
                    elif item.get("type") == "file_change":
                        files.append(item)
        else:
            return None
    return tools, messages, reasoning, files


def _file_payload(item: dict[str, Any]) -> dict[str, Any]:
    changes = item.get("changes")
    first = changes[0] if isinstance(changes, list) and changes and isinstance(changes[0], dict) else item
    path = first.get("path")
    change = first.get("kind") or first.get("change")
    result = {"status": str(item.get("status") or "completed")}
    if isinstance(path, str):
        result.update({"path": path, "filename": Path(path).name})
    if isinstance(change, str):
        result["change"] = change
    return result


def _tool_payload(item: dict[str, Any], ordinal: int, event_type: str) -> dict[str, Any]:
    kind = "command" if item.get("type") in {"CommandExecution", "command_execution"} else "mcp"
    result: dict[str, Any] = {
        "tool_call_id": f"native-tool-{ordinal}", "kind": kind,
        "native_id": str(item.get("id") or ""),
        "status": "running" if event_type == "tool_started" else "completed",
    }
    if event_type == "tool_completed" and _native_tool_failed(item):
        result["status"] = "failed"
    if kind == "command":
        command = item.get("command")
        result["name"] = command.strip().split(maxsplit=1)[0] if isinstance(command, str) and command.strip() else "command_execution"
        fields = {"command": "command", "cwd": "cwd"}
        if event_type == "tool_completed":
            fields.update({"aggregated_output": "output", "exit_code": "exit_code"})
            if isinstance(item.get("exit_code"), int) and item["exit_code"] != 0:
                result["status"] = "failed"
    else:
        result["server"] = item.get("server", "")
        result["tool"] = item.get("tool", "")
        result["name"] = f"{result['server']}.{result['tool']}".strip(".")
        fields = {"arguments": "arguments"}
        if event_type == "tool_completed":
            fields["result"] = "result"
    result.update({target: item[source] for source, target in fields.items() if source in item})
    if event_type == "tool_completed":
        result["provider_item"] = item
    return result
