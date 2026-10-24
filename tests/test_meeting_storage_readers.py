"""Meeting readers use the native Task and TaskRun identities."""

from datetime import datetime

import pytest

from tests.test_meeting_task_runtime_native import _AssembledMeetingStore


def _job(store, *, identity: int, status: str = "pending") -> int:
    task_id = store.upsert_meeting_alignment_job(
        meeting_id=f"meeting-{identity}",
        title="Review",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-10-11T00:00:00+00:00",
        eligible_at="2026-10-11T00:00:00+00:00",
        status=status,
    )
    return task_id


def test_meeting_readers_keep_canonical_task_id_across_claim_and_run(tmp_path):
    store = _AssembledMeetingStore(tmp_path / "store.db")
    store.db.execute(
        "insert into tasks(kind,status,parameters_json,created_at,updated_at) "
        "values('reply','pending',jsonb('{}'),'now','now')"
    )
    store.db.commit()

    task_id = _job(store, identity=700)
    assert task_id == 2
    assert store.get_meeting_alignment_job(task_id).id == task_id
    assert store.get_meeting_delivery_key(task_id) == "meeting-alignment:2:meeting-700"

    claimed = store.claim_meeting_alignment_jobs(1, "2026-10-12T00:00:00+00:00")
    assert [job.id for job in claimed] == [task_id]
    assert claimed[0].status == "processing"
    run_id = store.claim_professional_run(task_id)
    store.finish_professional_run(
        run_id, status="ready_to_send", session_id="session-1", transcript_start=3, transcript_end=9
    )
    run = store.get_meeting_alignment_run(run_id)
    assert run is not None
    assert (run.id, run.task_id, run.job_id, run.codex_session_id) == (
        run_id, task_id, None, "session-1"
    )
    assert store.list_meeting_alignment_runs(task_id)[0].id == run_id


def test_stale_delivery_claim_release_preserves_successor_token(tmp_path):
    store = _AssembledMeetingStore(tmp_path / "store.db")
    task_id = _job(store, identity=700)
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
    claim = store.db.execute(
        "select td_claim_token from actions where record_type='meeting_alignment_delivery_claims' "
        "and td_claim_job_id=?", (task_id,),
    ).fetchone()
    assert claim is not None and claim[0] == second.delivery_claim_token
    saved = store.get_meeting_alignment_job(task_id)
    assert saved.locked_at == second.locked_at
    store.release_ready_to_send_meeting_alignment_claims([second])
    assert store.get_meeting_alignment_job(task_id).locked_at is None


def test_group_history_reads_native_target_and_calendar_result(tmp_path):
    store = _AssembledMeetingStore(tmp_path / "store.db")
    task_ids = [_job(store, identity=key) for key in (700, 800)]
    for task_id in task_ids:
        store.update_meeting_alignment_job(
            task_id,
            status="sent",
            target_kind="group",
            target_id="group-1",
            target_title="Team",
            calendar_summary_status="updated",
            calendar_summary_result_json='{"eventId":"calendar-1"}',
            final_message="Decision",
        )

    assert store.recurring_meeting_group_targets("Review") == [("group-1", "Team", 2)]
    jobs = store.list_sent_meeting_alignment_jobs()
    assert [job.id for job in jobs] == task_ids
    assert all(job.calendar_summary_status == "updated" for job in jobs)
    assert all(job.calendar_summary_result_json == '{"eventId":"calendar-1"}' for job in jobs)


def test_meeting_run_parent_is_exact_native_task_and_wrong_kind_is_rejected(tmp_path):
    store = _AssembledMeetingStore(tmp_path / "store.db")
    store.db.execute(
        "insert into tasks(kind,status,parameters_json,created_at,updated_at) "
        "values('reply','pending',jsonb('{}'),'now','now')"
    )
    store.db.commit()
    task_id = _job(store, identity=700)
    store.update_meeting_alignment_job(task_id, status="processing")
    run_id = store.claim_professional_run(task_id)
    store.finish_professional_run(
        run_id, status="ready_to_send", session_id="native-session", transcript_start=2, transcript_end=9
    )
    run = store.get_meeting_alignment_run(run_id)
    assert run is not None and (run.id, run.task_id, run.job_id) == (run_id, task_id, None)
    assert [item.id for item in store.list_meeting_alignment_runs_for_codex_session("native-session")] == [run_id]

    with pytest.raises(ValueError, match="professional Run Task does not exist"):
        store.claim_professional_run(1)


def test_native_run_api_cannot_create_orphan_parent(tmp_path):
    store = _AssembledMeetingStore(tmp_path / "store.db")
    with pytest.raises(ValueError, match="professional Run Task does not exist"):
        store.claim_professional_run(4908)
    assert store.db.execute("select count(*) from runs").fetchone()[0] == 0


def test_meeting_run_entry_points_keep_valid_updated_timestamps(tmp_path):
    store = _AssembledMeetingStore(tmp_path / "store.db")
    task_id = _job(store, identity=700)
    store.update_meeting_alignment_job(task_id, status="processing")
    begun_id = store.claim_professional_run(task_id)
    begun = store.get_meeting_alignment_run(begun_id)
    assert begun is not None and begun.updated_at
    assert datetime.strptime(begun.updated_at, "%Y-%m-%d %H:%M:%S")
    store.finish_professional_run(begun_id, status="retry")

    direct_id = store.create_professional_run(
        task_id, status="ready_to_send", session_id="session-2",
    )
    direct = store.get_meeting_alignment_run(direct_id)
    assert direct is not None and direct.updated_at
    assert datetime.strptime(direct.updated_at, "%Y-%m-%d %H:%M:%S")
