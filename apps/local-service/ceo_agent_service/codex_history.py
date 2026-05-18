import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ceo_agent_service.config import forbidden_path_prefixes


DEFAULT_CODEX_HOME = Path.home() / ".codex"
MAX_EVENT_BODY_CHARS = 20_000


@dataclass(frozen=True)
class RenderedCodexEvent:
    timestamp: str
    kind: str
    title: str
    body: str
    expanded: bool = False


@dataclass(frozen=True)
class RenderedCodexSession:
    session_id: str
    path: Path | None
    events: list[RenderedCodexEvent]
    missing: bool = False


def render_local_codex_session(
    session_id: str,
    codex_home: Path | None = None,
    max_events: int = 500,
) -> RenderedCodexSession:
    path = find_codex_session_path(session_id, codex_home=codex_home)
    if path is None:
        return RenderedCodexSession(
            session_id=session_id,
            path=None,
            events=[],
            missing=True,
        )

    events: list[RenderedCodexEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if len(events) >= max_events:
            break
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = _render_jsonl_event(payload)
        if event is not None:
            events.append(event)

    return RenderedCodexSession(
        session_id=session_id,
        path=path,
        events=events,
    )


def count_codex_session_lines(
    session_id: str,
    codex_home: Path | None = None,
) -> int:
    path = find_codex_session_path(session_id, codex_home=codex_home)
    if path is None:
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def extract_codex_audit_events_from_session(
    session_id: str,
    codex_home: Path | None = None,
    start_line: int = 0,
    end_line: int | None = None,
    limit: int = 40,
) -> list[dict[str, str]]:
    path = find_codex_session_path(session_id, codex_home=codex_home)
    if path is None:
        return []
    events: list[dict[str, str]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    selected = lines[start_line:end_line]
    for line in selected:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = _audit_event_from_jsonl(payload)
        if event:
            events.append(event)
        if len(events) >= limit:
            break
    return events


def find_codex_session_path(
    session_id: str,
    codex_home: Path | None = None,
) -> Path | None:
    if not _valid_session_id(session_id):
        return None
    root = codex_home or DEFAULT_CODEX_HOME
    search_roots = [root / "sessions", root / "archived_sessions"]
    for search_root in search_roots:
        if not search_root.exists():
            continue
        matches = sorted(search_root.rglob(f"*{session_id}.jsonl"))
        if matches:
            return matches[-1]

    for search_root in search_roots:
        if not search_root.exists():
            continue
        for path in search_root.rglob("*.jsonl"):
            if _file_session_id(path) == session_id:
                return path
    return None


def _valid_session_id(session_id: str) -> bool:
    return bool(session_id) and all(
        char.isalnum() or char in {"-", "_"} for char in session_id
    )


def _file_session_id(path: Path) -> str:
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        return ""
    try:
        payload = json.loads(first_line)
    except json.JSONDecodeError:
        return ""
    if payload.get("type") != "session_meta":
        return ""
    meta = payload.get("payload")
    if not isinstance(meta, dict):
        return ""
    session_id = meta.get("id")
    return session_id if isinstance(session_id, str) else ""


def _render_jsonl_event(payload: Any) -> RenderedCodexEvent | None:
    if not isinstance(payload, dict):
        return None
    timestamp = _string(payload.get("timestamp"))
    event_type = _string(payload.get("type"))
    body = payload.get("payload")
    if event_type == "session_meta" and isinstance(body, dict):
        return RenderedCodexEvent(
            timestamp=timestamp,
            kind="session",
            title="Session metadata",
            body=_session_meta_body(body),
        )
    if event_type == "response_item" and isinstance(body, dict):
        return _render_response_item(timestamp, body)
    return None


def _render_response_item(
    timestamp: str,
    payload: dict[str, Any],
) -> RenderedCodexEvent | None:
    item_type = _string(payload.get("type"))
    if item_type == "message":
        role = _string(payload.get("role")) or "message"
        text = _content_text(payload.get("content"))
        if not text:
            return None
        if role == "user" and _is_system_context_message(text):
            return RenderedCodexEvent(
                timestamp=timestamp,
                kind="system_context",
                title="System context",
                body=_truncate(text),
                expanded=False,
            )
        return RenderedCodexEvent(
            timestamp=timestamp,
            kind=role,
            title=role.title(),
            body=_truncate(text),
            expanded=role in {"user", "assistant"},
        )
    if item_type == "function_call":
        name = _string(payload.get("name")) or "tool"
        arguments = _pretty_json_string(_string(payload.get("arguments")))
        return RenderedCodexEvent(
            timestamp=timestamp,
            kind="tool_call",
            title=f"Tool call: {name}",
            body=_truncate(arguments),
            expanded=False,
        )
    if item_type == "function_call_output":
        call_id = _string(payload.get("call_id"))
        return RenderedCodexEvent(
            timestamp=timestamp,
            kind="tool_output",
            title=f"Tool output: {call_id}" if call_id else "Tool output",
            body=_truncate(_string(payload.get("output"))),
            expanded=False,
        )
    if item_type == "reasoning":
        summary = _content_text(payload.get("summary"))
        body = summary or "Reasoning summary unavailable; Codex stored only encrypted reasoning for this item."
        return RenderedCodexEvent(
            timestamp=timestamp,
            kind="reasoning",
            title="Reasoning",
            body=_truncate(body),
            expanded=False,
        )
    return None


def _audit_event_from_jsonl(payload: dict[str, Any]) -> dict[str, str] | None:
    if payload.get("type") != "response_item":
        return None
    item = payload.get("payload")
    if not isinstance(item, dict):
        return None
    item_type = _string(item.get("type"))
    if item_type == "function_call":
        name = _string(item.get("name")) or "tool"
        arguments = _pretty_json_string(_string(item.get("arguments")))
        event: dict[str, str] = {
            "event_type": "response_item",
            "tool": name,
        }
        command = _command_from_json_text(arguments)
        if command:
            event["command"] = command
        path = _first_pathish_token(arguments)
        if path:
            event["path"] = path
        return event
    if item_type == "function_call_output":
        output = _string(item.get("output"))
        event = {
            "event_type": "response_item",
            "tool": "tool_output",
        }
        call_id = _string(item.get("call_id"))
        if call_id:
            event["command"] = call_id
        path = _first_pathish_token(output)
        if path:
            event["path"] = path
        return event
    return None


def _render_event_msg(
    timestamp: str,
    payload: dict[str, Any],
) -> RenderedCodexEvent | None:
    kind = _string(payload.get("type")) or "event"
    text = (
        _string(payload.get("message"))
        or _string(payload.get("text"))
        or _short_json(payload)
    )
    return RenderedCodexEvent(
        timestamp=timestamp,
        kind=f"event:{kind}",
        title=f"Event: {kind}",
        body=_truncate(text),
    )


def _session_meta_body(payload: dict[str, Any]) -> str:
    fields = {
        "id": payload.get("id"),
        "cwd": payload.get("cwd"),
        "originator": payload.get("originator"),
        "cli_version": payload.get("cli_version"),
        "source": payload.get("source"),
        "model_provider": payload.get("model_provider"),
    }
    return "\n".join(f"{key}: {value}" for key, value in fields.items() if value)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _is_system_context_message(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("# AGENTS.md instructions") or stripped.startswith(
        "<environment_context>"
    )


def _command_from_json_text(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if isinstance(payload, dict):
        value = payload.get("cmd") or payload.get("command")
        return value if isinstance(value, str) else ""
    return ""


def _first_pathish_token(text: str) -> str:
    for token in text.replace("\n", " ").split():
        stripped = token.strip("'\"`[](),:;")
        for suffix in (".md", ".pdf", ".docx", ".xlsx"):
            marker_index = stripped.find(f"{suffix}:")
            if marker_index >= 0:
                return stripped[: marker_index + len(suffix)]
        if (
            any(stripped.startswith(prefix) for prefix in forbidden_path_prefixes())
            or stripped.endswith(".md")
            or stripped.endswith(".pdf")
            or stripped.endswith(".docx")
            or stripped.endswith(".xlsx")
        ):
            return stripped
    return ""


def _pretty_json_string(text: str) -> str:
    if not text:
        return ""
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return text


def _short_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _truncate(text: str) -> str:
    if len(text) <= MAX_EVENT_BODY_CHARS:
        return text
    return f"{text[:MAX_EVENT_BODY_CHARS]}\n...[truncated]"


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""
