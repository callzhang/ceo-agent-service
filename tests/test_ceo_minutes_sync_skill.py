from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-minutes-sync" / "SKILL.md"
SCENARIOS_PATH = ROOT / "tests" / "fixtures" / "ceo_minutes_sync_scenarios.json"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _frontmatter() -> dict[str, object]:
    text = _skill_text()
    assert text.startswith("---\n")
    parsed = yaml.safe_load(text.split("---\n", 2)[1])
    assert isinstance(parsed, dict)
    return parsed


def _scenarios() -> list[dict[str, object]]:
    fixture = _fixture()
    scenarios = fixture["scenarios"]
    assert isinstance(scenarios, list)
    return scenarios


def _fixture() -> dict[str, object]:
    fixture = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    assert fixture["schema"] == "ceo_minutes_sync_skill_scenarios.v1"
    return fixture


def _scenario(case: str) -> dict[str, object]:
    return next(item for item in _scenarios() if item["case"] == case)


def test_minutes_sync_skill_frontmatter_is_metadata_not_schedule() -> None:
    frontmatter = _frontmatter()

    assert set(frontmatter) == {"name", "description", "metadata"}
    assert frontmatter["name"] == "ceo-minutes-sync"
    assert str(frontmatter["description"]).startswith("Use when ")
    assert frontmatter["metadata"] == {"managed_by": "ceo-agent-service"}


def test_recorded_complete_new_envelope_maps_to_synced_with_exact_fetch_evidence() -> None:
    scenario = _scenario("complete_new")
    dws = scenario["dws"]
    assert dws["list"]["data"]["complete"] is True
    assert dws["detail"]["ok"] is True
    assert dws["transcript"]["data"]["complete"] is True
    serialized = json.dumps(dws["transcript"]["data"]["paragraphs"], ensure_ascii=False)
    assert hashlib.sha256(serialized.encode()).hexdigest()

    assert "| `complete_new` | `synced` | yes | no | `fresh_fetch` |" in _skill_text()


def test_recorded_complete_equal_envelope_maps_to_unchanged_fetched() -> None:
    scenario = _scenario("complete_unchanged")
    dws = scenario["dws"]
    archive_contract = _fixture()["archive_contract"]
    assert archive_contract == {
        "artifacts": {
            "basic": "detail.data.basic",
            "summary": "detail.data.summary",
            "transcript": "transcript.data.paragraphs",
        },
        "json_encoding": {
            "encoding": "UTF-8",
            "ensure_ascii": False,
            "sort_keys": True,
            "separators": [",", ":"],
            "trailing_newline": False,
        },
    }
    current_hashes = {
        name: hashlib.sha256(
            json.dumps(
                artifact,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        for name, artifact in {
            "basic": dws["detail"]["data"]["basic"],
            "summary": dws["detail"]["data"]["summary"],
            "transcript": dws["transcript"]["data"]["paragraphs"],
        }.items()
    }
    assert scenario["prior"]["artifact_sha256"] == current_hashes
    assert "`detail.data.basic`, `detail.data.summary`, and `transcript.data.paragraphs`" in _skill_text()
    assert "UTF-8 JSON with sorted keys, compact `,`/`:` separators, unescaped Unicode, and no trailing newline" in _skill_text()
    assert (
        "| `complete_unchanged` | `skipped` | yes | no | `unchanged_fetched` |"
        in _skill_text()
    )


def test_recorded_partial_transcript_fails_without_advancing_cursor() -> None:
    scenario = _scenario("transcript_incomplete")
    transcript = scenario["dws"]["transcript"]
    assert transcript["ok"] is True
    assert transcript["data"]["complete"] is False
    assert transcript["meta"]["pagination"]["next_token"] == "page-2"
    assert (
        "| `transcript_incomplete` | `failed` | no | no | `freshness_unknown` |"
        in _skill_text()
    )


def test_complete_envelope_without_remote_version_keeps_honest_fetch_evidence() -> None:
    scenario = _scenario("complete_without_remote_version")
    encoded = json.dumps(scenario["dws"], sort_keys=True)
    assert "source_version" not in encoded
    assert "source_updated_at" not in encoded
    assert (
        "| `complete_without_remote_version` | `synced` | yes | no | `fresh_fetch` |"
        in _skill_text()
    )
    assert "does not establish a remote revision or continued currency after that fetch" in _skill_text()


def test_permission_recovery_is_read_even_when_absent_from_current_list() -> None:
    scenario = _scenario("permission_recovered")
    assert scenario["prior"]["status"] == "permission_pending"
    assert scenario["dws"]["list"]["data"]["items"] == []
    assert scenario["dws"]["detail"]["ok"] is True
    assert (
        "| `permission_recovered` | `synced` | yes | no | `permission_recovered` |"
        in _skill_text()
    )


@pytest.mark.parametrize(
    "case",
    (
        "restricted_relevant_authorized",
        "restricted_out_of_scope",
        "restricted_relevance_unknown",
    ),
)
def test_restricted_recorded_envelopes_follow_relevance_and_authorization(case: str) -> None:
    scenario = _scenario(case)
    expected = scenario["expected"]
    row = (
        f"| `{case}` | `{expected['outcome']}` | "
        f"{'yes' if expected['cursor_advance'] else 'no'} | "
        f"{'yes' if expected['access_request'] else 'no'} | "
        f"`{expected['evidence']}` |"
    )

    assert scenario["dws"]["detail"]["error"]["code"] == "permission_denied"
    assert row in _skill_text()


def test_skill_contract_keeps_results_mutually_exclusive_and_requires_real_fetches() -> None:
    text = _skill_text()
    counts = Counter(scenario["expected"]["outcome"] for scenario in _scenarios())

    assert counts == {"synced": 3, "skipped": 2, "permission_pending": 2, "failed": 1}
    assert sum(counts.values()) == len(_scenarios())
    assert "basic and summary reads succeed" in text
    assert "transcript pagination reaches `data.complete=true`" in text
    assert "synced + skipped + permission_pending + failed = discovered" in text
    assert "exactly one outcome" in text
    assert "A submitted access request remains `permission_pending`" in text
    assert "hash those exact archived bytes" in text
    assert "The run scope is new list items plus saved pending IDs" in text
    assert "record the out-of-scope decision in the item cursor" in text
    assert "**REQUIRED SUB-SKILL:** Use `dingtalk-minutes`" in text
    assert "**CONDITIONAL SUB-SKILL:** Use `dingtalk-minutes-access-request`" in text
