from pathlib import Path


SKILL = (
    Path(__file__).resolve().parents[1]
    / "ci/shared-skills/ceo-daily-report/SKILL.md"
)


def test_daily_report_tries_every_group_and_discloses_what_was_not_read():
    # Derek 2026-10-26: incomplete group coverage is a disclosed gap; the report
    # is still written and delivered (run 417543 held it back after reading 2 groups).
    skill = " ".join(SKILL.read_text(encoding="utf-8").lower().split())

    assert "every accessible group conversation" in skill
    assert "an inventory is not a message read" in skill
    assert "try every accessible group conversation" in skill
    assert "is a coverage gap, not a reason to hold the report" in skill
    assert "write it and deliver it anyway" in skill
    assert "`not attempted`" in skill
    assert "do not write the report or propose its notification while" not in skill


def test_daily_report_audit_does_not_return_a_report_for_a_disclosed_gap():
    skill = " ".join(SKILL.read_text(encoding="utf-8").lower().split())

    assert "is a disclosed gap, not a reason to return the proposal" in skill
    assert "return the proposal: this is unattempted required coverage" not in skill
