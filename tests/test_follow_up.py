import json

import pytest

from app.dws_client import DwsUserProfile
from app.follow_up import FollowUpNotSendable, send_business_task_follow_up
from app.store import AutoReplyStore


class FakeDws:
    def __init__(self):
        self.sent = []

    def send_message(
        self,
        conversation_id,
        text,
        at_users=None,
        at_open_dingtalk_ids=None,
        at_open_dingtalk_names=None,
        title=None,
        user_id=None,
        open_dingtalk_id=None,
        idempotency_uuid=None,
    ):
        self.sent.append(
            {
                "conversation_id": conversation_id,
                "text": text,
                "at_users": at_users or [],
                "at_open_dingtalk_ids": at_open_dingtalk_ids or [],
                "at_open_dingtalk_names": at_open_dingtalk_names or [],
                "title": title,
                "user_id": user_id,
                "open_dingtalk_id": open_dingtalk_id,
                "idempotency_uuid": idempotency_uuid,
            }
        )
        return {"ok": True}

    def get_user_profile(self, user_id):
        return DwsUserProfile(
            user_id=user_id,
            name={"owner-1": "Alex"}.get(user_id, user_id),
            open_dingtalk_id=f"open-{user_id}",
        )


def _business_follow_up(store: AutoReplyStore, *, title: str, source_ref: str) -> tuple[int, int]:
    task_id = store.create_business_task(
        title=title, stage="formal", formal_basis="explicit_assignment",
        owner_user_id="owner-1", owner_name="Alex",
    )
    with store.business_task_transaction() as db:
        signal_id = store.create_business_task_signal_in_transaction(
            source_type="reply_attempt", source_ref=source_ref,
            evidence_text=f"Alex owns {title}", dedupe_key=source_ref,
            conversation_id="cid-1", _db=db,
        )
        store.link_business_task_evidence_in_transaction(
            task_id=task_id, signal_id=signal_id, evidence_role="assignment", _db=db,
        )
        draft_id = store.create_business_task_follow_up(
            business_task_id=task_id, source_signal_id=signal_id,
            target_conversation_id="cid-1", target_kind="group",
            question_text=f"请确认{title}进展", scheduled_at="2026-06-27 09:00:00",
            owner_user_id="owner-1", owner_name="Alex",
            dedupe_key=f"follow-up:{task_id}:{signal_id}", _db=db,
        )
    return task_id, draft_id


def test_business_task_follow_up_sends_to_exact_source_conversation_with_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id, draft_id = _business_follow_up(store, title="客户验收", source_ref="message:1")
    dws = FakeDws()

    status, result_json = send_business_task_follow_up(
        store, dws, follow_up_id=draft_id, expected_revision=1,
        now="2026-06-29 01:00:00",
    )

    assert status == "sent"
    assert json.loads(result_json)["send_result"] == {"ok": True}
    assert dws.sent[0]["conversation_id"] == "cid-1"
    assert dws.sent[0]["at_users"] == ["owner-1"]
    [draft] = store.list_business_task_follow_ups(business_task_id=task_id)
    assert draft["id"] == draft_id and draft["status"] == "sent"
    assert json.loads(draft["send_result_json"])["send_result"] == {"ok": True}
    assert store.list_business_task_follow_up_send_attempts(draft_id=draft_id)[0]["state"] == "sent"


def test_clicked_business_task_follow_up_sends_outside_working_hours(tmp_path):
    # Derek, 2026-09-25: 催办 is a button; the click is the decision, so no
    # working-hours gate and no Agent review stand in front of it.
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id, draft_id = _business_follow_up(store, title="客户验收", source_ref="message:1")
    dws = FakeDws()

    status, _ = send_business_task_follow_up(
        store, dws, follow_up_id=draft_id, expected_revision=1,
        now="2026-06-28 16:00:00",  # Monday 00:00 in Shanghai, outside working hours
    )

    assert status == "sent"
    assert dws.sent[0]["conversation_id"] == "cid-1"
    [draft] = store.list_business_task_follow_ups(business_task_id=task_id)
    assert draft["status"] == "sent"
    with pytest.raises(FollowUpNotSendable):
        send_business_task_follow_up(
            store, dws, follow_up_id=draft_id, expected_revision=1,
            now="2026-06-28 16:01:00",
        )
    assert len(dws.sent) == 1


def test_clicked_follow_up_failure_stays_failed_and_resends_as_new_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id, draft_id = _business_follow_up(store, title="客户验收", source_ref="message:1")

    class RejectingDws(FakeDws):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            return {"success": False, "errorMsg": "rejected"}

    status, _ = send_business_task_follow_up(
        store, RejectingDws(), follow_up_id=draft_id, expected_revision=1,
        now="2026-06-29 01:00:00",
    )
    assert status == "failed"
    [draft] = store.list_business_task_follow_ups(business_task_id=task_id)
    assert draft["status"] == "failed"
    with store._connect() as db:
        assert db.execute(
            "select count(*) from work_summary_inputs where source_type='follow_up_completion_check'"
        ).fetchone()[0] == 0

    dws = FakeDws()
    status, _ = send_business_task_follow_up(
        store, dws, follow_up_id=draft_id, expected_revision=1,
        now="2026-06-29 01:05:00",
    )

    assert status == "sent"
    [draft] = store.list_business_task_follow_ups(business_task_id=task_id)
    assert draft["status"] == "sent" and draft["revision"] == 2
    attempts = store.list_business_task_follow_up_send_attempts(draft_id=draft_id)
    assert [(a["draft_revision"], a["state"]) for a in attempts] == [(1, "failed"), (2, "sent")]


def test_new_information_withdraws_pending_follow_ups_except_its_own(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id, old_id = _business_follow_up(store, title="客户验收", source_ref="message:1")
    [evidence] = store.list_business_task_evidence(task_id)
    with store.business_task_transaction() as db:
        new_signal = store.create_business_task_signal_in_transaction(
            source_type="reply_attempt", source_ref="message:2",
            evidence_text="Alex 说下周交付", dedupe_key="message:2",
            conversation_id="cid-1", _db=db,
        )
        store.link_business_task_evidence_in_transaction(
            task_id=task_id, signal_id=new_signal, evidence_role="correction", _db=db,
        )
        new_id = store.create_business_task_follow_up(
            business_task_id=task_id, source_signal_id=new_signal,
            target_conversation_id="cid-1", target_kind="group",
            question_text="请确认下周交付安排", scheduled_at="2026-07-06 09:00:00",
            owner_user_id="owner-1", owner_name="Alex", dedupe_key="follow-up:new", _db=db,
        )
        cancelled = store.cancel_pending_business_task_follow_ups(
            business_task_id=task_id, keep_source_signal_id=new_signal,
            reason="Task 已被新信息更新", _db=db,
        )

    assert evidence.signal_id != new_signal
    assert cancelled == 1
    rows = {row["id"]: row for row in store.list_business_task_follow_ups(business_task_id=task_id)}
    assert rows[old_id]["status"] == "cancelled"
    assert rows[old_id]["suppressed_reason"] == "Task 已被新信息更新"
    assert rows[new_id]["status"] == "draft"
    with pytest.raises(FollowUpNotSendable):
        send_business_task_follow_up(
            store, FakeDws(), follow_up_id=old_id, expected_revision=1,
            now="2026-06-29 01:00:00",
        )


def test_business_task_follow_up_rejects_unparseable_check_time(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id, _ = _business_follow_up(store, title="客户验收", source_ref="message:1")
    [signal] = store.list_business_task_evidence(task_id)
    with pytest.raises(ValueError, match="parseable"):
        store.create_business_task_follow_up(
            business_task_id=task_id, source_signal_id=signal.signal_id,
            target_conversation_id="cid-1", target_kind="group",
            question_text="请确认进展", scheduled_at="next week",
            owner_user_id="owner-1", owner_name="Alex", dedupe_key="bad-schedule",
        )


def test_completed_sibling_does_not_close_business_task_follow_up(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    first, first_draft = _business_follow_up(store, title="客户验收", source_ref="message:1")
    second, second_draft = _business_follow_up(store, title="客户合同", source_ref="message:2")
    store.create_business_task_dingtalk_link(
        business_task_id=first, dingtalk_task_id="dt-1", status="active"
    )
    from app.todo_completion import complete_business_task_from_external_todo
    assert complete_business_task_from_external_todo(
        store, business_task_id=first,
        evidence={"source": "dingtalk_todo:dt-1", "reason": "done"},
    )
    assert store.list_business_task_follow_ups(business_task_id=first)[0]["status"] == "completed"
    assert store.list_business_task_follow_ups(business_task_id=second)[0]["status"] == "draft"
    assert first_draft != second_draft


def test_business_task_follow_up_uncertain_send_does_not_replay(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id, draft_id = _business_follow_up(store, title="客户验收", source_ref="message:1")

    class UncertainDws(FakeDws):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            raise RuntimeError("connection lost after send")

    dws = UncertainDws()
    status, _ = send_business_task_follow_up(
        store, dws, follow_up_id=draft_id, expected_revision=1,
        now="2026-06-29 01:00:00",
    )

    assert status == "unknown"
    assert len(dws.sent) == 1
    assert store.list_business_task_follow_up_send_attempts(draft_id=draft_id)[0]["state"] == "unknown"
    assert store.list_business_task_follow_ups(business_task_id=task_id)[0]["status"] == "failed"
    with store._connect() as db:
        assert db.execute(
            "select count(*) from work_summary_inputs where source_type='follow_up_completion_check'"
        ).fetchone()[0] == 0


def test_expired_presend_business_follow_up_claim_can_be_reclaimed(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    _, draft_id = _business_follow_up(store, title="客户验收", source_ref="message:1")
    first = store.claim_business_task_follow_up_for_send(
        follow_up_id=draft_id, expected_revision=1, now="2026-06-29 01:00:00",
        claim_token="claim-a", lease_owner="worker-a",
        lease_until="2026-06-29 01:01:00", idempotency_uuid="send-a",
    )
    second = store.claim_business_task_follow_up_for_send(
        follow_up_id=draft_id, expected_revision=1, now="2026-06-29 01:02:00",
        claim_token="claim-b", lease_owner="worker-b",
        lease_until="2026-06-29 01:03:00", idempotency_uuid="send-b",
    )

    assert first["id"] == second["id"] == draft_id
    assert not store.transition_business_task_follow_up_to_sending(
        draft_id=draft_id, revision=1, claim_token="claim-a"
    )
    assert store.transition_business_task_follow_up_to_sending(
        draft_id=draft_id, revision=1, claim_token="claim-b"
    )


def test_expired_sending_follow_up_is_unknown_and_a_new_click_sends_a_new_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id, draft_id = _business_follow_up(store, title="客户验收", source_ref="message:1")
    store.claim_business_task_follow_up_for_send(
        follow_up_id=draft_id, expected_revision=1, now="2026-06-29 01:00:00",
        claim_token="claim-a", lease_owner="worker-a",
        lease_until="2026-06-29 01:01:00", idempotency_uuid="send-a",
    )
    assert store.transition_business_task_follow_up_to_sending(
        draft_id=draft_id, revision=1, claim_token="claim-a"
    )

    store.recover_expired_business_task_follow_up_sends(now="2026-06-29 01:02:00")

    [attempt] = store.list_business_task_follow_up_send_attempts(draft_id=draft_id)
    assert attempt["state"] == "unknown"
    assert store.list_business_task_follow_ups(business_task_id=task_id)[0]["status"] == "failed"
    with store._connect() as db:
        assert db.execute(
            "select count(*) from work_summary_inputs where source_type='follow_up_completion_check'"
        ).fetchone()[0] == 0

    dws = FakeDws()
    status, _ = send_business_task_follow_up(
        store, dws, follow_up_id=draft_id, expected_revision=1,
        now="2026-06-29 01:03:00",
    )

    assert status == "sent"
    assert len(dws.sent) == 1
    attempts = store.list_business_task_follow_up_send_attempts(draft_id=draft_id)
    assert [(a["draft_revision"], a["state"]) for a in attempts] == [(1, "unknown"), (2, "sent")]
