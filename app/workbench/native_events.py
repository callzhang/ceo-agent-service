"""Keep Workbench tool details in memory or read their bounded native turn."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.codex_history import find_codex_session_path
from app.codex_runner import _codex_home
from app.workbench.models import WorkbenchEvent


_TOOL_EVENTS = {"tool_started", "tool_completed"}
_LIVE_LIMIT_BYTES = 64 * 1024 * 1024
_live_payloads: OrderedDict[tuple[str, int], tuple[int, dict[str, Any]]] = OrderedDict()
_live_bytes = 0
_live_lock = threading.Lock()


def stored_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type not in _TOOL_EVENTS:
        return payload
    allowed = (
        "tool_call_id", "kind", "name", "native_id", "status", "server",
        "tool", "exit_code", "summary",
    )
    result: dict[str, Any] = {"_native_detail": True}
    for key in allowed:
        value = payload.get(key)
        if isinstance(value, str):
            result[key] = value[:512]
        elif key == "exit_code" and isinstance(value, int) and not isinstance(value, bool):
            result[key] = value
    return result


def remember_live_payload(db_path: Path, event_id: int, payload: dict[str, Any]) -> None:
    global _live_bytes
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > _LIVE_LIMIT_BYTES:
        return
    key = (str(db_path.resolve()), event_id)
    with _live_lock:
        old = _live_payloads.pop(key, None)
        if old is not None:
            _live_bytes -= old[0]
        _live_payloads[key] = (size, payload)
        _live_bytes += size
        while _live_bytes > _LIVE_LIMIT_BYTES:
            _, (removed_size, _) = _live_payloads.popitem(last=False)
            _live_bytes -= removed_size


def hydrate_events(
    db: sqlite3.Connection, db_path: Path, events: list[WorkbenchEvent]
) -> list[WorkbenchEvent]:
    pending: dict[str, list[WorkbenchEvent]] = {}
    path_key = str(db_path.resolve())
    with _live_lock:
        for event in events:
            if event.event_type not in _TOOL_EVENTS:
                continue
            is_native_reference = event.payload.pop("_native_detail", False)
            live = _live_payloads.get((path_key, event.id))
            if live is not None:
                event.payload = live[1]
            elif is_native_reference:
                pending.setdefault(event.turn_id, []).append(event)

    for turn_id, turn_events in pending.items():
        native_by_call = _native_tools_for_turn(db, turn_id)
        for event in turn_events:
            tool = native_by_call.get(str(event.payload.get("tool_call_id", "")))
            if tool is None:
                event.payload["summary"] = "原生 Agent 工具记录不可用。"
                continue
            event.payload.update(_detail_payload(tool, completed=event.event_type == "tool_completed"))
    return events


def _native_tools_for_turn(db: sqlite3.Connection, turn_id: str) -> dict[str, dict[str, Any]]:
    stored = db.execute(
        "select event_type,payload_json from workbench_events "
        "where turn_id=? and event_type in ('tool_started','tool_completed') order by sequence",
        (turn_id,),
    ).fetchall()
    completed = [json.loads(row["payload_json"]) for row in stored if row["event_type"] == "tool_completed"]
    attempts = db.execute(
        "select runtime_kind,session_id,transcript_start,transcript_end "
        "from agent_runtime_attempts where workload_kind='workbench' and workload_key=? order by id",
        (turn_id,),
    ).fetchall()
    if not attempts or any(
        row["runtime_kind"] != "codex_cli"
        or not row["session_id"]
        or row["transcript_end"] <= row["transcript_start"]
        for row in attempts
    ):
        return {}
    native: list[dict[str, Any]] = []
    for attempt in attempts:
        items = _read_native_tools(
            attempt["session_id"], attempt["transcript_start"], attempt["transcript_end"]
        )
        if items is None:
            return {}
        native.extend(items)
    if len(native) != len(completed):
        return {}
    for metadata, item in zip(completed, native, strict=True):
        kind = "command" if item["type"] == "CommandExecution" else "mcp"
        if metadata.get("kind") != kind:
            return {}
        if kind == "mcp" and (
            metadata.get("server") != item.get("server")
            or metadata.get("tool") != item.get("tool")
        ):
            return {}
        if kind == "command" and metadata.get("name") != _command_name(item):
            return {}
    return {str(meta["tool_call_id"]): item for meta, item in zip(completed, native, strict=True)}


def _read_native_tools(session_id: str, start: int, end: int) -> list[dict[str, Any]] | None:
    path = find_codex_session_path(session_id, codex_home=_codex_home())
    if path is None:
        return None
    items: list[dict[str, Any]] = []
    line_count = 0
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream):
                line_count = line_number + 1
                if line_number < start:
                    continue
                if line_number >= end:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "event_msg":
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "item_completed":
                    continue
                item = payload.get("item")
                if isinstance(item, dict) and item.get("type") in {"CommandExecution", "McpToolCall"}:
                    items.append(item)
    except OSError:
        return None
    return items if line_count >= end else None


def _command_name(item: dict[str, Any]) -> str:
    command = item.get("command")
    return command.strip().split(maxsplit=1)[0] if isinstance(command, str) and command.strip() else "command_execution"


def _detail_payload(item: dict[str, Any], *, completed: bool) -> dict[str, Any]:
    if item["type"] == "CommandExecution":
        fields = {"command": "command", "cwd": "cwd"}
        if completed:
            fields.update({"aggregated_output": "output", "exit_code": "exit_code"})
    else:
        fields = {"arguments": "arguments"}
        if completed:
            fields["result"] = "result"
    result = {target: item[source] for source, target in fields.items() if source in item}
    if completed:
        result["provider_item"] = item
    return result
