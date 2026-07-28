from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.dws_client import dws_noninteractive_environment

CliRunner = Callable[..., subprocess.CompletedProcess[str]]


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
        status_payload = _first_json_object(status.stdout, status.stderr)
        if status.returncode != 0:
            return _classify_failure(
                channel=self.channel_name,
                phase="status",
                completed=status,
                payload=status_payload,
                commands=commands,
                auth_returncodes=(4,),
            )
        if status_payload is None:
            return _invalid_json(self.channel_name, "status", status, commands)
        if not all(
            status_payload.get(field) is True
            for field in ("authenticated", "token_valid", "refresh_token_valid")
        ):
            return _result(
                self.channel_name,
                ChannelGateState.NEEDS_LOGIN,
                "status_auth_invalid",
                commands,
                detail=_safe_stderr(status.stderr),
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
        probe_payload = _first_json_object(probe.stdout, probe.stderr)
        if probe.returncode != 0:
            return _classify_failure(
                channel=self.channel_name,
                phase="live_probe",
                completed=probe,
                payload=probe_payload,
                commands=commands,
                auth_returncodes=(4,),
            )
        if probe_payload is None:
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
        status_payload = _first_json_object(status.stdout, status.stderr)
        if status.returncode != 0 or _declares_failure(status_payload):
            return _classify_failure(
                channel=self.channel_name,
                phase="status",
                completed=status,
                payload=status_payload,
                commands=commands,
                auth_returncodes=(4,),
            )
        if status_payload is None:
            return _invalid_json(self.channel_name, "status", status, commands)
        if _declares_invalid_auth(status_payload):
            return _result(
                self.channel_name,
                ChannelGateState.NEEDS_LOGIN,
                "status_auth_invalid",
                commands,
                detail=_safe_stderr(status.stderr),
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
        probe_payload = _first_json_object(probe.stdout, probe.stderr)
        if probe.returncode != 0 or _declares_failure(probe_payload):
            return _classify_failure(
                channel=self.channel_name,
                phase="live_probe",
                completed=probe,
                payload=probe_payload,
                commands=commands,
                auth_returncodes=(4,),
            )
        if probe_payload is None:
            return _invalid_json(self.channel_name, "live_probe", probe, commands)
        return _result(self.channel_name, ChannelGateState.READY, "ready", commands)


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
            detail=f"{command[0]} command not found",
        )
    except PermissionError as exc:
        return _result(
            channel,
            ChannelGateState.BLOCKED,
            "executable_unusable",
            commands,
            detail=str(exc),
        )
    except subprocess.TimeoutExpired:
        return _result(
            channel,
            ChannelGateState.UNAVAILABLE,
            f"{phase}_timeout",
            commands,
        )
    except OSError as exc:
        return _result(
            channel,
            ChannelGateState.UNAVAILABLE,
            f"{phase}_command_unavailable",
            commands,
            detail=str(exc),
        )


def _classify_failure(
    *,
    channel: str,
    phase: str,
    completed: subprocess.CompletedProcess[str],
    payload: dict[str, object] | None,
    commands: list[list[str]],
    auth_returncodes: tuple[int, ...],
) -> ChannelGateResult:
    detail = _safe_stderr(completed.stderr)
    error = _error_object(payload)
    error_type = _string_value(error, "type")
    error_subtype = _string_value(error, "subtype")
    error_code = _string_value(error, "code") or _string_value(payload, "code")
    if error_type == "config" or error_subtype == "not_configured":
        return _result(
            channel,
            ChannelGateState.BLOCKED,
            f"{phase}_configuration_missing",
            commands,
            detail=detail,
        )
    if error_type in {"permission", "authorization"}:
        return _result(
            channel,
            ChannelGateState.BLOCKED,
            f"{phase}_authorization_missing",
            commands,
            detail=detail,
        )
    if (
        error_type in {"auth", "authentication", "token", "refresh"}
        or error_subtype
        in {
            "not_authenticated",
            "token_expired",
            "refresh_failed",
            "refresh_token_expired",
        }
        or error_code in {"invalidParameter.authCode.notFound", "not_authenticated"}
        or (completed.returncode in auth_returncodes and bool(error_code))
    ):
        return _result(
            channel,
            ChannelGateState.NEEDS_LOGIN,
            f"{phase}_auth_failed",
            commands,
            detail=detail,
        )
    if error_type in {"network", "provider", "timeout"}:
        return _result(
            channel,
            ChannelGateState.UNAVAILABLE,
            f"{phase}_{error_type}_unavailable",
            commands,
            detail=detail,
        )
    return _result(
        channel,
        ChannelGateState.UNAVAILABLE,
        f"{phase}_failed",
        commands,
        detail=detail,
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
        detail=_safe_stderr(completed.stderr),
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


def _first_json_object(*values: str | None) -> dict[str, object] | None:
    for value in values:
        payload = _json_object(value)
        if payload is not None:
            return payload
    return None


def _error_object(payload: dict[str, object] | None) -> dict[str, object]:
    if payload is None:
        return {}
    error = payload.get("error")
    return error if isinstance(error, dict) else payload


def _string_value(payload: dict[str, object] | None, key: str) -> str:
    if payload is None:
        return ""
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _declares_failure(payload: dict[str, object] | None) -> bool:
    return payload is not None and payload.get("ok") is False


def _declares_invalid_auth(payload: dict[str, object]) -> bool:
    return any(
        field in payload and payload.get(field) is not True
        for field in ("authenticated", "verified")
    )


def _safe_stderr(raw: str | None) -> str:
    compact = (raw or "").strip().replace("\x00", "")
    if not compact:
        return ""
    payload = _json_object(compact)
    if payload is None:
        return compact[:500]
    safe: dict[str, object] = {}
    for key in ("type", "subtype", "code", "message", "reason", "status"):
        value = payload.get(key)
        if isinstance(value, (str, int, bool)):
            safe[key] = value
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("type", "subtype", "code", "message", "reason", "status"):
            value = error.get(key)
            if isinstance(value, (str, int, bool)):
                safe[f"error.{key}"] = value
    return json.dumps(safe, ensure_ascii=False) if safe else "<structured error>"


def _lark_noninteractive_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.setdefault("NO_COLOR", "1")
    return env
