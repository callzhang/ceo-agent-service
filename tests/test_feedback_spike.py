import json

import pytest

from app.cli import build_parser, feedback_spike_command
from app.feedback_spike import (
    build_callback_url,
    build_card_data,
    build_dws_send_card_command,
    build_events_url,
    build_feedback_spike_card,
    normalize_vercel_base_url,
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

    assert command[:10] == [
        "/bin/dws",
        "chat",
        "message",
        "send-card",
        "--group",
        "cid-1",
        "--user",
        "open-1",
        "--msg-content",
        "收到",
    ]
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
