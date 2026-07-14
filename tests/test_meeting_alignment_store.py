import sqlite3

import pytest

from app.meeting_alignment_models import MeetingAlignmentJob, MeetingAlignmentRun
from app.store import AutoReplyStore


def seed_job(
    store: AutoReplyStore,
    *,
    meeting_id: str = "minutes-1",
    status: str = "pending",
    eligible_at: str = "2026-07-14 02:10:00",
) -> int:
    return store.upsert_meeting_alignment_job(
        meeting_id=meeting_id,
        title="上线评审",
        source_json=f'{{"meeting_id":"{meeting_id}"}}',
        participants_json='[{"name":"Derek","user_id":"u-derek"}]',
        ended_at="2026-07-14 02:00:00",
        eligible_at=eligible_at,
        status=status,
    )


def test_meeting_alignment_schema_is_added_to_existing_database(tmp_path):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("create table legacy_data (id integer primary key)")

    store = AutoReplyStore(db_path)

    with store._connect() as db:
        tables = {
            row["name"]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            ).fetchall()
        }
    assert "legacy_data" in tables
    assert {"meeting_alignment_jobs", "meeting_alignment_runs"} <= tables
    assert {
        "idx_meeting_alignment_jobs_claim",
        "idx_meeting_alignment_runs_job",
    } <= indexes


def test_meeting_job_upsert_and_get_return_typed_model(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    job_id = seed_job(store)

    job = store.get_meeting_alignment_job(job_id)
    by_meeting_id = store.get_meeting_alignment_job_by_meeting_id("minutes-1")
    assert isinstance(job, MeetingAlignmentJob)
    assert by_meeting_id == job
    assert job.available_at == ""
    assert job.attempts == 0


def test_meeting_job_upsert_updates_nonterminal_discovery_state(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = seed_job(store, status="waiting")

    same_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1",
        title="上线评审（更新）",
        source_json='{"meeting_id":"minutes-1","version":2}',
        participants_json="[]",
        ended_at="2026-07-14 02:01:00",
        eligible_at="2026-07-14 02:11:00",
        status="pending",
    )

    job = store.get_meeting_alignment_job(job_id)
    assert same_id == job_id
    assert job.title == "上线评审（更新）"
    assert job.status == "pending"
    assert job.eligible_at == "2026-07-14 02:11:00"


@pytest.mark.parametrize("terminal_status", ["no_action", "sent", "failed"])
def test_meeting_job_upsert_does_not_reenter_terminal_state(
    tmp_path, terminal_status
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = seed_job(store)
    store.update_meeting_alignment_job(job_id, status=terminal_status)

    same_id = seed_job(store, status="pending")

    assert same_id == job_id
    assert store.get_meeting_alignment_job(job_id).status == terminal_status


def test_meeting_job_claim_is_exclusive_and_terminal_states_do_not_requeue(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = seed_job(store)

    claimed = store.claim_meeting_alignment_jobs(
        limit=1, now="2026-07-14 02:11:00"
    )
    assert [job.id for job in claimed] == [job_id]
    assert claimed[0].status == "processing"
    assert claimed[0].attempts == 1
    assert claimed[0].locked_at
    assert (
        store.claim_meeting_alignment_jobs(
            limit=1, now="2026-07-14 02:11:00"
        )
        == []
    )

    store.update_meeting_alignment_job(job_id, status="no_action", error="")
    assert (
        store.claim_meeting_alignment_jobs(
            limit=1, now="2026-07-14 02:12:00"
        )
        == []
    )


def test_meeting_job_claim_requires_both_eligibility_and_availability(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    future_eligible = seed_job(
        store,
        meeting_id="future-eligible",
        eligible_at="2026-07-14 02:20:00",
    )
    future_available = seed_job(store, meeting_id="future-available")
    store.schedule_meeting_alignment_job_retry(
        future_available,
        error="temporary failure",
        available_at="2026-07-14 02:30:00",
    )

    assert (
        store.claim_meeting_alignment_jobs(
            limit=5, now="2026-07-14 02:15:00"
        )
        == []
    )
    assert [
        job.id
        for job in store.claim_meeting_alignment_jobs(
            limit=5, now="2026-07-14 02:25:00"
        )
    ] == [future_eligible]
    assert [
        job.id
        for job in store.claim_meeting_alignment_jobs(
            limit=5, now="2026-07-14 02:31:00"
        )
    ] == [future_available]


def test_meeting_job_update_uses_an_explicit_allowlist(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = seed_job(store)

    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        decision_json='{"action":"send"}',
        target_kind="group",
        target_id="cid-1",
        target_title="项目群",
        mentions_json='[{"name":"A"}]',
        final_message="会后对齐",
        send_result_json='{"task_id":"task-1"}',
        error="",
    )

    job = store.get_meeting_alignment_job(job_id)
    assert job.status == "ready_to_send"
    assert job.target_id == "cid-1"
    with pytest.raises(ValueError, match="meeting_id"):
        store.update_meeting_alignment_job(job_id, meeting_id="changed")
    with pytest.raises(ValueError, match="Input should be"):
        store.update_meeting_alignment_job(job_id, status="done")


def test_meeting_job_retry_and_startup_recovery_preserve_attempts(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = seed_job(store, meeting_id="minutes-1")
    second_id = seed_job(store, meeting_id="minutes-2")
    claimed = store.claim_meeting_alignment_jobs(
        limit=2, now="2026-07-14 02:11:00"
    )
    assert [job.id for job in claimed] == [first_id, second_id]

    store.schedule_meeting_alignment_job_retry(
        first_id,
        error="minutes incomplete",
        available_at="2026-07-14 02:20:00",
    )
    reset = store.reset_processing_meeting_alignment_jobs()

    assert [job.id for job in reset] == [second_id]
    retried = store.get_meeting_alignment_job(first_id)
    recovered = store.get_meeting_alignment_job(second_id)
    assert (retried.status, retried.available_at, retried.locked_at) == (
        "retry",
        "2026-07-14 02:20:00",
        None,
    )
    assert (recovered.status, recovered.attempts, recovered.locked_at) == (
        "retry",
        1,
        None,
    )


def test_meeting_agent_runs_are_immutable(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = seed_job(store)
    first_id = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="session-1",
        decision_json="{}",
        audit_summary="首次发送结果不明确",
        status="retry",
        error="send status pending",
    )
    second_id = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="session-2",
        codex_transcript_start_line=10,
        codex_transcript_end_line=25,
        decision_json='{"action":"send"}',
        audit_tool_events_json='[{"tool":"dws"}]',
        audit_summary="发送已确认",
        status="sent",
        error="",
    )

    runs = store.list_meeting_alignment_runs(job_id)
    assert [run.id for run in runs] == [second_id, first_id]
    assert [run.status for run in runs] == ["sent", "retry"]
    assert all(isinstance(run, MeetingAlignmentRun) for run in runs)
    assert runs[0].codex_transcript_start_line == 10
    assert runs[1].codex_transcript_start_line == 0
    assert runs[1].audit_tool_events_json == "[]"
