"""Invocation-scoped credential boundary for MCP transports exposed to Claude."""

from __future__ import annotations

import argparse
import http.client
import json
import multiprocessing
import os
import secrets
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from app.agent_effects import McpToolEffectRegistry
from app.codex_runtime_adapter import _safe_child_environment
from app.service_codex_config import ServiceMcpServer

_AUTH_HEADER = "X-CEO-Runtime-Invocation"
_HOP_BY_HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
)
_CONTROL_METHODS = frozenset(
    {"initialize", "notifications/initialized", "ping", "tools/list"}
)


@dataclass(slots=True)
class _ProxyProcess:
    process: multiprocessing.Process
    token: str


class ClaudeMcpCredentialProxyManager:
    """Own authenticated proxy processes for exact invocation/server pairs."""

    def __init__(self, *, root: Path) -> None:
        self._root = root
        self._processes: dict[str, list[_ProxyProcess]] = {}

    @property
    def active_process_count(self) -> int:
        return sum(
            owned.process.is_alive()
            for processes in self._processes.values()
            for owned in processes
        )

    def prepare(
        self,
        server: ServiceMcpServer,
        *,
        invocation_id: str,
        allowed_tools: Sequence[str],
        source_env: Mapping[str, str],
    ) -> dict[str, object]:
        if not invocation_id or invocation_id != invocation_id.strip():
            raise ValueError("Claude MCP proxy invocation id is invalid")
        if server.args_env is not None:
            raise ValueError("Claude reviewed MCP args_env is not safely supported")
        if server.command is not None:
            exact_tools = tuple(sorted(set(allowed_tools)))
            if not exact_tools or any(
                not tool.startswith(f"mcp__{server.name}__")
                for tool in exact_tools
            ):
                raise ValueError("Claude MCP proxy tools must be exact for one server")
            return {
                "type": "stdio", "command": sys.executable,
                "args": [
                    "-m", "app.claude_mcp_proxy", "--server", server.name,
                    *(option for tool in exact_tools for option in ("--allowed-tool", tool)),
                    "--exec", server.command, *server.args,
                ],
            }
        if server.url is None:
            raise ValueError("Claude reviewed MCP transport is incomplete")
        exact_tools = tuple(sorted(set(allowed_tools)))
        if not exact_tools or any(
            not tool.startswith(f"mcp__{server.name}__") for tool in exact_tools
        ):
            raise ValueError("Claude MCP proxy tools must be exact for one server")
        headers = dict(server.http_headers)
        for header, env_name in server.env_http_headers:
            headers[header] = _required_secret(source_env, env_name)
        if server.bearer_token_env_var is not None:
            headers["Authorization"] = "Bearer " + _required_secret(
                source_env, server.bearer_token_env_var
            )
        token = secrets.token_urlsafe(32)
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_serve_remote_proxy,
            args=(child, server.name, server.url, headers, token, exact_tools),
            daemon=True,
        )
        process.start()
        child.close()
        if not parent.poll(10):
            process.terminate()
            process.join(timeout=5)
            raise ValueError("Claude MCP proxy failed to start")
        port = parent.recv()
        parent.close()
        if not isinstance(port, int) or port <= 0 or not process.is_alive():
            process.join(timeout=5)
            raise ValueError("Claude MCP proxy failed to start")
        self._processes.setdefault(invocation_id, []).append(
            _ProxyProcess(process=process, token=token)
        )
        return {
            "type": "http",
            "url": f"http://127.0.0.1:{port}/mcp",
            "headers": {_AUTH_HEADER: token},
        }

    def close_invocation(self, invocation_id: str) -> None:
        for owned in self._processes.pop(invocation_id, []):
            if owned.process.is_alive():
                owned.process.terminate()
            owned.process.join(timeout=5)
            if owned.process.is_alive():
                owned.process.kill()
                owned.process.join(timeout=5)

    def close(self) -> None:
        for invocation_id in tuple(self._processes):
            self.close_invocation(invocation_id)


def _required_secret(source_env: Mapping[str, str], name: str) -> str:
    value = source_env.get(name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Claude MCP proxy credential is missing")
    return value


def _serve_remote_proxy(
    ready,
    server_name: str,
    target_url: str,
    injected_headers: Mapping[str, str],
    token: str,
    allowed_tools: Sequence[str],
) -> None:
    safe_env = _safe_child_environment(dict(os.environ))
    os.environ.clear()
    os.environ.update(safe_env)
    parsed = urlsplit(target_url)
    allowed = frozenset(allowed_tools)
    registry = McpToolEffectRegistry.default()

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._authenticated_transport_forward()

        def do_POST(self) -> None:  # noqa: N802
            if not self._authenticated():
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            if not _reviewed_jsonrpc_request(
                body, server_name=server_name, allowed_tools=allowed, registry=registry
            ):
                self.send_error(403, "MCP operation denied")
                return
            self._forward(body, request_method=_jsonrpc_method(body))

        def do_DELETE(self) -> None:  # noqa: N802
            self._authenticated_transport_forward()

        def _authenticated_transport_forward(self) -> None:
            if self._authenticated():
                self._forward(None, request_method=None)

        def _authenticated(self) -> bool:
            if not secrets.compare_digest(self.headers.get(_AUTH_HEADER, ""), token):
                self.send_error(401, "MCP invocation authentication required")
                return False
            return True

        def _forward(self, body: bytes | None, *, request_method: str | None) -> None:
            connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
            connection = connection_type(parsed.hostname, parsed.port, timeout=60)
            forwarded_headers = {
                name: value for name, value in self.headers.items()
                if name.casefold() not in _HOP_BY_HOP_HEADERS
                and name.casefold() not in {"host", _AUTH_HEADER.casefold()}
            }
            forwarded_headers.update(injected_headers)
            target_path = parsed.path or "/"
            if parsed.query:
                target_path += f"?{parsed.query}"
            try:
                connection.request(self.command, target_path, body=body, headers=forwarded_headers)
                response = connection.getresponse()
                payload = response.read()
                if request_method == "tools/list":
                    payload = _filter_tools_list(payload, server_name, allowed)
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.casefold() not in _HOP_BY_HOP_HEADERS:
                        self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)
            except Exception:  # noqa: BLE001
                self.send_error(502, "MCP proxy unavailable")
            finally:
                connection.close()

        def log_message(self, *_args: object) -> None:
            return

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    ready.send(proxy.server_port)
    ready.close()
    proxy.serve_forever()


def _reviewed_jsonrpc_request(
    body: bytes,
    *,
    server_name: str,
    allowed_tools: frozenset[str],
    registry: McpToolEffectRegistry,
) -> bool:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return False
    method = payload.get("method")
    if method in _CONTROL_METHODS:
        return True
    if method != "tools/call":
        return False
    params = payload.get("params")
    if not isinstance(params, dict):
        return False
    wire_name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(wire_name, str) or not wire_name or not isinstance(arguments, dict):
        return False
    exact_name = f"mcp__{server_name}__{wire_name}"
    if exact_name not in allowed_tools:
        return False
    return registry.classify(
        {"type": "mcp_tool_call", "server": server_name, "tool": wire_name, "arguments": arguments}
    ) is not None


def _jsonrpc_method(body: bytes) -> str | None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload.get("method") if isinstance(payload, dict) else None


def _filter_tools_list(
    body: bytes, server_name: str, allowed_tools: frozenset[str]
) -> bytes:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    result = payload.get("result") if isinstance(payload, dict) else None
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        return body
    allowed_names = {
        exact[len(f"mcp__{server_name}__") :]
        for exact in allowed_tools
        if exact.startswith(f"mcp__{server_name}__")
    }
    result["tools"] = [
        tool
        for tool in tools
        if isinstance(tool, dict) and tool.get("name") in allowed_names
    ]
    return json.dumps(payload, separators=(",", ":")).encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--allowed-tool", action="append", default=[])
    parser.add_argument("--exec", dest="target", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.target:
        raise SystemExit(2)
    env = _safe_child_environment(dict(os.environ))
    env.pop("ANTHROPIC_API_KEY", None)
    registry = McpToolEffectRegistry.default()
    allowed = frozenset(args.allowed_tool)
    process = subprocess.Popen(
        args.target,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        for line in sys.stdin.buffer:
            if not _reviewed_jsonrpc_request(
                line,
                server_name=args.server,
                allowed_tools=allowed,
                registry=registry,
            ):
                request_id = None
                try:
                    request = json.loads(line)
                    request_id = request.get("id") if isinstance(request, dict) else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
                denied = {
                    "jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": "MCP operation denied"},
                }
                sys.stdout.buffer.write(json.dumps(denied, separators=(",", ":")).encode() + b"\n")
                sys.stdout.buffer.flush()
                continue
            process.stdin.write(line)
            process.stdin.flush()
            response = process.stdout.readline()
            if not response:
                return 1
            if _jsonrpc_method(line) == "tools/list":
                response = _filter_tools_list(response, args.server, allowed) + b"\n"
            sys.stdout.buffer.write(response)
            sys.stdout.buffer.flush()
    finally:
        process.terminate()
        process.wait(timeout=5)
    return process.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
