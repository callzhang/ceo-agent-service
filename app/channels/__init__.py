"""Reusable channel adapter contracts for reply task producers."""

from app.channels.dingtalk import DingTalkCliAdapter
from app.channels.enqueue import enqueue_channel_messages
from app.channels.feishu import FeishuCliAdapter, official_bot_doctor
from app.channels.models import (
    ChannelDoctorStatus,
    ChannelMessage,
    ChannelSendResult,
)

__all__ = [
    "ChannelDoctorStatus",
    "ChannelMessage",
    "ChannelSendResult",
    "DingTalkCliAdapter",
    "FeishuCliAdapter",
    "official_bot_doctor",
    "enqueue_channel_messages",
]
