from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from app.feishu.client import FeishuMessageState, FeishuSendResult


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
        message_state: str = "exists",
        state_error=None,
    ):
        self.result = result or FeishuSendResult(True, message_id="om_reply")
        self.error = error
        self.app_id = app_id
        self.deliveries = []
        self.chunk_calls = []
        self.message_state = message_state
        self.state_error = state_error
        self.state_probes = []

    async def fetch_message_state(self, app_id, message_id):
        self.state_probes.append((app_id, message_id))
        if self.state_error:
            raise self.state_error
        return FeishuMessageState(self.message_state)

    async def send_reply(self, delivery):
        self.deliveries.append(delivery)
        if self.error:
            raise self.error
        return self.result

    async def send_reply_chunk(
        self,
        delivery,
        *,
        text,
        ordinal,
        expected_chunks,
        idempotency_key,
    ):
        self.chunk_calls.append(
            {
                "text": text,
                "ordinal": ordinal,
                "expected_chunks": expected_chunks,
                "idempotency_key": idempotency_key,
            }
        )
        return await self.send_reply(delivery)


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
