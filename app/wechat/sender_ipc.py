"""Owner-only IPC facade for the dedicated WeChat Accessibility sender."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
from typing import Any

from app.wechat.accessibility import AccessibilityResult


PROTOCOL_VERSION = 1
DEFAULT_MAX_REQUEST_BYTES = 32 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024


class SenderIpcError(RuntimeError):
    """A rejected request, unavailable helper, or invalid helper response."""


def _bounded_text(
    params: dict, name: str, *, maximum: int, required: bool = False,
) -> str:
    value = params.get(name, "")
    if not isinstance(value, str) or len(value) > maximum:
        raise SenderIpcError(f"invalid {name}")
    if required and not value.strip():
        raise SenderIpcError(f"{name} is required")
    return value


class WechatSenderRpcService:
    """Strict allowlist around the real Accessibility runner."""

    def __init__(self, runner):
        self.runner = runner

    def dispatch(self, method: str, params: dict) -> Any:
        if not isinstance(params, dict):
            raise SenderIpcError("params must be an object")
        if method == "health":
            return {"status": "ready", "protocol_version": PROTOCOL_VERSION}
        if method == "preflight":
            return self.runner.preflight()
        if method == "request_accessibility":
            return self.runner.request_accessibility()
        if method == "open_and_identify":
            return self.runner.open_and_identify(
                _bounded_text(params, "target_label", maximum=512, required=True),
                search_query=(
                    _bounded_text(params, "search_query", maximum=512) or None
                ),
            )
        if method == "send":
            result = self.runner.send(
                _bounded_text(params, "target_label", maximum=512, required=True),
                _bounded_text(params, "reply_text", maximum=10_000, required=True),
                search_query=(
                    _bounded_text(params, "search_query", maximum=512) or None
                ),
            )
            return asdict(result)
        if method == "recall_last_outbound":
            return bool(self.runner.recall_last_outbound(
                _bounded_text(params, "text", maximum=10_000, required=True),
            ))
        raise SenderIpcError(f"unsupported method: {method}")


class _SenderRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server
        try:
            getpeereid = getattr(self.request, "getpeereid", None)
            if getpeereid is not None and getpeereid()[0] != os.getuid():
                self._reply_error("forbidden", "peer uid is not allowed")
                return
            raw = self.rfile.readline(server.max_request_bytes + 1)
            if len(raw) > server.max_request_bytes or not raw.endswith(b"\n"):
                self._reply_error("request_too_large", "request exceeds size limit")
                return
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise SenderIpcError("request must be an object")
            if request.get("protocol_version") != PROTOCOL_VERSION:
                raise SenderIpcError("unsupported protocol version")
            result = server.rpc_service.dispatch(
                request.get("method", ""), request.get("params", {}),
            )
            self._reply({"ok": True, "result": result})
        except SenderIpcError as exc:
            self._reply_error("invalid_request", str(exc))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._reply_error("invalid_request", "invalid request")
        except Exception:
            self._reply_error("internal_error", "sender operation failed")

    def _reply_error(self, code: str, message: str) -> None:
        self._reply({"ok": False, "error": {"code": code, "message": message}})

    def _reply(self, payload: dict) -> None:
        self.wfile.write(
            json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        )


class WechatSenderUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        socket_path: str | Path,
        rpc_service: WechatSenderRpcService,
        *,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    ):
        self.socket_path = Path(socket_path).expanduser()
        self.rpc_service = rpc_service
        self.max_request_bytes = max_request_bytes
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            mode = self.socket_path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise SenderIpcError("refusing to replace non-socket IPC path")
            self.socket_path.unlink()
        super().__init__(str(self.socket_path), _SenderRequestHandler)
        os.chmod(self.socket_path, 0o600)

    def server_close(self) -> None:
        super().server_close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


class WechatSenderClient:
    """Drop-in Accessibility runner backed by the dedicated Sender app."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout_seconds: float = 130.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ):
        self.socket_path = Path(socket_path).expanduser()
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def _request(self, method: str, params: dict | None = None) -> Any:
        request = json.dumps({
            "protocol_version": PROTOCOL_VERSION,
            "method": method,
            "params": params or {},
        }, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(request)
                raw = connection.makefile("rb").readline(self.max_response_bytes + 1)
        except (OSError, TimeoutError) as exc:
            raise SenderIpcError(f"WeChat sender unavailable: {exc}") from exc
        if len(raw) > self.max_response_bytes or not raw.endswith(b"\n"):
            raise SenderIpcError("invalid or oversized WeChat sender response")
        try:
            response = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SenderIpcError("invalid WeChat sender response") from exc
        if not isinstance(response, dict) or not response.get("ok"):
            error = response.get("error", {}) if isinstance(response, dict) else {}
            raise SenderIpcError(str(error.get("message", "sender request failed")))
        return response.get("result")

    def health(self) -> dict:
        result = self._request("health")
        return result if isinstance(result, dict) else {}

    def preflight(self) -> str:
        result = self._request("preflight")
        return result if isinstance(result, str) else "unknown"

    def request_accessibility(self) -> str:
        result = self._request("request_accessibility")
        return result if isinstance(result, str) else "unknown"

    def open_and_identify(
        self, target_label: str, *, search_query: str | None = None,
    ) -> str:
        result = self._request("open_and_identify", {
            "target_label": target_label,
            "search_query": search_query or "",
        })
        return result if isinstance(result, str) else ""

    def send(
        self, target_label: str, reply_text: str, *, search_query: str | None = None,
    ) -> AccessibilityResult:
        return AccessibilityResult(**self._request("send", {
            "target_label": target_label,
            "reply_text": reply_text,
            "search_query": search_query or "",
        }))

    def recall_last_outbound(self, text: str) -> bool:
        return bool(self._request("recall_last_outbound", {"text": text}))
