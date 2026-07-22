from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable

from app.channels.models import ChannelDoctorStatus, ChannelMessage, ChannelSendResult

CliRunner = Callable[..., subprocess.CompletedProcess[str]]


class DingTalkCliAdapter:
    """Thin status adapter; DingTalk production remains in the existing DWS worker."""

    channel_name = "dingtalk"

    def __init__(
        self,
        *,
        binary: str = "dws",
        runner: CliRunner = subprocess.run,
    ):
        self.binary = binary
        self.runner = runner

    def doctor(self) -> ChannelDoctorStatus:
        command = [self.binary, "auth", "status", "--format", "json", "--timeout", "5"]
        if shutil.which(self.binary) is None:
            return ChannelDoctorStatus(
                channel=self.channel_name,
                status="blocked",
                reason=f"{self.binary} command not found; existing DingTalk worker cannot be probed",
                command=command,
            )
        try:
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ChannelDoctorStatus(
                channel=self.channel_name,
                status="failed",
                reason="DingTalk DWS auth status command timed out",
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
                reason=reason or f"DingTalk DWS auth status exited {completed.returncode}",
                command=command,
            )
        return ChannelDoctorStatus(
            channel=self.channel_name,
            status="ready",
            reason="DingTalk DWS auth status completed",
            command=command,
        )

    def list_recent_messages(self, *, limit: int = 50) -> list[ChannelMessage]:
        raise NotImplementedError("DingTalk messages are produced by the existing worker")

    def send_reply(self, *, conversation_id: str, text: str) -> ChannelSendResult:
        return ChannelSendResult(
            channel=self.channel_name,
            status="blocked",
            reason="DingTalk sends remain owned by the existing DWS worker",
        )
