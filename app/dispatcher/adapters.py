from __future__ import annotations

import sqlite3
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.agent_cron.models import ensure_utc_datetime
from app.dispatcher.models import (
    DispatchEnvelope,
    QueueMetrics,
)
from app.store import AutoReplyStore


_CLAIM_SCAN_PAGE_SIZE = 32


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


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _LedgerClaimLifecycle:
    store: AutoReplyStore

    def assert_current(
        self, envelope: DispatchEnvelope, *, owner: str, now: datetime
    ) -> None:
        _assert_ledger_current(self.store, envelope=envelope, owner=owner, now=now)

    def complete(
        self, envelope: DispatchEnvelope, *, owner: str, now: datetime
    ) -> None:
        _complete_ledger(self.store, envelope=envelope, owner=owner, now=now)

    def record_lease_error(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        error: str,
        now: datetime,
    ) -> None:
        _record_ledger_error(
            self.store,
            envelope=envelope,
            owner=owner,
            error=error,
            now=now,
        )


class ScheduledTaskQueueAdapter(_LedgerClaimLifecycle):
    name = "scheduled"

    def __init__(self, store: AutoReplyStore, *, owner_alive=_process_is_alive) -> None:
        self.store = store
        self.owner_alive = owner_alive

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
            latest_error = _latest_claim_error(db, self.name) or _latest_error(
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
        owner_pid: int | None = None,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        owner_pid = os.getpid() if owner_pid is None else owner_pid
        _validate_claim(owner, lease)
        now_value = ensure_utc_datetime(now, field="scheduled dispatcher claim time")
        now_text = now_value.isoformat(timespec="seconds")
        lease_text = ensure_utc_datetime(
            now + lease, field="scheduled dispatcher claim expiry"
        ).isoformat(timespec="seconds")
        with self.store._immediate_write_transaction() as db:

            def fetch_page(after: sqlite3.Row | None, limit: int):
                keyset = ""
                params: list[object] = [self.name, now_text, now_text, now_text]
                if after is not None:
                    keyset = (
                        "and (run.scheduled_for>? or "
                        "(run.scheduled_for=? and run.id>?)) "
                    )
                    params.extend(
                        [after["scheduled_for"], after["scheduled_for"], after["id"]]
                    )
                params.append(limit)
                return db.execute(
                    "select run.*, claim.owner as claim_owner, "
                    "claim.owner_pid as claim_owner_pid, "
                    "claim.lease_expires_at as claim_expires_at "
                    "from scheduled_task_runs run "
                    "left join dispatcher_claim_leases claim "
                    "on claim.adapter_name=? and claim.source_id=cast(run.id as text) "
                    "where run.dispatch_status='pending' and run.scheduled_for<=? "
                    "and (run.lease_owner='' or run.lease_expires_at<=?) "
                    "and (claim.owner is null or claim.owner='' "
                    "or claim.lease_expires_at<=?) "
                    + keyset
                    + "order by run.scheduled_for,run.id limit ?",
                    params,
                ).fetchall()

            candidate = _scan_claimable_candidate(
                fetch_page,
                now=now_text,
                owner_alive=self.owner_alive,
            )
            if candidate is None:
                return None
            cursor = db.execute(
                "update scheduled_task_runs set lease_owner=?, lease_expires_at=? "
                "where id=? and dispatch_status='pending' and scheduled_for<=? "
                "and (lease_owner='' or lease_expires_at<=?)",
                (owner, lease_text, candidate["id"], now_text, now_text),
            )
            if cursor.rowcount != 1:
                return None
            generation = _acquire_lease(
                db,
                adapter_name=self.name,
                source_id=str(candidate["id"]),
                owner=owner,
                owner_pid=owner_pid,
                lease_expires_at=lease_text,
                now=now_text,
            )
            claimed = db.execute(
                "select * from scheduled_task_runs where id=?", (candidate["id"],)
            ).fetchone()
            assert claimed is not None
            run = self.store._scheduled_task_run_from_row(claimed)
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=str(run.id),
            available_at=run.scheduled_for,
            priority=0,
            attempt=1,
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
            _release_lease(
                db,
                envelope=envelope,
                owner=owner,
                now=ensure_utc_datetime(
                    now, field="scheduled dispatcher release time"
                ).isoformat(timespec="seconds"),
            )
            cursor = db.execute(
                "update scheduled_task_runs set lease_owner='', lease_expires_at=null "
                "where id=? and dispatch_status='pending' and lease_owner=?",
                (int(envelope.source_id), owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("scheduled dispatch claim is no longer owned")

    def renew(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
        lease: timedelta,
    ) -> None:
        _validate_release(self.name, envelope, owner, now)
        now_text = ensure_utc_datetime(
            now, field="scheduled dispatcher renewal time"
        ).isoformat(timespec="seconds")
        lease_text = ensure_utc_datetime(
            now + lease, field="scheduled dispatcher renewal expiry"
        ).isoformat(timespec="seconds")
        _lease_seconds(lease)
        with self.store._immediate_write_transaction() as db:
            _renew_lease_in_db(
                db,
                envelope=envelope,
                owner=owner,
                now=now_text,
                lease_expires_at=lease_text,
            )
            cursor = db.execute(
                "update scheduled_task_runs set lease_expires_at=? "
                "where id=? and dispatch_status='pending' and lease_owner=? "
                "and lease_expires_at>?",
                (lease_text, int(envelope.source_id), owner, now_text),
            )
            if cursor.rowcount != 1:
                raise ValueError("scheduled dispatch claim is no longer owned")

    def assert_current(
        self, envelope: DispatchEnvelope, *, owner: str, now: datetime
    ) -> None:
        super().assert_current(envelope, owner=owner, now=now)
        now_text = ensure_utc_datetime(
            now, field="scheduled dispatcher assertion time"
        ).isoformat(timespec="seconds")
        with self.store._connect() as db:
            row = db.execute(
                "select 1 from scheduled_task_runs where id=? "
                "and dispatch_status='pending' and lease_owner=? "
                "and lease_expires_at>?",
                (int(envelope.source_id), owner, now_text),
            ).fetchone()
        if row is None:
            raise ValueError("scheduled dispatch claim is no longer owned")

    def finish(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
        status: str,
        reason: str = "",
    ) -> None:
        self.assert_current(envelope, owner=owner, now=now)
        self.store.finish_scheduled_task_dispatch(
            int(envelope.source_id),
            owner=owner,
            status=status,
            reason=reason,
            now=now,
        )
        _complete_ledger(
            self.store,
            envelope=envelope,
            owner=owner,
            now=now,
        )


class ReplyQueueAdapter(_LedgerClaimLifecycle):
    name = "reply"

    def __init__(self, store: AutoReplyStore, *, owner_alive=_process_is_alive) -> None:
        self.store = store
        self.owner_alive = owner_alive

    def _channel_clause(self, alias: str = "task") -> str:
        return f"{alias}.channel in ('dingtalk','wechat')"

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        with self.store._connect() as db:
            pending = db.execute(
                "select count(*) from reply_tasks task where status='pending' and "
                + self._channel_clause()
            ).fetchone()[0]
            due = db.execute(
                "select count(*) from reply_tasks task "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(task.id as text) "
                "where " + self._channel_clause() + " and ((task.status='pending' "
                "and (task.available_at='' or task.available_at<=?) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (task.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?))",
                (self.name, now_text, now_text, now_text),
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from reply_tasks task "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(task.id as text) "
                "where " + self._channel_clause() + " and task.status='processing' and (claim.owner is null "
                "or claim.owner='' or claim.lease_expires_at>?)",
                (self.name, now_text),
            ).fetchone()[0]
            oldest = db.execute(
                "select min(case when task.available_at='' then task.created_at "
                "else task.available_at end) from reply_tasks task "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(task.id as text) "
                "where " + self._channel_clause() + " and ((task.status='pending' "
                "and (task.available_at='' or task.available_at<=?) "
                "and (claim.owner is null or claim.owner='' "
                "or claim.lease_expires_at<=?)) "
                "or (task.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?))",
                (self.name, now_text, now_text, now_text),
            ).fetchone()[0]
            latest_error = _latest_claim_error(db, self.name) or _latest_error(
                db, table="reply_tasks", column="error"
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
        owner_pid: int | None = None,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        owner_pid = os.getpid() if owner_pid is None else owner_pid
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        lease_text = _lease_expiry(now, lease)
        with self.store._immediate_write_transaction() as db:

            def fetch_page(after: sqlite3.Row | None, limit: int):
                after_id = 0 if after is None else int(after["id"])
                return db.execute(
                    "select task.*, claim.owner as claim_owner, "
                    "claim.generation as claim_generation, "
                    "claim.owner_pid as claim_owner_pid, "
                    "claim.lease_expires_at as claim_expires_at "
                    "from reply_tasks task left join dispatcher_claim_leases claim "
                    "on claim.adapter_name=? and claim.source_id=cast(task.id as text) "
                    "where " + self._channel_clause() + " and ((task.status='pending' "
                    "and (task.available_at='' or task.available_at<=?) "
                    "and (claim.owner is null or claim.owner='' "
                    "or claim.lease_expires_at<=?)) "
                    "or (task.status='processing' and claim.owner<>'' "
                    "and claim.lease_expires_at<=?)) and task.id>? "
                    "order by task.id limit ?",
                    (self.name, now_text, now_text, now_text, after_id, limit),
                ).fetchall()

            row = _scan_claimable_candidate(
                fetch_page,
                now=now_text,
                owner_alive=self.owner_alive,
            )
            if row is None:
                return None
            source_id = str(row["id"])
            cursor = db.execute(
                "update reply_tasks set status='processing', attempts=attempts+1, "
                "claimed_input_version=input_version, locked_at=?, available_at='', "
                "updated_at=? where id=? and status in ('pending','processing') and "
                + self._channel_clause("reply_tasks"),
                (now_text, now_text, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            generation = _acquire_lease(
                db,
                adapter_name=self.name,
                source_id=source_id,
                owner=owner,
                owner_pid=owner_pid,
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

    def renew(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
        lease: timedelta,
    ) -> None:
        _renew_lease(
            self.store,
            envelope=envelope,
            owner=owner,
            now=now,
            lease=lease,
        )


class ScheduledExecutionQueueAdapter(ReplyQueueAdapter):
    name = "scheduled_execution"

    def _channel_clause(self, alias: str = "task") -> str:
        return f"{alias}.channel='scheduled'"


class MeetingQueueAdapter(_LedgerClaimLifecycle):
    name = "meeting"

    def __init__(self, store: AutoReplyStore, *, owner_alive=_process_is_alive) -> None:
        self.store = store
        self.owner_alive = owner_alive

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
            latest_error = _latest_claim_error(db, self.name) or _latest_error(
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
        owner_pid: int | None = None,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        owner_pid = os.getpid() if owner_pid is None else owner_pid
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        with self.store._immediate_write_transaction() as db:

            def fetch_page(after: sqlite3.Row | None, limit: int):
                keyset = ""
                params: list[object] = [
                    self.name,
                    now_text,
                    now_text,
                    now_text,
                    now_text,
                ]
                if after is not None:
                    keyset = (
                        "and (datetime(job.eligible_at)>datetime(?) or "
                        "(datetime(job.eligible_at)=datetime(?) and job.id>?)) "
                    )
                    params.extend(
                        [after["eligible_at"], after["eligible_at"], after["id"]]
                    )
                params.append(limit)
                return db.execute(
                    "select job.*, claim.owner as claim_owner, "
                    "claim.generation as claim_generation, "
                    "claim.owner_pid as claim_owner_pid, "
                    "claim.lease_expires_at as claim_expires_at "
                    "from meeting_alignment_jobs job "
                    "left join dispatcher_claim_leases claim "
                    "on claim.adapter_name=? and claim.source_id=cast(job.id as text) "
                    "where ((job.status in ('waiting','pending','retry') "
                    "and datetime(job.eligible_at)<=datetime(?) "
                    "and (job.available_at='' "
                    "or datetime(job.available_at)<=datetime(?)) "
                    "and (claim.owner is null or claim.owner='' "
                    "or claim.lease_expires_at<=?)) "
                    "or (job.status='processing' and claim.owner<>'' "
                    "and claim.lease_expires_at<=?)) "
                    + keyset
                    + "order by datetime(job.eligible_at), job.id limit ?",
                    params,
                ).fetchall()

            row = _scan_claimable_candidate(
                fetch_page,
                now=now_text,
                owner_alive=self.owner_alive,
            )
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
                owner_pid=owner_pid,
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

    def renew(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
        lease: timedelta,
    ) -> None:
        _renew_lease(
            self.store,
            envelope=envelope,
            owner=owner,
            now=now,
            lease=lease,
        )


class OkrReviewQueueAdapter(_LedgerClaimLifecycle):
    """Claim pending OKR review requests without running a second poll loop."""

    name = "okr_review"

    def __init__(self, store: AutoReplyStore, *, owner_alive=_process_is_alive) -> None:
        self.store = store
        self.owner_alive = owner_alive

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        with self.store._connect() as db:
            pending = db.execute(
                "select count(*) from okr_review_requests where status='pending'"
            ).fetchone()[0]
            due = db.execute(
                "select count(*) from okr_review_requests request "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(request.id as text) "
                "where (request.status='pending' and (claim.owner is null "
                "or claim.owner='' or claim.lease_expires_at<=?)) "
                "or (request.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?)",
                (self.name, now_text, now_text),
            ).fetchone()[0]
            claimed = db.execute(
                "select count(*) from okr_review_requests request "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(request.id as text) "
                "where request.status='processing' and (claim.owner is null "
                "or claim.owner='' or claim.lease_expires_at>?)",
                (self.name, now_text),
            ).fetchone()[0]
            oldest = db.execute(
                "select min(request.created_at) from okr_review_requests request "
                "left join dispatcher_claim_leases claim "
                "on claim.adapter_name=? and claim.source_id=cast(request.id as text) "
                "where (request.status='pending' and (claim.owner is null "
                "or claim.owner='' or claim.lease_expires_at<=?)) "
                "or (request.status='processing' and claim.owner<>'' "
                "and claim.lease_expires_at<=?)",
                (self.name, now_text, now_text),
            ).fetchone()[0]
            latest_error = _latest_claim_error(db, self.name) or _latest_error(
                db, table="okr_review_requests", column="error"
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
        owner_pid: int | None = None,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        owner_pid = os.getpid() if owner_pid is None else owner_pid
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        with self.store._immediate_write_transaction() as db:

            def fetch_page(after: sqlite3.Row | None, limit: int):
                after_id = 0 if after is None else int(after["id"])
                return db.execute(
                    "select request.*, claim.owner as claim_owner, "
                    "claim.owner_pid as claim_owner_pid, "
                    "claim.lease_expires_at as claim_expires_at "
                    "from okr_review_requests request "
                    "left join dispatcher_claim_leases claim "
                    "on claim.adapter_name=? "
                    "and claim.source_id=cast(request.id as text) "
                    "where ((request.status='pending' and (claim.owner is null "
                    "or claim.owner='' or claim.lease_expires_at<=?)) "
                    "or (request.status='processing' and claim.owner<>'' "
                    "and claim.lease_expires_at<=?)) and request.id>? "
                    "order by request.id limit ?",
                    (self.name, now_text, now_text, after_id, limit),
                ).fetchall()

            row = _scan_claimable_candidate(
                fetch_page,
                now=now_text,
                owner_alive=self.owner_alive,
            )
            if row is None:
                return None
            source_id = str(row["id"])
            cursor = db.execute(
                "update okr_review_requests set status='processing', error='', "
                "updated_at=? where id=? and status in ('pending','processing')",
                (now_text, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            generation = _acquire_lease(
                db,
                adapter_name=self.name,
                source_id=source_id,
                owner=owner,
                owner_pid=owner_pid,
                lease_expires_at=_lease_expiry(now, lease),
                now=now_text,
            )
        return DispatchEnvelope(
            adapter_name=self.name,
            source_id=source_id,
            available_at=_source_time(str(row["created_at"]), fallback=now),
            priority=0,
            attempt=generation,
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
                "update okr_review_requests set status='pending', updated_at=? "
                "where id=? and status='processing'",
                (_sqlite_time(now), int(envelope.source_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("OKR review dispatch claim is no longer owned")

    def renew(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
        lease: timedelta,
    ) -> None:
        _renew_lease(
            self.store,
            envelope=envelope,
            owner=owner,
            now=now,
            lease=lease,
        )


class WorkSummaryQueueAdapter(_LedgerClaimLifecycle):
    name = "work_summary"

    def __init__(self, store: AutoReplyStore, *, owner_alive=_process_is_alive) -> None:
        self.store = store
        self.owner_alive = owner_alive

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
            latest_error = _latest_claim_error(db, self.name) or _latest_error(
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
        owner_pid: int | None = None,
        lease: timedelta,
    ) -> DispatchEnvelope | None:
        owner_pid = os.getpid() if owner_pid is None else owner_pid
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        with self.store._immediate_write_transaction() as db:

            def fetch_page(after: sqlite3.Row | None, limit: int):
                after_id = 0 if after is None else int(after["id"])
                return db.execute(
                    "select item.*, claim.owner as claim_owner, "
                    "claim.generation as claim_generation, "
                    "claim.owner_pid as claim_owner_pid, "
                    "claim.lease_expires_at as claim_expires_at "
                    "from work_summary_inputs item "
                    "left join dispatcher_claim_leases claim "
                    "on claim.adapter_name=? and claim.source_id=cast(item.id as text) "
                    "where ((item.status='pending' "
                    "and (item.available_at='' or item.available_at<=?) "
                    "and (claim.owner is null or claim.owner='' "
                    "or claim.lease_expires_at<=?)) "
                    "or (item.status='processing' and claim.owner<>'' "
                    "and claim.lease_expires_at<=?)) and item.id>? "
                    "order by item.id limit ?",
                    (self.name, now_text, now_text, now_text, after_id, limit),
                ).fetchall()

            row = _scan_claimable_candidate(
                fetch_page,
                now=now_text,
                owner_alive=self.owner_alive,
            )
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
                owner_pid=owner_pid,
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

    def renew(
        self,
        envelope: DispatchEnvelope,
        *,
        owner: str,
        now: datetime,
        lease: timedelta,
    ) -> None:
        _renew_lease(
            self.store,
            envelope=envelope,
            owner=owner,
            now=now,
            lease=lease,
        )


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
    owner_pid: int,
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
        "(adapter_name, source_id, owner, owner_pid, generation, lease_expires_at, updated_at) "
        "values (?, ?, ?, ?, ?, ?, ?) "
        "on conflict(adapter_name, source_id) do update set "
        "owner=excluded.owner, owner_pid=excluded.owner_pid, generation=excluded.generation, "
        "lease_expires_at=excluded.lease_expires_at, terminal_at='', last_error='', "
        "updated_at=excluded.updated_at",
        (adapter_name, source_id, owner, owner_pid, generation, lease_expires_at, now),
    )
    return generation


def _scan_claimable_candidate(
    fetch_page: Callable[[sqlite3.Row | None, int], list[sqlite3.Row]],
    *,
    now: str,
    owner_alive,
) -> sqlite3.Row | None:
    after = None
    while True:
        candidates = fetch_page(after, _CLAIM_SCAN_PAGE_SIZE)
        if not candidates:
            return None
        for candidate in candidates:
            if not _live_expired_owner(candidate, now, owner_alive):
                return candidate
        if len(candidates) < _CLAIM_SCAN_PAGE_SIZE:
            return None
        after = candidates[-1]


def _live_expired_owner(row, now: str, owner_alive) -> bool:
    return bool(
        str(row["claim_owner"] or "")
        and str(row["claim_expires_at"] or "") <= now
        and owner_alive(int(row["claim_owner_pid"] or 0))
    )


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


def _renew_lease(
    store: AutoReplyStore,
    *,
    envelope: DispatchEnvelope,
    owner: str,
    now: datetime,
    lease: timedelta,
) -> None:
    _validate_release(envelope.adapter_name, envelope, owner, now)
    now_text = _sqlite_time(now)
    with store._immediate_write_transaction() as db:
        _renew_lease_in_db(
            db,
            envelope=envelope,
            owner=owner,
            now=now_text,
            lease_expires_at=_lease_expiry(now, lease),
        )


def _renew_lease_in_db(
    db: sqlite3.Connection,
    *,
    envelope: DispatchEnvelope,
    owner: str,
    now: str,
    lease_expires_at: str,
) -> None:
    cursor = db.execute(
        "update dispatcher_claim_leases set lease_expires_at=?, last_error='', "
        "updated_at=? where adapter_name=? and source_id=? and owner=? "
        "and generation=? and lease_expires_at>? and terminal_at=''",
        (
            lease_expires_at,
            now,
            envelope.adapter_name,
            envelope.source_id,
            owner,
            envelope.generation,
            now,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError("dispatcher source claim is no longer owned")


def _assert_ledger_current(
    store: AutoReplyStore,
    *,
    envelope: DispatchEnvelope,
    owner: str,
    now: datetime,
) -> None:
    with store._connect() as db:
        row = db.execute(
            "select 1 from dispatcher_claim_leases where adapter_name=? "
            "and source_id=? and owner=? and generation=? "
            "and lease_expires_at>? and terminal_at=''",
            (
                envelope.adapter_name,
                envelope.source_id,
                owner,
                envelope.generation,
                _sqlite_time(now),
            ),
        ).fetchone()
    if row is None:
        raise ValueError("dispatcher source claim is no longer owned")


def _complete_ledger(
    store: AutoReplyStore,
    *,
    envelope: DispatchEnvelope,
    owner: str,
    now: datetime,
) -> None:
    now_text = _sqlite_time(now)
    with store._immediate_write_transaction() as db:
        cursor = db.execute(
            "update dispatcher_claim_leases set owner='', lease_expires_at='', "
            "terminal_at=?, last_error='', updated_at=? where adapter_name=? "
            "and source_id=? and owner=? and generation=?",
            (
                now_text,
                now_text,
                envelope.adapter_name,
                envelope.source_id,
                owner,
                envelope.generation,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("dispatcher source claim is no longer owned")
        db.execute(
            "delete from dispatcher_claim_leases where terminal_at<>'' and rowid not in ("
            "select rowid from dispatcher_claim_leases where terminal_at<>'' "
            "order by terminal_at desc, rowid desc limit 10000)"
        )


def _record_ledger_error(
    store: AutoReplyStore,
    *,
    envelope: DispatchEnvelope,
    owner: str,
    error: str,
    now: datetime,
) -> None:
    with store._immediate_write_transaction() as db:
        db.execute(
            "update dispatcher_claim_leases set last_error=?, updated_at=? "
            "where adapter_name=? and source_id=?",
            (
                error,
                _sqlite_time(now),
                envelope.adapter_name,
                envelope.source_id,
            ),
        )


def _latest_claim_error(db: sqlite3.Connection, adapter_name: str) -> str:
    row = db.execute(
        "select last_error from dispatcher_claim_leases where adapter_name=? "
        "and last_error<>'' order by updated_at desc, rowid desc limit 1",
        (adapter_name,),
    ).fetchone()
    return "" if row is None else str(row[0])


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
