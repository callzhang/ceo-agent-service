from pathlib import Path


SKILL = Path(__file__).parents[1] / "ci/shared-skills/dingtalk-oa-approval/SKILL.md"
RUNTIME_DOC = Path(__file__).parents[1] / "docs/runtime-mechanism.md"


def test_generic_oa_skill_requires_three_effective_same_gap_returns_before_count_based_reject():
    content = SKILL.read_text()

    assert "三次有效退回" in content
    assert "第四次审核" in content
    assert "不得仅凭退回次数拒绝" in content
    assert "退回回执只证明退回动作发生" in content
    assert "回应后同一缺口仍成立" in content


def test_historical_oa_review_uses_evidence_available_at_original_action_time():
    content = SKILL.read_text()
    runtime = RUNTIME_DOC.read_text()

    assert "历史审批证据按当时可见版本判断" in content
    assert "之后才更新的表格、文件或评论不能倒推" in content
    assert "明确标记为“未知”" in content
    assert "不得据此认定申请人当时漏交材料" in content
    assert "不得将该轮计为有效退回" in content
    assert "历史 OA 复核按原动作时点还原证据" in runtime
    assert "动作之后才更新的内容不能倒算成当时已有" in runtime
    assert "时间或版本无法核实就标记为“未知”" in runtime
