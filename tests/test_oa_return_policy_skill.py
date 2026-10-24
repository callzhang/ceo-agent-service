from pathlib import Path


SKILL = Path(__file__).parents[1] / "ci/shared-skills/dingtalk-oa-approval/SKILL.md"
RUNTIME_DOC = Path(__file__).parents[1] / "docs/runtime-mechanism.md"
ARCHITECTURE_DOC = Path(__file__).parents[1] / "docs/architecture.md"


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


def test_dws_verified_return_survives_local_uncertain_but_candidate_alone_does_not():
    content = SKILL.read_text()
    runtime = RUNTIME_DOC.read_text()
    architecture = ARCHITECTURE_DOC.read_text()

    assert "DWS 记录证明退回动作发生，但若完整详情也不含退回理由" in content
    assert "先读完整审批详情中的 `operationRecords` 和各条 `remark`" in content
    assert "Consumer 生成但被跳过或未执行的候选文字" in content
    assert "没有 DWS 实际备注或其他可核验的已发送证据" in content
    assert "将该轮有效性标记为“未知”" in content
    assert "DWS `REDIRECT_PROCESS` 记录只证明退回动作发生" in runtime
    assert "完整审批详情的 `operationRecords[].remark`" in runtime
    assert "Consumer 生成但被跳过或未执行的候选" in runtime
    assert "在没有 DWS 实际备注或其他已发送证据时只是草稿" in runtime
    assert "不计入三次有效退回" in runtime
    assert "本地状态为 `skipped` 或 `uncertain` 本身不能抹去" in content
    assert "以 DWS 时间、实际 `remark` 和可核对的任务/流程信息确认" in content
    assert "本地状态异常不自动令该轮有效或无效" in content
    assert "本地 Attempt 标记为 `skipped` 或 `uncertain` 不会单独抹去" in runtime
    assert "按来源时间及流程/任务信息" in runtime
    assert "Provider success without DWS action readback is not sufficient" in architecture
    assert "`REDIRECT_PROCESS` 只证明发生了退回动作" in architecture
    assert "完整 DWS 审批详情的 `operationRecords[].remark`" in architecture
    assert "local Attempt marked `skipped` or `uncertain` does not erase a return" in architecture
