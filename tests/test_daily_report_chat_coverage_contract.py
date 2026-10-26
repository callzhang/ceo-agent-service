from pathlib import Path


SKILL = (
    Path(__file__).resolve().parents[1]
    / "ci/shared-skills/ceo-daily-report/SKILL.md"
)


def test_daily_report_treats_unattempted_group_reads_as_missing_required_coverage():
    skill = " ".join(SKILL.read_text(encoding="utf-8").lower().split())

    assert "every accessible group conversation" in skill
    assert "an inventory is not a message read" in skill
    assert "not attempted is not a source failure" in skill
    assert "if an inventory contains an accessible conversation with no" in skill
    assert "read operation actually fails, times out, or returns an incomplete page" in skill
