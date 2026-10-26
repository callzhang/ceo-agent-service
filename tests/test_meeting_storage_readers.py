"""Meeting Store readers use native Task and Run identities."""

from app.run_entity_storage import get_run

from datetime import datetime

import pytest

from app.store import AutoReplyStore
from app.action_task_delivery_definitions import task_delivery_projection

CLAIMS_SQL = task_delivery_projection("meeting_alignment_delivery_claims")


def _job(store: AutoReplyStore, *, key: int, status: str = "pending") -> int:
    job_id = store.upsert_meeting_alignment_job(
        meeting_id=f"meeting-{key}", title="Review", source_json="{}",
        participants_json="[]", ended_at="2026-10-11T00:00:00+00:00",
        eligible_at="2026-10-11T00:00:00+00:00", status=status,
    )
    if status == "sent":
        store.update_meeting_alignment_job(job_id, final_message="Decision")
    return job_id


def test_meeting_job_id_controls_claims_and_delivery_fk(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    job_id = _job(store, key=700)

    assert store.get_meeting_alignment_job(job_id).id == job_id
    claimed = store.claim_meeting_alignment_jobs(1, "2026-10-12T00:00:00+00:00")
    assert [job.id for job in claimed] == [job_id]
    store.update_meeting_alignment_job(job_id, status="ready_to_send", final_message="Decision")
    ready = store.claim_ready_to_send_meeting_alignment_job(
        job_id, now="2026-10-12T00:00:00+00:00"
    )
    assert ready is not None and ready.id == job_id and ready.delivery_claim_token
    with store._connect() as db:
        assert db.execute(
            f"select job_id from ({CLAIMS_SQL})"
        ).fetchone()[0] == job_id
    store.release_ready_to_send_meeting_alignment_claims([ready])
    with store._connect() as db:
        assert db.execute(f"select count(*) from ({CLAIMS_SQL})").fetchone()[0] == 0


def test_successor_claim_token_survives_stale_release(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    job_id = _job(store, key=700)
    store.update_meeting_alignment_job(job_id, status="ready_to_send")
    first = store.claim_ready_to_send_meeting_alignment_job(
        job_id, now="2026-10-12T00:00:00+00:00"
    )
    assert first is not None
    assert [job.id for job in store.reset_ready_to_send_meeting_alignment_jobs()] == [job_id]
    second = store.claim_ready_to_send_meeting_alignment_job(
        job_id, now="2026-10-12T00:00:00+00:00"
    )
    assert second is not None and second.delivery_claim_token != first.delivery_claim_token
    store.release_ready_to_send_meeting_alignment_claims([first])
    with store._connect() as db:
        assert db.execute(
            f"select claim_token from ({CLAIMS_SQL}) where job_id=?",
            (job_id,),
        ).fetchone()[0] == second.delivery_claim_token
    assert store.get_meeting_alignment_job(job_id).locked_at == second.locked_at
    store.release_ready_to_send_meeting_alignment_claims([second])
    assert store.get_meeting_alignment_job(job_id).locked_at is None


def test_meeting_run_projects_native_parent(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    job_id = _job(store, key=700)
    store.update_meeting_alignment_job(job_id, status="processing")
    run_id = store.claim_professional_run(job_id)
    store.finish_professional_run(run_id, status="ready_to_send", session_id="session-1")
    with store._connect() as db:
        metadata = get_run(db, run_id)
    assert metadata is not None and metadata["task_id"] == job_id
    assert metadata["session_id"] == "session-1"
    from app.native_standalone import native_run_ref
    with store._connect() as db:
        assert native_run_ref(db, run_id, "meeting_alignment_runs") is None
    assert [run.id for run in store.list_meeting_alignment_runs(job_id)] == [run_id]
    with store._connect() as db:
        plan = db.execute(
            "explain query plan select id from runs where task_id=?", (job_id,),
        ).fetchall()
    assert any("idx_runs_task" in step[3] for step in plan)


def test_group_history_and_calendar_result_keep_job_projection(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    keys = [_job(store, key=key, status="sent") for key in (700, 800)]
    for key in keys:
        store.update_meeting_alignment_job(
            key, target_kind="group", target_id="group-1", target_title="Team",
            calendar_summary_status="updated",
            calendar_summary_result_json='{"eventId":"calendar-1"}',
        )
    assert store.recurring_meeting_group_targets("Review") == [("group-1", "Team", 2)]
    jobs = store.list_sent_meeting_alignment_jobs()
    assert [job.id for job in jobs] == keys
    assert all(job.calendar_summary_status == "updated" for job in jobs)
    assert all(job.calendar_summary_result_json == '{"eventId":"calendar-1"}' for job in jobs)


def test_meeting_runs_are_listed_by_job_and_wrong_type_task_is_excluded(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    wrong_type = store.enqueue_work_summary_input(
        source_type="test", source_ref="decoy", payload_json="{}",
    )
    job_id = _job(store, key=700)
    assert wrong_type != job_id
    sent = store.create_professional_run(
        job_id, status="no_action", session_id="native-session",
        transcript_start=2, transcript_end=9,
    )
    retry = store.create_professional_run(
        job_id, status="retry", session_id="native-session",
        transcript_start=10, transcript_end=12,
    )
    running = store.create_professional_run(job_id, status="running")
    with store._connect() as db:
        assert store._runtime_operation_parent_exists(db, "meeting", str(sent)) is False
        assert store._runtime_operation_parent_exists(db, "meeting", str(running)) is True
    run = store.get_meeting_alignment_run(sent)
    assert run is not None and run.id == sent and run.task_id == job_id
    assert run.codex_transcript_start_line == 2 and run.codex_transcript_end_line == 9
    assert [item.id for item in store.list_meeting_alignment_runs_for_codex_session("native-session")] == [retry, sent]
    runs = store.list_meeting_alignment_runs(job_id)
    assert [item.id for item in runs] == [running, retry, sent]
    assert all(not item.native_available and item.native_reason for item in runs)
    assert store.has_later_meeting_alignment_run(job_id, sent)
    assert store.list_meeting_alignment_runs(wrong_type) == []


def test_new_run_cannot_create_an_orphan_parent(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    with pytest.raises(ValueError, match="requires a Task Agent, Meeting, or OKR Task"):
        store.create_professional_run(4908, session_id="session", status="failed", error="missing parent")
    with store._connect() as db:
        assert db.execute("select count(*) from runs").fetchone()[0] == 0


def test_meeting_run_entry_points_write_updated_at_on_insert(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    job_id = _job(store, key=700)
    store.update_meeting_alignment_job(job_id, status="processing")

    begun_id = store.claim_professional_run(job_id)
    with store._connect() as db:
        begun = db.execute(
            "select updated_at, completed_at as finished_at from runs where id=?", (begun_id,),
        ).fetchone()
    assert begun["updated_at"]
    assert begun["finished_at"] == ""
    store.finish_professional_run(begun_id, status="retry")
    direct_id = store.create_professional_run(job_id, session_id="session-2", status="ready_to_send", error="")
    with store._connect() as db:
        rows = db.execute(
            "select id, created_at, updated_at, completed_at as finished_at "
            "from runs where id in (?, ?) order by id", (begun_id, direct_id),
        ).fetchall()
    assert [row["id"] for row in rows] == [begun_id, direct_id]
    assert all(row["updated_at"] for row in rows)
    assert all(datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S") for row in rows)
    assert rows[1]["updated_at"] == rows[1]["finished_at"]
