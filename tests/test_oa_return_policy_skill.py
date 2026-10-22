from pathlib import Path


SKILL = Path(__file__).parents[1] / "ci/shared-skills/dingtalk-oa-approval/SKILL.md"


def test_generic_oa_skill_has_one_unambiguous_return_limit():
    content = SKILL.read_text()

    assert "同一审批事项最多退回两次" in content
    assert "同一有效问题退回满 3 次后才适用拒绝" not in content
    assert "满 3 次后第 4 次拒绝" not in content
    assert "拒绝必须有独立的实质理由" in content
