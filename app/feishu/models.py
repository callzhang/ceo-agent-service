"""Persistent, SDK-independent contracts for the Feishu message channel.

These models deliberately contain only normalized business fields.  In
particular, raw event payloads, access tokens, tenant tokens, and app secrets do
not belong in this module or in the corresponding SQLite tables.
"""

import unicodedata
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


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
FeishuDeliveryReceiptStatus = Literal[
    "active", "recalled", "recall_unknown"
]
FeishuInboundResourceType = Literal[
    "image", "file", "audio", "video", "sticker"
]
FeishuInboundResourceRole = Literal["content", "cover"]


_MAX_RESOURCE_KEY_LENGTH = 512
_MAX_RESOURCE_FILE_NAME_LENGTH = 255
_MAX_RESOURCE_DURATION_MS = 86_400_000
def _safe_resource_file_name(value: object) -> str:
    """Return a display-only basename without path or control characters."""
    if value is None:
        return ""
    raw = str(value)
    basename_start = max(raw.rfind("/"), raw.rfind("\\")) + 1
    # The result is only a display label. Bound work before filtering an
    # untrusted, potentially very large name while preserving its basename.
    raw = raw[basename_start : basename_start + 1024]
    cleaned = "".join(
        character
        for character in raw
        if not unicodedata.category(character).startswith("C")
    )
    cleaned = cleaned.strip()
    if cleaned in {".", ".."}:
        return ""
    return cleaned[:_MAX_RESOURCE_FILE_NAME_LENGTH]


class FeishuInboundResourceCandidate(BaseModel):
    """A bounded SDK resource reference awaiting a separate download policy."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    ordinal: int = Field(ge=0, le=7)
    resource_type: FeishuInboundResourceType
    file_key: str = Field(
        min_length=1, max_length=_MAX_RESOURCE_KEY_LENGTH, repr=False
    )
    file_name: str = Field(
        default="", max_length=_MAX_RESOURCE_FILE_NAME_LENGTH
    )
    duration_ms: int | None = Field(
        default=None, ge=0, le=_MAX_RESOURCE_DURATION_MS
    )
    role: FeishuInboundResourceRole = "content"

    @field_validator("file_name", mode="before")
    @classmethod
    def sanitize_file_name(cls, value: object) -> str:
        return _safe_resource_file_name(value)

    @model_validator(mode="after")
    def validate_cover_role(self):
        if self.role == "cover" and self.resource_type != "image":
            raise ValueError("cover resources must be images")
        return self


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
    root_message_id: str = Field(default="", max_length=512)
    parent_message_id: str = Field(default="", max_length=512)
    reply_to_message_id: str = ""
    sender_open_id: str = Field(min_length=1)
    sender_type: str = "user"
    sender_name: str = ""
    sender_is_bot: bool = False
    message_type: str = "text"
    mentioned_bot: bool = False
    body_text: str = ""
    normalized_summary: str = Field(default="", max_length=512)
    event_create_time: str = Field(min_length=1)
    received_at: str = ""


class FeishuNormalizedEnvelope(BaseModel):
    """Versioned, immutable output of the untrusted SDK normalization edge."""

    model_config = ConfigDict(frozen=True)

    message: FeishuInboundMessage
    resources: tuple[FeishuInboundResourceCandidate, ...] = Field(
        default=(), max_length=8
    )
    normalization_version: Literal[1] = 1
    content_truncated: bool = False
    resource_truncated: bool = False


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
    root_message_id: str = ""
    parent_message_id: str = ""
    reply_to_message_id: str = ""
    sender_open_id: str
    sender_type: str
    sender_name: str = ""
    message_type: str
    mentioned_bot: bool
    body_text: str
    normalized_summary: str = ""
    normalization_version: int = 1
    content_truncated: bool = False
    resource_truncated: bool = False
    media_required: bool = False
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
    reply_format: Literal["text", "post"] = "text"
    mention_open_ids: tuple[str, ...] = ()
    payload_sha256: str = ""
    idempotency_key: str = Field(min_length=1)
    expected_chunks: int = Field(default=1, ge=1, le=100)
    chunk_plan_sha256: str = Field(default="", min_length=0, max_length=64)
    review_generation: int = Field(default=1, ge=1)
    approval_hash: str = Field(default="", min_length=0, max_length=64)
    status: FeishuDeliveryStatus = "ready_to_send"
    feishu_message_id: str = ""
    request_log_id: str = ""
    attempts: int = 0
    remote_failures: int = Field(default=0, ge=0)
    lease_token: str = ""
    mutation_started_at: str = ""
    approved_at: str = ""
    approved_by: str = ""
    locked_at: str = ""
    available_at: str = ""
    error_code: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


class FeishuDeliveryReceipt(BaseModel):
    """One durable remote message ID produced by a delivery chunk."""

    model_config = ConfigDict(frozen=True)

    id: int
    delivery_id: int
    app_id: str
    ordinal: int = Field(ge=0)
    message_id: str
    request_log_id: str = ""
    status: FeishuDeliveryReceiptStatus = "active"
    recall_action_id: int = 0
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
