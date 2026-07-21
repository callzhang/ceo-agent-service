import os
import socket
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from app.wechat.models import WechatAccount, WechatCapability, WechatMessage
from app.wechat.reader_ipc import (
    ReaderIpcError,
    WechatReaderClient,
    WechatReaderRpcService,
    WechatReaderUnixServer,
)


def _account() -> WechatAccount:
    return WechatAccount(
        account_id="acct-1",
        display_name="Derek",
        self_user_id="wxid_self",
        account_dir="/private/wechat/acct-1",
        db_dir="/private/wechat/acct-1/db_storage",
        app_version="4.1.10.80",
    )


def _short_socket_path() -> Path:
    # macOS AF_UNIX paths are capped at roughly 104 bytes; pytest temp paths are long.
    return Path("/tmp") / f"ceo-wx-{os.getpid()}-{uuid4().hex[:8]}.sock"


class FakeLocalReader:
    def probe(self, account):
        return WechatCapability(
            status="ready",
            account_id=account.account_id,
            app_version=account.app_version,
        )

    def detect_self_username(self, account):
        return "wxid_self"

    def list_targets(self, account, *, kind, query, limit, offset):
        del account, query, limit, offset
        return [{
            "target_type": kind,
            "target_id": "friend-1",
            "conversation_id": "friend-1",
            "display_name": "Alice",
            "last_active_at": "2026-07-21T08:00:00+08:00",
        }]

    def read_messages(self, account, **kwargs):
        del kwargs
        return [WechatMessage(
            account_id=account.account_id,
            conversation_id="friend-1",
            message_id="msg-1",
            sender_id="friend-1",
            sender_display_name="Alice",
            conversation_type="direct",
            direction="inbound",
            sent_at="2026-07-21T08:00:00+08:00",
            kind="text",
            text="hello",
            source_version=account.app_version,
        )]


@pytest.fixture
def rpc_service():
    return WechatReaderRpcService(FakeLocalReader(), lambda: [_account()])


def test_rpc_service_exposes_only_reader_operations(rpc_service):
    assert rpc_service.dispatch("health", {}) == {
        "status": "ready",
        "protocol_version": 1,
    }
    assert rpc_service.dispatch("discover_accounts", {}) == [_account().model_dump(mode="json")]
    assert rpc_service.dispatch("detect_self_username", {"account": _account().model_dump()}) == "wxid_self"

    with pytest.raises(ReaderIpcError, match="unsupported method"):
        rpc_service.dispatch("read_file", {"path": "/etc/passwd"})


def test_rpc_service_validates_bounded_read_arguments(rpc_service):
    with pytest.raises(ReaderIpcError, match="limit"):
        rpc_service.dispatch("read_messages", {
            "account": _account().model_dump(),
            "limit": 501,
        })


def test_client_round_trip_over_owner_only_unix_socket(tmp_path, rpc_service):
    del tmp_path
    socket_path = _short_socket_path()
    server = WechatReaderUnixServer(socket_path, rpc_service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = WechatReaderClient(socket_path, timeout_seconds=1)
        assert client.health()["status"] == "ready"
        assert client.discover_accounts() == [_account()]
        assert client.probe(_account()).status == "ready"
        assert client.detect_self_username(_account()) == "wxid_self"
        assert client.list_targets(_account(), kind="direct")[0]["display_name"] == "Alice"
        assert client.read_messages(_account(), limit=100)[0].text == "hello"
        assert socket_path.stat().st_mode & 0o777 == 0o600
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_client_fails_closed_when_reader_is_not_running(tmp_path):
    client = WechatReaderClient(tmp_path / "missing.sock", timeout_seconds=0.1)
    with pytest.raises(ReaderIpcError, match="unavailable"):
        client.health()


def test_socket_rejects_oversized_requests(tmp_path, rpc_service):
    del tmp_path
    socket_path = _short_socket_path()
    server = WechatReaderUnixServer(socket_path, rpc_service, max_request_bytes=128)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.connect(str(socket_path))
            conn.sendall(b"{" + b"x" * 256 + b"}\n")
            response = conn.recv(4096)
        assert b"request_too_large" in response
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_socket_reports_app_data_permission_without_leaking_paths():
    def denied_accounts():
        raise PermissionError("denied: /private/secret/wechat/path")

    socket_path = _short_socket_path()
    server = WechatReaderUnixServer(
        socket_path,
        WechatReaderRpcService(FakeLocalReader(), denied_accounts),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ReaderIpcError, match="App Data permission") as caught:
            WechatReaderClient(socket_path).discover_accounts()
        assert "/private/secret" not in str(caught.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
