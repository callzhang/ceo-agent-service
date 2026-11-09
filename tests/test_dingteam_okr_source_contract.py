"""The service keeps its source outcome contract across the HTTP transport."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def source(tmp_path, monkeypatch):
    scripts = tmp_path / "dingtang-okr-review" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "dingteam_okr_browser_source.py").write_text(
        "from types import SimpleNamespace\n"
        "class MissingOkrPeriod(Exception): pass\n"
        "direct=SimpleNamespace(MissingOkrPeriod=MissingOkrPeriod)\n"
    )
    monkeypatch.setenv("CEO_SKILLS_ROOT", str(tmp_path))
    script = Path(__file__).resolve().parents[1] / "scripts" / "dingteam_okr_headless_source.py"
    spec = importlib.util.spec_from_file_location("okr_service_contract", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_get_headless_headers", lambda: {})
    return module


def run_failure(source, capsys, cause):
    def fetch(*args):
        raise RuntimeError("provider source read failed") from cause
    source.browser.direct.fetch_with_headers = fetch
    assert source._fetch_user_okr(user_id="person", period_label="2026 Q4") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["userId"] == "person"
    assert result["periodLabel"] == "2026 Q4"
    return result["failure"]


@pytest.mark.parametrize("status,scope", [(401, "shared"), (403, "member"), (429, "member"), (500, "member")])
def test_native_http_status_keeps_existing_scope_and_provider_code(source, capsys, status, scope):
    request = httpx.Request("POST", "https://example.test/source")
    response = httpx.Response(status, request=request)
    cause = httpx.HTTPStatusError("synthetic provider failure", request=request, response=response)
    failure = run_failure(source, capsys, cause)
    assert failure["scope"] == scope
    assert failure["code"] == f"okr_source_http_{status}"


@pytest.mark.parametrize("cause_type", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError])
def test_native_transport_failure_keeps_member_read_failure(source, capsys, cause_type):
    failure = run_failure(source, capsys, cause_type("synthetic transport failure"))
    assert failure["scope"] == "member"
    assert failure["code"] == "okr_source_transport_failed"


def test_actual_invalid_json_keeps_invalid_response(source, capsys):
    def fetch(*args):
        raise ValueError("invalid JSON")
    source.browser.direct.fetch_with_headers = fetch
    assert source._fetch_user_okr(user_id="person", period_label="2026 Q4") == 0
    failure = json.loads(capsys.readouterr().out)["failure"]
    assert failure["scope"] == "member"
    assert failure["code"] == "okr_source_invalid_response"


def test_source_worker_budget_is_unchanged(source):
    assert source.HEADLESS_SOURCE_TIMEOUT_SECONDS == 220
    assert source.HEADLESS_SOURCE_SHUTDOWN_SECONDS == 5


def test_okr_browser_preserves_existing_session_through_shared_launcher(source, tmp_path, monkeypatch):
    from types import SimpleNamespace

    profile = tmp_path / "okr-session"
    monkeypatch.setattr(source.browser, "PROFILE_DIR", profile, raising=False)
    calls = []
    closed = []
    context = SimpleNamespace(close=lambda: closed.append(True))
    playwright = object()

    def launch(actual_playwright, actual_profile, **kwargs):
        calls.append((actual_playwright, actual_profile, kwargs))
        return context

    monkeypatch.setattr("app.service_browser.launch_service_chrome", launch)
    with source._service_browser(playwright) as opened:
        assert opened is context

    assert calls == [(playwright, profile, {"initialize_from_snapshot": False})]
    assert closed == [True]


@pytest.fixture(scope="module")
def sso_chrome():
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Error:
            pytest.skip("Chrome is unavailable for the local SSO DOM regression")
        try:
            yield browser
        finally:
            browser.close()


def sso_pages(body):
    """Old screens remain visible underneath the active screen, as in native SSO."""
    return (
        "<style>.app-page{position:absolute;inset:0;background:white}"
        ".app-page-curr{z-index:2}</style><output id='events'></output>"
        + "<div class='app-page'>" + body.replace("current", "old") + "</div>"
        + "<div class='app-page app-page-curr'>" + body + "</div>"
    )


def test_direct_sso_ignores_earlier_button_on_old_page(source, sso_chrome, monkeypatch):
    monkeypatch.setattr(source.browser, "ENTRY_URL", "about:blank", raising=False)
    monkeypatch.setattr(source, "LOCAL_SSO_TIMEOUT_MS", 500)
    monkeypatch.setattr(source, "LOCAL_SSO_DIALOG_DELAY_MS", 0)
    page = sso_chrome.new_page()
    try:
        page.set_content(sso_pages(
            "<div class='module-confirm'><div class='module-confirm-button "
            "base-comp-button-type-primary' "
            "onclick=\"document.querySelector('#events').textContent+='current,'\">"
            "Log In</div></div>"
        ))

        source._submit_local_dingtalk_account(page)

        assert page.locator("#events").text_content() == "current,"
    finally:
        page.close()


def test_qr_sso_uses_active_tab_avatar_and_organization(source, sso_chrome, monkeypatch):
    monkeypatch.setattr(source, "LOCAL_SSO_TIMEOUT_MS", 500)
    monkeypatch.setattr(source, "LOCAL_SSO_DIALOG_DELAY_MS", 0)
    monkeypatch.setattr(source, "LOCAL_SSO_CORP_NAME", "Synthetic Corp")
    confirmations = []
    monkeypatch.setattr(source, "_confirm_local_dingtalk_login", lambda: confirmations.append(True))
    page = sso_chrome.new_page()
    page.route("https://login.dingtalk.com/oauth2/test", lambda route: route.fulfill(
        body=sso_pages(
            "<div class='flex-box-tab-content'><button role='tab' "
            "onclick=\"document.querySelector('#events').textContent+='current-tab,'\">"
            "扫码登录</button></div>"
            "<button class='module-qrcode-user-avatar' "
            "onclick=\"document.querySelector('#events').textContent+='current-avatar,'\">Account</button>"
            "<button class='module-corp-sel-listitem' "
            "onclick=\"document.querySelector('#events').textContent+='current-corp,'\">Synthetic Corp</button>"
        ), content_type="text/html; charset=utf-8",
    ))
    try:
        page.goto("https://login.dingtalk.com/oauth2/test")

        assert source._attempt_local_dingtalk_sso(page) is True

        assert page.locator("#events").text_content() == "current-tab,current-avatar,current-corp,"
        assert confirmations == [True]
    finally:
        page.close()
