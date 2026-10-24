"""Native OKR analysis history maintenance preserves source terminal rules."""

from contextlib import contextmanager
from datetime import datetime
import sqlite3
from types import SimpleNamespace

from app.store import AutoReplyStore
from app.task_run_entity_definitions import task_run_entity_objects
from app.task_entity_storage import create_task


def _native_store(tmp_path):
    db = sqlite3.connect(tmp_path / "native-weekly.sqlite3")
    db.row_factory = sqlite3.Row
    db.execute("pragma foreign_keys=on")
    for ddl in task_run_entity_objects().values():
        db.execute(ddl)
    from app.run_entity_definitions import run_entity_objects
    run_objects = run_entity_objects()
    for name in ("runs", "run_provenance", "runtime_attempts"):
        db.execute(run_objects[name])

    @contextmanager
    def write_transaction(now):
        text = now if isinstance(now, str) else now.isoformat(sep=" ")
        value = datetime.fromisoformat(text)
        try:
            yield db, (value, text)
            db.commit()
        except BaseException:
            db.rollback()
            raise

    return SimpleNamespace(_agent_run_write_transaction=write_transaction), db


def _task(db, *, week, manager="manager", digest="a" * 64, status="running",
          error="", lease_owner="", lease_expires_at="", created_at="2026-10-01"):
    return create_task(db, values={
        "kind": "okr_analysis", "status": status, "error": error,
        "lease_owner": lease_owner, "lease_expires_at": lease_expires_at,
        "created_at": created_at, "updated_at": created_at,
    }, parameters={"week_end": week, "manager_user_id": manager, "source_digest": digest})["id"]


def test_native_weekly_history_cleanup_preserves_expiry_supersession_and_result_clock(tmp_path):
    store, db = _native_store(tmp_path)
    expired = _task(db, week="2026-09-06", lease_owner="old", lease_expires_at="2026-10-01 00:00:00")
    active = _task(db, week="2026-09-06", digest="b" * 64, lease_owner="live", lease_expires_at="2026-10-23 11:00:00")
    no_later = _task(db, week="2026-09-13", digest="c" * 64, lease_owner="old", lease_expires_at="2026-10-01 00:00:00")
    replacement = _task(db, week="2026-09-13", manager="manager", status="completed", created_at="2026-10-03")
    assert AutoReplyStore.complete_superseded_stale_weekly_okr_analysis_jobs(
        store, now="2026-10-23 10:00:00"
    ) == 1

    rows = {row["id"]: row for row in db.execute(
        "select id,status,error,lease_owner,lease_expires_at from tasks where id in (?,?,?,?)",
        (expired, active, no_later, replacement),
    )}
    assert tuple(rows[expired][field] for field in ("status", "error", "lease_owner", "lease_expires_at")) == (
        "completed", "superseded_by_later_completed_week", "", ""
    )
    assert rows[active]["status"] == "running"
    assert rows[no_later]["status"] == "running"
    assert rows[replacement]["status"] == "completed"
    assert tuple(db.execute(
        "select result_kind,finished_at,result_json from task_results where task_id=?", (expired,)
    ).fetchone()) == ("okr_analysis", "2026-10-23 10:00:00", None)
    assert AutoReplyStore.complete_superseded_stale_weekly_okr_analysis_jobs(
        store, now="2026-10-23 10:01:00"
    ) == 0


def test_native_weekly_failed_cleanup_keeps_business_failures_and_uses_created_clock(tmp_path):
    store, db = _native_store(tmp_path)
    technical = _task(db, week="2026-09-08", error="runtime_route_unavailable", status="failed", created_at="2026-09-08 04:25:00")
    business = _task(db, week="2026-09-08", manager="other", error="analysis_rejected", status="failed", created_at="2026-09-08 04:25:00")
    shipped = _task(db, week="2026-09-06", status="completed", created_at="2026-09-08 05:12:00")
    assert AutoReplyStore.complete_superseded_failed_weekly_okr_analysis_jobs(
        store, now="2026-10-23 10:00:00"
    ) == 1
    assert tuple(db.execute("select status,error from tasks where id=?", (technical,)).fetchone()) == (
        "completed", "superseded_by_later_completed_week"
    )
    assert tuple(db.execute("select status,error from tasks where id=?", (business,)).fetchone()) == (
        "failed", "analysis_rejected"
    )
    assert db.execute("select status from tasks where id=?", (shipped,)).fetchone()[0] == "completed"
    assert tuple(db.execute(
        "select result_kind,finished_at,result_json from task_results where task_id=?", (technical,)
    ).fetchone()) == ("okr_analysis", "2026-10-23 10:00:00", None)


def test_dedicated_weekly_analysis_writers_are_retired():
    for name in (
        "begin_weekly_okr_analysis_job",
        "finish_weekly_okr_analysis_job",
        "reclaim_weekly_okr_analysis_job_cache_miss",
    ):
        assert not hasattr(AutoReplyStore, name)


def test_native_weekly_operation_parent_uses_task_id_and_task_lease_owner(tmp_path):
    store, db = _native_store(tmp_path)
    task_id = _task(
        db, week="2026-10-11", manager="manager", digest="d" * 64,
        lease_owner="task-owner", lease_expires_at="2026-10-23 11:00:00",
    )
    natural_key = f"2026-10-11:manager:{'d' * 64}"
    assert AutoReplyStore._runtime_operation_parent_exists(db, "weekly_okr", natural_key)
    assert AutoReplyStore._runtime_operation_parent_exists(
        db, "weekly_okr", f"{natural_key}:{task_id}:task-owner"
    )
    assert not AutoReplyStore._runtime_operation_parent_exists(
        db, "weekly_okr", f"{natural_key}:{task_id + 1}:task-owner"
    )
    assert not AutoReplyStore._runtime_operation_parent_exists(
        db, "weekly_okr", f"{natural_key}:{task_id}:other-owner"
    )


def _failed_weekly_attempt(db, *, workload_key, failure_code="runtime_lease_expired", owner="worker"):
    return db.execute("""
        insert into runtime_attempts(
            workload_kind,workload_key,attempt_number,route_name,runtime_kind,
            credential_mode,model,session_mode,source_session_id,attempt_purpose,
            validation_retry_policy_id,validation_result_schema_id,session_id,
            transcript_reference,transcript_start,transcript_end,status,failure_class,
            failure_code,failover_permitted,lease_owner,lease_expires_at,
            first_effect_started_at,result_schema_id,started_at,finished_at,created_at,updated_at
        ) values ('weekly_okr',?,1,'route','codex_cli','oauth','model','fresh','','normal',
                  '','','','',0,0,'failed','process',?,1,?,'','','',
                  '2026-10-23 09:00:00','2026-10-23 09:01:00',
                  '2026-10-23 09:00:00','2026-10-23 09:01:00') returning id
        """, (workload_key, failure_code, owner)).fetchone()[0]


def test_native_weekly_runtime_recovery_requires_expired_task_and_matching_failed_attempt(tmp_path):
    store, db = _native_store(tmp_path)
    expired = _task(
        db, week="2026-10-11", digest="e" * 64, status="running",
        lease_owner="crashed", lease_expires_at="2026-10-23 09:00:00",
    )
    active = _task(
        db, week="2026-10-11", digest="f" * 64, status="running",
        lease_owner="live", lease_expires_at="2026-10-23 11:00:00",
    )
    unmatched = _task(
        db, week="2026-10-11", digest="0" * 64, status="running",
        lease_owner="crashed", lease_expires_at="2026-10-23 09:00:00",
    )
    _failed_weekly_attempt(
        db, workload_key=f"2026-10-11:manager:{'e' * 64}:{expired}:crashed"
    )
    _failed_weekly_attempt(
        db, workload_key=f"2026-10-11:manager:{'f' * 64}:{active}:live"
    )
    _failed_weekly_attempt(
        db, workload_key=f"2026-10-11:manager:{'0' * 64}:{unmatched}:crashed",
        failure_code="runtime_route_unavailable",
    )

    assert AutoReplyStore.recover_stale_runtime_attempts(
        store, stale_after_seconds=60, now="2026-10-23 10:00:00"
    ) == 1

    assert tuple(db.execute(
        "select status,error,lease_owner,lease_expires_at from tasks where id=?", (expired,)
    ).fetchone()) == ("failed", "runtime_lease_expired", "", "")
    assert tuple(db.execute(
        "select status,error,lease_owner,lease_expires_at from tasks where id=?", (active,)
    ).fetchone()) == ("running", "", "live", "2026-10-23 11:00:00")
    assert tuple(db.execute(
        "select status,error from tasks where id=?", (unmatched,)
    ).fetchone()) == ("running", "")
    assert tuple(db.execute(
        "select result_kind,finished_at,result_json from task_results where task_id=?", (expired,)
    ).fetchone()) == ("okr_analysis", "2026-10-23 10:00:00", None)
