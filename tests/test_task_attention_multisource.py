"""Contract fixtures shared by the staged multisource attention implementation."""

import json

import pytest
from pydantic import ValidationError

from app.task_models import TaskAgentDecision, WorkItem, WorkItemSourceType
from app.task_agent import apply_task_agent_decision
from app.store import AutoReplyStore


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


def test_omitted_anchor_accepts_this_decisions_project_proposal():
    payload = decision_payload()
    del payload["task_decisions"][0]["attention_proposal"]["anchor_id"]
    parsed = TaskAgentDecision.model_validate(payload).task_decisions[0]
    assert parsed.attention_proposal.anchor_id is None


@pytest.mark.parametrize("registration_excerpt", [
    PROJECT_ROW,
    PROJECT_ROW.lstrip("| "),
    "回款复核 | 降低现金流风险 | 09-30 | 有风险 |",
    "\n" + PROJECT_ROW,
])
def test_apply_independent_registry_proof_registers_project_and_reuses_task(tmp_path, registration_excerpt):
    store = AutoReplyStore(tmp_path / "registry.sqlite3")
    payload = decision_payload()
    payload["task_decisions"][0]["project_proposal"]["source_excerpt"] = registration_excerpt
    decision = TaskAgentDecision.model_validate(payload)
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(),
                                       decision=decision, record_run=False)
    project, = store.list_business_projects()
    assert project.title == "示例项目"
    task = store.get_business_task(result.task_ids[0])
    assert task.stage.value == "candidate"
    assert task.business_relevance.value == "relevant"
    assert [link.anchor_id for link in store.list_business_task_anchor_links(task_id=task.id)] == [
        project.canonical_anchor_id
    ]
    applied, = result.attention_proposals
    assert applied.anchor_id == project.canonical_anchor_id
    assert applied.task_id == task.id
    assert applied.decision == decision.task_decisions[0]
    replay = apply_task_agent_decision(store, summary_input_id=2, work_item=report_item(),
                                       decision=decision, record_run=False)
    assert replay.task_ids == result.task_ids
    assert store.list_business_projects() == [project]
    assert replay.attention_proposals[0].anchor_id == applied.anchor_id


@pytest.mark.parametrize("change", [
    {"title": "另一项目"},
    {"source_excerpt": TASK_QUOTE},
    {"source_excerpt": "| 虚构项目 | 无来源 |"},
    {"authority": "management_weekly_report"},
    {"title": "回款复核", "source_excerpt": "回款复核 | 降低现金流风险 | 09-30 | 有风险 |"},
])
def test_invalid_registry_proof_rolls_back_domain_transaction(tmp_path, change):
    store = AutoReplyStore(tmp_path / "invalid-registry.sqlite3")
    payload = decision_payload()
    payload["task_decisions"][0]["project_proposal"].update(change)
    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(),
                                  decision=TaskAgentDecision.model_validate(payload), record_run=False)
    assert store.list_business_projects() == []
    with store._connect() as db:
        assert db.execute("select count(*) from business_tasks").fetchone()[0] == 0
        assert db.execute("select count(*) from business_task_signals").fetchone()[0] == 0


@pytest.mark.parametrize("quote_from_following_row", [False, True])
def test_registry_prose_cannot_register_a_project(tmp_path, quote_from_following_row):
    store = AutoReplyStore(tmp_path / "registry-prose.sqlite3")
    prose = "项目负责人统一更新登记信息。"
    second_row = PROJECT_ROW.replace("示例项目", "真实第二项目")
    work_item = report_item()
    source = json.loads(work_item.summary)
    source["markdown"] = source["markdown"].replace(
        PROJECT_ROW, PROJECT_ROW + "\n" + prose + "\n" + second_row,
    )
    work_item = work_item.model_copy(update={"summary": json.dumps(source, ensure_ascii=False)})
    payload = decision_payload()
    payload["task_decisions"][0]["project_proposal"].update(
        title=prose, source_excerpt="\n" + second_row if quote_from_following_row else prose,
    )
    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(
            store, summary_input_id=1, work_item=work_item,
            decision=TaskAgentDecision.model_validate(payload), record_run=False,
        )
    assert store.list_business_projects() == []
    with store._connect() as db:
        assert db.execute("select count(*) from business_tasks").fetchone()[0] == 0


@pytest.mark.parametrize("meeting_source,quote", [
    (False, TASK_QUOTE), (True, "会议决定启动未出现在来源的项目"),
])
def test_meeting_registration_requires_meeting_provenance_and_exact_quote(tmp_path, meeting_source, quote):
    store = AutoReplyStore(tmp_path / "invalid-meeting.sqlite3")
    item = report_item()
    item = item.model_copy(update={
        "source": item.source.model_copy(update={"type": WorkItemSourceType.AI_MINUTES if meeting_source else WorkItemSourceType.REPLY_ATTEMPT}),
    })
    payload = decision_payload()
    payload["task_decisions"][0]["project_proposal"].update(
        authority="meeting_decision", source_excerpt=quote)
    with pytest.raises(ValueError, match="current meeting source"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item,
                                  decision=TaskAgentDecision.model_validate(payload), record_run=False)
    assert store.list_business_projects() == []


@pytest.mark.parametrize("new_project", [False, True])
def test_each_decision_keeps_its_own_applied_project_anchor(tmp_path, new_project):
    store = AutoReplyStore(tmp_path / "separate-anchors.sqlite3")
    payload = decision_payload()
    second = decision_payload()["task_decisions"][0]
    second["title"] = "另一项回款复核"
    item = report_item()
    if new_project:
        second_row = PROJECT_ROW.replace("示例项目", "另一项目")
        second["project_proposal"].update(title="另一项目", source_excerpt=second_row)
        second_quote = TASK_QUOTE.replace("示例项目", "另一项目")
        second["source_excerpt"] = second_quote
        source = json.loads(item.summary)
        source["markdown"] = source["markdown"].replace(PROJECT_ROW, f"{PROJECT_ROW}\n{second_row}")
        source["markdown"] += f"\n{second_quote}"
        item = item.model_copy(update={"summary": json.dumps(source, ensure_ascii=False)})
    else:
        del second["project_proposal"]
        second["attention_proposal"]["anchor_id"] = 987
    payload["task_decisions"].append(second)
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
                                       decision=TaskAgentDecision.model_validate(payload), record_run=False)
    first, second = result.attention_proposals
    assert first.anchor_id == store.list_business_projects()[0].canonical_anchor_id
    expected_second_anchor = store.list_business_projects()[1].canonical_anchor_id if new_project else 987
    assert second.anchor_id == expected_second_anchor
    assert second.anchor_id != first.anchor_id


def test_registry_task_excerpt_without_project_proposal_does_not_register(tmp_path):
    store = AutoReplyStore(tmp_path / "no-proposal.sqlite3")
    payload = decision_payload()
    row = payload["task_decisions"][0]
    row["source_excerpt"] = PROJECT_ROW
    del row["project_proposal"]
    del row["attention_proposal"]
    apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(),
                              decision=TaskAgentDecision.model_validate(payload), record_run=False)
    assert store.list_business_projects() == []


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
