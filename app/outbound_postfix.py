"""Immutable, channel-neutral preparation for outbound service messages."""

from dataclasses import dataclass

from app.feedback_spike import (
    extract_configured_feedback_link_context,
    extract_feedback_link_context,
    prepare_outgoing_reply_text,
    sanitize_configured_feedback_links,
)


POSTFIX_VERSION = "1"
SUPPORTED_OUTBOUND_CHANNELS = frozenset({"dingtalk", "wechat"})
_SANITIZED_FEEDBACK_LINK_MARKER = "\n\n[service-generated feedback callbacks]"


@dataclass(frozen=True)
class PreparedOutboundMessage:
    channel: str
    delivery_key: str
    final_body: str
    feedback_token: str
    postfix_version: str


def compose_outbound_postfix(
    *,
    channel: str,
    delivery_key: str,
    body: str,
    original_text: str,
    feedback_base_url: str,
) -> PreparedOutboundMessage:
    """Apply the canonical service signature and optional feedback links once."""
    normalized_channel, normalized_delivery_key = normalize_outbound_postfix_inputs(
        channel=channel,
        delivery_key=delivery_key,
        body=body,
    )
    channel_feedback_base_url = (
        "" if normalized_channel == "wechat" else feedback_base_url
    )
    prepared = prepare_outgoing_reply_text(
        reply_text=_normalize_disabled_feedback_body(
            body,
            channel_feedback_base_url,
        ),
        original_text=original_text,
        feedback_base_url=channel_feedback_base_url,
    )
    return PreparedOutboundMessage(
        channel=normalized_channel,
        delivery_key=normalized_delivery_key,
        final_body=prepared.text,
        feedback_token=prepared.feedback_token,
        postfix_version=POSTFIX_VERSION,
    )


def outbound_body_echo_key(body: str) -> str:
    """Key a body by what survives the channel rendering it and handing it back.

    DingTalk returns a message we sent with its line breaks rewritten: the blank
    line we send between paragraphs comes back as a markdown hard break. The
    text we stored and the text we read back are therefore equal only once every
    run of whitespace is collapsed to one space.
    """
    return " ".join(body.split())


def normalize_outbound_postfix_inputs(
    *,
    channel: str,
    delivery_key: str,
    body: str,
) -> tuple[str, str]:
    """Validate the immutable identity before looking up a prior preparation."""
    normalized_channel = channel.strip()
    normalized_delivery_key = delivery_key.strip()
    if normalized_channel not in SUPPORTED_OUTBOUND_CHANNELS:
        raise ValueError("unsupported outbound channel")
    if not normalized_delivery_key:
        raise ValueError("delivery key is required")
    if not body.strip():
        raise ValueError("outbound body is required")
    return normalized_channel, normalized_delivery_key


def _normalize_disabled_feedback_body(body: str, feedback_base_url: str) -> str:
    if feedback_base_url:
        return body
    context = extract_feedback_link_context(body)
    if context is None:
        if "/api/dingtalk-feedback-spike" in body:
            raise ValueError("feedback_callback_pair_invalid")
        return body
    if (
        extract_configured_feedback_link_context(
            body,
            vercel_base_url=context.vercel_base_url,
        )
        is None
    ):
        raise ValueError("feedback_callback_pair_invalid")
    sanitized = sanitize_configured_feedback_links(
        body,
        vercel_base_url=context.vercel_base_url,
    )
    assert isinstance(sanitized, str)
    return sanitized.removesuffix(_SANITIZED_FEEDBACK_LINK_MARKER)
