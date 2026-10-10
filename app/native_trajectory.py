"""Read runtime-owned transcripts; live stream payloads never enter SQLite."""

import json
import threading
from collections import OrderedDict
from pathlib import Path

from app.codex_history import find_codex_session_path, _mcp_tool_result_from_event_msg
from app.codex_runner import _codex_home


# The stream is needed while the invocation is running and its native file is
# still being written. Retain a small number of recently completed runs for
# callers consuming the result in this process, without another disk copy.
_LIVE_EVENTS: OrderedDict[tuple[str, int], tuple[int, list[dict]]] = OrderedDict()
_LIVE_LOCK = threading.Lock()
_LIVE_BYTES = 0
_LIVE_LIMIT_BYTES = 64 * 1024 * 1024


def remember_live_event(db_path: str, run_id: int, event: dict) -> None:
    global _LIVE_BYTES
    size = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
    with _LIVE_LOCK:
        key = (db_path, run_id)
        if size > _LIVE_LIMIT_BYTES:
            prior = _LIVE_EVENTS.pop(key, None)
            if prior is not None:
                _LIVE_BYTES -= prior[0]
            return
        prior_size, events = _LIVE_EVENTS.get(key, (0, []))
        events.append(event)
        _LIVE_EVENTS[key] = (prior_size + size, events)
        _LIVE_BYTES += size
        _LIVE_EVENTS.move_to_end(key)
        while len(_LIVE_EVENTS) > 32 or _LIVE_BYTES > _LIVE_LIMIT_BYTES:
            _, (removed_size, _) = _LIVE_EVENTS.popitem(last=False)
            _LIVE_BYTES -= removed_size


def live_events(db_path: str, run_id: int) -> list[dict] | None:
    with _LIVE_LOCK:
        entry = _LIVE_EVENTS.get((db_path, run_id))
        return list(entry[1]) if entry is not None else None


def event_metadata(event: dict) -> dict:
    """Only call identity/status, never prompt, arguments or tool output."""
    metadata = {key: event[key] for key in ("type", "tool", "title", "status", "id", "call_id") if key in event}
    item = event.get("item")
    if isinstance(item, dict):
        metadata["item"] = {
            key: item[key] for key in ("type", "id", "call_id", "status", "exit_code", "server", "tool")
            if key in item
        }
        from app.agent_effect_guard import provider_receipts
        receipts = provider_receipts([event])
        if receipts:
            metadata["item"]["provider_receipt_ids"] = list(receipts)
    return metadata


def read_codex_events(session_id: str, *, start_line: int = 0, end_line: int = 0) -> list[dict]:
    path = find_codex_session_path(session_id, codex_home=_codex_home())
    if path is None:
        return []
    events: dict[str, dict] = {}
    turn_started = False
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream):
            if line_number < start_line:
                continue
            if end_line > 0 and line_number >= end_line:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            event = _mcp_tool_result_from_event_msg(payload)
            body = payload.get("payload", {})
            if payload.get("type") == "event_msg" and body.get("type") == "task_started":
                if turn_started:
                    break
                turn_started = True
            if event is None and payload.get("type") == "event_msg" and body.get("type") == "item_completed":
                item = body.get("item", {})
                item_type = item.get("type")
                if item_type == "CommandExecution":
                    event = {"type": "item.completed", "item": {**item, "type": "command_execution"}}
                elif item_type == "AgentMessage":
                    event = {"type": "item.completed", "item": {**item, "type": "agent_message"}}
            if event is not None:
                item = event["item"]
                events[str(item.get("id") or len(events))] = event
            if payload.get("type") == "event_msg" and body.get("type") == "task_complete":
                break
    return list(events.values())


def claude_session_path(session_id: str) -> Path | None:
    import os
    from uuid import UUID
    try:
        UUID(session_id)
    except ValueError:
        return None
    root = Path(os.getenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"
    return next(root.glob(f"*/{session_id}.jsonl"), None)


def count_claude_session_lines(session_id: str) -> int:
    path = claude_session_path(session_id)
    if path is None:
        return 0
    with path.open(encoding="utf-8") as stream:
        return sum(1 for _ in stream)


def read_claude_events(session_id: str, *, start_line: int, end_line: int) -> list[dict]:
    """Match ClaudeEventNormalizer's tool evidence using native line bounds."""
    path = claude_session_path(session_id)
    if path is None or end_line <= start_line:
        return []
    from app.claude_runtime_adapter import ClaudeEventNormalizer
    normalizer = ClaudeEventNormalizer(
        expected_session_id=session_id, owner=object(),
        proof_issuer=lambda *_: None, cleanup_owner=lambda *_: None,
    )
    normalizer.normalize_events({"type": "system", "subtype": "init", "session_id": session_id})
    events = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream):
            if line_number < start_line:
                continue
            if line_number >= end_line:
                break
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("type") in {"assistant", "user"}:
                events.extend(normalizer.normalize_events({**record, "session_id": session_id}))
    return events


def read_run_events(db, row) -> list[dict]:
    events = []
    attempts = db.execute(
        "select runtime_kind, session_id, transcript_reference, transcript_start, transcript_end, started_at, finished_at "
        "from agent_runtime_attempts where agent_run_id=? and session_id<>'' order by id",
        (row["id"],),
    ).fetchall()
    for attempt in attempts:
        if attempt["transcript_end"] <= attempt["transcript_start"]:
            if attempt["runtime_kind"] == "codex_cli" and attempt["transcript_reference"].startswith("codex_session:"):
                events.extend(read_codex_events(attempt["session_id"], start_line=attempt["transcript_start"]))
            continue
        if attempt["runtime_kind"] == "codex_cli":
            events.extend(read_codex_events(attempt["session_id"], start_line=attempt["transcript_start"], end_line=attempt["transcript_end"]))
        elif attempt["runtime_kind"] == "claude_cli" and attempt["transcript_reference"].startswith("claude_session:"):
            events.extend(read_claude_events(attempt["session_id"], start_line=attempt["transcript_start"], end_line=attempt["transcript_end"]))
        # Friday retains its operation remotely. SQLite already records that
        # reference and the typed final result; it never had local tool history.
    if not attempts and row["codex_session_id"] and row["transcript_end_line"] > row["transcript_start_line"]:
        events.extend(read_codex_events(row["codex_session_id"], start_line=row["transcript_start_line"], end_line=row["transcript_end_line"]))
    return events


def payload_signature(event: dict) -> str:
    item = event.get("item")
    if not isinstance(item, dict):
        return json.dumps(event, ensure_ascii=False, sort_keys=True)
    return json.dumps({key: value for key, value in item.items()
                       if key not in {"id", "call_id", "status"}}, ensure_ascii=False, sort_keys=True)


def native_covers_event(event: dict, native: list[dict]) -> bool:
    item = event.get("item")
    if not isinstance(item, dict) or event.get("type") != "item.completed":
        return False
    fields = {key: value for key, value in item.items() if key not in {"id", "call_id", "status"}}
    return any(isinstance(candidate := record.get("item"), dict)
               and all(key in candidate and candidate[key] == value for key, value in fields.items())
               for record in native)


def merge_native_events(native: list[dict], stored: list[dict]) -> list[dict]:
    """Use native detail and keep historical rows with unmatched evidence."""
    signatures = {payload_signature(event) for event in native}
    native_ids = {event.get("item", {}).get("id") for event in native}
    from app.agent_effect_guard import provider_receipts
    native_receipts = set(provider_receipts(native))
    retained = []
    metadata_fields = {"type", "id", "call_id", "status", "exit_code", "server", "tool", "provider_receipt_ids"}
    for event in stored:
        item = event.get("item")
        if payload_signature(event) in signatures:
            continue
        if isinstance(item, dict) and set(item).issubset(metadata_fields):
            receipts = set(provider_receipts([event]))
            if (receipts and receipts.issubset(native_receipts)) or (not receipts and item.get("id") in native_ids):
                continue
        elif native_covers_event(event, native):
            continue
        retained.append(event)
    return native + retained


def hydrate_run_events(db, row, stored: list[dict], db_path: str) -> list[dict]:
    cached = live_events(db_path, int(row["id"]))
    if cached is not None:
        return stored[:len(stored) - len(cached)] + cached
    return merge_native_events(read_run_events(db, row), stored)


def native_run_available(db, row) -> bool:
    """A whole run can replace its old copy only with bounded native attempts."""
    attempts = db.execute(
        "select runtime_kind, session_id, transcript_reference, transcript_start, transcript_end "
        "from agent_runtime_attempts where agent_run_id=? order by id", (row["id"],),
    ).fetchall()
    if not attempts:
        if not row["codex_session_id"] or row["transcript_end_line"] <= row["transcript_start_line"]:
            return False
        path = find_codex_session_path(row["codex_session_id"], codex_home=_codex_home())
        if path is None:
            return False
        with path.open(encoding="utf-8") as stream:
            return sum(1 for _ in stream) >= row["transcript_end_line"]
    for attempt in attempts:
        if attempt["transcript_end"] <= attempt["transcript_start"]:
            return False
        if attempt["runtime_kind"] == "codex_cli":
            path = find_codex_session_path(attempt["session_id"], codex_home=_codex_home())
        elif attempt["runtime_kind"] == "claude_cli" and attempt["transcript_reference"].startswith("claude_session:"):
            path = claude_session_path(attempt["session_id"])
        else:
            return False
        if path is None:
            return False
        with path.open(encoding="utf-8") as stream:
            if sum(1 for _ in stream) < attempt["transcript_end"]:
                return False
    return True
