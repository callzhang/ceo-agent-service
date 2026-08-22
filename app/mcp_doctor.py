import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import httpx

from app.notification import send_macos_notification
from app.service_codex_config import (
    ServiceMcpConfigError,
    ServiceMcpConfigIssue,
    ServiceMcpServer,
    jwt_token_is_expired,
    load_service_mcp_servers,
)
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
    service_config_path: Path | None = None,
    env: Mapping[str, str] = os.environ,
    verify_live: bool = False,
    memory_reachability_checker: Callable[[str], None] | None = None,
) -> list[McpStatus]:
    issues: tuple[ServiceMcpConfigIssue, ...] = ()
    try:
        servers = load_service_mcp_servers(service_config_path, env=env)
    except ServiceMcpConfigError as exc:
        servers = ()
        issues = exc.issues
        blocked_servers = [
            McpStatus(
                name=server.name,
                state="missing_config",
                ready=False,
                reason="service MCP manifest contains invalid entries",
                recover_command="fix the service MCP manifest",
            )
            for server in exc.valid_servers
        ]
    else:
        blocked_servers = []

    statuses = blocked_servers + [
        _service_server_status(
            server,
            env=env,
            verify_live=verify_live,
            memory_reachability_checker=memory_reachability_checker,
        )
        for server in servers
    ]
    statuses.extend(_configuration_issue_status(issue) for issue in issues)
    return statuses


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
    service_config_path: Path | None = None,
    env: Mapping[str, str] = os.environ,
    verify_live: bool = False,
    notify: bool = False,
) -> dict[str, object]:
    statuses = check_mcp_statuses(
        service_config_path=service_config_path,
        env=env,
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


def _service_server_status(
    server: ServiceMcpServer,
    *,
    env: Mapping[str, str],
    verify_live: bool,
    memory_reachability_checker: Callable[[str], None] | None,
) -> McpStatus:
    bearer_token = (
        env.get(server.bearer_token_env_var, "")
        if server.bearer_token_env_var
        else ""
    )
    if bearer_token and jwt_token_is_expired(bearer_token):
        return McpStatus(
            name=server.name,
            state="token_expired",
            ready=False,
            reason="configured bearer token is expired",
            authorization_required=True,
            recover_command=f"configure {server.bearer_token_env_var}",
        )
    if verify_live and server.url is not None:
        try:
            if server.name == "memory_connector" and memory_reachability_checker is not None:
                memory_reachability_checker(server.url)
            else:
                _check_mcp_reachable(server.url, bearer_token=bearer_token)
        except Exception as exc:
            state, reason = _live_probe_failure(exc)
            return McpStatus(
                name=server.name,
                state=state,
                ready=False,
                reason=reason,
                authorization_required=state in AUTHORIZATION_STATES,
                recover_command="ceo-agent doctor-mcp --verify-live",
            )

    return McpStatus(
        name=server.name,
        state="ready",
        ready=True,
        reason="configured by service MCP manifest",
    )


def _configuration_issue_status(issue: ServiceMcpConfigIssue) -> McpStatus:
    reason = issue.reason
    if issue.server_name == "xiaoqing_interview" and issue.field == "command":
        reason = "service transport command is not configured"
    return McpStatus(
        name=issue.server_name,
        state="missing_config",
        ready=False,
        reason=reason,
        recover_command=(
            "configure CEO_SERVICE_MCP_CONFIG_PATH"
            if issue.server_name == "service_mcp_config"
            else f"fix {issue.server_name} in the service MCP manifest"
        ),
    )


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


def _live_probe_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return "needs_login", "HTTP authorization failed"
        if status_code >= 500:
            return "network_blocked", "HTTP service is unavailable"
        return "tool_not_found", "HTTP probe was rejected"
    state = _network_or_tool_state(type(exc).__name__ + " " + str(exc))
    reasons = {
        "network_blocked": "service endpoint is unreachable",
        "needs_login": "service authorization is required",
        "tool_not_found": "service probe failed",
    }
    return state, reasons[state]


def _check_mcp_reachable(url: str, *, bearer_token: str = "") -> None:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    payload = {
        "jsonrpc": "2.0",
        "id": "mcp-doctor-probe",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "ceo-agent-mcp-doctor", "version": "1"},
        },
    }
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()


def _error_detail(status: McpStatus) -> str:
    payload = status.as_dict()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _notification_message(status: McpStatus) -> str:
    command = f" Run: {status.recover_command}." if status.recover_command else ""
    return (
        f"{status.name} is {status.state}: {status.reason}. "
        f"The service gate reports this dependency unavailable.{command}"
    )[:240]
