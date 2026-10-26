from pathlib import Path


OA_SKILL = Path("ci/shared-skills/dingtalk-oa-approval/SKILL.md")
PEOPLE_SKILL = Path("ci/shared-skills/stardust-oa-people-review/SKILL.md")


def test_three_effective_same_gap_returns_rule_is_consistent_across_skills() -> None:
    oa_text = OA_SKILL.read_text(encoding="utf-8")
    people_text = PEOPLE_SKILL.read_text(encoding="utf-8")

    assert "适用于所有需要 Derek 审批的事项" in oa_text
    assert "前两次退回必须一次性列清" in oa_text
    assert "第四次到达 Derek 授权审批节点时" in oa_text
    assert "三次有效补正机会" in oa_text
    assert "不得仅凭退回次数拒绝" in oa_text
    assert "当前三轮退回均由当轮可读、来源核验的证据支持" in oa_text

    assert "Only evidence submitted after a verified return can count as a post-return response" in people_text
    assert "Apply the generic OA Skill's same-gap threshold" in people_text
    assert "count only a verified, actionable return based on a decision-changing" in people_text
    assert "At Derek's authorized review node after three such effective returns" in people_text
    assert "including approver comments" in people_text
    assert "Do not use the count of DWS `REDIRECT_PROCESS` records or service receipts as a substitute" in people_text
    assert "There is no automatic rejection threshold" not in people_text
    assert "after the third review still fails on the same issue, do not return or comment again" not in people_text
