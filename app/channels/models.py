from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


ChannelCapabilityState = Literal["ready", "blocked", "failed"]
ConversationType = Literal["direct", "group", "unknown"]


class ChannelDoctorStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: str
    status: ChannelCapabilityState
    reason: str = ""
    command: list[str] = Field(default_factory=list)


class ChannelSendResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: str
    status: ChannelCapabilityState
    reason: str = ""
    command: list[str] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)


class ChannelMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: str
    conversation_id: str
    conversation_title: str = ""
    conversation_type: ConversationType = "unknown"
    message_id: str
    sent_at: str
    sender_display: str
    text: str = ""
    raw_json: dict = Field(default_factory=dict)

    @property
    def single_chat(self) -> bool:
        return self.conversation_type == "direct"


class ChannelAdapter(Protocol):
    channel_name: str

    def doctor(self) -> ChannelDoctorStatus:
        ...

    def list_recent_messages(self, *, limit: int = 50) -> list[ChannelMessage]:
        ...

    def send_reply(self, *, conversation_id: str, text: str) -> ChannelSendResult:
        ...
