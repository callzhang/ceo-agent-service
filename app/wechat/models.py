"""Immutable WeChat channel contracts and state enums.

Contracts are frozen so producer/consumer/sender code cannot mutate a normalized
message or an approved reply scope in place. Trigger validation encodes the hard
product rule: direct chats reply to every inbound text, groups only on an exact
mention of the current account.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CapabilityStatus = Literal["ready", "blocked", "failed"]
TargetType = Literal["direct", "group"]
TriggerMode = Literal["every_inbound_text", "mention_current_account"]


class WechatAccount(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_id: str
    display_name: str
    self_user_id: str
    account_dir: str
    db_dir: str
    app_version: str


class WechatReplyTarget(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_id: str
    target_type: TargetType
    target_id: str
    conversation_id: str = ""
    display_name: str
    last_active_at: str = ""


class WechatReplyScope(WechatReplyTarget):
    trigger_mode: TriggerMode
    enabled: bool = True
    binding_status: Literal["unverified", "verified", "conflict"] = "unverified"
    binding_evidence: dict[str, str] = Field(default_factory=dict)
    disabled_reason: str = ""

    @model_validator(mode="after")
    def validate_trigger(self):
        expected = (
            "every_inbound_text"
            if self.target_type == "direct"
            else "mention_current_account"
        )
        if self.trigger_mode != expected:
            raise ValueError(f"{self.target_type} requires trigger_mode={expected}")
        return self


class WechatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_id: str
    conversation_id: str
    message_id: str
    sender_id: str
    sender_display_name: str
    conversation_type: TargetType
    direction: Literal["inbound", "outbound"]
    sent_at: str
    kind: Literal["text", "image", "file", "quote", "system", "unknown"]
    text: str = ""
    mentioned_user_ids: frozenset[str] = Field(default_factory=frozenset)
    source_version: str

    def mentions_user(self, user_id: str) -> bool:
        return bool(user_id) and user_id in self.mentioned_user_ids


class WechatCapability(BaseModel):
    status: CapabilityStatus
    account_id: str = ""
    app_version: str = ""
    reason: str = ""
    checked_at: str = ""


class WechatDelivery(BaseModel):
    id: int = 0
    task_id: int
    account_id: str
    target_type: TargetType
    target_id: str
    conversation_id: str = ""
    reply_text: str
    status: Literal[
        "ready_to_send", "sending", "sent", "send_unknown", "failed"
    ] = "ready_to_send"
    evidence: dict[str, str] = Field(default_factory=dict)
    error: str = ""
