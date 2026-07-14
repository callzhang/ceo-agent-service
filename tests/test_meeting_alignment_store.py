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


def test_activation_baseline_silences_unsent_historical_jobs(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    processing_id = seed_job(store, meeting_id="historical-processing")
    [processing] = store.claim_meeting_alignment_jobs(
        limit=1, now="2026-07-14 02:11:00"
    )
    assert processing.id == processing_id
    failed_id = seed_job(store, meeting_id="historical-failed")
    store.update_meeting_alignment_job(
        failed_id,
        status="failed",
        error='{"kind":"meeting_agent"}',
    )
    current_id = store.upsert_meeting_alignment_job(
        meeting_id="current-meeting",
        title="当前会议",
        source_json='{"meeting_id":"current-meeting"}',
        participants_json="[]",
        ended_at="2026-07-15T02:01:00+08:00",
        eligible_at="2026-07-15T02:11:00+08:00",
        status="pending",
    )

    baselined = store.baseline_meeting_alignment_jobs_before(
        "2026-07-15T02:00:00+08:00"
    )

    assert [job.id for job in baselined] == [processing_id, failed_id]
    for job_id in (processing_id, failed_id):
        job = store.get_meeting_alignment_job(job_id)
        assert job.status == "no_action"
        assert job.locked_at is None
        assert job.error == ""
    assert store.get_meeting_alignment_job(current_id).status == "pending"


def test_replay_reopens_only_unsent_no_action_job(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    replay_id = seed_job(store, meeting_id="replay-me")
    store.update_meeting_alignment_job(
        replay_id,
        status="no_action",
        decision_json='{"action":"no_action"}',
    )
    sent_id = seed_job(store, meeting_id="already-sent")
    store.update_meeting_alignment_job(
        sent_id,
        status="sent",
        send_result_json='{"status":"sent"}',
    )

    reopened = store.reopen_meeting_alignment_job_for_replay(
        replay_id,
        title="回放会议",
        source_json='{"replay":true}',
        participants_json="[]",
        ended_at="2026-07-14T02:00:00+08:00",
        eligible_at="2026-07-14T02:10:00+08:00",
    )

    assert reopened is not None
    assert reopened.status == "pending"
    assert reopened.attempts == 0
    assert reopened.decision_json == "{}"
    assert store.reopen_meeting_alignment_job_for_replay(
        sent_id,
        title="已发送",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-07-14T02:00:00+08:00",
        eligible_at="2026-07-14T02:10:00+08:00",
    ) is None
    assert store.get_meeting_alignment_job(sent_id).status == "sent"


def test_ready_to_send_transition_releases_processing_lock(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = seed_job(store)
    [processing] = store.claim_meeting_alignment_jobs(
        limit=1,
        now="2026-07-14 02:11:00",
    )
    assert processing.locked_at

    store.update_meeting_alignment_job(
        job_id,
        available_at="2026-07-14 03:00:00",
    )

    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        decision_json='{"action":"send"}',
        send_result_json='{"task_id":"task-1","status":"pending"}',
    )

    ready = store.get_meeting_alignment_job(job_id)
    assert ready.status == "ready_to_send"
    assert ready.locked_at is None
    assert ready.attempts == 1
    assert ready.available_at == ""


def test_ready_delivery_claim_is_exclusive_and_recovers_after_crash(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = seed_job(store)
    store.claim_meeting_alignment_jobs(
        limit=1,
        now="2026-07-14 02:11:00",
    )
    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        decision_json='{"action":"send"}',
        send_result_json='{"task_id":"task-1","status":"pending"}',
    )

    first_claim = store.claim_ready_to_send_meeting_alignment_jobs(
        limit=1, now="2026-07-14 02:11:00"
    )
    assert [job.id for job in first_claim] == [job_id]
    assert first_claim[0].locked_at
    assert store.claim_ready_to_send_meeting_alignment_jobs(
        limit=1, now="2026-07-14 02:11:00"
    ) == []
    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        send_result_json='{"task_id":"task-1","status":"checking"}',
    )
    assert store.get_meeting_alignment_job(job_id).locked_at
    assert store.claim_ready_to_send_meeting_alignment_jobs(
        limit=1, now="2026-07-14 02:11:00"
    ) == []

    reset = store.reset_ready_to_send_meeting_alignment_jobs()
    assert [job.id for job in reset] == [job_id]
    recovered = reset[0]
    assert recovered.status == "ready_to_send"
    assert recovered.locked_at is None
    assert recovered.attempts == 1
    assert recovered.decision_json == '{"action":"send"}'
    assert recovered.send_result_json == (
        '{"task_id":"task-1","status":"checking"}'
    )

    second_claim = store.claim_ready_to_send_meeting_alignment_jobs(
        limit=1, now="2026-07-14 02:11:00"
    )
    assert [job.id for job in second_claim] == [job_id]
    assert second_claim[0].locked_at


def test_ready_reconciliation_retry_is_atomic_and_not_claimable_before_due(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = seed_job(store)
    store.claim_meeting_alignment_jobs(
        limit=1,
        now="2026-07-14 02:11:00",
    )
    store.update_meeting_alignment_job(
        job_id,
        status="ready_to_send",
        send_result_json='{"openTaskId":"task-1"}',
        available_at="",
    )
    [claimed] = store.claim_ready_to_send_meeting_alignment_jobs(
        limit=1, now="2026-07-14 02:11:00"
    )

    scheduled = store.schedule_ready_to_send_meeting_alignment_reconciliation(
        claimed.id,
        error='{"kind":"meeting_send_reconcile"}',
        available_at="2026-07-14 02:12:00",
    )

    assert scheduled.status == "ready_to_send"
    assert scheduled.locked_at is None
    assert scheduled.attempts == 2
    assert scheduled.send_result_json == '{"openTaskId":"task-1"}'
    assert store.claim_ready_to_send_meeting_alignment_jobs(
        limit=1, now="2026-07-14 02:11:59"
    ) == []
    due = store.claim_ready_to_send_meeting_alignment_jobs(
        limit=1, now="2026-07-14 02:12:00"
    )
    assert [job.id for job in due] == [job_id]
    assert due[0].attempts == 2


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
