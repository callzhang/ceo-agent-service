import json
from pathlib import Path
import shlex
import shutil
import subprocess
import uuid


DEFAULT_NOTIFICATION_ICON_PATH = Path(__file__).resolve().parents[1] / "logo.png"


def send_macos_notification(title: str, message: str, url: str | None = None) -> None:
    terminal_notifier = shutil.which("terminal-notifier")
    if url and terminal_notifier:
        command = [terminal_notifier, "-title", title, "-message", message]
        command.extend(["-group", _notification_group_id()])
        command.extend(["-sound", "default"])
        if DEFAULT_NOTIFICATION_ICON_PATH.exists():
            command.extend(["-appIcon", str(DEFAULT_NOTIFICATION_ICON_PATH)])
        command.extend(["-execute", _open_url_command(url)])
        subprocess.run(
            command,
            check=False,
        )
        return

    script = f"display notification {_applescript_string(message)} with title {_applescript_string(title)}"
    subprocess.run(["osascript", "-e", script], check=False)


def _applescript_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _notification_group_id() -> str:
    return f"ceo-agent-service-{uuid.uuid4().hex}"


def _open_url_command(url: str) -> str:
    return f"/usr/bin/open {shlex.quote(url)}"
