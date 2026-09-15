"""Persist delivered DingTalk meeting-alignment conclusions to Memory."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.agent_runtime_router import RoutedCodexExecution
from app.codex_memory_write import CodexMemoryWriteFailed, execute_codex_memory_write
from app.external_retry import retry_delay_seconds
from app.store import AutoReplyStore, MeetingMemoryWriteEvent


LOGGER = logging.getLogger(__name__)

MEETING_MEMORY_WRITE_RETRY_BASE_SECONDS = 60.0
MEETING_MEMORY_WRITE_MAX_DELAY_SECONDS = 15 * 60
MEETING_MEMORY_TITLE_LIMIT = 80
MEETING_MEMORY_START_STALL_SECONDS = 60
MEETING_MEMORY_WRITE_LEASE_SECONDS = 45 * 60
MEETING_MEMORY_WRITE_LEASE_GRACE_SECONDS = 5 * 60
# A result-validation failure terminates the individual runtime attempt, but
# does not prove that the already-delivered meeting conclusion is invalid. A
# new event generation can use the current result schema and route health.
MEETING_MEMORY_WRITE_RETRYABLE_RUNTIME_CODES = frozenset(
    {"runtime_result_invalid", "friday_runtime_result_invalid"}
)


def _meeting_memory_content_title(final_message: str, decision_json: str = "") -> str:
    """Return a compact meeting summary from structured meeting topics when available."""
    structured_title = _structured_topic_title(decision_json)
    if structured_title:
        return structured_title[:MEETING_MEMORY_TITLE_LIMIT]
    heading_title = _content_heading_title(final_message)
    if heading_title:
        return heading_title[:MEETING_MEMORY_TITLE_LIMIT]
    for line in final_message.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("【") or candidate.endswith(("：", ":")):
            continue
        if "](" in candidate:
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


def _content_heading_title(final_message: str) -> str | None:
    for line in final_message.splitlines():
        candidate = line.strip()
        if not candidate.startswith("【"):
            continue
        heading, separator, remainder = candidate[1:].partition("】")
        if not separator or not remainder.strip():
            continue
        normalized_heading = " ".join(heading.split())
        if normalized_heading == "会议跟进":
            continue
        if normalized_heading:
            return normalized_heading
    return None


def _structured_topic_title(decision_json: str) -> str | None:
    try:
        decision = json.loads(decision_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    topics = decision.get("topics") if isinstance(decision, dict) else None
    if not isinstance(topics, list):
        return None
    titles = [
        " ".join(str(topic.get("title") or "").split())
        for topic in topics
        if isinstance(topic, dict) and str(topic.get("title") or "").strip()
    ]
    return "；".join(titles) or None


def meeting_memory_payload(job: Any) -> dict[str, str]:
    """Build the only Memory payload: the conclusion that was delivered."""
    if str(job.status) != "sent" or not str(job.final_message).strip():
        raise ValueError("meeting Memory payload requires a sent conclusion")
    meeting_id = str(job.meeting_id).strip()
    if not meeting_id:
        raise ValueError("meeting Memory payload requires meeting_id")
    content_title = _meeting_memory_content_title(
        str(job.final_message),
        str(getattr(job, "decision_json", "") or ""),
    )
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
    """Create each missing Memory-delivery record without idle conflict writes."""
    return store.enqueue_sent_meeting_memory_write_events()


@dataclass(frozen=True)
class MeetingMemoryWriteOutcome:
    """One worker tick's durable queue outcome."""

    claimed: int = 0
    completed: int = 0
    retried: int = 0
    failed: int = 0
    lost_lease: int = 0

    @property
    def processed(self) -> int:
        """Compatibility count: every claimed event was given one worker turn."""
        return self.claimed


def meeting_memory_write_lease_seconds(
    total_timeout_seconds: int,
    idle_timeout_seconds: int,
) -> int:
    """Return a lease that outlives the configured runtime plus recovery time."""
    if total_timeout_seconds <= 0 or idle_timeout_seconds <= 0:
        raise ValueError("meeting Memory runtime timeouts must be positive")
    return max(
        MEETING_MEMORY_WRITE_LEASE_SECONDS,
        total_timeout_seconds + MEETING_MEMORY_WRITE_LEASE_GRACE_SECONDS,
        idle_timeout_seconds + MEETING_MEMORY_WRITE_LEASE_GRACE_SECONDS,
    )


def process_meeting_memory_writes(
    store: AutoReplyStore,
    *,
    workspace: Path,
    routed_execution: RoutedCodexExecution,
    limit: int = 1,
    concurrency: int = 1,
    lease_seconds: int = MEETING_MEMORY_WRITE_LEASE_SECONDS,
    clock: Callable[[], datetime] | None = None,
) -> MeetingMemoryWriteOutcome:
    """Claim and write due conclusions with bounded, lease-safe concurrency.

    ``clock`` controls every durable queue timestamp. It defaults to current
    UTC time; callers inject it for deterministic tests.
    """
    if concurrency <= 0:
        raise ValueError("meeting Memory concurrency must be positive")
    if lease_seconds <= 0:
        raise ValueError("meeting Memory lease duration must be positive")
    tick_started_at = _meeting_memory_current_time(clock)
    store.supersede_obsolete_meeting_memory_runtime_attempts()
    store.recover_unstarted_runtime_operation_attempts(
        stale_after_seconds=MEETING_MEMORY_START_STALL_SECONDS,
        now=tick_started_at,
    )
    enqueue_sent_meeting_memory_writes(store)
    owner = f"meeting-memory-{uuid4().hex}"

    def process_claimed_event(event: MeetingMemoryWriteEvent) -> str:
        # SQLite connections must be confined to their worker thread. The
        # routed execution has no held SQLite connection; its persisted runtime
        # methods open their own short-lived connections as well.
        worker_store = AutoReplyStore(
            store.path,
            busy_timeout_seconds=store.busy_timeout_seconds,
        )
        return _process_event(
            worker_store,
            event,
            workspace=workspace,
            routed_execution=routed_execution,
            owner=owner,
            clock=clock,
        )

    results: list[str] = []
    claimed_count = 0
    remaining = limit
    while remaining > 0:
        # Do not lease the next wave until all events in the current wave can
        # begin. This keeps a slow single worker from parking the rest of a
        # large batch in ``processing`` until their lease expires.
        wave_started_at = _meeting_memory_current_time(clock)
        events = store.claim_due_meeting_memory_write_events(
            now=wave_started_at.isoformat(),
            limit=min(concurrency, remaining),
            owner=owner,
            lease_seconds=lease_seconds,
        )
        if not events:
            break
        claimed_count += len(events)
        remaining -= len(events)
        worker_count = min(concurrency, len(events))
        if worker_count == 1:
            results.extend(process_claimed_event(event) for event in events)
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                results.extend(executor.map(process_claimed_event, events))
    return MeetingMemoryWriteOutcome(
        claimed=claimed_count,
        completed=results.count("completed"),
        retried=results.count("retried"),
        failed=results.count("failed"),
        lost_lease=results.count("lost_lease"),
    )


def _process_event(
    store: AutoReplyStore,
    event: MeetingMemoryWriteEvent,
    *,
    workspace: Path,
    routed_execution: RoutedCodexExecution,
    owner: str,
    clock: Callable[[], datetime] | None,
) -> str:
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
        if (
            exc.retryable
            or exc.source_code == "runtime_attempt_active"
            or exc.source_code in MEETING_MEMORY_WRITE_RETRYABLE_RUNTIME_CODES
        ):
            delay = retry_delay_seconds(
                MEETING_MEMORY_WRITE_RETRY_BASE_SECONDS,
                event.attempts,
                max_delay_seconds=MEETING_MEMORY_WRITE_MAX_DELAY_SECONDS,
            )
            settled_at = _meeting_memory_current_time(clock)
            settled = store.retry_meeting_memory_write_event(
                event.id,
                owner=owner,
                error=f"{exc.source_code}: {exc}",
                available_at=(settled_at + timedelta(seconds=delay)).isoformat(),
                now=settled_at,
            )
            return "retried" if settled else "lost_lease"
        else:
            settled_at = _meeting_memory_current_time(clock)
            settled = store.fail_meeting_memory_write_event(
                event.id,
                owner=owner,
                error=f"{exc.source_code}: {exc}",
                now=settled_at,
            )
            return "failed" if settled else "lost_lease"
    except (TypeError, ValueError) as exc:
        settled_at = _meeting_memory_current_time(clock)
        settled = store.fail_meeting_memory_write_event(
            event.id,
            owner=owner,
            error=str(exc),
            now=settled_at,
        )
        return "failed" if settled else "lost_lease"
    except Exception as exc:  # keep one malformed runtime result from stopping the queue
        LOGGER.exception("meeting Memory write event %s crashed", event.id)
        delay = retry_delay_seconds(
            MEETING_MEMORY_WRITE_RETRY_BASE_SECONDS,
            event.attempts,
            max_delay_seconds=MEETING_MEMORY_WRITE_MAX_DELAY_SECONDS,
        )
        settled_at = _meeting_memory_current_time(clock)
        settled = store.retry_meeting_memory_write_event(
            event.id,
            owner=owner,
            error=(
                "meeting_memory_runtime_error: "
                f"{type(exc).__name__}: {exc}"
            ),
            available_at=(settled_at + timedelta(seconds=delay)).isoformat(),
            now=settled_at,
        )
        return "retried" if settled else "lost_lease"
    else:
        settled_at = _meeting_memory_current_time(clock)
        settled = store.complete_meeting_memory_write_event(
            event.id,
            owner=owner,
            memory_id=result.episode_uuid,
            now=settled_at,
        )
        return "completed" if settled else "lost_lease"


def _meeting_memory_current_time(
    clock: Callable[[], datetime] | None,
) -> datetime:
    value = clock() if clock is not None else datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("meeting Memory clock must include a timezone")
    return value


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
