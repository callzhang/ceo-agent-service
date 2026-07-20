"""Turn newly-read WeChat messages into channel-isolated reply tasks.

Eligibility is a pure function so it is trivially testable and auditable: direct
chats reply to every inbound text; selected groups only reply on an *exact*
structured mention of the current account (never inferred from display-name
text). Enqueue is idempotent via the reply_tasks (channel, conversation_id,
trigger_message_id) uniqueness, so repeated scans never duplicate.
"""
from __future__ import annotations

from datetime import datetime

from app.wechat.models import WechatAccount, WechatMessage, WechatReplyScope


def is_reply_candidate(
    message: WechatMessage,
    scope: WechatReplyScope | None,
    *,
    self_user_id: str,
) -> bool:
    if scope is None or not scope.enabled:
        return False
    if message.direction != "inbound" or message.kind != "text":
        return False
    if message.conversation_type == "direct":
        return scope.trigger_mode == "every_inbound_text"
    return (
        scope.trigger_mode == "mention_current_account"
        and message.mentions_user(self_user_id)
    )


class WechatReplyProducer:
    def __init__(self, store, reader, account: WechatAccount, *, self_user_id: str,
                 read_limit: int = 200):
        self.store = store
        self.reader = reader
        self.account = account
        self.self_user_id = self_user_id
        self.read_limit = read_limit

    def run_once(self) -> int:
        scopes = self.store.list_wechat_reply_scopes(
            self.account.account_id, enabled_only=True
        )
        enqueued = 0
        for scope in scopes:
            conversation_id = scope.conversation_id or scope.target_id
            if not scope.last_active_at:
                self.store.advance_wechat_scope_watermark(
                    scope.account_id, scope.target_type, scope.target_id,
                    datetime.now().astimezone().isoformat(),
                )
                continue
            messages = self.reader.read_messages(
                self.account,
                conversation_id=conversation_id,
                conversation_type=scope.target_type,
                since=scope.last_active_at,
                limit=self.read_limit,
                order="oldest",
            )
            ordered = sorted(messages, key=lambda m: m.sent_at)
            new_messages = [m for m in ordered if m.sent_at >= scope.last_active_at]
            # Oldest-first; the frontier moves only after the complete batch.
            for message in new_messages:
                if not is_reply_candidate(message, scope, self_user_id=self.self_user_id):
                    continue
                if self.store.enqueue_reply_task(
                    channel="wechat",
                    conversation_id=message.conversation_id,
                    conversation_title=scope.display_name,
                    single_chat=message.conversation_type == "direct",
                    trigger_message_id=message.message_id,
                    trigger_create_time=message.sent_at,
                    trigger_sender=message.sender_display_name,
                    trigger_text=message.text,
                    trigger_message_json=message.model_dump_json(),
                ):
                    enqueued += 1
            if new_messages:
                self.store.advance_wechat_scope_watermark(
                    scope.account_id, scope.target_type, scope.target_id,
                    new_messages[-1].sent_at,
                )
        return enqueued
