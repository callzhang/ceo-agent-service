from __future__ import annotations

from collections.abc import Iterable

from app.channels.models import ChannelAdapter, ChannelMessage


def enqueue_channel_messages(
    store,
    adapter: ChannelAdapter,
    messages: Iterable[ChannelMessage],
) -> int:
    enqueued = 0
    for message in messages:
        if message.channel != adapter.channel_name:
            raise ValueError(
                f"message channel {message.channel!r} does not match "
                f"adapter {adapter.channel_name!r}"
            )
        if store.enqueue_reply_task(
            channel=adapter.channel_name,
            conversation_id=message.conversation_id,
            conversation_title=message.conversation_title,
            single_chat=message.single_chat,
            trigger_message_id=message.message_id,
            trigger_create_time=message.sent_at,
            trigger_sender=message.sender_display,
            trigger_text=message.text,
            trigger_message_json=message.model_dump_json(),
        ):
            enqueued += 1
    return enqueued
