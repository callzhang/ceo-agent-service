from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.dws_client import DwsMinutesPermissionRequest
from app.store import AutoReplyStore
from app.universal_context import build_universal_context
from app.universal_executor import (
    UniversalActionExecutor,
    UniversalPlanExecution,
    build_universal_action_execution,
)
from app.universal_plan import (
    PlannedAction,
    PlannedActionKind,
    UniversalAudit,
    UniversalPlan,
)
from app.worker import DingTalkAutoReplyWorker


class RecordingPlanner:
    def __init__(self) -> None:
        self.calls = []
        self.last_session_id = None

    def plan(self, context, session_id=None):
        self.calls.append((context, session_id))
        return UniversalPlan(
            task_kind="reply",
            reason="fallback plan",
            dependencies=["dws"],
            actions=[
                PlannedAction(
                    kind=PlannedActionKind.NO_REPLY,
                    reason="fallback no reply",
                    target={
                        "conversation_id": context.conversation_id,
                        "trigger_message_id": context.trigger_message_id,
                    },
                    payload={},
                )
            ],
            audit=UniversalAudit(summary="fallback", confidence=0.9),
        )


class FakeDws:
    def __init__(self, trigger: DingTalkMessage) -> None:
        self.trigger = trigger
        self.minutes_permission_request = None
        self.added_minutes_permissions = []

    def auth_status(self):
        return {
            "authenticated": True,
            "token_valid": True,
            "refresh_token_valid": True,
        }

    def list_messages_by_ids(self, message_ids):
        assert message_ids == [self.trigger.open_message_id]
        return [self.trigger]

    def read_recent_messages(self, conversation):
        return [self.trigger]

    def read_unread_messages(self, conversation):
        return [self.trigger]

    def minutes_permission_request_from_message(self, message):
        assert message.open_message_id == self.trigger.open_message_id
        return self.minutes_permission_request

    def add_minutes_member_permission(self, request):
        self.added_minutes_permissions.append(request)
        return {"success": True}


class FakeLegacyCodex:
    timeout_seconds = 901
    idle_timeout_seconds = 900

    class Runner:
        workspace = Path("/tmp/universal-parity-workspace")
        codex_bin = "codex"

    runner = Runner()


def fixed_now() -> datetime:
    return datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def conversation() -> DingTalkConversation:
    return DingTalkConversation(
        open_conversation_id="cid-1",
        title="测试群",
        single_chat=False,
        unread_point=1,
    )


def message(*, content="请处理", message_type=None, raw_payload=None):
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="msg-1",
        conversation_title="测试群",
        single_chat=False,
        sender_name="宇航",
        sender_open_dingtalk_id="sender-open-1",
        sender_user_id="sender-user-1",
        message_type=message_type,
        create_time="2026-07-21 09:55:00",
        content=content,
        raw_payload=raw_payload or {},
    )


def make_worker(tmp_path, monkeypatch, trigger, planner):
    monkeypatch.delenv("CEO_UNIVERSAL_CONSUMER", raising=False)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("principal-user-1")
    dws = FakeDws(trigger)
    worker = DingTalkAutoReplyWorker(
        store=store,
        dws=dws,
        codex=FakeLegacyCodex(),
        now_provider=fixed_now,
        universal_planner=planner,
    )
    worker.store.upsert_conversation("cid-1", "测试群", False, None)
    assert worker.store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="测试群",
        single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    task = worker.store.list_reply_tasks(limit=1)[0]
    return worker, dws, task


def test_default_universal_auto_approves_structured_minutes_request_before_planner(
    tmp_path, monkeypatch
):
    trigger = message(
        content="[AI 听记权限申请]",
        raw_payload={"permissionRequest": {"taskUuid": "minutes-1"}},
    )
    planner = RecordingPlanner()
    worker, dws, task = make_worker(tmp_path, monkeypatch, trigger, planner)
    request = DwsMinutesPermissionRequest(
        uuids=["minutes-1"],
        member_uids=[451416406],
        policy_id=3,
        role_sub_resource_ids=["OrigContent", "Summary"],
        cover_permission=False,
    )
    dws.minutes_permission_request = request
    handler = Mock(wraps=worker._handle_minutes_permission_request_if_actionable)
    monkeypatch.setattr(
        worker,
        "_handle_minutes_permission_request_if_actionable",
        handler,
    )

    assert worker._process_queued_task(conversation(), task) is True

    handler.assert_called_once()
    assert planner.calls == []
    assert dws.added_minutes_permissions == [request]
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.codex_reason == "ai_minutes_permission_auto_approved"


def test_default_universal_skips_system_notification_before_planner(
    tmp_path, monkeypatch
):
    trigger = message(content="[图片]", message_type="image")
    planner = RecordingPlanner()
    worker, _, task = make_worker(tmp_path, monkeypatch, trigger, planner)

    assert worker._process_queued_task(conversation(), task) is True

    assert planner.calls == []
    assert worker.store.has_seen("msg-1") is True
    attempt = worker.store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.codex_reason == "system_or_notification_message"


def test_queue_okr_review_plan_dispatches_to_worker_executor():
    trigger = message(content="请审核我的 OKR")
    context = build_universal_context(
        conversation=conversation(),
        trigger=trigger,
        context_messages=[trigger],
        task_id=7,
        force_new_decision=False,
        dry_run=False,
        execution_generation="initial",
    )
    action = PlannedAction(
        kind=PlannedActionKind("queue_okr_review"),
        reason="发起 OKR 审核",
        target={"conversation_id": "cid-1", "trigger_message_id": "msg-1"},
        payload={},
    )
    plan = UniversalPlan(
        task_kind="okr_review",
        reason="需要读取 OKR 数据后审核",
        dependencies=["dws"],
        actions=[action],
        audit=UniversalAudit(summary="结构化 OKR 审核请求", confidence=0.95),
    )
    plan_execution = UniversalPlanExecution(
        execution_scope_id="scope-1",
        execution_generation="initial",
        plan=plan,
    )
    execution = build_universal_action_execution(
        context,
        plan_execution,
        action,
        0,
    )

    worker = Mock()
    worker.execute_universal_okr_review.return_value = True

    assert UniversalActionExecutor(worker).execute(execution) is True
    worker.execute_universal_okr_review.assert_called_once_with(execution)
