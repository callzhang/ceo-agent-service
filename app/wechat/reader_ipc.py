"""Local, owner-only IPC between CEO Agent and the dedicated WeChat reader.

The main service uses :class:`WechatReaderClient` and never opens WeChat's
container, passphrase file, or decrypted mirror.  The helper process owns those
resources and exposes only the small allowlist implemented by
``WechatReaderRpcService``.
"""
from __future__ import annotations

import json
import errno
import os
import socket
import socketserver
import stat
import threading
from pathlib import Path
from typing import Any, Callable

from app.wechat.models import WechatAccount, WechatCapability, WechatMessage


PROTOCOL_VERSION = 1
DEFAULT_MAX_REQUEST_BYTES = 256 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ReaderIpcError(RuntimeError):
    """A rejected request, unavailable helper, or invalid helper response."""

    def __init__(self, message: str, *, code: str = "reader_error"):
        super().__init__(message)
        self.code = code


def _account(value: Any) -> WechatAccount:
    try:
        return WechatAccount.model_validate(value)
    except Exception as exc:
        raise ReaderIpcError("invalid account") from exc


def _bounded_int(params: dict, name: str, default: int, maximum: int) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ReaderIpcError(f"{name} must be between 1 and {maximum}")
    return value


def _bounded_text(params: dict, name: str, default: str = "", maximum: int = 512) -> str:
    value = params.get(name, default)
    if not isinstance(value, str) or len(value) > maximum:
        raise ReaderIpcError(f"invalid {name}")
    return value


class WechatReaderRpcService:
    """Strict RPC allowlist backed by the reader that runs inside the helper."""

    def __init__(self, reader, accounts_provider: Callable[[], list[WechatAccount]]):
        self.reader = reader
        self.accounts_provider = accounts_provider
        self._operation_lock = threading.Lock()

    def dispatch(self, method: str, params: dict) -> Any:
        if not isinstance(params, dict):
            raise ReaderIpcError("params must be an object")
        if method == "health":
            return {"status": "ready", "protocol_version": PROTOCOL_VERSION}
        with self._operation_lock:
            if method == "discover_accounts":
                return [item.model_dump(mode="json") for item in self.accounts_provider()]
            if method == "probe":
                return self.reader.probe(_account(params.get("account"))).model_dump(mode="json")
            if method == "detect_self_username":
                return self.reader.detect_self_username(_account(params.get("account")))
            if method == "list_targets":
                account = _account(params.get("account"))
                kind = _bounded_text(params, "kind", "direct", 16)
                if kind not in {"direct", "group"}:
                    raise ReaderIpcError("invalid kind")
                return self.reader.list_targets(
                    account,
                    kind=kind,
                    query=_bounded_text(params, "query", maximum=256),
                    limit=_bounded_int(params, "limit", 50, 200),
                    offset=max(0, int(params.get("offset", 0))),
                )
            if method == "read_messages":
                account = _account(params.get("account"))
                conversation_type = _bounded_text(params, "conversation_type", "direct", 16)
                if conversation_type not in {"direct", "group"}:
                    raise ReaderIpcError("invalid conversation_type")
                order = _bounded_text(params, "order", "newest", 16)
                if order not in {"newest", "oldest"}:
                    raise ReaderIpcError("invalid order")
                messages = self.reader.read_messages(
                    account,
                    conversation_id=_bounded_text(params, "conversation_id", maximum=512),
                    conversation_type=conversation_type,
                    since=_bounded_text(params, "since", maximum=64),
                    until=_bounded_text(params, "until", maximum=64),
                    limit=_bounded_int(params, "limit", 100, 500),
                    order=order,
                )
                return [message.model_dump(mode="json") for message in messages]
        raise ReaderIpcError(f"unsupported method: {method}")


class _ReaderRequestHandler(socketserver.StreamRequestHandler):
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
                raise ReaderIpcError("request must be an object")
            if request.get("protocol_version") != PROTOCOL_VERSION:
                raise ReaderIpcError("unsupported protocol version")
            result = server.rpc_service.dispatch(
                request.get("method", ""), request.get("params", {})
            )
            self._reply({"ok": True, "result": result})
        except ReaderIpcError as exc:
            self._reply_error("invalid_request", str(exc))
        except PermissionError:
            self._reply_error(
                "permission_required",
                "Grant App Data permission to CEO WeChat Reader, then retry Connect.",
            )
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EPERM}:
                self._reply_error(
                    "permission_required",
                    "Grant App Data permission to CEO WeChat Reader, then retry Connect.",
                )
            else:
                self._reply_error("internal_error", "reader operation failed")
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._reply_error("invalid_request", "invalid JSON request")
        except Exception:
            # Do not expose database paths, passphrases, or tracebacks over IPC.
            self._reply_error("internal_error", "reader operation failed")

    def _reply_error(self, code: str, message: str) -> None:
        self._reply({"ok": False, "error": {"code": code, "message": message}})

    def _reply(self, payload: dict) -> None:
        self.wfile.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")


class WechatReaderUnixServer(socketserver.ThreadingUnixStreamServer):
    """Unix socket server whose filesystem endpoint is readable only by its owner."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        socket_path: str | Path,
        rpc_service: WechatReaderRpcService,
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
                raise ReaderIpcError("refusing to replace non-socket IPC path")
            self.socket_path.unlink()
        super().__init__(str(self.socket_path), _ReaderRequestHandler)
        os.chmod(self.socket_path, 0o600)

    def server_close(self) -> None:
        super().server_close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


class WechatReaderClient:
    """Drop-in reader facade used by the main CEO Agent process."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout_seconds: float = 5.0,
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
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout_seconds)
                conn.connect(str(self.socket_path))
                conn.sendall(request)
                stream = conn.makefile("rb")
                raw = stream.readline(self.max_response_bytes + 1)
        except (OSError, TimeoutError) as exc:
            raise ReaderIpcError(
                f"WeChat reader unavailable: {exc}", code="unavailable",
            ) from exc
        if len(raw) > self.max_response_bytes or not raw.endswith(b"\n"):
            raise ReaderIpcError("invalid or oversized WeChat reader response")
        try:
            response = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ReaderIpcError("invalid WeChat reader response") from exc
        if not isinstance(response, dict) or not response.get("ok"):
            error = response.get("error", {}) if isinstance(response, dict) else {}
            message = error.get("message", "reader request failed")
            raise ReaderIpcError(
                str(message), code=str(error.get("code", "reader_error")),
            )
        return response.get("result")

    def health(self) -> dict:
        result = self._request("health")
        return result if isinstance(result, dict) else {}

    def discover_accounts(self) -> list[WechatAccount]:
        return [WechatAccount.model_validate(item) for item in self._request("discover_accounts")]

    def probe(self, account: WechatAccount) -> WechatCapability:
        return WechatCapability.model_validate(self._request(
            "probe", {"account": account.model_dump(mode="json")}
        ))

    def detect_self_username(self, account: WechatAccount) -> str:
        value = self._request(
            "detect_self_username", {"account": account.model_dump(mode="json")}
        )
        return value if isinstance(value, str) else ""

    def list_targets(
        self,
        account: WechatAccount,
        *,
        kind: str = "direct",
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        result = self._request("list_targets", {
            "account": account.model_dump(mode="json"),
            "kind": kind,
            "query": query,
            "limit": limit,
            "offset": offset,
        })
        if not isinstance(result, list):
            raise ReaderIpcError("invalid target response")
        return result

    def read_messages(
        self,
        account: WechatAccount,
        *,
        conversation_id: str = "",
        conversation_type: str = "direct",
        since: str = "",
        until: str = "",
        limit: int = 100,
        order: str = "newest",
    ) -> list[WechatMessage]:
        result = self._request("read_messages", {
            "account": account.model_dump(mode="json"),
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "since": since,
            "until": until,
            "limit": limit,
            "order": order,
        })
        return [WechatMessage.model_validate(item) for item in result]
