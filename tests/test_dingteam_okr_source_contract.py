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
