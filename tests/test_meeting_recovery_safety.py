import json

import pytest

from app.store import AutoReplyStore
from app.action_storage import task_delivery_insert_parts
from app.task_entity_storage import update_task_cas


def seed_failed_job(store):
    return store.upsert_meeting_alignment_job(
        meeting_id="recording-one", title="Business review",
        source_json=json.dumps({
            "meeting_id": "recording-one",
            "calendar_evidence": {"source": "calendar"},
            "source": "original",
        }), participants_json="[]",
        ended_at="2026-10-01T10:00:00+00:00",
        eligible_at="2026-10-01T10:00:00+00:00", status="failed",
    )


@pytest.mark.parametrize("blocker", ["partial_effect", "prepared", "receipt", "sensitive_receipt", "locked", "live_claim", "delivery_claim", "running_run", "runtime_owner"])
def test_analysis_rerun_rejects_effects_or_active_ownership(tmp_path, blocker):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = seed_failed_job(store)
    key = f"meeting-alignment:{job_id}:recording-one"
    if blocker == "partial_effect":
        store.update_meeting_alignment_job(job_id, send_result_json='{"group":{"sent":true}}')
    elif blocker in {"prepared", "receipt", "sensitive_receipt"}:
        if blocker == "sensitive_receipt":
            key += ":sensitive"
        store.prepare_outbound_postfix("dingtalk", key, "Body", "Original")
        if blocker != "prepared":
            store.record_outbound_postfix_receipt(
                "dingtalk", key, {"openTaskId": "already-dispatched"},
                operation="meeting_alignment",
            )
    elif blocker == "locked":
        store.update_meeting_alignment_job(job_id, locked_at="2026-10-01T10:00:00+00:00")
    elif blocker == "live_claim":
        with store._immediate_write_transaction() as db:
            update_task_cas(db, task_id=job_id,
                expected={"status": "failed"},
                changes={"lease_owner": "worker",
                         "lease_expires_at": db.execute("select datetime('now','+5 minutes')").fetchone()[0]})
    elif blocker == "delivery_claim":
        with store._immediate_write_transaction() as db:
            columns, parameters = task_delivery_insert_parts(db, "meeting_alignment_delivery_claims", {"job_id": job_id, "claim_token": "active"})
            db.execute("insert into actions(" + ",".join(columns) + ") values(?,?,?,?)", parameters)
    elif blocker == "running_run":
        store.create_professional_run(job_id, status="running", session_id="session", error="")
    else:
        run_id = store.create_professional_run(job_id, status="running", session_id="session", error="")
        store.claim_runtime_operation_attempt("meeting", str(run_id), "codex_oauth", "codex_cli", "local_oauth", "gpt-5.6-sol", owner="worker", lease_seconds=300)
        store.finish_professional_run(run_id, status="failed", error="interrupted")
    before = store.get_meeting_alignment_job(job_id)
    assert store.attach_asr_transcript_and_rerun_meeting_job(job_id, {
        "provider": "ai_services", "model": "qwen3-asr", "audio_sha256": "a" * 64,
        "audio_duration_seconds": 120.0, "completed_chunks": 1, "total_chunks": 1,
        "segments": [{"start_seconds": 1.5, "end_seconds": 4.0, "text": "Reviewed the page."}],
    }) is None
    assert store.rerun_meeting_alignment_jobs([job_id]) == []
    assert store.get_meeting_alignment_job(job_id) == before


def test_analysis_rerun_preserves_identity_history_and_ignores_expired_claim(tmp_path):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = seed_failed_job(store)
    run_id = store.create_professional_run(job_id, status="failed", session_id="old-session", error="history read denied")
    with store._immediate_write_transaction() as db:
        update_task_cas(db, task_id=job_id,
            expected={"status": "failed"},
            changes={"lease_owner": "old-worker",
                     "lease_expires_at": db.execute("select datetime('now','-5 minutes')").fetchone()[0]})
    before = store.get_meeting_alignment_job(job_id)
    assert store.rerun_meeting_alignment_jobs([job_id]) == [job_id]
    after = store.get_meeting_alignment_job(job_id)
    assert (after.id, after.meeting_id, after.source_json) == (before.id, before.meeting_id, before.source_json)
    assert after.status == "retry"
    assert json.loads(after.send_result_json) == {}
    assert store.get_meeting_alignment_run(run_id).status == "failed"
    assert store.rerun_meeting_alignment_jobs([job_id]) == []


def test_complete_asr_transcript_requeues_the_same_failed_unsent_meeting(tmp_path):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    # Occupy another task family's physical identities first: public job ID
    # must remain independent of its consolidated Tasks row ID.
    store.enqueue_reply_task(
        conversation_id="other", conversation_title="Other", single_chat=True,
        trigger_message_id="other-message", trigger_create_time="2026-10-01 09:00:00",
        trigger_sender="Other", trigger_text="Unrelated task",
    )
    job_id = seed_failed_job(store)
    run_id = store.create_professional_run(job_id, status="failed", session_id="old-session", error="complete transcript is unavailable")
    evidence = {
        "provider": "ai_services",
        "model": "qwen3-asr",
        "audio_sha256": "a" * 64,
        "audio_duration_seconds": 120.0,
        "completed_chunks": 1,
        "total_chunks": 1,
        "segments": [
            {"start_seconds": 1.5, "end_seconds": 4.0, "text": "Reviewed the page."}
        ],
    }

    recovered = store.attach_asr_transcript_and_rerun_meeting_job(job_id, evidence)

    job = store.get_meeting_alignment_job(job_id)
    assert recovered == job_id
    assert job.status == "retry"
    assert job.meeting_id == "recording-one"
    assert json.loads(job.source_json)["asr_transcript"] == evidence
    assert job.decision_json == "{}"
    assert job.send_result_json == "{}"
    assert store.get_meeting_alignment_run(run_id).status == "failed"
    assert store.attach_asr_transcript_and_rerun_meeting_job(job_id, evidence) is None


def test_asr_transcript_rerun_preserves_existing_delivery_guards(tmp_path):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = seed_failed_job(store)
    key = f"meeting-alignment:{job_id}:recording-one"
    store.prepare_outbound_postfix("dingtalk", key, "Summary", "Original")
    store.record_outbound_postfix_receipt(
        "dingtalk", key, {"openTaskId": "already-dispatched"}, operation="meeting_alignment"
    )
    before = store.get_meeting_alignment_job(job_id)
    evidence = {
        "provider": "ai_services",
        "model": "qwen3-asr",
        "audio_sha256": "a" * 64,
        "audio_duration_seconds": 120.0,
        "completed_chunks": 1,
        "total_chunks": 1,
        "segments": [
            {"start_seconds": 1.5, "end_seconds": 4.0, "text": "Reviewed the page."}
        ],
    }

    assert store.attach_asr_transcript_and_rerun_meeting_job(job_id, evidence) is None
    assert store.get_meeting_alignment_job(job_id) == before


def test_analysis_rerun_reopens_needs_human_without_touching_its_runs(tmp_path):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = seed_failed_job(store)
    run_id = store.create_professional_run(job_id, status="failed", session_id="s", error="audit")
    with store._immediate_write_transaction() as db:
        update_task_cas(db, task_id=job_id, expected={"status": "failed"},
                        changes={"status": "needs_human"})
    assert store.rerun_meeting_alignment_jobs([job_id]) == [job_id]
    assert store.get_meeting_alignment_job(job_id).status == "retry"
    assert store.get_meeting_alignment_run(run_id).status == "failed"
