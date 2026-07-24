"""Persistent, SDK-independent contracts for the Feishu message channel.

These models deliberately contain only normalized business fields.  In
particular, raw event payloads, access tokens, tenant tokens, and app secrets do
not belong in this module or in the corresponding SQLite tables.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FeishuChatType = Literal["p2p", "group", "topic", "unknown"]
FeishuScopeTargetType = Literal["direct_sender", "group"]
FeishuTriggerMode = Literal["every_inbound_text", "mention_bot"]
FeishuScopeBindingStatus = Literal["pending", "verified", "disabled"]
FeishuDeliveryStatus = Literal[
    "ready_to_send",
    "sending",
    "sent",
    "retry",
    "send_unknown",
    "failed",
    "rejected",
]


class FeishuInboundMessage(BaseModel):
    """A normalized inbound event safe to persist and hand to the producer."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    chat_type: FeishuChatType
    chat_title: str = ""
    thread_id: str = ""
    reply_to_message_id: str = ""
    sender_open_id: str = Field(min_length=1)
    sender_type: str = "user"
    sender_name: str = ""
    sender_is_bot: bool = False
    message_type: str = "text"
    mentioned_bot: bool = False
    body_text: str = ""
    event_create_time: str = Field(min_length=1)
    received_at: str = ""


class FeishuEventRecord(BaseModel):
    """Durable result of recording a normalized Feishu event."""

    model_config = ConfigDict(frozen=True)

    id: int
    event_id: str
    app_id: str
    message_id: str
    chat_id: str
    chat_type: FeishuChatType
    chat_title: str = ""
    thread_id: str = ""
    reply_to_message_id: str = ""
    sender_open_id: str
    sender_type: str
    sender_name: str = ""
    message_type: str
    mentioned_bot: bool
    body_text: str
    event_create_time: str
    event_create_time_ms: int = 0
    received_at: str
    eligibility_status: str
    reject_reason: str = ""
    reply_task_id: int = 0
    created_at: str = ""
    inserted: bool = False
    enqueued: bool = False


class FeishuReplyScope(BaseModel):
    """A locally reviewed sender or group allowed to trigger the agent."""

    model_config = ConfigDict(frozen=True)

    app_id: str = Field(min_length=1)
    target_type: FeishuScopeTargetType
    target_id: str = Field(min_length=1)
    display_name: str = ""
    trigger_mode: FeishuTriggerMode
    enabled: bool = False
    binding_status: FeishuScopeBindingStatus = "pending"
    last_seen_at: str = ""
    approved_at: str = ""
    approved_by: str = ""
    created_at: str = ""
    updated_at: str = ""

    @model_validator(mode="after")
    def validate_trigger_mode(self):
        expected = (
            "every_inbound_text"
            if self.target_type == "direct_sender"
            else "mention_bot"
        )
        if self.trigger_mode != expected:
            raise ValueError(
                f"{self.target_type} requires trigger_mode={expected}"
            )
        if self.enabled and self.binding_status != "verified":
            raise ValueError("enabled Feishu scopes must be verified")
        return self


class FeishuDelivery(BaseModel):
    """One exactly-once-oriented outbound reply and its durable state."""

    model_config = ConfigDict(frozen=True)

    id: int
    reply_task_id: int
    attempt_id: int = 0
    app_id: str
    chat_id: str
    reply_to_message_id: str
    reply_in_thread: bool = False
    reply_text: str
    idempotency_key: str = Field(min_length=1)
    status: FeishuDeliveryStatus = "ready_to_send"
    feishu_message_id: str = ""
    request_log_id: str = ""
    attempts: int = 0
    lease_token: str = ""
    approved_at: str = ""
    approved_by: str = ""
    locked_at: str = ""
    available_at: str = ""
    error_code: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


class FeishuAuditEvent(BaseModel):
    """Append-only, payload-free audit evidence for local Feishu mutations."""

    model_config = ConfigDict(frozen=True)

    id: int
    app_id: str
    entity_type: str
    entity_id: str
    event_type: str
    previous_state: str = ""
    new_state: str = ""
    actor: str = ""
    detail: str = ""
    created_at: str = ""
