import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "ceo-sales-weekly-report" / "SKILL.md"
FEATURES_PATH = ROOT / "data" / "config" / "skill-features.json"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _normalized_skill_text() -> str:
    return " ".join(_skill_text().split())


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
