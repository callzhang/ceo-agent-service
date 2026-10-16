from pathlib import Path


SKILL_PATH = (
    Path(__file__).parents[1]
    / "ci"
    / "shared-skills"
    / "stardust-oa-project-review"
    / "SKILL.md"
)


def test_project_skill_reconciles_known_unknowns_without_repeating_checklists():
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert "mark each item resolved, still unknown, or newly raised" in skill
    assert "Ask only for a genuinely new fact the applicant can supply" in skill
    assert "Do not turn a completed response into another broad checklist" in skill
    assert "do not reopen the same three requests" in skill
    assert "do not send another request that cannot change the evidence" in skill
