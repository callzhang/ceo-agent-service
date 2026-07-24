from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable

from app import config
from app.channels.models import ChannelDoctorStatus, ChannelMessage, ChannelSendResult

CliRunner = Callable[..., subprocess.CompletedProcess[str]]


class FeishuCliAdapter:
    # Keep this namespace distinct from the official Bot channel.  Tasks from
    # this diagnostic/read-only adapter must never be claimed by app.feishu.
    channel_name = "feishu_cli"

    def __init__(
        self,
        *,
        binary: str | None = None,
        runner: CliRunner = subprocess.run,
        live_send_enabled: bool | None = None,
    ):
        self.binary = binary or config.feishu_cli_binary()
        self.runner = runner
        # Source-compatibility only. CLI outbound cannot satisfy the official
        # channel's durable approval, app binding, idempotency and ambiguous-
        # result fencing contract, so no flag or caller may enable it.
        _ = live_send_enabled

    def doctor(self) -> ChannelDoctorStatus:
        command = [self.binary, "--help"]
        if shutil.which(self.binary) is None:
            return ChannelDoctorStatus(
                channel=self.channel_name,
                status="blocked",
                reason=f"{self.binary} command not found",
                command=command,
            )
        try:
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                env=_non_interactive_env(),
            )
        except subprocess.TimeoutExpired:
            return ChannelDoctorStatus(
                channel=self.channel_name,
                status="failed",
                reason="Feishu CLI help command timed out",
                command=command,
            )
        except OSError as exc:
            return ChannelDoctorStatus(
                channel=self.channel_name,
                status="failed",
                reason=str(exc),
                command=command,
            )
        if completed.returncode != 0:
            reason = (completed.stderr or completed.stdout or "").strip()
            return ChannelDoctorStatus(
                channel=self.channel_name,
                status="failed",
                reason=reason or f"Feishu CLI help exited {completed.returncode}",
                command=command,
            )
        return ChannelDoctorStatus(
            channel=self.channel_name,
            status="ready",
            reason="Feishu CLI help command completed",
            command=command,
        )

    def list_recent_messages(self, *, limit: int = 50) -> list[ChannelMessage]:
        command = [
            self.binary,
            "message",
            "list",
            "--recent",
            "--limit",
            str(limit),
            "--json",
        ]
        try:
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=_non_interactive_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Feishu CLI list command timed out") from exc
        if completed.returncode != 0:
            reason = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(reason or f"Feishu CLI list exited {completed.returncode}")
        return parse_feishu_messages(completed.stdout)

    def send_reply(self, *, conversation_id: str, text: str) -> ChannelSendResult:
        command = [
            self.binary,
            "message",
            "send",
            "--chat-id",
            conversation_id,
            "--text",
            text,
            "--json",
        ]
        safe_command = _redact_send_command(command)
        return ChannelSendResult(
            channel=self.channel_name,
            status="blocked",
            reason=(
                "Feishu CLI outbound is permanently disabled; use the reviewed "
                "official Bot delivery pipeline"
            ),
            command=safe_command,
        )


def official_bot_doctor() -> ChannelDoctorStatus:
    """Return an offline-only status for the official SDK Bot channel."""
    from app.feishu.setup import doctor

    result = doctor(
        app_id=config.feishu_app_id(),
        app_secret=config.feishu_app_secret(),
    )
    ready = result.status == "ready_for_explicit_live_check"
    return ChannelDoctorStatus(
        channel="feishu_bot",
        status="ready" if ready else "blocked",
        reason=f"offline official Bot check: {result.status}",
        command=["ceo-agent", "feishu", "doctor"],
    )


def parse_feishu_messages(raw: str) -> list[ChannelMessage]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Feishu CLI output is not JSON: {exc}") from exc
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        items = payload["messages"]
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("Feishu CLI JSON must be a list or an object with messages[]")
    return [_parse_message(item, index) for index, item in enumerate(items)]


def _parse_message(item: object, index: int) -> ChannelMessage:
    if not isinstance(item, dict):
        raise ValueError(f"Feishu message at index {index} must be an object")
    conversation = item.get("conversation") if isinstance(item.get("conversation"), dict) else {}
    sender = item.get("sender") if isinstance(item.get("sender"), dict) else {}
    conversation_id = _string_field(item, "conversation_id") or _string_field(conversation, "id")
    message_id = _string_field(item, "message_id") or _string_field(item, "id")
    sent_at = _string_field(item, "sent_at") or _string_field(item, "create_time")
    sender_display = (
        _string_field(item, "sender_display")
        or _string_field(sender, "display_name")
        or _string_field(sender, "name")
    )
    text = _string_field(item, "text") or _string_field(item, "content")
    missing = [
        name
        for name, value in (
            ("conversation_id", conversation_id),
            ("message_id", message_id),
            ("sent_at", sent_at),
            ("sender_display", sender_display),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"Feishu message at index {index} missing required fields: {', '.join(missing)}"
        )
    conversation_type = (
        _string_field(item, "conversation_type")
        or _string_field(conversation, "type")
        or "unknown"
    )
    if conversation_type == "p2p":
        conversation_type = "direct"
    if conversation_type not in {"direct", "group"}:
        conversation_type = "unknown"
    return ChannelMessage(
        channel="feishu_cli",
        conversation_id=conversation_id,
        conversation_title=(
            _string_field(item, "conversation_title")
            or _string_field(conversation, "title")
            or conversation_id
        ),
        conversation_type=conversation_type,
        message_id=message_id,
        sent_at=sent_at,
        sender_display=sender_display,
        text=text,
        raw_json=item,
    )


def _string_field(payload: dict, key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _non_interactive_env() -> dict[str, str]:
    # Do not leak Bot, Memory, model-provider, or unrelated application
    # credentials into a subprocess used only for local diagnostics/reading.
    allowed = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "XDG_CONFIG_HOME")
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env["CI"] = "1"
    env["NO_COLOR"] = "1"
    return env


def _redact_send_command(command: list[str]) -> list[str]:
    safe_command = list(command)
    try:
        text_index = safe_command.index("--text") + 1
    except ValueError:
        return safe_command
    if text_index < len(safe_command):
        safe_command[text_index] = "[redacted]"
    return safe_command
