"""Service-owned credential boundary for MCP transports exposed to Claude."""

from __future__ import annotations

import argparse
import http.client
import os
import sys
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from app.codex_runtime_adapter import _safe_child_environment
from app.service_codex_config import ServiceMcpServer

_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


class ClaudeMcpCredentialProxyManager:
    """Create local transports while retaining credentials outside Claude."""

    def __init__(self, *, root: Path) -> None:
        self._root = root
        self._servers: list[ThreadingHTTPServer] = []

    def prepare(
        self,
        server: ServiceMcpServer,
        *,
        source_env: Mapping[str, str],
    ) -> dict[str, object]:
        if server.args_env is not None:
            raise ValueError("Claude reviewed MCP args_env is not safely supported")
        if server.command is not None:
            return {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    "-m",
                    "app.claude_mcp_proxy",
                    "--exec",
                    server.command,
                    *server.args,
                ],
            }
        if server.url is None:
            raise ValueError("Claude reviewed MCP transport is incomplete")
        headers = dict(server.http_headers)
        for header, env_name in server.env_http_headers:
            headers[header] = _required_secret(source_env, env_name)
        if server.bearer_token_env_var is not None:
            headers["Authorization"] = (
                "Bearer "
                + _required_secret(source_env, server.bearer_token_env_var)
            )
        proxy = _start_remote_proxy(server.url, headers)
        self._servers.append(proxy)
        return {
            "type": "http",
            "url": f"http://127.0.0.1:{proxy.server_port}/mcp",
        }

    def close(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()
        self._servers.clear()


def _required_secret(source_env: Mapping[str, str], name: str) -> str:
    value = source_env.get(name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Claude MCP proxy credential is missing")
    return value


def _start_remote_proxy(
    target_url: str,
    injected_headers: Mapping[str, str],
) -> ThreadingHTTPServer:
    parsed = urlsplit(target_url)

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            self._forward()

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            self._forward()

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler contract
            self._forward()

        def _forward(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else None
            connection_type = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            connection = connection_type(
                parsed.hostname,
                parsed.port,
                timeout=60,
            )
            forwarded_headers = {
                name: value
                for name, value in self.headers.items()
                if name.casefold() not in _HOP_BY_HOP_HEADERS
                and name.casefold() != "host"
            }
            forwarded_headers.update(injected_headers)
            target_path = parsed.path or "/"
            if parsed.query:
                target_path += f"?{parsed.query}"
            try:
                connection.request(
                    self.command,
                    target_path,
                    body=body,
                    headers=forwarded_headers,
                )
                response = connection.getresponse()
                payload = response.read()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.casefold() not in _HOP_BY_HOP_HEADERS:
                        self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)
            except Exception:  # noqa: BLE001 - never expose target/secret detail
                self.send_error(502, "MCP proxy unavailable")
            finally:
                connection.close()

        def log_message(self, *_args: object) -> None:
            return

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    return proxy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exec", dest="target", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.target:
        raise SystemExit(2)
    env = _safe_child_environment(dict(os.environ))
    env.pop("ANTHROPIC_API_KEY", None)
    os.execvpe(args.target[0], args.target, env)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
