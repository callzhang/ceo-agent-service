"""Contract fixtures shared by the staged multisource attention implementation."""

import json

import pytest
from pydantic import ValidationError

from app.task_models import TaskAgentDecision, WorkItem


PROJECT_ROW = "| 示例项目 | 回款复核 | 降低现金流风险 | 09-30 | 有风险 |"
RISK_QUOTE = "已交付收入因客户确认延迟，尚未进入当期确认，供应商付款需要协调。"
TASK_QUOTE = "复核示例项目回款及供应商付款计划。"


def report_item():
    return WorkItem.model_validate({
        "source": {"type": "project_weekly_report", "ref": "report:fixture"},
        "context": {"source_conversation_kind": "group"},
        "summary": json.dumps({
            "report": {"reporting_period": "2026-W39"},
            "markdown": (
                "## 手头项目\n| 项目名 | 负责内容 | 目标 | DDL | 状态 |\n"
                "| --- | --- | --- | --- | --- |\n"
                f"{PROJECT_ROW}\n## 本周进展\n{RISK_QUOTE}\n"
                f"## 下周工作重点\n{TASK_QUOTE}"
            ),
        }, ensure_ascii=False),
    })


def decision_payload():
    return {"task_decisions": [{
        "action": "record_candidate", "transition": "none",
        "source_ref": "report:fixture", "source_excerpt": TASK_QUOTE,
        "title": "复核示例项目回款及供应商付款计划", "missing_evidence": ["owner"],
        "project_proposal": {
            "title": "示例项目", "reason": "正式周报项目登记表明确列出",
            "authority": "project_weekly_report", "source_excerpt": PROJECT_ROW,
        },
        "attention_proposal": {
            "category": "watch", "title": "示例项目回款风险",
            "why_attention": "收入确认延迟与付款安排可能影响现金流",
            "current_state": "收入确认延迟",
            "ceo_action": "当前无需你处理；观察客户确认及付款安排是否恢复。",
            "anchor_id": None, "material_trigger": "risk_escalation",
            "evidence": [{"source_ref": "report:fixture", "source_excerpt": RISK_QUOTE}],
        },
    }]}


def test_parse_separate_registry_task_and_attention_evidence_without_inventing_owner():
    item = report_item()
    parsed = TaskAgentDecision.model_validate(decision_payload()).task_decisions[0]
    report = json.loads(item.summary)["report"]
    assert report["reporting_period"] == "2026-W39"
    assert parsed.source_excerpt == TASK_QUOTE
    assert parsed.project_proposal.source_excerpt == PROJECT_ROW
    attention = parsed.attention_proposal
    assert attention.anchor_id is None
    assert attention.related_task_ids == []
    assert attention.evidence[0].signal_id is None
    assert attention.evidence[0].source_ref == item.source.ref
    assert attention.evidence[0].source_excerpt == RISK_QUOTE
    assert RISK_QUOTE not in parsed.source_excerpt
    assert parsed.owner_name == ""
    assert parsed.owner_user_id == ""
    assert parsed.missing_evidence == ["owner"]


def test_null_anchor_requires_this_decisions_project_proposal():
    payload = decision_payload()
    del payload["task_decisions"][0]["project_proposal"]
    with pytest.raises(ValidationError, match="requires this decision's project_proposal"):
        TaskAgentDecision.model_validate(payload)


def test_omitted_anchor_resolves_this_decisions_project_proposal():
    payload = decision_payload()
    del payload["task_decisions"][0]["attention_proposal"]["anchor_id"]
    parsed = TaskAgentDecision.model_validate(payload).task_decisions[0]
    assert parsed.attention_proposal.anchor_id is None


def test_omitted_anchor_without_this_decisions_project_proposal_is_rejected():
    payload = decision_payload()
    del payload["task_decisions"][0]["attention_proposal"]["anchor_id"]
    del payload["task_decisions"][0]["project_proposal"]
    with pytest.raises(ValidationError, match="requires this decision's project_proposal"):
        TaskAgentDecision.model_validate(payload)


def test_project_proposal_in_another_decision_cannot_resolve_null_anchor():
    payload = decision_payload()
    registry_decision = decision_payload()["task_decisions"][0]
    del registry_decision["attention_proposal"]
    del payload["task_decisions"][0]["project_proposal"]
    payload["task_decisions"].append(registry_decision)
    with pytest.raises(ValidationError, match="requires this decision's project_proposal"):
        TaskAgentDecision.model_validate(payload)


def test_existing_anchor_does_not_require_new_project_proposal():
    payload = decision_payload()
    decision = payload["task_decisions"][0]
    del decision["project_proposal"]
    decision["attention_proposal"].update(anchor_id=8, related_task_ids=[3, 9])
    decision["attention_proposal"]["evidence"][0]["signal_id"] = 11
    parsed = TaskAgentDecision.model_validate(payload).task_decisions[0]
    assert parsed.attention_proposal.related_task_ids == [3, 9]
    assert parsed.attention_proposal.evidence[0].signal_id == 11


@pytest.mark.parametrize("field,value", [
    ("evidence", []), ("evidence", None),
    ("anchor_id", 0), ("anchor_id", "8"), ("anchor_id", True),
    ("related_task_ids", [0]), ("related_task_ids", [-1]),
    ("related_task_ids", ["3"]), ("related_task_ids", [True]),
])
def test_attention_rejects_invalid_evidence_and_ids(field, value):
    payload = decision_payload()
    payload["task_decisions"][0]["attention_proposal"][field] = value
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(field in e["loc"] for e in error.value.errors())


@pytest.mark.parametrize("field,value", [
    ("source_ref", ""), ("source_ref", " \t\n"),
    ("source_excerpt", ""), ("source_excerpt", " \t\n"),
    ("signal_id", 0), ("signal_id", -1), ("signal_id", "1"), ("signal_id", True),
])
def test_attention_evidence_rejects_blank_provenance_and_invalid_signal_ids(field, value):
    payload = decision_payload()
    payload["task_decisions"][0]["attention_proposal"]["evidence"][0][field] = value
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(e["loc"][-1] == field for e in error.value.errors())


@pytest.mark.parametrize("value", ["", " \t\n", None])
def test_project_registration_excerpt_is_required_and_nonblank(value):
    payload = decision_payload()
    payload["task_decisions"][0]["project_proposal"]["source_excerpt"] = value
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(e["loc"][-1] == "source_excerpt" for e in error.value.errors())


def test_project_registration_excerpt_cannot_be_omitted():
    payload = decision_payload()
    del payload["task_decisions"][0]["project_proposal"]["source_excerpt"]
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(e["loc"][-1] == "source_excerpt" for e in error.value.errors())


def test_attention_evidence_cannot_be_omitted():
    payload = decision_payload()
    del payload["task_decisions"][0]["attention_proposal"]["evidence"]
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(e["loc"][-1] == "evidence" for e in error.value.errors())


def test_old_trigger_evidence_field_is_rejected():
    payload = decision_payload()
    payload["task_decisions"][0]["attention_proposal"]["trigger_evidence"] = RISK_QUOTE
    with pytest.raises(ValidationError) as error:
        TaskAgentDecision.model_validate(payload)
    assert any(e["loc"][-1] == "trigger_evidence" and e["type"] == "extra_forbidden"
               for e in error.value.errors())
