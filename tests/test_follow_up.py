import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.dws_client import DwsUserProfile
from app.follow_up import process_due_follow_ups, resolve_failed_follow_up
from app.store import AutoReplyStore
from app.task_agent import apply_task_agent_decision
from app.task_models import TaskAgentDecision, WorkItem


class FakeDws:
    def __init__(self):
        self.sent = []
        self.todo_payloads = {}

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

    def search_user_profiles(self, query):
        if query == "Jack He(Yunguang He)":
            return [
                DwsUserProfile(
                    user_id="jack-user-1",
                    name="何耘光",
                    nick="Jack He(Yunguang He)",
                    open_dingtalk_id="open-jack-1",
                )
            ]
        return []

    def get_todo_task(self, task_id):
        return self.todo_payloads.get(task_id, {"id": task_id, "done": False})

    def read_direct_messages_since(self, user_id, *, start):
        return {"complete": True, "messages": []}


def _create_bound_todo(
    store: AutoReplyStore,
    project_id: int,
    *,
    owner_user_id: str = "owner-1",
    owner_name: str = "Alex",
) -> int:
    return store.create_work_todo(
        project_id=project_id,
        title="确认当前交付状态",
        owner_user_id=owner_user_id,
        owner_name=owner_name,
        status="open",
        priority="P1",
    )


def test_due_follow_up_sends_group_message(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户交付 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P0",
        next_follow_up_at="2026-06-07 09:00:00",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="这个 P0 事项现在结果、阻塞和 ETA 分别是什么？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] == "cid-1"
    assert dws.sent[0]["at_users"] == ["owner-1"]
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-owner-1"]
    assert dws.sent[0]["at_open_dingtalk_names"] == ["Alex"]
    assert not dws.sent[0]["text"].startswith("<@")
    assert dws.sent[0]["text"].startswith("**请确认：**")
    assert "**事项**" in dws.sent[0]["text"]
    assert "- 项目：客户交付" in dws.sent[0]["text"]
    assert "- 事项：给客户交付 ETA" in dws.sent[0]["text"]
    assert "结果、阻塞和 ETA" in dws.sent[0]["text"]
    sent_draft = store.list_follow_up_drafts(statuses=("sent",))[0]
    assert sent_draft.id == draft_id
    send_result = json.loads(sent_draft.send_result_json)
    assert send_result["at_users"] == ["owner-1"]
    assert send_result["at_open_dingtalk_ids"] == ["open-owner-1"]
    assert send_result["at_open_dingtalk_names"] == ["Alex"]


def test_concurrent_correction_invalidates_unclaimed_send_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )

    class CorrectingDws(FakeDws):
        def get_user_profile(self, user_id):
            store.update_follow_up_draft(
                draft_id,
                question_text="修正后的问题",
                scheduled_at="2026-06-08 01:00:00",
            )
            return super().get_user_profile(user_id)

    first_dws = CorrectingDws()
    assert process_due_follow_ups(
        store,
        first_dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    ) == 0
    assert first_dws.sent == []
    corrected = store.get_follow_up_draft(draft_id)
    assert corrected is not None
    assert corrected.status == "draft"
    assert corrected.question_text == "修正后的问题"
    assert corrected.send_claim_token == ""

    second_dws = FakeDws()
    assert process_due_follow_ups(
        store,
        second_dws,
        now="2026-06-08 02:01:00",
        auto_send=True,
    ) == 1
    assert len(second_dws.sent) == 1
    assert "修正后的问题" in second_dws.sent[0]["text"]
    assert "旧问题" not in second_dws.sent[0]["text"]


def test_correction_invalidates_claim_before_external_send(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    original_claim = store.claim_follow_up_draft_revision

    def claim_then_correct(*args, **kwargs):
        claimed = original_claim(*args, **kwargs)
        assert claimed is True
        store.update_follow_up_draft(
            draft_id,
            question_text="修正后的问题",
            scheduled_at="2026-06-08 01:00:00",
        )
        return claimed

    monkeypatch.setattr(store, "claim_follow_up_draft_revision", claim_then_correct)
    dws = FakeDws()

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    ) == 0
    assert dws.sent == []
    corrected = store.get_follow_up_draft(draft_id)
    assert corrected is not None
    assert corrected.status == "draft"
    assert corrected.question_text == "修正后的问题"
    attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert attempt is not None
    assert attempt["state"] == "invalidated"


def test_expired_claim_before_sending_is_reclaimed_and_sent_once(
    tmp_path,
    monkeypatch,
):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    original_transition = store.transition_follow_up_attempt_to_sending

    def crash_after_claim(*args, **kwargs):
        raise KeyboardInterrupt("simulated crash after claim")

    monkeypatch.setattr(
        store,
        "transition_follow_up_attempt_to_sending",
        crash_after_claim,
    )
    dws = FakeDws()
    with pytest.raises(KeyboardInterrupt, match="simulated crash"):
        process_due_follow_ups(
            store,
            dws,
            now="2026-06-08 02:00:00",
            auto_send=True,
        )
    first_attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert first_attempt is not None
    assert first_attempt["state"] == "claimed"
    assert str(first_attempt["lease_owner"]).startswith("follow-up-dispatch:")
    assert first_attempt["claimed_at"] == "2026-06-08 02:00:00"
    assert first_attempt["lease_until"] == "2026-06-08 02:05:00"
    first_token = first_attempt["claim_token"]
    first_uuid = first_attempt["idempotency_uuid"]
    assert dws.sent == []

    monkeypatch.setattr(
        store,
        "transition_follow_up_attempt_to_sending",
        original_transition,
    )
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:06:00",
        auto_send=True,
    ) == 1
    assert len(dws.sent) == 1
    reclaimed = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert reclaimed is not None
    assert reclaimed["state"] == "sent"
    assert reclaimed["claim_token"] != first_token
    assert reclaimed["idempotency_uuid"] == first_uuid
    assert dws.sent[0]["idempotency_uuid"] == first_uuid


def test_expired_sending_attempt_retries_normally(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    original_finalize = store.update_claimed_follow_up_draft

    def crash_before_result_persistence(*args, **kwargs):
        raise KeyboardInterrupt("simulated crash before finalization")

    monkeypatch.setattr(store, "update_claimed_follow_up_draft", crash_before_result_persistence)
    dws = FakeDws()
    with pytest.raises(KeyboardInterrupt, match="simulated crash"):
        process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True)

    sending = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert sending is not None and sending["state"] == "sending"
    first_uuid = sending["idempotency_uuid"]
    monkeypatch.setattr(store, "update_claimed_follow_up_draft", original_finalize)

    # Lease expiry makes the same operation retryable; no application readback
    # or reconciliation worker is involved.
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:06:00", auto_send=True) == 1
    assert len(dws.sent) == 2
    assert dws.sent[1]["idempotency_uuid"] == first_uuid
    assert store.get_follow_up_draft(draft_id).status == "sent"
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None and attempt["state"] == "sent"

def test_expired_sending_result_retries_with_same_operation_id(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    original_finalize = store.update_claimed_follow_up_draft
    monkeypatch.setattr(
        store,
        "update_claimed_follow_up_draft",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    class DwsWithResult(FakeDws):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            return {"success": True, "result": {"openTaskId": "task-1"}}

    dws = DwsWithResult()
    with pytest.raises(KeyboardInterrupt):
        process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True)
    first = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert first is not None and first["state"] == "sending"
    first_uuid = first["idempotency_uuid"]
    assert json.loads(str(first["result_json"]))["send_result"]["result"]["openTaskId"] == "task-1"
    monkeypatch.setattr(store, "update_claimed_follow_up_draft", original_finalize)

    assert process_due_follow_ups(store, dws, now="2026-06-08 02:06:00", auto_send=True) == 1
    assert len(dws.sent) == 2
    assert dws.sent[1]["idempotency_uuid"] == first_uuid
    final = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert final is not None and final["state"] == "sent"
    assert store.get_follow_up_draft(draft_id).status == "sent"

def test_correction_invalidates_abandoned_claim_and_sends_new_revision(
    tmp_path,
    monkeypatch,
):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    original_transition = store.transition_follow_up_attempt_to_sending
    monkeypatch.setattr(
        store,
        "transition_follow_up_attempt_to_sending",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        process_due_follow_ups(
            store,
            FakeDws(),
            now="2026-06-08 02:00:00",
            auto_send=True,
        )
    old_attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert old_attempt is not None
    old_uuid = old_attempt["idempotency_uuid"]

    store.update_follow_up_draft(
        draft_id,
        question_text="修正后的问题",
        scheduled_at="2026-06-08 01:00:00",
    )
    assert store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )["state"] == "invalidated"
    monkeypatch.setattr(
        store,
        "transition_follow_up_attempt_to_sending",
        original_transition,
    )
    dws = FakeDws()
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:01:00",
        auto_send=True,
    ) == 1
    assert len(dws.sent) == 1
    assert "修正后的问题" in dws.sent[0]["text"]
    assert dws.sent[0]["idempotency_uuid"] != old_uuid
    assert store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )["state"] == "invalidated"


def test_follow_up_attempt_lease_ownership_uses_cas(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        status="draft",
        scheduled_at="2026-06-08 01:00:00",
    )
    assert store.claim_follow_up_draft_revision(
        draft_id,
        expected_revision=1,
        claim_token="token-a",
        idempotency_uuid="same-uuid",
        lease_owner="worker-a",
        claimed_at="2026-06-08 02:00:00",
        lease_until="2026-06-08 02:05:00",
    )
    assert not store.transition_follow_up_attempt_to_sending(
        draft_id,
        claimed_revision=1,
        claim_token="token-a",
        lease_owner="worker-b",
        now="2026-06-08 02:01:00",
        lease_until="2026-06-08 02:06:00",
    )
    assert not store.claim_follow_up_draft_revision(
        draft_id,
        expected_revision=1,
        claim_token="token-b",
        idempotency_uuid="same-uuid",
        lease_owner="worker-b",
        claimed_at="2026-06-08 02:04:00",
        lease_until="2026-06-08 02:09:00",
    )
    assert store.claim_follow_up_draft_revision(
        draft_id,
        expected_revision=1,
        claim_token="token-b",
        idempotency_uuid="same-uuid",
        lease_owner="worker-b",
        claimed_at="2026-06-08 02:06:00",
        lease_until="2026-06-08 02:11:00",
    )
    assert not store.transition_follow_up_attempt_to_sending(
        draft_id,
        claimed_revision=1,
        claim_token="token-a",
        lease_owner="worker-a",
        now="2026-06-08 02:06:00",
        lease_until="2026-06-08 02:11:00",
    )
    assert store.transition_follow_up_attempt_to_sending(
        draft_id,
        claimed_revision=1,
        claim_token="token-b",
        lease_owner="worker-b",
        now="2026-06-08 02:06:00",
        lease_until="2026-06-08 02:11:00",
    )


def test_expired_sending_attempt_is_reclaimed_by_only_one_worker(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        status="draft",
        scheduled_at="2026-07-01 01:00:00",
    )
    assert store.claim_follow_up_draft_revision(
        draft_id,
        expected_revision=1,
        claim_token="send-token",
        idempotency_uuid="send-uuid",
        lease_owner="sender",
        claimed_at="2026-06-08 02:00:00",
        lease_until="2026-06-08 02:05:00",
    )
    assert store.transition_follow_up_attempt_to_sending(
        draft_id,
        claimed_revision=1,
        claim_token="send-token",
        lease_owner="sender",
        now="2026-06-08 02:00:00",
        lease_until="2026-06-08 02:05:00",
    )

    # An expired sender lease is reclaimed through the normal draft claim CAS.
    assert store.claim_follow_up_draft_revision(
        draft_id,
        expected_revision=1,
        claim_token="retry-token",
        idempotency_uuid="send-uuid",
        lease_owner="retry-worker",
        claimed_at="2026-06-08 02:06:00",
        lease_until="2026-06-08 02:11:00",
    )
    assert not store.claim_follow_up_draft_revision(
        draft_id,
        expected_revision=1,
        claim_token="other-token",
        idempotency_uuid="send-uuid",
        lease_owner="other-worker",
        claimed_at="2026-06-08 02:06:00",
        lease_until="2026-06-08 02:11:00",
    )
    reclaimed = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert reclaimed is not None
    assert reclaimed["state"] == "claimed"
    assert reclaimed["lease_owner"] == "retry-worker"


def test_sent_attempt_review_enqueue_is_exactly_once_for_current_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        status="draft",
        scheduled_at="2026-07-01 01:00:00",
    )
    assert store.claim_follow_up_draft_revision(
        draft_id,
        expected_revision=1,
        claim_token="send-token",
        idempotency_uuid="send-uuid",
        lease_owner="sender",
        claimed_at="2026-06-08 02:00:00",
        lease_until="2026-06-08 02:05:00",
    )
    assert store.transition_follow_up_attempt_to_sending(
        draft_id,
        claimed_revision=1,
        claim_token="send-token",
        lease_owner="sender",
        now="2026-06-08 02:00:00",
        lease_until="2026-06-08 02:05:00",
    )
    assert store.update_claimed_follow_up_draft(
        draft_id,
        claimed_revision=1,
        claim_token="send-token",
        lease_owner="sender",
        now="2026-06-08 02:01:00",
        attempt_state="sent",
        attempt_result_json=json.dumps({"idempotency_uuid": "send-uuid"}),
        status="sent",
        send_result_json=json.dumps({"idempotency_uuid": "send-uuid"}),
    )
    source_ref = f"follow-up-repair:{draft_id}:prior-delivery:1:current:2"

    first = store.enqueue_follow_up_delivery_review(
        draft_id=draft_id,
        draft_revision=1,
        claim_token="send-token",
        current_revision=2,
        source_type="follow_up_completion_check",
        source_ref=source_ref,
        payload_json="{}",
    )
    second = store.enqueue_follow_up_delivery_review(
        draft_id=draft_id,
        draft_revision=1,
        claim_token="send-token",
        current_revision=2,
        source_type="follow_up_completion_check",
        source_ref=source_ref,
        payload_json="{}",
    )

    assert first is True
    assert second is False
    attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert attempt is not None
    assert attempt["state"] == "sent"
    assert attempt["review_enqueued_revision"] == 2
    queued = store.claim_work_summary_inputs(limit=2)
    assert len(queued) == 1
    assert queued[0].source_ref == source_ref


def test_prior_attempt_query_returns_all_failed_revisions_in_order(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        status="draft",
        scheduled_at="2026-06-08 01:00:00",
    )
    for revision in (1, 2):
        token = f"token-{revision}"
        owner = f"sender-{revision}"
        assert store.claim_follow_up_draft_revision(
            draft_id,
            expected_revision=revision,
            claim_token=token,
            idempotency_uuid=f"uuid-{revision}",
            lease_owner=owner,
            claimed_at="2026-06-08 02:00:00",
            lease_until="2026-06-08 02:05:00",
        )
        assert store.transition_follow_up_attempt_to_sending(
            draft_id,
            claimed_revision=revision,
            claim_token=token,
            lease_owner=owner,
            now="2026-06-08 02:00:00",
            lease_until="2026-06-08 02:05:00",
        )
        assert store.mark_follow_up_sending_retryable(
            draft_id,
            draft_revision=revision,
            claim_token=token,
            lease_owner=owner,
            result_json=json.dumps({"idempotency_uuid": f"uuid-{revision}"}),
        )
        store.update_follow_up_draft(
            draft_id,
            question_text=f"revision-{revision + 1}",
            scheduled_at="2026-06-08 01:00:00",
        )

    def read_blockers(_):
        return store.list_prior_follow_up_send_attempts(
            draft_id=draft_id,
            before_revision=3,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_reader, second_reader = list(pool.map(read_blockers, range(2)))

    assert [row["draft_revision"] for row in first_reader] == [2, 1]
    assert [row["draft_revision"] for row in second_reader] == [2, 1]
    assert [row["claim_token"] for row in first_reader] == ["token-2", "token-1"]


def test_historical_failed_attempt_does_not_block_new_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    assert store.claim_follow_up_draft_revision(
        draft_id, expected_revision=1, claim_token="old-token",
        idempotency_uuid="old-uuid", lease_owner="sender",
        claimed_at="2026-06-08 02:00:00", lease_until="2026-06-08 02:05:00",
    )
    assert store.transition_follow_up_attempt_to_sending(
        draft_id, claimed_revision=1, claim_token="old-token",
        lease_owner="sender", now="2026-06-08 02:00:00",
        lease_until="2026-06-08 02:05:00",
    )
    # A prior failed attempt remains historical data; the current dispatcher
    # must not route on it once a newer revision is scheduled.
    store.update_follow_up_draft(draft_id, question_text="revision-2", scheduled_at="2026-06-08 01:00:00")
    with store._connect() as db:
        db.execute(
            "update follow_up_send_attempts set state='failed', lease_owner='', lease_until='' where draft_id=? and draft_revision=1",
            (draft_id,),
        )
    store.update_follow_up_draft(draft_id, question_text="revision-3", scheduled_at="2026-06-08 01:00:00")

    dws = FakeDws()
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:02:00", auto_send=True) == 1
    assert len(dws.sent) == 1
    assert "revision-3" in dws.sent[0]["text"]
    old = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert old is not None and old["state"] == "failed"

def test_due_follow_up_defers_outside_local_working_hours(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步这个事项的最新进展。",
        scheduled_at="2026-06-29 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-29 12:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "draft"
    assert draft.scheduled_at == "2026-06-30 01:00:00"
    assert draft.suppressed_reason == "outside_local_working_hours"


def test_due_follow_up_queues_agent_repair_for_unverified_group_target(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.upsert_conversation(
        "cid-open-1",
        "客户项目群",
        single_chat=False,
        codex_session_id=None,
    )
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
        source_conversations_json=json.dumps(
            [
                {
                    "conversation_id": "123456",
                    "title": "客户项目群",
                    "kind": "project_chat",
                }
            ],
            ensure_ascii=False,
        ),
    )
    update_id = store.create_work_update(
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="42",
        summary="来源回复明确要求确认交付风险。",
        changes_json="{}",
        merge_reason="来源事项需要持续跟进。",
        confidence=0.9,
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认交付风险",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P0",
        next_follow_up_at="2026-06-07 09:00:00",
        created_from_update_id=update_id,
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="123456",
        target_kind="group",
        question_text="基于客户项目群提到的事项，今天能确认交付风险吗？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.target_conversation_id == "123456"
    assert draft.suppressed_reason == "target_requires_agent_review"
    queued = store.claim_work_summary_inputs(limit=1)
    assert len(queued) == 1
    assert queued[0].source_ref == f"follow-up-repair:{draft.id}"
    summary = json.loads(queued[0].payload_json)["summary"]
    assert json.loads(summary)["original_work_update"] == {
        "id": update_id,
        "source_type": "reply_attempt",
        "source_ref": "42",
        "summary": "来源回复明确要求确认交付风险。",
        "merge_reason": "来源事项需要持续跟进。",
    }


def test_due_follow_up_uses_reply_postfix_and_feedback_links(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户交付 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P0",
        next_follow_up_at="2026-06-07 09:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步这个事项的最新进展。",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
        feedback_base_url="https://feedback.example.com",
    )

    assert sent == 1
    sent_text = dws.sent[0]["text"]
    assert sent_text.startswith("**请确认：**")
    assert "**事项**" in sent_text
    assert "- 项目：客户交付" in sent_text
    assert "请同步这个事项的最新进展。" in sent_text
    assert "（by明哥分身）" in sent_text
    assert "/api/dingtalk-feedback-spike?feedback_token=" in sent_text
    send_result = json.loads(
        store.list_follow_up_drafts(statuses=("sent",))[0].send_result_json
    )
    assert send_result["feedback_token"].startswith("spike_")


def test_due_follow_up_uses_compact_markdown_sections_for_long_context(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="重点客户交付项目",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认验收材料和客户侧 blocker",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P0",
        deadline_at="2026-06-09 18:00:00",
    )
    long_description = (
        "客户侧验收材料已经反复沟通过，当前需要负责人明确最终验收口径、"
        "客户还缺哪些材料、是否存在资源阻塞、预计什么时候能闭环。"
        * 8
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        title="客户验收闭环确认",
        description=long_description,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步当前结果、阻塞、下一步和预计完成时间。",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    sent_text = dws.sent[0]["text"]
    assert sent_text.startswith("**请确认：** 请同步当前结果、阻塞、下一步和预计完成时间。")
    assert "**事项**" in sent_text
    assert "- 事项：客户验收闭环确认" in sent_text
    assert "- 项目：重点客户交付项目" in sent_text
    assert "- TODO：确认验收材料和客户侧 blocker" in sent_text
    assert "- 优先级：P0" in sent_text
    assert "- DDL：2026-06-09 18:00:00" in sent_text
    assert "**背景**" in sent_text
    assert long_description not in sent_text
    assert sent_text.count("...") == 1


def test_direct_follow_up_prefers_open_dingtalk_id_for_send_target(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户交付 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P0",
        next_follow_up_at="2026-06-07 09:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步这个事项的最新进展。",
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["open_dingtalk_id"] == "open-owner-1"
    assert dws.sent[0]["user_id"] is None
    send_result = json.loads(
        store.list_follow_up_drafts(statuses=("sent",))[0].send_result_json
    )
    assert send_result["owner_user_id"] == "owner-1"
    assert send_result["at_open_dingtalk_ids"] == ["open-owner-1"]


def test_group_follow_up_does_not_resolve_owner_from_name(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="Henry/BMW 自动驾驶数据挖掘商机技术响应推进",
        category="sales",
        status="active",
        priority="P0",
        risk_level="high",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        owner_user_id="",
        owner_name="Jack He(Yunguang He)",
        target_conversation_id="cid-henry",
        target_kind="group",
        question_text="Henry/BMW 数据挖掘昨天客户沟通结果怎样？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-11 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-12 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    draft = store.list_follow_up_drafts(statuses=("draft",))[0]
    assert draft.owner_user_id == ""
    assert draft.suppressed_reason == "owner_requires_agent_review"
    assert len(store.claim_work_summary_inputs(limit=1)) == 1


def test_due_follow_up_queues_agent_repair_instead_of_guessing_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户交付 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P0",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="",
        owner_name="",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="这个 P0 事项现在结果、阻塞和 ETA 分别是什么？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    deferred = store.get_follow_up_draft(draft_id)
    assert deferred is not None
    assert deferred.status == "draft"
    assert deferred.owner_user_id == ""
    assert deferred.suppressed_reason == "owner_requires_agent_review"
    queued = store.claim_work_summary_inputs(limit=1)
    assert len(queued) == 1
    assert queued[0].source_ref == f"follow-up-repair:{draft_id}"


def test_due_follow_up_skips_when_todo_completion_evidence_exists(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户交付 ETA",
        owner_user_id="owner-1",
        status="open",
        priority="P0",
        completion_evidence_json=json.dumps(
            {"source": "reply_attempt:7", "summary": "ETA 已发送客户"},
            ensure_ascii=False,
        ),
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="这个 P0 事项现在结果、阻塞和 ETA 分别是什么？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    completed = store.list_follow_up_drafts(statuses=("completed",))[0]
    assert completed.id == draft_id
    assert "todo has completion evidence" in completed.send_result_json


def test_due_follow_up_skips_when_todo_is_done(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户交付 ETA",
        owner_user_id="owner-1",
        status="done",
        priority="P0",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="这个 P0 事项现在结果、阻塞和 ETA 分别是什么？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    completed = store.list_follow_up_drafts(statuses=("completed",))[0]
    assert "todo status is done" in completed.send_result_json


def test_due_follow_up_skips_when_todo_is_cancelled(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户交付 ETA",
        owner_user_id="owner-1",
        status="cancelled",
        priority="P0",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="这个 P0 事项现在结果、阻塞和 ETA 分别是什么？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    skipped = store.list_follow_up_drafts(statuses=("skipped",))[0]
    assert "todo status is cancelled" in skipped.send_result_json


def test_due_follow_up_skips_when_linked_dingtalk_todo_is_done(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步验收 ETA。",
        scheduled_at="2026-06-27 01:00:00",
    )
    dws = FakeDws()
    dws.get_todo_task = lambda task_id: {"id": task_id, "done": True}

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-29 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    completed = store.list_follow_up_drafts(statuses=("completed",))[0]
    check = json.loads(completed.evidence_check_json)
    assert check["source"] == "dingtalk_todo:dt-task-1"
    assert check["reason"] == "DingTalk Todo marked done by owner"


def test_due_follow_up_sends_when_linked_dingtalk_todo_is_not_done(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步验收 ETA。",
        scheduled_at="2026-06-27 01:00:00",
    )
    dws = FakeDws()
    dws.todo_payloads["dt-task-1"] = {"id": "dt-task-1", "done": False}

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-29 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert len(dws.sent) == 1
    assert store.get_work_todo(todo_id).status == "open"


def test_old_due_follow_up_refreshes_live_todo_then_queues_agent_reevaluation(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = _create_bound_todo(store, project_id)
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-stale",
        executor_user_id="owner-1",
        title_snapshot="确认当前交付状态",
        status="active",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步这个事项的最新进展。",
        scheduled_at="2026-06-01 09:00:00",
    )
    dws = FakeDws()
    dws.todo_payloads["dt-task-stale"] = {
        "id": "dt-task-stale",
        "done": False,
        "modifiedTime": "2026-06-09T08:58:00+08:00",
    }

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-09 09:00:01",
        auto_send=True,
    )
    sent_again = process_due_follow_ups(
        store,
        dws,
        now="2026-06-10 01:00:01",
        auto_send=True,
    )

    assert sent == 0
    assert sent_again == 0
    assert dws.sent == []
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "draft"
    assert draft.suppressed_reason == "stale_follow_up_requires_agent_review"
    link = store.get_active_work_todo_dingtalk_link(todo_id)
    assert link is not None
    assert link.last_pull_at == "2026-06-10 01:00:01"
    assert json.loads(link.last_dingtalk_payload_json)["modifiedTime"] == (
        "2026-06-09T08:58:00+08:00"
    )
    queued = store.claim_work_summary_inputs(limit=2)
    assert len(queued) == 1
    first_repair_source_ref = queued[0].source_ref
    work_item = json.loads(queued[0].payload_json)
    assert queued[0].source_ref.startswith(f"follow-up-repair:{draft_id}:")
    summary = json.loads(work_item["summary"])
    assert summary["reason"] == "stale_follow_up_requires_agent_review"
    assert summary["todo"]["status"] == "open"
    assert summary["todo"]["dingtalk"]["done"] is False
    assert summary["todo"]["dingtalk"]["last_pull_at"] == "2026-06-10 01:00:01"

    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "project": {
                "id": project_id,
                "title": "客户交付",
                "memory_context": {
                    "query": "客户交付",
                    "summary": "Current repair context supplied by the work item.",
                },
            },
            "follow_up_changes": [
                {
                    "follow_up_id": draft_id,
                    "todo_id": todo_id,
                    "action": "keep_open",
                    "reason": "Current TODO is still open; ask again next workday.",
                    "next_due_at": "2026-06-11T10:00:00+08:00",
                }
            ],
            "memory_recall_used": True,
        }
    )
    apply_task_agent_decision(
        store,
        summary_input_id=queued[0].id,
        work_item=WorkItem.model_validate_json(queued[0].payload_json),
        decision=decision,
        memory_recall_attempted=True,
        now="2026-06-10 01:05:00",
    )
    store.mark_work_summary_input_done(queued[0].id)

    repaired = store.get_follow_up_draft(draft_id)
    assert repaired is not None
    assert repaired.suppressed_reason == ""
    assert repaired.scheduled_at == "2026-06-11T10:00:00+08:00"
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-10 01:06:00",
        auto_send=True,
    ) == 0
    assert store.claim_work_summary_inputs(limit=2) == []

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-19 02:00:01",
        auto_send=True,
    ) == 0
    later_repairs = store.claim_work_summary_inputs(limit=2)
    assert len(later_repairs) == 1
    assert later_repairs[0].source_ref.startswith(f"follow-up-repair:{draft_id}:")
    assert later_repairs[0].source_ref != first_repair_source_ref


def test_stale_deferral_cannot_overwrite_concurrent_repaired_schedule(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-stale-race",
        executor_user_id="owner-1",
        status="active",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="旧问题",
        scheduled_at="2026-06-01 09:00:00",
    )

    class RepairingDws(FakeDws):
        def get_todo_task(self, task_id):
            store.update_follow_up_draft(
                draft_id,
                question_text="修复后的问题",
                scheduled_at="2026-06-10 02:00:00",
                suppressed_reason="",
            )
            return {"id": task_id, "done": False}

    dws = RepairingDws()
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-09 02:00:00",
        auto_send=True,
    ) == 0

    repaired = store.get_follow_up_draft(draft_id)
    assert repaired is not None
    assert repaired.status == "draft"
    assert repaired.question_text == "修复后的问题"
    assert repaired.scheduled_at == "2026-06-10 02:00:00"
    assert repaired.suppressed_reason == ""
    assert store.claim_work_summary_inputs(limit=2) == []


def test_draft_follow_up_sends_direct_message_when_live_send_enabled(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = _create_bound_todo(store, project_id)
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步这个事项的最新进展。",
        risk_check_json=json.dumps({"owner_in_group": False, "sensitive": True}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] is None
    assert dws.sent[0]["user_id"] is None
    assert dws.sent[0]["open_dingtalk_id"] == "open-owner-1"
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-owner-1"]
    assert not dws.sent[0]["text"].startswith("<@")


def test_direct_follow_up_with_conversation_id_uses_direct_owner_target(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="售前圆桌",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = _create_bound_todo(store, project_id)
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="direct:owner-1",
        target_kind="direct",
        question_text="请同步这个事项的最新进展。",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] is None
    assert dws.sent[0]["user_id"] is None
    assert dws.sent[0]["open_dingtalk_id"] == "open-owner-1"
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-owner-1"]


def test_follow_up_uses_cached_org_profile_before_live_dws_lookup(tmp_path):
    class LookupFailingDws(FakeDws):
        def get_user_profile(self, user_id):
            raise AssertionError("live profile lookup should not be called")

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.upsert_org_user_profile(
        user_id="owner-1",
        name="Alex Cached",
        open_dingtalk_id="open-cached-owner",
        manager_user_id=None,
        department_ids=set(),
    )
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = _create_bound_todo(store, project_id)
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步这个事项的最新进展。",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = LookupFailingDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["at_users"] == ["owner-1"]
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-cached-owner"]
    assert dws.sent[0]["at_open_dingtalk_names"] == ["Alex Cached"]


def test_dry_run_does_not_send_due_follow_up(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_user_id="owner-1",
        target_kind="direct",
        question_text="请同步这个事项的最新进展。",
        risk_check_json=json.dumps({"owner_in_group": False, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=False,
    )

    assert sent == 0
    assert dws.sent == []
    assert store.list_follow_up_drafts(statuses=("draft",))[0].id == draft_id


def test_sensitive_group_follow_up_requires_verified_direct_target(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="人事敏感事项",
        category="HR",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": True}),
        scheduled_at="2026-06-07 09:00:00",
    )

    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "draft"
    assert draft.target_kind == "group"
    assert draft.suppressed_reason == "target_requires_agent_review"
    queued = store.claim_work_summary_inputs(limit=1)
    assert len(queued) == 1
    assert queued[0].source_ref == f"follow-up-repair:{draft_id}"


def test_missing_risk_check_does_not_block_sendable_follow_up(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] == "cid-1"
    assert store.list_follow_up_drafts(statuses=("sent",))[0].id == draft_id


def test_group_follow_up_without_group_queues_agent_target_repair(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="group",
        question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": False, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "draft"
    assert draft.suppressed_reason == "target_requires_agent_review"
    queued = store.claim_work_summary_inputs(limit=1)
    assert len(queued) == 1


def test_follow_up_cannot_send_without_bound_todo(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步进展",
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.suppressed_reason == "todo_binding_requires_agent_review"
    assert len(store.claim_work_summary_inputs(limit=1)) == 1


def test_follow_up_failure_marks_failed_and_records_error(tmp_path):
    class BrokenDws:
        def get_user_profile(self, user_id):
            return DwsUserProfile(
                user_id=user_id,
                name=user_id,
                open_dingtalk_id=f"open-{user_id}",
            )

        def send_message(self, *args, **kwargs):
            raise RuntimeError("send failed")

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )

    sent = process_due_follow_ups(
        store,
        BrokenDws(),
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    failed = store.list_follow_up_drafts(statuses=("failed",))[0]
    assert failed.id == draft_id
    assert "send failed" in failed.send_result_json


def test_transport_failure_uses_failed_projection_without_reconciliation(
    tmp_path,
):
    """A send exception is an ordinary failure; no app-owned readback state is created."""
    from app.dws_client import DwsError

    class FailedDws(FakeDws):
        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            raise DwsError("transport returned no result", code="1")

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )

    dws = FailedDws()
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    ) == 0

    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "failed"
    result = json.loads(draft.send_result_json)
    assert result["reason"] == "delivery_failed"
    assert result["delivery_state"] == "not_sent"
    attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert attempt is not None
    assert attempt["state"] == "failed"
    assert attempt["idempotency_uuid"] == result["idempotency_uuid"]
    assert "reconciliation" not in result


def test_direct_target_rejection_is_clear_non_delivery_failure(tmp_path):
    from app.dws_client import DwsError

    class DirectTargetRejectedDws(FakeDws):
        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            raise DwsError(
                "dws command failed; operation: chat/send_personal_message",
                code="ERROR",
            )

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="Recruiting", category="HR")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        target_kind="direct",
        question_text="请确认候选人流程状态。",
        risk_check_json=json.dumps({"owner_in_group": False, "sensitive": True}),
        scheduled_at="2026-06-07 09:00:00",
    )

    assert process_due_follow_ups(
        store,
        DirectTargetRejectedDws(),
        now="2026-06-08 02:00:00",
        auto_send=True,
    ) == 0

    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    result = json.loads(draft.send_result_json)
    assert draft.status == "failed"
    assert result["reason"] == "direct_message_target_rejected"
    assert result["delivery_state"] == "not_sent"
    assert "no message was delivered" in result["error"]
    [error] = store.list_errors()
    assert error.detail == result["error"]


def test_requeued_rejected_direct_target_waits_for_agent_repair(tmp_path):
    from app.dws_client import DwsError

    class DirectTargetRejectedDws(FakeDws):
        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            raise DwsError(
                "dws command failed; operation: chat/send_personal_message",
                code="ERROR",
            )

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="Recruiting", category="HR")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="inactive-owner",
        owner_name="Former owner",
        target_kind="direct",
        question_text="请确认候选人流程状态。",
        risk_check_json=json.dumps({"sensitive": True}),
        scheduled_at="2026-06-08 01:00:00",
    )
    rejected_dws = DirectTargetRejectedDws()
    assert process_due_follow_ups(
        store,
        rejected_dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    ) == 0
    rejected = store.get_follow_up_draft(draft_id)
    assert rejected is not None and rejected.status == "failed"

    store.update_follow_up_draft(
        draft_id,
        status="draft",
        scheduled_at="2026-06-09 01:00:00",
        send_result_json="{}",
        suppressed_reason="",
    )
    live_dws = FakeDws()

    assert process_due_follow_ups(
        store,
        live_dws,
        now="2026-06-09 02:00:00",
        auto_send=True,
    ) == 0

    deferred = store.get_follow_up_draft(draft_id)
    assert deferred is not None and deferred.status == "draft"
    assert deferred.suppressed_reason == (
        "direct_message_target_rejected_requires_agent_review"
    )
    assert live_dws.sent == []
    [review] = store.claim_work_summary_inputs(limit=1)
    assert review.source_ref == f"follow-up-repair:{draft_id}"


def test_reassigned_rejected_direct_target_can_send_new_revision(tmp_path):
    from app.dws_client import DwsError

    class DirectTargetRejectedDws(FakeDws):
        def send_message(self, *args, **kwargs):
            raise DwsError(
                "dws command failed; operation: chat/send_personal_message",
                code="ERROR",
            )

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="Recruiting", category="HR")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="inactive-owner",
        owner_name="Former owner",
        target_kind="direct",
        question_text="请确认候选人流程状态。",
        risk_check_json=json.dumps({"sensitive": True}),
        scheduled_at="2026-06-08 01:00:00",
    )
    assert process_due_follow_ups(
        store,
        DirectTargetRejectedDws(),
        now="2026-06-08 02:00:00",
        auto_send=True,
    ) == 0
    store.update_follow_up_draft(
        draft_id,
        status="draft",
        owner_user_id="active-owner",
        owner_name="Active owner",
        reaction_status="redirect_owner",
        scheduled_at="2026-06-09 01:00:00",
        send_result_json="{}",
        suppressed_reason="",
    )
    live_dws = FakeDws()

    assert process_due_follow_ups(
        store,
        live_dws,
        now="2026-06-09 02:00:00",
        auto_send=True,
    ) == 1

    sent = store.get_follow_up_draft(draft_id)
    assert sent is not None and sent.status == "sent"
    assert live_dws.sent[0]["user_id"] is None
    assert live_dws.sent[0]["open_dingtalk_id"] == "open-active-owner"


def test_confirmed_not_sent_follow_up_can_repair_same_draft_once(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="Recruiting", category="HR")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="inactive-owner",
        owner_name="Former owner",
        target_kind="direct",
        question_text="请确认候选人流程状态。",
        risk_check_json=json.dumps({"sensitive": True}),
        scheduled_at="2026-06-07 09:00:00",
        status="failed",
        send_result_json=json.dumps(
            {
                "reason": "direct_message_target_rejected",
                "delivery_state": "not_sent",
                "external_side_effect": "none",
            }
        ),
    )
    before = store.get_follow_up_draft(draft_id)
    assert before is not None

    assert resolve_failed_follow_up(
        store,
        draft_id,
        expected_revision=before.revision,
        resolution="repair_target",
        now="2026-06-08 02:00:00",
    )

    repaired = store.get_follow_up_draft(draft_id)
    assert repaired is not None
    assert repaired.id == draft_id
    assert repaired.status == "draft"
    assert repaired.revision == before.revision + 1
    assert repaired.suppressed_reason == "manual_follow_up_target_repair_requested"
    [review] = store.claim_work_summary_inputs(limit=1)
    assert review.source_ref == f"follow-up-repair:{draft_id}"
    assert not resolve_failed_follow_up(
        store,
        draft_id,
        expected_revision=before.revision,
        resolution="repair_target",
        now="2026-06-08 02:00:01",
    )


def test_confirmed_not_sent_follow_up_can_be_cancelled_without_replay(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="Recruiting", category="HR")
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_user_id="inactive-owner",
        target_kind="direct",
        question_text="请确认候选人流程状态。",
        scheduled_at="2026-06-07 09:00:00",
        status="failed",
        send_result_json=json.dumps({"delivery_state": "not_sent"}),
    )
    before = store.get_follow_up_draft(draft_id)
    assert before is not None

    assert resolve_failed_follow_up(
        store,
        draft_id,
        expected_revision=before.revision,
        resolution="cancel",
        now="2026-06-08 02:00:00",
    )

    cancelled = store.get_follow_up_draft(draft_id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert cancelled.suppressed_reason == "human_cancelled_after_delivery_failure"
    assert store.claim_work_summary_inputs(limit=1) == []


def test_failed_follow_up_delivery_can_be_repaired_or_cancelled(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="Recruiting", category="HR")
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_user_id="owner-1",
        target_kind="direct",
        question_text="请确认候选人流程状态。",
        scheduled_at="2026-06-07 09:00:00",
        status="failed",
        send_result_json=json.dumps({"delivery_state": "failed"}),
    )
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None

    assert resolve_failed_follow_up(
        store,
        draft_id,
        expected_revision=draft.revision,
        resolution="repair_target",
        now="2026-06-08 02:00:00",
    )
    repaired = store.get_follow_up_draft(draft_id)
    assert repaired is not None
    assert repaired.status == "draft"


def test_dws_login_required_defers_follow_up_without_marking_failed(tmp_path):
    from app.dws_client import DwsError

    class AuthMissingDws:
        def get_user_profile(self, user_id):
            raise DwsError("not_authenticated", code="not_authenticated")

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )

    sent = process_due_follow_ups(
        store,
        AuthMissingDws(),
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert store.list_follow_up_drafts(statuses=("failed",)) == []
    draft = store.list_follow_up_drafts(statuses=("draft",))[0]
    assert draft.id == draft_id
    assert draft.scheduled_at == "2026-06-08 02:15:00"
    result = json.loads(draft.send_result_json)
    assert result["recoverable"] is True
    assert result["reason"] == "dws_login_required"


def test_transport_timeout_is_retryable_without_reconciliation_state(tmp_path):
    from app.dws_client import DwsError

    class RetryableDws(FakeDws):
        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            raise DwsError("dws command timed out after 30 seconds", retryable_external_dependency=True)

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付", category="projects", status="active", priority="P0", risk_level="high")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        target_conversation_id="cid-1", target_kind="group", question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = RetryableDws()
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True) == 0
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None and draft.status == "draft"
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None and attempt["state"] == "retryable"
    result = json.loads(str(attempt["result_json"]))
    assert result["reason"] == "delivery_failed"
    assert result["retryable"] is True
    assert "reconciliation" not in result

    # The normal retry uses the same operation identifier and does not perform
    # a second application-owned readback.
    first_uuid = dws.sent[0]["idempotency_uuid"]
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:01:00", auto_send=True) == 0
    assert len(dws.sent) == 2
    assert dws.sent[1]["idempotency_uuid"] == first_uuid

def test_dws_send_failure_records_provider_result_and_operation_id(tmp_path):
    from app.dws_client import DwsError

    class FailedDws(FakeDws):
        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            raise DwsError("dws command failed with exit code 1", code="1")

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付", category="projects", status="active", priority="P0", risk_level="high")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        target_conversation_id="cid-1", target_kind="group", question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FailedDws()
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True) == 0
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None and draft.status == "failed"
    result = json.loads(draft.send_result_json)
    assert result["reason"] == "delivery_failed"
    assert result["idempotency_uuid"] == dws.sent[0]["idempotency_uuid"]
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None and attempt["state"] == "failed"
    assert attempt["idempotency_uuid"] == result["idempotency_uuid"]
    assert "reconciliation" not in result

def test_failed_direct_follow_up_does_not_start_application_readback(tmp_path):
    from app.dws_client import DwsError

    class ReadbackDws(FakeDws):
        def __init__(self):
            super().__init__()
            self.readbacks = 0

        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            raise DwsError("network interrupted after send", code="1")

        def read_direct_messages_since(self, user_id, *, start):
            self.readbacks += 1
            return {"complete": True, "messages": []}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        owner_name="Alex", target_kind="direct", question_text="请同步进展",
        scheduled_at="2026-06-08 01:00:00",
    )
    dws = ReadbackDws()
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True) == 0
    assert dws.readbacks == 0
    assert len(dws.sent) == 1
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None and draft.status == "failed"
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None and attempt["state"] == "failed"

def test_failed_direct_follow_up_retries_after_normal_revision_update(tmp_path):
    from app.dws_client import DwsError

    class ReadbackDws(FakeDws):
        def __init__(self):
            super().__init__()
            self.fail_first_send = True
            self.readbacks = 0

        def send_message(self, *args, **kwargs):
            if self.fail_first_send:
                self.fail_first_send = False
                self.sent.append(kwargs)
                raise DwsError("network interrupted before send", code="1")
            return super().send_message(*args, **kwargs)

        def read_direct_messages_since(self, user_id, *, start):
            self.readbacks += 1
            return {"complete": True, "messages": []}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        owner_name="Alex", target_kind="direct", question_text="请同步进展",
        scheduled_at="2026-06-08 01:00:00",
    )
    dws = ReadbackDws()
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True) == 0
    failed = store.get_follow_up_draft(draft_id)
    assert failed is not None and failed.status == "failed"
    first = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert first is not None and first["state"] == "failed"
    first_uuid = first["idempotency_uuid"]
    assert dws.readbacks == 0

    # A subsequent normal revision is the retry boundary; it does not invoke a
    # special reconciliation pass.
    store.update_follow_up_draft(
        draft_id, status="draft", question_text="请同步修正后的进展",
        scheduled_at="2026-06-08 02:01:00", send_result_json="{}",
    )
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:02:00", auto_send=True) == 1
    assert len(dws.sent) == 2
    assert dws.sent[1]["idempotency_uuid"] != first_uuid
    assert dws.readbacks == 0
    assert store.get_follow_up_draft(draft_id).status == "sent"

def test_failed_direct_follow_up_ignores_partial_readback_payload(tmp_path):
    from app.dws_client import DwsError

    class PartialReadbackDws(FakeDws):
        def __init__(self):
            super().__init__()
            self.readbacks = 0

        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            raise DwsError("network interrupted after send", code="1")

        def read_direct_messages_since(self, user_id, *, start):
            self.readbacks += 1
            return {"complete": False, "messages": []}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        owner_name="Alex", target_kind="direct", question_text="请同步进展",
        scheduled_at="2026-06-08 01:00:00",
    )
    dws = PartialReadbackDws()
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True) == 0
    assert dws.readbacks == 0
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None and draft.status == "failed"
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None and attempt["state"] == "failed"

def test_correction_retries_new_revision_after_old_send_failure(tmp_path):
    from app.dws_client import DwsError

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        owner_name="Alex", target_conversation_id="cid-1", target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )

    class CorrectingDws(FakeDws):
        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            if len(self.sent) == 1:
                store.update_follow_up_draft(
                    draft_id, question_text="修正后的问题", scheduled_at="2026-06-08 02:00:00"
                )
                raise DwsError("dws command failed with exit code 1", code="1")
            return {"success": True, "result": {"openTaskId": "new-revision"}}

    dws = CorrectingDws()
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True) == 0
    old = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert old is not None and old["state"] == "failed"
    assert "reconciliation" not in json.loads(str(old["result_json"]))
    corrected = store.get_follow_up_draft(draft_id)
    assert corrected is not None and corrected.revision == 2
    assert corrected.status == "draft" and corrected.question_text == "修正后的问题"

    assert process_due_follow_ups(store, dws, now="2026-06-08 02:01:00", auto_send=True) == 1
    assert len(dws.sent) == 2
    assert dws.sent[1]["idempotency_uuid"] != dws.sent[0]["idempotency_uuid"]
    assert store.get_follow_up_draft(draft_id).status == "sent"

def test_future_scheduled_correction_invalidates_expired_old_send(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        owner_name="Alex", target_conversation_id="cid-1", target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    original_finalize = store.update_claimed_follow_up_draft
    monkeypatch.setattr(store, "update_claimed_follow_up_draft", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    dws = FakeDws()
    with pytest.raises(KeyboardInterrupt):
        process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True)
    old_uuid = dws.sent[0]["idempotency_uuid"]
    store.update_follow_up_draft(
        draft_id, question_text="修正后的问题", scheduled_at="2026-07-01 01:00:00"
    )
    monkeypatch.setattr(store, "update_claimed_follow_up_draft", original_finalize)
    corrected = store.get_follow_up_draft(draft_id)
    assert corrected is not None and corrected.revision == 2

    # Nothing runs while the corrected revision is scheduled in the future;
    # no readback worker is started for the expired prior operation.
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:16:00", auto_send=True) == 0
    assert len(dws.sent) == 1
    old = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert old is not None and old["state"] == "sending"
    assert old["idempotency_uuid"] == old_uuid

    assert process_due_follow_ups(store, dws, now="2026-07-01 01:01:00", auto_send=True) == 1
    assert len(dws.sent) == 2
    assert dws.sent[1]["idempotency_uuid"] != old_uuid
    old = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert old is not None and old["state"] == "invalidated"

def test_late_send_result_is_persisted_only_on_old_revision_and_queues_review(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )

    class CorrectingSuccessDws(FakeDws):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            store.update_follow_up_draft(
                draft_id,
                question_text="修正后的问题",
                scheduled_at="2026-07-01 01:00:00",
            )
            return {"success": True, "result": {"openTaskId": "late-result"}}

    dws = CorrectingSuccessDws()
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    ) == 0
    assert len(dws.sent) == 1
    old_uuid = dws.sent[0]["idempotency_uuid"]
    old_attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert old_attempt is not None
    assert old_attempt["state"] == "sent"
    assert old_attempt["idempotency_uuid"] == old_uuid
    assert json.loads(str(old_attempt["result_json"]))["send_result"]["result"] == {
        "openTaskId": "late-result"
    }
    corrected = store.get_follow_up_draft(draft_id)
    assert corrected is not None
    assert corrected.revision == 2
    assert corrected.status == "draft"
    assert corrected.question_text == "修正后的问题"
    assert corrected.scheduled_at == "2026-07-01 01:00:00"
    assert corrected.suppressed_reason == ""
    assert corrected.send_result_json == "{}"
    queued = store.claim_work_summary_inputs(limit=2)
    assert len(queued) == 1
    assert queued[0].source_ref == (
        f"follow-up-repair:{draft_id}:prior-delivery:1:current:2"
    )
    work_item = json.loads(queued[0].payload_json)
    summary = json.loads(work_item["summary"])
    evidence = summary["delivery_evidence"]
    assert evidence["prior_revision"] == 1
    assert evidence["prior_idempotency_uuid"] == old_uuid
    assert evidence["old_content_delivery_proven"] is True
    assert evidence["prior_delivered_text"]
    assert evidence["current_revision"] == 2
    assert evidence["current_question_text"] == "修正后的问题"

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:01:00",
        auto_send=True,
    ) == 0
    assert len(dws.sent) == 1
    held = store.get_follow_up_draft(draft_id)
    assert held is not None
    assert held.status == "draft"
    assert held.scheduled_at == "2026-07-01 01:00:00"
    assert held.suppressed_reason == ""
    reviewed_attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert reviewed_attempt is not None
    assert reviewed_attempt["state"] == "sent"
    assert reviewed_attempt["review_enqueued_revision"] == 2
    assert store.claim_work_summary_inputs(limit=2) == []
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-09 02:01:00",
        auto_send=True,
    ) == 0
    assert len(dws.sent) == 1


@pytest.mark.parametrize("late_outcome", ["sent", "failed"])
def test_delayed_sender_result_is_recorded_after_revision_change(tmp_path, late_outcome):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )

    class DelayedResultDws(FakeDws):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            store.update_follow_up_draft(
                draft_id,
                question_text="修正后的问题",
                scheduled_at="2026-07-01 01:00:00",
            )
            if late_outcome == "failed":
                return {"success": False, "error": "late send failure"}
            return {"success": True, "result": {"openTaskId": "late-result"}}

    dws = DelayedResultDws()
    assert process_due_follow_ups(
        store, dws, now="2026-06-08 02:00:00", auto_send=True
    ) == 0
    assert len(dws.sent) == 1
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None
    expected_state = "sent" if late_outcome == "sent" else "failed"
    assert attempt["state"] == expected_state
    late_result = json.loads(str(attempt["late_result_json"]))["send_result"]
    assert late_result["success"] is (late_outcome == "sent")
    assert json.loads(str(attempt["conflict_json"])) == {}
    corrected = store.get_follow_up_draft(draft_id)
    assert corrected is not None and corrected.revision == 2
    assert corrected.question_text == "修正后的问题"

def test_failed_delivery_review_reopens_same_input_and_done_releases_draft(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )

    class CorrectingDws(FakeDws):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            if len(self.sent) == 1:
                store.update_follow_up_draft(
                    draft_id,
                    question_text="修正后的问题",
                    scheduled_at="2026-06-08 01:00:00",
                )
            return {"success": True, "result": {"openTaskId": "delivered"}}

    dws = CorrectingDws()
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    ) == 0
    first_review = store.claim_work_summary_inputs(limit=1)[0]
    store.mark_work_summary_input_failed(first_review.id, "retry exhaustion")

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:01:00",
        auto_send=True,
    ) == 0
    assert len(dws.sent) == 1
    reopened = store.claim_work_summary_inputs(limit=2)
    assert len(reopened) == 1
    assert reopened[0].id == first_review.id
    assert reopened[0].source_ref == first_review.source_ref
    store.mark_work_summary_input_done(reopened[0].id)

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:02:00",
        auto_send=True,
    ) == 1
    assert len(dws.sent) == 2
    assert "修正后的问题" in dws.sent[1]["text"]
    assert store.claim_work_summary_inputs(limit=2) == []

def test_late_receipt_does_not_overwrite_newer_revision_cas(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )

    class CorrectingDws(FakeDws):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            store.update_follow_up_draft(
                draft_id,
                question_text="修正后的问题",
                scheduled_at="2026-07-01 01:00:00",
            )
            return {"success": True, "result": {"openTaskId": "late-result"}}

    dws = CorrectingDws()
    assert process_due_follow_ups(
        store, dws, now="2026-06-08 02:00:00", auto_send=True
    ) == 0
    assert len(dws.sent) == 1
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None
    assert attempt["state"] == "sent"
    assert json.loads(str(attempt["late_result_json"]))["send_result"]["result"]["openTaskId"] == "late-result"
    corrected = store.get_follow_up_draft(draft_id)
    assert corrected is not None
    assert corrected.question_text == "修正后的问题"
    assert corrected.revision == 2
    assert corrected.status == "draft"

def _claim_same_revision_authoritative_sent_upgrade(store, draft_id, lease_owner):
    assert store.claim_follow_up_draft_revision(
        draft_id,
        expected_revision=1,
        claim_token="same-revision-token",
        idempotency_uuid="same-revision-uuid",
        lease_owner="sender",
        claimed_at="2026-06-08 02:00:00",
        lease_until="2026-06-08 02:05:00",
    )
    assert store.transition_follow_up_attempt_to_sending(
        draft_id,
        claimed_revision=1,
        claim_token="same-revision-token",
        lease_owner="sender",
        now="2026-06-08 02:00:00",
        lease_until="2026-06-08 02:05:00",
    )
    assert store.mark_follow_up_sending_retryable(
        draft_id,
        draft_revision=1,
        claim_token="same-revision-token",
        lease_owner="sender",
        result_json=json.dumps({"send_result": {"success": False, "error": "transport"}}),
    )
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None
    transition = store.apply_follow_up_late_send_result(
        attempt_id=int(attempt["id"]),
        draft_id=draft_id,
        draft_revision=1,
        claim_token="same-revision-token",
        idempotency_uuid="same-revision-uuid",
        outcome="sent",
        result_json=json.dumps({"send_result": {"success": True, "result": {"openTaskId": "same"}}}),
        sent_at="2026-06-08 02:06:00",
    )
    assert transition["outcome"] == "conflict"
    return attempt


def test_late_sent_result_does_not_finalize_retryable_draft(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        status="draft",
        scheduled_at="2026-06-08 01:00:00",
    )
    _claim_same_revision_authoritative_sent_upgrade(store, draft_id, "upgrade")
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "draft"
    assert draft.revision == 1
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None and attempt["state"] == "retryable"


def test_newer_claim_prevents_late_result_from_overwriting_current_projection(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(project_id=project_id, status="draft", scheduled_at="2026-06-08 01:00:00")
    assert store.claim_follow_up_draft_revision(
        draft_id, expected_revision=1, claim_token="old-token", idempotency_uuid="old-uuid",
        lease_owner="sender", claimed_at="2026-06-08 02:00:00", lease_until="2026-06-08 02:05:00",
    )
    assert store.transition_follow_up_attempt_to_sending(
        draft_id, claimed_revision=1, claim_token="old-token", lease_owner="sender",
        now="2026-06-08 02:00:00", lease_until="2026-06-08 02:05:00",
    )
    assert store.record_follow_up_sending_result(
        draft_id, draft_revision=1, claim_token="old-token", lease_owner="sender",
        now="2026-06-08 02:00:00",
        result_json=json.dumps({"send_result": {"result": {"openTaskId": "old"}}}),
    )
    with store._connect() as db:
        db.execute(
            "update follow_up_drafts set send_claim_revision=1, send_claim_token='new-token', send_claim_idempotency_uuid='new-uuid' where id=? and revision=1",
            (draft_id,),
        )
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None
    transition = store.apply_follow_up_late_send_result(
        attempt_id=int(attempt["id"]), draft_id=draft_id, draft_revision=1,
        claim_token="old-token", idempotency_uuid="old-uuid", outcome="sent",
        result_json=json.dumps({"send_result": {"result": {"openTaskId": "late"}}}),
        sent_at="2026-06-08 02:06:00",
    )
    assert transition["draft_finalized"] is False
    current = store.get_follow_up_draft(draft_id)
    assert current is not None and current.revision == 1
    assert current.send_claim_token == "new-token" and current.status == "draft"
    persisted = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert persisted is not None and persisted["state"] == "sent"

def test_same_revision_late_result_is_idempotent_under_concurrency(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        status="draft",
        scheduled_at="2026-06-08 01:00:00",
    )
    _claim_same_revision_authoritative_sent_upgrade(store, draft_id, "upgrade")

    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None

    def finalize(_):
        return store.apply_follow_up_late_send_result(
            attempt_id=int(attempt["id"]),
            draft_id=draft_id,
            draft_revision=1,
            claim_token="same-revision-token",
            idempotency_uuid="same-revision-uuid",
            outcome="sent",
            result_json=json.dumps({"send_result": {"success": True, "result": {"openTaskId": "same"}}}),
            sent_at="2026-06-08 02:07:00",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(finalize, range(2)))

    assert [result["outcome"] for result in outcomes] == ["conflict", "conflict"]
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "draft"
    assert draft.revision == 1


@pytest.mark.parametrize("late_outcome", ["sent", "failed", "ambiguous"])
def test_late_result_preserves_retryable_attempt_without_readback(tmp_path, late_outcome):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(project_id=project_id, status="draft", scheduled_at="2026-06-08 01:00:00")
    assert store.claim_follow_up_draft_revision(
        draft_id, expected_revision=1, claim_token="token", idempotency_uuid="uuid",
        lease_owner="sender", claimed_at="2026-06-08 02:00:00", lease_until="2026-06-08 02:05:00",
    )
    assert store.transition_follow_up_attempt_to_sending(
        draft_id, claimed_revision=1, claim_token="token", lease_owner="sender",
        now="2026-06-08 02:00:00", lease_until="2026-06-08 02:05:00",
    )
    base = {"send_result": {"success": True, "provider_id": "provider-1"}}
    assert store.mark_follow_up_sending_retryable(
        draft_id, draft_revision=1, claim_token="token", lease_owner="sender",
        result_json=json.dumps(base),
    )
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None and attempt["state"] == "retryable"
    # A late transport report is retained as data.  It never triggers a
    # reconciliation transition; the attempt remains ordinary retryable.
    late_result = json.dumps({"send_result": {"success": False, "provider_id": f"provider-{late_outcome}"}})
    transition = store.apply_follow_up_late_send_result(
        attempt_id=int(attempt["id"]), draft_id=draft_id, draft_revision=1,
        claim_token="token", idempotency_uuid="uuid",
        outcome="failed",
        result_json=late_result, sent_at="2026-06-08 02:06:00",
    )
    assert transition["outcome"] == "equivalent_failed"
    persisted = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert persisted is not None and persisted["state"] == "retryable"
    assert json.loads(str(persisted["late_result_json"]))["send_result"]["provider_id"] == f"provider-{late_outcome}"
    assert "reconciliation" not in json.loads(str(persisted["result_json"]))

def test_retryable_follow_up_claim_is_exclusive(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(project_id=project_id, status="draft", scheduled_at="2026-06-08 01:00:00")
    assert store.claim_follow_up_draft_revision(
        draft_id, expected_revision=1, claim_token="initial", idempotency_uuid="uuid",
        lease_owner="sender", claimed_at="2026-06-08 02:00:00", lease_until="2026-06-08 02:05:00",
    )
    assert store.transition_follow_up_attempt_to_sending(
        draft_id, claimed_revision=1, claim_token="initial", lease_owner="sender",
        now="2026-06-08 02:00:00", lease_until="2026-06-08 02:05:00",
    )
    assert store.mark_follow_up_sending_retryable(
        draft_id, draft_revision=1, claim_token="initial", lease_owner="sender",
        result_json=json.dumps({"reason": "delivery_failed", "idempotency_uuid": "uuid"}),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(
            lambda owner: store.claim_follow_up_draft_revision(
                draft_id, expected_revision=1, claim_token=owner,
                idempotency_uuid="uuid", lease_owner=owner,
                claimed_at="2026-06-08 02:06:00", lease_until="2026-06-08 02:11:00",
            ),
            ("retry-a", "retry-b"),
        ))
    assert sorted(claims) == [False, True]
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None and attempt["state"] == "claimed"

def test_repeated_delivery_failure_retries_after_explicit_revision_update(tmp_path):
    from app.dws_client import DwsError

    class SequencedDws(FakeDws):
        def __init__(self):
            super().__init__()
            self.fail_first = True

        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            if self.fail_first:
                self.fail_first = False
                raise DwsError("first delivery failed", code="1")
            return {"success": True, "result": {"openTaskId": "second"}}

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        target_conversation_id="cid-1", target_kind="group", question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    dws = SequencedDws()
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True) == 0
    failed = store.get_follow_up_draft(draft_id)
    assert failed is not None and failed.status == "failed"
    first = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert first is not None and first["state"] == "failed"
    first_uuid = first["idempotency_uuid"]
    store.update_follow_up_draft(draft_id, status="draft", question_text="修正后的问题", scheduled_at="2026-06-08 02:01:00", send_result_json="{}")
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:02:00", auto_send=True) == 1
    assert len(dws.sent) == 2 and dws.sent[1]["idempotency_uuid"] != first_uuid
    assert store.get_follow_up_draft(draft_id).status == "sent"

def test_late_provider_result_is_persisted_without_application_readback(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    draft_id = store.create_follow_up_draft(project_id=project_id, status="draft", scheduled_at="2026-06-08 01:00:00")
    assert store.claim_follow_up_draft_revision(
        draft_id, expected_revision=1, claim_token="token", idempotency_uuid="uuid",
        lease_owner="sender", claimed_at="2026-06-08 02:00:00", lease_until="2026-06-08 02:05:00",
    )
    assert store.transition_follow_up_attempt_to_sending(
        draft_id, claimed_revision=1, claim_token="token", lease_owner="sender",
        now="2026-06-08 02:00:00", lease_until="2026-06-08 02:05:00",
    )
    provider_result = json.dumps({"send_result": {"success": True, "provider_id": "provider-1"}})
    assert store.mark_follow_up_sending_retryable(
        draft_id, draft_revision=1, claim_token="token", lease_owner="sender", result_json=provider_result
    )
    attempt = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert attempt is not None
    transition = store.apply_follow_up_late_send_result(
        attempt_id=int(attempt["id"]), draft_id=draft_id, draft_revision=1,
        claim_token="token", idempotency_uuid="uuid", outcome="failed",
        result_json=json.dumps({"send_result": {"success": False, "provider_id": "provider-2"}}),
        sent_at="2026-06-08 02:06:00",
    )
    assert transition["outcome"] == "equivalent_failed"
    persisted = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert persisted is not None and persisted["state"] == "retryable"
    assert json.loads(str(persisted["late_result_json"]))["send_result"]["provider_id"] == "provider-2"

def test_corrected_revision_is_not_blocked_by_expired_old_send(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        target_conversation_id="cid-1", target_kind="group", question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    original_finalize = store.update_claimed_follow_up_draft
    monkeypatch.setattr(store, "update_claimed_follow_up_draft", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    dws = FakeDws()
    with pytest.raises(KeyboardInterrupt):
        process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True)
    old_uuid = dws.sent[0]["idempotency_uuid"]
    store.update_follow_up_draft(draft_id, question_text="修正后的问题", scheduled_at="2026-06-08 01:00:00")
    monkeypatch.setattr(store, "update_claimed_follow_up_draft", original_finalize)
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:06:00", auto_send=True) == 1
    assert len(dws.sent) == 2 and dws.sent[1]["idempotency_uuid"] != old_uuid
    old = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert old is not None and old["state"] == "invalidated"
    assert store.get_follow_up_draft(draft_id).status == "sent"

def test_failed_old_send_releases_corrected_revision(tmp_path, monkeypatch):
    from app.dws_client import DwsError
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = _create_bound_todo(store, project_id)
    draft_id = store.create_follow_up_draft(
        project_id=project_id, todo_id=todo_id, owner_user_id="owner-1",
        target_conversation_id="cid-1", target_kind="group", question_text="旧问题",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-08 01:00:00",
    )
    class Dws(FakeDws):
        def __init__(self): super().__init__(); self.first = True
        def send_message(self, *a, **k):
            self.sent.append({"text": a[1], **k})
            if self.first:
                self.first = False
                raise DwsError("send failed", code="1")
            return {"success": True, "result": {"openTaskId": "new"}}
    dws = Dws()
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:00:00", auto_send=True) == 0
    failed = store.get_follow_up_draft(draft_id)
    assert failed is not None and failed.status == "failed"
    first = store.get_follow_up_send_attempt(draft_id=draft_id, draft_revision=1)
    assert first is not None and first["state"] == "failed"
    first_uuid = first["idempotency_uuid"]
    store.update_follow_up_draft(draft_id, status="draft", question_text="修正后的问题", scheduled_at="2026-06-08 02:01:00", send_result_json="{}")
    assert process_due_follow_ups(store, dws, now="2026-06-08 02:02:00", auto_send=True) == 1
    assert len(dws.sent) == 2 and dws.sent[1]["idempotency_uuid"] != first_uuid
    assert "修正后的问题" in dws.sent[1]["text"]
    assert store.get_follow_up_draft(draft_id).status == "sent"

def test_process_due_follow_ups_can_target_one_draft_for_recovery(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = _create_bound_todo(store, project_id)
    first_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="第一条",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    second_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="第二条",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
        draft_ids=(second_id,),
    )

    assert sent == 1
    assert store.get_follow_up_draft(first_id).status == "draft"
    assert store.get_follow_up_draft(second_id).status == "sent"
    assert "第二条" in dws.sent[0]["text"]


def test_due_follow_up_does_not_close_todo_from_reply_keywords(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户交付 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P0",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="这个 P0 事项现在结果、阻塞和 ETA 分别是什么？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="客户交付群",
        trigger_message_id="msg-complete",
        trigger_sender="Alex",
        trigger_text="完成了，这块已经结束了。",
        action="no_reply",
        sensitivity_kind="general",
    )
    with store._connect() as db:
        db.execute(
            """
            update reply_attempts
            set created_at='2026-06-07 09:30:00',
                updated_at='2026-06-07 09:30:00'
            where id=?
            """,
            (attempt_id,),
        )

    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert len(dws.sent) == 1
    todo = store.get_work_todo(todo_id)
    assert todo is not None
    assert todo.status == "open"
    assert todo.completion_evidence_json == "{}"
    assert store.list_follow_up_drafts(statuses=("skipped",)) == []


def test_completion_reply_keyword_does_not_push_dingtalk_todo_done(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步验收 ETA。",
        scheduled_at="2026-06-27 01:00:00",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="客户交付群",
        trigger_message_id="msg-complete",
        trigger_sender="Alex",
        trigger_text="完成了，这块已经结束了。",
        action="no_reply",
        sensitivity_kind="general",
    )
    with store._connect() as db:
        db.execute(
            """
            update reply_attempts
            set created_at='2026-06-27 09:30:00',
                updated_at='2026-06-27 09:30:00'
            where id=?
            """,
            (attempt_id,),
        )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-29 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert len(dws.sent) == 1
    todo = store.get_work_todo(todo_id)
    assert todo is not None
    assert todo.status == "open"
    assert todo.completion_evidence_json == "{}"


def test_due_follow_up_does_not_skip_when_recent_reply_asks_for_source(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="Friday 产品落地",
        category="product",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认 Q3 客户侧前端落地计划",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="Q3 客户侧前端产品落地进展如何？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-source",
        trigger_sender="Alex",
        trigger_text="你是看了什么材料提出的这个需求？",
        action="no_reply",
        sensitivity_kind="general",
    )
    with store._connect() as db:
        db.execute(
            """
            update reply_attempts
            set created_at='2026-06-07 09:30:00',
                updated_at='2026-06-07 09:30:00'
            where id=?
            """,
            (attempt_id,),
        )

    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert len(dws.sent) == 1
    assert store.list_follow_up_drafts(statuses=("skipped",)) == []
    sent_draft = store.list_follow_up_drafts(statuses=("sent",))[0]
    assert sent_draft.id == draft_id
    assert sent_draft.suppressed_reason == ""


def test_prior_owner_send_count_does_not_defer_due_follow_up(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = _create_bound_todo(store, project_id)
    for index in range(3):
        sent_id = store.create_follow_up_draft(
            project_id=project_id,
            owner_user_id="owner-1",
            owner_name="Alex",
            target_kind="direct",
            question_text=f"已发送 {index}",
            scheduled_at="2026-06-08 01:00:00",
            status="sent",
            sent_at=f"2026-06-08 01:0{index}:00",
        )
        assert sent_id > 0
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步这个事项的最新进展。",
        scheduled_at="2026-06-07 09:00:00",
    )

    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert len(dws.sent) == 1
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "sent"
    assert draft.suppressed_reason == ""


def test_prior_group_send_count_does_not_defer_due_follow_up(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = _create_bound_todo(store, project_id)
    for index in range(8):
        store.create_follow_up_draft(
            project_id=project_id,
            owner_user_id=f"owner-{index}",
            owner_name=f"Owner {index}",
            target_conversation_id="cid-1",
            target_kind="group",
            question_text=f"已发送 {index}",
            scheduled_at="2026-06-08 01:00:00",
            status="sent",
            sent_at=f"2026-06-08 01:{index:02d}:00",
        )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步这个事项的最新进展。",
        scheduled_at="2026-06-07 09:00:00",
    )

    dws = FakeDws()
    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert len(dws.sent) == 1
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "sent"
    assert draft.suppressed_reason == ""
