from __future__ import annotations

import sqlite3
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


class ScheduledTaskQueueAdapter:
    name = "scheduled"

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = ensure_utc_datetime(
            now, field="scheduled dispatcher metrics time"
        ).isoformat(timespec="seconds")
        with self.store._connect() as db:
            pending = db.execute(
                "select count(*) from scheduled_task_runs "
                "where dispatch_status='pending' "
                "and (lease_owner='' or lease_expires_at<=?)",
                (now_text,),
            ).fetchone()[0]
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
            oldest = db.execute(
                "select min(scheduled_for) from scheduled_task_runs "
                "where dispatch_status='pending' and scheduled_for<=? "
                "and (lease_owner='' or lease_expires_at<=?)",
                (now_text, now_text),
            ).fetchone()[0]
            latest_error = _latest_error(
                db,
                table="scheduled_task_runs",
                column="skip_or_error_reason",
                order_by="id desc",
            )
        return QueueMetrics(
            pending=int(pending),
            due=int(due),
            oldest_available_at=(
                _source_time(str(oldest), fallback=now) if oldest else None
            ),
            running=int(claimed),
            latest_error=latest_error,
        )

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
            pending = db.execute(
                "select count(*) from reply_tasks where status='pending'"
            ).fetchone()[0]
            due = db.execute(
                "select count(*) from reply_tasks task "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(task.id as text) "
                "where (task.status='pending' "
                "and (task.available_at='' or task.available_at<=?) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (task.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?)",
                (self.name, now_text, now_text, now_text),
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from reply_tasks task "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(task.id as text) "
                "where task.status='processing' and (claim.owner is null "
                "or claim.owner='' or claim.lease_expires_at>?)",
                (self.name, now_text),
            ).fetchone()[0]
            oldest = db.execute(
                "select min(case when task.available_at='' then task.created_at "
                "else task.available_at end) from reply_tasks task "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(task.id as text) "
                "where (task.status='pending' "
                "and (task.available_at='' or task.available_at<=?) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (task.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?)",
                (self.name, now_text, now_text, now_text),
            ).fetchone()[0]
            latest_error = _latest_error(db, table="reply_tasks", column="error")
        return QueueMetrics(
            pending=int(pending),
            due=int(due),
            oldest_available_at=(
                _source_time(str(oldest), fallback=now) if oldest else None
            ),
            running=int(claimed),
            latest_error=latest_error,
        )

    def claim(
        self,
        now: datetime,
        *,
        owner: str,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        lease_text = _lease_expiry(now, lease)
        with self.store._immediate_write_transaction() as db:
            row = db.execute(
                "select task.*, claim.owner as claim_owner, "
                "claim.generation as claim_generation, "
                "claim.lease_expires_at as claim_expires_at "
                "from reply_tasks task left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(task.id as text) "
                "where (task.status='pending' "
                "and (task.available_at='' or task.available_at<=?) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (task.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?) order by task.id limit 1",
                (self.name, now_text, now_text, now_text),
            ).fetchone()
            if row is None:
                return None
            source_id = str(row["id"])
            cursor = db.execute(
                "update reply_tasks set status='processing', attempts=attempts+1, "
                "claimed_input_version=input_version, locked_at=?, available_at='', "
                "updated_at=? where id=? and status in ('pending','processing')",
                (now_text, now_text, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            generation = _acquire_lease(
                db,
                adapter_name=self.name,
                source_id=source_id,
                owner=owner,
                lease_expires_at=lease_text,
                now=now_text,
            )
            claimed = db.execute(
                "select * from reply_tasks where id=?", (row["id"],)
            ).fetchone()
            assert claimed is not None
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=source_id,
            available_at=_source_time(str(claimed["created_at"]), fallback=now),
            priority=0,
            attempt=int(claimed["attempts"]),
            generation=generation,
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
            _release_lease(db, envelope=envelope, owner=owner, now=_sqlite_time(now))
            cursor = db.execute(
                "update reply_tasks set status='pending', attempts=max(attempts-1,0), "
                "locked_at=null, updated_at=? where id=? and status='processing'",
                (_sqlite_time(now), int(envelope.source_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("reply dispatch claim is no longer owned")


class MeetingQueueAdapter:
    name = "meeting"

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        with self.store._connect() as db:
            pending = db.execute(
                "select count(*) from meeting_alignment_jobs "
                "where status in ('waiting','pending','retry')"
            ).fetchone()[0]
            due = db.execute(
                "select count(*) from meeting_alignment_jobs job "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(job.id as text) "
                "where (job.status in ('waiting','pending','retry') "
                "and datetime(job.eligible_at)<=datetime(?) "
                "and (job.available_at='' or datetime(job.available_at)<=datetime(?)) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (job.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?)",
                (self.name, now_text, now_text, now_text, now_text),
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from meeting_alignment_jobs job "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(job.id as text) "
                "where job.status='processing' and (claim.owner is null "
                "or claim.owner='' or claim.lease_expires_at>?)",
                (self.name, now_text),
            ).fetchone()[0]
            oldest = db.execute(
                "select min(case when job.available_at='' then job.eligible_at "
                "when datetime(job.available_at)>datetime(job.eligible_at) "
                "then job.available_at else job.eligible_at end) "
                "from meeting_alignment_jobs job "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(job.id as text) "
                "where (job.status in ('waiting','pending','retry') "
                "and datetime(job.eligible_at)<=datetime(?) "
                "and (job.available_at='' or datetime(job.available_at)<=datetime(?)) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (job.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?)",
                (self.name, now_text, now_text, now_text, now_text),
            ).fetchone()[0]
            latest_error = _latest_error(
                db, table="meeting_alignment_jobs", column="error"
            )
        return QueueMetrics(
            pending=int(pending),
            due=int(due),
            oldest_available_at=(
                _source_time(str(oldest), fallback=now) if oldest else None
            ),
            running=int(claimed),
            latest_error=latest_error,
        )

    def claim(
        self,
        now: datetime,
        *,
        owner: str,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        with self.store._immediate_write_transaction() as db:
            row = db.execute(
                "select job.*, claim.owner as claim_owner, "
                "claim.generation as claim_generation, "
                "claim.lease_expires_at as claim_expires_at "
                "from meeting_alignment_jobs job "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(job.id as text) "
                "where (job.status in ('waiting','pending','retry') "
                "and datetime(job.eligible_at)<=datetime(?) "
                "and (job.available_at='' or datetime(job.available_at)<=datetime(?)) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (job.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?) "
                "order by datetime(job.eligible_at), job.id limit 1",
                (self.name, now_text, now_text, now_text, now_text),
            ).fetchone()
            if row is None:
                return None
            source_id = str(row["id"])
            cursor = db.execute(
                "update meeting_alignment_jobs set status='processing', "
                "attempts=attempts+1, locked_at=?, updated_at=? "
                "where id=? and status in ('waiting','pending','retry','processing')",
                (now_text, now_text, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            generation = _acquire_lease(
                db,
                adapter_name=self.name,
                source_id=source_id,
                owner=owner,
                lease_expires_at=_lease_expiry(now, lease),
                now=now_text,
            )
            claimed = db.execute(
                "select * from meeting_alignment_jobs where id=?", (row["id"],)
            ).fetchone()
            assert claimed is not None
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=source_id,
            available_at=_source_time(str(claimed["eligible_at"]), fallback=now),
            priority=0,
            attempt=int(claimed["attempts"]),
            generation=generation,
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
            _release_lease(db, envelope=envelope, owner=owner, now=_sqlite_time(now))
            cursor = db.execute(
                "update meeting_alignment_jobs set status='retry', "
                "attempts=max(attempts-1,0), locked_at=null, updated_at=? "
                "where id=? and status='processing'",
                (_sqlite_time(now), int(envelope.source_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("meeting dispatch claim is no longer owned")


class WorkSummaryQueueAdapter:
    name = "work_summary"

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        with self.store._connect() as db:
            pending = db.execute(
                "select count(*) from work_summary_inputs where status='pending'"
            ).fetchone()[0]
            due = db.execute(
                "select count(*) from work_summary_inputs item "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(item.id as text) "
                "where (item.status='pending' "
                "and (item.available_at='' or item.available_at<=?) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (item.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?)",
                (self.name, now_text, now_text, now_text),
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from work_summary_inputs item "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(item.id as text) "
                "where item.status='processing' and (claim.owner is null "
                "or claim.owner='' or claim.lease_expires_at>?)",
                (self.name, now_text),
            ).fetchone()[0]
            oldest = db.execute(
                "select min(case when item.available_at='' then item.created_at "
                "else item.available_at end) from work_summary_inputs item "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(item.id as text) "
                "where (item.status='pending' "
                "and (item.available_at='' or item.available_at<=?) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (item.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?)",
                (self.name, now_text, now_text, now_text),
            ).fetchone()[0]
            latest_error = _latest_error(
                db, table="work_summary_inputs", column="error"
            )
        return QueueMetrics(
            pending=int(pending),
            due=int(due),
            oldest_available_at=(
                _source_time(str(oldest), fallback=now) if oldest else None
            ),
            running=int(claimed),
            latest_error=latest_error,
        )

    def claim(
        self,
        now: datetime,
        *,
        owner: str,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        with self.store._immediate_write_transaction() as db:
            row = db.execute(
                "select item.*, claim.owner as claim_owner, "
                "claim.generation as claim_generation, "
                "claim.lease_expires_at as claim_expires_at "
                "from work_summary_inputs item "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(item.id as text) "
                "where (item.status='pending' "
                "and (item.available_at='' or item.available_at<=?) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (item.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?) order by item.id limit 1",
                (self.name, now_text, now_text, now_text),
            ).fetchone()
            if row is None:
                return None
            source_id = str(row["id"])
            cursor = db.execute(
                "update work_summary_inputs set status='processing', "
                "attempts=attempts+1, error='', available_at='', updated_at=? "
                "where id=? and status in ('pending','processing')",
                (now_text, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            generation = _acquire_lease(
                db,
                adapter_name=self.name,
                source_id=source_id,
                owner=owner,
                lease_expires_at=_lease_expiry(now, lease),
                now=now_text,
            )
            claimed = db.execute(
                "select * from work_summary_inputs where id=?", (row["id"],)
            ).fetchone()
            assert claimed is not None
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=source_id,
            available_at=_source_time(str(claimed["created_at"]), fallback=now),
            priority=0,
            attempt=int(claimed["attempts"]),
            generation=generation,
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
            _release_lease(db, envelope=envelope, owner=owner, now=_sqlite_time(now))
            cursor = db.execute(
                "update work_summary_inputs set status='pending', "
                "attempts=max(attempts-1,0), updated_at=? "
                "where id=? and status='processing'",
                (_sqlite_time(now), int(envelope.source_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("work-summary dispatch claim is no longer owned")


def _lease_seconds(lease: timedelta) -> int:
    seconds = int(lease.total_seconds())
    if seconds <= 0:
        raise ValueError("dispatcher lease must be positive")
    return seconds


def _lease_expiry(now: datetime, lease: timedelta) -> str:
    _lease_seconds(lease)
    return _sqlite_time(now + lease)


def _acquire_lease(
    db: sqlite3.Connection,
    *,
    adapter_name: str,
    source_id: str,
    owner: str,
    lease_expires_at: str,
    now: str,
) -> int:
    row = db.execute(
        "select owner, generation, lease_expires_at "
        "from dispatcher_claim_leases where adapter_name=? and source_id=?",
        (adapter_name, source_id),
    ).fetchone()
    if row is not None and str(row["owner"]) and str(row["lease_expires_at"]) > now:
        raise ValueError("dispatcher source lease is still active")
    generation = 1 if row is None else int(row["generation"]) + 1
    db.execute(
        "insert into dispatcher_claim_leases "
        "(adapter_name, source_id, owner, generation, lease_expires_at, updated_at) "
        "values (?, ?, ?, ?, ?, ?) "
        "on conflict(adapter_name, source_id) do update set "
        "owner=excluded.owner, generation=excluded.generation, "
        "lease_expires_at=excluded.lease_expires_at, updated_at=excluded.updated_at",
        (adapter_name, source_id, owner, generation, lease_expires_at, now),
    )
    return generation


def _release_lease(
    db: sqlite3.Connection,
    *,
    envelope: DispatchEnvelope,
    owner: str,
    now: str,
) -> None:
    cursor = db.execute(
        "update dispatcher_claim_leases set owner='', lease_expires_at='', updated_at=? "
        "where adapter_name=? and source_id=? and owner=? and generation=?",
        (
            now,
            envelope.adapter_name,
            envelope.source_id,
            owner,
            envelope.generation,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError("dispatcher source claim is no longer owned")


def _latest_error(
    db: sqlite3.Connection,
    *,
    table: str,
    column: str,
    order_by: str = "updated_at desc, id desc",
) -> str:
    row = db.execute(
        f"select {column} from {table} where trim({column})<>'' "
        f"order by {order_by} limit 1"
    ).fetchone()
    return "" if row is None else str(row[0])


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
