import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.cli import build_parser, feedback_spike_command
from app.dws_client import DwsClient
from app.feedback_policy import FEEDBACK_REQUIRED_LINK_PREFIX
from app.feedback_spike import (
    append_feedback_links,
    build_callback_url,
    build_events_url,
    build_feedback_link_text,
    build_feedback_spike_link_message,
    contains_forbidden_leak_outside_feedback_links,
    extract_feedback_link_context,
    normalize_vercel_base_url,
    prepare_outgoing_reply_text,
    send_feedback_spike_links,
)


def test_leak_check_trusts_only_exact_generated_feedback_callbacks():
    message = build_feedback_spike_link_message(
        vercel_base_url="https://feedback.example.com",
        reply_text="先按A方案走",
        original_text="怎么处理",
        attempt_id=42,
        feedback_token="spike_1_abcd",
    )

    assert not contains_forbidden_leak_outside_feedback_links(
        message.text,
        vercel_base_url="https://feedback.example.com",
        feedback_token=message.feedback_token,
        attempt_id=42,
    )
    assert contains_forbidden_leak_outside_feedback_links(
        message.text + "\n参考 [1]",
        vercel_base_url="https://feedback.example.com",
        feedback_token=message.feedback_token,
        attempt_id=42,
    )
    assert contains_forbidden_leak_outside_feedback_links(
        message.text,
        vercel_base_url="https://feedback.example.com",
        feedback_token=message.feedback_token,
        attempt_id=99,
    )


def test_build_callback_url_contains_token_and_rating():
    url = build_callback_url(
        "https://feedback.example.com/",
        feedback_token="spike_1_abcd",
        rating="up",
    )

    assert url == (
        "https://feedback.example.com/api/dingtalk-feedback-spike"
        "?feedback_token=spike_1_abcd&rating=up"
    )


def test_build_callback_url_carries_attempt_id():
    url = build_callback_url(
        "https://feedback.example.com/",
        feedback_token="spike_1_abcd",
        rating="up",
        attempt_id=42,
    )

    query = parse_qs(urlparse(url).query)

    assert query["attempt_id"] == ["42"]


def test_build_callback_url_carries_short_feedback_context():
    url = build_callback_url(
        "https://feedback.example.com/",
        feedback_token="spike_1_abcd",
        rating="down",
        original_text="原话",
        reply_text="回复样例",
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "feedback.example.com"
    assert parsed.path == "/api/dingtalk-feedback-spike"
    assert query["feedback_token"] == ["spike_1_abcd"]
    assert query["rating"] == ["down"]
    assert query["original_text"] == ["原话"]
    assert query["reply_text"] == ["回复样例"]


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
        "反馈：[👍 赞](https://feedback.example.com/up)"
        "｜[👎 踩](https://feedback.example.com/down)"
    )


def test_build_feedback_link_text_accepts_required_feedback_prefix():
    text = build_feedback_link_text(
        "可以，先按这个方向试一下。",
        up_url="https://feedback.example.com/up",
        down_url="https://feedback.example.com/down",
        link_prefix=FEEDBACK_REQUIRED_LINK_PREFIX,
    )

    assert text == (
        "可以，先按这个方向试一下。\n\n"
        + FEEDBACK_REQUIRED_LINK_PREFIX
        + "[👍 赞](https://feedback.example.com/up)"
        "｜[👎 踩](https://feedback.example.com/down)"
    )


def test_extract_feedback_link_context_handles_emoji_feedback_labels():
    message = build_feedback_spike_link_message(
        vercel_base_url="https://feedback.example.com",
        reply_text="收到",
        feedback_token="spike_1_abcd",
    )

    context = extract_feedback_link_context(message.text)

    assert context is not None
    assert context.feedback_token == "spike_1_abcd"
    assert context.vercel_base_url == "https://feedback.example.com"
    assert context.attempt_id == ""


def test_extract_feedback_link_context_handles_markdown_links():
    context = extract_feedback_link_context(
        "反馈：[👍](https://feedback.example.com/api/dingtalk-feedback-spike"
        "?feedback_token=spike_1_abcd&rating=up)｜[👎](https://feedback.example.com"
        "/api/dingtalk-feedback-spike?feedback_token=spike_1_abcd&rating=down)"
    )

    assert context is not None
    assert context.feedback_token == "spike_1_abcd"
    assert context.vercel_base_url == "https://feedback.example.com"


def test_append_feedback_links_does_not_duplicate_existing_links():
    first = append_feedback_links(
        vercel_base_url="https://feedback.example.com",
        reply_text="收到",
        original_text="帮我看一下",
        attempt_id=42,
        feedback_token="spike_1_abcd",
    )

    second = append_feedback_links(
        vercel_base_url="https://feedback.example.com",
        reply_text=first.text,
        original_text="帮我看一下",
        attempt_id=42,
    )

    assert second.feedback_token == "spike_1_abcd"
    assert second.text == first.text
    assert second.text.count("/api/dingtalk-feedback-spike") == 2


def test_prepare_outgoing_reply_text_applies_signature_and_feedback_once():
    prepared = prepare_outgoing_reply_text(
        reply_text="收到",
        original_text="帮我看一下",
        attempt_id=42,
        feedback_base_url="https://feedback.example.com",
        feedback_token="spike_1_abcd",
    )

    assert prepared.feedback_token == "spike_1_abcd"
    assert prepared.text.startswith("收到（by明哥分身）")
    assert prepared.text.count("（by明哥分身）") == 1
    assert prepared.text.count("/api/dingtalk-feedback-spike") == 2
    assert "attempt_id=42" in prepared.text


def test_callback_url_truncates_long_feedback_context():
    url = build_callback_url(
        "https://feedback.example.com/",
        feedback_token="spike_1_abcd",
        rating="up",
        original_text="原话" * 500,
        reply_text="回复" * 500,
    )
    query = parse_qs(urlparse(url).query)

    assert query["feedback_token"] == ["spike_1_abcd"]
    assert query["rating"] == ["up"]
    assert query["original_text"][0].startswith("原话原话")
    assert query["original_text"][0].endswith("...")
    assert len(query["original_text"][0]) <= 30
    assert query["reply_text"][0].startswith("回复回复")
    assert query["reply_text"][0].endswith("...")
    assert len(query["reply_text"][0]) <= 30


def test_callback_url_omits_context_that_would_trip_leak_check():
    url = build_callback_url(
        "https://feedback.example.com/",
        feedback_token="spike_1_abcd",
        rating="up",
        original_text="OpenAI Codex 团队案例",
        reply_text="source=memory 不应该藏进反馈链接",
        attempt_id=42,
    )
    query = parse_qs(urlparse(url).query)

    assert query["feedback_token"] == ["spike_1_abcd"]
    assert query["rating"] == ["up"]
    assert query["attempt_id"] == ["42"]
    assert "original_text" not in query
    assert "reply_text" not in query


def test_build_feedback_spike_link_message_accepts_fixed_token_for_verification():
    message = build_feedback_spike_link_message(
        vercel_base_url="https://feedback.example.com",
        reply_text="收到",
        original_text="能看一下这个方案吗？",
        attempt_id=42,
        feedback_token="spike_1_abcd",
    )

    assert message.feedback_token == "spike_1_abcd"
    assert "rating=up" in message.callback_url_up
    assert "rating=down" in message.callback_url_down
    assert message.callback_url_up in message.text
    assert message.callback_url_down in message.text
    assert "original_text=" in message.callback_url_up
    assert "reply_text=" in message.callback_url_up
    assert "attempt_id=42" in message.callback_url_up


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
            at_open_dingtalk_ids=None,
            user_id=None,
            open_dingtalk_id=None,
            title=None,
        ):
            self.sent.append(
                {
                    "conversation_id": conversation_id,
                    "text": text,
                    "at_users": at_users,
                    "at_open_dingtalk_ids": at_open_dingtalk_ids,
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
        attempt_id=42,
        conversation_id="cid-1",
        dws_client=client,
    )

    assert result["response"] == {"result": {"processQueryKey": "key-1"}}
    assert client.sent[0]["conversation_id"] == "cid-1"
    assert client.sent[0]["user_id"] is None
    assert client.sent[0]["title"] == "收到"
    assert "rating=up" in client.sent[0]["text"]
    assert "rating=down" in client.sent[0]["text"]
    assert "attempt_id=42" in client.sent[0]["text"]
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
            "--attempt-id",
            "42",
            "--preview",
        ]
    )

    assert args.command == "feedback-spike"
    assert args.spike_action == "send-links"
    assert args.vercel_base_url == "https://feedback.example.com"
    assert args.conversation_id == "cid-1"
    assert args.reply_text == "收到"
    assert args.original_text == "原话"
    assert args.attempt_id == "42"
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
