import json

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
        title=None,
        user_id=None,
        open_dingtalk_id=None,
    ):
        self.sent.append(
            {
                "conversation_id": conversation_id,
                "text": text,
                "at_users": at_users or [],
                "title": title,
                "user_id": user_id,
                "open_dingtalk_id": open_dingtalk_id,
            }
        )
        return {"ok": True}


def test_due_low_risk_follow_up_sends_group_message(tmp_path):
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
    assert "结果、阻塞和 ETA" in dws.sent[0]["text"]
    assert store.list_follow_up_drafts(statuses=("sent",))[0].id == draft_id


def test_due_low_risk_follow_up_sends_direct_message(tmp_path):
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
        risk_check_json=json.dumps({"owner_in_group": False, "sensitive": False}),
        status="approved",
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-07 10:00:00",
        auto_send=False,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] is None
    assert dws.sent[0]["user_id"] == "owner-1"


def test_non_low_risk_follow_up_stays_draft(tmp_path):
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

    assert sent == 0
    assert store.list_follow_up_drafts(statuses=("draft",))[0].id == draft_id


def test_follow_up_failure_marks_failed_and_records_error(tmp_path):
    class BrokenDws:
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
