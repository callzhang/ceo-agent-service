import json
import shutil
import subprocess


def send_macos_notification(title: str, message: str, url: str | None = None) -> None:
    terminal_notifier = shutil.which("terminal-notifier")
    if url and terminal_notifier:
        subprocess.run(
            [terminal_notifier, "-title", title, "-message", message, "-open", url],
            check=False,
        )
        return

    script = f"display notification {_applescript_string(message)} with title {_applescript_string(title)}"
    subprocess.run(["osascript", "-e", script], check=False)


def _applescript_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
