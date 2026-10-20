from pathlib import Path


SKILL = Path("ci/shared-skills/stardust-oa-people-review/SKILL.md")


def test_org_change_requires_source_verified_post_return_evidence() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "source-timestamp order using the DWS operation records" in text
    assert "Only material submitted after the latest verified return" in text
    assert "an approver's `AGREE` alone does not resolve the gap" in text
    assert "preserve the current OA task as pending with `no_action`" in text
    assert "do not issue another return or duplicate comment" in text
    assert "Reviewer-side read or OCR failures are not applicant-resolvable gaps" in text
