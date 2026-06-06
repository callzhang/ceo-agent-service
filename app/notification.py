import json
from pathlib import Path
import shlex
import shutil
import subprocess
from urllib import error, request

from app.config import notification_bridge_base_url


DEFAULT_NOTIFICATION_ICON_PATH = Path(__file__).resolve().parent / "logo.png"


def send_macos_notification(title: str, message: str, url: str | None = None) -> None:
    if _send_terminal_notifier_notification(title=title, message=message, url=url):
        return

    if _send_browser_notification(title=title, message=message, url=url):
        return

    script = f"display notification {_applescript_string(message)} with title {_applescript_string(title)}"
    subprocess.run(["osascript", "-e", script], check=False)


def _applescript_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _send_terminal_notifier_notification(
    title: str,
    message: str,
    url: str | None,
) -> bool:
    executable = shutil.which("terminal-notifier")
    if not executable:
        return False
    command = [
        executable,
        "-title",
        title,
        "-message",
        message,
        "-group",
        "ceo-agent-service",
    ]
    if DEFAULT_NOTIFICATION_ICON_PATH.exists():
        command.extend(["-appIcon", DEFAULT_NOTIFICATION_ICON_PATH.as_uri()])
    if url:
        command.extend(
            [
                "-execute",
                f"/usr/bin/curl -fsS {shlex.quote(url)} >/dev/null 2>&1",
            ]
        )
    completed = subprocess.run(command, check=False)
    return completed.returncode == 0


def _send_browser_notification(title: str, message: str, url: str | None) -> bool:
    endpoint = f"{notification_bridge_base_url()}/browser-notifications"
    body = json.dumps(
        {"title": title, "message": message, "url": url or ""},
        ensure_ascii=False,
    ).encode("utf-8")
    http_request = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return False
    return bool(payload.get("delivered"))
