from contextlib import contextmanager
import time
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dingteam_okr_headless_source.py"


def load_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("dingteam_okr_headless_source", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_service_entrypoint_disables_visible_browser_fallback():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "_capture_stable_headless_headers" in source
    assert '"--headless=new"' in source
    assert "headless" in source.casefold()
    assert "headful" not in source.casefold()


def test_expired_cache_is_refreshed_headlessly(monkeypatch):
    module = load_module()
    calls = []

    monkeypatch.setattr(module.browser, "_read_cache", lambda: None)
    monkeypatch.setattr(
        module,
        "_capture_stable_headless_headers",
        lambda **kwargs: calls.append(kwargs) or {"Authorization": "Bearer test"},
    )
    written = []
    monkeypatch.setattr(module.browser, "_write_cache", written.append)

    assert module._get_headless_headers() == {"Authorization": "Bearer test"}
    assert calls == [{}]
    assert written == [{"Authorization": "Bearer test"}]


def test_valid_cache_skips_browser_refresh(monkeypatch):
    module = load_module()
    cached = {"Authorization": "Bearer cached"}

    monkeypatch.setattr(module.browser, "_read_cache", lambda: cached)
    monkeypatch.setattr(
        module,
        "_capture_stable_headless_headers",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("browser should not start")),
    )

    assert module._get_headless_headers() == cached


def test_waiting_caller_rechecks_cache_after_acquiring_refresh_lock(monkeypatch):
    module = load_module()
    refreshed = {"Authorization": "Bearer refreshed-by-first-caller"}
    cache_reads = iter([None, refreshed])
    lock_entries = []

    monkeypatch.setattr(module.browser, "_read_cache", lambda: next(cache_reads))

    @contextmanager
    def refresh_lock():
        lock_entries.append("entered")
        yield

    monkeypatch.setattr(module, "_headless_browser_lock", refresh_lock)
    monkeypatch.setattr(
        module,
        "_capture_stable_headless_headers",
        lambda: (_ for _ in ()).throw(
            AssertionError("a waiting caller must reuse the refreshed cache")
        ),
    )

    assert module._get_headless_headers() == refreshed
    assert lock_entries == ["entered"]


def test_unmounted_okr_shell_is_checked_only_after_auth_wait():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    wait_marker = 'deadline = time.monotonic() + HEADLESS_REFRESH_SECONDS'
    state_check = "page_error = _unmounted_page_error("

    assert source.index(wait_marker) < source.rindex(state_check)


def test_headless_cdp_launch_uses_playwright_browser_binary_and_loopback_only():
    module = load_module()

    class Chromium:
        executable_path = "/tmp/playwright-chrome"

    class Playwright:
        chromium = Chromium()

    assert module._headless_cdp_command(
        Playwright(), port=9222, profile_dir="/tmp/ceo-okr-profile"
    ) == [
        "/tmp/playwright-chrome",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=LocalNetworkAccessChecks",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
        "--user-data-dir=/tmp/ceo-okr-profile",
        "about:blank",
    ]


def test_login_redirect_uses_local_dingtalk_sso_before_expiring(monkeypatch):
    module = load_module()
    calls = []
    submit_calls = 0

    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            calls.append(("wait_for", kwargs))

        def click(self, **kwargs):
            calls.append(("click_avatar", kwargs))

    class Page:
        url = "https://login.dingtalk.com/oauth2/challenge.htm"

        def get_by_text(self, text, *, exact):
            calls.append(("get_by_text", text, exact))
            return self

        def click(self, **kwargs):
            calls.append(("click_qr", kwargs))

        def locator(self, selector):
            calls.append(("locator", selector))
            return Locator()

        def wait_for_timeout(self, milliseconds):
            calls.append(("wait", milliseconds))

    def submit(_page):
        nonlocal submit_calls
        submit_calls += 1
        calls.append(("submit", submit_calls))
        if submit_calls == 1:
            raise RuntimeError("local account is not shown yet")

    monkeypatch.setattr(module, "_submit_local_dingtalk_account", submit)
    monkeypatch.setattr(module, "_confirm_local_dingtalk_login", lambda: calls.append("confirm"))

    assert module._attempt_local_dingtalk_sso(Page()) is True
    assert calls == [
        ("submit", 1),
        ("get_by_text", "QR Code", True),
        ("click_qr", {"timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("locator", module.LOCAL_SSO_ACCOUNT_AVATAR),
        ("wait_for", {"state": "visible", "timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("click_avatar", {"timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("wait", module.LOCAL_SSO_DIALOG_DELAY_MS),
        "confirm",
        ("submit", 2),
    ]


def test_existing_local_account_is_submitted_without_qr(monkeypatch):
    module = load_module()
    calls = []

    class Page:
        url = "https://login.dingtalk.com/oauth2/challenge.htm"

    monkeypatch.setattr(
        module,
        "_submit_local_dingtalk_account",
        lambda page: calls.append(("submit", page.url)),
    )
    monkeypatch.setattr(
        module,
        "_confirm_local_dingtalk_login",
        lambda: (_ for _ in ()).throw(AssertionError("QR fallback is not needed")),
    )

    assert module._attempt_local_dingtalk_sso(Page()) is True
    assert calls == [("submit", Page.url)]


def test_local_account_submission_selects_target_organization():
    module = load_module()
    calls = []

    class Locator:
        @property
        def first(self):
            return self

        def filter(self, **kwargs):
            calls.append(("filter", kwargs))
            return self

        def wait_for(self, **kwargs):
            calls.append(("wait_for", kwargs))

        def click(self, **kwargs):
            calls.append(("click", kwargs))

    class Page:
        def locator(self, selector):
            calls.append(("locator", selector))
            return Locator()

    module._submit_local_dingtalk_account(Page())

    assert calls == [
        ("locator", module.LOCAL_SSO_DIRECT_BUTTON),
        ("wait_for", {"state": "visible", "timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("click", {"timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("locator", module.LOCAL_SSO_CORP_ITEM),
        ("filter", {"has_text": module.LOCAL_SSO_CORP_NAME}),
        ("wait_for", {"state": "visible", "timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("click", {"timeout": module.LOCAL_SSO_TIMEOUT_MS}),
    ]


def test_local_sso_selectors_are_scoped_to_the_current_login_page():
    module = load_module()

    assert module.LOCAL_SSO_DIRECT_BUTTON.startswith(".app-page.app-page-curr ")
    assert module.LOCAL_SSO_ACCOUNT_AVATAR.startswith(".app-page.app-page-curr ")
    assert module.LOCAL_SSO_CORP_ITEM.startswith(".app-page.app-page-curr ")


def test_native_dingtalk_confirmation_allows_slow_local_sso(monkeypatch):
    module = load_module()
    calls = []

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    module._confirm_local_dingtalk_login()

    assert calls[0][1]["timeout"] == module.LOCAL_SSO_CONFIRM_PROCESS_SECONDS
    assert module.LOCAL_SSO_CONFIRM_PROCESS_SECONDS > 30


def test_delayed_login_redirect_starts_local_sso_once(monkeypatch):
    module = load_module()
    page = type("Page", (), {"url": "https://dingokr.dingteam.com/web/okr"})()
    calls = []
    monkeypatch.setattr(
        module,
        "_attempt_local_dingtalk_sso",
        lambda current_page: calls.append(current_page.url) or True,
    )

    attempted = module._maybe_attempt_local_dingtalk_sso(page, attempted=False)
    assert attempted is False

    page.url = "https://login.dingtalk.com/oauth2/challenge.htm"
    attempted = module._maybe_attempt_local_dingtalk_sso(page, attempted=attempted)
    assert attempted is True
    assert module._maybe_attempt_local_dingtalk_sso(page, attempted=attempted) is True
    assert calls == ["https://login.dingtalk.com/oauth2/challenge.htm"]


def test_failed_local_sso_is_retried_until_it_starts(monkeypatch):
    module = load_module()
    page = type(
        "Page", (), {"url": "https://login.dingtalk.com/oauth2/challenge.htm"}
    )()
    outcomes = iter([False, True])
    calls = []

    monkeypatch.setattr(
        module,
        "_attempt_local_dingtalk_sso",
        lambda current_page: calls.append(current_page.url) or next(outcomes),
    )

    attempted = module._maybe_attempt_local_dingtalk_sso(page, attempted=False)
    assert attempted is False
    attempted = module._maybe_attempt_local_dingtalk_sso(page, attempted=attempted)
    assert attempted is True
    assert calls == [page.url, page.url]


def test_header_wait_nudges_only_dingteam_pages():
    module = load_module()
    calls = []

    class Page:
        def __init__(self, url):
            self.url = url

        def evaluate(self, script):
            calls.append((self.url, script))

    context = type(
        "Context",
        (),
        {
            "pages": [
                Page("about:blank"),
                Page("https://login.dingtalk.com/oauth2/challenge.htm"),
                Page("https://dingokr.dingteam.com/web/okr/pc/index.html"),
            ]
        },
    )()

    module._nudge_dingteam_pages(context)

    assert len(calls) == 1
    assert calls[0][0].startswith("https://dingokr.dingteam.com/")
    assert "person.period.list" in calls[0][1]


def test_header_refresh_reuses_the_authenticated_persistent_context(monkeypatch, tmp_path):
    module = load_module()
    closed = []

    class Request:
        url = "https://dingokr.dingteam.com/data/okr/person/period/list"
        headers = {"authorization": "Bearer refreshed"}

    class Page:
        def goto(self, *_args, **_kwargs):
            context.request_handler(Request())

        def wait_for_timeout(self, _milliseconds):
            return None

        def evaluate(self, _script):
            return None

    class Context:
        def on(self, event, handler):
            assert event == "request"
            self.request_handler = handler

        def new_page(self):
            return Page()

        def close(self):
            closed.append(True)

    context = Context()

    class BrowserInstance:
        contexts = [context]

    @contextmanager
    def cdp_browser(_playwright):
        yield BrowserInstance()

    @contextmanager
    def playwright():
        yield object()

    monkeypatch.setattr(module.browser, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(module.browser, "_jwt_exp", lambda _headers: int(time.time()) + 3600)
    monkeypatch.setattr(module, "_headless_cdp_browser", cdp_browser)
    monkeypatch.setattr(module, "sync_playwright", playwright)

    assert module._capture_stable_headless_headers() == {
        "Authorization": "Bearer refreshed"
    }
    assert closed == [True]


def test_expired_captured_session_is_rejected_before_api_fetch(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module.browser, "_jwt_exp", lambda _headers: int(time.time()) - 1)

    with pytest.raises(RuntimeError, match="okr_headless_session_expired"):
        module._validate_captured_headers({"Authorization": "expired"})


def test_missing_captured_session_is_reported_as_expired(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module.browser, "_jwt_exp", lambda _headers: None)

    with pytest.raises(RuntimeError, match="okr_headless_session_expired"):
        module._validate_captured_headers({})

    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "could not capture Dingteam auth token" not in source


def test_login_redirect_is_reported_as_expired_session():
    module = load_module()

    error = module._unmounted_page_error(
        page_url="https://login.dingtalk.com/oauth2/challenge.htm",
        app_state={"root": True, "mounted": False},
    )

    assert isinstance(error, RuntimeError)
    assert str(error).startswith("okr_headless_session_expired:")


def test_unmounted_okr_application_is_reported_as_website_failure():
    module = load_module()

    error = module._unmounted_page_error(
        page_url="https://dingokr.dingteam.com/personal",
        app_state={"root": True, "mounted": False},
    )

    assert isinstance(error, RuntimeError)
    assert str(error).startswith("okr_website_unavailable:")


def test_headless_browser_uses_process_lock():
    module = load_module()
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "_headless_browser_lock" in source
    assert "fcntl.LOCK_EX" in source
    assert "fcntl.LOCK_NB" in source
    assert "HEADLESS_LOCK_TIMEOUT_SECONDS" in source
    assert module.HEADLESS_LOCK_TIMEOUT_SECONDS > module.HEADLESS_REFRESH_SECONDS


def test_headless_browser_force_kills_chrome_after_bounded_shutdown_wait():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    terminate_marker = "process.terminate()"
    timeout_marker = "except subprocess.TimeoutExpired:"
    kill_marker = "process.kill()"

    assert source.index(terminate_marker) < source.index(timeout_marker) < source.index(kill_marker)
