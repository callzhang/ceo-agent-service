"""Channel-specific prompt for WeChat turns.

No DingTalk/DWS assumptions: WeChat replies are plain text, decided from
same-conversation context plus durable memory recall. The agent must return the
existing AgentEnvelope and must not request DingTalk-only system actions.
"""
from __future__ import annotations

from app.wechat.models import WechatMessage

WECHAT_TURN_INSTRUCTIONS = """- This is a selected personal WeChat conversation.
- Use memory_recall for relevant durable history; never write Memory here.
- Return only the existing AgentEnvelope.
- Allowed user modes: send_reply, ask_clarifying_question, handoff_to_human, no_reply.
- Do not request DingTalk-only system actions, reactions, documents, OA, calendar, or DING.
- Group context that did not mention the principal is background only."""


def build_wechat_turn_prompt(
    trigger: WechatMessage, context: list[WechatMessage]
) -> str:
    lines = [WECHAT_TURN_INSTRUCTIONS, "", "同一对话最近上下文（最多 20 条）:"]
    for message in context[-20:]:
        lines.append(f"[{message.sent_at}] {message.sender_display_name}: {message.text}")
    lines += [
        "",
        "需要处理的触发消息:",
        f"[{trigger.sent_at}] {trigger.sender_display_name}: {trigger.text}",
    ]
    return "\n".join(lines)
