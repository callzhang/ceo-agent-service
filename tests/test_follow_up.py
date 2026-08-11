import json

import pytest

from app.dws_client import DwsUserProfile
from app.follow_up import process_due_follow_ups
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


def test_expired_sending_attempt_reconciles_without_resend(tmp_path, monkeypatch):
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

    monkeypatch.setattr(
        store,
        "update_claimed_follow_up_draft",
        crash_before_result_persistence,
    )

    class ReconcilingDws(FakeDws):
        def __init__(self):
            super().__init__()
            self.verify_calls = []

        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            return {"success": True, "result": {"openTaskId": "task-1"}}

        def verify_message_send_result(self, send_result):
            self.verify_calls.append(send_result)
            return {"state": "sent", "status_result": {"sendStatus": "SUCCESS"}}

    dws = ReconcilingDws()
    with pytest.raises(KeyboardInterrupt, match="simulated crash"):
        process_due_follow_ups(
            store,
            dws,
            now="2026-06-08 02:00:00",
            auto_send=True,
        )
    assert len(dws.sent) == 1
    sending = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert sending is not None
    assert sending["state"] == "sending"
    persisted_result = json.loads(str(sending["result_json"]))["send_result"]
    assert persisted_result["result"]["openTaskId"] == "task-1"

    monkeypatch.setattr(store, "update_claimed_follow_up_draft", original_finalize)
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:06:00",
        auto_send=True,
    ) == 0
    assert len(dws.sent) == 1
    assert dws.verify_calls == [persisted_result]
    assert store.get_follow_up_draft(draft_id).status == "sent"
    reconciled = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert reconciled is not None
    assert reconciled["state"] == "sent"


def test_failed_send_readback_returns_exact_revision_to_retryable(
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
    original_finalize = store.update_claimed_follow_up_draft
    monkeypatch.setattr(
        store,
        "update_claimed_follow_up_draft",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    class FailedReadbackDws(FakeDws):
        def __init__(self):
            super().__init__()
            self.verify_calls = []

        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            return {"success": True, "result": {"openTaskId": "task-failed"}}

        def verify_message_send_result(self, send_result):
            self.verify_calls.append(send_result)
            return {"state": "failed", "status_result": {"sendStatus": "FAILED"}}

    dws = FailedReadbackDws()
    with pytest.raises(KeyboardInterrupt):
        process_due_follow_ups(
            store,
            dws,
            now="2026-06-08 02:00:00",
            auto_send=True,
        )
    first_uuid = dws.sent[0]["idempotency_uuid"]
    monkeypatch.setattr(store, "update_claimed_follow_up_draft", original_finalize)

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:06:00",
        auto_send=True,
    ) == 0
    retryable = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert retryable is not None
    assert retryable["state"] == "retryable"
    assert len(dws.sent) == 1
    assert len(dws.verify_calls) == 1

    send_dws = FakeDws()
    assert process_due_follow_ups(
        store,
        send_dws,
        now="2026-06-08 02:07:00",
        auto_send=True,
    ) == 1
    assert len(send_dws.sent) == 1
    assert send_dws.sent[0]["idempotency_uuid"] == first_uuid


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
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认交付风险",
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


def test_transport_timeout_becomes_unknown_without_blind_resend(tmp_path):
    from app.dws_client import DwsError

    class RetryableDws(FakeDws):
        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            raise DwsError(
                "dws command timed out after 30 seconds",
                retryable_external_dependency=True,
            )

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
    dws = RetryableDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert store.list_follow_up_drafts(statuses=("failed",)) == []
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "draft"
    assert draft.scheduled_at == "2026-06-07 09:00:00"
    assert draft.send_result_json == "{}"
    first_key = dws.sent[0]["idempotency_uuid"]
    assert first_key
    attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert attempt is not None
    assert attempt["state"] == "unknown"

    process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:16:00",
        auto_send=True,
    )

    assert len(dws.sent) == 1
    assert store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )["state"] == "unknown"

    store.update_follow_up_draft(
        draft_id,
        question_text="请同步修正后的进展和预计完成时间",
        scheduled_at="2026-06-08 02:16:00",
    )
    process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:17:00",
        auto_send=True,
    )

    assert len(dws.sent) == 1
    assert store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )["idempotency_uuid"] == first_key


def test_unknown_dws_send_outcome_enters_reconciliation_with_stable_uuid(
    tmp_path,
):
    from app.dws_client import DwsError

    class UnknownOutcomeDws(FakeDws):
        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            raise DwsError("dws command failed with exit code 1", code="1")

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
    dws = UnknownOutcomeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert store.list_follow_up_drafts(statuses=("failed",)) == []
    draft = store.get_follow_up_draft(draft_id)
    assert draft is not None
    assert draft.status == "draft"
    assert draft.scheduled_at == "2026-06-07 09:00:00"
    assert draft.send_result_json == "{}"
    attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert attempt is not None
    assert attempt["state"] == "unknown"
    result = json.loads(str(attempt["result_json"]))
    assert result["reason"] == "dws_send_outcome_unknown"
    assert result["claimed_revision"] == 1
    assert result["idempotency_uuid"] == dws.sent[0]["idempotency_uuid"]
    assert result["idempotency_uuid"]
    assert attempt["idempotency_uuid"] == result["idempotency_uuid"]


def test_correction_holds_new_revision_while_old_send_outcome_is_unknown(tmp_path):
    from app.dws_client import DwsError

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

    class UnknownOutcomeAfterCorrectionDws(FakeDws):
        def send_message(self, *args, **kwargs):
            self.sent.append(kwargs)
            store.update_follow_up_draft(
                draft_id,
                question_text="修正后的问题",
                scheduled_at="2026-06-09 02:00:00",
            )
            raise DwsError("dws command failed with exit code 1", code="1")

    dws = UnknownOutcomeAfterCorrectionDws()
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:00:00",
        auto_send=True,
    ) == 0

    corrected = store.get_follow_up_draft(draft_id)
    assert corrected is not None
    assert corrected.status == "draft"
    assert corrected.question_text == "修正后的问题"
    assert corrected.scheduled_at == "2026-06-09 02:00:00"
    assert corrected.send_result_json == "{}"
    attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert attempt is not None
    assert attempt["state"] == "unknown"
    attempt_result = json.loads(str(attempt["result_json"]))
    assert attempt_result["reason"] == "dws_send_outcome_unknown"
    old_uuid = attempt["idempotency_uuid"]

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-09 02:00:00",
        auto_send=True,
    ) == 0
    assert len(dws.sent) == 1
    unresolved = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert unresolved is not None
    assert unresolved["state"] == "unknown"
    assert unresolved["idempotency_uuid"] == old_uuid
    assert store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=2,
    ) is None


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
                scheduled_at="2026-06-08 01:00:00",
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
    assert corrected.send_result_json == "{}"

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
    assert held.suppressed_reason == (
        "prior_revision_delivered_requires_agent_review"
    )
    reviewed_attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert reviewed_attempt is not None
    assert reviewed_attempt["state"] == "sent_review_enqueued"
    queued = store.claim_work_summary_inputs(limit=2)
    assert len(queued) == 1
    assert queued[0].source_ref == f"follow-up-repair:{draft_id}:prior-delivery:1"
    work_item = json.loads(queued[0].payload_json)
    summary = json.loads(work_item["summary"])
    evidence = summary["delivery_evidence"]
    assert evidence["prior_revision"] == 1
    assert evidence["prior_idempotency_uuid"] == old_uuid
    assert evidence["old_content_delivery_proven"] is True
    assert evidence["current_question_text"] == "修正后的问题"
    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-09 02:01:00",
        auto_send=True,
    ) == 0
    assert len(dws.sent) == 1


def test_corrected_revision_waits_for_old_confirmed_sent_reconciliation(
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
    original_finalize = store.update_claimed_follow_up_draft
    monkeypatch.setattr(
        store,
        "update_claimed_follow_up_draft",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    class ConfirmedSentDws(FakeDws):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            return {"success": True, "result": {"openTaskId": "confirmed-sent"}}

        def verify_message_send_result(self, send_result):
            return {"state": "sent", "status_result": {"sendStatus": "SUCCESS"}}

    dws = ConfirmedSentDws()
    with pytest.raises(KeyboardInterrupt):
        process_due_follow_ups(
            store,
            dws,
            now="2026-06-08 02:00:00",
            auto_send=True,
        )
    old_uuid = dws.sent[0]["idempotency_uuid"]
    store.update_follow_up_draft(
        draft_id,
        question_text="修正后的问题",
        scheduled_at="2026-06-08 01:00:00",
    )
    monkeypatch.setattr(store, "update_claimed_follow_up_draft", original_finalize)

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:06:00",
        auto_send=True,
    ) == 0
    assert len(dws.sent) == 1
    old_attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert old_attempt is not None
    assert old_attempt["state"] == "sent_review_enqueued"
    assert old_attempt["idempotency_uuid"] == old_uuid
    reconciliation = json.loads(str(old_attempt["result_json"]))["reconciliation"]
    assert reconciliation["state"] == "sent"
    held = store.get_follow_up_draft(draft_id)
    assert held is not None
    assert held.question_text == "修正后的问题"
    assert held.status == "draft"
    assert held.suppressed_reason == (
        "prior_revision_delivered_requires_agent_review"
    )
    assert store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=2,
    ) is None
    assert len(store.claim_work_summary_inputs(limit=2)) == 1


def test_old_confirmed_not_sent_releases_corrected_revision(tmp_path, monkeypatch):
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
    original_finalize = store.update_claimed_follow_up_draft
    monkeypatch.setattr(
        store,
        "update_claimed_follow_up_draft",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    class ConfirmedFailedDws(FakeDws):
        def send_message(self, *args, **kwargs):
            super().send_message(*args, **kwargs)
            return {"success": True, "result": {"openTaskId": "confirmed-failed"}}

        def verify_message_send_result(self, send_result):
            return {"state": "failed", "status_result": {"sendStatus": "FAILED"}}

    dws = ConfirmedFailedDws()
    with pytest.raises(KeyboardInterrupt):
        process_due_follow_ups(
            store,
            dws,
            now="2026-06-08 02:00:00",
            auto_send=True,
        )
    old_uuid = dws.sent[0]["idempotency_uuid"]
    store.update_follow_up_draft(
        draft_id,
        question_text="修正后的问题",
        scheduled_at="2026-06-08 01:00:00",
    )
    monkeypatch.setattr(store, "update_claimed_follow_up_draft", original_finalize)

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:06:00",
        auto_send=True,
    ) == 0
    assert len(dws.sent) == 1
    old_attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=1,
    )
    assert old_attempt is not None
    assert old_attempt["state"] == "not_sent"
    assert old_attempt["idempotency_uuid"] == old_uuid
    assert json.loads(str(old_attempt["result_json"]))["reconciliation"]["state"] == (
        "failed"
    )

    assert process_due_follow_ups(
        store,
        dws,
        now="2026-06-08 02:07:00",
        auto_send=True,
    ) == 1
    assert len(dws.sent) == 2
    assert dws.sent[1]["idempotency_uuid"] != old_uuid
    assert "修正后的问题" in dws.sent[1]["text"]
    current_attempt = store.get_follow_up_send_attempt(
        draft_id=draft_id,
        draft_revision=2,
    )
    assert current_attempt is not None
    assert current_attempt["state"] == "sent"
    assert store.claim_work_summary_inputs(limit=2) == []


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
