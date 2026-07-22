"""Reusable channel adapter contracts for reply task producers."""

from app.channels.dingtalk import DingTalkCliAdapter
from app.channels.enqueue import enqueue_channel_messages
from app.channels.feishu import FeishuCliAdapter
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
    "enqueue_channel_messages",
]
