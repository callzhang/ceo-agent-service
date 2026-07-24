from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from app.feishu.client import FeishuSendResult


@dataclass
class FakeSdkMessage:
    message_id: str = "om_1"
    chat_id: str = "oc_1"
    chat_type: str = "group"
    sender_id: str = "ou_1"
    sender_name: str = "Alex"
    sender_type: str = "user"
    sender_is_bot: bool = False
    raw_content_type: str = "text"
    mentioned_bot: bool = True
    body_text: str = "请看一下"
    create_time: str = "1784685600000"
    thread_id: str = ""
    reply_to_message_id: str = ""
    raw: dict = field(
        default_factory=lambda: {"header": {"event_id": "evt_1"}}
    )


class FakeRunner:
    tool_mode = "none"

    def __init__(self, decision=None, *, error: Exception | None = None):
        self.decision = decision
        self.error = error
        self.prompts: list[str] = []

    def decide(self, prompt, session_id, image_paths=None):
        del session_id, image_paths
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.decision


class FakeDeliveryClient:
    def __init__(
        self,
        result: FeishuSendResult | None = None,
        error=None,
        *,
        app_id: str = "cli_test",
    ):
        self.result = result or FeishuSendResult(True, message_id="om_reply")
        self.error = error
        self.app_id = app_id
        self.deliveries = []

    async def send_reply(self, delivery):
        self.deliveries.append(delivery)
        if self.error:
            raise self.error
        return self.result


class FakeRawChannel:
    def __init__(self, result=None):
        self.result = result or SimpleNamespace(
            success=True, message_id="om_reply", error=None, raw={}
        )
        self.send_calls = []
        self.connected = False
        self.disconnected = False

    async def connect_until_ready(self, timeout=30):
        del timeout
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def send(self, *args):
        self.send_calls.append(args)
        return self.result
