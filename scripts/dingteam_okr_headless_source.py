#!/usr/bin/env python3
"""Fetch Dingteam OKR in headless service mode without a visible browser."""
from __future__ import annotations

import argparse
import importlib.util
import json
import fcntl
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright


SCRIPT_DIR = Path("/Users/derek/.agents/skills/dingtang-okr-review/scripts")
LOCAL_SSO_CONFIRM_SCRIPT = Path(__file__).with_name("dingtalk_local_sso_confirm.py")
HEADLESS_REFRESH_SECONDS = 40
HEADLESS_LOCK_TIMEOUT_SECONDS = 130
LOCAL_SSO_TIMEOUT_MS = 10_000
LOCAL_SSO_DIALOG_DELAY_MS = 1_500
LOCAL_SSO_CONFIRM_PROCESS_SECONDS = 40
LOCAL_SSO_CURRENT_PAGE = ".app-page.app-page-curr"
LOCAL_SSO_DIRECT_BUTTON = (
    f"{LOCAL_SSO_CURRENT_PAGE} "
    ".module-confirm-button.base-comp-button-type-primary"
    ":not(.base-comp-button-disabled)"
)
LOCAL_SSO_ACCOUNT_AVATAR = f"{LOCAL_SSO_CURRENT_PAGE} .module-qrcode-user-avatar"
LOCAL_SSO_CORP_ITEM = f"{LOCAL_SSO_CURRENT_PAGE} .module-corp-sel-listitem"
LOCAL_SSO_CORP_NAME = "北京星尘纪元智能科技有限公司"
OKR_REQUEST_NUDGE = """() => { try {
    if (location.hash.indexOf('okr') < 0) { location.hash = '#/okr/personal'; }
    if (window.webpackChunkallinone) {
        window.webpackChunkallinone.push([
            ['__ceo_okr_' + Date.now()], {},
            function(require) { window.__ceoOkrRequire = require; }
        ]);
        var api = window.__ceoOkrRequire(37615).Z;
        api.person.period.list({userId: '__noop__'}).catch(function() {});
    }
} catch (error) {} }"""
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
    with _headless_browser_lock():
        cached = browser._read_cache()
        if cached:
            return cached
        headers = _capture_stable_headless_headers()
        browser._write_cache(headers)
        return headers


def _validate_captured_headers(headers: dict[str, str]) -> dict[str, str]:
    """Reject an expired dedicated browser session before querying OKR APIs."""
    expires_at = browser._jwt_exp(headers)
    if expires_at is None or expires_at <= time.time() + browser.TOKEN_SKEW_SECONDS:
        raise RuntimeError(
            "okr_headless_session_expired: dedicated Dingteam session requires login"
        )
    return headers


def _unmounted_page_error(
    *, page_url: str, app_state: dict[str, bool]
) -> RuntimeError | None:
    """Classify an unmounted page without confusing login expiry with site health."""
    normalized_url = page_url.casefold()
    if "login.dingtalk.com/" in normalized_url or "/oauth2/" in normalized_url:
        return RuntimeError(
            "okr_headless_session_expired: dedicated Dingteam session requires login"
        )
    if app_state.get("root") and not app_state.get("mounted"):
        return RuntimeError(
            "okr_website_unavailable: Dingteam OKR website did not render"
        )
    return None


def _is_dingtalk_login_url(page_url: str) -> bool:
    normalized_url = page_url.casefold()
    return "login.dingtalk.com/" in normalized_url and "/oauth2/" in normalized_url


def _confirm_local_dingtalk_login() -> None:
    subprocess.run(
        ["/usr/bin/python3", str(LOCAL_SSO_CONFIRM_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
        timeout=LOCAL_SSO_CONFIRM_PROCESS_SECONDS,
    )


def _submit_local_dingtalk_account(page) -> None:
    direct_login = page.locator(LOCAL_SSO_DIRECT_BUTTON).first
    direct_login.wait_for(state="visible", timeout=LOCAL_SSO_TIMEOUT_MS)
    direct_login.click(timeout=LOCAL_SSO_TIMEOUT_MS)

    try:
        corp = page.locator(LOCAL_SSO_CORP_ITEM).filter(
            has_text=LOCAL_SSO_CORP_NAME
        ).first
        corp.wait_for(state="visible", timeout=LOCAL_SSO_TIMEOUT_MS)
        corp.click(timeout=LOCAL_SSO_TIMEOUT_MS)
    except Exception:
        # A single-organization account navigates directly to Dingteam.
        pass


def _attempt_local_dingtalk_sso(page) -> bool:
    """Use the logged-in desktop DingTalk account to recover the web session."""
    if not _is_dingtalk_login_url(getattr(page, "url", "")):
        return False
    try:
        _submit_local_dingtalk_account(page)
        return True
    except Exception:
        pass
    try:
        page.get_by_text("QR Code", exact=True).click(timeout=LOCAL_SSO_TIMEOUT_MS)
        avatar = page.locator(LOCAL_SSO_ACCOUNT_AVATAR).first
        avatar.wait_for(state="visible", timeout=LOCAL_SSO_TIMEOUT_MS)
        avatar.click(timeout=LOCAL_SSO_TIMEOUT_MS)
        page.wait_for_timeout(LOCAL_SSO_DIALOG_DELAY_MS)
        _confirm_local_dingtalk_login()
        _submit_local_dingtalk_account(page)
        return True
    except Exception:
        return False


def _maybe_attempt_local_dingtalk_sso(page, *, attempted: bool) -> bool:
    if attempted or not _is_dingtalk_login_url(getattr(page, "url", "")):
        return attempted
    return _attempt_local_dingtalk_sso(page)


def _nudge_dingteam_pages(context) -> None:
    for page in list(context.pages):
        try:
            if page.url.startswith("https://dingokr.dingteam.com/"):
                page.evaluate(OKR_REQUEST_NUDGE)
        except Exception:
            continue


def _headless_cdp_command(playwright, *, port: int, profile_dir: str) -> list[str]:
    """Launch an isolated Chrome process that Playwright connects to over loopback."""
    return [
        playwright.chromium.executable_path,
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=LocalNetworkAccessChecks",
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
    """Run the authenticated headless profile without Playwright's pipe mode."""
    port = _reserve_loopback_port()
    browser.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        _headless_cdp_command(
            playwright,
            port=port,
            profile_dir=str(browser.PROFILE_DIR),
        ),
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
    with sync_playwright() as playwright:
        with _headless_cdp_browser(playwright) as browser_instance:
            context = browser_instance.contexts[0]
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
                local_sso_attempted = _maybe_attempt_local_dingtalk_sso(
                    page, attempted=False
                )
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
                        _nudge_dingteam_pages(context)
                        local_sso_attempted = _maybe_attempt_local_dingtalk_sso(
                            page, attempted=local_sso_attempted
                        )
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
                    page_error = _unmounted_page_error(
                        page_url=page.url,
                        app_state=app_state,
                    )
                    if page_error is not None:
                        raise page_error
            finally:
                context.close()
    return _validate_captured_headers(captured)


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
