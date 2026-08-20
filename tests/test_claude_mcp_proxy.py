import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.claude_mcp_proxy import (
    ClaudeMcpCredentialProxyManager,
    _spawn_proxy_process,
    main,
)
from app.service_codex_config import ServiceMcpServer


def _issue_grant(manager, invocation_id, server_name, tool, arguments):
    endpoint = manager.grant_descriptor(invocation_id, server_name)
    request = urllib.request.Request(
        endpoint["url"],
        data=json.dumps({"tool": tool, "arguments": arguments}).encode(),
        headers={
            "Content-Type": "application/json",
            "X-CEO-Runtime-Invocation": endpoint["token"],
        },
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())["grant"]


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


def test_proxy_rejects_unauthenticated_and_unknown_tools_before_target(tmp_path):
    calls = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.rfile.read(int(self.headers["Content-Length"])))
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
            ),
            invocation_id="invocation-adversary",
            allowed_tools=("mcp__memory_connector__memory_recall",),
            source_env={},
        )
        bare_allowed = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "memory_recall", "arguments": {"query": "safe"}},
            }
        ).encode()
        for headers in ({}, {"X-CEO-Runtime-Invocation": "wrong"}):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(
                    urllib.request.Request(
                        transport["url"], data=bare_allowed, headers=headers
                    )
                )
            assert exc.value.code == 401
        for payload in (
            {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "memory_write", "arguments": {"data": "no"}},
            },
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
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(
                urllib.request.Request(
                    transport["url"], data=bare_allowed, headers=transport["headers"]
                )
            )
        assert exc.value.code == 403

        arguments = {"query": "safe"}
        grant = _issue_grant(
            manager,
            "invocation-adversary",
            "memory_connector",
            "mcp__memory_connector__memory_recall",
            arguments,
        )
        wrong_arguments = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "memory_recall",
                    "arguments": {
                        "query": "changed",
                        "__ceo_runtime_grant": grant,
                    },
                },
            }
        ).encode()
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(
                urllib.request.Request(
                    transport["url"],
                    data=wrong_arguments,
                    headers=transport["headers"],
                )
            )
        assert exc.value.code == 403
        assert calls == []
        grant = _issue_grant(
            manager,
            "invocation-adversary",
            "memory_connector",
            "mcp__memory_connector__memory_recall",
            arguments,
        )
        allowed = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "memory_recall",
                    "arguments": {**arguments, "__ceo_runtime_grant": grant},
                },
            }
        ).encode()
        with urllib.request.urlopen(
            urllib.request.Request(
                transport["url"], data=allowed, headers=transport["headers"]
            )
        ) as response:
            assert response.status == 200
        assert len(calls) == 1
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(
                urllib.request.Request(
                    transport["url"], data=allowed, headers=transport["headers"]
                )
            )
        assert exc.value.code == 403
        assert len(calls) == 1
    finally:
        manager.close()
        target.shutdown()


def test_proxy_grant_is_bound_to_one_invocation(tmp_path):
    calls = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(1)
            self.rfile.read(int(self.headers["Content-Length"]))
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
    server = ServiceMcpServer(
        name="memory_connector",
        url=f"http://127.0.0.1:{target.server_port}/mcp",
    )
    try:
        first = manager.prepare(
            server,
            invocation_id="first",
            allowed_tools=("mcp__memory_connector__memory_recall",),
            source_env={},
        )
        second = manager.prepare(
            server,
            invocation_id="second",
            allowed_tools=("mcp__memory_connector__memory_recall",),
            source_env={},
        )
        grant = _issue_grant(
            manager,
            "first",
            "memory_connector",
            "mcp__memory_connector__memory_recall",
            {"query": "safe"},
        )
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "memory_recall",
                    "arguments": {
                        "query": "safe",
                        "__ceo_runtime_grant": grant,
                    },
                },
            }
        ).encode()
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(
                urllib.request.Request(
                    second["url"], data=payload, headers=second["headers"]
                )
            )
        assert exc.value.code == 403
        assert calls == []
        with urllib.request.urlopen(
            urllib.request.Request(first["url"], data=payload, headers=first["headers"])
        ) as response:
            assert response.status == 200
        assert calls == [1]
    finally:
        manager.close()
        target.shutdown()


def test_remote_proxy_separates_client_and_broker_tokens(tmp_path):
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
                name="memory_connector",
                url=f"http://127.0.0.1:{target.server_port}/mcp",
            ),
            invocation_id="split-token",
            allowed_tools=("mcp__memory_connector__memory_recall",),
            source_env={},
        )
        broker = manager.grant_descriptor("split-token", "memory_connector")
        assert broker["token"] != transport["headers"]["X-CEO-Runtime-Invocation"]
        grant_request = {
            "tool": "mcp__memory_connector__memory_recall",
            "arguments": {"query": "safe"},
        }
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(broker["url"], grant_request, transport["headers"])
        assert exc.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(
                transport["url"],
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"X-CEO-Runtime-Invocation": broker["token"]},
            )
        assert exc.value.code == 401
        for invalid in (
            {"jsonrpc": "2.0", "method": "notifications/foreign"},
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            ],
        ):
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(transport["url"], invalid, transport["headers"])
            assert exc.value.code == 403
        assert calls == []
    finally:
        manager.close()
        target.shutdown()


def test_remote_proxy_forwards_jsonrpc_notifications_without_json_response(tmp_path):
    received = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(payload)
            if "id" not in payload:
                self.send_response(202)
                self.end_headers()
                return
            response = json.dumps(
                {"jsonrpc": "2.0", "id": payload["id"], "result": {}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

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
            invocation_id="notification-handshake",
            allowed_tools=("mcp__memory_connector__memory_recall",),
            source_env={},
        )
        for method in ("notifications/initialized", "notifications/cancelled"):
            with _post(
                transport["url"],
                {"jsonrpc": "2.0", "method": method, "params": {}},
                transport["headers"],
            ) as response:
                assert response.status == 202
                assert response.read() == b""
        with _post(
            transport["url"],
            {"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}},
            transport["headers"],
        ) as response:
            assert json.loads(response.read())["id"] == 7
        assert [item["method"] for item in received] == [
            "notifications/initialized",
            "notifications/cancelled",
            "initialize",
        ]
    finally:
        manager.close()
        target.shutdown()


def test_proxy_filters_tools_list_schema_and_rejects_sse_response(tmp_path):
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if request["method"] == "initialize":
                payload = b"event: message\ndata: {}\n\n"
                content_type = "text/event-stream"
            elif request["method"] == "ping":
                payload = b'{"jsonrpc":"2.0","id":999,"result":{}}'
                content_type = "application/json"
            else:
                payload = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {
                            "tools": [
                                {"name": "memory_recall", "inputSchema": schema},
                                {"name": "memory_write", "inputSchema": schema},
                                {
                                    "name": "memory_recall",
                                    "inputSchema": {"type": "object"},
                                },
                            ]
                        },
                    },
                    separators=(",", ":"),
                ).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
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
            invocation_id="json-only",
            allowed_tools=("mcp__memory_connector__memory_recall",),
            source_env={},
        )
        tools_request = urllib.request.Request(
            transport["url"],
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            headers=transport["headers"],
        )
        with urllib.request.urlopen(tools_request) as response:
            body = response.read()
            assert int(response.headers["Content-Length"]) == len(body)
        assert json.loads(body)["result"]["tools"] == [
            {"name": "memory_recall", "inputSchema": schema}
        ]

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(
                urllib.request.Request(
                    transport["url"],
                    data=b'{"jsonrpc":"2.0","id":2,"method":"initialize"}',
                    headers=transport["headers"],
                ),
                timeout=2,
            )
        assert exc.value.code == 502
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(
                transport["url"],
                {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                transport["headers"],
            )
        assert exc.value.code == 502
    finally:
        manager.close()
        target.shutdown()


def test_stdio_wrapper_strips_provider_and_ambient_credentials(monkeypatch):
    captured = {}

    class FakeProcess:
        def __init__(self):
            self.stdin = __import__("io").BytesIO()
            self.stdout = __import__("io").BytesIO()
            self.returncode = 0
            self.waits = 0
            self.killed = False

        def terminate(self):
            return None

        def wait(self, timeout):
            del timeout
            self.waits += 1
            if self.waits == 1:
                raise __import__("subprocess").TimeoutExpired("target", 5)
            return 0

        def kill(self):
            self.killed = True

    def fake_popen(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        captured["process"] = FakeProcess()
        return captured["process"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("CONNECTOR_API_KEY", "connector-secret")
    monkeypatch.setattr("app.claude_mcp_proxy.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "app.claude_mcp_proxy.sys.stdin",
        __import__("io").TextIOWrapper(__import__("io").BytesIO()),
    )

    assert (
        main(
            [
                "--server",
                "memory_connector",
                "--allowed-tool",
                "mcp__memory_connector__memory_recall",
                "--grant-url",
                "http://127.0.0.1:1/consume",
                "--consume-token",
                "invocation-token",
                "--exec",
                "/opt/service/memory-mcp",
                "serve",
                "--stdio",
            ]
        )
        == 0
    )

    assert captured["argv"] == [
        "/opt/service/memory-mcp",
        "serve",
        "--stdio",
    ]
    assert "ANTHROPIC_API_KEY" not in captured["kwargs"]["env"]
    assert "CONNECTOR_API_KEY" not in captured["kwargs"]["env"]
    assert captured["process"].killed is True
    assert captured["process"].waits == 2
    assert captured["process"].stdin.closed is True
    assert captured["process"].stdout.closed is True


def test_proxy_startup_error_terminates_waits_and_closes_pipes(monkeypatch):
    class FailedStartup:
        def __init__(self):
            self.stdin = __import__("io").BytesIO()
            self.stdout = __import__("io").BytesIO()
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


def test_stdio_forwards_notification_without_consuming_request_response(monkeypatch):
    import io

    class RetainedBytesIO(io.BytesIO):
        def close(self):
            self.snapshot = self.getvalue()
            super().close()

    class FakeProcess:
        def __init__(self):
            self.stdin = RetainedBytesIO()
            self.stdout = RetainedBytesIO(b'{"jsonrpc":"2.0","id":7,"result":{}}\n')
            self.returncode = 0

        def terminate(self):
            return None

        def wait(self, timeout):
            del timeout
            return 0

    process = FakeProcess()
    stdin_bytes = io.BytesIO(
        b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        b'{"jsonrpc":"2.0","id":7,"method":"initialize"}\n'
    )
    output = io.BytesIO()

    class BinaryFacade:
        def __init__(self, buffer):
            self.buffer = buffer

    monkeypatch.setattr(
        "app.claude_mcp_proxy.subprocess.Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr("app.claude_mcp_proxy.sys.stdin", BinaryFacade(stdin_bytes))
    monkeypatch.setattr("app.claude_mcp_proxy.sys.stdout", BinaryFacade(output))

    assert (
        main(
            [
                "--server",
                "memory_connector",
                "--allowed-tool",
                "mcp__memory_connector__memory_recall",
                "--grant-url",
                "http://127.0.0.1:1/consume",
                "--consume-token",
                "client-token",
                "--exec",
                "/opt/service/memory-mcp",
            ]
        )
        == 0
    )
    assert process.stdin.snapshot == stdin_bytes.getvalue()
    assert output.getvalue().splitlines() == [b'{"jsonrpc":"2.0","id":7,"result":{}}']


def test_stdio_unknown_notification_terminates_without_response_or_target(monkeypatch):
    import io

    class RetainedBytesIO(io.BytesIO):
        def close(self):
            self.snapshot = self.getvalue()
            super().close()

    class FakeProcess:
        def __init__(self):
            self.stdin = RetainedBytesIO()
            self.stdout = RetainedBytesIO()
            self.returncode = 0

        def terminate(self):
            return None

        def wait(self, timeout):
            del timeout
            return 0

    process = FakeProcess()
    output = io.BytesIO()

    class BinaryFacade:
        def __init__(self, buffer):
            self.buffer = buffer

    monkeypatch.setattr(
        "app.claude_mcp_proxy.subprocess.Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(
        "app.claude_mcp_proxy.sys.stdin",
        BinaryFacade(
            io.BytesIO(b'{"jsonrpc":"2.0","method":"notifications/foreign"}\n')
        ),
    )
    monkeypatch.setattr("app.claude_mcp_proxy.sys.stdout", BinaryFacade(output))

    assert (
        main(
            [
                "--server",
                "memory_connector",
                "--allowed-tool",
                "mcp__memory_connector__memory_recall",
                "--grant-url",
                "http://127.0.0.1:1/consume",
                "--consume-token",
                "client-token",
                "--exec",
                "/opt/service/memory-mcp",
            ]
        )
        == 1
    )
    assert process.stdin.snapshot == b""
    assert output.getvalue() == b""
