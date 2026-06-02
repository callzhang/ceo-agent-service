import json

import pytest

from app.cli import build_parser, feedback_spike_command
from app.feedback_spike import (
    build_callback_url,
    build_card_data,
    build_dingtalk_interactive_card_request_body,
    build_events_url,
    build_feedback_spike_card,
    normalize_vercel_base_url,
    send_feedback_spike_card,
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

    assert card_data["config"] == {"autoLayout": True, "enableForward": False}
    assert card_data["metadata"] == {
        "source": "ceo-agent-spike",
        "feedbackToken": "spike_1_abcd",
    }
    assert card_data["contents"][0] == {
        "type": "markdown",
        "text": "可以，先按这个方向试一下。",
        "id": "reply_text",
    }
    actions = card_data["contents"][1]["actions"]
    assert actions[0]["label"]["text"] == "赞"
    assert actions[0]["actionType"] == "openLink"
    assert actions[0]["url"]["all"] == (
        "https://feedback.example.com/api/dingtalk-feedback-spike"
        "?source=ceo-agent-spike&feedback_token=spike_1_abcd&rating=up"
    )
    assert actions[1]["label"]["text"] == "踩"
    assert actions[1]["actionType"] == "openLink"
    assert actions[1]["url"]["all"] == (
        "https://feedback.example.com/api/dingtalk-feedback-spike"
        "?source=ceo-agent-spike&feedback_token=spike_1_abcd&rating=down"
    )


def test_build_dingtalk_interactive_card_request_body_uses_native_card_api_shape():
    card_data = {"contents": []}

    body = build_dingtalk_interactive_card_request_body(
        conversation_id="cid-1",
        robot_code="robot-code",
        feedback_token="spike_1_abcd",
        card_data=card_data,
        card_template_id="template-1",
    )

    assert body["cardTemplateId"] == "template-1"
    assert body["openConversationId"] == "cid-1"
    assert body["cardBizId"] == "spike_1_abcd"
    assert body["robotCode"] == "robot-code"
    assert json.loads(body["cardData"]) == card_data


def test_build_feedback_spike_card_accepts_fixed_token_for_verification():
    card = build_feedback_spike_card(
        vercel_base_url="https://feedback.example.com",
        conversation_id="cid-1",
        robot_code="robot-code",
        reply_text="收到",
        feedback_token="spike_1_abcd",
    )

    assert card.feedback_token == "spike_1_abcd"
    assert "rating=up" in card.callback_url_up
    assert "rating=down" in card.callback_url_down
    assert card.request_body["cardTemplateId"] == "StandardCard"
    assert card.request_body["openConversationId"] == "cid-1"
    assert card.request_body["robotCode"] == "robot-code"


def test_send_feedback_spike_card_uses_native_dingtalk_interactive_card_api(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "app.feedback_spike.read_dingtalk_app_credentials",
        lambda path: {"DINGTALK_APP_KEY": "app-key", "DINGTALK_APP_SECRET": "app-secret"},
    )
    monkeypatch.setattr("app.feedback_spike.get_dingtalk_access_token", lambda credentials: "token-1")

    def fake_post(url, payload, *, access_token=""):
        calls.append((url, payload, access_token))
        return {"processQueryKey": "key-1"}

    monkeypatch.setattr("app.feedback_spike.post_dingtalk_json", fake_post)
    result = send_feedback_spike_card(
        vercel_base_url="https://feedback.example.com",
        conversation_id="cid-1",
        robot_code="robot-code",
        reply_text="收到",
    )

    assert result["response"] == {"processQueryKey": "key-1"}
    assert calls[0][0].endswith("/v1.0/im/v1.0/robot/interactiveCards/send")
    assert calls[0][1]["robotCode"] == "robot-code"
    assert calls[0][1]["openConversationId"] == "cid-1"
    assert calls[0][2] == "token-1"


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
            "--robot-code",
            "robot-code",
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
    assert args.robot_code == "robot-code"
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
