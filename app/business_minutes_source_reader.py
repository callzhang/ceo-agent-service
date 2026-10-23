"""Unselected exact AI Minutes read from current DWS source coordinates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.dws_client import DwsError
from app.minutes_todo_context import minutes_full_summary, todo_transcript_excerpts


class MinutesSourceUnavailable(LookupError):
    """The source cannot be reconstructed from a complete provider read."""


class MinutesSourceIncomplete(ValueError):
    """Provider data or pagination is incomplete or contradictory."""


class MinutesInvalidLocator(ValueError):
    """The stored source coordinates cannot name this source."""


def _source_uuid(source_ref: str) -> str:
    uuid, separator, suffix = source_ref.partition("#todos-sha256=")
    if not uuid or not separator or len(suffix) != 64 or any(c not in "0123456789abcdef" for c in suffix):
        raise MinutesInvalidLocator("invalid AI Minutes source reference")
    return uuid


def _exact_minutes_info(dws: Any, uuid: str) -> dict[str, Any]:
    """Read the meeting metadata directly by its provider UUID."""
    payload = dws.get_minutes_info(uuid)
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        raise MinutesSourceIncomplete("AI Minutes exact info unavailable")
    info = payload["result"]
    if info.get("taskUuid") != uuid:
        raise MinutesSourceUnavailable("AI Minutes UUID missing")
    start_ms = info.get("startTime")
    if isinstance(start_ms, bool) or not isinstance(start_ms, int):
        raise MinutesSourceIncomplete("AI Minutes exact start time unavailable")
    try:
        instant = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise MinutesSourceIncomplete("AI Minutes exact start time invalid") from exc
    if not 2000 <= instant.year <= 2100:
        raise MinutesSourceIncomplete("AI Minutes exact start time invalid")
    return info


def minutes_provider_locator(
    signal: Any, *, transcript_excerpt_format: str = "sentence_v1",
) -> dict[str, object]:
    """Retain only existing UUID and canonical component presence while body exists."""
    if signal.source_type != "ai_minutes":
        raise ValueError("source is not AI Minutes")
    if transcript_excerpt_format not in ("paragraph_v1", "sentence_v1"):
        raise ValueError("unknown transcript excerpt format")
    uuid = _source_uuid(signal.source_ref)
    try:
        original = json.loads(signal.evidence_text)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid canonical AI Minutes body") from exc
    if not isinstance(original, dict) or not {"meeting", "todos"} <= original.keys():
        raise ValueError("unsupported AI Minutes body")
    components = ["meeting", "todos"]
    if "transcript_excerpts" in original:
        components.append("transcript_excerpts")
    if "meeting_summary" in original:
        components.append("meeting_summary")
    if set(original) != set(components):
        raise ValueError("unsupported AI Minutes components")
    if not isinstance(original["meeting"], dict) or not isinstance(original["todos"], dict):
        raise ValueError("unsupported AI Minutes source shape")
    if "transcript_excerpts" in original and (
        not isinstance(original["transcript_excerpts"], list)
        or not original["transcript_excerpts"]
        or not all(isinstance(row, dict) for row in original["transcript_excerpts"])
    ):
        raise ValueError("unsupported transcript excerpt shape")
    if "meeting_summary" in original and (
        not isinstance(original["meeting_summary"], str) or not original["meeting_summary"]
    ):
        raise ValueError("unsupported meeting summary shape")
    from app.task_scanners import _canonical_minutes_todos_payload, _minutes_id, _minutes_todo_actions

    if _minutes_id(original["meeting"]) != uuid or _minutes_todo_actions(original["todos"]) is None:
        raise ValueError("AI Minutes original source identity or actions invalid")
    canonical = _canonical_minutes_todos_payload(
        minutes=original["meeting"],
        todos_payload=original["todos"],
        transcript_excerpts=original.get("transcript_excerpts", []),
        meeting_summary=original.get("meeting_summary", ""),
    )
    if canonical != signal.evidence_text:
        raise ValueError("AI Minutes body is not producer canonical format")
    locator = {"kind": "ai_minutes", "minutes_id": uuid, "components": components}
    if "transcript_excerpts" in components:
        locator["transcript_excerpt_format"] = transcript_excerpt_format
    return locator


def read_minutes_source_body(
    locator: object,
    *,
    source_ref: str,
    dws: Any,
) -> str:
    """Read and verify one exact AI Minutes source by provider UUID."""
    uuid = _source_uuid(source_ref)
    if not isinstance(locator, dict) or not {"kind", "minutes_id", "components"} <= set(locator):
        raise MinutesInvalidLocator("invalid AI Minutes locator")
    components = locator["components"]
    if locator["kind"] != "ai_minutes" or locator["minutes_id"] != uuid or not isinstance(components, list):
        raise MinutesInvalidLocator("invalid AI Minutes locator")
    if components not in (
        ["meeting", "todos"],
        ["meeting", "todos", "transcript_excerpts"],
        ["meeting", "todos", "meeting_summary"],
        ["meeting", "todos", "transcript_excerpts", "meeting_summary"],
    ):
        raise MinutesInvalidLocator("invalid AI Minutes components")
    expected_keys = {"kind", "minutes_id", "components"}
    if "transcript_excerpts" in components:
        expected_keys.add("transcript_excerpt_format")
        if locator.get("transcript_excerpt_format") not in ("paragraph_v1", "sentence_v1"):
            raise MinutesInvalidLocator("missing or unknown transcript excerpt format")
    if set(locator) != expected_keys:
        raise MinutesInvalidLocator("invalid AI Minutes locator fields")
    from app.task_scanners import _canonical_minutes_todos_payload, _minutes_todo_actions

    try:
        meeting = _exact_minutes_info(dws, uuid)
        todos_payload = dws.get_minutes_todos(uuid)
        if not isinstance(todos_payload, dict) or _minutes_todo_actions(todos_payload) is None:
            raise MinutesSourceIncomplete("AI Minutes actions unrecognized")
        excerpts: list[dict[str, Any]] = []
        if "transcript_excerpts" in components:
            transcript = dws.get_all_minutes_transcription(uuid)
            if not isinstance(transcript, dict) or not isinstance(transcript.get("paragraphs"), list) or not all(isinstance(row, dict) for row in transcript["paragraphs"]):
                raise MinutesSourceIncomplete("AI Minutes transcript incomplete")
            try:
                excerpts = todo_transcript_excerpts(
                    todos_payload, transcript["paragraphs"],
                    transcript_excerpt_format=locator["transcript_excerpt_format"],
                )
            except OverflowError as exc:
                raise MinutesSourceIncomplete("AI Minutes excerpt offset is non-finite") from exc
            if not excerpts:
                raise MinutesSourceIncomplete("AI Minutes excerpts unavailable")
        summary = ""
        if "meeting_summary" in components:
            summary = minutes_full_summary(dws.get_minutes_summary(uuid))
            if not summary:
                raise MinutesSourceIncomplete("AI Minutes summary unavailable")
    except DwsError as exc:
        raise MinutesSourceIncomplete("AI Minutes provider unreadable") from exc
    return _canonical_minutes_todos_payload(
        minutes=meeting, todos_payload=todos_payload,
        transcript_excerpts=excerpts, meeting_summary=summary,
    )
