import json
from pathlib import Path
import subprocess
from urllib import error, request

from ceo_agent_service.config import notification_bridge_base_url


DEFAULT_NOTIFICATION_ICON_PATH = Path(__file__).resolve().parents[1] / "logo.png"


def send_macos_notification(title: str, message: str, url: str | None = None) -> None:
    if _send_browser_notification(title=title, message=message, url=url):
        return

    script = f"display notification {_applescript_string(message)} with title {_applescript_string(title)}"
    subprocess.run(["osascript", "-e", script], check=False)


def _applescript_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


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
