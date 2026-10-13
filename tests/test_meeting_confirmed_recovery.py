import pytest
import json

from app.meeting_alignment import reconcile_confirmed_meeting_delivery
from app.store import AutoReplyStore


def test_recovery_requires_provider_confirmation_and_never_sends(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    # The real job fixture is provided by the existing store helper.
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting", title="Meeting", participants_json="[]",
        source_json="{}", ended_at="2026-10-12 00:00:00",
        eligible_at="2026-10-12 00:10:00", status="pending",
    )
    class Provider:
        def send_message(self, *args, **kwargs):
            pytest.fail("reconciliation must never send")
    report = reconcile_confirmed_meeting_delivery(store, Provider(), job_id)
    assert report["eligible"] is False


@pytest.mark.parametrize("state", ["sent", "ambiguous", "failed"])
def test_only_confirmed_receipt_restores_exact_body_without_new_send(tmp_path, state):
    from test_meeting_alignment_delivery import send_decision
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(meeting_id="meeting", title="Meeting",
        source_json="{}", participants_json="[]", ended_at="2026-10-12 00:00:00",
        eligible_at="2026-10-12 00:10:00", status="failed")
    decision = send_decision(target="direct", mention_names=[])
    decision = decision.model_copy(update={"final_message": "Later revised unsent content"})
    store.update_meeting_alignment_job(job_id, decision_json=decision.model_dump_json(),
        target_kind="direct", target_id="u-a", target_title="A", error="body mismatch")
    key = f"meeting-alignment:{job_id}:meeting"
    prepared = store.prepare_meeting_alignment_message(delivery_key=key,
        body="Original delivered content", original_text="", target_kind="user", target_id="u-a",
        feedback_base_url="")
    receipt = {"result": {"openTaskId": "exact-original-task"}}
    store.record_outbound_postfix_receipt("dingtalk", key, receipt)
    class Provider:
        def verify_message_send_result(self, value):
            assert value == receipt
            return {"state": state}
        def send_message(self, *args, **kwargs):
            pytest.fail("must never resend")
    before = store.get_meeting_alignment_job(job_id)
    assert reconcile_confirmed_meeting_delivery(store, Provider(), job_id)["eligible"] == (state == "sent")
    assert store.get_meeting_alignment_job(job_id) == before
    report = reconcile_confirmed_meeting_delivery(store, Provider(), job_id, apply=True)
    assert report["applied"] == (state == "sent")
    after = store.get_meeting_alignment_job(job_id)
    if state == "sent":
        assert after.status == "ready_to_send"
        result = json.loads(after.send_result_json)
        assert result["message_text"] == prepared.final_body
        assert after.decision_json == before.decision_json
        assert result["send_result"] == receipt
    else:
        assert after == before


def test_restore_cas_rejects_source_change_and_ignores_unrelated_claim(tmp_path):
    from test_meeting_alignment_delivery import send_decision
    from app.meeting_alignment_delivery import MeetingDeliveryResult
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(meeting_id="meeting", title="Meeting",
        source_json='{"event":"original"}', participants_json="[]", ended_at="2026-10-12 00:00:00",
        eligible_at="2026-10-12 00:10:00", status="failed")
    decision = send_decision(target="direct", mention_names=[])
    store.update_meeting_alignment_job(job_id, decision_json=decision.model_dump_json(),
        target_kind="direct", target_id="u-a", target_title="A", error="body mismatch")
    key = f"meeting-alignment:{job_id}:meeting"
    prepared = store.prepare_meeting_alignment_message(delivery_key=key,
        body="Delivered body", original_text="", target_kind="user", target_id="u-a",
        feedback_base_url="")
    receipt = {"result": {"openMessageId": "original-message"}}
    store.record_outbound_postfix_receipt("dingtalk", key, receipt)
    job = store.get_meeting_alignment_job(job_id)
    result_json = MeetingDeliveryResult(target_kind="direct", target_id="u-a", target_title="A",
        resolved_mentions=[], unresolved_mention_names=[], send_result=receipt,
        message_text=prepared.final_body).model_dump_json()
    with store._connect() as db:
        db.execute("update tasks set source_json=? where record_type='meeting_alignment_jobs' and cast(legacy_key as integer)=?",
                   ('{"event":"different"}', job_id))
    assert not store.restore_confirmed_meeting_delivery(job, key=key, prepared=prepared,
        receipt=receipt, result_json=result_json)
    job = store.get_meeting_alignment_job(job_id)
    with store._connect() as db:
        db.execute("insert into dispatcher_claim_leases(adapter_name,source_id,owner,owner_pid,generation,lease_expires_at,terminal_at,last_error,created_at,updated_at) "
                   "values ('scheduled',?,'unrelated',1,1,'2099-01-01','','','2026-10-12','2026-10-12')", (str(job_id),))
    assert store.restore_confirmed_meeting_delivery(job, key=key, prepared=prepared,
        receipt=receipt, result_json=result_json)
