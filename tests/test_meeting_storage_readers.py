"""Meeting Store readers preserve public IDs across typed storage."""

from tests.test_email_legacy_schema import published_source as published_source

from datetime import datetime, timezone
import sqlite3

import pytest

from app.store import AutoReplyStore
from app.action_storage import task_delivery_projection
from app.task_storage import task_id_expression
from app.task8_storage import task8_insert_parts
from tests.test_meeting_task_storage import meeting_source
from tests.test_task_storage_readers import original_rows_source13
from tests.workbench_caller_fixtures import advance_source16_to_store17, advance_source17_to_current_store

CLAIMS_SQL = task_delivery_projection("meeting_alignment_delivery_claims")


def _job(store: AutoReplyStore, *, legacy_key: int, status: str = "pending") -> int:
    with store._connect() as db:
        parts = task8_insert_parts("meeting_alignment_jobs", {
            "legacy_key": legacy_key,
            "meeting_id": f"meeting-{legacy_key}",
            "title": "Review",
            "ended_at": "2026-10-11T00:00:00+00:00",
            "eligible_at": "2026-10-11T00:00:00+00:00",
            "status": status,
            "final_message": "Decision" if status == "sent" else "",
        })
        cursor = db.execute(
            f"insert into tasks ({parts.columns}) values ({parts.values})",
            parts.parameters,
        )
        return int(cursor.lastrowid)


def test_meeting_public_job_id_controls_claims_and_delivery_fk(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    physical = _job(store, legacy_key=700)
    assert physical != 700

    assert store.get_meeting_alignment_job(700).id == 700
    claimed = store.claim_meeting_alignment_jobs(1, "2026-10-12T00:00:00+00:00")
    assert [job.id for job in claimed] == [700]
    store.update_meeting_alignment_job(700, status="ready_to_send", final_message="Decision")
    ready = store.claim_ready_to_send_meeting_alignment_job(
        700, now="2026-10-12T00:00:00+00:00"
    )
    assert ready is not None and ready.id == 700 and ready.delivery_claim_token
    with store._connect() as db:
        assert db.execute(
            f"select job_id from ({CLAIMS_SQL})"
        ).fetchone()[0] == physical
    store.release_ready_to_send_meeting_alignment_claims([ready])
    with store._connect() as db:
        assert db.execute(f"select count(*) from ({CLAIMS_SQL})").fetchone()[0] == 0


def test_successor_claim_token_survives_stale_release_with_divergent_id(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    physical = _job(store, legacy_key=700)
    store.update_meeting_alignment_job(700, status="ready_to_send")
    first = store.claim_ready_to_send_meeting_alignment_job(
        700, now="2026-10-12T00:00:00+00:00"
    )
    assert first is not None
    assert [job.id for job in store.reset_ready_to_send_meeting_alignment_jobs()] == [700]
    second = store.claim_ready_to_send_meeting_alignment_job(
        700, now="2026-10-12T00:00:00+00:00"
    )
    assert second is not None and second.delivery_claim_token != first.delivery_claim_token
    store.release_ready_to_send_meeting_alignment_claims([first])
    with store._connect() as db:
        assert db.execute(
            f"select claim_token from ({CLAIMS_SQL}) where job_id=?",
            (physical,),
        ).fetchone()[0] == second.delivery_claim_token
    assert store.get_meeting_alignment_job(700).locked_at == second.locked_at
    store.release_ready_to_send_meeting_alignment_claims([second])
    assert store.get_meeting_alignment_job(700).locked_at is None


def test_meeting_run_projects_public_parent(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    physical = _job(store, legacy_key=700)
    store.update_meeting_alignment_job(700, status="processing")
    run_id = store.claim_logical_run('meeting_alignment_runs', 700)
    store.finish_logical_run('meeting_alignment_runs', run_id, status="ready_to_send", codex_session_id="session-1")
    metadata = store.get_logical_run('meeting_alignment_runs', run_id, load_native=False)
    assert metadata is not None and metadata.job_id == 700
    assert metadata.codex_session_id == "session-1"
    assert metadata.decision_json == "{}" and metadata.audit_tool_events_json == "[]"
    assert [run.id for run in store.list_meeting_alignment_runs(700)] == [run_id]
    with store._connect() as db:
        plan = db.execute(
            "explain query plan select cast(legacy_key as integer) from runs "
            "where record_type='agent_runs' and source_type='meeting_alignment_runs' and task_id="
            + task_id_expression("meeting_alignment_jobs", "?"), (700,),
        ).fetchall()
    assert any("idx_logical_runs_task" in step[3] for step in plan)



def test_group_history_and_calendar_result_keep_public_job_projection(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    for key in (700, 800):
        _job(store, legacy_key=key, status="sent")
        store.update_meeting_alignment_job(
            key, target_kind="group", target_id="group-1", target_title="Team",
            calendar_summary_status="updated",
            calendar_summary_result_json='{"eventId":"calendar-1"}',
        )
    assert store.recurring_meeting_group_targets("Review") == [("group-1", "Team", 2)]
    jobs = store.list_sent_meeting_alignment_jobs()
    assert [job.id for job in jobs] == [700, 800]
    assert all(job.calendar_summary_status == "updated" for job in jobs)
    assert all(job.calendar_summary_result_json == '{"eventId":"calendar-1"}' for job in jobs)


def test_meeting_run_public_id_can_differ_from_physical_and_wrong_type_is_excluded(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    with store._connect() as db:
        parts = task8_insert_parts("work_summary_inputs", {
            "legacy_key": 700, "source_type": "test", "source_ref": "decoy",
            "payload_json": "{}",
        })
        wrong_type = db.execute(
            f"insert into tasks ({parts.columns}) values ({parts.values})",
            parts.parameters,
        ).lastrowid
    physical = _job(store, legacy_key=700)
    assert wrong_type != physical
    with store._connect() as db:
        db.execute("insert into runs(record_type,source_type,legacy_key,task_id,status,session_id,transcript_start,transcript_end,error,completed_at,updated_at) values('agent_runs','meeting_alignment_runs',:legacy_key,:task_id,:status,:session_id,:transcript_start,:transcript_end,'','',current_timestamp)", {
            "legacy_key": 900, "task_id": physical, "status": "sent",
            "session_id": "native-session", "transcript_start": 2, "transcript_end": 9,
        })
        db.execute("insert into runs(record_type,source_type,legacy_key,task_id,status,session_id,transcript_start,transcript_end,error,completed_at,updated_at) values('agent_runs','meeting_alignment_runs',:legacy_key,:task_id,:status,:session_id,:transcript_start,:transcript_end,'','',current_timestamp)", {
            "legacy_key": 901, "task_id": physical, "status": "retry",
            "session_id": "native-session", "transcript_start": 10, "transcript_end": 12,
        })
        db.execute("insert into runs(record_type,source_type,legacy_key,task_id,status,session_id,transcript_start,transcript_end,error,completed_at,updated_at) values('agent_runs','meeting_alignment_runs',:legacy_key,:task_id,:status,:session_id,:transcript_start,:transcript_end,'','',current_timestamp)", {
            "legacy_key": 902, "task_id": physical, "status": "running",
            "session_id": "", "transcript_start": 0, "transcript_end": 0,
        })
        assert store._runtime_operation_parent_exists(db, "meeting", "900") is False
        assert store._runtime_operation_parent_exists(db, "meeting", "902") is True
    run = store.get_logical_run('meeting_alignment_runs', 900, load_native=False)
    assert run is not None and run.id == 900 and run.job_id == 700
    assert run.codex_transcript_start_line == 2 and run.codex_transcript_end_line == 9
    assert [item.id for item in store.list_meeting_alignment_runs_for_codex_session("native-session")] == [901, 900]
    runs = store.list_meeting_alignment_runs(700)
    assert [item.id for item in runs] == [902, 901, 900]
    assert all(not item.native_available and item.native_reason for item in runs)
    assert store.has_later_meeting_alignment_run(700, 900)


def test_historical_orphan_preserves_public_job_id_without_a_ghost_parent(tmp_path, published_source):
    path = tmp_path / "source.db"
    original_rows_source13(path, published_source, meeting_source)
    advance_source16_to_store17(path)
    advance_source17_to_current_store(path, published_source)
    store = AutoReplyStore(path)

    orphan = store.get_logical_run('meeting_alignment_runs', 2298, load_native=False)
    assert orphan is not None and orphan.job_id == 4908
    assert [run.id for run in store.list_meeting_alignment_runs(4908)] == [2298]
    with store._connect() as db:
        assert db.execute(
            "select record_type from tasks where id=4908"
        ).fetchone()[0] == "reply_tasks"


def test_new_run_cannot_create_an_orphan_parent(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    with pytest.raises(ValueError, match="parent does not exist"):
        store.create_logical_run('meeting_alignment_runs', task_public_id=4908, codex_session_id="session", status="failed", error="missing parent")
    with store._connect() as db:
        assert db.execute(
            "select count(*) from runs where record_type='agent_runs' and source_type='meeting_alignment_runs'"
        ).fetchone()[0] == 0


def test_meeting_run_entry_points_write_updated_at_on_insert(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    _job(store, legacy_key=700)
    store.update_meeting_alignment_job(700, status="processing")

    begun_id = store.claim_logical_run('meeting_alignment_runs', 700)
    with store._connect() as db:
        begun = db.execute(
            "select updated_at, completed_at as finished_at from runs "
            "where record_type='agent_runs' and source_type='meeting_alignment_runs' "
            "and cast(legacy_key as integer)=?", (begun_id,),
        ).fetchone()
    assert begun["updated_at"]
    assert begun["finished_at"] == ""
    store.finish_logical_run('meeting_alignment_runs', begun_id, status="retry")
    direct_id = store.create_logical_run('meeting_alignment_runs', task_public_id=700, codex_session_id="session-2", status="ready_to_send", error="")
    with store._connect() as db:
        rows = db.execute(
            "select cast(legacy_key as integer) as id, created_at, updated_at, completed_at as finished_at "
            "from runs where record_type='agent_runs' and source_type='meeting_alignment_runs' "
            "and cast(legacy_key as integer) in (?, ?) order by id",
            (begun_id, direct_id),
        ).fetchall()
    assert [row["id"] for row in rows] == [begun_id, direct_id]
    assert all(row["updated_at"] for row in rows)
    assert all(datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S") for row in rows)
    assert rows[1]["updated_at"] == rows[1]["finished_at"]
