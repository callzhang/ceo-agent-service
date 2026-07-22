"""Offline-first dependency, credential, and explicit live connection checks."""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any


CHANNEL_PACKAGE = "lark-channel-sdk"
CHANNEL_VERSION = "1.2.0"
OAPI_PACKAGE = "lark-oapi"
OAPI_VERSION = "1.7.1"
KEYRING_SERVICE = "ceo-agent-service/feishu"
KEYRING_USERNAME = "app_secret"

REQUIRED_TENANT_SCOPES = (
    "im:message.p2p_msg:readonly",
    "im:message.group_at_msg:readonly",
    "im:message:send_as_bot",
)
REQUIRED_EVENTS = ("im.message.receive_v1",)


@dataclass(frozen=True)
class FeishuDependencyStatus:
    channel_installed: bool
    channel_version: str = ""
    channel_version_ok: bool = False
    oapi_installed: bool = False
    oapi_version: str = ""
    oapi_version_ok: bool = False


@dataclass(frozen=True)
class FeishuDoctorResult:
    status: str
    app_id_configured: bool
    app_secret_configured: bool
    dependencies: FeishuDependencyStatus
    live_verified: bool = False
    checks: dict[str, str] = field(default_factory=dict)


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return ""


def dependency_status() -> FeishuDependencyStatus:
    channel = _version(CHANNEL_PACKAGE)
    oapi = _version(OAPI_PACKAGE)
    return FeishuDependencyStatus(
        channel_installed=bool(channel),
        channel_version=channel,
        channel_version_ok=channel == CHANNEL_VERSION,
        oapi_installed=bool(oapi),
        oapi_version=oapi,
        oapi_version_ok=oapi == OAPI_VERSION,
    )


def registration_manifest() -> dict[str, tuple[str, ...] | bool]:
    """The exact least-privilege manifest for manual or official registration."""
    return {
        "tenant_scopes": REQUIRED_TENANT_SCOPES,
        "events": REQUIRED_EVENTS,
        "addons_preset": False,
    }


def save_app_secret(app_secret: str) -> None:
    """Store a secret in Keychain without ever returning or logging it."""
    secret = app_secret.strip()
    if not secret:
        raise ValueError("Feishu App Secret must not be empty")
    keyring = importlib.import_module("keyring")
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, secret)


def doctor(*, app_id: str = "", app_secret: str = "") -> FeishuDoctorResult:
    """Perform only local checks; this function never connects to Feishu."""
    dependencies = dependency_status()
    configured = bool(app_id.strip()) and bool(app_secret.strip())
    version_ok = dependencies.channel_version_ok and dependencies.oapi_version_ok
    if configured and version_ok:
        status = "ready_for_explicit_live_check"
    elif not configured:
        status = "credentials_missing"
    else:
        status = "dependencies_missing_or_unpinned"
    return FeishuDoctorResult(
        status=status,
        app_id_configured=bool(app_id.strip()),
        app_secret_configured=bool(app_secret.strip()),
        dependencies=dependencies,
        checks={
            "network": "not_checked",
            "send": "not_checked",
            "tenant_permissions": "requires_admin_console_or_explicit_live_check",
        },
    )


async def verify_live_connection(client, *, timeout: float = 30) -> bool:
    """Explicit receive-only check.  It never invokes a send method."""
    try:
        await client.connect_until_ready(timeout=timeout)
        return True
    finally:
        await client.disconnect()
