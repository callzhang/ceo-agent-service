from pathlib import Path


SKILL = Path("ci/shared-skills/stardust-oa-people-review/SKILL.md")


def test_org_change_requires_source_verified_post_return_evidence() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "source-timestamp order using the DWS operation records" in text
    assert "Only material submitted after the latest verified return" in text
    assert "an approver's `AGREE` alone does not resolve the gap" in text
    assert "return the current OA task to the approver who agreed" in text
    assert "do not treat the requirement as met" in text
    assert "Do not repeat the same request to the submitter" in text
    assert "Reviewer-side read or OCR failures are not applicant-resolvable gaps" in text
