import json
import subprocess


def send_macos_notification(title: str, message: str, url: str | None = None) -> None:
    script = f"display notification {_applescript_string(message)} with title {_applescript_string(title)}"
    if url:
        script = f"{script}\nopen location {_applescript_string(url)}"
    subprocess.run(["osascript", "-e", script], check=False)


def _applescript_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
