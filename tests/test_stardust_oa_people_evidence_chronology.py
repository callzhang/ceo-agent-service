from pathlib import Path


SKILL = Path("ci/shared-skills/stardust-oa-people-review/SKILL.md")


def test_org_change_requires_source_verified_post_return_evidence() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "source-timestamp order using the DWS operation records" in text
    assert "Only material submitted after the latest verified return" in text
    assert "If the responsible approver then approves again, treat that as the approver's decision" in text
    assert "an approver's `AGREE` alone does not resolve the gap" not in text
    assert "There is no automatic rejection threshold based on the number of returns" in text
    assert "Do not send the same return again or reject solely because no newer evidence was supplied after the responsible approver has expressly approved" in text
    assert "Do not repeat the same request to the submitter" in text
    assert "Reviewer-side read or OCR failures are not applicant-resolvable gaps" in text
