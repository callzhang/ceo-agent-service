# CLI Channel Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build our own replacement for PR #4: a reusable CLI channel adapter layer so DingTalk CLI, Feishu CLI, and WeChat CLI can all become reply-capable channels without duplicating the DWS reply system.

**Architecture:** Extract the smallest common channel interface from the existing DingTalk/DWS flow: auth doctor, message normalization, reply sending, and optional reaction/open-link capability. Keep `reply_tasks`, `reply_attempts`, and History as the shared pipeline, distinguished by `channel`. Implement Feishu as a CLI adapter that can run in doctor and dry-run normalization mode first; live send stays behind explicit channel config.

**Tech Stack:** Python, Pydantic models, SQLite existing store, FastAPI audit web, pytest, local CLIs (`dws`, future `lark`/`feishu`, future `wechat`).

---

## File Structure

- Create: `app/channels/__init__.py`
  - Package marker and public exports.
- Create: `app/channels/models.py`
  - Provider-neutral `ChannelConversation`, `ChannelMessage`, `ChannelDoctorStatus`, `ChannelSendResult`.
- Create: `app/channels/base.py`
  - `ReplyChannelAdapter` protocol defining the common CLI-backed operations.
- Create: `app/channels/dingtalk_adapter.py`
  - Thin wrapper around existing `DwsClient`, proving DingTalk can fit the interface without changing behavior.
- Create: `app/channels/feishu_cli_adapter.py`
  - Feishu CLI adapter with doctor and dry-run message normalization. Live send remains disabled until configured.
- Create: `app/channels/registry.py`
  - Loads enabled adapters from env/config and exposes doctor summaries.
- Modify: `app/worker.py`
  - Only after adapter tests pass, add a narrow producer path that converts `ChannelMessage` into existing reply tasks.
- Modify: `app/audit_web.py`
  - Show channel doctor status in Tutorial/Config; do not add a separate Feishu History system.
- Modify: `README.md`, `.env.example`
  - Document `CEO_ENABLED_CHANNELS`, `CEO_FEISHU_CLI_BIN`, `CEO_WECHAT_CLI_BIN`.
- Test: `tests/channels/test_models.py`
- Test: `tests/channels/test_dingtalk_adapter.py`
- Test: `tests/channels/test_feishu_cli_adapter.py`
- Test: `tests/channels/test_registry.py`
- Test: `tests/test_worker.py`
- Test: `tests/test_audit_web.py`

## Design Rules

- Do not copy the DWS producer/consumer/delivery/store stack for Feishu.
- All channels feed the same `reply_tasks` and `reply_attempts` pipeline.
- Feishu and WeChat CLIs are adapters, not new subsystems.
- Each adapter must report `ready`, `needs_login`, `missing_config`, `permission_blocked`, or `unavailable`.
- Live send requires explicit channel enablement; dry-run normalization can be tested without credentials.
- The first implementation must support text reply only. Media, cards, reactions, recalls, OA, mail, and docs are later capabilities.

### Task 1: Provider-Neutral Channel Models

**Files:**
- Create: `app/channels/__init__.py`
- Create: `app/channels/models.py`
- Test: `tests/channels/test_models.py`

- [ ] **Step 1: Write failing tests**

Create `tests/channels/test_models.py`:

```python
from app.channels.models import (
    ChannelConversation,
    ChannelDoctorStatus,
    ChannelMessage,
    ChannelSendResult,
)


def test_channel_message_requires_stable_identity():
    message = ChannelMessage(
        channel="feishu",
        conversation=ChannelConversation(
            channel="feishu",
            conversation_id="chat-1",
            title="飞书测试群",
            single_chat=False,
        ),
        message_id="msg-1",
        sender_name="Alice",
        sender_user_id="user-1",
        text="@Derek 请看一下",
        created_at="2026-07-22T10:00:00+08:00",
    )

    assert message.channel == "feishu"
    assert message.conversation.conversation_id == "chat-1"
    assert message.text == "@Derek 请看一下"


def test_channel_doctor_status_is_closed_enum():
    status = ChannelDoctorStatus(channel="feishu", status="needs_login", detail="请先登录")

    assert status.status == "needs_login"
    assert status.ready is False


def test_channel_send_result_keeps_raw_payload():
    result = ChannelSendResult(
        channel="dingtalk",
        success=True,
        message_id="open-msg-1",
        raw={"result": {"openMessageId": "open-msg-1"}},
    )

    assert result.success is True
    assert result.raw["result"]["openMessageId"] == "open-msg-1"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_models.py -q
```

Expected: FAIL because `app.channels` does not exist.

- [ ] **Step 3: Create models**

Create `app/channels/__init__.py`:

```python
from app.channels.models import (
    ChannelConversation,
    ChannelDoctorStatus,
    ChannelMessage,
    ChannelSendResult,
)

__all__ = [
    "ChannelConversation",
    "ChannelDoctorStatus",
    "ChannelMessage",
    "ChannelSendResult",
]
```

Create `app/channels/models.py`:

```python
from typing import Any, Literal

from pydantic import BaseModel, Field

ChannelName = Literal["dingtalk", "feishu", "wechat"]
DoctorState = Literal[
    "ready",
    "needs_login",
    "missing_config",
    "permission_blocked",
    "unavailable",
]


class ChannelDoctorStatus(BaseModel):
    channel: ChannelName
    status: DoctorState
    detail: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == "ready"


class ChannelConversation(BaseModel):
    channel: ChannelName
    conversation_id: str
    title: str = ""
    single_chat: bool = False


class ChannelMessage(BaseModel):
    channel: ChannelName
    conversation: ChannelConversation
    message_id: str
    sender_name: str = ""
    sender_user_id: str = ""
    text: str
    created_at: str
    raw: dict[str, Any] = Field(default_factory=dict)


class ChannelSendResult(BaseModel):
    channel: ChannelName
    success: bool
    message_id: str = ""
    error: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/channels/__init__.py app/channels/models.py tests/channels/test_models.py
git commit -m "feat: add shared channel models"
```

### Task 2: Adapter Protocol

**Files:**
- Create: `app/channels/base.py`
- Test: `tests/channels/test_models.py`

- [ ] **Step 1: Write failing test**

Append to `tests/channels/test_models.py`:

```python
def test_reply_channel_adapter_protocol_accepts_minimal_fake():
    from app.channels.base import ReplyChannelAdapter

    class FakeAdapter:
        channel = "feishu"

        def doctor(self):
            return ChannelDoctorStatus(channel="feishu", status="ready")

        def list_recent_messages(self, limit=20):
            return []

        def send_reply(self, conversation_id, text, *, reply_to_message_id=""):
            return ChannelSendResult(channel="feishu", success=True, message_id="msg-out")

    adapter: ReplyChannelAdapter = FakeAdapter()

    assert adapter.channel == "feishu"
    assert adapter.doctor().ready is True
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_models.py::test_reply_channel_adapter_protocol_accepts_minimal_fake -q
```

Expected: FAIL because `app.channels.base` does not exist.

- [ ] **Step 3: Add protocol**

Create `app/channels/base.py`:

```python
from typing import Protocol

from app.channels.models import (
    ChannelDoctorStatus,
    ChannelMessage,
    ChannelName,
    ChannelSendResult,
)


class ReplyChannelAdapter(Protocol):
    channel: ChannelName

    def doctor(self) -> ChannelDoctorStatus:
        ...

    def list_recent_messages(self, limit: int = 20) -> list[ChannelMessage]:
        ...

    def send_reply(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to_message_id: str = "",
    ) -> ChannelSendResult:
        ...
```

- [ ] **Step 4: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_models.py::test_reply_channel_adapter_protocol_accepts_minimal_fake -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/channels/base.py tests/channels/test_models.py
git commit -m "feat: define reply channel adapter protocol"
```

### Task 3: DingTalk Adapter Wrapper

**Files:**
- Create: `app/channels/dingtalk_adapter.py`
- Test: `tests/channels/test_dingtalk_adapter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/channels/test_dingtalk_adapter.py`:

```python
from app.channels.dingtalk_adapter import DingTalkChannelAdapter
from app.dingtalk_models import DingTalkConversation, DingTalkMessage


class FakeDws:
    def __init__(self):
        self.sent = []

    def auth_status(self):
        return {"success": True, "authenticated": True, "token_valid": True}

    def list_unread_conversations(self):
        return [
            DingTalkConversation(
                open_conversation_id="cid-1",
                title="钉钉群",
                single_chat=False,
                unread_point=1,
            )
        ]

    def list_messages(self, conversation_id, limit=20):
        assert conversation_id == "cid-1"
        return [
            DingTalkMessage(
                open_conversation_id="cid-1",
                open_message_id="msg-1",
                conversation_title="钉钉群",
                single_chat=False,
                sender_name="Alice",
                sender_user_id="user-1",
                create_time="2026-07-22 10:00:00",
                content="请看一下",
            )
        ]

    def send_message(self, conversation_id, text, **kwargs):
        self.sent.append((conversation_id, text, kwargs))
        return {"result": {"openMessageId": "msg-out-1"}}


def test_dingtalk_adapter_doctor_ready():
    adapter = DingTalkChannelAdapter(FakeDws())

    assert adapter.doctor().status == "ready"


def test_dingtalk_adapter_normalizes_messages():
    adapter = DingTalkChannelAdapter(FakeDws())

    messages = adapter.list_recent_messages(limit=5)

    assert len(messages) == 1
    assert messages[0].channel == "dingtalk"
    assert messages[0].conversation.conversation_id == "cid-1"
    assert messages[0].message_id == "msg-1"


def test_dingtalk_adapter_sends_reply():
    dws = FakeDws()
    adapter = DingTalkChannelAdapter(dws)

    result = adapter.send_reply("cid-1", "收到", reply_to_message_id="msg-1")

    assert result.success is True
    assert result.message_id == "msg-out-1"
    assert dws.sent[0][0] == "cid-1"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_dingtalk_adapter.py -q
```

Expected: FAIL because adapter does not exist.

- [ ] **Step 3: Implement adapter**

Create `app/channels/dingtalk_adapter.py`:

```python
from app.channels.models import (
    ChannelConversation,
    ChannelDoctorStatus,
    ChannelMessage,
    ChannelSendResult,
)
from app.dws_client import DwsClient


class DingTalkChannelAdapter:
    channel = "dingtalk"

    def __init__(self, dws: DwsClient | None = None):
        self.dws = dws or DwsClient()

    def doctor(self) -> ChannelDoctorStatus:
        auth_status = getattr(self.dws, "auth_status", None)
        if auth_status is None:
            return ChannelDoctorStatus(channel="dingtalk", status="ready")
        try:
            payload = auth_status()
        except Exception as exc:
            return ChannelDoctorStatus(channel="dingtalk", status="unavailable", detail=str(exc))
        if payload.get("authenticated") and payload.get("token_valid"):
            return ChannelDoctorStatus(channel="dingtalk", status="ready", raw=payload)
        return ChannelDoctorStatus(channel="dingtalk", status="needs_login", raw=payload)

    def list_recent_messages(self, limit: int = 20) -> list[ChannelMessage]:
        messages: list[ChannelMessage] = []
        for conversation in self.dws.list_unread_conversations():
            for message in self.dws.list_messages(conversation.open_conversation_id, limit=limit):
                messages.append(
                    ChannelMessage(
                        channel="dingtalk",
                        conversation=ChannelConversation(
                            channel="dingtalk",
                            conversation_id=conversation.open_conversation_id,
                            title=conversation.title,
                            single_chat=conversation.single_chat,
                        ),
                        message_id=message.open_message_id,
                        sender_name=message.sender_name,
                        sender_user_id=message.sender_user_id,
                        text=message.content,
                        created_at=message.create_time,
                        raw=message.model_dump(mode="json"),
                    )
                )
                if len(messages) >= limit:
                    return messages
        return messages

    def send_reply(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to_message_id: str = "",
    ) -> ChannelSendResult:
        raw = self.dws.send_message(conversation_id, text, ref_message_id=reply_to_message_id)
        result = raw.get("result", {}) if isinstance(raw, dict) else {}
        message_id = str(result.get("openMessageId") or result.get("messageId") or "")
        return ChannelSendResult(
            channel="dingtalk",
            success=True,
            message_id=message_id,
            raw=raw if isinstance(raw, dict) else {"raw": raw},
        )
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_dingtalk_adapter.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/channels/dingtalk_adapter.py tests/channels/test_dingtalk_adapter.py
git commit -m "feat: adapt DingTalk DWS to reply channel interface"
```

### Task 4: Feishu CLI Adapter Doctor And Normalization

**Files:**
- Create: `app/channels/feishu_cli_adapter.py`
- Test: `tests/channels/test_feishu_cli_adapter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/channels/test_feishu_cli_adapter.py`:

```python
import json

from app.channels.feishu_cli_adapter import FeishuCliChannelAdapter


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        return self.outputs.pop(0)


def test_feishu_cli_doctor_ready():
    runner = FakeRunner([
        json.dumps({"success": True, "authenticated": True, "user": "Derek"})
    ])
    adapter = FeishuCliChannelAdapter(cli_bin="lark", runner=runner)

    status = adapter.doctor()

    assert status.status == "ready"
    assert runner.commands[0] == ["lark", "auth", "status", "--format", "json"]


def test_feishu_cli_doctor_needs_login():
    runner = FakeRunner([
        json.dumps({"success": True, "authenticated": False})
    ])
    adapter = FeishuCliChannelAdapter(cli_bin="lark", runner=runner)

    assert adapter.doctor().status == "needs_login"


def test_feishu_cli_normalizes_recent_messages():
    payload = {
        "success": True,
        "result": {
            "messages": [
                {
                    "chat_id": "chat-1",
                    "chat_title": "飞书群",
                    "chat_type": "group",
                    "message_id": "msg-1",
                    "sender_name": "Alice",
                    "sender_user_id": "user-1",
                    "text": "@Derek 请看一下",
                    "create_time": "2026-07-22T10:00:00+08:00",
                }
            ]
        },
    }
    runner = FakeRunner([json.dumps(payload)])
    adapter = FeishuCliChannelAdapter(cli_bin="lark", runner=runner)

    messages = adapter.list_recent_messages(limit=10)

    assert len(messages) == 1
    assert messages[0].channel == "feishu"
    assert messages[0].conversation.conversation_id == "chat-1"
    assert messages[0].message_id == "msg-1"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_feishu_cli_adapter.py -q
```

Expected: FAIL because adapter does not exist.

- [ ] **Step 3: Implement adapter**

Create `app/channels/feishu_cli_adapter.py`:

```python
import json
import subprocess
from typing import Any, Callable

from app.channels.models import (
    ChannelConversation,
    ChannelDoctorStatus,
    ChannelMessage,
    ChannelSendResult,
)


def _default_runner(command: list[str]) -> str:
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout


class FeishuCliChannelAdapter:
    channel = "feishu"

    def __init__(
        self,
        *,
        cli_bin: str = "lark",
        runner: Callable[[list[str]], str] = _default_runner,
        live_send_enabled: bool = False,
    ):
        self.cli_bin = cli_bin
        self.runner = runner
        self.live_send_enabled = live_send_enabled

    def doctor(self) -> ChannelDoctorStatus:
        try:
            raw = self.runner([self.cli_bin, "auth", "status", "--format", "json"])
            payload = json.loads(raw)
        except FileNotFoundError as exc:
            return ChannelDoctorStatus(channel="feishu", status="missing_config", detail=str(exc))
        except Exception as exc:
            return ChannelDoctorStatus(channel="feishu", status="unavailable", detail=str(exc))
        if payload.get("authenticated") is True:
            return ChannelDoctorStatus(channel="feishu", status="ready", raw=payload)
        return ChannelDoctorStatus(channel="feishu", status="needs_login", raw=payload)

    def list_recent_messages(self, limit: int = 20) -> list[ChannelMessage]:
        raw = self.runner(
            [
                self.cli_bin,
                "chat",
                "message",
                "list-recent",
                "--limit",
                str(limit),
                "--format",
                "json",
            ]
        )
        payload = json.loads(raw)
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        raw_messages = result.get("messages", [])
        messages: list[ChannelMessage] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            conversation = ChannelConversation(
                channel="feishu",
                conversation_id=str(item.get("chat_id") or ""),
                title=str(item.get("chat_title") or ""),
                single_chat=str(item.get("chat_type") or "") == "p2p",
            )
            messages.append(
                ChannelMessage(
                    channel="feishu",
                    conversation=conversation,
                    message_id=str(item.get("message_id") or ""),
                    sender_name=str(item.get("sender_name") or ""),
                    sender_user_id=str(item.get("sender_user_id") or ""),
                    text=str(item.get("text") or ""),
                    created_at=str(item.get("create_time") or ""),
                    raw=item,
                )
            )
        return messages

    def send_reply(
        self,
        conversation_id: str,
        text: str,
        *,
        reply_to_message_id: str = "",
    ) -> ChannelSendResult:
        if not self.live_send_enabled:
            return ChannelSendResult(
                channel="feishu",
                success=False,
                error="feishu_live_send_disabled",
            )
        raw = self.runner(
            [
                self.cli_bin,
                "chat",
                "message",
                "send",
                "--chat-id",
                conversation_id,
                "--text",
                text,
                "--reply-to-message-id",
                reply_to_message_id,
                "--format",
                "json",
            ]
        )
        payload = json.loads(raw)
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        return ChannelSendResult(
            channel="feishu",
            success=bool(payload.get("success", True)),
            message_id=str(result.get("message_id") or ""),
            raw=payload,
        )
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_feishu_cli_adapter.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/channels/feishu_cli_adapter.py tests/channels/test_feishu_cli_adapter.py
git commit -m "feat: add Feishu CLI channel adapter"
```

### Task 5: Channel Registry

**Files:**
- Create: `app/channels/registry.py`
- Modify: `.env.example`
- Test: `tests/channels/test_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/channels/test_registry.py`:

```python
from app.channels.registry import build_channel_adapters, channel_doctor_summary


def test_registry_builds_enabled_channels(monkeypatch):
    monkeypatch.setenv("CEO_ENABLED_CHANNELS", "dingtalk,feishu")
    monkeypatch.setenv("CEO_FEISHU_CLI_BIN", "lark")

    adapters = build_channel_adapters(dws=object())

    assert [adapter.channel for adapter in adapters] == ["dingtalk", "feishu"]


def test_channel_doctor_summary_reports_all_adapters():
    class Adapter:
        def __init__(self, channel):
            self.channel = channel

        def doctor(self):
            from app.channels.models import ChannelDoctorStatus

            return ChannelDoctorStatus(channel=self.channel, status="ready")

    summary = channel_doctor_summary([Adapter("dingtalk"), Adapter("feishu")])

    assert summary["dingtalk"]["status"] == "ready"
    assert summary["feishu"]["status"] == "ready"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_registry.py -q
```

Expected: FAIL because registry does not exist.

- [ ] **Step 3: Implement registry**

Create `app/channels/registry.py`:

```python
import os
from typing import Iterable

from app.channels.base import ReplyChannelAdapter
from app.channels.dingtalk_adapter import DingTalkChannelAdapter
from app.channels.feishu_cli_adapter import FeishuCliChannelAdapter


def _enabled_channels() -> list[str]:
    raw = os.getenv("CEO_ENABLED_CHANNELS", "dingtalk").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_channel_adapters(*, dws=None) -> list[ReplyChannelAdapter]:
    adapters: list[ReplyChannelAdapter] = []
    for channel in _enabled_channels():
        if channel == "dingtalk":
            adapters.append(DingTalkChannelAdapter(dws))
        elif channel == "feishu":
            adapters.append(
                FeishuCliChannelAdapter(
                    cli_bin=os.getenv("CEO_FEISHU_CLI_BIN", "lark").strip() or "lark",
                    live_send_enabled=os.getenv("CEO_FEISHU_LIVE_SEND", "0") == "1",
                )
            )
        elif channel == "wechat":
            continue
    return adapters


def channel_doctor_summary(adapters: Iterable[ReplyChannelAdapter]) -> dict[str, dict[str, str]]:
    summary: dict[str, dict[str, str]] = {}
    for adapter in adapters:
        status = adapter.doctor()
        summary[adapter.channel] = {
            "status": status.status,
            "detail": status.detail,
        }
    return summary
```

Add to `.env.example`:

```env
CEO_ENABLED_CHANNELS=dingtalk
CEO_FEISHU_CLI_BIN=lark
CEO_FEISHU_LIVE_SEND=0
CEO_WECHAT_CLI_BIN=wechat-cli
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/channels/test_registry.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/channels/registry.py tests/channels/test_registry.py .env.example
git commit -m "feat: add reply channel registry"
```

### Task 6: Convert Channel Messages Into Existing Reply Tasks

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_worker_enqueues_channel_message_as_reply_task(tmp_path: Path, monkeypatch):
    from app.channels.models import ChannelConversation, ChannelMessage

    channel_message = ChannelMessage(
        channel="feishu",
        conversation=ChannelConversation(
            channel="feishu",
            conversation_id="chat-1",
            title="飞书群",
            single_chat=False,
        ),
        message_id="msg-feishu-1",
        sender_name="Alice",
        sender_user_id="user-1",
        text="@Derek 请看一下",
        created_at="2026-07-22T10:00:00+08:00",
    )
    dws = FakeDws([], {})
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)

    queued = worker.enqueue_channel_messages([channel_message])
    task = worker.store.claim_reply_tasks(limit=1)[0]

    assert queued == 1
    assert task.channel == "feishu"
    assert task.conversation_id == "chat-1"
    assert task.trigger_message_id == "msg-feishu-1"
    assert task.trigger_text == "@Derek 请看一下"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_worker_enqueues_channel_message_as_reply_task -q
```

Expected: FAIL because `enqueue_channel_messages` does not exist or `ReplyTask.channel` is not populated.

- [ ] **Step 3: Add conversion helper**

In `app/worker.py`, add:

```python
    def enqueue_channel_messages(self, messages: list[Any]) -> int:
        queued = 0
        for channel_message in messages:
            conversation = DingTalkConversation(
                open_conversation_id=channel_message.conversation.conversation_id,
                title=channel_message.conversation.title,
                single_chat=channel_message.conversation.single_chat,
                unread_point=0,
            )
            trigger = DingTalkMessage(
                open_conversation_id=channel_message.conversation.conversation_id,
                open_message_id=channel_message.message_id,
                conversation_title=channel_message.conversation.title,
                single_chat=channel_message.conversation.single_chat,
                sender_name=channel_message.sender_name,
                sender_user_id=channel_message.sender_user_id,
                create_time=channel_message.created_at,
                content=channel_message.text,
                raw_payload=channel_message.raw,
            )
            if self.store.has_seen(trigger.open_message_id):
                continue
            self.store.upsert_conversation(
                conversation_id=conversation.open_conversation_id,
                title=conversation.title,
                single_chat=conversation.single_chat,
                codex_session_id=None,
            )
            if self._enqueue_reply_task(
                conversation,
                trigger,
                context_messages=[],
                replace_pending_single_chat=False,
                channel=channel_message.channel,
            ):
                queued += 1
        return queued
```

If `_enqueue_reply_task` does not accept `channel`, add an optional parameter:

```python
        channel: str = "dingtalk",
```

and pass it into the store enqueue call:

```python
            channel=channel,
```

- [ ] **Step 4: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py::test_worker_enqueues_channel_message_as_reply_task -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "feat: enqueue shared channel messages as reply tasks"
```

### Task 7: Channel Doctor In Tutorial/Config

**Files:**
- Modify: `app/audit_web.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_config_page_shows_channel_doctor_status(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CEO_ENABLED_CHANNELS", "dingtalk,feishu")
    monkeypatch.setattr(
        "app.audit_web.channel_doctor_summary",
        lambda adapters: {
            "dingtalk": {"status": "ready", "detail": ""},
            "feishu": {"status": "needs_login", "detail": "请执行 lark auth login"},
        },
        raising=False,
    )
    monkeypatch.setattr(
        "app.audit_web.build_channel_adapters",
        lambda: [],
        raising=False,
    )
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/config")

    assert response.status_code == 200
    assert "Channel Doctor" in response.text
    assert "dingtalk" in response.text
    assert "feishu" in response.text
    assert "needs_login" in response.text
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_config_page_shows_channel_doctor_status -q
```

Expected: FAIL because config page does not show channel doctor.

- [ ] **Step 3: Add config card**

In `app/audit_web.py`, import:

```python
from app.channels.registry import build_channel_adapters, channel_doctor_summary
```

Add:

```python
def _channel_doctor_card() -> str:
    try:
        summary = channel_doctor_summary(build_channel_adapters())
    except Exception as exc:
        summary = {"channels": {"status": "unavailable", "detail": str(exc)}}
    rows = []
    for channel, status in summary.items():
        rows.append(
            "<tr>"
            f"<td>{escape(channel)}</td>"
            f"<td>{escape(status.get('status', 'unknown'))}</td>"
            f"<td>{escape(status.get('detail', ''))}</td>"
            "</tr>"
        )
    return (
        "<section class=\"card\"><h2>Channel Doctor</h2>"
        "<table class=\"attempt-table\"><thead><tr><th>Channel</th><th>Status</th><th>Detail</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )
```

Add `_channel_doctor_card()` to the `/config` page body.

- [ ] **Step 4: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_config_page_shows_channel_doctor_status -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: show channel doctor status in config"
```

### Task 8: Documentation And Regression

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document channel architecture**

Add:

```markdown
### Reply channel adapters

CEO Agent Service uses one reply pipeline for supported IM channels. A channel adapter is responsible only for auth status, message normalization, reply send, and optional open/reaction capabilities. The shared pipeline owns task creation, Codex decisioning, audit records, History, retry, and feedback.

Supported adapter stages:

| Channel | CLI | Status | Notes |
| --- | --- | --- | --- |
| DingTalk | `dws` | production | Existing default channel |
| Feishu | `lark` / Feishu CLI | dry-run adapter | Doctor and text message normalization first; live send requires `CEO_FEISHU_LIVE_SEND=1` |
| WeChat | custom WeChat CLI | planned | Must implement the same adapter interface |
```

- [ ] **Step 2: Run channel tests**

Run:

```bash
.venv/bin/python -m pytest tests/channels -q
```

Expected: PASS.

- [ ] **Step 3: Run affected app tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker.py tests/test_audit_web.py tests/test_cli.py -q
```

Expected: PASS, or list known main baseline failures with proof from `origin/main`.

- [ ] **Step 4: Commit docs**

```bash
git add README.md
git commit -m "docs: describe shared reply channel adapters"
```

- [ ] **Step 5: Restart service after runtime changes**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: service has a fresh running `pid`.

## Self-Review

Spec coverage:
- #4 self-development is covered by the shared CLI channel adapter plan.
- DingTalk CLI, Feishu CLI, and WeChat CLI configuration are covered by registry/env/docs.
- "可以回复的通路" is covered by shared `reply_tasks` enqueue and adapter `send_reply`.
- "代码太冗余，复用 dws 逻辑" is covered by DingTalk adapter proof and the rule that Feishu must not copy producer/consumer/delivery/store.

Placeholder scan:
- No `TBD`, `TODO`, `implement later`, or "similar to" placeholders remain.

Type consistency:
- `ChannelConversation`, `ChannelMessage`, `ChannelDoctorStatus`, and `ChannelSendResult` are defined before use.
- Adapter protocol signatures match all adapter implementations and tests.
