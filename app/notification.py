import json
from pathlib import Path
import shlex
import shutil
import subprocess
from urllib import error, request
from urllib.parse import quote

from app.config import notification_bridge_base_url


DEFAULT_NOTIFICATION_ICON_PATH = Path(__file__).resolve().parent / "logo.png"


def dingtalk_conversation_notification_url(
    conversation_id: str,
    *,
    attempt_id: int | None = None,
) -> str | None:
    cleaned_conversation_id = conversation_id.strip()
    if not cleaned_conversation_id:
        return None
    query = f"conversation_id={quote(cleaned_conversation_id, safe='')}"
    if attempt_id is not None:
        query = f"{query}&attempt_id={int(attempt_id)}"
    return f"{notification_bridge_base_url()}/open-dingtalk?{query}"


def attempt_notification_url(attempt_id: int) -> str:
    return f"{notification_bridge_base_url()}/open-attempt?attempt_id={int(attempt_id)}"


_FOCUS_TAB_SCRIPTS = {
    "Google Chrome": """
tell application "System Events" to set isRunning to (exists process "Google Chrome")
if not isRunning then return "none"
tell application "Google Chrome"
  repeat with w in windows
    set i to 0
    repeat with t in tabs of w
      set i to i + 1
      if (URL of t) starts with ORIGIN then
        set URL of t to TARGET
        set active tab index of w to i
        set index of w to 1
        activate
        return "focused"
      end if
    end repeat
  end repeat
end tell
return "none"
""",
    "Safari": """
tell application "System Events" to set isRunning to (exists process "Safari")
if not isRunning then return "none"
tell application "Safari"
  repeat with w in windows
    repeat with t in tabs of w
      if (URL of t) starts with ORIGIN then
        set URL of t to TARGET
        set current tab of w to t
        set index of w to 1
        activate
        return "focused"
      end if
    end repeat
  end repeat
end tell
return "none"
""",
}


def focus_console_tab(origin: str, target_url: str) -> bool:
    """Show `target_url` in a console tab that is already open.

    Derek, 2026-09-25: clicking a notification should bring up the attempt in
    the console tab he already has open, not a new tab or window. Returns
    False when no browser has a console tab, and the caller opens one.
    """
    for browser, template in _FOCUS_TAB_SCRIPTS.items():
        script = template.replace("ORIGIN", _applescript_string(origin)).replace(
            "TARGET", _applescript_string(target_url)
        )
        try:
            completed = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0 and completed.stdout.strip() == "focused":
            return True
    return False


def send_macos_notification(title: str, message: str, url: str | None = None) -> None:
    if _send_terminal_notifier_notification(title=title, message=message, url=url):
        return

    if _send_browser_notification(title=title, message=message, url=url):
        return

    script = f"display notification {_applescript_string(message)} with title {_applescript_string(title)}"
    subprocess.run(["osascript", "-e", script], check=False)


def send_browser_notification(
    title: str,
    message: str,
    url: str | None = None,
    *,
    notification_id: str | None = None,
    detail_url: str | None = None,
) -> bool:
    return _send_browser_notification(
        title=title,
        message=message,
        url=url,
        notification_id=notification_id,
        detail_url=detail_url,
    )


def dismiss_browser_notification(notification_id: str) -> bool:
    return _send_browser_notification(
        title="",
        message="",
        url=None,
        notification_id=notification_id,
        dismiss=True,
    )


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
                f"/usr/bin/curl -fsS -X POST {shlex.quote(url)} >/dev/null 2>&1",
            ]
        )
    completed = subprocess.run(command, check=False)
    return completed.returncode == 0


def _send_browser_notification(
    title: str,
    message: str,
    url: str | None,
    *,
    notification_id: str | None = None,
    detail_url: str | None = None,
    dismiss: bool = False,
) -> bool:
    endpoint = f"{notification_bridge_base_url()}/browser-notifications"
    payload = {"title": title, "message": message, "url": url or ""}
    if notification_id:
        payload["id"] = notification_id
    if detail_url:
        payload["detail_url"] = detail_url
    if dismiss:
        payload["dismiss"] = True
    body = json.dumps(
        payload,
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
