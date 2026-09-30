from app.wechat.models import WechatMessage
from app.wechat.prompt import build_wechat_turn_prompt


def _msg(mid, text):
    return WechatMessage(
        account_id="a", conversation_id="c1", message_id=mid, sender_id="u",
        sender_display_name="Alex", conversation_type="direct", direction="inbound",
        sent_at="2026-07-17T10:00:00+08:00", kind="text", text=text, source_version="4.1.10",
    )


def test_prompt_keeps_context_in_same_conversation():
    trigger = _msg("t", "trigger here")
    prompt = build_wechat_turn_prompt(trigger, [_msg("c", "same chat context")])
    assert "same chat" in prompt
    assert "other chat" not in prompt
    assert "memory_recall" in prompt
    assert "trigger here" in prompt


def test_prompt_caps_context_at_20():
    trigger = _msg("t", "x")
    ctx = [_msg(str(i), f"line{i}") for i in range(30)]
    prompt = build_wechat_turn_prompt(trigger, ctx)
    assert "line29" in prompt
    assert "line0" not in prompt


def test_prompt_exposes_processing_time_and_requires_freshness_check():
    trigger = _msg("t", "早点来公司")

    prompt = build_wechat_turn_prompt(
        trigger,
        [],
        current_time="2026-07-18T09:00:00+08:00",
    )

    assert "2026-07-18T09:00:00+08:00" in prompt
    assert "比较当前处理时间与消息时间" in prompt
    assert "已经失去沟通目的" in prompt


def test_manual_rerun_overrides_freshness_only_no_reply_rule():
    trigger = _msg("t", "他为啥这么贵")
    prompt = build_wechat_turn_prompt(
        trigger,
        [_msg("later", "后续上下文")],
        current_time="2026-09-26T14:39:23-07:00",
        manual_rerun=True,
    )

    assert "Derek 明确要求的手动重跑" in prompt
    assert "不要仅因为消息有延迟或后续已有上下文就返回 no_reply" in prompt
