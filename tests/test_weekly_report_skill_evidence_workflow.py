import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "ci/shared-skills/ceo-weekly-report/SKILL.md"
RUNBOOK = ROOT / "ci/shared-skills/ceo-weekly-report/references/dingtalk-runbook.md"
EVALS = ROOT / "ci/shared-skills/ceo-weekly-report/evals/evals.json"


def test_weekly_report_requires_source_queries_before_unavailable_result():
    skill = SKILL.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "Never label an unsearched" in skill
    assert "Do not return `operation_unavailable` for evidence that was never" in runbook
    for tool_name in (
        "list_dingtalk_minutes",
        "read_dingtalk_minutes",
        "recent_dingtalk_conversations",
        "read_dingtalk_messages_in_window",
        "search_dingtalk_documents",
        "read_dingtalk_document_full",
    ):
        assert f"`{tool_name}" in runbook
    assert "`not searched`" in runbook
    assert "`no results`" in runbook
    assert "`read failed`" in runbook
    assert "absent business-line submissions do not block publication" in runbook


def test_weekly_report_eval_covers_premature_operation_unavailable():
    cases = json.loads(EVALS.read_text(encoding="utf-8"))["evals"]
    case = next(item for item in cases if item["id"] == 17)

    assert "operation_unavailable" in case["expected_output"]
    assert any("分钟清单查询" in item for item in case["expectations"])
    assert any("四条业务线" in item for item in case["expectations"])
    assert any("完整文档读回" in item for item in case["expectations"])


def test_weekly_report_skill_release_digests_match_sources():
    release = json.loads(
        (ROOT / "ci/consumer-system-contract-release.json").read_text(
            encoding="utf-8"
        )
    )
    entries = {
        item["source"]: item
        for item in release["files"]
        if item["source"] in {str(SKILL.relative_to(ROOT)), str(RUNBOOK.relative_to(ROOT))}
    }

    assert set(entries) == {str(SKILL.relative_to(ROOT)), str(RUNBOOK.relative_to(ROOT))}
    for source, item in entries.items():
        digest = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
        assert item["new_sha256"] == digest
