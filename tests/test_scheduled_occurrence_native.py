from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from time import perf_counter

from app.store import AutoReplyStore
from tests.test_store_task_entity_creator import _target_store


def _scheduled_command(store: AutoReplyStore):
    return store.create_scheduled_task(
        name="Native schedule",
        command="produce-once",
        cron_expression="0 * * * * *",
        timezone_name="UTC",
        enabled=True,
    )


def test_occurrence_crud_and_dispatch_use_native_owner(tmp_path):
    store = _target_store(tmp_path / "native-scheduled.sqlite3")
    task = _scheduled_command(store)
    instant = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)

    run = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=instant, now=instant
    )
    assert store.get_scheduled_task_run(run.id) == run
    assert store.list_scheduled_task_runs(task.id) == (run,)

    claimed = store.claim_scheduled_task_run(
        run.id, owner="worker-a", lease_seconds=30, now=instant
    )
    assert claimed is not None
    linked = store.link_scheduled_task_run_execution(
        run.id,
        owner="worker-a",
        execution_kind="service_command",
        execution_id="produce-once",
        result_summary="produce-once queued=0",
        now=instant + timedelta(seconds=1),
    )
    finished = store.finish_scheduled_task_dispatch(
        run.id, owner="worker-a", status="dispatched", now=instant + timedelta(seconds=2)
    )

    assert linked.execution_id == finished.execution_id == "produce-once"
    with store._connect() as db:
        assert db.execute(
            "select count(*) from schedule_occurrences where id=?", (run.id,)
        ).fetchone()[0] == 1
        assert "scheduled_task_runs" not in {
            row[0] for row in db.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        plans = db.execute(
            "explain query plan select id from schedule_occurrences "
            "where dispatch_status='pending' and scheduled_for<=? "
            "and (lease_owner='' or lease_expires_at<=?) order by scheduled_for,id limit 1",
            (instant.isoformat(), instant.isoformat()),
        ).fetchall()
        assert any("idx_schedule_occurrences_claim" in row[3] for row in plans)
        indexes = {
            row[0] for row in db.execute(
                "select name from sqlite_master where type='index' "
                "and tbl_name='schedule_occurrences'"
            )
        }
        assert {
            "idx_schedule_occurrences_dispatch", "idx_schedule_occurrences_claim",
            "idx_schedule_occurrences_latest", "idx_schedule_occurrences_reply_execution",
            "idx_schedule_occurrences_scheduled_instant",
        } <= indexes

        snapshot_id = db.execute(
            "select snapshot_id from schedule_occurrences where id=?", (run.id,)
        ).fetchone()[0]
        terminal = [
            (task.id, "scheduled",
             (datetime(2020, 1, 1, tzinfo=UTC) + timedelta(seconds=index)).isoformat(),
             "dispatched", "", f"terminal-{index}", "service_command", "produce-once",
             instant.isoformat(), "2020-01-01T00:00:00+00:00", 1,
             "produce-once queued=1", snapshot_id, "", None,
             "2020-01-01T00:00:00+00:00")
            for index in range(8000)
        ]
        db.executemany(
            "insert into schedule_occurrences(scheduled_task_id,trigger_kind,scheduled_for,"
            "dispatch_status,skip_or_error_reason,event_id,execution_kind,execution_id,"
            "dispatched_at,first_scheduled_for,occurrence_count,result_summary,snapshot_id,"
            "lease_owner,lease_expires_at,created_at) "
            "values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            terminal,
        )
        db.execute(
            "insert into schedule_occurrences(scheduled_task_id,trigger_kind,scheduled_for,"
            "dispatch_status,skip_or_error_reason,event_id,execution_kind,execution_id,"
            "dispatched_at,first_scheduled_for,occurrence_count,result_summary,snapshot_id,"
            "lease_owner,lease_expires_at,created_at) "
            "values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task.id, "scheduled", (instant + timedelta(minutes=1)).isoformat(),
             "pending", "", "pending-tail", "", "", None,
             (instant + timedelta(minutes=1)).isoformat(), 1, "", snapshot_id, "", None,
             instant.isoformat()),
        )
        started = perf_counter()
        db.execute(
            "select id from schedule_occurrences where dispatch_status='pending' "
            "and scheduled_for<=? and (lease_owner='' or lease_expires_at<=?) "
            "order by scheduled_for,id limit 1",
            (instant.isoformat(), instant.isoformat()),
        ).fetchone()
        elapsed = perf_counter() - started
        assert elapsed < 0.25


def test_scheduled_occurrence_claims_serialize_on_native_owner(tmp_path):
    store = _target_store(tmp_path / "native-scheduled-claims.sqlite3")
    task = _scheduled_command(store)
    instant = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    run = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=instant, now=instant
    )
    def claim(owner):
        return store.claim_scheduled_task_run(
            run.id, owner=owner, lease_seconds=30, now=instant
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))
    assert sum(result is not None for result in results) == 1
    assert store.get_scheduled_task_run(run.id).lease_owner in {"worker-a", "worker-b"}
