import pytest
from pydantic import ValidationError

from app.feishu.models import (
    FeishuDelivery,
    FeishuDeliveryReceipt,
    FeishuInboundMessage,
    FeishuReplyScope,
)


def test_inbound_message_is_frozen_and_sdk_independent():
    message = FeishuInboundMessage(
        event_id="evt-1",
        app_id="cli_a",
        message_id="om_1",
        chat_id="oc_1",
        chat_type="group",
        sender_open_id="ou_1",
        mentioned_bot=True,
        body_text="hello",
        event_create_time="2026-07-22T10:00:00+08:00",
    )

    assert '"event_id":"evt-1"' in message.model_dump_json()
    with pytest.raises(ValidationError):
        message.body_text = "changed"


@pytest.mark.parametrize(
    ("target_type", "trigger_mode"),
    [
        ("direct_sender", "mention_bot"),
        ("group", "every_inbound_text"),
    ],
)
def test_scope_rejects_trigger_mode_for_other_target_type(
    target_type, trigger_mode
):
    with pytest.raises(ValidationError):
        FeishuReplyScope(
            app_id="cli_a",
            target_type=target_type,
            target_id="target",
            trigger_mode=trigger_mode,
        )


def test_enabled_scope_must_be_verified():
    with pytest.raises(ValidationError):
        FeishuReplyScope(
            app_id="cli_a",
            target_type="direct_sender",
            target_id="ou_1",
            trigger_mode="every_inbound_text",
            enabled=True,
            binding_status="pending",
        )


def test_delivery_exposes_durable_retry_and_idempotency_fields():
    delivery = FeishuDelivery(
        id=1,
        reply_task_id=2,
        app_id="cli_a",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_text="reply",
        idempotency_key="8e5e38d0-f7f8-4a73-8795-dff3b44f96b5",
        available_at="2026-07-22T10:01:00+08:00",
    )

    assert delivery.status == "ready_to_send"
    assert delivery.available_at.endswith("+08:00")


def test_delivery_rich_fields_and_receipt_are_immutable():
    delivery = FeishuDelivery(
        id=1,
        reply_task_id=2,
        attempt_id=3,
        app_id="cli_a",
        chat_id="oc_a",
        reply_to_message_id="om_trigger",
        reply_text="# Markdown",
        reply_format="post",
        mention_open_ids=("ou_a",),
        payload_sha256="a" * 64,
        idempotency_key="stable",
    )
    receipt = FeishuDeliveryReceipt(
        id=1,
        delivery_id=delivery.id,
        app_id=delivery.app_id,
        ordinal=0,
        message_id="om_chunk",
    )
    assert delivery.reply_format == "post"
    assert delivery.mention_open_ids == ("ou_a",)
    assert receipt.status == "active"
    with pytest.raises(ValidationError):
        receipt.message_id = "changed"
