"""Read standalone Agent decisions from their exact native execution ranges."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass

from app.native_trajectory import read_native_result_stream


@dataclass(frozen=True)
class NativeStandaloneDecision:
    available: bool
    reason: str
    decision_json: str = "{}"
    audit_summary: str = ""
    audit_tool_events_json: str = "[]"
    memory_recall_used: bool = False
    tool_events_available: bool = False
    tool_events_reason: str = "native_source_unavailable"


@dataclass(frozen=True)
class NativeStandaloneRef:
    kind: str
    session_id: str
    start: int
    end: int


def _native_stream(ref: NativeStandaloneRef) -> str:
    if not ref.session_id:
        return ""
    if ref.kind == "friday_runtime":
        from app.agent_runtime_config import load_runtime_config
        from app.agent_runtime_router import _final_message_events
        from app.friday_runtime_adapter import FridayRuntimeAdapter

        adapter = FridayRuntimeAdapter(load_runtime_config(os.environ))
        text = adapter.read_final_artifact(
            ref.session_id.removeprefix("friday_thread:")
        )
        return _final_message_events(text) if text else ""
    if ref.end <= ref.start:
        return ""
    return read_native_result_stream(
        ref.kind, ref.session_id, ref.start, ref.end
    )


def meeting_decision(ref: NativeStandaloneRef) -> NativeStandaloneDecision:
    try:
        raw = _native_stream(ref)
    except (OSError, RuntimeError, ValueError):
        return NativeStandaloneDecision(False, "native_source_unavailable")
    if not raw:
        return NativeStandaloneDecision(False, "native_source_unavailable")
    from app.agent_result import _agent_message_candidate
    from app.meeting_alignment_agent import parse_meeting_alignment_decision

    tool_events_json, tool_events_available, tool_events_reason = _meeting_tool_events(ref)
    for line in reversed(raw.splitlines()):
        candidate = _agent_message_candidate(json.loads(line))
        if candidate is None:
            continue
        try:
            decision = parse_meeting_alignment_decision(candidate)
        except ValueError:
            continue
        return NativeStandaloneDecision(
            True,
            "",
            decision_json=decision.model_dump_json(),
            audit_summary=decision.audit_summary,
            audit_tool_events_json=tool_events_json,
            tool_events_available=tool_events_available,
            tool_events_reason=tool_events_reason,
        )
    return NativeStandaloneDecision(
        False, "native_decision_invalid",
        audit_tool_events_json=tool_events_json,
        tool_events_available=tool_events_available,
        tool_events_reason=tool_events_reason,
    )


def _meeting_tool_events(ref: NativeStandaloneRef) -> tuple[str, bool, str]:
    if ref.kind == "friday_runtime":
        return "[]", False, "native_tool_stream_unavailable"
    try:
        if ref.kind == "codex_cli":
            return _codex_audit_events_json(ref.session_id, ref.start, ref.end), True, ""
        if ref.kind == "claude_cli":
            return _claude_audit_events_json(ref.session_id, ref.start, ref.end), True, ""
    except (OSError, RuntimeError, ValueError):
        pass
    return "[]", False, "native_tool_stream_unavailable"


def _claude_audit_events_json(session_id: str, start: int, end: int) -> str:
    from app.codex_decision import _audit_event_from_payload
    from app.codex_history import _audit_event_from_jsonl
    from app.native_trajectory import read_claude_events

    audit_events = []
    for native_event in read_claude_events(
        session_id, start_line=start, end_line=end
    ):
        if native_event.get("type") not in {"item.completed", "item.failed"}:
            continue
        item = native_event.get("item")
        if not isinstance(item, dict):
            continue
        native_type = item.get("type")
        if native_type in {"mcp_tool_call", "command_execution"}:
            codex_type = (
                "McpToolCall" if native_type == "mcp_tool_call" else "CommandExecution"
            )
            event = _audit_event_from_jsonl({
                "type": "event_msg",
                "payload": {"type": "item_completed", "item": {**item, "type": codex_type}},
            })
        else:
            event = _audit_event_from_payload({
                **native_event,
                "item": {**item, "call_id": item.get("id", "")},
            })
        if event is not None:
            audit_events.append(event)
        if len(audit_events) >= 200:
            break
    return json.dumps(audit_events, ensure_ascii=False)


def _codex_audit_events_json(session_id: str, start: int, end: int) -> str:
    from app.codex_history import _audit_event_from_jsonl
    from app.native_trajectory import _codex_turn_records, find_codex_session_path

    return json.dumps(
        [event for record in _codex_turn_records(
            find_codex_session_path(session_id), start, end
        ) if (event := _audit_event_from_jsonl(record)) is not None][:200],
        ensure_ascii=False,
    )


def meeting_source(ref: NativeStandaloneRef, *, meeting_id: str):
    """Find the exact MeetingSource submitted as native user input for a run."""
    from pydantic import ValidationError

    from app.agent_result import agent_message_json_objects
    from app.meeting_alignment_models import MeetingSource
    from app.native_trajectory import (
        _codex_turn_records, _native_records, claude_session_path,
        find_codex_session_path,
    )

    if ref.kind == "codex_cli":
        records = _codex_turn_records(
            find_codex_session_path(ref.session_id), ref.start, ref.end
        )
    elif ref.kind == "claude_cli":
        records = _native_records(
            claude_session_path(ref.session_id), ref.start, ref.end
        )
    else:
        return None
    for record in reversed(records):
        payload = record.get("payload")
        if record.get("type") == "response_item" and isinstance(payload, dict):
            if payload.get("type") != "message" or payload.get("role") != "user":
                continue
            value = payload.get("content")
        elif record.get("type") == "event_msg" and isinstance(payload, dict):
            if payload.get("type") != "user_message":
                continue
            value = payload.get("message")
        elif record.get("type") == "user":
            message = record.get("message")
            value = message.get("content") if isinstance(message, dict) else None
        else:
            continue
        for text in _native_user_texts(value):
            for candidate in reversed(agent_message_json_objects(text)):
                try:
                    source = MeetingSource.model_validate(candidate)
                except ValidationError:
                    continue
                if source.meeting_id == meeting_id:
                    return source
    return None


def _native_user_texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _native_user_texts(item)
    elif isinstance(value, dict):
        for key in ("text", "content", "message"):
            if key in value:
                yield from _native_user_texts(value[key])


def task_decision(ref: NativeStandaloneRef | None) -> NativeStandaloneDecision:
    if ref is None:
        return NativeStandaloneDecision(False, "native_reference_unavailable")
    try:
        raw = _native_stream(ref)
    except (OSError, RuntimeError, ValueError):
        return NativeStandaloneDecision(False, "native_source_unavailable")
    if not raw:
        return NativeStandaloneDecision(False, "native_source_unavailable")
    from app.task_agent import _parse_task_agent_decision

    try:
        decision = _parse_task_agent_decision(raw)
    except ValueError:
        return NativeStandaloneDecision(False, "native_decision_invalid")
    return NativeStandaloneDecision(
        True,
        "",
        decision_json=decision.model_dump_json(),
        audit_summary="; ".join(
            item.update_summary for item in decision.task_decisions
            if item.update_summary
        ),
        memory_recall_used=any(
            item.memory_recall_used for item in decision.task_decisions
        ),
    )


def task_decision_value(ref: NativeStandaloneRef | None) -> dict | None:
    """Read raw Task fields for evaluation without filling model defaults."""
    from app.task_agent import select_task_decision_candidate

    if ref is None:
        return None
    try:
        raw = _native_stream(ref)
    except (OSError, RuntimeError, ValueError):
        return None
    _decision, original, _problems = select_task_decision_candidate(raw)
    return original


def task_project_value(ref: NativeStandaloneRef | None) -> dict | None:
    """Recover a legacy project decision for the offline repair planner."""
    from app.agent_result import agent_message_json_objects
    from app.task_agent import _task_decision_text_candidates

    if ref is None:
        return None
    try:
        raw = _native_stream(ref)
    except (OSError, RuntimeError, ValueError):
        return None
    for line in reversed(raw.splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        for text in reversed(_task_decision_text_candidates(record)):
            for candidate in reversed(agent_message_json_objects(text)):
                if isinstance(candidate, dict) and isinstance(candidate.get("project"), dict):
                    return candidate
    return None


def latest_task_ref(db: sqlite3.Connection, run_id: int) -> NativeStandaloneRef | None:
    attempt = db.execute(
        "select runtime_kind, session_id, transcript_start, transcript_end "
        "from agent_runtime_attempts where workload_kind='task' "
        "and (workload_key=? or workload_key like ?) and status='completed' "
        "order by id desc limit 1",
        (str(run_id), f"{run_id}:decision_repair.%"),
    ).fetchone()
    return ref_from_attempt(attempt)


def latest_standalone_ref(
    db: sqlite3.Connection, *, workload_kind: str, workload_key: str,
    legacy_row: sqlite3.Row | None = None,
) -> NativeStandaloneRef | None:
    attempt = db.execute(
        "select status, runtime_kind, session_id, transcript_start, transcript_end "
        "from agent_runtime_attempts where workload_kind=? and workload_key=? "
        "order by (status='completed') desc, id desc limit 1",
        (workload_kind, workload_key),
    ).fetchone()
    if attempt is None:
        return legacy_codex_ref(legacy_row) if legacy_row is not None else None
    if attempt["status"] != "completed":
        return None
    return ref_from_attempt(attempt)


def exact_standalone_ref(
    db: sqlite3.Connection, *, workload_kind: str, workload_key: str,
    row: sqlite3.Row,
) -> NativeStandaloneRef | None:
    """Bind one stored run to its own completed native session and range."""
    attempt = db.execute(
        "select runtime_kind, session_id, transcript_start, transcript_end "
        "from agent_runtime_attempts where workload_kind=? and workload_key=? "
        "and status='completed' and session_id=? "
        "and transcript_start=? and transcript_end=? "
        "order by id desc limit 1",
        (
            workload_kind, workload_key, row["codex_session_id"],
            row["codex_transcript_start_line"], row["codex_transcript_end_line"],
        ),
    ).fetchone()
    if attempt is not None:
        return ref_from_attempt(attempt)
    exists = db.execute(
        "select 1 from agent_runtime_attempts "
        "where workload_kind=? and workload_key=? limit 1",
        (workload_kind, workload_key),
    ).fetchone()
    return legacy_codex_ref(row) if exists is None else None


def legacy_codex_ref(row: sqlite3.Row) -> NativeStandaloneRef | None:
    """Use a complete historical Codex range only when no completed attempt exists."""
    session_id = str(row["codex_session_id"] or "")
    start = int(row["codex_transcript_start_line"] or 0)
    end = int(row["codex_transcript_end_line"] or 0)
    if not session_id or end <= start:
        return None
    return NativeStandaloneRef("codex_cli", session_id, start, end)


def ref_from_attempt(attempt: sqlite3.Row | None) -> NativeStandaloneRef | None:
    if attempt is None:
        return None
    return NativeStandaloneRef(
        kind=str(attempt["runtime_kind"]),
        session_id=str(attempt["session_id"]),
        start=int(attempt["transcript_start"]),
        end=int(attempt["transcript_end"]),
    )


def okr_envelope(ref: NativeStandaloneRef | None) -> tuple[str, str]:
    if ref is None:
        return "{}", "native_reference_unavailable"
    try:
        raw = _native_stream(ref)
    except (OSError, RuntimeError, ValueError):
        return "{}", "native_source_unavailable"
    if not raw:
        return "{}", "native_source_unavailable"
    from app.agent_envelope import AgentEnvelope, AgentKind
    from app.agent_result import ResultParseError, parse_typed_agent_result

    try:
        envelope = parse_typed_agent_result(raw, AgentEnvelope)
    except ResultParseError:
        return "{}", "native_envelope_invalid"
    if envelope.kind != AgentKind.OKR_REVIEW:
        return "{}", "native_envelope_invalid"
    return envelope.model_dump_json(), ""
