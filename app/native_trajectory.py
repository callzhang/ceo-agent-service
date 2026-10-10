"""Read runtime-owned transcripts; live stream payloads never enter SQLite."""

import json
import threading
import time
from collections import OrderedDict
from pathlib import Path

from app.codex_history import (
    _indexed_session, _file_session_id, _valid_session_id,
    _mcp_tool_result_from_event_msg, _content_text,
)
from app.codex_runner import _codex_home


# Stream payloads exist only for active invocations; terminal reads use native
# transcript bounds. The bounded cache never serves historical output.
_LIVE_EVENTS: OrderedDict[tuple[str, int], tuple[int, list[dict]]] = OrderedDict()
_LIVE_LOCK = threading.Lock()
_LIVE_BYTES = 0
_LIVE_LIMIT_BYTES = 64 * 1024 * 1024


# Missing native sessions are common after retention. Inventory paths once per
# process/refresh window rather than recursively scanning per historical run.
# This is only a path inventory: no transcript payload or derived disk cache.
_PATH_INVENTORIES: dict[tuple[str, Path], tuple[float, dict[str, Path]]] = {}
_PATH_LOCK = threading.Lock()
_PATH_REFRESH_SECONDS = 60.0


def _native_path_inventory(kind: str, root: Path, *, refresh: bool = False) -> dict[str, Path]:
    key = (kind, root)
    with _PATH_LOCK:
        cached = _PATH_INVENTORIES.get(key)
        now = time.monotonic()
        if cached is not None and not refresh and now - cached[0] < _PATH_REFRESH_SECONDS:
            return cached[1]
        paths = {}
        if kind == "codex_cli":
            for directory in (root / "sessions", root / "archived_sessions"):
                for path in directory.rglob("*.jsonl"):
                    identity = _file_session_id(path)
                    if identity:
                        paths[identity] = path
        else:
            for path in (root / "projects").glob("*/*.jsonl"):
                paths[path.stem] = path
        _PATH_INVENTORIES[key] = (now, paths)
        return paths


def find_codex_session_path(session_id: str, *, codex_home: Path | None = None) -> Path | None:
    if not _valid_session_id(session_id):
        return None
    root = codex_home or _codex_home()
    indexed = _indexed_session(session_id, root)
    if indexed is not None and indexed.path.is_file():
        return indexed.path
    path = _native_path_inventory("codex_cli", root).get(session_id)
    return path if path is not None and path.is_file() else None


def _native_records(path: Path | None, start: int, end: int) -> list[dict]:
    if path is None or not path.is_file() or start < 0 or end <= start:
        return []
    records = []
    observed = 0
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream):
            observed = number + 1
            if number < start:
                continue
            if number >= end:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                return []
            if not isinstance(payload, dict):
                return []
            records.append(payload)
    return records if observed >= end else []


def _codex_turn_records(path: Path | None, start: int, end: int) -> list[dict]:
    """Reject references crossing a native turn boundary; never select a turn."""
    records = _native_records(path, start, end)
    started = completed = False
    for record in records:
        body = record.get("payload")
        if record.get("type") != "event_msg" or not isinstance(body, dict):
            continue
        if body.get("type") == "task_started":
            if started or completed:
                return []
            started = True
        elif body.get("type") == "task_complete":
            if completed:
                return []
            completed = True
    return records


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


def terminal_cached_run_ids(db, db_path: str) -> list[int]:
    """Find terminal cache entries inside the caller's write transaction."""
    with _LIVE_LOCK:
        ids = [run_id for path, run_id in _LIVE_EVENTS if path == db_path]
    if not ids:
        return []
    active = {row[0] for row in db.execute(
        "select id from agent_runs where status='running' and id in ("
        + ','.join('?' for _ in ids) + ')', ids)}
    return [run_id for run_id in ids if run_id not in active]


def forget_live_events(db_path: str, run_ids: list[int]) -> None:
    global _LIVE_BYTES
    with _LIVE_LOCK:
        for run_id in run_ids:
            entry = _LIVE_EVENTS.pop((db_path, run_id), None)
            if entry is not None:
                _LIVE_BYTES -= entry[0]


def read_codex_events(session_id: str, *, start_line: int = 0, end_line: int = 0) -> list[dict]:
    path = find_codex_session_path(session_id, codex_home=_codex_home())
    events: dict[str, dict] = {}
    started: dict[str, dict] = {}
    for payload in _codex_turn_records(path, start_line, end_line):
        body = payload.get("payload", {})
        if payload.get("type") == "event_msg" and isinstance(body, dict) and body.get("type") == "item_started":
            item = body.get("item")
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                started[item["id"]] = item
        event = None
        if payload.get("type") == "event_msg" and isinstance(body, dict) and body.get("type") == "item_completed":
            item = body.get("item", {})
            if not isinstance(item, dict):
                continue
            prior = started.get(item.get("id"))
            if isinstance(prior, dict) and prior.get("type") == item.get("type"):
                item = {**prior, **item}
                for key in ("arguments", "input", "command"):
                    if not item.get(key) and prior.get(key):
                        item[key] = prior[key]
            event = _mcp_tool_result_from_event_msg({
                "type": "event_msg", "payload": {"type": "item_completed", "item": item}
            })
            item_type = item.get("type")
            if event is None and item_type == "CommandExecution":
                event = {"type": "item.completed", "item": {**item, "type": "command_execution"}}
            elif event is None and item_type == "AgentMessage":
                event = {"type": "item.completed", "item": {
                    **item, "type": "agent_message",
                    "text": item.get("text") or _content_text(item.get("content")),
                }}
        else:
            event = _mcp_tool_result_from_event_msg(payload)
        if event is not None:
            item = event["item"]
            events[str(item.get("id") or len(events))] = event
    return list(events.values())


def claude_session_path(session_id: str, *, refresh: bool = False) -> Path | None:
    import os
    from uuid import UUID
    try:
        UUID(session_id)
    except ValueError:
        return None
    root = Path(os.getenv("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    path = _native_path_inventory("claude_cli", root, refresh=refresh).get(session_id)
    return path if path is not None and path.is_file() else None


def count_claude_session_lines(session_id: str) -> int:
    path = claude_session_path(session_id, refresh=True)
    if path is None:
        return 0
    with path.open(encoding="utf-8") as stream:
        return sum(1 for _ in stream)


def read_claude_events(session_id: str, *, start_line: int, end_line: int) -> list[dict]:
    """Match ClaudeEventNormalizer's tool evidence using native line bounds."""
    path = claude_session_path(session_id)
    if path is None or not path.is_file() or end_line <= start_line:
        return []
    from app.claude_runtime_adapter import ClaudeEventNormalizer
    normalizer = ClaudeEventNormalizer(
        expected_session_id=session_id, owner=object(),
        proof_issuer=lambda *_: None, cleanup_owner=lambda *_: None,
    )
    normalizer.normalize_events({"type": "system", "subtype": "init", "session_id": session_id})
    events = []
    arguments_by_call = {}
    for record in _native_records(path, start_line, end_line):
        if record.get("type") in {"assistant", "user"}:
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            results_by_call = {}
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    arguments_by_call[block.get("id")] = block.get("input")
                elif block.get("type") == "tool_result":
                    results_by_call[block.get("tool_use_id")] = block.get("content")
            for event in normalizer.normalize_events({**record, "session_id": session_id}):
                item = event.get("item", {})
                call_id = item.get("id")
                arguments = arguments_by_call.get(call_id)
                if isinstance(arguments, dict):
                    item["arguments"] = arguments
                    if item.get("type") == "command_execution":
                        item["command"] = arguments.get("command", "")
                if call_id in results_by_call:
                    content = results_by_call[call_id]
                    if item.get("type") == "command_execution":
                        item["aggregated_output"] = _content_text(content)
                    else:
                        item["result"] = {"content": content} if isinstance(content, list) else content
                events.append(event)
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
            continue
        if attempt["runtime_kind"] == "codex_cli":
            events.extend(read_codex_events(attempt["session_id"], start_line=attempt["transcript_start"], end_line=attempt["transcript_end"]))
        elif attempt["runtime_kind"] == "claude_cli" and attempt["transcript_reference"].startswith("claude_session:"):
            events.extend(read_claude_events(attempt["session_id"], start_line=attempt["transcript_start"], end_line=attempt["transcript_end"]))
        # Friday retains its Artifact remotely; there is no local tool history.
    if not attempts and row["codex_session_id"] and row["transcript_end_line"] > row["transcript_start_line"]:
        events.extend(read_codex_events(row["codex_session_id"], start_line=row["transcript_start_line"], end_line=row["transcript_end_line"]))
    return events


def read_run_event_evidence(db, row) -> tuple[list[dict], bool]:
    """Read complete native evidence for existing operator eligibility checks.

    UI event projections intentionally omit native records. Eligibility cannot
    use that omission as proof of no activity. Native activity items remain
    visible; protocol metadata follows the runtime's normalization boundary.
    A missing or unparseable source is unavailable. Classification remains with
    the caller's existing event predicate.
    """
    attempts = db.execute(
        "select runtime_kind,session_id,transcript_start,transcript_end,status,"
        "transcript_reference,source_session_id,first_effect_started_at "
        "from agent_runtime_attempts where agent_run_id=? order by id", (row['id'],),
    ).fetchall()
    database_path = next(record[2] for record in db.execute('pragma database_list') if record[1] == 'main')
    events = live_events(str(Path(database_path).resolve()), row['id']) or []
    if not attempts:
        if not row['codex_session_id'] and row['transcript_end_line'] == row['transcript_start_line'] == 0:
            return events, not events  # A claim that never reached invocation.
        attempts = [dict(runtime_kind='codex_cli', session_id=row['codex_session_id'],
                         transcript_start=row['transcript_start_line'], transcript_end=row['transcript_end_line'],
                         status=row['status'], transcript_reference='', source_session_id='', first_effect_started_at='')]
    available = True
    for attempt in attempts:
        kind, session_id = attempt['runtime_kind'], attempt['session_id']
        start, end = attempt['transcript_start'], attempt['transcript_end']
        if (attempt['status'] in ('failed', 'superseded') and not session_id
            and not attempt['transcript_reference'] and not attempt['source_session_id']
            and not attempt['first_effect_started_at'] and start == end == 0
            and row['transcript_start_line'] == row['transcript_end_line'] == 0 and not events):
            continue  # A failed route before any native session/stream existed.
        if kind == 'codex_cli' and session_id:
            records = _codex_turn_records(find_codex_session_path(session_id), start, end)
            available = available and bool(records)
            for record in records:
                body = record.get('payload')
                if record.get('type') == 'response_item':
                    if not isinstance(body, dict):
                        available = False
                        continue
                    item = dict(body)
                    if item.get('type') == 'message':
                        item['type'] = 'agent_message'
                    events.append({'type': 'item.completed', 'item': item})
                elif record.get('type') == 'event_msg':
                    if not isinstance(body, dict):
                        available = False
                        continue
                    if isinstance(body.get('item'), dict):
                        item = dict(body['item'])
                        if item.get('type') == 'AgentMessage':
                            item['type'] = 'agent_message'
                        elif item.get('type') == 'Reasoning':
                            item['type'] = 'reasoning'
                        events.append({'type': str(body.get('type') or '').replace('_', '.'), 'item': item})
                    elif body.get('type') in ('item_started', 'item_completed'):
                        available = False
                    elif event := _mcp_tool_result_from_event_msg(record):
                        events.append(event)
                    elif body.get('type') not in (
                        'task_started', 'task_complete', 'token_count', 'user_message',
                        'agent_message', 'agent_reasoning', 'thread_settings_applied',
                    ):
                        events.append(body)
                        available = False
        elif kind == 'claude_cli' and session_id:
            records = _native_records(claude_session_path(session_id), start, end)
            available = available and bool(records)
            events.extend(read_claude_events(session_id, start_line=start, end_line=end))
        else:
            available = False
    return events, available


def hydrate_run_events(db, row, db_path: str) -> list[dict]:
    if row["status"] == "running":
        cached = live_events(db_path, int(row["id"]))
        if cached is not None:
            native = read_run_events(db, row)
            if not native:
                return cached
            seen = {
                (event.get("type"), item.get("id") or item.get("call_id"))
                for event in native
                if isinstance(item := event.get("item"), dict)
            }
            return native + [
                event for event in cached
                if not isinstance(item := event.get("item"), dict)
                or (event.get("type"), item.get("id") or item.get("call_id")) not in seen
            ]
    return read_run_events(db, row)


def read_native_result_json(db, row) -> str:
    """Project the original native JSON object without runtime validation."""
    from pydantic import RootModel
    from app.agent_result import ResultParseError, parse_typed_agent_result

    attempts = db.execute(
        "select runtime_kind, session_id, transcript_start, transcript_end "
        "from agent_runtime_attempts where agent_run_id=? and status='completed' "
        "order by id desc limit 1", (row["id"],),
    ).fetchone()
    if attempts is not None:
        kind, session_id = attempts["runtime_kind"], attempts["session_id"]
        start, end = attempts["transcript_start"], attempts["transcript_end"]
    else:
        kind, session_id = "codex_cli", row["codex_session_id"]
        start, end = row["transcript_start_line"], row["transcript_end_line"]
    raw = read_native_result_stream(kind, session_id, start, end)
    if not raw:
        return ""
    try:
        result = parse_typed_agent_result(raw, RootModel[dict[str, object]]).root
    except ResultParseError:
        return ""
    return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_native_result_stream(kind: str, session_id: str, start: int, end: int) -> str:
    """Return assistant messages from the exact complete native line range."""
    if not session_id or end <= start:
        return ""
    if kind == "codex_cli":
        path = find_codex_session_path(session_id, codex_home=_codex_home())
        native_records = _codex_turn_records(path, start, end)
    elif kind == "claude_cli":
        path = claude_session_path(session_id)
        native_records = _native_records(path, start, end)
    else:
        return ""
    records = []
    for payload in native_records:
        if kind == "codex_cli":
            item = payload.get("payload")
            if (payload.get("type") == "response_item" and isinstance(item, dict)
                and item.get("type") == "message" and item.get("role") == "assistant"):
                records.append(payload)
            elif (payload.get("type") == "event_msg" and isinstance(item, dict)
                  and item.get("type") == "item_completed"
                  and isinstance(message := item.get("item"), dict)
                  and message.get("type") == "AgentMessage"):
                records.append({"type": "item.completed", "item": {
                    "type": "agent_message", "text": message.get("text") or _content_text(message.get("content")),
                }})
        elif payload.get("type") == "assistant":
            message = payload.get("message")
            for block in message.get("content", []) if isinstance(message, dict) else []:
                if isinstance(block, dict) and block.get("type") == "text":
                    records.append({"type": "item.completed", "item": {
                        "type": "agent_message", "text": block.get("text", "")}})
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
