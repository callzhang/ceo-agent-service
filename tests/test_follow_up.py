import json

from app.dws_client import DwsUserProfile
from app.follow_up import process_due_follow_ups
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
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] == "cid-1"
    assert dws.sent[0]["at_users"] == ["owner-1"]
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-owner-1"]
    assert dws.sent[0]["at_open_dingtalk_names"] == ["Alex"]
    assert not dws.sent[0]["text"].startswith("<@")
    assert "结果、阻塞和 ETA" in dws.sent[0]["text"]
    sent_draft = store.list_follow_up_drafts(statuses=("sent",))[0]
    assert sent_draft.id == draft_id
    send_result = json.loads(sent_draft.send_result_json)
    assert send_result["at_users"] == ["owner-1"]
    assert send_result["at_open_dingtalk_ids"] == ["open-owner-1"]
    assert send_result["at_open_dingtalk_names"] == ["Alex"]


def test_due_follow_up_uses_reply_postfix_and_feedback_links(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    store.create_work_todo(
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
        now="2026-06-07 10:00:00",
        auto_send=True,
        feedback_base_url="https://feedback.example.com",
    )

    assert sent == 1
    sent_text = dws.sent[0]["text"]
    assert sent_text.startswith("请同步这个事项的最新进展。")
    assert "（by明哥分身）" in sent_text
    assert "/api/dingtalk-feedback-spike?feedback_token=" in sent_text
    send_result = json.loads(
        store.list_follow_up_drafts(statuses=("sent",))[0].send_result_json
    )
    assert send_result["feedback_token"].startswith("spike_")


def test_group_follow_up_resolves_owner_name_before_sending_at_user(tmp_path):
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
        now="2026-06-11 10:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["at_users"] == ["jack-user-1"]
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-jack-1"]
    assert dws.sent[0]["at_open_dingtalk_names"] == ["何耘光"]
    send_result = json.loads(
        store.list_follow_up_drafts(statuses=("sent",))[0].send_result_json
    )
    assert send_result["at_users"] == ["jack-user-1"]
    assert send_result["at_open_dingtalk_ids"] == ["open-jack-1"]


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
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    skipped = store.list_follow_up_drafts(statuses=("skipped",))[0]
    assert skipped.id == draft_id
    assert "todo has completion evidence" in skipped.send_result_json


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
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    skipped = store.list_follow_up_drafts(statuses=("skipped",))[0]
    assert "todo status is done" in skipped.send_result_json


def test_due_follow_up_skips_when_scheduled_more_than_seven_days_ago(tmp_path):
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
        target_kind="direct",
        question_text="请同步这个事项的最新进展。",
        scheduled_at="2026-06-01 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-09 09:00:01",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    skipped = store.list_follow_up_drafts(statuses=("skipped",))[0]
    assert skipped.id == draft_id
    result = json.loads(skipped.send_result_json)
    assert result["reason"] == "stale_due_follow_up"
    assert result["scheduled_at"] == "2026-06-01 09:00:00"


def test_draft_follow_up_sends_direct_message_when_live_send_enabled(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    store.create_follow_up_draft(
        project_id=project_id,
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
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] is None
    assert dws.sent[0]["user_id"] == "owner-1"
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-owner-1"]
    assert not dws.sent[0]["text"].startswith("<@")


def test_direct_follow_up_with_conversation_id_uses_conversation_target(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="售前圆桌",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        owner_name="Alex",
        target_conversation_id="direct-cid-1",
        target_kind="direct",
        question_text="请同步这个事项的最新进展。",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] == "direct-cid-1"
    assert dws.sent[0]["user_id"] is None
    assert dws.sent[0]["open_dingtalk_id"] is None


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
        now="2026-06-07 10:00:00",
        auto_send=False,
    )

    assert sent == 0
    assert dws.sent == []
    assert store.list_follow_up_drafts(statuses=("draft",))[0].id == draft_id


def test_group_follow_up_sends_without_risk_gate_when_target_exists(tmp_path):
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

    sent = process_due_follow_ups(
        store,
        FakeDws(),
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert store.list_follow_up_drafts(statuses=("sent",))[0].id == draft_id


def test_missing_risk_check_does_not_block_sendable_follow_up(tmp_path):
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
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] == "cid-1"
    assert store.list_follow_up_drafts(statuses=("sent",))[0].id == draft_id


def test_group_follow_up_without_group_falls_back_to_direct_message(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    store.create_follow_up_draft(
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
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] is None
    assert dws.sent[0]["user_id"] == "owner-1"
    assert dws.sent[0]["at_open_dingtalk_ids"] == ["open-owner-1"]
    assert not dws.sent[0]["text"].startswith("<@")


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
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
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
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 0
    failed = store.list_follow_up_drafts(statuses=("failed",))[0]
    assert failed.id == draft_id
    assert "send failed" in failed.send_result_json
