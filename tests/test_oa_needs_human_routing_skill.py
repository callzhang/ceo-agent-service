from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "ci/shared-skills/dingtalk-oa-approval/SKILL.md"


def test_current_case_facts_do_not_use_needs_human_requested_input():
    text = SKILL.read_text(encoding="utf-8")

    assert "需要 Derek 为同类任务确定可复用规则的真实规则缺口" in text
    assert "`requested_input` 必须为空" in text
    assert "不能用 `needs_human` 或 `requested_input` 把当前审批的事实、材料、偏好或普通审批决定交给 Derek" in text
    assert "向已核实且掌握事实的申请人、发起人或责任人提出具体、可核验的问题" in text
    assert "Audit 的建议也不能替代对问题归属和补正对象的核实" in text
    assert "或一个明确的 `requested_input`" not in text
    assert "这样付是否可以" not in text

