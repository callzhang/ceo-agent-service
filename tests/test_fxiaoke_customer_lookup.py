from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from app.fxiaoke_customer_lookup import lookup_account_customers


@pytest.fixture(autouse=True)
def _resolve_fake_executable(monkeypatch):
    monkeypatch.setattr("app.fxiaoke_customer_lookup.shutil.which", lambda binary: binary)


def _completed(candidates, *, status="RESOLVED", returncode=0):
    return SimpleNamespace(
        returncode=returncode,
        stdout=json.dumps({"resolution_status": status, "record_candidates": candidates}),
        stderr="private provider diagnostic",
    )


def _candidate(record_id="crm-1", name="甲客户有限公司", object_api_name="AccountObj"):
    return {
        "record_id": record_id,
        "matched_name": name,
        "object_api_name": object_api_name,
        "object_name": "CRM customer object",
    }


def test_single_resolved_customer_remains_a_candidate_for_human_confirmation():
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return _completed([_candidate()])

    result = lookup_account_customers("  甲客户  一期 ", runner=run)

    assert result.status == "matched"
    assert result.candidates[0].customer_id == "crm-1"
    assert result.candidates[0].name == "甲客户有限公司"
    assert result.candidates[0].matched_fields == ("name_resolution",)
    assert seen["command"] == [
        "sharecrm", "data", "record", "query-by-name", "--name", "甲客户 一期",
        "--object_api_names", "AccountObj",
    ]
    assert seen["kwargs"]["timeout"] > 0
    assert "create" not in seen["command"] and "update" not in seen["command"]


def test_multiple_resolved_customers_remain_ambiguous():
    result = lookup_account_customers(
        "甲客户",
        runner=lambda *_args, **_kwargs: _completed(
            [_candidate("crm-1"), _candidate("crm-2", "甲客户交付有限公司")],
            status="NEEDS_CONFIRMATION",
        ),
    )

    assert result.status == "ambiguous"
    assert {candidate.customer_id for candidate in result.candidates} == {"crm-1", "crm-2"}


def test_no_match_is_distinct_from_unavailable_or_unknown_empty_resolution():
    empty = lookup_account_customers(
        "不存在客户", runner=lambda *_args, **_kwargs: _completed([], status="NO_MATCH")
    )
    unknown = lookup_account_customers(
        "甲客户", runner=lambda *_args, **_kwargs: _completed([], status="PENDING")
    )

    def failed(*_args, **_kwargs):
        return _completed([], status="", returncode=1)

    unavailable = lookup_account_customers("甲客户", runner=failed)
    assert empty.status == "no_match"
    assert unknown.status == "unavailable"
    assert unavailable.status == "unavailable"
    assert unavailable.error_code == "command_failed"
    assert "private provider diagnostic" not in unavailable.error_code


def test_cli_missing_timeout_and_malformed_result_are_unavailable(monkeypatch):
    monkeypatch.setattr("app.fxiaoke_customer_lookup.shutil.which", lambda _binary: None)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("sharecrm")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("sharecrm", 10)

    def malformed(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="not-json", stderr="")

    assert lookup_account_customers("甲客户", runner=missing).error_code == "cli_missing"
    monkeypatch.setattr("app.fxiaoke_customer_lookup.shutil.which", lambda binary: binary)
    assert lookup_account_customers("甲客户", runner=timeout).error_code == "timeout"
    assert lookup_account_customers("甲客户", runner=malformed).error_code == "invalid_response"


def test_unknown_object_candidates_are_not_project_customer_candidates():
    result = lookup_account_customers(
        "甲客户",
        runner=lambda *_args, **_kwargs: _completed([_candidate(object_api_name="ContactObj")]),
    )

    assert result.status == "unavailable"
    assert result.candidates == ()
    assert result.error_code == "no_account_candidate"


def test_unbounded_candidate_list_is_rejected_without_exposing_candidates():
    rows = [_candidate(f"crm-{index}") for index in range(51)]
    result = lookup_account_customers(
        "甲客户", runner=lambda *_args, **_kwargs: _completed(rows)
    )

    assert result.status == "unavailable"
    assert result.candidates == ()
    assert result.error_code == "too_many_candidates"
