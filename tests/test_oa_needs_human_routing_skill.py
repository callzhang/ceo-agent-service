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


def test_project_facts_still_to_be_formed_are_returned_before_rule_escalation():
    generic = SKILL.read_text(encoding="utf-8")
    project = (ROOT / "ci/shared-skills/stardust-oa-project-review/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "尚未形成或正在等待技术/责任来源方讨论" in generic
    assert "正式退回申请人" in generic
    assert "不要把“现在暂时拿不到”改写成让 Derek 选择" in generic
    assert "只有证据确认申请人及其可协调的责任来源方均无法补齐" in generic
    assert "not yet formed or is waiting for technical discussion" in project
    assert "formally return the application for coordination and resubmission" in project
    assert "both confirmed unable to complete the material" in project
