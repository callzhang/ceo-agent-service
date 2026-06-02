import json

import pytest

from app.cli import build_parser, feedback_spike_command
from app.dws_client import DwsClient
from app.feedback_spike import (
    build_callback_url,
    build_events_url,
    build_feedback_link_text,
    build_feedback_spike_link_message,
    normalize_vercel_base_url,
    send_feedback_spike_links,
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


def test_build_callback_url_can_include_feedback_context():
    url = build_callback_url(
        "https://feedback.example.com/",
        feedback_token="spike_1_abcd",
        rating="down",
        original_text="原话",
        reply_text="回复样例",
    )

    assert "rating=down" in url
    assert "original_text=%E5%8E%9F%E8%AF%9D" in url
    assert "reply_text=%E5%9B%9E%E5%A4%8D%E6%A0%B7%E4%BE%8B" in url


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


def test_build_feedback_link_text_contains_two_feedback_urls():
    text = build_feedback_link_text(
        "可以，先按这个方向试一下。",
        up_url="https://feedback.example.com/up",
        down_url="https://feedback.example.com/down",
    )

    assert text == (
        "可以，先按这个方向试一下。\n\n"
        "反馈：赞 https://feedback.example.com/up  踩 https://feedback.example.com/down"
    )


def test_build_feedback_spike_link_message_accepts_fixed_token_for_verification():
    message = build_feedback_spike_link_message(
        vercel_base_url="https://feedback.example.com",
        reply_text="收到",
        original_text="能看一下这个方案吗？",
        feedback_token="spike_1_abcd",
    )

    assert message.feedback_token == "spike_1_abcd"
    assert "rating=up" in message.callback_url_up
    assert "rating=down" in message.callback_url_down
    assert message.callback_url_up in message.text
    assert message.callback_url_down in message.text
    assert "original_text=" in message.callback_url_up
    assert "reply_text=" in message.callback_url_up


def test_send_feedback_spike_links_uses_current_user_message_path():
    class RecordingDwsClient(DwsClient):
        def __init__(self):
            super().__init__(dws_bin="dws")
            self.sent = []

        def send_message(
            self,
            conversation_id,
            text,
            at_users=None,
            user_id=None,
            open_dingtalk_id=None,
            title=None,
        ):
            self.sent.append(
                {
                    "conversation_id": conversation_id,
                    "text": text,
                    "at_users": at_users,
                    "user_id": user_id,
                    "open_dingtalk_id": open_dingtalk_id,
                    "title": title,
                }
            )
            return {"result": {"processQueryKey": "key-1"}}

    client = RecordingDwsClient()

    result = send_feedback_spike_links(
        vercel_base_url="https://feedback.example.com",
        reply_text="收到",
        original_text="帮我看看这个方案",
        conversation_id="cid-1",
        dws_client=client,
    )

    assert result["response"] == {"result": {"processQueryKey": "key-1"}}
    assert client.sent[0]["conversation_id"] == "cid-1"
    assert client.sent[0]["user_id"] is None
    assert client.sent[0]["title"] == "收到"
    assert "rating=up" in client.sent[0]["text"]
    assert "rating=down" in client.sent[0]["text"]
    assert result["command"][3] == "send"
    assert "--group" in result["command"]
    assert result["command"][result["command"].index("--title") + 1] == "收到"
    assert "send-by-bot" not in result["command"]


def test_parser_supports_feedback_spike_send_links():
    parser = build_parser()

    args = parser.parse_args(
        [
            "feedback-spike",
            "send-links",
            "--vercel-base-url",
            "https://feedback.example.com",
            "--conversation-id",
            "cid-1",
            "--reply-text",
            "收到",
            "--original-text",
            "原话",
            "--preview",
        ]
    )

    assert args.command == "feedback-spike"
    assert args.spike_action == "send-links"
    assert args.vercel_base_url == "https://feedback.example.com"
    assert args.conversation_id == "cid-1"
    assert args.reply_text == "收到"
    assert args.original_text == "原话"
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


def test_feedback_spike_send_links_requires_exactly_one_target():
    parser = build_parser()
    args = parser.parse_args(
        [
            "feedback-spike",
            "send-links",
            "--vercel-base-url",
            "https://feedback.example.com",
        ]
    )

    with pytest.raises(SystemExit, match="exactly one"):
        feedback_spike_command(args)


def test_feedback_spike_send_links_supports_direct_user_preview():
    parser = build_parser()
    args = parser.parse_args(
        [
            "feedback-spike",
            "send-links",
            "--vercel-base-url",
            "https://feedback.example.com",
            "--user-id",
            "user-1",
            "--reply-text",
            "收到",
            "--preview",
        ]
    )

    result = feedback_spike_command(args)

    assert result["preview"] is True
    assert "--user" in result["command"]
    assert "user-1" in result["command"]
