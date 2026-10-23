from pathlib import Path


PEOPLE_SKILL = Path("ci/shared-skills/stardust-oa-people-review/SKILL.md")
GENERIC_SKILL = Path("ci/shared-skills/dingtalk-oa-approval/SKILL.md")


def test_org_change_uses_timestamped_evidence_without_repeating_return_or_auto_reject() -> None:
    text = PEOPLE_SKILL.read_text(encoding="utf-8")

    assert "source-timestamp order" in text
    assert "using the DWS operation records" in text
    assert "Only material submitted after the latest verified return" in text
    assert "treat that as the approver's decision" in text
    assert "do not return or comment again on the unchanged gap" in text
    assert "do not require a separate confirmation record" in text
    assert "There is no automatic rejection threshold based on the number of returns" in text
    assert "Do not invent or import a “reject after three returns” rule" in text
    assert "reject solely because no newer evidence was supplied after the responsible approver has expressly approved" in text
    assert "Statements in the approval's comments are evidence at the same level" in text
    assert "Do not repeat the same request to the submitter" in text
    assert "Reviewer-side read or OCR failures are not applicant-resolvable gaps" in text


def test_generic_repeat_return_rule_never_rejects_by_count_and_honors_renewed_approval() -> None:
    text = GENERIC_SKILL.read_text(encoding="utf-8")
    assert "“三次退回后拒绝”" in text
    assert "审批人再次同意应视为其审批决定" in text
    assert "审批人再次同意本身不解决缺口" not in text
    assert "不要求另行提交确认记录" in text
    assert "不得仅因未提交新材料而自动拒绝" in text
