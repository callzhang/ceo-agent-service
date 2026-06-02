import json

import pytest

from app.cli import build_parser, feedback_spike_command
from app.feedback_spike import (
    build_callback_url,
    build_card_data,
    build_dws_send_bot_markdown_command,
    build_dws_send_card_command,
    build_dws_update_card_command,
    build_events_url,
    build_feedback_spike_card,
    build_feedback_spike_markdown_message,
    build_markdown_text,
    build_update_content,
    normalize_vercel_base_url,
    send_feedback_spike_card,
    send_feedback_spike_markdown_message,
)


def test_build_callback_url_contains_token_rating_and_source():
    url = build_callback_url(
        "https://feedback.example.com/",
        feedback_token="spike_1_abcd",
        rating="up",
    )

    assert url == (
        "https://feedback.example.com/api/dingtalk-feedback-spike"
        "?source=ceo-agent-spike&feedback_token=spike_1_abcd&rating=up"
    )


def test_build_events_url_contains_secret_and_limit():
    url = build_events_url(
        "https://feedback.example.com",
        secret="secret-1",
        limit=7,
    )

    assert url == (
        "https://feedback.example.com/api/dingtalk-feedback-spike-events"
        "?secret=secret-1&limit=7"
    )


def test_normalize_vercel_base_url_rejects_missing_scheme():
    with pytest.raises(ValueError, match="must start"):
        normalize_vercel_base_url("feedback.example.com")


def test_build_card_data_contains_two_feedback_actions():
    card_data = build_card_data(
        "可以，先按这个方向试一下。",
        vercel_base_url="https://feedback.example.com",
        feedback_token="spike_1_abcd",
    )

    assert card_data["source"] == "ceo-agent-spike"
    assert card_data["feedbackToken"] == "spike_1_abcd"
    assert card_data["msgContent"] == "可以，先按这个方向试一下。"
    assert card_data["replyText"] == "可以，先按这个方向试一下。"
    assert card_data["actions"] == [
        {
            "label": "赞",
            "rating": "up",
            "url": (
                "https://feedback.example.com/api/dingtalk-feedback-spike"
                "?source=ceo-agent-spike&feedback_token=spike_1_abcd&rating=up"
            ),
        },
        {
            "label": "踩",
            "rating": "down",
            "url": (
                "https://feedback.example.com/api/dingtalk-feedback-spike"
                "?source=ceo-agent-spike&feedback_token=spike_1_abcd&rating=down"
            ),
        },
    ]
    assert card_data["cardParamMap"]["upText"] == "赞"
    assert card_data["cardParamMap"]["downText"] == "踩"


def test_build_dws_send_card_command_uses_documented_flags():
    card_data = {"replyText": "收到", "actions": []}

    command = build_dws_send_card_command(
        conversation_id="cid-1",
        receiver_open_dingtalk_id="open-1",
        reply_text="收到",
        card_data=card_data,
        card_template_id="template-1",
        dws_bin="/bin/dws",
    )

    assert command[:8] == [
        "/bin/dws",
        "chat",
        "message",
        "send-card",
        "--group",
        "cid-1",
        "--user",
        "open-1",
    ]
    assert "--msg-content" not in command
    assert "--card-data" in command
    card_data_index = command.index("--card-data") + 1
    assert json.loads(command[card_data_index]) == card_data
    assert command[-4:] == ["--format", "json", "--card-template-id", "template-1"]


def test_build_feedback_spike_card_accepts_fixed_token_for_verification():
    card = build_feedback_spike_card(
        vercel_base_url="https://feedback.example.com",
        conversation_id="cid-1",
        receiver_open_dingtalk_id="open-1",
        reply_text="收到",
        feedback_token="spike_1_abcd",
    )

    assert card.feedback_token == "spike_1_abcd"
    assert "rating=up" in card.callback_url_up
    assert "rating=down" in card.callback_url_down
    assert "send-card" in card.command
    assert "反馈：" in card.update_content
    assert card.callback_url_up in card.update_content
    assert card.callback_url_down in card.update_content


def test_build_update_content_contains_reply_and_feedback_urls():
    content = build_update_content(
        "收到",
        up_url="https://feedback.example.com/up",
        down_url="https://feedback.example.com/down",
    )

    assert content == (
        "收到\n\n"
        "反馈：\n"
        "赞：https://feedback.example.com/up\n"
        "踩：https://feedback.example.com/down"
    )


def test_build_markdown_text_contains_two_feedback_links_on_separate_lines():
    content = build_markdown_text(
        "收到",
        up_url="https://feedback.example.com/up",
        down_url="https://feedback.example.com/down",
    )

    assert content == (
        "收到\n\n"
        "[赞](https://feedback.example.com/up)\n\n"
        "[踩](https://feedback.example.com/down)"
    )


def test_build_dws_send_bot_markdown_command_uses_robot_message_path():
    command = build_dws_send_bot_markdown_command(
        conversation_id="cid-1",
        robot_code="robot-code",
        title="CEO agent feedback",
        markdown_text="收到\n\n[赞](https://feedback.example.com/up)",
        dws_bin="/bin/dws",
    )

    assert command == [
        "/bin/dws",
        "chat",
        "message",
        "send-by-bot",
        "--group",
        "cid-1",
        "--robot-code",
        "robot-code",
        "--title",
        "CEO agent feedback",
        "--text",
        "收到\n\n[赞](https://feedback.example.com/up)",
        "--format",
        "json",
    ]


def test_build_dws_update_card_command_uses_documented_flags():
    command = build_dws_update_card_command(
        biz_id="transformer_card_1",
        content="收到",
        dws_bin="/bin/dws",
    )

    assert command == [
        "/bin/dws",
        "chat",
        "message",
        "update-card",
        "--biz-id",
        "transformer_card_1",
        "--content",
        "收到",
        "--flow-status",
        "2",
        "--format",
        "json",
    ]


def test_build_feedback_spike_markdown_message_accepts_fixed_token_for_verification():
    message = build_feedback_spike_markdown_message(
        vercel_base_url="https://feedback.example.com",
        conversation_id="cid-1",
        robot_code="robot-code",
        reply_text="收到",
        title="CEO agent feedback",
        feedback_token="spike_1_abcd",
    )

    assert message.feedback_token == "spike_1_abcd"
    assert "rating=up" in message.callback_url_up
    assert "rating=down" in message.callback_url_down
    assert "[赞](" in message.markdown_text
    assert "[踩](" in message.markdown_text
    assert "send-by-bot" in message.command


def test_send_feedback_spike_card_updates_streaming_card(monkeypatch):
    calls = []

    class Completed:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, **kwargs):
        calls.append(command)
        if "send-card" in command:
            return Completed(
                0,
                json.dumps(
                    {
                        "success": True,
                        "result": {
                            "bizId": "transformer_card_1",
                            "cardInstanceId": 123,
                        },
                    }
                ),
            )
        return Completed(0, json.dumps({"success": True, "result": {}}))

    monkeypatch.setattr("app.feedback_spike.subprocess.run", fake_run)

    result = send_feedback_spike_card(
        vercel_base_url="https://feedback.example.com",
        conversation_id="cid-1",
        receiver_open_dingtalk_id="open-1",
        reply_text="收到",
    )

    assert result["biz_id"] == "transformer_card_1"
    assert calls[0][3] == "send-card"
    assert calls[1][3] == "update-card"
    assert calls[1][calls[1].index("--biz-id") + 1] == "transformer_card_1"
    assert "反馈：" in calls[1][calls[1].index("--content") + 1]


def test_send_feedback_spike_markdown_message_sends_by_bot(monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = json.dumps({"success": True, "result": {"processQueryKey": "key-1"}})
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr("app.feedback_spike.subprocess.run", fake_run)

    result = send_feedback_spike_markdown_message(
        vercel_base_url="https://feedback.example.com",
        conversation_id="cid-1",
        robot_code="robot-code",
        reply_text="收到",
        title="CEO agent feedback",
    )

    assert result["returncode"] == 0
    assert calls[0][3] == "send-by-bot"
    assert "[赞](" in calls[0][calls[0].index("--text") + 1]
    assert "[踩](" in calls[0][calls[0].index("--text") + 1]


def test_parser_supports_feedback_spike_send_card():
    parser = build_parser()

    args = parser.parse_args(
        [
            "feedback-spike",
            "send-card",
            "--vercel-base-url",
            "https://feedback.example.com",
            "--conversation-id",
            "cid-1",
            "--receiver-open-dingtalk-id",
            "open-1",
            "--reply-text",
            "收到",
            "--card-template-id",
            "template-1",
            "--preview",
        ]
    )

    assert args.command == "feedback-spike"
    assert args.spike_action == "send-card"
    assert args.vercel_base_url == "https://feedback.example.com"
    assert args.conversation_id == "cid-1"
    assert args.receiver_open_dingtalk_id == "open-1"
    assert args.reply_text == "收到"
    assert args.card_template_id == "template-1"
    assert args.preview is True


def test_parser_supports_feedback_spike_send_markdown():
    parser = build_parser()

    args = parser.parse_args(
        [
            "feedback-spike",
            "send-markdown",
            "--vercel-base-url",
            "https://feedback.example.com",
            "--conversation-id",
            "cid-1",
            "--robot-code",
            "robot-code",
            "--reply-text",
            "收到",
            "--title",
            "CEO agent feedback",
            "--preview",
        ]
    )

    assert args.command == "feedback-spike"
    assert args.spike_action == "send-markdown"
    assert args.conversation_id == "cid-1"
    assert args.robot_code == "robot-code"
    assert args.reply_text == "收到"
    assert args.title == "CEO agent feedback"
    assert args.preview is True


def test_feedback_spike_events_url_command_prints_json(capsys):
    parser = build_parser()
    args = parser.parse_args(
        [
            "feedback-spike",
            "events-url",
            "--vercel-base-url",
            "https://feedback.example.com",
            "--secret",
            "secret-1",
            "--limit",
            "3",
        ]
    )

    result = feedback_spike_command(args)

    assert result == {
        "events_url": (
            "https://feedback.example.com/api/dingtalk-feedback-spike-events"
            "?secret=secret-1&limit=3"
        )
    }
    output = json.loads(capsys.readouterr().out)
    assert output == result


def test_feedback_spike_send_card_requires_target_args():
    parser = build_parser()
    args = parser.parse_args(
        [
            "feedback-spike",
            "send-card",
            "--vercel-base-url",
            "https://feedback.example.com",
        ]
    )

    with pytest.raises(SystemExit, match="--conversation-id"):
        feedback_spike_command(args)


def test_feedback_spike_send_markdown_requires_robot_code():
    parser = build_parser()
    args = parser.parse_args(
        [
            "feedback-spike",
            "send-markdown",
            "--vercel-base-url",
            "https://feedback.example.com",
            "--conversation-id",
            "cid-1",
            "--robot-code",
            "",
        ]
    )

    with pytest.raises(SystemExit, match="--robot-code"):
        feedback_spike_command(args)
