from app.feishu.models import FeishuInboundMessage
from app.feishu.prompt import build_feishu_turn_prompt


def _trigger(chat_type="group"):
    return FeishuInboundMessage(
        event_id="evt_1",
        app_id="cli_test",
        message_id="om_1",
        chat_id="oc_1",
        chat_type=chat_type,
        sender_open_id="ou_1",
        sender_name="Alex",
        message_type="text",
        mentioned_bot=chat_type != "p2p",
        body_text="请处理",
        event_create_time="2026-07-22T03:20:00+00:00",
    )


def test_prompt_states_consumer_and_side_effect_boundary():
    prompt = build_feishu_turn_prompt(_trigger(), [])
    assert "cannot send a Feishu message" in prompt
    assert "do not call Memory (including recall/write)" in prompt
    assert "No tools are available" in prompt
    assert "DingTalk/DWS" in prompt
    assert "may be incomplete" in prompt


def test_prompt_bounds_context_and_marks_it_untrusted():
    context = [
        {
            "event_create_time": f"t{i}",
            "sender_name": "A",
            "body_text": f"m{i}",
        }
        for i in range(4)
    ]
    prompt = build_feishu_turn_prompt(_trigger("p2p"), context, context_limit=2)
    assert "m0" not in prompt and "m1" not in prompt
    assert "m2" in prompt and "m3" in prompt
    assert "<untrusted_feishu_context>" in prompt
    assert "direct Bot conversation" in prompt
