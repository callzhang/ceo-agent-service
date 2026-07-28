from __future__ import annotations

import errno
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from app.dws_client import dws_noninteractive_environment

CliRunner = Callable[..., subprocess.CompletedProcess[str]]
LoginLauncher = Callable[[], "LoginProcess"]

AUTH_ERROR_TYPES = frozenset({"auth", "authentication", "token", "refresh"})
AUTH_ERROR_SUBTYPES = frozenset(
    {
        "not_authenticated",
        "not_logged_in",
        "token_expired",
        "invalid_token",
        "refresh_failed",
        "refresh_token_expired",
        "invalid_refresh_token",
    }
)
AUTH_ERROR_CODES = frozenset(
    {
        "invalidParameter.authCode.notFound",
        "not_authenticated",
        "AUTH_REQUIRED",
        "TOKEN_EXPIRED",
        "REFRESH_TOKEN_EXPIRED",
    }
)


class ChannelGateState(StrEnum):
    READY = "ready"
    NEEDS_LOGIN = "needs_login"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


class ChannelGateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: str
    state: ChannelGateState
    reason_code: str
    detail: str = ""
    commands: tuple[tuple[str, ...], ...] = ()


class ChannelGate(Protocol):
    channel_name: str

    def check(self) -> ChannelGateResult: ...


class ServiceStateStore(Protocol):
    def get_service_state(self, key: str) -> str | None: ...

    def set_service_state(self, key: str, value: str) -> None: ...

    def claim_channel_login_request(
        self,
        *,
        channel: str,
        reason_code: str,
        now: datetime,
        suppression_seconds: int,
        reservation_owner: str,
    ) -> tuple[bool, dict[str, object]]: ...

    def update_claimed_channel_login_request(
        self,
        *,
        channel: str,
        reservation_owner: str,
        state: dict[str, object],
    ) -> bool: ...


class LoginProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...


@dataclass(frozen=True)
class LoginHandlingResult:
    launched: bool = False
    suppressed: bool = False
    pid: int | None = None


class LoginCoordinator:
    SUPPRESSION = timedelta(hours=1)
    _SAFE_FIELDS = (
        "status",
        "reason_code",
        "started_at",
        "checked_at",
        "exited_at",
        "pid",
    )

    def __init__(
        self,
        *,
        store: ServiceStateStore,
        launchers: dict[str, LoginLauncher],
        now: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.launchers = launchers
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._processes: dict[str, LoginProcess] = {}

    def handle(self, result: ChannelGateResult) -> LoginHandlingResult:
        now = self._utc_now()
        state = self._state(result.channel)
        process = self._processes.get(result.channel)
        pid = state.get("pid") if isinstance(state.get("pid"), int) else None

        if process is not None and process.poll() is not None:
            self._processes.pop(result.channel, None)
            state = {
                **state,
                "status": "exited",
                "exited_at": now.isoformat(),
            }
        elif process is not None:
            pid = process.pid

        if result.state is ChannelGateState.READY:
            self._write_state(
                result.channel,
                {
                    **state,
                    "status": "healthy",
                    "reason_code": result.reason_code,
                    "checked_at": now.isoformat(),
                },
            )
            return LoginHandlingResult()

        state = {
            **state,
            "reason_code": result.reason_code,
            "checked_at": now.isoformat(),
        }
        if result.state is not ChannelGateState.NEEDS_LOGIN:
            state["status"] = result.state.value
            self._write_state(result.channel, state)
            return LoginHandlingResult()

        if process is not None and process.poll() is None:
            self._write_state(
                result.channel, {**state, "status": "running", "pid": pid}
            )
            return LoginHandlingResult(suppressed=True, pid=pid)
        if self._within_suppression(state, now):
            self._write_state(result.channel, state)
            return LoginHandlingResult(suppressed=True, pid=pid)

        reservation_owner = uuid4().hex
        claimed, state = self.store.claim_channel_login_request(
            channel=result.channel,
            reason_code=result.reason_code,
            now=now,
            suppression_seconds=int(self.SUPPRESSION.total_seconds()),
            reservation_owner=reservation_owner,
        )
        if not claimed:
            reserved_pid = state.get("pid")
            return LoginHandlingResult(
                suppressed=True,
                pid=reserved_pid if isinstance(reserved_pid, int) else None,
            )

        launcher = self.launchers.get(result.channel)
        if launcher is None:
            self.store.update_claimed_channel_login_request(
                channel=result.channel,
                reservation_owner=reservation_owner,
                state={"status": "unavailable"},
            )
            return LoginHandlingResult()
        try:
            process = launcher()
        except Exception:
            self.store.update_claimed_channel_login_request(
                channel=result.channel,
                reservation_owner=reservation_owner,
                state={
                    "status": "failed",
                    "exited_at": now.isoformat(),
                },
            )
            return LoginHandlingResult()
        self._processes[result.channel] = process
        self.store.update_claimed_channel_login_request(
            channel=result.channel,
            reservation_owner=reservation_owner,
            state={"status": "running", "pid": process.pid},
        )
        return LoginHandlingResult(launched=True, pid=process.pid)

    def _state(self, channel: str) -> dict[str, object]:
        raw = self.store.get_service_state(self._key(channel))
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, channel: str, state: dict[str, object]) -> None:
        safe = {field: state[field] for field in self._SAFE_FIELDS if field in state}
        self.store.set_service_state(
            self._key(channel),
            json.dumps(safe, ensure_ascii=False, sort_keys=True),
        )

    def _within_suppression(self, state: dict[str, object], now: datetime) -> bool:
        started_at = state.get("started_at")
        if not isinstance(started_at, str):
            return False
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            return False
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = now - started.astimezone(timezone.utc)
        return timedelta(0) <= age < self.SUPPRESSION

    def _utc_now(self) -> datetime:
        now = self.now()
        if now.tzinfo is None:
            now = now.astimezone()
        return now.astimezone(timezone.utc)

    @staticmethod
    def _key(channel: str) -> str:
        return f"channel_login_request:{channel}"


class DwsChannelGate:
    channel_name = "dingtalk"

    def __init__(self, *, binary: str = "dws", runner: CliRunner = subprocess.run):
        self.binary = binary
        self.runner = runner

    def check(self) -> ChannelGateResult:
        status_command = [
            self.binary,
            "auth",
            "status",
            "--format",
            "json",
            "--timeout",
            "5",
        ]
        probe_command = [
            self.binary,
            "contact",
            "user",
            "get-self",
            "--format",
            "json",
        ]
        commands: list[list[str]] = []
        if not self.binary.strip():
            return _result(
                self.channel_name,
                ChannelGateState.BLOCKED,
                "configuration_missing",
                commands,
                detail="DWS binary is not configured",
            )
        env = dws_noninteractive_environment()
        status = _run_command(
            channel=self.channel_name,
            phase="status",
            command=status_command,
            commands=commands,
            runner=self.runner,
            env=env,
        )
        if isinstance(status, ChannelGateResult):
            return status
        status_payloads = _json_objects(status.stdout, status.stderr)
        classified = _classify_structured_failure(
            channel=self.channel_name,
            phase="status",
            completed=status,
            payloads=status_payloads,
            commands=commands,
        )
        if classified is not None:
            return classified
        if status.returncode != 0:
            return _generic_failure(
                channel=self.channel_name,
                phase="status",
                completed=status,
                commands=commands,
            )
        if not status_payloads:
            return _invalid_json(self.channel_name, "status", status, commands)
        status_ready = any(_dws_status_ready(payload) for payload in status_payloads)
        if not status_ready and any(
            _dws_status_auth_invalid(payload) for payload in status_payloads
        ):
            return _result(
                self.channel_name,
                ChannelGateState.NEEDS_LOGIN,
                "status_auth_invalid",
                commands,
                detail=_safe_detail(status.stdout, status.stderr),
            )
        if not status_ready:
            return _result(
                self.channel_name,
                ChannelGateState.UNAVAILABLE,
                "status_unrecognized",
                commands,
                detail=_safe_detail(status.stdout, status.stderr),
            )

        probe = _run_command(
            channel=self.channel_name,
            phase="live_probe",
            command=probe_command,
            commands=commands,
            runner=self.runner,
            env=env,
        )
        if isinstance(probe, ChannelGateResult):
            return probe
        probe_payloads = _json_objects(probe.stdout, probe.stderr)
        classified = _classify_structured_failure(
            channel=self.channel_name,
            phase="live_probe",
            completed=probe,
            payloads=probe_payloads,
            commands=commands,
        )
        if classified is not None:
            return classified
        if probe.returncode != 0:
            return _generic_failure(
                channel=self.channel_name,
                phase="live_probe",
                completed=probe,
                commands=commands,
            )
        if not probe_payloads:
            return _invalid_json(self.channel_name, "live_probe", probe, commands)
        return _result(self.channel_name, ChannelGateState.READY, "ready", commands)


class LarkChannelGate:
    channel_name = "lark"

    def __init__(
        self,
        *,
        binary: str = "lark-cli",
        runner: CliRunner = subprocess.run,
    ):
        self.binary = binary
        self.runner = runner

    def check(self) -> ChannelGateResult:
        status_command = [self.binary, "auth", "status", "--json", "--verify"]
        probe_command = [
            self.binary,
            "contact",
            "+get-user",
            "--as",
            "user",
            "--json",
        ]
        commands: list[list[str]] = []
        if not self.binary.strip():
            return _result(
                self.channel_name,
                ChannelGateState.BLOCKED,
                "configuration_missing",
                commands,
                detail="Lark CLI binary is not configured",
            )
        env = _lark_noninteractive_environment()
        status = _run_command(
            channel=self.channel_name,
            phase="status",
            command=status_command,
            commands=commands,
            runner=self.runner,
            env=env,
        )
        if isinstance(status, ChannelGateResult):
            return status
        status_payloads = _json_objects(status.stdout, status.stderr)
        classified = _classify_structured_failure(
            channel=self.channel_name,
            phase="status",
            completed=status,
            payloads=status_payloads,
            commands=commands,
        )
        if classified is not None:
            return classified
        if status.returncode != 0 or _declares_failure(status_payloads):
            return _generic_failure(
                channel=self.channel_name,
                phase="status",
                completed=status,
                commands=commands,
            )
        if not status_payloads:
            return _invalid_json(self.channel_name, "status", status, commands)
        if any(_declares_invalid_auth(payload) for payload in status_payloads):
            return _result(
                self.channel_name,
                ChannelGateState.NEEDS_LOGIN,
                "status_auth_invalid",
                commands,
                detail=_safe_detail(status.stdout, status.stderr),
            )
        if not any(_lark_status_ready(payload) for payload in status_payloads):
            return _result(
                self.channel_name,
                ChannelGateState.UNAVAILABLE,
                "status_unrecognized",
                commands,
                detail=_safe_detail(status.stdout, status.stderr),
            )

        probe = _run_command(
            channel=self.channel_name,
            phase="live_probe",
            command=probe_command,
            commands=commands,
            runner=self.runner,
            env=env,
        )
        if isinstance(probe, ChannelGateResult):
            return probe
        probe_payloads = _json_objects(probe.stdout, probe.stderr)
        classified = _classify_structured_failure(
            channel=self.channel_name,
            phase="live_probe",
            completed=probe,
            payloads=probe_payloads,
            commands=commands,
        )
        if classified is not None:
            return classified
        if probe.returncode != 0 or _declares_failure(probe_payloads):
            return _generic_failure(
                channel=self.channel_name,
                phase="live_probe",
                completed=probe,
                commands=commands,
            )
        if not probe_payloads:
            return _invalid_json(self.channel_name, "live_probe", probe, commands)
        if not any(_lark_probe_ready(payload) for payload in probe_payloads):
            return _result(
                self.channel_name,
                ChannelGateState.UNAVAILABLE,
                "live_probe_unrecognized",
                commands,
                detail=_safe_detail(probe.stdout, probe.stderr),
            )
        return _result(self.channel_name, ChannelGateState.READY, "ready", commands)


def default_channel_gates(
    *, dws_binary: str = "dws", lark_binary: str = "lark-cli"
) -> dict[str, ChannelGate]:
    gates: tuple[ChannelGate, ...] = (
        DwsChannelGate(binary=dws_binary),
        LarkChannelGate(binary=lark_binary),
    )
    return {gate.channel_name: gate for gate in gates}


def start_lark_auth_login(binary: str = "lark-cli") -> subprocess.Popen[str]:
    return subprocess.Popen(
        [binary, "auth", "login"],
        text=True,
        start_new_session=True,
        env=os.environ.copy(),
    )


def _run_command(
    *,
    channel: str,
    phase: str,
    command: list[str],
    commands: list[list[str]],
    runner: CliRunner,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str] | ChannelGateResult:
    commands.append(command)
    try:
        return runner(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=env,
        )
    except FileNotFoundError:
        return _result(
            channel,
            ChannelGateState.BLOCKED,
            "executable_missing",
            commands,
            detail="executable not found",
        )
    except PermissionError:
        return _result(
            channel,
            ChannelGateState.BLOCKED,
            "executable_unusable",
            commands,
            detail="executable could not be started",
        )
    except subprocess.TimeoutExpired:
        return _result(
            channel,
            ChannelGateState.UNAVAILABLE,
            f"{phase}_timeout",
            commands,
        )
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ENOTDIR}:
            return _result(
                channel,
                ChannelGateState.BLOCKED,
                "executable_missing",
                commands,
                detail="executable not found",
            )
        if exc.errno in {errno.EACCES, errno.EPERM, errno.ENOEXEC}:
            return _result(
                channel,
                ChannelGateState.BLOCKED,
                "executable_unusable",
                commands,
                detail="executable could not be started",
            )
        return _result(
            channel,
            ChannelGateState.UNAVAILABLE,
            f"{phase}_command_unavailable",
            commands,
            detail="operating system error",
        )


def _classify_structured_failure(
    *,
    channel: str,
    phase: str,
    completed: subprocess.CompletedProcess[str],
    payloads: tuple[dict[str, object], ...],
    commands: list[list[str]],
) -> ChannelGateResult | None:
    errors = tuple(_structured_error(payload) for payload in payloads)
    detail = _safe_detail(completed.stdout, completed.stderr)
    if any(
        error_type == "config"
        or error_subtype == "not_configured"
        or error_code == "AGENT_CODE_NOT_EXISTS"
        for error_type, error_subtype, error_code in errors
    ):
        return _result(
            channel,
            ChannelGateState.BLOCKED,
            f"{phase}_configuration_missing",
            commands,
            detail=detail,
        )
    if any(
        error_type in {"permission", "authorization"} for error_type, _, _ in errors
    ):
        return _result(
            channel,
            ChannelGateState.BLOCKED,
            f"{phase}_authorization_missing",
            commands,
            detail=detail,
        )
    unavailable_type = next(
        (
            error_type
            for error_type, _, _ in errors
            if error_type in {"network", "provider", "timeout", "unavailable"}
        ),
        "",
    )
    if unavailable_type:
        reason_code = (
            f"{phase}_unavailable"
            if unavailable_type == "unavailable"
            else f"{phase}_{unavailable_type}_unavailable"
        )
        return _result(
            channel,
            ChannelGateState.UNAVAILABLE,
            reason_code,
            commands,
            detail=detail,
        )
    if any(
        error_type in AUTH_ERROR_TYPES
        or error_subtype in AUTH_ERROR_SUBTYPES
        or error_code in AUTH_ERROR_CODES
        for error_type, error_subtype, error_code in errors
    ):
        return _result(
            channel,
            ChannelGateState.NEEDS_LOGIN,
            f"{phase}_auth_failed",
            commands,
            detail=detail,
        )
    if any(_has_structured_error(payload) for payload in payloads):
        return _result(
            channel,
            ChannelGateState.UNAVAILABLE,
            f"{phase}_failed",
            commands,
            detail=detail,
        )
    return None


def _generic_failure(
    *,
    channel: str,
    phase: str,
    completed: subprocess.CompletedProcess[str],
    commands: list[list[str]],
) -> ChannelGateResult:
    return _result(
        channel,
        ChannelGateState.UNAVAILABLE,
        f"{phase}_failed",
        commands,
        detail=_safe_detail(completed.stdout, completed.stderr),
    )


def _invalid_json(
    channel: str,
    phase: str,
    completed: subprocess.CompletedProcess[str],
    commands: list[list[str]],
) -> ChannelGateResult:
    return _result(
        channel,
        ChannelGateState.UNAVAILABLE,
        f"{phase}_invalid_json",
        commands,
        detail=_safe_detail(completed.stdout, completed.stderr),
    )


def _result(
    channel: str,
    state: ChannelGateState,
    reason_code: str,
    commands: list[list[str]],
    *,
    detail: str = "",
) -> ChannelGateResult:
    return ChannelGateResult(
        channel=channel,
        state=state,
        reason_code=reason_code,
        detail=detail,
        commands=tuple(tuple(command) for command in commands),
    )


def _json_object(raw: str | None) -> dict[str, object] | None:
    try:
        payload = json.loads((raw or "").strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _json_objects(*values: str | None) -> tuple[dict[str, object], ...]:
    payloads: list[dict[str, object]] = []
    for value in values:
        payload = _json_object(value)
        if payload is not None:
            payloads.append(payload)
    return tuple(payloads)


def _error_object(payload: dict[str, object]) -> dict[str, object]:
    error = payload.get("error")
    return error if isinstance(error, dict) else payload


def _string_value(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _structured_error(payload: dict[str, object]) -> tuple[str, str, str]:
    error = _error_object(payload)
    return (
        _string_value(error, "type").casefold(),
        _string_value(error, "subtype").casefold(),
        _string_value(error, "code") or _string_value(payload, "code"),
    )


def _has_structured_error(payload: dict[str, object]) -> bool:
    error = payload.get("error")
    if isinstance(error, dict):
        return bool(error)
    return any(_string_value(payload, field) for field in ("type", "subtype", "code"))


def _declares_failure(payloads: tuple[dict[str, object], ...]) -> bool:
    return any(payload.get("ok") is False for payload in payloads)


def _dws_status_ready(payload: dict[str, object]) -> bool:
    return all(
        payload.get(field) is True
        for field in ("authenticated", "token_valid", "refresh_token_valid")
    )


def _dws_status_auth_invalid(payload: dict[str, object]) -> bool:
    return any(
        payload.get(field) is False
        for field in ("authenticated", "token_valid", "refresh_token_valid")
    )


def _lark_status_ready(payload: dict[str, object]) -> bool:
    if payload.get("authenticated") is True:
        return True
    identities = payload.get("identities")
    user = identities.get("user") if isinstance(identities, dict) else None
    return bool(
        payload.get("verified") is True
        and payload.get("identity") == "user"
        and isinstance(user, dict)
        and user.get("available") is True
        and user.get("verified") is True
        and user.get("status") == "ready"
        and user.get("tokenStatus") == "valid"
    )


def _lark_probe_ready(payload: dict[str, object]) -> bool:
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    if _nonempty_string(data.get("user_id")):
        return True
    user = data.get("user")
    return isinstance(user, dict) and any(
        _nonempty_string(user.get(field))
        for field in ("user_id", "open_id", "union_id")
    )


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _declares_invalid_auth(payload: dict[str, object]) -> bool:
    return any(
        field in payload and payload.get(field) is not True
        for field in ("authenticated", "verified")
    )


def _safe_detail(*values: str | None) -> str:
    nonempty = tuple((value or "").strip() for value in values if (value or "").strip())
    if not nonempty:
        return ""
    safe: dict[str, object] = {}
    payloads = _json_objects(*nonempty)
    for payload in payloads:
        error = payload.get("error")
        source = error if isinstance(error, dict) else payload
        prefix = "error." if isinstance(error, dict) else ""
        for key in ("type", "subtype", "code", "status"):
            value = source.get(key)
            if isinstance(value, (str, int, bool)):
                safe[f"{prefix}{key}"] = value
    if safe:
        return json.dumps(safe, ensure_ascii=False)
    return "<structured error>" if payloads else "<unstructured error>"


def _lark_noninteractive_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.setdefault("NO_COLOR", "1")
    return env
