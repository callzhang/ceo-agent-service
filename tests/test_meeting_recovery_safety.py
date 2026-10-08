import json

import pytest

from app.store import AutoReplyStore


def seed_failed_job(store):
    return store.upsert_meeting_alignment_job(
        meeting_id="recording-one", title="Business review",
        source_json='{"source":"original"}', participants_json="[]",
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
            store.record_outbound_postfix_receipt("dingtalk", key, {"openTaskId": "already-dispatched"})
    elif blocker == "locked":
        store.update_meeting_alignment_job(job_id, locked_at="2026-10-01T10:00:00+00:00")
    elif blocker == "live_claim":
        with store._connect() as db:
            db.execute("insert into dispatcher_claim_leases(adapter_name,source_id,owner,lease_expires_at) values ('meeting',?,'worker',datetime('now','+5 minutes'))", (str(job_id),))
    elif blocker == "delivery_claim":
        with store._connect() as db:
            db.execute("insert into meeting_alignment_delivery_claims(job_id,claim_token) values (?, 'active')", (job_id,))
    elif blocker == "running_run":
        store.record_meeting_alignment_run(job_id=job_id, codex_session_id="session", decision_json="{}", audit_summary="", status="running", error="")
    else:
        run_id = store.record_meeting_alignment_run(job_id=job_id, codex_session_id="session", decision_json="{}", audit_summary="", status="running", error="")
        store.claim_runtime_operation_attempt("meeting", str(run_id), "codex_oauth", "codex_cli", "local_oauth", "gpt-5.6-sol", owner="worker", lease_seconds=300)
        store.finish_meeting_alignment_run(run_id, status="failed", error="interrupted")
    before = store.get_meeting_alignment_job(job_id)
    assert store.rerun_meeting_alignment_jobs([job_id]) == []
    assert store.get_meeting_alignment_job(job_id) == before


def test_analysis_rerun_preserves_identity_history_and_ignores_expired_claim(tmp_path):
    store = AutoReplyStore(tmp_path / "store.sqlite3")
    job_id = seed_failed_job(store)
    run_id = store.record_meeting_alignment_run(job_id=job_id, codex_session_id="old-session", decision_json="{}", audit_summary="Read denied", status="failed", error="history read denied")
    with store._connect() as db:
        db.execute("insert into dispatcher_claim_leases(adapter_name,source_id,owner,lease_expires_at) values ('meeting',?,'old-worker',datetime('now','-5 minutes'))", (str(job_id),))
    before = store.get_meeting_alignment_job(job_id)
    assert store.rerun_meeting_alignment_jobs([job_id]) == [job_id]
    after = store.get_meeting_alignment_job(job_id)
    assert (after.id, after.meeting_id, after.source_json) == (before.id, before.meeting_id, before.source_json)
    assert after.status == "retry"
    assert json.loads(after.send_result_json) == {}
    assert store.get_meeting_alignment_run(run_id).status == "failed"
    assert store.rerun_meeting_alignment_jobs([job_id]) == []
