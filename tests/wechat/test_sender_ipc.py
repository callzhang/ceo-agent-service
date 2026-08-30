import importlib
import importlib.util
import os
import socketserver
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from app.wechat.accessibility import AccessibilityResult


def _module():
    assert importlib.util.find_spec("app.wechat.sender_ipc") is not None
    return importlib.import_module("app.wechat.sender_ipc")


def _short_socket_path() -> Path:
    return Path("/tmp") / f"ceo-wx-send-{os.getpid()}-{uuid4().hex[:8]}.sock"


class FakeAccessibility:
    def __init__(self):
        self.calls = []

    def preflight(self):
        return "ready"

    def request_accessibility(self):
        self.calls.append(("request_accessibility",))
        return "ready"

    def open_and_identify(
        self, target_label, *, search_query=None, expected_recent_text=None,
    ):
        self.calls.append((
            "open_and_identify", target_label, search_query,
            expected_recent_text,
        ))
        return target_label

    def send(
        self, target_label, reply_text, *, search_query=None,
        expected_recent_text=None,
    ):
        self.calls.append((
            "send", target_label, reply_text, search_query,
            expected_recent_text,
        ))
        return AccessibilityResult(True, True, "fp-1")

    def recall_last_outbound(self, text):
        self.calls.append(("recall", text))
        return True


def test_sender_rpc_exposes_only_bounded_accessibility_operations():
    module = _module()
    runner = FakeAccessibility()
    service = module.WechatSenderRpcService(runner)

    assert service.dispatch("health", {}) == {
        "status": "ready",
        "protocol_version": 1,
    }
    assert service.dispatch("preflight", {}) == "ready"
    assert service.dispatch("request_accessibility", {}) == "ready"
    assert service.dispatch("open_and_identify", {
        "target_label": "Melody",
        "expected_recent_text": "那他为啥问我要材料呢",
    }) == "Melody"
    assert service.dispatch("send", {
        "target_label": "Melody",
        "expected_recent_text": "那他为啥问我要材料呢",
        "reply_text": "收到",
    }) == {
        "action_performed": True,
        "visible_confirmation": True,
        "target_fingerprint": "fp-1",
        "failure_reason": "",
    }
    assert service.dispatch("recall_last_outbound", {"text": "收到"}) is True
    with pytest.raises(module.SenderIpcError, match="unsupported method"):
        service.dispatch("run_applescript", {"script": "arbitrary"})
    with pytest.raises(module.SenderIpcError, match="reply_text"):
        service.dispatch("send", {
            "target_label": "Melody", "reply_text": "x" * 10_001,
        })


def test_sender_client_round_trip_over_owner_only_socket():
    module = _module()
    runner = FakeAccessibility()
    socket_path = _short_socket_path()
    server = module.WechatSenderUnixServer(
        socket_path, module.WechatSenderRpcService(runner),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = module.WechatSenderClient(socket_path, timeout_seconds=1)
        assert client.health()["status"] == "ready"
        assert client.preflight() == "ready"
        assert client.request_accessibility() == "ready"
        assert client.open_and_identify(
            "Melody", expected_recent_text="那他为啥问我要材料呢",
        ) == "Melody"
        result = client.send(
            "Melody", "收到",
            expected_recent_text="那他为啥问我要材料呢",
        )
        assert result == AccessibilityResult(True, True, "fp-1")
        assert client.recall_last_outbound("收到") is True
        assert socket_path.stat().st_mode & 0o777 == 0o600
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sender_server_serializes_accessibility_requests():
    module = _module()

    assert not issubclass(module.WechatSenderUnixServer, socketserver.ThreadingMixIn)


def test_sender_client_fails_closed_when_helper_is_not_running(tmp_path):
    module = _module()
    client = module.WechatSenderClient(tmp_path / "missing.sock", timeout_seconds=0.1)
    with pytest.raises(module.SenderExecutionError, match="unavailable") as exc:
        client.health()
    assert exc.value.action_may_have_started is False


def test_sender_handler_ignores_client_disconnect_when_writing_response():
    module = _module()

    class ClosedWriter:
        def write(self, payload):
            raise BrokenPipeError

    handler = object.__new__(module._SenderRequestHandler)
    handler.wfile = ClosedWriter()
    handler._reply({"ok": True})
