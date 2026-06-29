import json

from app.dws_client import DwsUserProfile
from app.follow_up import process_due_follow_ups
from app.store import AutoReplyStore


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

    def get_todo_task(self, task_id):
        return self.todo_payloads.get(task_id, {"id": task_id, "done": False})


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
    assert "结果、阻塞和 ETA" in dws.sent[0]["text"]
    sent_draft = store.list_follow_up_drafts(statuses=("sent",))[0]
    assert sent_draft.id == draft_id
    send_result = json.loads(sent_draft.send_result_json)
    assert send_result["at_users"] == ["owner-1"]
    assert send_result["at_open_dingtalk_ids"] == ["open-owner-1"]
    assert send_result["at_open_dingtalk_names"] == ["Alex"]


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


def test_due_follow_up_resolves_non_open_group_id_from_cached_source(tmp_path):
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
    store.create_follow_up_draft(
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

    assert sent == 1
    assert dws.sent[0]["conversation_id"] == "cid-open-1"
    sent_draft = store.list_follow_up_drafts(statuses=("sent",))[0]
    assert sent_draft.target_conversation_id == "cid-open-1"


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
        now="2026-06-08 02:00:00",
        auto_send=True,
        feedback_base_url="https://feedback.example.com",
    )

    assert sent == 1
    sent_text = dws.sent[0]["text"]
    assert "基于项目「客户交付」的未完成事项：" in sent_text
    assert "请同步这个事项的最新进展。" in sent_text
    assert "（by明哥分身）" in sent_text
    assert "/api/dingtalk-feedback-spike?feedback_token=" in sent_text
    send_result = json.loads(
        store.list_follow_up_drafts(statuses=("sent",))[0].send_result_json
    )
    assert send_result["feedback_token"].startswith("spike_")


def test_direct_follow_up_prefers_open_dingtalk_id_for_send_target(tmp_path):
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
        now="2026-06-12 02:00:00",
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
        now="2026-06-08 02:00:00",
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
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    skipped = store.list_follow_up_drafts(statuses=("skipped",))[0]
    assert "todo status is done" in skipped.send_result_json


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
    skipped = store.list_follow_up_drafts(statuses=("skipped",))[0]
    assert "dingtalk_todo_done" in skipped.send_result_json


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
    store.create_follow_up_draft(
        project_id=project_id,
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


def test_sensitive_group_follow_up_reroutes_to_direct_message(tmp_path):
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

    assert sent == 1
    assert dws.sent[0]["conversation_id"] is None
    assert dws.sent[0]["open_dingtalk_id"] == "open-owner-1"
    assert store.list_follow_up_drafts(statuses=("sent",))[0].id == draft_id
    sent_result = json.loads(
        store.list_follow_up_drafts(statuses=("sent",))[0].send_result_json
    )
    assert sent_result["target_kind_used"] == "direct"


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
        now="2026-06-08 02:00:00",
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
        now="2026-06-08 02:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] is None
    assert dws.sent[0]["user_id"] is None
    assert dws.sent[0]["open_dingtalk_id"] == "open-owner-1"
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


def test_due_follow_up_defers_when_owner_daily_cap_reached(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
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

    assert sent == 0
    assert dws.sent == []
    draft = [
        item
        for item in store.list_follow_up_drafts(statuses=("draft",))
        if item.id == draft_id
    ][0]
    assert draft.scheduled_at == "2026-06-09 01:00:00"
    assert draft.suppressed_reason == "owner_daily_cap"
