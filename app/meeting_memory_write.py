"""Persist delivered DingTalk meeting-alignment conclusions to Memory."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.agent_runtime_router import RoutedCodexExecution
from app.codex_memory_write import CodexMemoryWriteFailed, execute_codex_memory_write
from app.external_retry import retry_delay_seconds
from app.store import AutoReplyStore, MeetingMemoryWriteEvent

MEETING_MEMORY_WRITE_RETRY_BASE_SECONDS = 60.0
MEETING_MEMORY_WRITE_MAX_DELAY_SECONDS = 15 * 60
MEETING_MEMORY_TITLE_LIMIT = 80
MEETING_MEMORY_START_STALL_SECONDS = 60


def _meeting_memory_content_title(final_message: str) -> str:
    """Return the first substantive conclusion line as the Memory entry title."""
    for line in final_message.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("【") or candidate.endswith(("：", ":")):
            continue
        while candidate.startswith("@"):
            _, separator, remainder = candidate.partition(" ")
            if not separator:
                break
            candidate = remainder.lstrip()
        ordinal, separator, remainder = candidate.partition(". ")
        if separator and ordinal.isdecimal():
            candidate = remainder
        for sentence_end in ("。", "！", "？", "!", "?"):
            sentence, separator, _ = candidate.partition(sentence_end)
            if separator:
                candidate = sentence
                break
        candidate = candidate.rstrip("。！？!?：: ")
        if candidate:
            return candidate[:MEETING_MEMORY_TITLE_LIMIT]
    return "会议结论"


def meeting_memory_payload(job: Any) -> dict[str, str]:
    """Build the only Memory payload: the conclusion that was delivered."""
    if str(job.status) != "sent" or not str(job.final_message).strip():
        raise ValueError("meeting Memory payload requires a sent conclusion")
    meeting_id = str(job.meeting_id).strip()
    if not meeting_id:
        raise ValueError("meeting Memory payload requires meeting_id")
    content_title = _meeting_memory_content_title(str(job.final_message))
    return {
        "data": (
            f"{content_title}\n\n"
            f"{str(job.final_message).strip()}\n\n"
            f"[meeting-alignment:{meeting_id}]"
        ),
        "type": "text",
        "created_at": str(job.ended_at).strip() or str(job.updated_at).strip(),
        "source_description": content_title,
    }


def enqueue_sent_meeting_memory_writes(store: AutoReplyStore) -> int:
    """Create one idempotent Memory-delivery record for each sent meeting."""
    created = 0
    for job in store.list_sent_meeting_alignment_jobs():
        if store.create_meeting_memory_write_event(job.id):
            created += 1
    return created


def process_meeting_memory_writes(
    store: AutoReplyStore,
    *,
    workspace: Path,
    routed_execution: RoutedCodexExecution,
    now: datetime,
    limit: int = 1,
) -> int:
    """Write due delivered conclusions and preserve their terminal meeting state."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("meeting Memory processing time must include a timezone")
    store.supersede_obsolete_meeting_memory_runtime_attempts()
    store.recover_unstarted_runtime_operation_attempts(
        stale_after_seconds=MEETING_MEMORY_START_STALL_SECONDS,
        now=now,
    )
    enqueue_sent_meeting_memory_writes(store)
    processed = 0
    for event in store.list_due_meeting_memory_write_events(
        now=now.isoformat(), limit=limit
    ):
        _process_event(
            store,
            event,
            workspace=workspace,
            routed_execution=routed_execution,
            now=now,
        )
        processed += 1
    return processed


def _process_event(
    store: AutoReplyStore,
    event: MeetingMemoryWriteEvent,
    *,
    workspace: Path,
    routed_execution: RoutedCodexExecution,
    now: datetime,
) -> None:
    try:
        payload = meeting_memory_payload(store.get_meeting_alignment_job(event.meeting_job_id))
        result = execute_codex_memory_write(
            workspace=workspace,
            store=store,
            workload_key=(
                f"meeting_memory_write_event:{event.id}:"
                f"{event.execution_generation}"
            ),
            data=_required_payload_text(payload, "data"),
            type=_payload_type(payload),
            created_at=_required_payload_text(payload, "created_at"),
            source_description=_required_payload_text(payload, "source_description"),
            routed_execution=routed_execution,
        )
    except CodexMemoryWriteFailed as exc:
        if exc.retryable or exc.source_code == "runtime_attempt_active":
            delay = retry_delay_seconds(
                MEETING_MEMORY_WRITE_RETRY_BASE_SECONDS,
                event.attempts,
                max_delay_seconds=MEETING_MEMORY_WRITE_MAX_DELAY_SECONDS,
            )
            store.retry_meeting_memory_write_event(
                event.id,
                error=f"{exc.source_code}: {exc}",
                available_at=(now + timedelta(seconds=delay)).isoformat(),
            )
        else:
            store.fail_meeting_memory_write_event(
                event.id,
                error=f"{exc.source_code}: {exc}",
            )
    except (TypeError, ValueError) as exc:
        store.fail_meeting_memory_write_event(event.id, error=str(exc))
    else:
        store.complete_meeting_memory_write_event(
            event.id, memory_id=result.episode_uuid
        )


def _required_payload_text(payload: dict[str, object], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"meeting Memory payload is missing {key}")
    return value


def _payload_type(payload: dict[str, object]) -> str:
    value = _required_payload_text(payload, "type")
    if value not in {"text", "message"}:
        raise ValueError("meeting Memory payload has an invalid type")
    return value
