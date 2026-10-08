import importlib.util
import json
from pathlib import Path

import pytest


def bundled_headless(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("CEO_SKILLS_ROOT", str(root / "ci/shared-skills"))
    spec = importlib.util.spec_from_file_location("typed_headless", root / "scripts/dingteam_okr_headless_source.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_get_headless_headers", lambda: {})
    return module


def test_real_bundled_type_reaches_headless_absence_receipt(monkeypatch, capsys):
    module = bundled_headless(monkeypatch)
    periods = [{"name": "2026 Q3", "okrId": "q3"}]
    monkeypatch.setattr(module.browser.direct, "_post", lambda *args: {"code": 0, "data": {"list": periods}})
    assert module._fetch_user_okr(user_id="member", period_label="2026 Q4") == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["userId"] == "member"
    assert receipt["periods"] == periods
    assert receipt["availability"]["status"] == "goals_not_established"
    assert "processed" not in receipt


@pytest.mark.parametrize("payload", [
    {"code": 401, "data": {"list": []}},
    {"code": 0, "data": None},
    {"code": 0, "data": {"list": [], "hasMore": True}},
    {"code": 0, "data": {"list": ["invalid"]}},
])
def test_real_bundled_provider_errors_never_emit_missing_goals(monkeypatch, capsys, payload):
    module = bundled_headless(monkeypatch)
    monkeypatch.setattr(module.browser.direct, "_post", lambda *args: payload)
    assert module._fetch_user_okr(user_id="member", period_label="2026 Q4") == 0
    result = json.loads(capsys.readouterr().out)
    assert "failure" in result
    assert "availability" not in result
    assert "processed" not in result


@pytest.mark.parametrize("objective_response", [
    {"code": 401, "data": None},
    {"code": 0, "data": {"list": [], "pageNo": 1, "totalPages": 1, "totalCount": 1}},
])
def test_objective_errors_do_not_become_empty_current_period(monkeypatch, capsys, objective_response):
    module = bundled_headless(monkeypatch)
    def post(path, *args):
        if path.endswith("/person/period/list"):
            return {"code": 0, "data": {"list": [{"name": "2026 Q4", "okrId": "q4"}]}}
        return objective_response
    monkeypatch.setattr(module.browser.direct, "_post", post)
    assert module._fetch_user_okr(user_id="member", period_label="2026 Q4") == 0
    result = json.loads(capsys.readouterr().out)
    assert "failure" in result
    assert "processed" not in result


def test_empty_current_period_has_verified_objective_receipt(monkeypatch, capsys):
    module = bundled_headless(monkeypatch)
    def post(path, *args):
        if path.endswith("/person/period/list"):
            return {"code": 0, "data": {"list": [{"name": "2026 Q4", "okrId": "q4"}]}}
        return {"code": 0, "data": {"list": [], "pageNo": 1, "totalPages": 0, "totalCount": 0}}
    monkeypatch.setattr(module.browser.direct, "_post", post)
    assert module._fetch_user_okr(user_id="member", period_label="2026 Q4") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["source"]["objectiveListReceipt"] == {"providerCode": 0, "complete": True, "count": 0}
