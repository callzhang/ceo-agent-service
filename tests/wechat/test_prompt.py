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
