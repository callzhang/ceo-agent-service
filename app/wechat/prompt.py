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
- Group context that did not mention the principal is background only.
- 比较当前处理时间与消息时间；如果延迟回复已经失去沟通目的，或会让对方误以为动作仍会及时发生，返回 no_reply。"""


def build_wechat_turn_prompt(
    trigger: WechatMessage,
    context: list[WechatMessage],
    *,
    current_time: str = "",
    manual_rerun: bool = False,
) -> str:
    instructions = WECHAT_TURN_INSTRUCTIONS
    if manual_rerun:
        instructions += (
            "\n- 这是 Derek 明确要求的手动重跑；请重新回答原触发消息。"
            "不要仅因为消息有延迟或后续已有上下文就返回 no_reply；"
            "把后续消息作为补充上下文，仍针对原问题给出当前可用的回复。"
        )
    lines = [
        instructions,
        "",
        f"当前处理时间: {current_time}",
        "",
        "同一对话最近上下文（最多 20 条）:",
    ]
    for message in context[-20:]:
        lines.append(f"[{message.sent_at}] {message.sender_display_name}: {message.text}")
    lines += [
        "",
        "需要处理的触发消息:",
        f"[{trigger.sent_at}] {trigger.sender_display_name}: {trigger.text}",
    ]
    return "\n".join(lines)
