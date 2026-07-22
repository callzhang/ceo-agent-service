import asyncio
from types import SimpleNamespace

import pytest

from app.feishu.client import (
    FeishuChannelClient,
    FeishuClientConfig,
    build_channel,
    normalize_send_result,
)
from app.feishu.models import FeishuDelivery
from tests.feishu.fakes import FakeRawChannel


def _delivery():
    return FeishuDelivery(
        id=1,
        reply_task_id=1,
        app_id="cli_test",
        chat_id="oc_1",
        reply_to_message_id="om_1",
        reply_in_thread=True,
        reply_text="收到",
        idempotency_key="e7c1c1ad-c345-5f9e-bddd-dace542577c9",
        status="sending",
    )


def test_send_reply_uses_original_message_and_fail_closed_fallback():
    raw = FakeRawChannel()
    result = asyncio.run(
        FeishuChannelClient(raw, app_id="cli_test").send_reply(_delivery())
    )
    assert result.success and result.message_id == "om_reply"
    to, message, opts = raw.send_calls[0]
    assert to == "oc_1" and message == {"text": "收到"}
    assert opts["reply_to"] == "om_1"
    assert opts["reply_target_gone"] == "fail"
    assert opts["uuid"] == _delivery().idempotency_key
    assert opts["resolve_mentions_in_text"] is False


def test_result_exposes_only_message_and_request_ids():
    raw = {"headers": {"x-tt-logid": "log-1", "authorization": "secret"}}
    result = normalize_send_result(
        SimpleNamespace(success=True, message_id="om_2", error=None, raw=raw)
    )
    assert result.request_log_id == "log-1"
    assert not hasattr(result, "raw")


def test_config_repr_does_not_leak_secret():
    config = FeishuClientConfig(app_id="cli_test", app_secret="super-secret")
    assert "super-secret" not in repr(config)


def test_real_sdk_import_is_delayed_until_build(monkeypatch):
    calls = []

    class SecurityConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class PolicyConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Channel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.handlers = {}

        def on(self, event, callback):
            self.handlers[event] = callback

    fake_sdk = SimpleNamespace(
        SecurityConfig=SecurityConfig,
        PolicyConfig=PolicyConfig,
        FeishuChannel=Channel,
    )

    def fake_import(name):
        calls.append(name)
        return fake_sdk

    monkeypatch.setattr("app.feishu.client.importlib.import_module", fake_import)
    client = build_channel(
        FeishuClientConfig(app_id="cli_test", app_secret="secret"),
        on_message=lambda message: None,
    )
    assert calls == ["lark_channel"]
    assert client.channel.kwargs["transport"] == "ws"
    assert client.app_id == "cli_test"
    assert client.channel.kwargs["security"].kwargs["mode"] == "strict"
    assert client.channel.kwargs["policy"].kwargs["require_mention"] is True


def test_missing_reply_target_is_rejected_before_sdk_call():
    raw = FakeRawChannel()
    invalid = _delivery().model_copy(update={"reply_to_message_id": ""})
    with pytest.raises(ValueError):
        asyncio.run(
            FeishuChannelClient(raw, app_id="cli_test").send_reply(invalid)
        )
    assert raw.send_calls == []


def test_client_rejects_delivery_for_another_app_before_sdk_call():
    raw = FakeRawChannel()
    delivery = _delivery().model_copy(update={"app_id": "cli_other"})

    with pytest.raises(PermissionError, match="App ID"):
        asyncio.run(
            FeishuChannelClient(raw, app_id="cli_test").send_reply(delivery)
        )

    assert raw.send_calls == []
