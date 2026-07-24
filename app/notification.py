import json
from ipaddress import ip_address
from pathlib import Path
import shlex
import shutil
import subprocess
from urllib import error, request
from urllib.parse import quote, urlsplit

from app.audit_security import (
    NOTIFICATION_BRIDGE_HEADER_NAME,
    NOTIFICATION_BRIDGE_HEADER_VALUE,
)
from app.config import notification_bridge_base_url


DEFAULT_NOTIFICATION_ICON_PATH = Path(__file__).resolve().parent / "logo.png"


def _validated_notification_bridge_base_url() -> str | None:
    base_url = notification_bridge_base_url()
    if not base_url or base_url != base_url.strip() or any(
        character.isspace() for character in base_url
    ):
        return None
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "http"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or "?" in base_url
        or "#" in base_url
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        return None
    normalized_host = host.rstrip(".").lower()
    if normalized_host != "localhost":
        try:
            if not ip_address(normalized_host).is_loopback:
                return None
        except ValueError:
            return None
    return base_url.rstrip("/")


def dingtalk_conversation_notification_url(
    conversation_id: str,
    *,
    attempt_id: int | None = None,
) -> str | None:
    cleaned_conversation_id = conversation_id.strip()
    base_url = _validated_notification_bridge_base_url()
    if not cleaned_conversation_id or base_url is None:
        return None
    query = f"conversation_id={quote(cleaned_conversation_id, safe='')}"
    if attempt_id is not None:
        query = f"{query}&attempt_id={int(attempt_id)}"
    return f"{base_url}/open-dingtalk?{query}"


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
    base_url = _validated_notification_bridge_base_url()
    if base_url is None:
        return False
    endpoint = f"{base_url}/browser-notifications"
    body = json.dumps(
        {"title": title, "message": message, "url": url or ""},
        ensure_ascii=False,
    ).encode("utf-8")
    http_request = request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            NOTIFICATION_BRIDGE_HEADER_NAME: NOTIFICATION_BRIDGE_HEADER_VALUE,
        },
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError):
        return False
    return bool(payload.get("delivered"))
