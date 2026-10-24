"""Meeting readers preserve canonical Task and Run identities."""

from datetime import datetime

import pytest

from app.action_storage import task_delivery_projection
from app.store import AutoReplyStore


CLAIMS_SQL = task_delivery_projection("meeting_alignment_delivery_claims")


def _job(store: AutoReplyStore, *, key: int, status: str = "pending") -> int:
    task_id = store.upsert_meeting_alignment_job(
        meeting_id=f"meeting-{key}",
        title="Review",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-10-11T00:00:00+00:00",
        eligible_at="2026-10-11T00:00:00+00:00",
        status=status,
    )
    if status == "sent":
        store.update_meeting_alignment_job(task_id, final_message="Decision")
    return task_id


def test_native_task_id_controls_claims_and_delivery_fk(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    task_id = _job(store, key=700)

    assert store.get_meeting_alignment_job(task_id).id == task_id
    claimed = store.claim_meeting_alignment_jobs(1, "2026-10-12T00:00:00+00:00")
    assert [job.id for job in claimed] == [task_id]
    store.update_meeting_alignment_job(task_id, status="ready_to_send", final_message="Decision")
    ready = store.claim_ready_to_send_meeting_alignment_job(
        task_id, now="2026-10-12T00:00:00+00:00"
    )
    assert ready is not None and ready.id == task_id and ready.delivery_claim_token
    with store._connect() as db:
        assert db.execute(f"select job_id from ({CLAIMS_SQL})").fetchone()[0] == task_id
    store.release_ready_to_send_meeting_alignment_claims([ready])
    with store._connect() as db:
        assert db.execute(f"select count(*) from ({CLAIMS_SQL})").fetchone()[0] == 0


def test_successor_claim_token_survives_stale_release(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    task_id = _job(store, key=700)
    store.update_meeting_alignment_job(task_id, status="ready_to_send")
    first = store.claim_ready_to_send_meeting_alignment_job(
        task_id, now="2026-10-12T00:00:00+00:00"
    )
    assert first is not None
    assert [job.id for job in store.reset_ready_to_send_meeting_alignment_jobs()] == [task_id]
    second = store.claim_ready_to_send_meeting_alignment_job(
        task_id, now="2026-10-12T00:00:00+00:00"
    )
    assert second is not None and second.delivery_claim_token != first.delivery_claim_token
    store.release_ready_to_send_meeting_alignment_claims([first])
    with store._connect() as db:
        assert db.execute(
            f"select claim_token from ({CLAIMS_SQL}) where job_id=?", (task_id,)
        ).fetchone()[0] == second.delivery_claim_token
    assert store.get_meeting_alignment_job(task_id).locked_at == second.locked_at
    store.release_ready_to_send_meeting_alignment_claims([second])
    assert store.get_meeting_alignment_job(task_id).locked_at is None


def test_meeting_run_projects_native_task_parent_and_uses_task_index(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    task_id = _job(store, key=700)
    store.update_meeting_alignment_job(task_id, status="processing")
    run_id = store.claim_professional_run(task_id)
    store.finish_professional_run(
        run_id, status="ready_to_send", session_id="session-1", transcript_start=2,
        transcript_end=9,
    )
    run = store.get_meeting_alignment_run(run_id)
    assert run is not None
    assert (run.id, run.task_id, run.job_id, run.codex_session_id) == (
        run_id, task_id, None, "session-1",
    )
    assert run.codex_transcript_start_line == 2 and run.codex_transcript_end_line == 9
    assert [item.id for item in store.list_meeting_alignment_runs(task_id)] == [run_id]
    with store._connect() as db:
        plan = db.execute(
            "explain query plan select id from runs where task_id=? order by id desc",
            (task_id,),
        ).fetchall()
    assert any("idx_runs_task" in step[3] for step in plan)


def test_group_history_and_calendar_result_keep_native_task_projection(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    task_ids = []
    for key in (700, 800):
        task_id = _job(store, key=key, status="sent")
        task_ids.append(task_id)
        store.update_meeting_alignment_job(
            task_id, target_kind="group", target_id="group-1", target_title="Team",
            calendar_summary_status="updated",
            calendar_summary_result_json='{"eventId":"calendar-1"}',
        )
    assert store.recurring_meeting_group_targets("Review") == [("group-1", "Team", 2)]
    jobs = store.list_sent_meeting_alignment_jobs()
    assert [job.id for job in jobs] == task_ids
    assert all(job.calendar_summary_status == "updated" for job in jobs)
    assert all(job.calendar_summary_result_json == '{"eventId":"calendar-1"}' for job in jobs)


def test_other_task_kind_is_not_listed_as_meeting_run(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    other_task_id = store.enqueue_work_summary_input("local_file", "decoy", "{}")
    meeting_task_id = _job(store, key=700)
    store.update_meeting_alignment_job(meeting_task_id, status="processing")
    run_id = store.create_professional_run(
        meeting_task_id, status="ready_to_send", session_id="native-session",
        transcript_start=2, transcript_end=9,
    )

    run = store.get_meeting_alignment_run(run_id)
    assert run is not None and run.task_id == meeting_task_id and run.job_id is None
    assert run.task_id != other_task_id
    assert store.list_meeting_alignment_runs(other_task_id) == []
    assert [item.id for item in store.list_meeting_alignment_runs(meeting_task_id)] == [run_id]
    assert store.list_meeting_alignment_runs_for_codex_session("native-session")[0].id == run_id


def test_professional_run_cannot_be_created_without_a_parent_task(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    with pytest.raises(ValueError, match="professional Run requires"):
        store.create_professional_run(4908, status="failed", error="missing parent")
    with store._connect() as db:
        assert db.execute("select count(*) from runs").fetchone()[0] == 0


def test_professional_run_entry_points_write_updated_at_on_insert(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    task_id = _job(store, key=700)
    store.update_meeting_alignment_job(task_id, status="processing")

    begun_id = store.claim_professional_run(task_id)
    with store._connect() as db:
        begun = db.execute(
            "select updated_at,completed_at from runs where id=?", (begun_id,)
        ).fetchone()
    assert begun["updated_at"]
    assert begun["completed_at"] == ""
    store.finish_professional_run(begun_id, status="retry")
    direct_id = store.create_professional_run(
        task_id, status="ready_to_send", session_id="session-2",
        transcript_start=10, transcript_end=25,
    )
    with store._connect() as db:
        rows = db.execute(
            "select id,created_at,updated_at,completed_at from runs where id in (?, ?) order by id",
            (begun_id, direct_id),
        ).fetchall()
    assert [row["id"] for row in rows] == [begun_id, direct_id]
    assert all(row["updated_at"] for row in rows)
    assert all(datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S") for row in rows)
    assert rows[1]["updated_at"] == rows[1]["completed_at"]
