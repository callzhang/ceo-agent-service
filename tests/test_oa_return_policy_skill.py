from pathlib import Path


SKILL = Path(__file__).parents[1] / "ci/shared-skills/dingtalk-oa-approval/SKILL.md"


def test_generic_oa_skill_requires_three_effective_same_gap_returns_before_count_based_reject():
    content = SKILL.read_text()

    assert "三次有效退回" in content
    assert "第四次审核" in content
    assert "不得仅凭退回次数拒绝" in content
    assert "退回回执只证明退回动作发生" in content
    assert "回应后同一缺口仍成立" in content
