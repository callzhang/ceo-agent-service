from pathlib import Path


SKILL = Path("ci/shared-skills/stardust-oa-attendance-travel-review/SKILL.md")


def test_occasional_online_availability_does_not_force_leave_reclassification() -> None:
    skill = SKILL.read_text(encoding="utf-8")

    assert "Do not ask the applicant to reclassify leave as remote work" in skill
    assert "Occasional online availability or meeting attendance alone" in skill
    assert "only when the stated duties materially conflict with leave" in skill
    assert "Zhou Junjie" not in skill
    assert "BHZX5wC1SHCNFYgwez08lA03641792719517" not in skill
