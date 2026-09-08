from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from app.agent_cron.models import ensure_utc_datetime
from app.dispatcher.models import (
    DispatchEnvelope,
    QueueMetrics,
)
from app.store import AutoReplyStore


def _sqlite_time(value: datetime) -> str:
    return ensure_utc_datetime(value, field="dispatcher time").strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _source_time(value: str, *, fallback: datetime) -> datetime:
    if not value:
        return fallback
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _generation(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big")


class ScheduledTaskQueueAdapter:
    name = "scheduled"

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = ensure_utc_datetime(
            now, field="scheduled dispatcher metrics time"
        ).isoformat(timespec="seconds")
        with self.store._connect() as db:
            due = db.execute(
                "select count(*) from scheduled_task_runs "
                "where dispatch_status='pending' and scheduled_for<=? "
                "and (lease_owner='' or lease_expires_at<=?)",
                (now_text, now_text),
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from scheduled_task_runs "
                "where dispatch_status='pending' and lease_owner<>'' "
                "and lease_expires_at>?",
                (now_text,),
            ).fetchone()[0]
        return QueueMetrics(due=int(due), claimed=int(claimed))

    def claim(
        self,
        now: datetime,
        *,
        owner: str,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        run = self.store.claim_scheduled_task_run(
            owner=owner,
            lease_seconds=_lease_seconds(lease),
            now=now,
        )
        if run is None:
            return None
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=str(run.id),
            available_at=run.scheduled_for,
            priority=0,
            attempt=1,
            generation=run.id,
        )

    def release(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
    ) -> None:
        _validate_release(self.name, envelope, owner, now)
        with self.store._immediate_write_transaction() as db:
            cursor = db.execute(
                "update scheduled_task_runs set lease_owner='', lease_expires_at=null "
                "where id=? and dispatch_status='pending' and lease_owner=?",
                (int(envelope.source_id), owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("scheduled dispatch claim is no longer owned")


class ReplyQueueAdapter:
    name = "reply"

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        with self.store._connect() as db:
            due = db.execute(
                "select count(*) from reply_tasks where status='pending' "
                "and (available_at='' or available_at<=?)",
                (now_text,),
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from reply_tasks where status='processing'"
            ).fetchone()[0]
        return QueueMetrics(due=int(due), claimed=int(claimed))

    def claim(
        self,
        now: datetime,
        *,
        owner: str,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        with self.store._connect() as db:
            row = db.execute(
                "select id from reply_tasks where status='pending' "
                "and (available_at='' or available_at<=?) order by id limit 1",
                (now_text,),
            ).fetchone()
        if row is None:
            return None
        claimed = self.store.claim_reply_task(int(row["id"]), now=now_text)
        if claimed is None:
            return None
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=str(claimed.id),
            available_at=_source_time(claimed.created_at, fallback=now),
            priority=0,
            attempt=claimed.attempts,
            generation=_generation(claimed.execution_generation),
        )

    def release(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
    ) -> None:
        _validate_release(self.name, envelope, owner, now)
        with self.store._immediate_write_transaction() as db:
            row = db.execute(
                "select execution_generation from reply_tasks where id=?",
                (int(envelope.source_id),),
            ).fetchone()
            if (
                row is None
                or _generation(str(row["execution_generation"])) != envelope.generation
            ):
                raise ValueError("reply dispatch generation changed")
            cursor = db.execute(
                "update reply_tasks set status='pending', attempts=max(attempts-1,0), "
                "locked_at=null, updated_at=? where id=? and status='processing'",
                (_sqlite_time(now), int(envelope.source_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("reply dispatch claim is no longer active")


class MeetingQueueAdapter:
    name = "meeting"

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        with self.store._connect() as db:
            due = db.execute(
                "select count(*) from meeting_alignment_jobs "
                "where status in ('waiting','pending','retry') "
                "and datetime(eligible_at)<=datetime(?) "
                "and (available_at='' or datetime(available_at)<=datetime(?))",
                (now_text, now_text),
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from meeting_alignment_jobs where status='processing'"
            ).fetchone()[0]
        return QueueMetrics(due=int(due), claimed=int(claimed))

    def claim(
        self,
        now: datetime,
        *,
        owner: str,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        _validate_claim(owner, lease)
        jobs = self.store.claim_meeting_alignment_jobs(1, _sqlite_time(now))
        if not jobs:
            return None
        job = jobs[0]
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=str(job.id),
            available_at=_source_time(job.eligible_at, fallback=now),
            priority=0,
            attempt=job.attempts,
            generation=job.attempts,
        )

    def release(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
    ) -> None:
        _validate_release(self.name, envelope, owner, now)
        with self.store._immediate_write_transaction() as db:
            cursor = db.execute(
                "update meeting_alignment_jobs set status='retry', "
                "attempts=max(attempts-1,0), locked_at=null, updated_at=? "
                "where id=? and status='processing' and attempts=?",
                (_sqlite_time(now), int(envelope.source_id), envelope.generation),
            )
            if cursor.rowcount != 1:
                raise ValueError("meeting dispatch claim is no longer active")


class WorkSummaryQueueAdapter:
    name = "work_summary"

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        with self.store._connect() as db:
            due = db.execute(
                "select count(*) from work_summary_inputs where status='pending' "
                "and (available_at='' or available_at<=?)",
                (now_text,),
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from work_summary_inputs where status='processing'"
            ).fetchone()[0]
        return QueueMetrics(due=int(due), claimed=int(claimed))

    def claim(
        self,
        now: datetime,
        *,
        owner: str,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        _validate_claim(owner, lease)
        claimed_items = self.store.claim_work_summary_inputs(1, now=_sqlite_time(now))
        if not claimed_items:
            return None
        claimed = claimed_items[0]
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=str(claimed.id),
            available_at=_source_time(claimed.created_at, fallback=now),
            priority=0,
            attempt=claimed.attempts,
            generation=claimed.attempts,
        )

    def release(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
    ) -> None:
        _validate_release(self.name, envelope, owner, now)
        with self.store._immediate_write_transaction() as db:
            cursor = db.execute(
                "update work_summary_inputs set status='pending', "
                "attempts=max(attempts-1,0), updated_at=? "
                "where id=? and status='processing' and attempts=?",
                (_sqlite_time(now), int(envelope.source_id), envelope.generation),
            )
            if cursor.rowcount != 1:
                raise ValueError("work-summary dispatch claim is no longer active")


def _lease_seconds(lease: timedelta) -> int:
    seconds = int(lease.total_seconds())
    if seconds <= 0:
        raise ValueError("dispatcher lease must be positive")
    return seconds


def _validate_claim(owner: str, lease: timedelta) -> None:
    if not owner.strip():
        raise ValueError("dispatcher owner must not be empty")
    _lease_seconds(lease)


def _validate_release(
    adapter_name: str,
    envelope: DispatchEnvelope,
    owner: str,
    now: datetime,
) -> None:
    if envelope.adapter_name != adapter_name:
        raise ValueError("dispatch envelope belongs to another adapter")
    if not owner.strip():
        raise ValueError("dispatcher owner must not be empty")
    ensure_utc_datetime(now, field="dispatcher release time")
