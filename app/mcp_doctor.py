import asyncio
import json
import os
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import httpx

from app.codex_runner import (
    CODEX_PASSTHROUGH_MCP_SERVERS_ENV,
    DEFAULT_CODEX_PASSTHROUGH_MCP_SERVERS,
    DEFAULT_EXA_MCP_URL,
)
from app.memory_connector_auth import (
    MemoryConnectorAuthError,
    MemoryConnectorAuthManager,
)
from app.notification import send_macos_notification
from app.store import AutoReplyStore

MCP_DOCTOR_STATE_FILENAME = "mcp-doctor-state.json"
MCP_DOCTOR_ERROR_KIND = "mcp_doctor"
AUTHORIZATION_STATES = {"needs_login", "token_expired"}


@dataclass(frozen=True)
class McpStatus:
    name: str
    state: str
    ready: bool
    reason: str
    authorization_required: bool = False
    recover_command: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class McpDoctorState:
    def __init__(self, path: Path) -> None:
        self.path = path

    def should_notify(self, status: McpStatus) -> bool:
        if status.ready or status.state not in AUTHORIZATION_STATES:
            return False
        payload = self._read()
        return self._notification_key(status) not in payload.get("notifications", {})

    def mark_notified(self, status: McpStatus, *, now: datetime | None = None) -> None:
        if status.ready:
            return
        timestamp = (now or datetime.now(timezone.utc)).isoformat()
        payload = self._read()
        notifications = payload.setdefault("notifications", {})
        notifications[self._notification_key(status)] = {
            "server": status.name,
            "state": status.state,
            "reason": status.reason,
            "notified_at": timestamp,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {"notifications": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"notifications": {}}
        return payload if isinstance(payload, dict) else {"notifications": {}}

    @staticmethod
    def _notification_key(status: McpStatus) -> str:
        return f"{status.name}:{status.state}:{status.reason}"


def mcp_doctor_state_path(db_path: Path) -> Path:
    return db_path.expanduser().parent / MCP_DOCTOR_STATE_FILENAME


def check_mcp_statuses(
    *,
    codex_config_path: Path | None = None,
    verify_live: bool = False,
    memory_auth_factory: Callable[[], MemoryConnectorAuthManager] | None = None,
    memory_reachability_checker: Callable[[str], None] | None = None,
) -> list[McpStatus]:
    config_path = codex_config_path or _codex_config_path()
    config = _read_toml(config_path)
    passthrough_names = _passthrough_mcp_server_names()
    servers = config.get("mcp_servers") if isinstance(config, dict) else {}
    if not isinstance(servers, dict):
        servers = {}
    return [
        _memory_connector_status(
            config_path=config_path,
            verify_live=verify_live,
            memory_auth_factory=memory_auth_factory,
            memory_reachability_checker=memory_reachability_checker,
        ),
        _passthrough_server_status(
            "exa",
            servers=servers,
            passthrough_names=passthrough_names,
            default_server={"url": DEFAULT_EXA_MCP_URL},
        ),
        _passthrough_server_status(
            "xiaoqing_interview",
            servers=servers,
            passthrough_names=passthrough_names,
        ),
    ]


def record_and_notify_mcp_doctor(
    *,
    db_path: Path,
    statuses: Iterable[McpStatus],
    notify: bool = True,
    store_factory: Callable[[Path], AutoReplyStore] = AutoReplyStore,
    notification_sender: Callable[[str, str], None] | None = None,
) -> None:
    state = McpDoctorState(mcp_doctor_state_path(db_path))
    store = store_factory(db_path)
    sender = notification_sender or (
        lambda title, message: send_macos_notification(title=title, message=message)
    )
    for status in statuses:
        if status.ready:
            continue
        if state.should_notify(status):
            store.record_error(
                None,
                None,
                MCP_DOCTOR_ERROR_KIND,
                _error_detail(status),
            )
            if notify:
                sender(
                    f"CEO MCP needs authorization: {status.name}",
                    _notification_message(status),
                )
            state.mark_notified(status)


def mcp_doctor_report(
    *,
    db_path: Path,
    codex_config_path: Path | None = None,
    verify_live: bool = False,
    notify: bool = False,
) -> dict[str, object]:
    statuses = check_mcp_statuses(
        codex_config_path=codex_config_path,
        verify_live=verify_live,
    )
    if notify:
        record_and_notify_mcp_doctor(
            db_path=db_path,
            statuses=statuses,
            notify=True,
        )
    return {
        "ok": all(status.ready for status in statuses),
        "statuses": [status.as_dict() for status in statuses],
    }


def _memory_connector_status(
    *,
    config_path: Path,
    verify_live: bool,
    memory_auth_factory: Callable[[], MemoryConnectorAuthManager] | None,
    memory_reachability_checker: Callable[[str], None] | None,
) -> McpStatus:
    try:
        manager = (
            memory_auth_factory()
            if memory_auth_factory is not None
            else MemoryConnectorAuthManager(config_path=config_path)
        )
    except MemoryConnectorAuthError as exc:
        return McpStatus(
            name="memory_connector",
            state="missing_config",
            ready=False,
            reason=str(exc),
            recover_command="ceo-agent setup-memory-connector --memory-url <memory-mcp-url>",
        )

    try:
        auth_status = asyncio.run(manager.status())
    except MemoryConnectorAuthError as exc:
        return McpStatus(
            name="memory_connector",
            state="missing_config",
            ready=False,
            reason=str(exc),
            recover_command="ceo-agent setup-memory-connector --memory-url <memory-mcp-url>",
        )

    if not auth_status.ready:
        state = "token_expired" if "expired" in auth_status.reason else "needs_login"
        return McpStatus(
            name="memory_connector",
            state=state,
            ready=False,
            reason=auth_status.reason,
            authorization_required=True,
            recover_command="ceo-agent login-memory-connector",
        )

    if verify_live:
        try:
            if memory_reachability_checker is not None:
                memory_reachability_checker(manager.url)
            else:
                _check_http_reachable(manager.url)
        except Exception as exc:
            return McpStatus(
                name="memory_connector",
                state=_network_or_tool_state(str(exc)),
                ready=False,
                reason=str(exc),
                recover_command="ceo-agent doctor-mcp --verify-live",
            )

    return McpStatus(
        name="memory_connector",
        state="ready",
        ready=True,
        reason="ready",
    )


def _passthrough_server_status(
    name: str,
    *,
    servers: dict[str, object],
    passthrough_names: tuple[str, ...],
    default_server: dict[str, object] | None = None,
) -> McpStatus:
    if name not in passthrough_names:
        return McpStatus(
            name=name,
            state="tool_not_found",
            ready=False,
            reason=f"{name} is not enabled in {CODEX_PASSTHROUGH_MCP_SERVERS_ENV}",
        )
    server = servers.get(name)
    if not isinstance(server, dict):
        server = default_server
    if not isinstance(server, dict):
        return McpStatus(
            name=name,
            state="missing_config",
            ready=False,
            reason=f"[mcp_servers.{name}] is missing from Codex config",
        )
    if _server_has_launch_target(server):
        return McpStatus(
            name=name,
            state="ready",
            ready=True,
            reason="configured",
        )
    return McpStatus(
        name=name,
        state="missing_config",
        ready=False,
        reason=f"[mcp_servers.{name}] has no url or command",
    )


def _server_has_launch_target(server: dict[str, object]) -> bool:
    for key in ("url", "command"):
        value = server.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _passthrough_mcp_server_names() -> tuple[str, ...]:
    raw = os.environ.get(CODEX_PASSTHROUGH_MCP_SERVERS_ENV, "").strip()
    if not raw:
        return DEFAULT_CODEX_PASSTHROUGH_MCP_SERVERS
    names = tuple(
        name.strip()
        for name in raw.replace(";", ",").split(",")
        if name.strip()
    )
    return names or DEFAULT_CODEX_PASSTHROUGH_MCP_SERVERS


def _codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"


def _read_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _network_or_tool_state(message: str) -> str:
    lowered = message.casefold()
    if any(
        marker in lowered
        for marker in (
            "network",
            "connection",
            "timeout",
            "temporary failure",
            "failed to resolve",
            "nodename nor servname",
        )
    ):
        return "network_blocked"
    if "authorization" in lowered or "unauthorized" in lowered:
        return "needs_login"
    return "tool_not_found"


def _check_http_reachable(url: str) -> None:
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        client.get(url)


def _error_detail(status: McpStatus) -> str:
    payload = status.as_dict()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _notification_message(status: McpStatus) -> str:
    command = f" Run: {status.recover_command}." if status.recover_command else ""
    return (
        f"{status.name} is {status.state}: {status.reason}. "
        f"Related tasks are blocked until this is fixed.{command}"
    )[:240]
