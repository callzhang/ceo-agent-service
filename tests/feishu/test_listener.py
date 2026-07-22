import asyncio

import pytest

from app.feishu.client import FeishuClientConfig
from app.feishu.listener import FeishuIngressListener


class FakeStore:
    def __init__(self):
        self.errors = []

    def record_error(self, conversation_id, message_id, kind, detail):
        self.errors.append((conversation_id, message_id, kind, detail))


class FakeProducer:
    def __init__(self, *, error=None):
        self.store = FakeStore()
        self.messages = []
        self.error = error

    def ingest_sdk_message(self, message):
        self.messages.append(message)
        if self.error:
            raise self.error


class FakeConnectedClient:
    def __init__(self):
        self.connected = False
        self.disconnected = False

    async def connect_until_ready(self, timeout=30):
        del timeout
        self.connected = True

    async def disconnect(self):
        self.disconnected = True


def _config():
    return FeishuClientConfig(app_id="cli_test", app_secret="secret")


def test_disabled_listener_never_builds_or_connects():
    calls = []
    listener = FeishuIngressListener(
        FakeProducer(),
        _config(),
        enabled=False,
        client_factory=lambda *a, **k: calls.append((a, k)),
    )
    asyncio.run(listener.run())
    assert calls == []
    assert listener.health.status == "disabled"


def test_listener_connects_handles_event_and_stops_cleanly():
    producer = FakeProducer()
    client = FakeConnectedClient()
    handlers = {}

    def factory(config, **callbacks):
        del config
        handlers.update(callbacks)
        return client

    listener = FeishuIngressListener(
        producer, _config(), enabled=True, client_factory=factory
    )

    async def scenario():
        task = asyncio.create_task(listener.run())
        await asyncio.sleep(0)
        await listener.wait_ready()
        await handlers["on_message"]("message")
        await listener.stop()
        await task

    asyncio.run(scenario())
    assert producer.messages == ["message"]
    assert client.connected and client.disconnected
    assert listener.health.events_seen == 1


def test_ingress_and_sdk_errors_are_recorded_without_error_text():
    producer = FakeProducer(error=RuntimeError("secret=do-not-store"))
    listener = FeishuIngressListener(producer, _config(), enabled=True)

    async def scenario():
        await listener._on_message(object())
        await listener._on_error(ValueError("token=do-not-store"))
        await listener._on_reconnecting()

    asyncio.run(scenario())
    details = "\n".join(item[3] for item in producer.store.errors)
    kinds = [item[2] for item in producer.store.errors]
    assert "do-not-store" not in details
    assert "feishu_ingress_callback_failed" in kinds
    assert "feishu_sdk_error" in kinds
    assert "feishu_sdk_reconnecting" in kinds


def test_connect_failure_is_persisted_sanitized():
    producer = FakeProducer()

    class FailedClient(FakeConnectedClient):
        async def connect_until_ready(self, timeout=30):
            del timeout
            raise RuntimeError("app_secret=do-not-store")

    listener = FeishuIngressListener(
        producer,
        _config(),
        enabled=True,
        client_factory=lambda *args, **kwargs: FailedClient(),
    )
    with pytest.raises(RuntimeError):
        asyncio.run(listener.run())
    errors = [item for item in producer.store.errors if item[2] == "feishu_connect_failed"]
    assert errors
    assert "do-not-store" not in errors[0][3]
