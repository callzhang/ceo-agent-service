from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import sqlite3
from time import perf_counter

import pytest

from app.store import AutoReplyStore
from app.agent_cron.commands import ServiceCommandResult
from app.store import ScheduledTaskRunCreateState
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
            "idx_schedule_occurrences_task_chronology",
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
        db.execute(
            "update schedule_occurrences set execution_kind='reply_task',"
            "execution_id='123' where event_id='terminal-7999'"
        )
        db.execute("analyze schedule_occurrences")
        plan_cases = (
            ("idx_schedule_occurrences_latest|INTEGER PRIMARY KEY",
             "select id from schedule_occurrences where scheduled_task_id=? "
             "order by id desc limit 1", (task.id,)),
            ("idx_schedule_occurrences_latest|INTEGER PRIMARY KEY",
             "select id from schedule_occurrences where scheduled_task_id=? and id<? "
             "order by id desc limit ?", (task.id, 999999, 20)),
            ("idx_schedule_occurrences_reply_execution",
             "select id from schedule_occurrences where execution_kind='reply_task' "
             "and cast(execution_id as integer)=? order by id desc limit 1", (123,)),
            ("idx_schedule_occurrences_scheduled_instant",
             "select id from schedule_occurrences where scheduled_task_id=? "
             "and trigger_kind='scheduled' and scheduled_for=?",
             (task.id, instant.isoformat())),
            ("idx_schedule_occurrences_task_chronology",
             "select id from schedule_occurrences where scheduled_task_id=? "
             "and julianday(scheduled_for)<julianday(?) "
             "order by julianday(scheduled_for) desc,id desc limit 1",
             (task.id, instant.isoformat())),
        )
        for index_name, sql, parameters in plan_cases:
            plan = db.execute("explain query plan " + sql, parameters).fetchall()
            assert any(
                any(expected in row[3] for expected in index_name.split("|"))
                for row in plan
            ), (
                index_name, tuple(tuple(row) for row in plan)
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


def test_empty_occurrence_coalesces_and_covered_instant_is_duplicate(tmp_path):
    store = _target_store(tmp_path / "native-scheduled-coalesce.sqlite3")
    task = _scheduled_command(store)
    first_at = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    second_at = first_at + timedelta(minutes=1)
    first = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=first_at, now=first_at
    )
    # This is the previously persisted historical output grammar. The native
    # command result below supplies typed proof for the new tick.
    with store._immediate_write_transaction() as db:
        db.execute(
            "update schedule_occurrences set dispatch_status='dispatched',"
            "execution_kind='service_command',execution_id='produce-once',"
            "result_summary='produce-once queued=0' where id=?", (first.id,),
        )
    second = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=second_at, now=second_at
    )
    with store._immediate_write_transaction() as db:
        db.execute(
            "update schedule_occurrences set dispatch_status='dispatched',"
            "execution_kind='service_command',execution_id='produce-once',"
            "result_summary='produce-once queued=0' where id=?",
            (second.id,),
        )
    assert store.coalesce_empty_scheduled_occurrence(
        second.id,
        command_result=ServiceCommandResult(
            summary="produce-once queued=0", producer_counts=(("queued", 0),),
            complete=True,
        ),
    )
    aggregate = store.get_scheduled_task_run(second.id)
    assert aggregate.first_scheduled_for == first_at
    assert aggregate.scheduled_for == second_at
    assert aggregate.occurrence_count == 2
    assert store.get_scheduled_task_run(first.id) is None

    covered = store.create_scheduled_task_run_if_current(
        task.id, expected_version=task.version, expected_overlap_run_id=second.id,
        scheduled_for=first_at, now=second_at + timedelta(seconds=1),
    )
    assert covered.state is ScheduledTaskRunCreateState.DUPLICATE
    assert covered.run == aggregate
    assert store.list_scheduled_task_runs(task.id) == (aggregate,)


def test_unknown_or_positive_counts_never_coalesce(tmp_path):
    store = _target_store(tmp_path / "native-scheduled-no-coalesce.sqlite3")
    task = _scheduled_command(store)
    instant = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    prior = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=instant, now=instant
    )
    with store._immediate_write_transaction() as db:
        db.execute(
            "update schedule_occurrences set dispatch_status='dispatched',"
            "execution_kind='service_command',execution_id='produce-once',"
            "result_summary='produce-once queued=0' where id=?", (prior.id,),
        )
    current = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=instant + timedelta(minutes=1),
        now=instant + timedelta(minutes=1),
    )
    assert not store.coalesce_empty_scheduled_occurrence(
        current.id, command_result=ServiceCommandResult(
            summary="produce-once queued=1", producer_counts=(("queued", 1),),
            complete=True,
        ),
    )
    assert not store.coalesce_empty_scheduled_occurrence(
        current.id, command_result=ServiceCommandResult(
            summary="unknown", producer_counts=None, complete=False,
        ),
    )


@pytest.mark.parametrize("reference_target", ("prior", "current"))
def test_meeting_jsonb_occurrence_reference_protects_both_aggregate_ends(
    reference_target, tmp_path,
):
    import json

    store = _target_store(tmp_path / f"native-scheduled-meeting-{reference_target}.sqlite3")
    task = _scheduled_command(store)
    first_at = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    prior = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=first_at, now=first_at,
    )
    current_at = first_at + timedelta(minutes=1)
    current = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=current_at, now=current_at,
    )
    with store._immediate_write_transaction() as db:
        db.execute(
            "update schedule_occurrences set dispatch_status='dispatched',"
            "execution_kind='service_command',execution_id='produce-once',"
            "result_summary='produce-once queued=0' where id in (?,?)",
            (prior.id, current.id),
        )
    referenced = prior.id if reference_target == "prior" else current.id
    store.upsert_meeting_alignment_job(
        meeting_id="referenced-meeting", title="Referenced", participants_json="[]",
        source_json=json.dumps({
            "scheduled_consumer_reference": {"scheduled_task_run_id": referenced},
        }), ended_at="2026-10-23T11:00:00Z", eligible_at="2026-10-23T11:00:00Z",
        status="pending",
    )
    with store._connect() as db:
        assert db.execute(
            "select typeof(parameters_json) from tasks where kind='meeting_alignment'"
        ).fetchone()[0] == "blob"
    assert not store.coalesce_empty_scheduled_occurrence(
        current.id,
        command_result=ServiceCommandResult(
            summary="produce-once queued=0", producer_counts=(("queued", 0),),
            complete=True,
        ),
    )
    assert store.get_scheduled_task_run(prior.id) is not None
    assert store.get_scheduled_task_run(current.id).occurrence_count == 1


def test_occurrence_coalesce_delete_failure_rolls_back_interval_update(tmp_path):
    store = _target_store(tmp_path / "native-scheduled-rollback.sqlite3")
    task = _scheduled_command(store)
    first_at = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    first = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=first_at, now=first_at
    )
    second_at = first_at + timedelta(minutes=1)
    second = store.create_scheduled_task_run(
        task.id, trigger_kind="scheduled", scheduled_for=second_at, now=second_at
    )
    with store._immediate_write_transaction() as db:
        db.execute(
            "update schedule_occurrences set dispatch_status='dispatched',"
            "execution_kind='service_command',execution_id='produce-once',"
            "result_summary='produce-once queued=0' where id in (?,?)",
            (first.id, second.id),
        )
        db.execute(
            "create trigger abort_occurrence_retire before delete on schedule_occurrences "
            f"when old.id={first.id} begin select raise(abort,'retire blocked'); end",
        )
    with pytest.raises(sqlite3.IntegrityError, match="retire blocked"):
        store.coalesce_empty_scheduled_occurrence(
            second.id, command_result=ServiceCommandResult(
                summary="produce-once queued=0", producer_counts=(("queued", 0),),
                complete=True,
            ),
        )
    rows = store.list_scheduled_task_runs(task.id)
    assert [run.id for run in rows] == [first.id, second.id]
    assert rows[1].first_scheduled_for == second_at
    assert rows[1].occurrence_count == 1
