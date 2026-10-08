from contextlib import contextmanager
import base64
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from urllib.error import URLError

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dingteam_okr_headless_source.py"


def load_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("dingteam_okr_headless_source", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_complete_personal_period_absence_emits_identity_bound_outcome(monkeypatch, capsys):
    module = load_module()
    class MissingPeriod(RuntimeError):
        user_id = "person"
        period_label = "2026 Q4"
        periods = [{"name": "2026 Q3", "okrId": "q3"}]
    monkeypatch.setattr(module.browser.direct, "MissingOkrPeriod", MissingPeriod, raising=False)
    monkeypatch.setattr(module, "_get_headless_headers", lambda: {"private-auth": "do-not-output"})
    def absent(*args):
        raise MissingPeriod("period absent")
    monkeypatch.setattr(module.browser.direct, "fetch_with_headers", absent)

    assert module._fetch_user_okr(user_id="person", period_label="2026 Q4") == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["userId"] == "person"
    assert result["periodLabel"] == "2026 Q4"
    assert result["availability"] == {"status": "goals_not_established", "providerCode": 0, "periodsComplete": True}
    assert result["periods"] == MissingPeriod.periods
    assert "processed" not in result
    assert "do-not-output" not in output


def test_headless_technical_error_is_not_a_goal_absence(monkeypatch, capsys):
    module = load_module()
    class MissingPeriod(RuntimeError):
        pass
    monkeypatch.setattr(module.browser.direct, "MissingOkrPeriod", MissingPeriod, raising=False)
    monkeypatch.setattr(module, "_get_headless_headers", lambda: {})
    def unavailable(*args):
        raise RuntimeError("source authorization failed")
    monkeypatch.setattr(module.browser.direct, "fetch_with_headers", unavailable)
    assert module._fetch_user_okr(user_id="person", period_label="2026 Q4") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["failure"]["scope"] == "shared"
    assert "source authorization failed" in result["failure"]["detail"]
    assert "availability" not in result


@pytest.mark.parametrize("http_code,scope", [(401, "shared"), (403, "member")])
def test_source_http_failure_scope_uses_typed_cause(monkeypatch, capsys, http_code, scope):
    from urllib.error import HTTPError
    module = load_module()
    monkeypatch.setattr(module, "_get_headless_headers", lambda: {})
    def failed(*args):
        error = RuntimeError("source read failed")
        error.__cause__ = HTTPError("https://example.invalid", http_code, "error", {}, None)
        raise error
    monkeypatch.setattr(module.browser.direct, "fetch_with_headers", failed)
    assert module._fetch_user_okr(user_id="person", period_label="2026 Q4") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["failure"]["scope"] == scope
    assert result["failure"]["code"] == f"okr_source_http_{http_code}"
    assert "processed" not in result


def test_authentication_readiness_failure_is_shared_and_redacted(monkeypatch, capsys):
    module = load_module()
    def failed():
        raise RuntimeError("session unavailable; Bearer secretcredential123456")
    monkeypatch.setattr(module, "_get_headless_headers", failed)
    assert module._fetch_user_okr(user_id="person", period_label="2026 Q4") == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["failure"]["scope"] == "shared"
    assert result["failure"]["code"] == "okr_authentication_readiness_failed"
    assert "secretcredential123456" not in output


def test_headless_entrypoint_runs_without_inherited_pythonpath(tmp_path):
    import sys
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run([sys.executable, str(SCRIPT_PATH), "--help"], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "--period-label" in result.stdout


@pytest.mark.parametrize("transport", [TimeoutError("request timeout"), URLError("network unavailable")])
def test_fetch_transport_failure_is_a_scoped_source_result(monkeypatch, capsys, transport):
    module = load_module()
    monkeypatch.setattr(module, "_get_headless_headers", lambda: {})
    def failed(*args):
        raise transport
    monkeypatch.setattr(module.browser.direct, "fetch_with_headers", failed)
    assert module._fetch_user_okr(user_id="person", period_label="2026 Q4") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["failure"]["scope"] == "member"
    assert result["failure"]["code"] == "okr_source_transport_failed"


@pytest.mark.parametrize("detail", [
    '{"accessToken":"short-secret","reason":"permission denied"}',
    'HTTP body {"accessToken":"short-secret","reason":"permission denied"}',
    'HTTP body {"accessToken":"short-secret",',
    '{"error":"{\\"accessToken\\":\\"short-secret\\",\\"reason\\":\\"denied\\"}"}',
])
def test_json_credentials_cannot_enter_source_failure_details(detail):
    module = load_module()
    result = module._source_failure(RuntimeError(detail), user_id="person", period_label="2026 Q4",
                                    scope="shared", code="failed")
    assert "short-secret" not in result["failure"]["detail"]


def test_shared_source_respects_configured_skills_root(monkeypatch, tmp_path):
    root = SCRIPT_PATH.parents[1] / "ci" / "shared-skills"
    monkeypatch.setenv("CEO_SKILLS_ROOT", str(root))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    module = load_module()

    assert module.SCRIPT_DIR == root / "dingtang-okr-review" / "scripts"
    assert Path(module.browser.__file__).is_relative_to(root)


def test_service_entrypoint_disables_visible_browser_fallback():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "_capture_stable_headless_headers" in source
    assert "launch_service_chrome" in source
    assert "headless" in source.casefold()
    assert "_headless_cdp_command" not in source
    assert "connect_over_cdp" not in source


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


def test_refresh_lock_uses_system_temporary_directory(monkeypatch, tmp_path):
    import tempfile

    module = load_module()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    with module._headless_browser_lock():
        assert (tmp_path / "ceo-okr-headless.lock").is_file()


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


def test_service_browser_uses_shared_launcher_and_closes_context(monkeypatch, tmp_path):
    module = load_module()
    calls = []

    class Context:
        def close(self):
            calls.append("close")

    monkeypatch.setattr(module.browser, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(
        "app.service_browser.launch_service_chrome",
        lambda playwright, profile_dir: calls.append((playwright, profile_dir)) or Context(),
    )

    with module._service_browser("playwright"):
        pass

    assert calls == [("playwright", tmp_path), "close"]


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

        def filter(self, **kwargs):
            calls.append(("filter", kwargs))
            return self

        def click(self, **kwargs):
            calls.append(("click_avatar", kwargs))

    class Page:
        url = "https://login.dingtalk.com/oauth2/challenge.htm"

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
        ("locator", module.LOCAL_SSO_QR_TAB),
        ("filter", {"has_text": "QR Code"}),
        ("wait_for", {"state": "visible", "timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("click_avatar", {"timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("locator", module.LOCAL_SSO_ACCOUNT_AVATAR),
        ("wait_for", {"state": "visible", "timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("click_avatar", {"timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("wait", module.LOCAL_SSO_DIALOG_DELAY_MS),
        "confirm",
        ("submit", 2),
    ]


def test_existing_local_account_uses_direct_submission_without_qr(monkeypatch):
    module = load_module()
    calls = []

    class Page:
        url = "https://login.dingtalk.com/oauth2/challenge.htm"

        def wait_for_timeout(self, milliseconds):
            calls.append(("wait", milliseconds))

    monkeypatch.setattr(
        module,
        "_submit_local_dingtalk_account",
        lambda page: calls.append(("submit", page.url)),
    )
    monkeypatch.setattr(
        module,
        "_confirm_local_dingtalk_login",
        lambda: calls.append("confirm"),
    )

    assert module._attempt_local_dingtalk_sso(Page()) is True
    assert calls == [("submit", Page.url)]


def test_local_account_submission_selects_visible_org_without_native_prompt(monkeypatch):
    module = load_module()
    calls = []

    class Locator:
        @property
        def first(self):
            return self

        def filter(self, **_kwargs):
            return self

        def is_visible(self):
            return True

        def wait_for(self, **_kwargs):
            pass

        def click(self, **_kwargs):
            calls.append("click")

    class Page:
        url = "https://login.dingtalk.com/oauth2/challenge.htm"

        def locator(self, _selector):
            return Locator()

        def wait_for_timeout(self, _milliseconds):
            pass

    def unexpected_confirmation():
        raise AssertionError("visible organization chooser needs no native prompt")

    monkeypatch.setattr(module, "_confirm_local_dingtalk_login", unexpected_confirmation)
    module._submit_local_dingtalk_account(Page())
    assert calls == ["click", "click"]


def test_local_account_submission_does_not_hide_failed_org_selection(monkeypatch):
    module = load_module()

    class Locator:
        def __init__(self, selector):
            self.selector = selector

        @property
        def first(self):
            return self

        def filter(self, **_kwargs):
            return self

        def is_visible(self):
            return False

        def wait_for(self, **_kwargs):
            if self.selector == module.LOCAL_SSO_CORP_ITEM:
                raise RuntimeError("target organization is unavailable")

        def click(self, **_kwargs):
            pass

    class Page:
        url = "https://login.dingtalk.com/oauth2/challenge.htm"

        def locator(self, selector):
            return Locator(selector)

        def wait_for_timeout(self, _milliseconds):
            pass

    monkeypatch.setattr(module, "_confirm_local_dingtalk_login", lambda: None)
    with pytest.raises(RuntimeError, match="target organization is unavailable"):
        module._submit_local_dingtalk_account(Page())


def test_local_account_submission_confirms_native_prompt_before_selecting_org(monkeypatch):
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

        def is_visible(self):
            return False

        def click(self, **kwargs):
            calls.append(("click", kwargs))

    class Page:
        url = "https://login.dingtalk.com/oauth2/challenge.htm"

        def locator(self, selector):
            calls.append(("locator", selector))
            return Locator()

        def wait_for_timeout(self, milliseconds):
            calls.append(("wait", milliseconds))

    monkeypatch.setattr(
        module, "_confirm_local_dingtalk_login", lambda: calls.append("confirm")
    )

    module._submit_local_dingtalk_account(Page())

    assert calls.index("confirm") < len(calls) - 1
    assert calls[-1] == ("click", {"timeout": module.LOCAL_SSO_TIMEOUT_MS})


def test_local_account_submission_skips_native_confirmation_after_redirect(monkeypatch):
    module = load_module()
    calls = []

    class Locator:
        @property
        def first(self):
            return self

        def filter(self, **_kwargs):
            return self

        def wait_for(self, **_kwargs):
            return None

        def click(self, **_kwargs):
            return None

    class Page:
        url = "https://login.dingtalk.com/oauth2/challenge.htm"

        def locator(self, _selector):
            return Locator()

        def wait_for_timeout(self, _milliseconds):
            self.url = "https://dingokr.dingteam.com/web/okr/pc/index.html"

    monkeypatch.setattr(
        module, "_confirm_local_dingtalk_login", lambda: calls.append("confirm")
    )

    module._submit_local_dingtalk_account(Page())

    assert calls == []


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
        url = "https://dingokr.dingteam.com/web/okr/pc/index.html"

        def locator(self, selector):
            calls.append(("locator", selector))
            return Locator()

        def wait_for_timeout(self, milliseconds):
            calls.append(("wait", milliseconds))

    module._submit_local_dingtalk_account(Page())

    assert calls == [
        ("locator", module.LOCAL_SSO_DIRECT_BUTTON),
        ("wait_for", {"state": "visible", "timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("click", {"timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("wait", module.LOCAL_SSO_DIALOG_DELAY_MS),
        ("locator", module.LOCAL_SSO_CORP_ITEM),
        ("filter", {"has_text": module.LOCAL_SSO_CORP_NAME}),
        ("wait_for", {"state": "visible", "timeout": module.LOCAL_SSO_TIMEOUT_MS}),
        ("click", {"timeout": module.LOCAL_SSO_TIMEOUT_MS}),
    ]


@pytest.mark.parametrize(
    "redirected_url, accepted",
    [
        ("https://dingokr.dingteam.com/web/okr/pc/index.html#/okr/personal", True),
        ("about:blank", False),
        ("https://dingokr.dingteam.com/unrelated", False),
        ("https://example.com/web/okr/pc/index.html", False),
    ],
)
def test_local_account_submission_requires_business_page_when_org_is_absent(
    monkeypatch, redirected_url, accepted
):
    module = load_module()

    class Locator:
        @property
        def first(self):
            return self

        def filter(self, **_kwargs):
            return self

        def wait_for(self, **_kwargs):
            if page.url == redirected_url:
                raise RuntimeError("organization is absent after redirect")

        def click(self, **_kwargs):
            pass

    class Page:
        url = "https://login.dingtalk.com/oauth2/challenge.htm"

        def locator(self, _selector):
            return Locator()

        def wait_for_timeout(self, _milliseconds):
            self.url = redirected_url

    page = Page()
    monkeypatch.setattr(
        module, "_confirm_local_dingtalk_login",
        lambda: pytest.fail("redirected page must not trigger a native confirmation"),
    )
    if accepted:
        module._submit_local_dingtalk_account(page)
    else:
        with pytest.raises(RuntimeError, match="organization is absent"):
            module._submit_local_dingtalk_account(page)
        page.url = "https://login.dingtalk.com/oauth2/challenge.htm"
        assert module._attempt_local_dingtalk_sso(page) is False


def test_local_sso_selectors_are_scoped_to_the_current_login_page():
    module = load_module()

    assert module.LOCAL_SSO_DIRECT_BUTTON.startswith(".app-page.app-page-curr ")
    assert module.LOCAL_SSO_ACCOUNT_AVATAR.startswith(".app-page.app-page-curr ")
    assert module.LOCAL_SSO_CORP_ITEM.startswith(".app-page.app-page-curr ")
    assert module.LOCAL_SSO_QR_TAB.startswith(".flex-box-tab-content ")


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

    @contextmanager
    def service_browser(_playwright):
        yield context

    @contextmanager
    def playwright():
        yield object()

    monkeypatch.setattr(module.browser, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(module.browser, "_jwt_exp", lambda _headers: int(time.time()) + 3600)
    monkeypatch.setattr(module, "_service_browser", service_browser)
    monkeypatch.setattr(module, "sync_playwright", playwright)

    assert module._capture_stable_headless_headers() == {
        "Authorization": "Bearer refreshed"
    }
    assert closed == [True]


@pytest.mark.parametrize(
    "initial_kind", ["expired", "appid", "skew", "boundary", "malformed", "missing_exp"]
)
def test_header_refresh_waits_for_valid_auth_after_early_request(
    monkeypatch, tmp_path, initial_kind
):
    module = load_module()
    now = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: now)

    def token(payload):
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        return f"Bearer test.{encoded}.signature"

    initial_headers = {
        "expired": {"authorization": token({"exp": now - 1})},
        "appid": {"x-dingteam-auth-app-id": "40707"},
        "skew": {"authorization": token({"exp": now + module.browser.TOKEN_SKEW_SECONDS - 1})},
        "boundary": {"authorization": token({"exp": now + module.browser.TOKEN_SKEW_SECONDS})},
        "malformed": {"authorization": "Bearer invalid"},
        "missing_exp": {"authorization": token({})},
    }[initial_kind]
    refreshed = token({"exp": now + 3600})

    class Request:
        url = "https://dingokr.dingteam.com/data/okr/person/period/list"

        def __init__(self, headers):
            self.headers = headers

    class Page:
        url = "https://dingokr.dingteam.com/web/okr/pc/index.html"

        def goto(self, *_args, **_kwargs):
            context.handler(Request(initial_headers))

        def wait_for_timeout(self, _milliseconds):
            pass

        def evaluate(self, _script):
            if _script == module.OKR_REQUEST_NUDGE:
                context.handler(Request({"authorization": refreshed}))
            if "root:" in _script:
                return {"root": True, "mounted": True}
            return None

    class Context:
        def on(self, _event, handler):
            self.handler = handler

        def new_page(self):
            page = Page()
            self.pages = [page]
            return page

        def close(self):
            pass

    context = Context()

    @contextmanager
    def service_browser(_playwright):
        yield context

    @contextmanager
    def playwright():
        yield object()

    monkeypatch.setattr(module.browser, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(module, "HEADLESS_REFRESH_SECONDS", 1)
    monkeypatch.setattr(module, "_service_browser", service_browser)
    monkeypatch.setattr(module, "sync_playwright", playwright)

    assert module._capture_stable_headless_headers() == {
        "Authorization": refreshed
    }


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


def test_bounded_source_kills_its_entire_worker_group_on_timeout(monkeypatch):
    module = load_module()
    command = ["python", "source.py", "--worker"]
    signals = []

    class TimedOutWorker:
        pid = 43210
        returncode = None

        def communicate(self, *, timeout):
            assert timeout == module.HEADLESS_SOURCE_TIMEOUT_SECONDS
            raise subprocess.TimeoutExpired(command, timeout)

        def wait(self, *, timeout=None):
            if timeout == module.HEADLESS_SOURCE_SHUTDOWN_SECONDS:
                raise subprocess.TimeoutExpired(command, timeout)
            self.returncode = -signal.SIGKILL
            return self.returncode

    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda actual, **kwargs: (
            signals.append(("spawn", actual, kwargs)) or TimedOutWorker()
        ),
    )
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pid, sent_signal: signals.append(("signal", pid, sent_signal)),
    )

    with pytest.raises(RuntimeError, match="okr_headless_source_timeout"):
        module._run_bounded_source(command)

    assert signals[0][0] == "spawn"
    assert signals[0][2]["start_new_session"] is True
    assert signals[1:] == [
        ("signal", 43210, signal.SIGTERM),
        ("signal", 43210, signal.SIGKILL),
    ]


def test_bounded_source_surfaces_worker_failure_as_explicit_error(monkeypatch):
    module = load_module()
    signals = []

    class FailedWorker:
        pid = 43211
        returncode = 1

        def communicate(self, *, timeout):
            return "", "okr_website_unavailable: did not render\n"

    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: FailedWorker())
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pid, sent_signal: signals.append((pid, sent_signal)),
    )

    with pytest.raises(
        RuntimeError,
        match="okr_headless_source_failed: okr_website_unavailable: did not render",
    ):
        module._run_bounded_source(["python", "source.py", "--worker"])

    assert signals == [(43211, signal.SIGKILL)]
