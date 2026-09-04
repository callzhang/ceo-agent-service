#!/usr/bin/env python3
"""Fetch Dingteam OKR in headless service mode without a visible browser."""
from __future__ import annotations

import argparse
import importlib.util
import json
import fcntl
import socket
import subprocess
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright


SCRIPT_DIR = Path("/Users/derek/.agents/skills/dingtang-okr-review/scripts")
HEADLESS_REFRESH_SECONDS = 40
HEADLESS_LOCK_TIMEOUT_SECONDS = 10
_browser_spec = importlib.util.spec_from_file_location(
    "dingteam_okr_browser_source", SCRIPT_DIR / "dingteam_okr_browser_source.py"
)
if _browser_spec is None or _browser_spec.loader is None:
    raise RuntimeError("Dingteam OKR browser source is unavailable")
browser = importlib.util.module_from_spec(_browser_spec)
_browser_spec.loader.exec_module(browser)


def _get_headless_headers() -> dict[str, str]:
    """Reuse a valid token or refresh it through the headless browser only."""
    cached = browser._read_cache()
    if cached:
        return cached
    headers = _capture_stable_headless_headers()
    browser._write_cache(headers)
    return headers


def _headless_cdp_command(playwright, *, port: int, profile_dir: str) -> list[str]:
    """Launch an isolated Chrome process that Playwright connects to over loopback."""
    return [
        playwright.chromium.executable_path,
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "about:blank",
    ]


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _headless_cdp_browser(playwright):
    """Run a disposable headless Chrome without Playwright's crashing pipe mode."""
    port = _reserve_loopback_port()
    with tempfile.TemporaryDirectory(prefix="ceo-okr-chrome-") as profile_dir:
        process = subprocess.Popen(
            _headless_cdp_command(playwright, port=port, profile_dir=profile_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        endpoint = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("okr_headless_browser_exited")
                try:
                    with urllib.request.urlopen(f"{endpoint}/json/version", timeout=1):
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                raise RuntimeError("okr_headless_browser_start_timeout")
            browser_instance = playwright.chromium.connect_over_cdp(endpoint)
            try:
                yield browser_instance
            finally:
                browser_instance.close()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


@contextmanager
def _headless_browser_lock():
    """Serialize Chrome startup across the service's OKR workers."""
    with open("/private/tmp/ceo-okr-headless.lock", "a", encoding="utf-8") as lock:
        deadline = time.monotonic() + HEADLESS_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("okr_headless_browser_lock_timeout")
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _capture_stable_headless_headers() -> dict[str, str]:
    """Capture source headers after the OKR page has finished navigating."""
    browser.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    captured: dict[str, str] = {}
    with _headless_browser_lock():
        with sync_playwright() as playwright:
            with _headless_cdp_browser(playwright) as browser_instance:
                context = browser_instance.new_context(
                    storage_state=str(browser.PROFILE_DIR / "storage_state.json")
                )
                try:
                    def on_request(request):
                        if "/data/okr/" not in request.url or captured:
                            return
                        for key, value in request.headers.items():
                            if key.lower() in browser.AUTH_HEADER_KEYS:
                                captured[browser._canonical(key)] = value

                    context.on("request", on_request)
                    page = context.new_page()
                    page.goto(browser.ENTRY_URL, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2500)
                    for _ in range(3):
                        try:
                            page.evaluate("() => document.readyState")
                            break
                        except Exception as exc:
                            if "execution context was destroyed" not in str(exc).lower():
                                raise
                            page.wait_for_timeout(1000)
                    deadline = time.monotonic() + HEADLESS_REFRESH_SECONDS
                    while time.monotonic() < deadline and "Authorization" not in captured:
                        try:
                            page.wait_for_timeout(800)
                            page.evaluate("() => document.readyState")
                        except Exception as exc:
                            if "execution context was destroyed" not in str(exc).lower():
                                raise
                    if "Authorization" not in captured:
                        app_state = page.evaluate(
                            """() => ({
                                root: !!document.querySelector('#root-master'),
                                mounted: !!document.querySelector('#root-master > * > *'),
                            })"""
                        )
                        if app_state.get("root") and not app_state.get("mounted"):
                            raise RuntimeError("okr_website_unavailable: Dingteam OKR website did not render")
                finally:
                    context.close()
    if "Authorization" not in captured:
        raise RuntimeError("could not capture Dingteam auth token from the browser")
    return captured


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--period-label", required=True)
    args = parser.parse_args()

    headers = _get_headless_headers()
    result = browser.direct.fetch_with_headers(
        args.user_id,
        args.period_label,
        headers,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
