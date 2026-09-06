"""Narrow macOS boundary for onboarding the dedicated WeChat reader."""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.wechat.reader_ipc import ReaderIpcError


READER_LABEL = "com.stardust.ceo-agent.wechat-reader"
FULL_DISK_ACCESS_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
)
DEFAULT_READER_APP = Path("~/Applications/CEO WeChat Reader.app")
DEFAULT_LAUNCH_AGENT = Path("~/Library/LaunchAgents/com.stardust.ceo-agent.wechat-reader.plist")


class PermissionOnboardingError(RuntimeError):
    """The reader onboarding command or readiness check could not complete."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True)
class PermissionLaunchResult:
    app_name: str
    app_path: str
    settings_opened: bool


def _path(value: str | os.PathLike[str] | None, default: Path) -> Path:
    return Path(value if value is not None else default).expanduser()


def open_full_disk_access_settings(
    *,
    reader_app: str | os.PathLike[str] | None = None,
    launch_agent: str | os.PathLike[str] | None = None,
    run_command: Callable = subprocess.run,
) -> PermissionLaunchResult:
    """Validate the reader installation and open macOS Full Disk Access settings."""
    app_path = _path(reader_app, DEFAULT_READER_APP)
    launch_agent_path = _path(launch_agent, DEFAULT_LAUNCH_AGENT)
    if not app_path.is_dir():
        raise PermissionOnboardingError(f"reader app directory is missing: {app_path}")
    if not launch_agent_path.is_file():
        raise PermissionOnboardingError(f"launch agent file is missing: {launch_agent_path}")

    result = run_command(
        ["/usr/bin/open", FULL_DISK_ACCESS_SETTINGS_URL],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr or ""
        raise PermissionOnboardingError(
            f"opening Full Disk Access settings failed: {stderr.strip() or result.returncode}",
            returncode=result.returncode,
            stderr=stderr,
        )
    return PermissionLaunchResult(
        app_name="CEO WeChat Reader",
        app_path=str(app_path),
        settings_opened=True,
    )


def restart_reader_and_wait(
    reader,
    *,
    uid: int | None = None,
    run_command: Callable = subprocess.run,
    monotonic: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
    timeout_seconds: float = 15.0,
) -> dict:
    """Kickstart the reader LaunchAgent and wait for its ready health response."""
    resolved_uid = os.getuid() if uid is None else uid
    result = run_command(
        ["/bin/launchctl", "kickstart", "-k", f"gui/{resolved_uid}/{READER_LABEL}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr or ""
        raise PermissionOnboardingError(
            f"restarting WeChat reader failed: {stderr.strip() or result.returncode}",
            returncode=result.returncode,
            stderr=stderr,
        )

    deadline = monotonic() + timeout_seconds
    while True:
        try:
            health = reader.health()
        except ReaderIpcError:
            health = None
        if isinstance(health, dict) and health.get("status") == "ready":
            return health
        if monotonic() >= deadline:
            raise PermissionOnboardingError("Reader did not become ready before timeout")
        pause(0.2)
