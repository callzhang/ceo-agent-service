import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dingteam_okr_live_source.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dingteam_okr_live_source", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_page_script_uses_page_api_without_browser_storage_access():
    module = load_module()

    script = module._build_page_script(
        user_id="user-1",
        period_label="2026 Q2",
        result_attribute="data-result",
    )

    assert "user-1" in script
    assert "2026 Q2" in script
    assert "data-result" in script
    assert "postJson('/data/okr/person/period/list'" in script
    assert "postJson('/data/okr/objective/showListView/v2'" in script
    assert "postJson('/data/okr/objective/findKrDetail'" in script
    assert "postJson('/data/okr/objective/log/progressHistory'" in script
    assert "postJson('/data/okr/objective/findCommentList/v2'" in script
    assert "credentials: 'include'" in script
    assert "Dingteam OKR API error" in script
    assert "mergeUpdates(aggregateHistory(histories), aggregateComments(krComments))" in script
    assert "progressUpdatesAggregated: aggregated" in script
    assert "krDetailsUpdatesAggregated: aggregated" in script
    assert "cookie" not in script.casefold()
    assert "localstorage" not in script.casefold()
    assert "sessionstorage" not in script.casefold()
    assert "webpackChunkallinone" not in script
    assert ".catch(" not in script
    assert "} catch (error) {" in script


def test_module_uses_chrome_cookie_persistence_without_copying_cookie_files():
    source = SCRIPT_PATH.read_text()

    assert "Chrome persists the login cookies" in source
    assert "not export or copy browser cookies" in source
    assert "Cookies" not in source
    assert "Local Storage" not in source


def test_injected_script_does_not_inline_page_source():
    module = load_module()

    injected = module._inject_script("window.__dingteamTest = '叮当OKR';")

    assert "sourceBase64" in injected
    assert "TextDecoder" in injected
    assert "叮当OKR" not in injected


def test_chrome_tab_matching_requires_real_dingteam_page():
    module = load_module()

    assert 'starts with "https://dingokr.dingteam.com/"' in module.APPLESCRIPT
    assert 'starts with "https://dingokr.dingteam.com/"' in module.OPEN_TAB_APPLESCRIPT
    assert 'starts with "https://dingokr.dingteam.com/"' in module.FOCUS_DINGTEAM_TAB_APPLESCRIPT
    assert 'contains "dingokr.dingteam.com"' not in module.APPLESCRIPT
    assert 'contains "dingokr.dingteam.com"' not in module.OPEN_TAB_APPLESCRIPT
    assert 'contains "dingokr.dingteam.com"' not in module.FOCUS_DINGTEAM_TAB_APPLESCRIPT


def test_format_page_error_uses_page_error_message():
    module = load_module()

    detail = module._format_page_error(
        {
            "ok": False,
            "error": "Cannot read properties of undefined (reading 'Z')",
            "stack": "TypeError: Cannot read properties of undefined",
        }
    )

    assert detail == (
        "Dingteam OKR page script failed: "
        "Cannot read properties of undefined (reading 'Z')"
    )


def test_print_cli_error_writes_safe_json(capsys):
    module = load_module()

    module._print_cli_error(RuntimeError("Dingteam OKR page script failed: api missing"))

    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "message": "Dingteam OKR live source failed",
        "reason": "Dingteam OKR page script failed: api missing",
    }


def test_dingteam_login_prompt_focuses_tab_and_notifies(monkeypatch):
    module = load_module()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module._prompt_dingteam_login()

    assert calls[0][0][:3] == ["osascript", "-e", module.FOCUS_DINGTEAM_TAB_APPLESCRIPT]
    assert calls[0][0][3] == module.DINGTEAM_URL
    assert calls[0][1]["capture_output"] is True
    assert "display notification" in calls[1][0][2]
    assert "CEO DingTeam login required" in calls[1][0][2]


def test_dingteam_login_error_detection_requires_api_103():
    module = load_module()

    assert module._is_dingteam_login_error(
        "Dingteam OKR page script failed: Dingteam OKR API error 103: 未登录"
    )
    assert not module._is_dingteam_login_error(
        "Dingteam OKR page script failed: Dingteam OKR API error 500: 未登录"
    )


def test_default_timeout_allows_slow_dingteam_page():
    module = load_module()

    assert module.DEFAULT_TIMEOUT_SECONDS == 90.0


def test_ready_probe_accepts_chunk_on_global_root():
    source = SCRIPT_PATH.read_text()

    assert "document.readyState==='complete'?'ready':'loading'" in source
    assert "webpackChunkallinone?'ready':'loading'" not in source
