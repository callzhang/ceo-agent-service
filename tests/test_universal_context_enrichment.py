from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.dws_client import DwsCalendarEvent
from app.store import AutoReplyStore, ReplyTask
from app.universal_consumer import UniversalConsumerOutcome, UniversalConsumerResult
from app.universal_context import build_universal_context
from app.universal_executor import UniversalPlanExecution, build_universal_action_execution
from app.universal_plan import PlannedAction, PlannedActionKind, UniversalAudit, UniversalPlan
from app.universal_planner import UniversalPlanner
from app.worker import CalendarConflictContext, DingTalkAutoReplyWorker


class FakeDws:
    pass


class FakeLegacyCodex:
    timeout_seconds = 901
    idle_timeout_seconds = 900

    class Runner:
        workspace = Path("/tmp/universal-context-enrichment")
        codex_bin = "codex"

    runner = Runner()


class CapturingConsumer:
    def __init__(self) -> None:
        self.contexts = []

    def process(self, context):
        self.contexts.append(context)
        return UniversalConsumerResult(
            completed=True,
            reason="captured",
            executed_actions=(),
            outcome=UniversalConsumerOutcome.COMPLETED,
        )


def fixed_now() -> datetime:
    return datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def conversation(*, single_chat: bool = False) -> DingTalkConversation:
    return DingTalkConversation(
        open_conversation_id="cid-1",
        title="测试群",
        single_chat=single_chat,
        unread_point=1,
    )


def message(
    content: str,
    *,
    message_id: str = "msg-trigger",
    message_type: str | None = None,
    single_chat: bool = False,
) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id=message_id,
        conversation_title="测试群",
        single_chat=single_chat,
        sender_name="宇航",
        sender_open_dingtalk_id="sender-open-1",
        sender_user_id="sender-user-1",
        message_type=message_type,
        create_time="2026-07-21 09:55:00",
        content=content,
    )


def reply_task(trigger: DingTalkMessage, *, oa_url: str = "") -> ReplyTask:
    return ReplyTask(
        id=7,
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
        oa_url=oa_url,
        status="processing",
        attempts=1,
        created_at="2026-07-21 09:56:00",
        updated_at="2026-07-21 09:56:00",
    )


def make_worker(tmp_path: Path) -> DingTalkAutoReplyWorker:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_current_user_id("principal-user-1")
    return DingTalkAutoReplyWorker(
        store=store,
        dws=FakeDws(),
        codex=FakeLegacyCodex(),
        now_provider=fixed_now,
    )


def no_reply_plan(*, reason: str = "无需回复") -> UniversalPlan:
    return UniversalPlan(
        task_kind="reply",
        reason=reason,
        dependencies=["dws"],
        actions=[
            PlannedAction(
                kind=PlannedActionKind.NO_REPLY,
                reason=reason,
                target={
                    "conversation_id": "cid-1",
                    "trigger_message_id": "msg-trigger",
                },
            )
        ],
        audit=UniversalAudit(summary=reason, confidence=0.9),
    )


def test_universal_worker_enriches_calendar_context_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = make_worker(tmp_path)
    trigger = message("[日程] 晚间评审", message_type="calendar")
    old = message("前情：准备评审材料", message_id="msg-old")
    invite = DwsCalendarEvent(
        event_id="event-18-30",
        title="晚间评审",
        start_time="2026-07-21T18:30:00+08:00",
        end_time="2026-07-21T19:30:00+08:00",
        description="评审 MorningStar 产品方案",
        organizer="韩露",
        self_response_status="needsAction",
    )
    conflict = DwsCalendarEvent(
        event_id="event-conflict",
        title="经营复盘",
        start_time="2026-07-21T18:00:00+08:00",
        end_time="2026-07-21T19:00:00+08:00",
    )
    calendar_context = CalendarConflictContext(invite=invite, conflicts=[conflict])
    calendar_calls = []

    def fake_calendar_context(*args, **kwargs):
        calendar_calls.append((args, kwargs))
        return calendar_context

    consumer = CapturingConsumer()
    monkeypatch.setattr(worker, "_calendar_invite_context", fake_calendar_context)
    monkeypatch.setattr(worker, "_collect_image_paths", lambda *_: ([], []))
    monkeypatch.setattr(worker, "_universal_consumer", lambda: consumer)

    assert worker._process_universal_queued_task(
        conversation(), reply_task(trigger), trigger, [old, trigger]
    ) is True

    assert len(calendar_calls) == 1
    context = consumer.contexts[0]
    assert context.trusted_calendar_event_id == "event-18-30"
    assert context.trusted_calendar_response_status == "needsAction"
    assert context.trusted_calendar_organizer == "韩露"
    synthetic = context.context_messages[-1]
    assert synthetic.open_message_id == "msg-trigger:calendar-conflict-context"
    assert "晚间评审" in synthetic.content
    assert "2026-07-21T18:30:00+08:00" in synthetic.content
    assert "经营复盘" in synthetic.content


@pytest.mark.parametrize("resolver", ["context", "attempt"])
def test_universal_worker_freezes_oa_follow_up_target_from_existing_resolvers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolver: str,
) -> None:
    worker = make_worker(tmp_path)
    trigger = message("这个审批按刚才意见处理", single_chat=True)
    previous = message(
        "之前的审批上下文", message_id="msg-old", single_chat=True
    )
    oa_url = (
        "https://aflow.dingtalk.com/detail?"
        "procInstId=proc-follow-up&taskId=task-follow-up"
    )
    calls = {"context": 0, "attempt": 0}

    def context_override(*_):
        calls["context"] += 1
        return oa_url if resolver == "context" else ""

    def attempt_override(*_):
        calls["attempt"] += 1
        return oa_url

    consumer = CapturingConsumer()
    monkeypatch.setattr(worker, "_is_oa_approval_message", lambda _: False)
    monkeypatch.setattr(worker, "_oa_context_url_override", context_override)
    monkeypatch.setattr(worker, "_oa_follow_up_url_override", attempt_override)
    monkeypatch.setattr(worker, "_calendar_invite_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "_collect_image_paths", lambda *_: ([], []))
    monkeypatch.setattr(worker, "_universal_consumer", lambda: consumer)

    worker._process_universal_queued_task(
        conversation(single_chat=True),
        reply_task(trigger),
        trigger,
        [previous, trigger],
    )

    context = consumer.contexts[0]
    assert context.trusted_oa_process_instance_id == "proc-follow-up"
    assert context.trusted_oa_task_id == "task-follow-up"
    assert calls["context"] == 1
    assert calls["attempt"] == (1 if resolver == "attempt" else 0)


def test_universal_reply_new_messages_contains_only_trigger() -> None:
    old_minutes = message(
        "旧听记 https://alidocs.dingtalk.com/i/nodes/old-minutes",
        message_id="msg-old-minutes",
    )
    trigger = message("请回复当前问题")
    context = build_universal_context(
        conversation=conversation(),
        trigger=trigger,
        context_messages=[old_minutes, trigger],
        task_id=7,
        force_new_decision=False,
        dry_run=False,
    )
    action = PlannedAction(
        kind=PlannedActionKind.SEND_REPLY,
        reason="回复当前问题",
        sensitivity_kind="general",
        target={"conversation_id": "cid-1", "trigger_message_id": "msg-trigger"},
        payload={"text": "已处理"},
    )
    plan = UniversalPlan(
        task_kind="reply",
        reason="回复当前问题",
        dependencies=["dws"],
        actions=[action],
        audit=UniversalAudit(summary="回复当前问题", confidence=0.9),
    )
    execution = build_universal_action_execution(
        context,
        UniversalPlanExecution("scope-1", "initial", plan),
        action,
        0,
    )

    _, rebuilt_trigger, new_messages = DingTalkAutoReplyWorker._universal_reply_context(
        execution
    )

    assert rebuilt_trigger.open_message_id == "msg-trigger"
    assert [item.open_message_id for item in new_messages] == ["msg-trigger"]
    assert all("old-minutes" not in item.content for item in new_messages)


def test_universal_planner_command_passes_every_context_image(tmp_path: Path) -> None:
    planner = UniversalPlanner(workspace=tmp_path, codex_bin="codex")
    images = (str(tmp_path / "first.png"), str(tmp_path / "second.jpg"))

    command = planner._build_command(None, images)

    assert command[-7:] == [
        "--image",
        images[0],
        "--image",
        images[1],
        "--cd",
        str(tmp_path),
        "-",
    ]


def test_universal_planner_persists_actual_tool_events_in_plan_audit(
    tmp_path: Path,
) -> None:
    plan_json = no_reply_plan().model_dump_json()
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "tool_name": "xiaoqing_interview.search_candidates",
                        "arguments": {"candidate_name": "Melody"},
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": plan_json},
                },
                ensure_ascii=False,
            ),
        ]
    )
    planner = UniversalPlanner(
        workspace=tmp_path,
        executor=lambda _command, _prompt, _env: raw,
    )
    trigger = message("请检查 Melody 的面试记录")
    context = build_universal_context(
        conversation=conversation(),
        trigger=trigger,
        context_messages=[trigger],
        task_id=7,
        force_new_decision=False,
        dry_run=False,
    )

    plan = planner.plan(context)

    assert plan.audit.tool_events == [
        {
            "event_type": "item.completed",
            "tool": "xiaoqing_interview.search_candidates",
        }
    ]


def test_universal_planner_rejects_xiaoqing_unavailable_claim_without_tool_event(
    tmp_path: Path,
) -> None:
    raw = no_reply_plan(
        reason="critical_info_unavailable:xiaoqing_interview"
    ).model_dump_json()
    planner = UniversalPlanner(
        workspace=tmp_path,
        executor=lambda _command, _prompt, _env: raw,
    )
    trigger = message("请检查 Melody 的面试记录")
    context = build_universal_context(
        conversation=conversation(),
        trigger=trigger,
        context_messages=[trigger],
        task_id=7,
        force_new_decision=False,
        dry_run=False,
    )

    with pytest.raises(
        RuntimeError, match="xiaoqing_interview_required_but_not_called"
    ):
        planner.plan(context)
