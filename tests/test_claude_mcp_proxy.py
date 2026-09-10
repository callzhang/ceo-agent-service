import io
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import repo_root
from app.claude_mcp_proxy import (
    ClaudeMcpCredentialProxyManager,
    _spawn_proxy_process,
    main,
)
from app.service_codex_config import ServiceMcpServer


def _post(url, payload, headers):
    return urllib.request.urlopen(
        urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **headers},
        )
    )


def test_remote_proxy_injects_only_target_credentials(tmp_path):
    received = {}

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
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
            source_env={
                "CONNECTOR_API_KEY": "raw-memory-secret",
                "MEMORY_AUTH_TYPE": "oauth",
                "FOREIGN_API_KEY": "raw-foreign-secret",
            },
        )
        assert "raw-memory-secret" not in json.dumps(transport)
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        with _post(transport["url"], request, transport["headers"]) as response:
            assert response.status == 200
        assert received == {
            "authorization": "Bearer raw-memory-secret",
            "memory_auth": "oauth",
            "body": json.dumps(request).encode(),
        }
    finally:
        manager.close()
        target.shutdown()


def test_remote_proxy_requires_invocation_authentication(tmp_path):
    calls = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(1)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    manager = ClaudeMcpCredentialProxyManager(root=tmp_path)
    try:
        transport = manager.prepare(
            ServiceMcpServer(
                name="provider", url=f"http://127.0.0.1:{target.server_port}/mcp"
            ),
            invocation_id="auth-boundary",
            source_env={},
        )
        for headers in ({}, {"X-CEO-Runtime-Invocation": "wrong"}):
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(transport["url"], {"future": "payload"}, headers)
            assert exc.value.code == 401
        assert calls == []
    finally:
        manager.close()
        target.shutdown()


def test_remote_proxy_forwards_unknown_tools_and_provider_payloads_unchanged(tmp_path):
    received = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            payload = b"event: message\ndata: {\"future\":true}\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
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
                name="new_provider",
                url=f"http://127.0.0.1:{target.server_port}/mcp",
            ),
            invocation_id="transparent-tool",
            source_env={},
        )
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "future_action", "arguments": {"new": "shape"}},
        }
        with _post(transport["url"], request, transport["headers"]) as response:
            assert response.headers["Content-Type"] == "text/event-stream"
            assert response.read().startswith(b"event: message")
        assert received == [request]
    finally:
        manager.close()
        target.shutdown()


def test_stdio_wrapper_forwards_unknown_notifications_and_strips_ambient_credentials(
    monkeypatch,
):
    class RetainedBytesIO(io.BytesIO):
        def close(self):
            self.snapshot = self.getvalue()
            super().close()

    class FakeProcess:
        def __init__(self):
            self.stdin = RetainedBytesIO()
            self.stdout = RetainedBytesIO(b'{"future":"response"}\n')
            self.returncode = 0

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            del timeout
            return 0

    captured = {}
    process = FakeProcess()
    request = b'{"jsonrpc":"2.0","method":"notifications/future"}\n'
    output = io.BytesIO()

    class BinaryFacade:
        def __init__(self, buffer):
            self.buffer = buffer

    def fake_popen(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return process

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("CONNECTOR_API_KEY", "connector-secret")
    monkeypatch.setattr("app.claude_mcp_proxy.subprocess.Popen", fake_popen)
    monkeypatch.setattr("app.claude_mcp_proxy.sys.stdin", BinaryFacade(io.BytesIO(request)))
    monkeypatch.setattr("app.claude_mcp_proxy.sys.stdout", BinaryFacade(output))

    assert main(["--exec", "/opt/service/provider-mcp"]) == 0
    assert process.stdin.snapshot == request
    assert output.getvalue() == b'{"future":"response"}\n'
    assert "ANTHROPIC_API_KEY" not in captured["kwargs"]["env"]
    assert "CONNECTOR_API_KEY" not in captured["kwargs"]["env"]


def test_proxy_startup_error_terminates_waits_and_closes_pipes(monkeypatch):
    class FailedStartup:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()
            self.terminated = False
            self.waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            del timeout
            self.waited = True
            return 1

    process = FailedStartup()
    monkeypatch.setattr(
        "app.claude_mcp_proxy.subprocess.Popen", lambda *_args, **_kwargs: process
    )

    with pytest.raises(ValueError, match="failed to start"):
        _spawn_proxy_process("remote", {"secret": "not-argv-or-env"})

    assert process.terminated is True
    assert process.waited is True
    assert process.stdin.closed is True
    assert process.stdout.closed is True


def test_stdio_wrapper_forwards_one_message_before_the_client_closes_stdin():
    """A live MCP client holds stdin open, so a single line must not be buffered."""
    echo_server = (
        "import sys\n"
        "for line in sys.stdin:\n"
        "    sys.stdout.write(line)\n"
        "    sys.stdout.flush()\n"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.claude_mcp_proxy",
            "--exec",
            sys.executable,
            "-c",
            echo_server,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo_root(),
    )
    try:
        request = b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
        process.stdin.write(request)
        process.stdin.flush()
        # stdin deliberately stays open, exactly as an MCP client keeps it.
        reply = _read_line_within(process.stdout, timeout_seconds=15)
        assert reply == request
    finally:
        process.kill()
        process.wait(timeout=10)


def _read_line_within(stream, *, timeout_seconds: float) -> bytes:
    result: list[bytes] = []
    reader = threading.Thread(target=lambda: result.append(stream.readline()))
    reader.daemon = True
    reader.start()
    reader.join(timeout_seconds)
    assert result, "the proxy did not forward the request before stdin was closed"
    return result[0]
