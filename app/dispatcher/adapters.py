from __future__ import annotations

from app.deployment_drain import claim_connection
from app.run_storage import SCHEDULED_RUN_SQL
from app.state_definitions import state_projection
from app.state_storage import state_json_field, update_state, upsert_state
from app.task20_definitions import task20_column, task20_projection
from app import task_lifecycle_storage as task_claims
from app.task_lifecycle_definitions import DISPATCH_KINDS, dispatch_source_projection
from app.action_followup_sync_definitions import SYNC_ACTION_PROJECTIONS
from app.action_storage import update_sync_action, task_delivery_projection, task_delivery_set_parts


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


MEETING_JOB_SQL = task20_projection('meeting_alignment_jobs')
OKR_REQUEST_SQL = task20_projection('okr_review_requests')
REPLY_TASK_SQL = task20_projection('reply_tasks')
WORK_SUMMARY_INPUT_SQL = task20_projection('work_summary_inputs')


_CLAIM_SCAN_PAGE_SIZE = 32
_LEASE_PROJECTION = "(" + state_projection("dispatcher_claim_leases", include_rowid=True) + ")"
BUSINESS_TASK_TODO_OUTBOX_PROJECTION = "(" + SYNC_ACTION_PROJECTIONS["business_task_todo_sync_outbox"] + ")"
TASK_MEMORY_WRITE_PROJECTION = "(" + task_delivery_projection("task_memory_write_events") + ")"


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

    def let_go(self, envelope: DispatchEnvelope, *, owner: str) -> None:
        """Hand back a claim whose handler has already finished."""
        _let_go_of_lease(self.store, envelope=envelope, owner=owner)


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
                f"select count(*) from ({SCHEDULED_RUN_SQL}) as scheduled_task_runs "
                "where dispatch_status='pending' "
                "and (lease_owner='' or lease_expires_at<=?)",
                (now_text,),
            ).fetchone()[0]
            due = db.execute(
                f"select count(*) from ({SCHEDULED_RUN_SQL}) as scheduled_task_runs "
                "where dispatch_status='pending' and scheduled_for<=? "
                "and (lease_owner='' or lease_expires_at<=?)",
                (now_text, now_text),
            ).fetchone()[0]
            claimed = db.execute(
                f"select count(*) from ({SCHEDULED_RUN_SQL}) as scheduled_task_runs "
                "where dispatch_status='pending' and lease_owner<>'' "
                "and lease_expires_at>?",
                (now_text,),
            ).fetchone()[0]
            oldest = db.execute(
                f"select min(scheduled_for) from ({SCHEDULED_RUN_SQL}) as scheduled_task_runs "
                "where dispatch_status='pending' and scheduled_for<=? "
                "and (lease_owner='' or lease_expires_at<=?)",
                (now_text, now_text),
            ).fetchone()[0]
            latest_error = _latest_actionable_error(
                db,
                column="skip_or_error_reason",
                source_projection=(
                    f"from ({SCHEDULED_RUN_SQL}) source "
                    f"left join {_LEASE_PROJECTION} source_claim "
                    "on source_claim.adapter_name=? "
                    "and source_claim.source_id=cast(source.id as text) "
                    "where source.dispatch_status='pending' and source.scheduled_for<=? "
                    "and (source.lease_owner='' or source.lease_expires_at<=?)"
                ),
                source_params=(self.name, now_text, now_text),
                order_by="id desc",
                candidate_is_protected=_scheduled_candidate_is_protected,
                owner_alive=self.owner_alive,
                now=now_text,
                source_updated_at_column="scheduled_for",
                source_fields=(
                    "source.lease_owner as lease_owner",
                    "source.lease_expires_at as lease_expires_at",
                ),
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
        lease_text = _iso_lease_expiry(
            now,
            lease,
            field="scheduled dispatcher claim expiry",
        )
        with self.store._connect() as db:

            def fetch_page(after: sqlite3.Row | None, limit: int):
                keyset = ""
                params: list[object] = [self.name, now_text]
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
                    "claim.generation as claim_generation, "
                    "claim.lease_expires_at as claim_expires_at "
                    f"from ({SCHEDULED_RUN_SQL}) run "
                    f"left join {_LEASE_PROJECTION} claim "
                    "on claim.adapter_name=? and claim.source_id=cast(run.id as text) "
                    "where run.dispatch_status='pending' and run.scheduled_for<=? "
                    + keyset
                    + "order by run.scheduled_for,run.id limit ?",
                    params,
                ).fetchall()

            candidate = _scan_scheduled_claimable_candidate(
                fetch_page,
                now=now_text,
                owner_alive=self.owner_alive,
            )
            if candidate is None:
                return None
        with claim_connection(self.store, immediate=True) as db:
            if db is None:
                return None
            candidate = _scan_scheduled_claimable_candidate(
                fetch_page,
                now=now_text,
                owner_alive=self.owner_alive,
            )
            if candidate is None:
                return None
            previous_source_owner = str(candidate["lease_owner"] or "")
            cursor = db.execute(
                "update runs set lease_owner=?, lease_expires_at=? "
                "where record_type='scheduled_task_runs' and cast(legacy_key as integer)=? and dispatch_status='pending' and scheduled_for<=? "
                "and (lease_owner='' or lease_expires_at<=? or lease_owner=?)",
                (
                    owner,
                    lease_text,
                    candidate["id"],
                    now_text,
                    now_text,
                    previous_source_owner,
                ),
            )
            if cursor.rowcount != 1:
                return None
            if _scheduled_claim_owner_is_dead(
                candidate,
                now=now_text,
                owner_alive=self.owner_alive,
            ):
                cleared = update_state(
                    db, "dispatcher_claim_leases",
                    _lease_rowid(db, self.name, str(candidate["id"])),
                    {"owner": "", "lease_expires_at": "", "updated_at": now_text},
                    where_sql=(
                        "adapter_name=? and source_id=? and owner=? and owner_pid=? "
                        "and generation=? and lease_expires_at=?"
                    ),
                    parameters=(
                        self.name, str(candidate["id"]), candidate["claim_owner"],
                        candidate["claim_owner_pid"], candidate["claim_generation"],
                        candidate["claim_expires_at"],
                    ),
                )
                if cleared != 1:
                    raise ValueError("scheduled dead-owner claim changed during takeover")
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
                f"select {self.store._scheduled_task_run_columns()} from ({SCHEDULED_RUN_SQL}) as scheduled_task_runs where id=?", (candidate["id"],)
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
                "update runs set lease_owner='', lease_expires_at=null "
                "where record_type='scheduled_task_runs' and cast(legacy_key as integer)=? and dispatch_status='pending' and lease_owner=?",
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
        lease_text = _iso_lease_expiry(
            now,
            lease,
            field="scheduled dispatcher renewal expiry",
        )
        with self.store._immediate_write_transaction() as db:
            _renew_lease_in_db(
                db,
                envelope=envelope,
                owner=owner,
                now=now_text,
                lease_expires_at=lease_text,
            )
            cursor = db.execute(
                "update runs set lease_expires_at=? "
                "where record_type='scheduled_task_runs' and cast(legacy_key as integer)=? and dispatch_status='pending' and lease_owner=? "
                "and lease_expires_at>?",
                (lease_text, int(envelope.source_id), owner, now_text),
            )
            if cursor.rowcount != 1:
                raise ValueError("scheduled dispatch claim is no longer owned")

    def assert_current(
        self, envelope: DispatchEnvelope, *, owner: str, now: datetime
    ) -> None:
        now_text = ensure_utc_datetime(
            now, field="scheduled dispatcher assertion time"
        ).isoformat(timespec="seconds")
        with self.store._connect() as db:
            _assert_ledger_current_in_db(
                db,
                envelope=envelope,
                owner=owner,
                now=now_text,
            )
            row = db.execute(
                f"select 1 from ({SCHEDULED_RUN_SQL}) as scheduled_task_runs where id=? "
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


class _TaskQueueAdapter(_LedgerClaimLifecycle):
    """One Task-owned due/claim/release mechanism with exact source phases."""

    def __init__(self, store: AutoReplyStore, *, owner_alive=_process_is_alive) -> None:
        self.store = store
        self.owner_alive = owner_alive

    @property
    def kind(self):
        return DISPATCH_KINDS[self.name]

    @property
    def source(self):
        return dispatch_source_projection(self.kind)

    def _channel_clause(self, alias="task"):
        return "1"

    def _pending(self, alias="task"):
        phases = "('waiting','pending','retry')" if self.kind == "meeting_alignment_jobs" else "('pending')"
        return alias + ".task_status='pending' and " + alias + ".status in " + phases

    def _due(self, now_text, alias="task"):
        due = ""
        params = []
        if self.kind == "meeting_alignment_jobs":
            due += " and datetime(" + alias + ".eligible_at)<=datetime(?)"
            params.append(now_text)
        if self.kind != "okr_review_requests":
            due += " and (" + alias + ".available_at='' or datetime(" + alias + ".available_at)<=datetime(?))"
            params.append(now_text)
        return (self._channel_clause(alias) + " and " + alias + ".task_status in ('pending','processing') and ((" + self._pending(alias) + due
            + " and (" + alias + ".claim_owner is null or " + alias + ".claim_owner='' or " + alias + ".claim_expires_at<=?))"
            + " or (" + alias + ".status='processing' and " + alias + ".claim_owner<>'' and " + alias + ".claim_expires_at<=?))",
            (*params, now_text, now_text))

    def _ready_at(self, alias="task"):
        if self.kind == "meeting_alignment_jobs":
            return ("max(datetime(" + alias + ".eligible_at),coalesce(datetime(nullif(" + alias
                    + ".available_at,'')),datetime(" + alias + ".eligible_at)))")
        return alias + ".id"

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        eligible, params = self._due(now_text)
        joined = f"from ({self.source}) task "
        if self.kind == "meeting_alignment_jobs":
            oldest_sql = ("case when task.available_at='' then task.eligible_at "
                          "when datetime(task.available_at)>datetime(task.eligible_at) "
                          "then task.available_at else task.eligible_at end")
        elif self.kind == "okr_review_requests":
            oldest_sql = "task.created_at"
        else:
            oldest_sql = "case when task.available_at='' then task.created_at else task.available_at end"
        with self.store._connect() as db:
            pending = db.execute(f"select count(*) from ({self.source}) task where "
                                 + self._pending() + " and " + self._channel_clause()).fetchone()[0]
            due = db.execute("select count(*) " + joined + "where " + eligible, params).fetchone()[0]
            running = db.execute("select count(*) " + joined + "where " + self._channel_clause()
                + " and task.task_status='processing' and task.status='processing' "
                "and (task.claim_owner is null or task.claim_owner='' or task.claim_expires_at>?)",
                (now_text,)).fetchone()[0]
            oldest = db.execute("select min(" + oldest_sql + ") " + joined + "where " + eligible,
                                params).fetchone()[0]
            error_eligible, error_params = self._due(now_text, "source")
            latest_error = _latest_actionable_error(db, column="error",
                source_projection=f"from ({self.source}) source left join {_LEASE_PROJECTION} source_claim "
                    "on source_claim.adapter_name=? and source_claim.source_id=cast(source.id as text) where " + error_eligible,
                source_params=(self.name, *error_params), candidate_is_protected=_live_expired_owner,
                owner_alive=self.owner_alive, now=now_text, task_owned=True)
        return QueueMetrics(pending=int(pending), due=int(due), running=int(running), latest_error=latest_error,
                            oldest_available_at=_source_time(str(oldest), fallback=now) if oldest else None)

    def claim(self, now: datetime, *, owner: str, owner_pid: int | None = None,
              lease: timedelta) -> DispatchEnvelope | None:
        owner_pid = os.getpid() if owner_pid is None else owner_pid
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        eligible, params = self._due(now_text)
        ready = self._ready_at()

        def candidate(db):
            def fetch_page(after, limit):
                keyset, keys = "", []
                if after is not None:
                    if self.kind == "meeting_alignment_jobs":
                        keyset = " and (" + ready + ">datetime(?) or (" + ready + "=datetime(?) and task.id>?))"
                        keys = [after["ready_at"], after["ready_at"], after["id"]]
                    else:
                        keyset, keys = " and task.id>?", [after["id"]]
                elif self.kind != "meeting_alignment_jobs":
                    # The source adapters start their public ID keyset at zero.
                    keyset, keys = " and task.id>?", [0]
                return db.execute(
                    "select task.*," + ready + " as ready_at "
                    f"from ({self.source}) task where "
                    + eligible + keyset + " order by " + ready + ",task.id limit ?",
                    (*params, *keys, limit)).fetchall()
            return _scan_claimable_candidate(fetch_page, now=now_text, owner_alive=self.owner_alive)

        with self.store._connect() as db:
            if candidate(db) is None:
                return None
        with claim_connection(self.store, immediate=True) as db:
            if db is None:
                return None
            row = candidate(db)
            if row is None:
                return None
            source_id = str(row["id"])
            changes = {"status": "'processing'", "updated_at": "?"}
            bindings = [now_text]
            structural = {}
            if self.kind != "okr_review_requests":
                changes["attempts"] = task20_column(self.kind, "attempts") + "+1"
            if self.kind in ("reply_tasks", "meeting_alignment_jobs"):
                changes["locked_at"] = "?"
                bindings.append(now_text)
            if self.kind in ("reply_tasks", "work_summary_inputs"):
                changes["available_at"] = "''"
            if self.kind in ("work_summary_inputs", "okr_review_requests"):
                changes["error"] = "''"
            if self.kind == "reply_tasks":
                changes["claimed_input_version"] = task20_column(self.kind, "input_version")
                structural["claimed_input_id"] = "current_input_id"
            generation = task_claims.acquire_claim(db, adapter_name=self.name, source_id=source_id,
                owner=owner, owner_pid=owner_pid, lease_expires_at=_lease_expiry(now, lease), now=now_text,
                domain_sql=changes, domain_parameters=tuple(bindings), structural_sql=structural,
                domain_where=(task20_column(self.kind, "status") + "=" + repr(row["status"])
                              + " and " + self._channel_clause("tasks")))
            claimed = db.execute(f"select * from ({self.source}) where id=?", (row["id"],)).fetchone()
        time_field = "eligible_at" if self.kind == "meeting_alignment_jobs" else "created_at"
        return DispatchEnvelope(adapter_name=self.name, source_id=source_id,
            available_at=_source_time(str(claimed[time_field]), fallback=now), priority=0,
            attempt=generation if self.kind == "okr_review_requests" else int(claimed["attempts"]), generation=generation)

    def release(self, envelope: DispatchEnvelope, *, owner: str, now: datetime) -> None:
        _validate_release(self.name, envelope, owner, now)
        now_text = _sqlite_time(now)
        changes = {"status": "'retry'" if self.kind == "meeting_alignment_jobs" else "'pending'",
                   "updated_at": "?"}
        if self.kind != "okr_review_requests":
            changes["attempts"] = "max(" + task20_column(self.kind, "attempts") + "-1,0)"
        if self.kind in ("reply_tasks", "meeting_alignment_jobs"):
            changes["locked_at"] = "null"
        with self.store._immediate_write_transaction() as db:
            task_claims.transition_claim(db, envelope=envelope, owner=owner,
                values={"lease_owner": "", "lease_expires_at": ""},
                history={"owner": "", "lease_expires_at": "", "updated_at": now_text},
                domain_sql=changes, domain_parameters=(now_text,),
                domain_where=task20_column(self.kind, "status") + "='processing'")

    def renew(self, envelope: DispatchEnvelope, *, owner: str, now: datetime, lease: timedelta) -> None:
        _renew_lease(self.store, envelope=envelope, owner=owner, now=now, lease=lease)


class ReplyQueueAdapter(_TaskQueueAdapter):
    name = "reply"

    def _channel_clause(self, alias="task"):
        return f"{alias}.{task20_column('reply_tasks', 'channel') if alias == 'tasks' else 'channel'} in ('dingtalk','wechat')"

    def complete(self, envelope: DispatchEnvelope, *, owner: str, now: datetime) -> None:
        with self.store._immediate_write_transaction() as db:
            _complete_ledger_in_db(db, envelope=envelope, owner=owner, now=_sqlite_time(now))
            self.store._compact_settled_reply_task_input(db, int(envelope.source_id))

    def handle_handler_error(self, envelope: DispatchEnvelope, *, owner: str, now: datetime, error: str) -> None:
        from app.external_retry import retry_delay_seconds
        from app.worker import MAX_REPLY_TASK_ATTEMPTS
        _validate_release(self.name, envelope, owner, now)
        now_text = _sqlite_time(now)
        terminal = envelope.attempt >= MAX_REPLY_TASK_ATTEMPTS
        available_at = '' if terminal else _sqlite_time(
            now + timedelta(seconds=retry_delay_seconds(60, max(envelope.attempt - 1, 0))))
        with self.store._immediate_write_transaction() as db:
            task_claims.transition_claim(db, envelope=envelope, owner=owner,
                values={"lease_owner": "", "lease_expires_at": ""},
                history={"owner": "", "lease_expires_at": "", "updated_at": now_text},
                domain_values={"status": "failed" if terminal else "pending", "locked_at": None,
                               "available_at": available_at, "error": error[:2000], "updated_at": now_text},
                domain_where=task20_column(self.kind, "status") + "='processing'")


class ScheduledExecutionQueueAdapter(ReplyQueueAdapter):
    name = "scheduled_execution"

    def _channel_clause(self, alias="task"):
        return f"{alias}.{task20_column('reply_tasks', 'channel') if alias == 'tasks' else 'channel'}='scheduled'"


class MeetingQueueAdapter(_TaskQueueAdapter):
    name = "meeting"


class OkrReviewQueueAdapter(_TaskQueueAdapter):
    name = "okr_review"


class WorkSummaryQueueAdapter(_TaskQueueAdapter):
    name = "work_summary"


class TaskMemoryWriteQueueAdapter(_LedgerClaimLifecycle):
    """Write a finished task's durable memories as soon as it is queued.

    Derek 2026-09-24: writing memory is automatic system behaviour, not a
    scheduled task. Finishing a task queues the row; the dispatcher claims it
    like any other outbox, and a failed write waits for its backoff.
    """

    name = "task_memory_write"

    def __init__(self, store: AutoReplyStore) -> None:
        self.store = store

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        due_clause = (
            "status='pending' and (available_at='' "
            "or datetime(available_at)<=datetime(?))"
        )
        with self.store._connect() as db:
            pending = db.execute(
                f"select count(*) from {TASK_MEMORY_WRITE_PROJECTION} where status='pending'"
            ).fetchone()[0]
            due = db.execute(
                f"select count(*) from {TASK_MEMORY_WRITE_PROJECTION} where {due_clause}",
                (now_text,),
            ).fetchone()[0]
            running = db.execute(
                f"select count(*) from {TASK_MEMORY_WRITE_PROJECTION} where status='processing'"
            ).fetchone()[0]
            oldest = db.execute(
                f"select min(created_at) from {TASK_MEMORY_WRITE_PROJECTION} where {due_clause}",
                (now_text,),
            ).fetchone()[0]
            latest_error = db.execute(
                f"select error from {TASK_MEMORY_WRITE_PROJECTION} where status='failed' "
                "order by updated_at desc, id desc limit 1"
            ).fetchone()
        return QueueMetrics(
            pending=int(pending), due=int(due), running=int(running),
            oldest_available_at=_source_time(str(oldest), fallback=now) if oldest else None,
            latest_error=str(latest_error[0]) if latest_error else "",
        )

    def claim(
        self, now: datetime, *, owner: str, owner_pid: int | None = None, lease: timedelta
    ) -> DispatchEnvelope | None:
        owner_pid = os.getpid() if owner_pid is None else owner_pid
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        lease_text = _lease_expiry(now, lease)
        candidate_sql = (
            f"select id, attempts from {TASK_MEMORY_WRITE_PROJECTION} "
            "where (status='pending' and (available_at='' "
            "or datetime(available_at)<=datetime(?))) "
            "or (status='processing' and datetime(lease_expires_at)<=datetime(?)) "
            "order by id limit 1"
        )
        with self.store._connect() as db:
            if db.execute(candidate_sql, (now_text, now_text)).fetchone() is None:
                return None
        with claim_connection(self.store, immediate=True) as db:
            if db is None:
                return None
            row = db.execute(candidate_sql, (now_text, now_text)).fetchone()
            if row is None:
                return None
            assignments, set_args = task_delivery_set_parts(
                "task_memory_write_events",
                {"status": "processing", "lease_owner": owner,
                 "lease_expires_at": lease_text, "started_at": now_text},
                update_sql={"updated_at": "current_timestamp"},
            )
            db.execute(
                f"update actions set {assignments} "
                "where record_type='task_memory_write_events' and cast(legacy_key as integer)=?",
                (*set_args, row["id"]),
            )
            generation = _acquire_lease(
                db, adapter_name=self.name, source_id=str(row["id"]), owner=owner,
                owner_pid=owner_pid, lease_expires_at=lease_text, now=now_text,
            )
        return DispatchEnvelope(
            adapter_name=self.name, source_id=str(row["id"]), available_at=now,
            priority=0, attempt=int(row["attempts"]) + 1, generation=generation,
        )

    def release(self, envelope: DispatchEnvelope, *, owner: str, now: datetime) -> None:
        _validate_release(self.name, envelope, owner, now)
        now_text = _sqlite_time(now)
        with self.store._immediate_write_transaction() as db:
            _release_lease(db, envelope=envelope, owner=owner, now=now_text)
            assignments, set_args = task_delivery_set_parts(
                "task_memory_write_events",
                {"status": "pending", "lease_owner": "", "lease_expires_at": ""},
                update_sql={"updated_at": "current_timestamp"},
            )
            changed = db.execute(
                f"update actions set {assignments} "
                "where record_type='task_memory_write_events' and cast(legacy_key as integer)=? "
                "and td_status='processing' and td_lease_owner=?",
                (*set_args, int(envelope.source_id), owner),
            )
            if changed.rowcount != 1:
                raise ValueError("task Memory write claim is no longer owned")

    def renew(
        self, envelope: DispatchEnvelope, *, owner: str, now: datetime, lease: timedelta
    ) -> None:
        _validate_release(self.name, envelope, owner, now)
        now_text = _sqlite_time(now)
        lease_text = _lease_expiry(now, lease)
        with self.store._immediate_write_transaction() as db:
            _renew_lease_in_db(
                db, envelope=envelope, owner=owner, now=now_text, lease_expires_at=lease_text
            )
            assignments, set_args = task_delivery_set_parts(
                "task_memory_write_events", {"lease_expires_at": lease_text},
                update_sql={"updated_at": "current_timestamp"},
            )
            changed = db.execute(
                f"update actions set {assignments} "
                "where record_type='task_memory_write_events' and cast(legacy_key as integer)=? "
                "and td_status='processing' and td_lease_owner=?",
                (*set_args, int(envelope.source_id), owner),
            )
            if changed.rowcount != 1:
                raise ValueError("task Memory write claim is no longer owned")

    def finish(
        self, envelope: DispatchEnvelope, *, owner: str, now: datetime, status: str,
        reason: str = "",
    ) -> None:
        del reason
        event = self.store.get_task_memory_write_event(int(envelope.source_id))
        if event is None or event.status != status:
            raise ValueError("task Memory write status was not persisted")
        _complete_ledger(self.store, envelope=envelope, owner=owner, now=now)


class BusinessTaskTodoSyncOutboxQueueAdapter(_LedgerClaimLifecycle):
    """Dispatch Task-keyed DingTalk effects with the normal receipt lease."""

    name = "business_task_todo_sync_outbox"

    def __init__(self, store: AutoReplyStore, *, owner_alive=_process_is_alive) -> None:
        self.store = store
        self.owner_alive = owner_alive

    def metrics(self, now: datetime) -> QueueMetrics:
        now_text = _sqlite_time(now)
        with self.store._connect() as db:
            pending = db.execute(
                f"select count(*) from {BUSINESS_TASK_TODO_OUTBOX_PROJECTION} "
                "where status='queued' or (status='failed' and attempt_count<3)"
            ).fetchone()[0]
            due = db.execute(
                f"select count(*) from {BUSINESS_TASK_TODO_OUTBOX_PROJECTION} "
                "where status='queued' or (status='failed' and attempt_count<3 "
                "and next_attempt_at<=?)", (now_text,),
            ).fetchone()[0]
            running = db.execute(
                f"select count(*) from {BUSINESS_TASK_TODO_OUTBOX_PROJECTION} where status='running'"
            ).fetchone()[0]
            oldest = db.execute(
                f"select min(created_at) from {BUSINESS_TASK_TODO_OUTBOX_PROJECTION} "
                "where status='queued' or (status='failed' and attempt_count<3 and next_attempt_at<=?)",
                (now_text,),
            ).fetchone()[0]
        return QueueMetrics(
            pending=int(pending), due=int(due), running=int(running),
            oldest_available_at=_source_time(str(oldest), fallback=now) if oldest else None,
            latest_error="",
        )

    def claim(self, now: datetime, *, owner: str, owner_pid: int | None = None, lease: timedelta) -> DispatchEnvelope | None:
        owner_pid = os.getpid() if owner_pid is None else owner_pid
        _validate_claim(owner, lease)
        now_text = _sqlite_time(now)
        lease_text = _lease_expiry(now, lease)
        with self.store._connect() as db:
            needs_write = db.execute(
                f"select 1 from {BUSINESS_TASK_TODO_OUTBOX_PROJECTION} "
                "where status='queued' or (status='failed' and attempt_count<3 "
                "and next_attempt_at<=?) or (status='running' and lease_expires_at<=?) "
                "limit 1", (now_text, now_text),
            ).fetchone()
        if needs_write is None:
            return None
        with claim_connection(self.store, immediate=True) as db:
            if db is None:
                return None
            expired = db.execute(
                f"select id from {BUSINESS_TASK_TODO_OUTBOX_PROJECTION} "
                "where status='running' and lease_expires_at<=?",
                (now_text,),
            ).fetchall()
            for item in expired:
                update_sync_action(
                    db,
                    "business_task_todo_sync_outbox",
                    int(item["id"]),
                    {
                        "status": "unknown",
                        "lease_owner": "",
                        "lease_expires_at": "",
                        "error": "receipt_reconciliation_required",
                        "updated_at": now_text,
                    },
                    where_sql="status='running' and lease_expires_at<=?",
                    parameters=(now_text,),
                )
            row = db.execute(
                f"select * from {BUSINESS_TASK_TODO_OUTBOX_PROJECTION} where (status='queued' or "
                "(status='failed' and attempt_count<3 and next_attempt_at<=?)) order by id limit 1",
                (now_text,),
            ).fetchone()
            if row is None:
                return None
            if update_sync_action(
                db,
                "business_task_todo_sync_outbox",
                int(row["id"]),
                {
                    "status": "running",
                    "lease_owner": owner,
                    "lease_expires_at": lease_text,
                    "attempt_count": int(row["attempt_count"]) + 1,
                    "updated_at": now_text,
                },
                where_sql="status in ('queued','failed')",
            ).rowcount != 1:
                return None
            generation = _acquire_lease(db, adapter_name=self.name, source_id=str(row["id"]), owner=owner, owner_pid=owner_pid, lease_expires_at=lease_text, now=now_text)
        return DispatchEnvelope(adapter_name=self.name, source_id=str(row["id"]), available_at=now, priority=0, attempt=int(row["attempt_count"]) + 1, generation=generation)

    def finish_delivery(self, envelope: DispatchEnvelope, *, owner: str, now: datetime, status: str, receipt_json: str = "{}", error: str = "") -> None:
        now_text = _sqlite_time(now)
        with self.store._immediate_write_transaction() as db:
            _assert_ledger_current_in_db(db, envelope=envelope, owner=owner, now=now_text)
            if status == "failed":
                self.store.retry_business_task_todo_sync_outbox(outbox_id=int(envelope.source_id), owner=owner, error=error, now=now_text, _db=db)
            else:
                self.store.finish_business_task_todo_sync_outbox(outbox_id=int(envelope.source_id), owner=owner, status=status, receipt_json=receipt_json, error=error, _db=db)
            _complete_ledger_in_db(db, envelope=envelope, owner=owner, now=now_text)

    def release(self, envelope: DispatchEnvelope, *, owner: str, now: datetime) -> None:
        _validate_release(self.name, envelope, owner, now)
        now_text = _sqlite_time(now)
        with self.store._immediate_write_transaction() as db:
            _release_lease(db, envelope=envelope, owner=owner, now=now_text)
            row = db.execute(
                f"select attempt_count from {BUSINESS_TASK_TODO_OUTBOX_PROJECTION} where id=?",
                (int(envelope.source_id),),
            ).fetchone()
            if row is None:
                raise ValueError("business Task Todo outbox dispatch claim is no longer owned")
            changed = update_sync_action(
                db,
                "business_task_todo_sync_outbox",
                int(envelope.source_id),
                {
                    "status": "failed" if int(row["attempt_count"]) > 1 else "queued",
                    "attempt_count": max(int(row["attempt_count"]) - 1, 0),
                    "lease_owner": "",
                    "lease_expires_at": "",
                    "updated_at": now_text,
                },
                where_sql="status='running' and lease_owner=?",
                parameters=(owner,),
            )
            if changed.rowcount != 1:
                raise ValueError("business Task Todo outbox dispatch claim is no longer owned")

    def renew(self, envelope: DispatchEnvelope, *, owner: str, now: datetime, lease: timedelta) -> None:
        _validate_release(self.name, envelope, owner, now)
        now_text = _sqlite_time(now)
        lease_text = _lease_expiry(now, lease)
        with self.store._immediate_write_transaction() as db:
            _renew_lease_in_db(db, envelope=envelope, owner=owner, now=now_text, lease_expires_at=lease_text)
            changed = update_sync_action(
                db,
                "business_task_todo_sync_outbox",
                int(envelope.source_id),
                {"lease_expires_at": lease_text, "updated_at": now_text},
                where_sql="status='running' and lease_owner=? and lease_expires_at>?",
                parameters=(owner, now_text),
            )
            if changed.rowcount != 1:
                raise ValueError("business Task Todo outbox dispatch claim is no longer owned")

    def finish(self, envelope: DispatchEnvelope, *, owner: str, now: datetime, status: str, reason: str = "") -> None:
        del reason
        with self.store._connect() as db:
            row = db.execute(
                f"select status from {BUSINESS_TASK_TODO_OUTBOX_PROJECTION} where id=?",
                (int(envelope.source_id),),
            ).fetchone()
        if row is None or str(row["status"]) != status:
            raise ValueError("business Task Todo outbox terminal status was not persisted")
        _complete_ledger(self.store, envelope=envelope, owner=owner, now=now)


def _lease_seconds(lease: timedelta) -> int:
    seconds = int(lease.total_seconds())
    if seconds <= 0:
        raise ValueError("dispatcher lease must be positive")
    return seconds


def _lease_expiry(now: datetime, lease: timedelta) -> str:
    _lease_seconds(lease)
    return _sqlite_time(_ceil_to_whole_second(now + lease))


def _iso_lease_expiry(
    now: datetime,
    lease: timedelta,
    *,
    field: str,
) -> str:
    _lease_seconds(lease)
    ensure_utc_datetime(now, field=field)
    expires_at = (now + lease).astimezone(UTC)
    return _ceil_to_whole_second(expires_at).isoformat(timespec="seconds")


def _ceil_to_whole_second(value: datetime) -> datetime:
    if value.microsecond:
        return value.replace(microsecond=0) + timedelta(seconds=1)
    return value



def _lease_rowid(db: sqlite3.Connection, adapter_name: str, source_id: str) -> int | None:
    row = db.execute(
        f"select rowid from {_LEASE_PROJECTION} where adapter_name=? and source_id=?",
        (adapter_name, source_id),
    ).fetchone()
    return None if row is None else int(row["rowid"])


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
    if adapter_name in DISPATCH_KINDS:
        return task_claims.acquire_claim(db, adapter_name=adapter_name, source_id=source_id,
            owner=owner, owner_pid=owner_pid, lease_expires_at=lease_expires_at, now=now)
    row = db.execute(
        "select owner, generation, lease_expires_at "
        f"from {_LEASE_PROJECTION} where adapter_name=? and source_id=?",
        (adapter_name, source_id),
    ).fetchone()
    if row is not None and str(row["owner"]) and str(row["lease_expires_at"]) > now:
        raise ValueError("dispatcher source lease is still active")
    generation = 1 if row is None else int(row["generation"]) + 1
    upsert_state(
        db, "dispatcher_claim_leases",
        {"adapter_name": adapter_name, "source_id": source_id,
         "owner": owner, "owner_pid": owner_pid, "generation": generation,
         "lease_expires_at": lease_expires_at, "updated_at": now},
        {field: state_json_field(field) for field in (
            "owner", "owner_pid", "generation", "lease_expires_at", "updated_at"
        )} | {"terminal_at": "json_quote('')", "last_error": "json_quote('')"},
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


def _scan_scheduled_claimable_candidate(
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
            if not _scheduled_candidate_is_protected(candidate, now, owner_alive):
                return candidate
        if len(candidates) < _CLAIM_SCAN_PAGE_SIZE:
            return None
        after = candidates[-1]


def _scheduled_candidate_is_protected(row, now: str, owner_alive) -> bool:
    source_owner = str(row["lease_owner"] or "")
    source_active = bool(
        source_owner and str(row["lease_expires_at"] or "") > now
    )
    claim_owner = str(row["claim_owner"] or "")
    if not claim_owner:
        return source_active
    if owner_alive(int(row["claim_owner_pid"] or 0)):
        return True
    return bool(source_active and source_owner != claim_owner)


def _scheduled_claim_owner_is_dead(row, *, now: str, owner_alive) -> bool:
    return bool(
        str(row["claim_owner"] or "")
        and str(row["claim_expires_at"] or "") > now
        and not owner_alive(int(row["claim_owner_pid"] or 0))
    )


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
    if envelope.adapter_name in DISPATCH_KINDS:
        task_claims.transition_claim(db, envelope=envelope, owner=owner,
            values={"lease_owner": "", "lease_expires_at": ""},
            history={"owner": "", "lease_expires_at": "", "updated_at": now})
        return
    changed = update_state(
        db, "dispatcher_claim_leases",
        _lease_rowid(db, envelope.adapter_name, envelope.source_id),
        {"owner": "", "lease_expires_at": "", "updated_at": now},
        where_sql="adapter_name=? and source_id=? and owner=? and generation=?",
        parameters=(envelope.adapter_name, envelope.source_id, owner, envelope.generation),
    )
    if changed != 1:
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
    if envelope.adapter_name in DISPATCH_KINDS:
        task_claims.transition_claim(db, envelope=envelope, owner=owner, now=now,
            values={"lease_expires_at": lease_expires_at},
            history={"lease_expires_at": lease_expires_at, "last_error": "", "updated_at": now})
        return
    changed = update_state(
        db, "dispatcher_claim_leases",
        _lease_rowid(db, envelope.adapter_name, envelope.source_id),
        {"lease_expires_at": lease_expires_at, "last_error": "", "updated_at": now},
        where_sql=(
            "adapter_name=? and source_id=? and owner=? and generation=? "
            "and lease_expires_at>? and terminal_at=''"
        ),
        parameters=(envelope.adapter_name, envelope.source_id, owner,
                    envelope.generation, now),
    )
    if changed != 1:
        raise ValueError("dispatcher source claim is no longer owned")


def _assert_ledger_current(
    store: AutoReplyStore,
    *,
    envelope: DispatchEnvelope,
    owner: str,
    now: datetime,
) -> None:
    with store._connect() as db:
        _assert_ledger_current_in_db(
            db,
            envelope=envelope,
            owner=owner,
            now=_sqlite_time(now),
        )


def _assert_ledger_current_in_db(
    db: sqlite3.Connection,
    *,
    envelope: DispatchEnvelope,
    owner: str,
    now: str,
) -> None:
    if envelope.adapter_name in DISPATCH_KINDS:
        task_claims.assert_claim(db, envelope=envelope, owner=owner, now=now)
        return
    row = db.execute(
        f"select 1 from {_LEASE_PROJECTION} where adapter_name=? "
        "and source_id=? and owner=? and generation=? "
        "and lease_expires_at>? and terminal_at=''",
        (envelope.adapter_name, envelope.source_id, owner,
         envelope.generation, now),
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
        _complete_ledger_in_db(
            db,
            envelope=envelope,
            owner=owner,
            now=now_text,
        )


def _complete_ledger_in_db(
    db: sqlite3.Connection,
    *,
    envelope: DispatchEnvelope,
    owner: str,
    now: str,
) -> None:
    if envelope.adapter_name in DISPATCH_KINDS:
        task_claims.transition_claim(db, envelope=envelope, owner=owner,
            values={"lease_owner": "", "lease_expires_at": ""},
            history={"owner": "", "lease_expires_at": "", "terminal_at": now,
                     "last_error": "", "updated_at": now})
        _prune_completed_claim_history(db)
        return
    changed = update_state(
        db, "dispatcher_claim_leases",
        _lease_rowid(db, envelope.adapter_name, envelope.source_id),
        {"owner": "", "lease_expires_at": "", "terminal_at": now,
         "last_error": "", "updated_at": now},
        where_sql="adapter_name=? and source_id=? and owner=? and generation=?",
        parameters=(envelope.adapter_name, envelope.source_id, owner, envelope.generation),
    )
    if changed != 1:
        raise ValueError("dispatcher source claim is no longer owned")
    _prune_completed_claim_history(db)


def _prune_completed_claim_history(db):
    deleted = db.execute(
        "delete from state where record_type='dispatcher_claim_leases' "
        "and terminal_at<>'' and (terminal_at,cast(legacy_key as integer))<("
        "select terminal_at,cast(legacy_key as integer) from state "
        "where record_type='dispatcher_claim_leases' and terminal_at<>'' "
        "order by terminal_at desc,cast(legacy_key as integer) desc "
        "limit 1 offset 9999) returning adapter_name,source_id,generation"
    ).fetchall()
    task_claims.forget_pruned_claims(db, deleted)


def _let_go_of_lease(
    store: AutoReplyStore,
    *,
    envelope: DispatchEnvelope,
    owner: str,
) -> None:
    """Mark a claim as held by nobody alive, so the queue's own rules reclaim it.

    A handler that ended in an error left its lease owned by the running
    service. Once that lease expired, the live-owner guard kept skipping the
    source, because a live owner might still be working on it -- but this one
    had finished. Task 384735 sat pending for thirty minutes that way, until a
    restart ended the owning process. Fenced on owner and generation, so a
    claim someone else has since taken is untouched.
    """
    with store._immediate_write_transaction() as db:
        if envelope.adapter_name in DISPATCH_KINDS:
            task_claims.recover_claims(db, adapter=envelope.adapter_name,
                source_ids=(envelope.source_id,), values={"owner_pid": 0, "lease_expires_at": ""},
                history={"owner_pid": 0, "lease_expires_at": ""},
                where_sql="lifecycle_lease_owner=? and lifecycle_generation=?",
                parameters=(owner, envelope.generation), repack_history=True)
            return
        update_state(
            db, "dispatcher_claim_leases",
            _lease_rowid(db, envelope.adapter_name, envelope.source_id),
            {"owner_pid": 0, "lease_expires_at": ""},
            where_sql="adapter_name=? and source_id=? and owner=? and generation=?",
            parameters=(envelope.adapter_name, envelope.source_id,
                        owner, envelope.generation),
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
        update_state(
            db, "dispatcher_claim_leases",
            _lease_rowid(db, envelope.adapter_name, envelope.source_id),
            {"last_error": error, "updated_at": _sqlite_time(now)},
        )


def _latest_actionable_error(
    db: sqlite3.Connection,
    *,
    column: str,
    source_projection: str,
    source_params: tuple[object, ...],
    order_by: str = "source_updated_at desc, id desc",
    candidate_is_protected,
    owner_alive,
    now: str,
    source_updated_at_column: str = "updated_at",
    source_fields: tuple[str, ...] = (),
    task_owned: bool = False,
) -> str:
    base_fields = (
        "source.id, "
        f"source.{column} as source_error, "
        f"source.{source_updated_at_column} as source_updated_at, "
        + ("source.claim_owner,source.claim_owner_pid,source.claim_expires_at, " if task_owned else
           "source_claim.owner as claim_owner,source_claim.owner_pid as claim_owner_pid,"
           "source_claim.lease_expires_at as claim_expires_at, ")
        + "source_claim.last_error as claim_last_error, "
        "source_claim.updated_at as claim_updated_at, "
        "source_claim.rowid as claim_rowid"
    )
    source_field_names = tuple(field.rsplit(" as ", maxsplit=1)[1] for field in source_fields)
    candidate_fields = (
        "id, source_error, source_updated_at, claim_owner, claim_owner_pid, "
        "claim_expires_at, claim_last_error, claim_updated_at, claim_rowid"
        + (", " + ", ".join(source_field_names) if source_field_names else "")
    )
    source_rows = db.execute(
        "select "
        + candidate_fields
        + " from (select "
        + base_fields
        + (", " + ", ".join(source_fields) if source_fields else "")
        + " "
        + source_projection
        + ") candidate where trim(source_error)<>'' or trim(claim_last_error)<>'' "
        + f"order by {order_by}",
        source_params,
    ).fetchall()
    actionable_rows = [
        row
        for row in source_rows
        if not candidate_is_protected(row, now=now, owner_alive=owner_alive)
    ]
    claim_errors = [
        row for row in actionable_rows if str(row["claim_last_error"] or "").strip()
    ]
    if claim_errors:
        claim_error = max(
            claim_errors,
            key=lambda row: (str(row["claim_updated_at"] or ""), int(row["claim_rowid"] or 0)),
        )
        return str(claim_error["claim_last_error"])
    for row in actionable_rows:
        if str(row["source_error"] or "").strip():
            return str(row["source_error"])
    return ""


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
