from pathlib import Path

from app.dingtalk_models import DingTalkMessage
from app.oa_notification_routing import (
    OaNotificationKind,
    classify_oa_notification,
    oa_case_key,
    oa_node_key,
    render_oa_result_reply,
)
from app.store import AutoReplyStore


def _message(*, sender_name: str, content: str) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="cid",
        open_message_id="mid",
        conversation_title="审批提醒",
        single_chat=True,
        sender_name=sender_name,
        sender_open_dingtalk_id="sender-open-id",
        sender_user_id="sender-user-id",
        create_time="2026-09-25T10:00:00+08:00",
        content=content,
    )


def test_system_oa_notification_is_context_only():
    route = classify_oa_notification(
        _message(
            sender_name="OA审批",
            content="张三提交了采购审批 https://aflow.dingtalk.com/dingflow?procInstId=proc-1",
        ),
        "https://aflow.dingtalk.com/dingflow?procInstId=proc-1",
    )

    assert route is not None
    assert route.kind is OaNotificationKind.SYSTEM_NOTIFICATION
    assert route.process_instance_id == "proc-1"
    assert route.task_id == ""
    assert route.requires_reply is False


def test_chat_reminder_is_saved_as_reply_target_not_agent_input():
    route = classify_oa_notification(
        _message(
            sender_name="张静",
            content="[Ding]张静提醒您审批他的录用申请 https://aflow.dingtalk.com/dingflow?procInstId=proc-2",
        ),
        "https://aflow.dingtalk.com/dingflow?procInstId=proc-2",
    )

    assert route is not None
    assert route.kind is OaNotificationKind.CHAT_REMINDER
    assert route.requires_reply is True
    assert route.process_instance_id == "proc-2"
    assert route.task_id == ""


def test_non_oa_chat_message_is_not_captured():
    assert (
        classify_oa_notification(
            _message(sender_name="张静", content="请看一下这个项目方案"),
            "",
        )
        is None
    )


def test_chat_reminder_without_link_still_does_not_create_agent_input():
    route = classify_oa_notification(
        _message(sender_name="张静", content="[Ding]张静提醒您审批他的录用申请"),
        "",
    )

    assert route is not None
    assert route.kind is OaNotificationKind.CHAT_REMINDER
    assert route.process_instance_id == ""


def test_oa_keys_separate_case_from_resolved_task_node():
    assert oa_case_key("proc-3") == "oa:proc-3"
    assert oa_node_key("proc-3", "task-9") == "oa:proc-3:task-9"
    assert oa_node_key("proc-3", "") == "oa:proc-3"


def test_result_reply_contains_confirmed_action_and_link():
    body = render_oa_result_reply(
        process_instance_id="proc-4",
        task_id="task-4",
        action="approve",
        status="done",
        summary="已完成审批并回读到钉钉结果",
        oa_url="https://aflow.dingtalk.com/dingflow?procInstId=proc-4&taskId=task-4",
    )

    assert "审批结果" in body
    assert "通过" in body
    assert "https://aflow.dingtalk.com" in body


def test_oa_notification_event_is_idempotent_and_can_be_adopted(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "oa-events.sqlite3")
    payload = _message(
        sender_name="张静",
        content=(
            "[Ding]张静提醒您审批他的录用申请 "
            "https://aflow.dingtalk.com/dingflow?procInstId=proc-5"
        ),
    )
    event_id = store.record_oa_notification_event(
        kind=OaNotificationKind.CHAT_REMINDER,
        process_instance_id="proc-5",
        task_id="",
        channel="dingtalk",
        conversation_id=payload.open_conversation_id,
        conversation_title=payload.conversation_title,
        trigger_message_id=payload.open_message_id,
        trigger_create_time=payload.create_time,
        trigger_sender=payload.sender_name,
        trigger_text=payload.content,
        trigger_message_json=payload.model_dump_json(),
        oa_url="https://aflow.dingtalk.com/dingflow?procInstId=proc-5",
    )
    assert store.record_oa_notification_event(
        kind=OaNotificationKind.CHAT_REMINDER,
        process_instance_id="proc-5",
        task_id="",
        channel="dingtalk",
        conversation_id=payload.open_conversation_id,
        conversation_title=payload.conversation_title,
        trigger_message_id=payload.open_message_id,
        trigger_create_time=payload.create_time,
        trigger_sender=payload.sender_name,
        trigger_text=payload.content,
        trigger_message_json=payload.model_dump_json(),
        oa_url="https://aflow.dingtalk.com/dingflow?procInstId=proc-5",
    ) == event_id
    assert store.adopt_oa_notification_events("proc-5", "task-5") == 1
    targets = store.list_pending_oa_reminder_targets("proc-5", "task-5")
    assert len(targets) == 1
    assert targets[0]["task_id"] == "task-5"


def test_oa_reminder_claim_and_sent_transition_is_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "oa-events.sqlite3")
    event_id = store.record_oa_notification_event(
        kind=OaNotificationKind.CHAT_REMINDER,
        process_instance_id="proc-6",
        task_id="task-6",
        channel="dingtalk",
        conversation_id="cid-6",
        conversation_title="审批",
        trigger_message_id="mid-6",
        trigger_create_time="2026-09-25T10:00:00+08:00",
        trigger_sender="张静",
        trigger_text="催办",
        trigger_message_json="{}",
        oa_url="https://aflow.dingtalk.com/dingflow?procInstId=proc-6&taskId=task-6",
    )

    assert store.claim_oa_reminder_target(event_id, 91) is True
    assert store.claim_oa_reminder_target(event_id, 92) is False
    assert store.mark_oa_reminder_sent(event_id, 91, 123) is True
    assert store.mark_oa_reminder_sent(event_id, 91, 123) is False
    assert store.list_pending_oa_reminder_targets("proc-6", "task-6") == []
