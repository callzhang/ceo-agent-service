import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-sales-weekly-report" / "SKILL.md"
FEATURES_PATH = ROOT / "data" / "config" / "skill-features.json"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _normalized_skill_text() -> str:
    return " ".join(_skill_text().split())


def _score_rows(skill_text: str) -> list[tuple[str, str, str]]:
    table = re.search(
        r"\| Progress index \| Score \| Status \|\n\| --- \| ---: \| --- \|\n(?P<rows>(?:\|[^\n]+\|\n)+)",
        skill_text,
    )
    assert table is not None
    rows = []
    for line in table.group("rows").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 3
        rows.append(tuple(cells))
    return rows


def _weight_rows(skill_text: str) -> list[tuple[str, int]]:
    table = re.search(
        r"\| Metric group \| Weight \|\n\| --- \| ---: \|\n(?P<rows>(?:\|[^\n]+\|\n)+)",
        skill_text,
    )
    assert table is not None
    rows = []
    for line in table.group("rows").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 2
        assert cells[1].endswith("%")
        rows.append((cells[0], int(cells[1][:-1])))
    return rows


def _assert_complete_score_contract(skill_text: str) -> None:
    assert _score_rows(skill_text) == [
        ("`>= 1.10`", "`100`", "`✅ 超前`"),
        ("`0.95` to `< 1.10`", "interpolate `90` to `100`", "`✅ 正常`"),
        ("`0.80` to `< 0.95`", "interpolate `75` to `90`", "`⌛ 轻度偏离`"),
        ("`0.60` to `< 0.80`", "interpolate `50` to `75`", "`⚠️ 明显偏离`"),
        ("`< 0.60`", "interpolate `0` to `50` over `0.00` to `0.60`", "`❌ 严重偏离`"),
    ]
    assert _weight_rows(skill_text) == [
        ("New signed contracts or orders", 25),
        ("Recognized revenue", 15),
        ("Payment collected", 20),
        ("Gross profit or gross margin", 10),
        ("Weighted Pipeline", 15),
        ("Opportunities advancing to the next Gate", 10),
        ("Overdue receivables and material sales risk", 5),
    ]
    assert sum(weight for _, weight in _weight_rows(skill_text)) == 100


def test_sales_weekly_report_skill_composes_existing_skills_and_sources() -> None:
    text = _normalized_skill_text()

    for required in (
        "`ceo-weekly-report`",
        "`fxiaoke-crm-cli`",
        "current company OKR or an explicitly approved sales plan",
        "authoritative target",
        "CRM actual",
        "target-definition conflict",
        "Asia/Shanghai",
    ):
        assert required in text


def test_sales_weekly_report_skill_is_explicitly_crm_read_only() -> None:
    text = _normalized_skill_text()

    for required in (
        "CRM reads only",
        "Never pass `--confirm` or `--yes`",
        "Do not create, update, delete, invalidate",
        "Do not assign, transfer, claim, return, or reclaim",
        "Do not create follow-ups or sales activities",
        "Do not send CRM email, IM, feed, or notice",
        "Do not act on CRM approvals, BPM, workflows, stages, automations, or schedules",
        "Do not log in, log out, import token data, or change CLI configuration",
        "cannot establish that a command is read-only",
    ):
        assert required in text

    for forbidden in (
        "Fxiaoke MCP",
        "crm_connector MCP",
        "execute_reviewed_read",
        "execute_reviewed_write",
    ):
        assert forbidden not in text


def test_sales_weekly_report_skill_defines_scoring_and_coverage() -> None:
    text = _normalized_skill_text()

    for required in (
        "target_completion_rate = actual / period_target",
        "time_progress_rate = elapsed_time / total_target_period",
        "progress_index = target_completion_rate / time_progress_rate",
        "piecewise-linear",
        "score coverage",
        "Missing values are not zero",
        "company score",
        "MorningStar",
        "Friday",
        "international business",
        "world-model marketing",
        "Do not rank individual salespeople",
        "待归属",
    ):
        assert required in text


def test_sales_weekly_report_skill_writes_only_one_final_workspace_report() -> None:
    raw_text = _skill_text()
    text = " ".join(raw_text.split())

    for required in (
        "`CEO_WORKSPACE`",
        "`01_业务与客户/销售周报/YYYY/`",
        "`YYYY-MM-DD-HHmm-销售周报.md`",
        "Never overwrite an existing report",
        "Save only the final Markdown report",
        "Reopen the saved file",
    ):
        assert required in text

    assert raw_text.endswith("\n")
    assert not raw_text.endswith("\n\n")


def test_sales_weekly_report_has_no_independent_producer_feature() -> None:
    payload = json.loads(FEATURES_PATH.read_text(encoding="utf-8"))
    feature_skills = {
        skill
        for feature in payload["features"]
        for skill in feature.get("skills", [])
    }

    assert "ceo-sales-weekly-report" not in feature_skills


def test_sales_weekly_report_contract_preserves_dependency_precedence() -> None:
    text = _normalized_skill_text()

    for required in (
        "management reasoning, evidence classification, issue continuity, and privacy rules",
        "source authority, reporting window/scope, report structure, storage, and completion rules take precedence",
        "final-Markdown-only",
        "without importing write behavior",
    ):
        assert required in text


def test_sales_weekly_report_contract_defines_all_score_bands_and_weights() -> None:
    raw_text = _skill_text()
    text = _normalized_skill_text()

    for band in (
        "`>= 1.10`",
        "`0.95` to `< 1.10`",
        "`0.80` to `< 0.95`",
        "`0.60` to `< 0.80`",
        "`< 0.60`",
    ):
        assert band in text

    _assert_complete_score_contract(raw_text)


def test_sales_weekly_report_contract_has_exact_ordered_sections() -> None:
    raw_text = _skill_text()
    match = re.search(
        r"## Report Structure\n\nWrite these sections in order:\n\n(?P<section_list>(?:\d+\. `[^`]+`\n?)+)",
        raw_text,
    )
    assert match is not None
    sections = re.findall(r"\d+\. `([^`]+)`", match.group("section_list"))
    assert sections == [
        "CEO销售判断",
        "公司业务目标进度评分",
        "公司销售经营指标",
        "MorningStar",
        "Friday",
        "国际业务",
        "世界模型营销",
        "重点Pipeline与异常",
        "回款、应收与交付风险",
        "跨周问题与下周检查点",
        "需CEO讨论与决策",
        "数据口径、覆盖率与缺失项",
    ]


def test_sales_weekly_report_contract_handles_ratios_and_zero_coverage() -> None:
    text = _normalized_skill_text()

    for required in (
        "elapsed-time normalization only to additive period-to-date measures",
        "ratio/snapshot metrics",
        "gross-margin percentage",
        "without automatic elapsed-time division",
        "approved dated trajectory",
        "zero eligible weight independently for company and each business line",
        "不可评分",
        "coverage 0%",
        "enumerate missing definitions/sources",
        "never normalize, invent numeric score, or assign status",
        "If compatible target semantics are unavailable, mark the metric unscored and reduce score coverage",
    ):
        assert required in text


def test_sales_weekly_report_contract_fails_without_artifact() -> None:
    text = _normalized_skill_text()

    for required in (
        "If the authoritative target source cannot be resolved, or all material CRM actuals are unavailable, return a failed outcome and do not create a report",
        "ceo_workspace_unavailable",
        "sales_weekly_report_path_exists",
        "leave it unchanged",
        "A generated answer without a matching saved file is not complete",
    ):
        assert required in text


def test_sales_weekly_report_contract_separates_report_cutoff_from_target_period() -> None:
    text = _normalized_skill_text()

    for required in (
        "Determine actual-data cutoff from the requested report interval, not the target period",
        "For a completed prior-week report, preserve the Monday-exclusive end even when a quarterly or annual target is still open",
        "Only truncate to actual query time when the requested report interval itself is unfinished/open",
        "Score quarterly or annual elapsed-time normalization at the reporting cutoff, separately from interval selection",
    ):
        assert required in text


def test_sales_weekly_report_contract_excludes_personal_data_from_artifact() -> None:
    text = _normalized_skill_text()

    for required in (
        "Do not save customer or contact phone numbers",
        "Do not save unrelated personal data",
    ):
        assert required in text


def test_sales_weekly_report_contract_mutations_are_rejected() -> None:
    original = _skill_text()
    normalized = _normalized_skill_text()

    with pytest.raises(AssertionError):
        _assert_complete_score_contract(original.replace("`100`", "`0`", 1))

    with pytest.raises(AssertionError):
        _assert_complete_score_contract(
            original.replace(
                "| Overdue receivables and material sales risk | 5% |",
                "| Overdue receivables and material sales risk | 5% |\n| Extra metric | 25% |",
                1,
            )
        )

    failure_policy = (
        "If the authoritative target source cannot be resolved, or all material CRM actuals are unavailable, "
        "return a failed outcome and do not create a report."
    )
    assert failure_policy in normalized
    with pytest.raises(AssertionError):
        assert failure_policy in normalized.replace(failure_policy, "", 1)

    ratio_policy = "If compatible target semantics are unavailable, mark the metric unscored and reduce score coverage."
    assert ratio_policy in normalized
    with pytest.raises(AssertionError):
        assert ratio_policy in normalized.replace(ratio_policy, "", 1)
