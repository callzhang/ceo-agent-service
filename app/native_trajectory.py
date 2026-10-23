"""Read runtime-owned transcripts; live stream payloads never enter SQLite."""

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from itertools import islice
from pathlib import Path

from app.codex_history import (
    _indexed_session, _file_session_id, _valid_session_id,
    _mcp_tool_result_from_event_msg, _content_text,
)
from app.codex_runner import _codex_home
from app.logical_run_storage import AGENT_RUN_SQL
from app.run_storage import RUNTIME_ATTEMPT_SQL


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
    if indexed is not None:
        return indexed.path if indexed.path.is_file() else None
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
        f"select id from ({AGENT_RUN_SQL}) as agent_runs where status='running' and id in ("
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
    completed_ids: set[str] = set()
    positions: dict[str, int] = {}
    started_positions: dict[str, int] = {}
    for position, payload in enumerate(_codex_turn_records(path, start_line, end_line)):
        body = payload.get("payload", {})
        if payload.get("type") == "event_msg" and isinstance(body, dict) and body.get("type") == "item_started":
            item = body.get("item")
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                started[item["id"]] = item
                started_positions[item["id"]] = position
        event = None
        if payload.get("type") == "event_msg" and isinstance(body, dict) and body.get("type") == "item_completed":
            item = body.get("item", {})
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("id"), str):
                completed_ids.add(item["id"])
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
            if event.get("type") == "item.completed":
                native_id = item.get("native_id") or item.get("id")
                if isinstance(native_id, str):
                    completed_ids.add(native_id)
                prior = started.get(native_id) if isinstance(native_id, str) else None
                if isinstance(prior, dict):
                    for field in ("arguments", "input", "command"):
                        if not item.get(field) and prior.get(field):
                            item[field] = prior[field]
            key = str(item.get("id") or len(events))
            events[key] = event
            positions[key] = position
    for item_id, item in started.items():
        if item_id in completed_ids:
            continue
        native_type = item.get("type")
        protocol_type = {"McpToolCall": "mcp_tool_call", "CommandExecution": "command_execution"}.get(native_type) if isinstance(native_type, str) else None
        if protocol_type is not None:
            events[item_id] = {"type": "item.started", "item": {**item, "type": protocol_type}}
            positions[item_id] = started_positions[item_id]
    return [events[key] for key in sorted(events, key=positions.__getitem__)]


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


class NativeSourceMissing(LookupError):
    """The recorded native file or line is no longer available."""


class NativeSourceVersionChanged(LookupError):
    """The recorded native line or decoded body no longer matches its check."""


def locate_native_source_body(
    body: str, *, runtime_kind: str, session_uuid: str,
    start_line: int, end_line: int,
    literal_encoding_mode: str = "json_string_literal_utf8",
) -> dict[str, object]:
    """Locate the existing prompt's full source literal within one native turn.

    The caller supplies actual runtime/session/turn bounds, never an archive
    destination. source_bundle uses JSON-string serialization; directly injected
    prompt text requires explicit plain_utf8 selection. Missing content is
    explicit; no transcript or source copy is made.
    """
    if not isinstance(body, str):
        raise ValueError("native source body must be text")
    if literal_encoding_mode not in ("json_string_literal_utf8", "plain_utf8"):
        raise ValueError("unsupported native source literal encoding")
    if runtime_kind not in ("codex_cli", "claude_cli"):
        raise ValueError("unsupported native source runtime")
    if not isinstance(session_uuid, str) or not session_uuid:
        raise ValueError("native source session UUID is required")
    if type(start_line) is not int or type(end_line) is not int or start_line < 0 or end_line <= start_line:
        raise ValueError("invalid native source turn bounds")
    path = (find_codex_session_path(session_uuid) if runtime_kind == "codex_cli"
            else claude_session_path(session_uuid))
    if path is None:
        raise NativeSourceMissing("native source file unavailable")
    literal = (json.dumps(body, ensure_ascii=False)
               if literal_encoding_mode == "json_string_literal_utf8" else body)
    encoded = body.encode("utf-8")

    def leaves(value, prefix):
        if isinstance(value, str):
            yield prefix, value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from leaves(item, [*prefix, key])
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from leaves(item, [*prefix, index])

    with path.open("rb") as stream:
        for index, raw in enumerate(islice(stream, start_line, end_line), start_line):
            record = json.loads(raw)
            for leaf_path, leaf in leaves(record, []):
                start = leaf.find(literal)
                if start < 0 or not leaf_path:
                    continue
                return {
                    "runtime_kind": runtime_kind, "session_uuid": session_uuid,
                    "path": str(path), "line_index_zero_based": index,
                    "line_number_one_based": index + 1,
                    "raw_line_sha256": hashlib.sha256(raw).hexdigest(),
                    "raw_line_bytes": len(raw), "leaf_path": leaf_path,
                    "substring_start_character_zero_based": start,
                    "substring_end_character_exclusive": start + len(literal),
                    "literal_encoding_mode": literal_encoding_mode,
                    "decoded_utf8_sha256": hashlib.sha256(encoded).hexdigest(),
                    "decoded_utf8_bytes": len(encoded),
                }
    raise NativeSourceMissing("full source literal unavailable in native turn")


def read_native_source_body(
    native_provenance: Mapping[str, object], *,
    literal_encoding_mode: str = "json_string_literal_utf8",
) -> str:
    """Read one exact source body from its recorded CLI-owned JSONL version."""
    if not isinstance(native_provenance, Mapping):
        raise ValueError("native source provenance must be a mapping")
    runtime = native_provenance.get("runtime_kind")
    session_id = native_provenance.get("session_uuid")
    if runtime not in ("codex_cli", "claude_cli"):
        raise ValueError("unsupported native source runtime")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("native source session UUID is required")
    if (literal_encoding_mode not in ("json_string_literal_utf8", "plain_utf8")
            or native_provenance.get("literal_encoding_mode") != literal_encoding_mode):
        raise ValueError("unsupported native source literal encoding")
    for field in (
        "line_index_zero_based",
        "substring_start_character_zero_based",
        "substring_end_character_exclusive",
        "decoded_utf8_bytes",
    ):
        value = native_provenance.get(field)
        if type(value) is not int or value < 0:
            raise ValueError(f"invalid native source {field}")
    for field in ("raw_line_sha256", "decoded_utf8_sha256"):
        value = native_provenance.get(field)
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"invalid native source {field}")
    leaf_path = native_provenance.get("leaf_path")
    if not isinstance(leaf_path, list) or not leaf_path:
        raise ValueError("native source leaf path must be a nonempty list")
    start = native_provenance["substring_start_character_zero_based"]
    end = native_provenance["substring_end_character_exclusive"]
    if end < start:
        raise ValueError("invalid native source character range")
    if runtime == "codex_cli":
        path = find_codex_session_path(session_id)
    else:
        path = claude_session_path(session_id)
    if path is None:
        raise NativeSourceMissing("native source file unavailable")
    line_index = native_provenance["line_index_zero_based"]
    try:
        with path.open("rb") as stream:
            raw_line = next(islice(stream, line_index, line_index + 1), b"")
    except FileNotFoundError as exc:
        raise NativeSourceMissing("native source file unavailable") from exc
    if not raw_line:
        raise NativeSourceMissing("native source line unavailable")
    if hashlib.sha256(raw_line).hexdigest() != native_provenance["raw_line_sha256"]:
        raise NativeSourceVersionChanged("native source line version changed")
    leaf = json.loads(raw_line)
    for component in leaf_path:
        if isinstance(leaf, dict) and isinstance(component, str) and component in leaf:
            leaf = leaf[component]
        elif (
            isinstance(leaf, list)
            and type(component) is int
            and 0 <= component < len(leaf)
        ):
            leaf = leaf[component]
        else:
            raise ValueError("invalid native source leaf path")
    if not isinstance(leaf, str):
        raise ValueError("native source leaf must be a string")
    if end > len(leaf) or start > len(leaf):
        raise ValueError("invalid native source character range")
    body = (json.loads(leaf[start:end])
            if literal_encoding_mode == "json_string_literal_utf8" else leaf[start:end])
    if not isinstance(body, str):
        raise ValueError("native source literal must decode to a string")
    encoded = body.encode("utf-8")
    if (
        len(encoded) != native_provenance["decoded_utf8_bytes"]
        or hashlib.sha256(encoded).hexdigest() != native_provenance["decoded_utf8_sha256"]
    ):
        raise NativeSourceVersionChanged("native source body version changed")
    return body


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


def codex_session_has_final_user_marker_without_compaction(
    session_id: str,
    marker: str,
    *,
    codex_home: Path | None = None,
    context_start_line: int,
    attempt_start_line: int,
    attempt_end_line: int,
    session_end_line: int,
) -> bool:
    """Verify one exact completed User input remains in an uncompacted session."""
    if (
        not session_id
        or not marker
        or context_start_line < 0
        or attempt_start_line < context_start_line
        or attempt_end_line <= attempt_start_line
        or session_end_line < attempt_end_line
    ):
        return False
    try:
        path = find_codex_session_path(
            session_id, codex_home=codex_home or _codex_home()
        )
        if path is None:
            return False
        context_records = _native_records(path, context_start_line, session_end_line)
        attempt_records = _native_records(path, attempt_start_line, attempt_end_line)
    except (OSError, UnicodeError):
        return False
    if (
        not context_records
        or not attempt_records
        or any(record.get("type") == "compacted" for record in context_records)
    ):
        return False
    saw_user = saw_answer = completed_answer = False
    for record in attempt_records:
        body = record.get("payload")
        if not isinstance(body, dict):
            continue
        if record.get("type") == "response_item" and body.get("type") == "message":
            metadata = body.get("internal_chat_message_metadata_passthrough")
            kinds = metadata.get("content_item_kinds", []) if isinstance(metadata, dict) else []
            if body.get("role") == "user" and "user.text" in kinds:
                saw_user, saw_answer = True, False
            elif saw_user and body.get("role") == "assistant":
                content = body.get("content")
                saw_answer = isinstance(content, list) and any(
                    isinstance(block, dict)
                    and block.get("type") == "output_text"
                    and isinstance(block.get("text"), str)
                    and block["text"].strip()
                    for block in content
                )
        elif (
            record.get("type") == "event_msg"
            and body.get("type") == "task_complete"
            and saw_answer
        ):
            completed_answer = True
    if not completed_answer:
        return False

    from types import SimpleNamespace

    from app.native_prompt_input import read_attempt_input

    try:
        submitted = read_attempt_input(
            SimpleNamespace(
                runtime_kind="codex_cli",
                session_id=session_id,
                transcript_start=attempt_start_line,
                transcript_end=attempt_end_line,
            ),
            codex_home=codex_home or _codex_home(),
        )
    except (OSError, UnicodeError):
        return False
    if not submitted:
        return False
    task_prompt = submitted["task_prompt"].rstrip()
    return task_prompt.endswith(marker) or task_prompt.endswith(f"<!-- {marker} -->")


def read_run_events(db, row) -> list[dict]:
    attempts = db.execute(
        f"select runtime_kind, session_id, transcript_reference, transcript_start, transcript_end, started_at, finished_at from ({RUNTIME_ATTEMPT_SQL}) as agent_runtime_attempts where agent_run_id=? and session_id<>'' order by id",
        (row["id"],),
    ).fetchall()
    return read_run_events_from_attempts(row, attempts)


def read_run_events_from_attempts(row, attempts) -> list[dict]:
    """Read transcript files from already-loaded runtime-attempt references."""
    events = []
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
        f'select runtime_kind,session_id,transcript_start,transcript_end,status,transcript_reference,source_session_id,first_effect_started_at from ({RUNTIME_ATTEMPT_SQL}) as agent_runtime_attempts where agent_run_id=? order by id', (row['id'],),
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
                        elif item.get('type') == 'McpToolCall':
                            item['type'] = 'mcp_tool_call'
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
    attempts = db.execute(
        f"select runtime_kind, session_id, transcript_reference, transcript_start, transcript_end, started_at, finished_at from ({RUNTIME_ATTEMPT_SQL}) as agent_runtime_attempts where agent_run_id=? and session_id<>'' order by id",
        (row["id"],),
    ).fetchall()
    return hydrate_run_events_from_attempts(row, attempts, db_path)


def hydrate_run_events_from_attempts(row, attempts, db_path: str) -> list[dict]:
    """Hydrate native files after their SQLite references have been read."""
    if row["status"] == "running":
        cached = live_events(db_path, int(row["id"]))
        if cached is not None:
            native = read_run_events_from_attempts(row, attempts)
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
    return read_run_events_from_attempts(row, attempts)


def read_native_result_json(db, row) -> str:
    """Project the original native JSON object without runtime validation."""
    attempts = db.execute(
        """select runtime_kind,session_id,transcript_start,transcript_end
            from runtime_attempts where parent_id=? and status='completed'
            order by id desc limit 1""", (row["id"],),
    ).fetchone()
    return read_native_result_json_from_attempt(row, attempts)


def read_native_result_json_from_attempt(row, attempt) -> str:
    """Read the result transcript using a reference loaded before file I/O."""
    from pydantic import RootModel
    from app.agent_result import ResultParseError, parse_typed_agent_result

    if attempt is not None:
        kind, session_id = attempt["runtime_kind"], attempt["session_id"]
        start, end = attempt["transcript_start"], attempt["transcript_end"]
    else:
        kind, session_id = "codex_cli", row["session_id"]
        start, end = row["transcript_start"], row["transcript_end"]
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
