from pathlib import Path


def test_confirmed_missing_okr_period_uses_one_terminal_failure_code():
    skill = (
        Path(__file__).parents[1]
        / "ci/shared-skills/ceo-weekly-okr-report/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "error_code=MissingOkrPeriod" in skill
    assert "do not label" in skill
    assert "`dependency_read_unavailable`, `source_failed`" in skill
    assert "Other source transport or" in skill
    assert "incomplete-read failures keep their existing retry behavior" in skill
