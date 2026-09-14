"""Persist delivered DingTalk meeting-alignment conclusions to Memory."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.agent_runtime_router import RoutedCodexExecution
from app.codex_memory_write import CodexMemoryWriteFailed, execute_codex_memory_write
from app.external_retry import retry_delay_seconds
from app.store import AutoReplyStore, MeetingMemoryWriteEvent

MEETING_MEMORY_WRITE_RETRY_BASE_SECONDS = 60.0
MEETING_MEMORY_WRITE_MAX_DELAY_SECONDS = 15 * 60


def meeting_memory_payload(job: Any) -> dict[str, str]:
    """Build the only Memory payload: the conclusion that was delivered."""
    if str(job.status) != "sent" or not str(job.final_message).strip():
        raise ValueError("meeting Memory payload requires a sent conclusion")
    meeting_id = str(job.meeting_id).strip()
    if not meeting_id:
        raise ValueError("meeting Memory payload requires meeting_id")
    title = str(job.title).strip() or "未命名会议"
    return {
        "data": (
            f"[meeting-alignment:{meeting_id}]\n"
            f"会议：{title}\n\n"
            f"{str(job.final_message).strip()}"
        ),
        "type": "text",
        "created_at": str(job.ended_at).strip() or str(job.updated_at).strip(),
        "source_description": f"delivered DingTalk meeting alignment {meeting_id}",
    }


def enqueue_sent_meeting_memory_writes(store: AutoReplyStore) -> int:
    """Create one idempotent Memory-delivery record for each sent meeting."""
    created = 0
    for job in store.list_sent_meeting_alignment_jobs():
        payload = meeting_memory_payload(job)
        if store.create_meeting_memory_write_event(
            job.id,
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ):
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
        payload = json.loads(event.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("meeting Memory payload must be an object")
        result = execute_codex_memory_write(
            workspace=workspace,
            store=store,
            workload_key=f"meeting_memory_write_event:{event.id}",
            data=_required_payload_text(payload, "data"),
            type=_payload_type(payload),
            created_at=_required_payload_text(payload, "created_at"),
            source_description=_required_payload_text(payload, "source_description"),
            routed_execution=routed_execution,
        )
    except CodexMemoryWriteFailed as exc:
        if exc.retryable:
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
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
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
