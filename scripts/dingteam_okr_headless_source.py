#!/usr/bin/env python3
"""Fetch Dingteam OKR in headless service mode without a visible browser."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


SCRIPT_DIR = Path("/Users/derek/.agents/skills/dingtang-okr-review/scripts")
_browser_spec = importlib.util.spec_from_file_location(
    "dingteam_okr_browser_source", SCRIPT_DIR / "dingteam_okr_browser_source.py"
)
if _browser_spec is None or _browser_spec.loader is None:
    raise RuntimeError("Dingteam OKR browser source is unavailable")
browser = importlib.util.module_from_spec(_browser_spec)
_browser_spec.loader.exec_module(browser)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--period-label", required=True)
    args = parser.parse_args()

    headers = browser.get_headers(allow_browser=False)
    result = browser.direct.fetch_with_headers(
        args.user_id,
        args.period_label,
        headers,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
