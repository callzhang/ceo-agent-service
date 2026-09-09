from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-minutes-sync" / "SKILL.md"
SCENARIOS_PATH = ROOT / "tests" / "fixtures" / "ceo_minutes_sync_scenarios.json"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _fixture() -> dict[str, Any]:
    fixture = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    assert fixture["schema"] == "ceo_minutes_sync_skill_scenarios.v2"
    return fixture


def _path(value: object, dotted_path: str) -> object:
    current = value
    for segment in dotted_path.split("."):
        assert isinstance(current, dict), dotted_path
        current = current[segment]
    return current


def _canonical_sha(value: object) -> str:
    archive = _fixture()["archive_json"]
    assert archive == {
        "encoding": "UTF-8",
        "ensure_ascii": False,
        "sort_keys": True,
        "separators": [",", ":"],
        "trailing_newline": False,
    }
    serialized = json.dumps(
        value,
        ensure_ascii=archive["ensure_ascii"],
        sort_keys=archive["sort_keys"],
        separators=tuple(archive["separators"]),
    )
    return hashlib.sha256(serialized.encode(archive["encoding"])).hexdigest()


def _artifact_hashes(scenario: dict[str, Any]) -> dict[str, str]:
    contract = _fixture()["recorded_contract"]
    return {
        "basic": _canonical_sha(_path(scenario["detail"], contract["basic"])),
        "summary": _canonical_sha(_path(scenario["detail"], contract["summary"])),
        "transcript": _canonical_sha(
            _path(scenario["transcript"], contract["transcript"])
        ),
    }


def _permission_denied(detail: dict[str, Any]) -> bool:
    failures = detail.get("failures", ())
    return detail.get("complete") is False and any(
        "permission denied" in str(failure.get("error", "")).casefold()
        for failure in failures
        if isinstance(failure, dict)
    )


def _restricted_relevance(list_item: dict[str, Any]) -> str:
    title = str(list_item.get("title", "")).casefold()
    owner = str(list_item.get("owner", "")).casefold()
    if "ceo" in title or owner == "cfo":
        return "relevant"
    if "cafeteria" in title or owner == "facilities":
        return "out_of_scope"
    return "unknown"


def _evaluate(scenario: dict[str, Any]) -> dict[str, object]:
    contract = _fixture()["recorded_contract"]
    assert _path(scenario["list"], contract["list_ok"]) is True
    assert _path(scenario["list"], contract["list_complete"]) is True
    assert _path(scenario["list"], contract["list_pagination_complete"]) is True
    items = _path(scenario["list"], contract["list_items"])
    assert isinstance(items, list)
    prior = scenario.get("prior")
    if items:
        item = items[0]
    else:
        assert prior and prior["status"] == "permission_pending"
        item = {"taskUuid": prior["taskUuid"]}

    detail = scenario["detail"]
    if _permission_denied(detail):
        relevance = _restricted_relevance(item)
        authorization = scenario.get("authorization", {})
        if relevance == "out_of_scope":
            return _result("skipped", True, False, "out_of_scope_metadata")
        if relevance == "relevant" and authorization.get("content_needed"):
            requested = authorization.get("request_access") is True
            return _result(
                "permission_pending",
                False,
                requested,
                "request_pending" if requested else "needs_authorization",
            )
        return _result("permission_pending", False, False, "needs_review")

    if (
        _path(detail, contract["detail_complete"]) is not True
        or detail.get("failureCount") != 0
        or str(_path(detail, contract["basic_success"])).casefold() != "true"
        or str(_path(detail, contract["summary_success"])).casefold() != "true"
        or not isinstance(_path(detail, contract["basic"]), dict)
        or not isinstance(_path(detail, contract["summary"]), dict)
    ):
        return _result("failed", False, False, "freshness_unknown")

    transcript = scenario["transcript"]
    if (
        transcript.get("ok") is not True
        or _path(transcript, contract["transcript_complete"]) is not True
        or _path(transcript, contract["transcript_pagination_complete"]) is not True
    ):
        return _result("failed", False, False, "freshness_unknown")

    current_hashes = _artifact_hashes(scenario)
    if isinstance(prior, dict) and prior.get("artifact_sha256") == current_hashes:
        return _result("skipped", True, False, "unchanged_fetched")
    evidence = "permission_recovered" if prior else "fresh_fetch"
    return _result("synced", True, False, evidence)


def _result(
    outcome: str, cursor_advance: bool, access_request: bool, evidence: str
) -> dict[str, object]:
    return {
        "outcome": outcome,
        "cursor_advance": cursor_advance,
        "access_request": access_request,
        "evidence": evidence,
    }


def test_minutes_sync_skill_frontmatter_is_metadata_not_schedule() -> None:
    text = _skill_text()
    frontmatter = yaml.safe_load(text.split("---\n", 2)[1])

    assert set(frontmatter) == {"name", "description", "metadata"}
    assert frontmatter["name"] == "ceo-minutes-sync"
    assert frontmatter["description"].startswith("Use when ")
    assert frontmatter["metadata"] == {"managed_by": "ceo-agent-service"}


@pytest.mark.parametrize(
    "case",
    (
        "complete_new",
        "complete_unchanged",
        "transcript_incomplete",
        "complete_without_remote_version",
        "permission_recovered",
        "restricted_relevant_authorized",
        "restricted_out_of_scope",
        "restricted_relevance_unknown",
    ),
)
def test_recorded_dws_scenario_matches_executable_reference_contract(case: str) -> None:
    scenario = next(item for item in _fixture()["scenarios"] if item["case"] == case)

    assert _evaluate(scenario) == scenario["expected"]


def test_real_contract_paths_and_success_conditions_are_documented() -> None:
    text = _skill_text()

    for path in (
        "`data.minutes`",
        "`basic.result`",
        "`summary.result`",
        "`data.paragraphList`",
        "`data.complete=true`",
        "`meta.pagination.endpoint_exhausted=true`",
    ):
        assert path in text
    assert "detail is the top-level envelope" in text
    assert "failureCount=0" in text
    assert "permission failures enter the restricted-item decision first" in text
    assert "source_version" in text and "optional" in text


def test_reference_ledger_is_mutually_exclusive_and_conservative() -> None:
    results = [_evaluate(scenario) for scenario in _fixture()["scenarios"]]
    counts = Counter(result["outcome"] for result in results)

    assert counts == {"synced": 3, "skipped": 2, "permission_pending": 2, "failed": 1}
    assert sum(counts.values()) == len(results)
    assert sum(result["access_request"] is True for result in results) == 1
    assert all(set(result) == {"outcome", "cursor_advance", "access_request", "evidence"} for result in results)


def test_no_version_fields_still_produce_honest_fetch_evidence() -> None:
    scenario = next(
        item
        for item in _fixture()["scenarios"]
        if item["case"] == "complete_without_remote_version"
    )
    encoded = json.dumps(scenario, sort_keys=True)

    assert "source_version" not in encoded
    assert "source_updated_at" not in encoded
    assert _evaluate(scenario) == _result("synced", True, False, "fresh_fetch")
    assert "does not establish a remote revision or continued currency after that fetch" in _skill_text()
