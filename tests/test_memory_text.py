from app.memory_text import memory_body


FEEDBACK = (
    "反馈：[👍 有帮助](https://fb.example.test/api/dingtalk-feedback-spike"
    "?feedback_token=spike_1_ab&rating=up)｜[👎 需改进]"
    "(https://fb.example.test/api/dingtalk-feedback-spike"
    "?feedback_token=spike_1_ab&rating=down)"
)


def test_memory_body_drops_feedback_callbacks_and_signature(monkeypatch) -> None:
    monkeypatch.setenv("CEO_ASSISTANT_SIGNATURE", "（by磊哥分身）")

    text = f"先完成客户验证。\n\n再扩大投入。（by磊哥分身）\n\n{FEEDBACK}"

    assert memory_body(text) == "先完成客户验证。\n\n再扩大投入。"


def test_memory_body_drops_the_reminder_style_feedback_prefix_too() -> None:
    reminder = FEEDBACK.replace(
        "反馈：", "【反馈】这条回复有帮助吗？点一次 👍 / 👎 即可，点过后不再提示："
    )

    assert memory_body(f"结论。\n\n{reminder}") == "结论。"


def test_memory_body_keeps_ordinary_links_and_text() -> None:
    text = "方案见 [文档](https://alidocs.example.test/doc/1)。"

    assert memory_body(text) == text
