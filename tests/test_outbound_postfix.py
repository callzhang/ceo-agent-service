import pytest

from app.outbound_postfix import (
    POSTFIX_VERSION,
    PreparedOutboundMessage,
    compose_outbound_postfix,
    outbound_body_echo_key,
)
from app.codex_decision import append_signature
from app.feedback_spike import prepare_outgoing_reply_text


def test_compose_appends_canonical_signature_without_feedback_when_disabled() -> None:
    prepared = compose_outbound_postfix(
        channel="wechat",
        delivery_key="wechat:message-1",
        body="已收到，会跟进。",
        original_text="请同步进度",
        feedback_base_url="",
    )

    assert prepared == PreparedOutboundMessage(
        channel="wechat",
        delivery_key="wechat:message-1",
        final_body=append_signature("已收到，会跟进。"),
        feedback_token="",
        postfix_version=POSTFIX_VERSION,
    )


def test_compose_omits_feedback_links_for_wechat_when_feedback_is_enabled() -> None:
    prepared = compose_outbound_postfix(
        channel="wechat",
        delivery_key="wechat:message-2",
        body="已收到，会跟进。",
        original_text="请同步进度",
        feedback_base_url="https://feedback.example",
    )

    assert prepared == PreparedOutboundMessage(
        channel="wechat",
        delivery_key="wechat:message-2",
        final_body=append_signature("已收到，会跟进。"),
        feedback_token="",
        postfix_version=POSTFIX_VERSION,
    )


def test_compose_rejects_blank_fields_and_unsupported_channel() -> None:
    for kwargs in (
        {"channel": "email", "delivery_key": "key", "body": "body"},
        {"channel": "dingtalk", "delivery_key": " ", "body": "body"},
        {"channel": "dingtalk", "delivery_key": "key", "body": " "},
    ):
        with pytest.raises(ValueError):
            compose_outbound_postfix(
                original_text="",
                feedback_base_url="",
                **kwargs,
            )


def test_compose_removes_recognized_feedback_pair_when_feedback_is_disabled() -> None:
    old_prepared = prepare_outgoing_reply_text(
        reply_text="已收到，会跟进。",
        original_text="请同步进度",
        feedback_base_url="https://feedback.example",
        feedback_token="spike_1234567890_ab12cd34",
    )

    prepared = compose_outbound_postfix(
        channel="dingtalk",
        delivery_key="conversation:message",
        body=old_prepared.text,
        original_text="请同步进度",
        feedback_base_url="",
    )

    assert prepared.final_body == append_signature("已收到，会跟进。")
    assert "/api/dingtalk-feedback-spike" not in prepared.final_body
    assert prepared.feedback_token == ""


def test_compose_rejects_unrecognized_feedback_callback_when_disabled() -> None:
    with pytest.raises(ValueError, match="feedback_callback_pair_invalid"):
        compose_outbound_postfix(
            channel="dingtalk",
            delivery_key="conversation:message",
            body=(
                "引用了未知链接："
                "https://untrusted.example/api/dingtalk-feedback-spike?rating=up"
            ),
            original_text="请同步进度",
            feedback_base_url="",
        )


def test_echo_key_survives_the_hard_break_dingtalk_returns() -> None:
    """DingTalk rewrites a blank line as a markdown hard break on the way back.

    Recognising our own delivery therefore cannot compare the raw text: the body
    we sent and the body we read back differ by exactly that rewrite.
    """
    sent = "【会议跟进】销售周会\n\n本次会议未形成统一规则。"
    echoed = sent.replace("\n\n", "  \n")

    assert echoed != sent
    assert outbound_body_echo_key(echoed) == outbound_body_echo_key(sent)
    assert outbound_body_echo_key("另一条消息") != outbound_body_echo_key(sent)
