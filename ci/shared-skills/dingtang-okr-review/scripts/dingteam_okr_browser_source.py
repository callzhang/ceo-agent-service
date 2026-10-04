#!/usr/bin/env python3
"""Headless-browser live source for 叮当OKR (Dingteam) — no always-open tab.

Architecture:
  - A dedicated, persistent browser profile holds the DingTalk SSO session.
  - `login`  : one-time, HEADFUL. You scan the DingTalk QR once; the session is
               saved into the profile dir.
  - `fetch`  : service-driven, HEADLESS. Reuses the saved session to auto-SSO,
               lets 叮当OKR mint a fresh session JWT, captures it, and pulls OKR
               data via direct HTTP (shared with dingteam_okr_direct_source).
  - Token cache: the captured JWT is cached (by its `exp`); while it is valid,
               `fetch` skips launching the browser entirely.

Output matches the dingtang-okr-review contract: `processed.objectives` /
`processed.okrRows`. The auth token is never printed.

Requires: playwright (`pip install playwright`); uses the system Google Chrome
via channel="chrome" (falls back to Playwright's bundled Chromium).
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

# --- reuse the direct-HTTP fetch/processing from the sibling module ---
_DIRECT_PATH = Path(__file__).resolve().parent / "dingteam_okr_direct_source.py"
_spec = importlib.util.spec_from_file_location("dingteam_okr_direct_source", _DIRECT_PATH)
direct = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(direct)

CORP_ID = os.getenv("CEO_OKR_DINGTEAM_CORPID", "ding8ffc70a4ef94915f35c2f4657eb6378f")
APP_ID = os.getenv("CEO_OKR_DINGTEAM_APPID", "40707")
SUITE_ID = os.getenv("CEO_OKR_DINGTEAM_SUITEID", "9242001")
ENTRY_URL = os.getenv(
    "CEO_OKR_DINGTEAM_ENTRY_URL",
    f"https://dingokr.dingteam.com/web/okr/pc/index.html?corpId={CORP_ID}#/okr/cycle",
)

PROFILE_DIR = Path(
    os.getenv(
        "CEO_OKR_BROWSER_PROFILE",
        str(Path.home() / ".agents/runtime/dingteam-okr-chrome"),
    )
)
CACHE_PATH = PROFILE_DIR / "token_cache.json"
AUTH_HEADER_KEYS = {
    "authorization",
    "x-dingteam-auth-app-id",
    "x-dingteam-auth-suite-id",
    "x-dingteam-release",
    "x-space-id",
}
TOKEN_SKEW_SECONDS = 300  # refresh if the JWT expires within 5 minutes


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _jwt_exp(headers: dict[str, str]) -> int | None:
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    token = auth.split(" ")[-1] if auth else ""
    if token.count(".") < 2:
        return None
    try:
        seg = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
        exp = payload.get("exp")
        return int(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


def _read_cache() -> dict[str, str] | None:
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    headers = data.get("headers")
    if not isinstance(headers, dict):
        return None
    exp = _jwt_exp(headers)
    if exp is None or exp <= time.time() + TOKEN_SKEW_SECONDS:
        return None
    return headers


def _write_cache(headers: dict[str, str]) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps({"headers": headers, "savedAt": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        os.chmod(CACHE_PATH, 0o600)
    except OSError:
        pass


def _evaluate_after_navigation(page, script: str, *, timeout_seconds: float = 10):
    """Evaluate once Chromium has installed a stable execution context."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return page.evaluate(script)
        except Exception as exc:
            if "execution context was destroyed" not in str(exc).lower():
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Dingteam page kept navigating and never exposed a stable context"
                ) from exc
            time.sleep(0.25)


def _capture_candidate_headers(
    captured: dict[str, str], request_headers: dict[str, str]
) -> bool:
    """Accept only a token that can outlive the cache refresh skew window."""
    candidate = {
        _canonical(key): value
        for key, value in request_headers.items()
        if key.lower() in AUTH_HEADER_KEYS
    }
    expires_at = _jwt_exp(candidate)
    if expires_at is None or expires_at <= time.time() + TOKEN_SKEW_SECONDS:
        return False
    captured.clear()
    captured.update(candidate)
    return True


def _capture_headers(headless: bool, wait_seconds: float) -> dict[str, str]:
    """Open the OKR page in a persistent context and capture the session auth headers."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    captured: dict[str, str] = {}

    cur_url = ""
    with sync_playwright() as p:
        launch_kwargs = dict(user_data_dir=str(PROFILE_DIR), headless=headless)
        # Prefer the installed system Chrome. Playwright's downloaded
        # Chromium is optional on this host, while the documented source uses
        # system Chrome for both interactive login and headless fetches.
        system_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if system_chrome.is_file():
            launch_kwargs["executable_path"] = str(system_chrome)
            context = p.chromium.launch_persistent_context(**launch_kwargs)
        else:
            context = p.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)

        # Always tear the browser down — otherwise the headless Chromium lingers
        # and holds memory after the fetch returns.
        try:
            def on_request(request):
                if "/data/okr/" in request.url and not captured:
                    try:
                        headers = request.headers
                        if not headers:
                            headers = request.all_headers()
                        _capture_candidate_headers(captured, headers)
                    except Exception:
                        pass

            context.on("request", on_request)
            # Chromium starts with an internal about:blank page.  Reusing that
            # page can leave the headful login window visually blank on macOS
            # while navigation is still attached to the startup page.  Use a
            # fresh page for the actual entry URL and close the placeholder.
            placeholder = context.pages[0] if context.pages else None
            page = context.new_page()
            if placeholder is not None:
                try:
                    placeholder.close()
                except Exception:
                    pass
            try:
                page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                _log(f"navigation note: {exc}")

            # Do not open a visible Chrome window when standalone Chrome cannot
            # initialize the Dingteam application.  In that environment the
            # HTML shell loads but #root-master remains empty (the app depends
            # on DingTalk's embedded WebView), so an interactive popup would
            # only be a disruptive blank page.
            time.sleep(1.5)
            app_state = _evaluate_after_navigation(
                page,
                """() => ({
                    root: !!document.querySelector('#root-master'),
                    mounted: !!document.querySelector('#root-master > * > *'),
                    title: document.title || '',
                })"""
            )
            if (
                isinstance(app_state, dict)
                and app_state.get("root")
                and not app_state.get("mounted")
            ):
                raise RuntimeError(
                    "okr_website_unavailable: Dingteam OKR website did not "
                    "render in the current browser session"
                )

            nudge = (
                "() => { try {"
                "  if (location.hash.indexOf('okr') < 0) { location.hash = '#/okr/personal'; }"
                "  if (window.webpackChunkallinone) {"
                "    window.webpackChunkallinone.push([['__hb'+Date.now()],{},function(r){window.__hbReq=r;}]);"
                "    var a = window.__hbReq(37615).Z;"
                "    a.person.period.list({userId:'__noop__'}).catch(function(){});"
                "  } } catch(e){} }"
            )
            cur_url = page.url
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline and "Authorization" not in captured:
                # nudge every dingokr page in the context to issue an OKR API call
                for pg in list(context.pages):
                    try:
                        if "dingokr.dingteam.com" in pg.url:
                            cur_url = pg.url
                            _evaluate_after_navigation(pg, nudge)
                    except Exception:
                        pass
                time.sleep(0.8)
        finally:
            try:
                context.close()
            except Exception:
                pass

    if "Authorization" not in captured:
        if "login" in cur_url or "oauth2" in cur_url or "dingtalk.com" in cur_url:
            raise RuntimeError(
                "Dingteam session is not logged in (stuck at DingTalk login). "
                "Run: dingteam_okr_browser_source.py login"
            )
        raise RuntimeError("could not capture Dingteam auth token from the browser")
    return captured


def _canonical(key: str) -> str:
    mapping = {
        "authorization": "Authorization",
        "x-dingteam-auth-app-id": "X-Dingteam-Auth-App-Id",
        "x-dingteam-auth-suite-id": "X-Dingteam-Auth-Suite-Id",
        "x-dingteam-release": "x-Dingteam-release",
        "x-space-id": "X-Space-Id",
    }
    return mapping.get(key.lower(), key)


def get_headers(allow_browser: bool = True) -> dict[str, str]:
    cached = _read_cache()
    if cached:
        _log("using cached token (browser not launched)")
        return cached
    if not allow_browser:
        raise RuntimeError("no valid cached token and browser launch disabled")
    _log("cached token missing/expired — trying headless refresh")
    try:
        headers = _capture_headers(headless=True, wait_seconds=40)
    except Exception as exc:
        error_text = str(exc)
        if (
            "okr_website_unavailable" in error_text
            or "ProcessSingleton" in error_text
            or "SingletonLock" in error_text
        ):
            raise
        _log(
            "headless refresh needs interactive authentication; opening a visible "
            "browser window. Complete DingTalk login/QR to continue."
        )
        headers = _capture_headers(headless=False, wait_seconds=240)
    _write_cache(headers)
    return headers


def cmd_login() -> int:
    _log(
        "Opening a browser window. Complete the DingTalk login/QR for 叮当OKR.\n"
        "Waiting up to 4 minutes for the session..."
    )
    headers = _capture_headers(headless=False, wait_seconds=240)
    _write_cache(headers)
    exp = _jwt_exp(headers)
    when = (
        datetime.fromtimestamp(exp, tz=timezone.utc).isoformat() if exp else "unknown"
    )
    _log(f"login OK — session saved to {PROFILE_DIR}. Token valid until {when}.")
    return 0


def cmd_fetch(user_id: str, period_label: str) -> int:
    headers = get_headers(allow_browser=True)
    result = direct.fetch_with_headers(user_id, period_label, headers)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("login", help="one-time interactive DingTalk login (headful)")

    fetch_p = sub.add_parser("fetch", help="headless OKR fetch (default)")
    fetch_p.add_argument("--user-id", required=True)
    fetch_p.add_argument("--period-label", required=True)

    # Allow bare `--user-id/--period-label` (no subcommand) to act as `fetch`,
    # so it is a drop-in for CEO_OKR_LIVE_SOURCE_COMMAND.
    argv = sys.argv[1:]
    if argv and argv[0] not in {"login", "fetch", "-h", "--help"}:
        argv = ["fetch", *argv]
    args = parser.parse_args(argv)

    if args.command == "login":
        return cmd_login()
    if args.command == "fetch":
        return cmd_fetch(args.user_id, args.period_label)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
