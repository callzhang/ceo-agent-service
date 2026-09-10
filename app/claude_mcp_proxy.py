"""Invocation-scoped credential proxy for MCP transports exposed to Claude."""

from __future__ import annotations

import argparse
import http.client
import io
import json
import os
import secrets
import selectors
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from app.codex_runtime_adapter import _safe_child_environment
from app.service_codex_config import ServiceMcpServer

_AUTH_HEADER = "X-CEO-Runtime-Invocation"
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


@dataclass(slots=True)
class _ProxyProcess:
    process: subprocess.Popen[bytes]
    server_name: str


class ClaudeMcpCredentialProxyManager:
    """Keep provider credentials out of Claude-owned files and environment."""

    def __init__(self, *, root) -> None:
        del root
        self._processes: dict[str, list[_ProxyProcess]] = {}

    @property
    def active_process_count(self) -> int:
        return sum(
            owned.process.poll() is None
            for processes in self._processes.values()
            for owned in processes
        )

    def prepare(
        self,
        server: ServiceMcpServer,
        *,
        invocation_id: str,
        source_env: Mapping[str, str],
    ) -> dict[str, object]:
        if not invocation_id or invocation_id != invocation_id.strip():
            raise ValueError("Claude MCP proxy invocation id is invalid")
        if server.args_env is not None:
            raise ValueError("Claude MCP args_env is not supported")
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
            raise ValueError("Claude MCP transport is incomplete")
        headers = dict(server.http_headers)
        for header, env_name in server.env_http_headers:
            headers[header] = _required_secret(source_env, env_name)
        if server.bearer_token_env_var is not None:
            headers["Authorization"] = "Bearer " + _required_secret(
                source_env, server.bearer_token_env_var
            )
        client_token = secrets.token_urlsafe(32)
        process, port = _spawn_proxy_process(
            "remote",
            {
                "target_url": server.url,
                "injected_headers": headers,
                "client_token": client_token,
            },
        )
        self._processes.setdefault(invocation_id, []).append(
            _ProxyProcess(process=process, server_name=server.name)
        )
        return {
            "type": "http",
            "url": f"http://127.0.0.1:{port}/mcp",
            "headers": {_AUTH_HEADER: client_token},
        }

    def close_invocation(self, invocation_id: str) -> None:
        for owned in self._processes.pop(invocation_id, []):
            _stop_process(owned.process)

    def close(self) -> None:
        for invocation_id in tuple(self._processes):
            self.close_invocation(invocation_id)


def _required_secret(source_env: Mapping[str, str], name: str) -> str:
    value = source_env.get(name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Claude MCP proxy credential is missing")
    return value


def _spawn_proxy_process(
    mode: str, payload: Mapping[str, object]
) -> tuple[subprocess.Popen[bytes], int]:
    env = _safe_child_environment(dict(os.environ))
    process = subprocess.Popen(
        [sys.executable, "-m", "app.claude_mcp_proxy", f"--serve-{mode}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    selector: selectors.BaseSelector | None = None
    try:
        if process.stdin is None or process.stdout is None:
            raise ValueError("Claude MCP proxy pipes are unavailable")
        process.stdin.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
        process.stdin.close()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=10):
            raise ValueError("Claude MCP proxy failed to start")
        port = int(process.stdout.readline())
        if port <= 0 or process.poll() is not None:
            raise ValueError("Claude MCP proxy failed to start")
        process.stdout.close()
        return process, port
    except Exception:  # noqa: BLE001 - startup must roll back its child
        _stop_process(process)
        raise ValueError("Claude MCP proxy failed to start") from None
    finally:
        if selector is not None:
            selector.close()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if process.stdout is not None and not process.stdout.closed:
        process.stdout.close()


def _serve_remote_proxy(
    ready,
    target_url: str,
    injected_headers: Mapping[str, str],
    client_token: str,
) -> None:
    safe_env = _safe_child_environment(dict(os.environ))
    os.environ.clear()
    os.environ.update(safe_env)
    parsed = urlsplit(target_url)

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._forward()

        def do_POST(self) -> None:
            self._forward()

        def do_DELETE(self) -> None:
            self._forward()

        def _forward(self) -> None:
            if not secrets.compare_digest(
                self.headers.get(_AUTH_HEADER, ""), client_token
            ):
                self.send_error(401, "MCP invocation authentication required")
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else None
            connection_type = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            connection = connection_type(parsed.hostname, parsed.port, timeout=60)
            headers = {
                name: value
                for name, value in self.headers.items()
                if name.casefold() not in _HOP_BY_HOP_HEADERS
                and name.casefold()
                not in {"host", "content-length", _AUTH_HEADER.casefold()}
            }
            headers.update(injected_headers)
            if body is not None:
                headers["Content-Length"] = str(len(body))
            path = parsed.path or "/"
            if parsed.query:
                path += f"?{parsed.query}"
            try:
                connection.request(self.command, path, body=body, headers=headers)
                response = connection.getresponse()
                payload = response.read()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.casefold() not in _HOP_BY_HOP_HEADERS | {"content-length"}:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception:  # noqa: BLE001 - convert provider transport failure
                self.send_error(502, "MCP proxy unavailable")
            finally:
                connection.close()

        def log_message(self, *_args: object) -> None:
            return

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    ready.send(proxy.server_port)
    ready.close()
    proxy.serve_forever()


def _serve_stdio(command: Sequence[str]) -> int:
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr.buffer,
        env=_safe_child_environment(dict(os.environ)),
    )
    assert process.stdin is not None and process.stdout is not None

    def forward_requests() -> None:
        try:
            _copy_stream(sys.stdin.buffer, process.stdin)
        finally:
            process.stdin.close()

    request_thread = threading.Thread(target=forward_requests, daemon=True)
    request_thread.start()
    try:
        _copy_stream(process.stdout, sys.stdout.buffer)
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        request_thread.join(timeout=5)


def _copy_stream(source, destination) -> None:
    # read1 returns as soon as any bytes are available.  A buffered read()
    # blocks until the full size or EOF, which would hold one JSON-RPC message
    # inside the proxy until the client closed the stream and stall every
    # stdio MCP handshake.
    while chunk := source.read1(64 * 1024):
        destination.write(chunk)
        destination.flush()


class _StdoutReady:
    def __init__(self, stream: io.TextIOBase) -> None:
        self._stream = stream

    def send(self, port: int) -> None:
        self._stream.write(f"{port}\n")
        self._stream.flush()

    def close(self) -> None:
        return


def _read_bootstrap_payload(stream: io.BufferedIOBase) -> dict[str, object]:
    raw = stream.readline(256 * 1024)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Claude MCP proxy bootstrap is invalid") from None
    if not isinstance(payload, dict):
        raise ValueError("Claude MCP proxy bootstrap is invalid")
    return payload


def _required_payload_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Claude MCP proxy bootstrap is invalid")
    return value


def _required_string_mapping(payload: Mapping[str, object], key: str) -> dict[str, str]:
    value = payload.get(key)
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and name and isinstance(item, str) and item
        for name, item in value.items()
    ):
        raise ValueError("Claude MCP proxy bootstrap is invalid")
    return dict(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve-remote", action="store_true")
    parser.add_argument("--exec", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.serve_remote:
        payload = _read_bootstrap_payload(sys.stdin.buffer)
        _serve_remote_proxy(
            _StdoutReady(sys.stdout),
            _required_payload_string(payload, "target_url"),
            _required_string_mapping(payload, "injected_headers"),
            _required_payload_string(payload, "client_token"),
        )
        return 0
    if args.exec:
        return _serve_stdio(args.exec)
    parser.error("one proxy mode is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
