import importlib.util
import json
from pathlib import Path


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
    assert "webpackChunkallinone" in script
    assert "api.objective.log.progressHistory" in script
    assert "api.objective.findCommentListV2" in script
    assert "mergeUpdates(aggregateHistory(histories), aggregateComments(krComments))" in script
    assert "progressUpdatesAggregated: aggregated" in script
    assert "krDetailsUpdatesAggregated: aggregated" in script
    assert "cookie" not in script.casefold()
    assert "localstorage" not in script.casefold()
    assert "sessionstorage" not in script.casefold()
    assert ".catch(" not in script
    assert "try {" not in script


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
    assert 'contains "dingokr.dingteam.com"' not in module.APPLESCRIPT
    assert 'contains "dingokr.dingteam.com"' not in module.OPEN_TAB_APPLESCRIPT


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


def test_default_timeout_allows_slow_dingteam_page():
    module = load_module()

    assert module.DEFAULT_TIMEOUT_SECONDS == 90.0
