import json
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.claude_mcp_proxy import ClaudeMcpCredentialProxyManager, main
from app.service_codex_config import ServiceMcpServer


def test_remote_proxy_injects_only_target_credentials(tmp_path):
    received = {}

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - stdlib handler contract
            received["authorization"] = self.headers.get("Authorization")
            received["memory_auth"] = self.headers.get("X-Memory-Auth")
            received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            payload = b'{"jsonrpc":"2.0","id":1,"result":{}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    manager = ClaudeMcpCredentialProxyManager(root=tmp_path)
    try:
        transport = manager.prepare(
            ServiceMcpServer(
                name="memory_connector",
                url=f"http://127.0.0.1:{target.server_port}/mcp",
                bearer_token_env_var="CONNECTOR_API_KEY",
                env_http_headers=(("X-Memory-Auth", "MEMORY_AUTH_TYPE"),),
            ),
            invocation_id="invocation-1",
            allowed_tools=("mcp__memory_connector__memory_recall",),
            source_env={
                "CONNECTOR_API_KEY": "raw-memory-secret",
                "MEMORY_AUTH_TYPE": "oauth",
                "FOREIGN_API_KEY": "raw-foreign-secret",
            },
        )
        serialized = json.dumps(transport)
        assert "raw-memory-secret" not in serialized
        assert "CONNECTOR_API_KEY" not in serialized
        assert "MEMORY_AUTH_TYPE" not in serialized
        request = urllib.request.Request(
            transport["url"],
            data=b'{"jsonrpc":"2.0","id":1,"method":"initialize"}',
            headers={"Content-Type": "application/json", **transport["headers"]},
        )
        with urllib.request.urlopen(request) as response:
            assert response.status == 200

        assert received == {
            "authorization": "Bearer raw-memory-secret",
            "memory_auth": "oauth",
            "body": b'{"jsonrpc":"2.0","id":1,"method":"initialize"}',
        }
    finally:
        manager.close()
        target.shutdown()


def test_proxy_rejects_unauthenticated_and_unknown_tools_before_target(
    tmp_path
):
    calls = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            calls.append(self.rfile.read(int(self.headers["Content-Length"])))
            payload = b'{"jsonrpc":"2.0","id":1,"result":{}}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    manager = ClaudeMcpCredentialProxyManager(root=tmp_path)
    try:
        transport = manager.prepare(
            ServiceMcpServer(
                name="memory_connector",
                url=f"http://127.0.0.1:{target.server_port}/mcp",
            ),
            invocation_id="invocation-adversary",
            allowed_tools=("mcp__memory_connector__memory_recall",),
            source_env={},
        )
        allowed = json.dumps(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "memory_recall", "arguments": {"query": "safe"}},
            }
        ).encode()
        for headers in ({}, {"X-CEO-Runtime-Invocation": "wrong"}):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(
                    urllib.request.Request(transport["url"], data=allowed, headers=headers)
                )
            assert exc.value.code == 401
        for payload in (
            {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory_write", "arguments": {"data": "no"}}},
        ):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(
                    urllib.request.Request(
                        transport["url"],
                        data=json.dumps(payload).encode(),
                        headers=transport["headers"],
                    )
                )
            assert exc.value.code == 403
        assert calls == []
        with urllib.request.urlopen(
            urllib.request.Request(
                transport["url"], data=allowed, headers=transport["headers"]
            )
        ) as response:
            assert response.status == 200
        assert len(calls) == 1
    finally:
        manager.close()
        target.shutdown()


def test_stdio_wrapper_strips_provider_and_ambient_credentials(monkeypatch):
    captured = {}

    class FakeProcess:
        stdin = __import__("io").BytesIO()
        stdout = __import__("io").BytesIO()
        returncode = 0

        def terminate(self):
            return None

        def wait(self, timeout):
            del timeout
            return 0

    def fake_popen(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return FakeProcess()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("CONNECTOR_API_KEY", "connector-secret")
    monkeypatch.setattr("app.claude_mcp_proxy.subprocess.Popen", fake_popen)
    monkeypatch.setattr("app.claude_mcp_proxy.sys.stdin", __import__("io").TextIOWrapper(__import__("io").BytesIO()))

    assert main([
        "--server", "memory_connector",
        "--allowed-tool", "mcp__memory_connector__memory_recall",
        "--exec", "/opt/service/memory-mcp", "serve", "--stdio",
    ]) == 0

    assert captured["argv"] == [
        "/opt/service/memory-mcp",
        "serve",
        "--stdio",
    ]
    assert "ANTHROPIC_API_KEY" not in captured["kwargs"]["env"]
    assert "CONNECTOR_API_KEY" not in captured["kwargs"]["env"]
