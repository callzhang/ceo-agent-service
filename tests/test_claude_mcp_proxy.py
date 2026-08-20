import json
import threading
import urllib.request
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
            headers={"Content-Type": "application/json"},
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


def test_stdio_wrapper_strips_provider_and_ambient_credentials(monkeypatch):
    captured = {}

    def fake_exec(file, argv, env):
        captured.update(file=file, argv=argv, env=env)
        raise RuntimeError("exec intercepted")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("CONNECTOR_API_KEY", "connector-secret")
    monkeypatch.setattr("app.claude_mcp_proxy.os.execvpe", fake_exec)

    with pytest.raises(RuntimeError, match="exec intercepted"):
        main(["--exec", "/opt/service/memory-mcp", "serve", "--stdio"])

    assert captured["file"] == "/opt/service/memory-mcp"
    assert captured["argv"] == [
        "/opt/service/memory-mcp",
        "serve",
        "--stdio",
    ]
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert "CONNECTOR_API_KEY" not in captured["env"]
