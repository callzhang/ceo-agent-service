"""Invocation-scoped credential boundary for MCP transports exposed to Claude."""

from __future__ import annotations

import argparse
import hashlib
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
from pathlib import Path
from urllib.parse import urlsplit

from app.agent_effects import McpToolEffectRegistry
from app.claude_tool_input import (
    reviewed_claude_tool_schema,
    validate_claude_tool_input,
)
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
_CONTROL_METHODS = frozenset(
    {"initialize", "notifications/initialized", "ping", "tools/list"}
)


@dataclass(slots=True)
class _ProxyProcess:
    process: subprocess.Popen[bytes]
    token: str
    server_name: str
    url: str


class ClaudeMcpCredentialProxyManager:
    """Own authenticated proxy processes for exact invocation/server pairs."""

    def __init__(self, *, root: Path) -> None:
        self._root = root
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
                not tool.startswith(f"mcp__{server.name}__") for tool in exact_tools
            ):
                raise ValueError("Claude MCP proxy tools must be exact for one server")
            token = secrets.token_urlsafe(32)
            process, port = _spawn_proxy_process(
                "grant",
                {
                    "server_name": server.name,
                    "token": token,
                    "allowed_tools": exact_tools,
                },
            )
            base_url = f"http://127.0.0.1:{port}"
            self._processes.setdefault(invocation_id, []).append(
                _ProxyProcess(
                    process=process,
                    token=token,
                    server_name=server.name,
                    url=base_url,
                )
            )
            return {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    "-m",
                    "app.claude_mcp_proxy",
                    "--server",
                    server.name,
                    *(
                        option
                        for tool in exact_tools
                        for option in ("--allowed-tool", tool)
                    ),
                    "--grant-url",
                    base_url + "/consume",
                    "--grant-token",
                    token,
                    "--exec",
                    server.command,
                    *server.args,
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
        process, port = _spawn_proxy_process(
            "remote",
            {
                "server_name": server.name,
                "target_url": server.url,
                "injected_headers": headers,
                "token": token,
                "allowed_tools": exact_tools,
            },
        )
        self._processes.setdefault(invocation_id, []).append(
            _ProxyProcess(
                process=process,
                token=token,
                server_name=server.name,
                url=f"http://127.0.0.1:{port}",
            )
        )
        return {
            "type": "http",
            "url": f"http://127.0.0.1:{port}/mcp",
            "headers": {_AUTH_HEADER: token},
        }

    def grant_descriptor(self, invocation_id: str, server_name: str) -> dict[str, str]:
        matches = [
            owned
            for owned in self._processes.get(invocation_id, ())
            if owned.server_name == server_name
        ]
        if len(matches) != 1:
            raise ValueError("Claude MCP proxy grant endpoint is unavailable")
        return {
            "url": matches[0].url + "/grant",
            "token": matches[0].token,
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
        raw_port = process.stdout.readline()
        port = int(raw_port)
        if port <= 0 or process.poll() is not None:
            raise ValueError("Claude MCP proxy failed to start")
        process.stdout.close()
        return process, port
    except Exception:  # noqa: BLE001 - startup must roll back every child failure
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
    grants: dict[str, tuple[str, str]] = {}
    grant_lock = threading.Lock()

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_error(405, "Streaming MCP transport is not supported")

        def do_POST(self) -> None:
            if not self._authenticated():
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            if self.path == "/grant":
                self._grant(body)
                return
            if self.path != "/mcp":
                self.send_error(404, "MCP endpoint not found")
                return
            forwarded = _authorized_jsonrpc_request(
                body,
                server_name=server_name,
                allowed_tools=allowed,
                registry=registry,
                grants=grants,
                grant_lock=grant_lock,
            )
            if forwarded is None:
                self.send_error(403, "MCP operation denied")
                return
            self._forward(forwarded, request_method=_jsonrpc_method(forwarded))

        def do_DELETE(self) -> None:
            self.send_error(405, "Streaming MCP transport is not supported")

        def _grant(self, body: bytes) -> None:
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(403, "MCP grant denied")
                return
            grant = _issue_grant(
                payload,
                server_name=server_name,
                allowed_tools=allowed,
                registry=registry,
                grants=grants,
                grant_lock=grant_lock,
            )
            if grant is None:
                self.send_error(403, "MCP grant denied")
                return
            response = json.dumps({"grant": grant}, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def _authenticated(self) -> bool:
            if not secrets.compare_digest(self.headers.get(_AUTH_HEADER, ""), token):
                self.send_error(401, "MCP invocation authentication required")
                return False
            return True

        def _forward(self, body: bytes | None, *, request_method: str | None) -> None:
            connection_type = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            connection = connection_type(parsed.hostname, parsed.port, timeout=60)
            forwarded_headers = {
                name: value
                for name, value in self.headers.items()
                if name.casefold() not in _HOP_BY_HOP_HEADERS
                and name.casefold()
                not in {"host", "content-length", _AUTH_HEADER.casefold()}
            }
            forwarded_headers.update(injected_headers)
            if body is not None:
                forwarded_headers["Content-Length"] = str(len(body))
            target_path = parsed.path or "/"
            if parsed.query:
                target_path += f"?{parsed.query}"
            try:
                connection.request(
                    self.command, target_path, body=body, headers=forwarded_headers
                )
                response = connection.getresponse()
                payload = response.read()
                content_type = response.getheader("Content-Type", "")
                if "application/json" not in content_type.casefold():
                    self.send_error(502, "MCP response is not safely reviewable JSON")
                    return
                if request_method == "tools/list":
                    filtered = _filter_tools_list(payload, server_name, allowed)
                    if filtered is None:
                        self.send_error(502, "MCP tools list is not safely reviewable")
                        return
                    payload = filtered
                else:
                    parsed_payload = json.loads(payload)
                    if not isinstance(parsed_payload, dict):
                        raise ValueError("MCP response must be a JSON object")
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.casefold() not in _HOP_BY_HOP_HEADERS | {"content-length"}:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
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


def _serve_grant_authority(
    ready,
    server_name: str,
    token: str,
    allowed_tools: Sequence[str],
) -> None:
    safe_env = _safe_child_environment(dict(os.environ))
    os.environ.clear()
    os.environ.update(safe_env)
    allowed = frozenset(allowed_tools)
    registry = McpToolEffectRegistry.default()
    grants: dict[str, tuple[str, str]] = {}
    grant_lock = threading.Lock()

    class GrantHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if not secrets.compare_digest(self.headers.get(_AUTH_HEADER, ""), token):
                self.send_error(401, "MCP invocation authentication required")
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(403, "MCP grant denied")
                return
            if self.path == "/grant":
                grant = _issue_grant(
                    payload,
                    server_name=server_name,
                    allowed_tools=allowed,
                    registry=registry,
                    grants=grants,
                    grant_lock=grant_lock,
                )
                if grant is None:
                    self.send_error(403, "MCP grant denied")
                    return
                self._json({"grant": grant})
                return
            if self.path == "/consume" and _consume_grant_payload(
                payload, grants=grants, grant_lock=grant_lock
            ):
                self._json({"allowed": True})
                return
            self.send_error(403, "MCP grant denied")

        def _json(self, payload: dict[str, object]) -> None:
            response = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), GrantHandler)
    ready.send(server.server_port)
    ready.close()
    server.serve_forever()


def _issue_grant(
    payload: object,
    *,
    server_name: str,
    allowed_tools: frozenset[str],
    registry: McpToolEffectRegistry,
    grants: dict[str, tuple[str, str]],
    grant_lock: threading.Lock,
) -> str | None:
    if not isinstance(payload, dict):
        return None
    exact_name = payload.get("tool")
    arguments = payload.get("arguments")
    prefix = f"mcp__{server_name}__"
    if (
        exact_name not in allowed_tools
        or not isinstance(exact_name, str)
        or not exact_name.startswith(prefix)
        or not isinstance(arguments, dict)
        or "__ceo_runtime_grant" in arguments
        or not validate_claude_tool_input(exact_name, arguments)
        or registry.classify(
            {
                "type": "mcp_tool_call",
                "server": server_name,
                "tool": exact_name[len(prefix) :],
                "arguments": arguments,
            }
        )
        is None
    ):
        return None
    grant = secrets.token_urlsafe(32)
    with grant_lock:
        grants[grant] = (exact_name, _arguments_digest(arguments))
    return grant


def _consume_grant_payload(
    payload: object,
    *,
    grants: dict[str, tuple[str, str]],
    grant_lock: threading.Lock,
) -> bool:
    if not isinstance(payload, dict):
        return False
    grant = payload.get("grant")
    tool = payload.get("tool")
    arguments = payload.get("arguments")
    if (
        not isinstance(grant, str)
        or not isinstance(tool, str)
        or not isinstance(arguments, dict)
    ):
        return False
    with grant_lock:
        expected = grants.pop(grant, None)
    return expected == (tool, _arguments_digest(arguments))


def _authorized_jsonrpc_request(
    body: bytes,
    *,
    server_name: str,
    allowed_tools: frozenset[str],
    registry: McpToolEffectRegistry,
    grants: dict[str, tuple[str, str]],
    grant_lock: threading.Lock,
) -> bytes | None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return None
    method = payload.get("method")
    if method in _CONTROL_METHODS:
        return body
    if method != "tools/call":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    wire_name = params.get("name")
    arguments = params.get("arguments", {})
    if (
        not isinstance(wire_name, str)
        or not wire_name
        or not isinstance(arguments, dict)
    ):
        return None
    arguments = dict(arguments)
    grant = arguments.pop("__ceo_runtime_grant", None)
    if not isinstance(grant, str) or not grant:
        return None
    exact_name = f"mcp__{server_name}__{wire_name}"
    if exact_name not in allowed_tools:
        return None
    if not validate_claude_tool_input(exact_name, arguments):
        return None
    if (
        registry.classify(
            {
                "type": "mcp_tool_call",
                "server": server_name,
                "tool": wire_name,
                "arguments": arguments,
            }
        )
        is None
    ):
        return None
    with grant_lock:
        expected = grants.pop(grant, None)
    if expected != (exact_name, _arguments_digest(arguments)):
        return None
    params["arguments"] = arguments
    return json.dumps(payload, separators=(",", ":")).encode()


def _arguments_digest(arguments: Mapping[str, object]) -> str:
    encoded = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _jsonrpc_method(body: bytes) -> str | None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload.get("method") if isinstance(payload, dict) else None


def _filter_tools_list(
    body: bytes, server_name: str, allowed_tools: frozenset[str]
) -> bytes | None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    result = payload.get("result") if isinstance(payload, dict) else None
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        return None
    allowed_names = {
        exact[len(f"mcp__{server_name}__") :]
        for exact in allowed_tools
        if exact.startswith(f"mcp__{server_name}__")
    }
    result["tools"] = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("name") not in allowed_names:
            continue
        exact_name = f"mcp__{server_name}__{tool['name']}"
        reviewed_schema = reviewed_claude_tool_schema(exact_name)
        if reviewed_schema is not None and tool.get("inputSchema") == reviewed_schema:
            result["tools"].append(tool)
    return json.dumps(payload, separators=(",", ":")).encode()


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv in (["--serve-remote"], ["--serve-grant"]):
        payload = _read_bootstrap_payload(sys.stdin.buffer)
        ready = _StdoutReady(sys.stdout)
        if raw_argv == ["--serve-remote"]:
            _serve_remote_proxy(
                ready,
                _required_payload_string(payload, "server_name"),
                _required_payload_string(payload, "target_url"),
                _required_string_mapping(payload, "injected_headers"),
                _required_payload_string(payload, "token"),
                _required_string_sequence(payload, "allowed_tools"),
            )
        else:
            _serve_grant_authority(
                ready,
                _required_payload_string(payload, "server_name"),
                _required_payload_string(payload, "token"),
                _required_string_sequence(payload, "allowed_tools"),
            )
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--allowed-tool", action="append", default=[])
    parser.add_argument("--grant-url", required=True)
    parser.add_argument("--grant-token", required=True)
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
            forwarded = _stdio_authorized_request(
                line,
                server_name=args.server,
                allowed_tools=allowed,
                registry=registry,
                grant_url=args.grant_url,
                grant_token=args.grant_token,
            )
            if forwarded is None:
                request_id = None
                try:
                    request = json.loads(line)
                    request_id = (
                        request.get("id") if isinstance(request, dict) else None
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
                denied = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "MCP operation denied"},
                }
                sys.stdout.buffer.write(
                    json.dumps(denied, separators=(",", ":")).encode() + b"\n"
                )
                sys.stdout.buffer.flush()
                continue
            process.stdin.write(forwarded)
            process.stdin.flush()
            response = process.stdout.readline()
            if not response:
                return 1
            if _jsonrpc_method(line) == "tools/list":
                filtered = _filter_tools_list(response, args.server, allowed)
                if filtered is None:
                    return 1
                response = filtered + b"\n"
            sys.stdout.buffer.write(response)
            sys.stdout.buffer.flush()
    finally:
        process.stdin.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
    return process.returncode or 0


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
        raise ValueError("Claude MCP proxy bootstrap is invalid")  # noqa: TRY004
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


def _required_string_sequence(
    payload: Mapping[str, object], key: str
) -> tuple[str, ...]:
    value = payload.get(key)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ValueError("Claude MCP proxy bootstrap is invalid")
    return tuple(value)


def _stdio_authorized_request(
    body: bytes,
    *,
    server_name: str,
    allowed_tools: frozenset[str],
    registry: McpToolEffectRegistry,
    grant_url: str,
    grant_token: str,
) -> bytes | None:
    method = _jsonrpc_method(body)
    if method in _CONTROL_METHODS:
        return body
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or method != "tools/call":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    wire_name = params.get("name")
    raw_arguments = params.get("arguments", {})
    if not isinstance(wire_name, str) or not isinstance(raw_arguments, dict):
        return None
    arguments = dict(raw_arguments)
    grant = arguments.pop("__ceo_runtime_grant", None)
    exact_name = f"mcp__{server_name}__{wire_name}"
    if (
        exact_name not in allowed_tools
        or not isinstance(grant, str)
        or not validate_claude_tool_input(exact_name, arguments)
        or registry.classify(
            {
                "type": "mcp_tool_call",
                "server": server_name,
                "tool": wire_name,
                "arguments": arguments,
            }
        )
        is None
        or not _consume_remote_grant(
            grant_url,
            grant_token,
            grant=grant,
            tool=exact_name,
            arguments=arguments,
        )
    ):
        return None
    params["arguments"] = arguments
    return json.dumps(payload, separators=(",", ":")).encode() + b"\n"


def _consume_remote_grant(
    url: str,
    token: str,
    *,
    grant: str,
    tool: str,
    arguments: Mapping[str, object],
) -> bool:
    from urllib.request import Request, urlopen

    payload = json.dumps(
        {"grant": grant, "tool": tool, "arguments": arguments},
        separators=(",", ":"),
    ).encode()
    request = Request(
        url,
        data=payload,
        headers={_AUTH_HEADER: token, "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read())
    except Exception:  # noqa: BLE001 - deny on any grant transport failure
        return False
    return result == {"allowed": True}


if __name__ == "__main__":
    raise SystemExit(main())
