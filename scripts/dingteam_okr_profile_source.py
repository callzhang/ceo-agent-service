#!/usr/bin/env python3
"""Fetch Dingteam OKR data through the user's authorized Chrome tab.

This wrapper makes the direct source deterministic by navigating the existing
Dingteam tab to the requested user's profile before auth-header capture.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


BASE_URL = (
    "https://dingokr.dingteam.com/web/okr/pc/index.html"
    "?corpid=ding8ffc70a4ef94915f35c2f4657eb6378f&appid=40707&suiteid=9242001"
)
DIRECT_SOURCE = (
    Path.home()
    / ".agents"
    / "skills"
    / "dingtang-okr-review"
    / "scripts"
    / "dingteam_okr_direct_source.py"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--period-label", required=True)
    parser.add_argument("--load-wait-seconds", type=float, default=4.0)
    args = parser.parse_args()

    _navigate_profile(args.user_id)
    time.sleep(max(0.0, args.load_wait_seconds))
    command = [
        sys.executable,
        str(DIRECT_SOURCE),
        "--user-id",
        args.user_id,
        "--period-label",
        args.period_label,
    ]
    completed = subprocess.run(command, text=True, check=False)
    return completed.returncode


def _navigate_profile(user_id: str) -> None:
    profile_url = f"{BASE_URL}#/okr/profile?profileUserId={user_id}"
    script = f"""
tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      if (URL of t) contains "dingokr.dingteam.com" then
        set URL of t to "{profile_url}"
        return "navigated"
      end if
    end repeat
  end repeat
  open location "{profile_url}"
  return "opened"
end tell
"""
    completed = subprocess.run(
        ["osascript", "-e", script],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "LANG": "en_US.UTF-8"},
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())


if __name__ == "__main__":
    raise SystemExit(main())
