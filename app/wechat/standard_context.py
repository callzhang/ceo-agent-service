"""Build the standard pipeline's task context for a WeChat reply task.

The standard Consumer -> Audit -> SystemExecutor flow reads an
``AgentTaskContext``; this builds one from the persisted WeChat trigger and the
recent conversation read through the Reader. The only target a WeChat reply may
have is the conversation that triggered it, so it is stated here once as the
task-bound contract and enforced when the candidate is prepared.
"""

from __future__ import annotations

from app.agent_context import AgentContextMessage, AgentTaskContext
from app.wechat.models import WechatAccount, WechatMessage

CONTEXT_MESSAGE_LIMIT = 20


def wechat_trigger_message(task, account: WechatAccount) -> WechatMessage:
    raw = task.trigger_message_json
    if raw and raw != "{}":
        try:
            return WechatMessage.model_validate_json(raw)
        except Exception:
            pass
    return WechatMessage(
        account_id=account.account_id,
        conversation_id=task.conversation_id,
        message_id=task.trigger_message_id,
        sender_id="",
        sender_display_name=task.trigger_sender,
        conversation_type="direct" if task.single_chat else "group",
        direction="inbound",
        sent_at=task.trigger_create_time,
        kind="text",
        text=task.trigger_text,
        source_version=account.app_version,
    )


def wechat_reply_target(task, trigger: WechatMessage, account: WechatAccount) -> dict[str, str]:
    """The one target a reply to this task may have."""
    return {
        "account_id": account.account_id,
        "target_type": "direct" if task.single_chat else "group",
        "target_id": trigger.conversation_id,
        "conversation_id": trigger.conversation_id,
    }


def build_wechat_task_context(task, *, reader, account: WechatAccount) -> AgentTaskContext:
    trigger = wechat_trigger_message(task, account)
    recent: list[WechatMessage] = []
    if reader is not None:
        try:
            recent = list(reader.read_messages(
                account, conversation_id=trigger.conversation_id,
                conversation_type=trigger.conversation_type, limit=CONTEXT_MESSAGE_LIMIT,
            ))
        except Exception:
            recent = []
    try:
        from app.wechat.article import enrich_context

        recent = list(enrich_context(recent))
    except Exception:
        pass
    messages = tuple(
        AgentContextMessage(
            message_id=message.message_id, sender=message.sender_display_name,
            text=message.text, create_time=message.sent_at,
        )
        for message in recent[-CONTEXT_MESSAGE_LIMIT:]
    )
    target = wechat_reply_target(task, trigger, account)
    return AgentTaskContext(
        task_id=task.id,
        channel="wechat",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        trigger_create_time=task.trigger_create_time,
        messages=messages,
        materials=(),
        prior_receipts=(),
        trigger_raw_payload={
            "wechat": {"account_id": account.account_id, "conversation_type": trigger.conversation_type},
        },
        required_proposal_action={
            "capability": "wechat",
            "operation": "send_message",
            "target": target,
            "payload": {"text": "<the reply to send, exact text>"},
        },
    )
