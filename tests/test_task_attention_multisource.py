"""Contract fixtures shared by the staged multisource attention implementation."""

import json
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.task_models import TaskAgentDecision, WorkItem, WorkItemSourceType
from app.task_agent import apply_task_agent_decision
from app.store import AutoReplyStore
from app.task_agent import process_work_item
from app.task_attention_projection import BusinessAttentionProjection


def evaluation_tool():
    path = Path(__file__).parents[1] / "scripts/replay_task_attention.py"
    assert path.exists(), "single-input native evaluation tool is missing"
    spec = importlib.util.spec_from_file_location("attention_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_evaluation_cases_have_independent_inputs_and_expectations():
    tool = evaluation_tool()
    cases = tool.load_cases(Path(__file__).parent / "fixtures/task_attention_multisource.json")
    assert [case["case_id"] for case in cases] == [
        "w39-project-risk", "meeting-new-risk", "chat-with-report-context",
        "newer-conflicting-chat", "risk-label-only", "routine-progress",
        "unconfirmed-project", "no-real-task", "same-project-two-actions",
    ]
    for case in cases:
        assert set(case) == {"case_id", "work_item", "existing_context", "expected"}
        WorkItem.model_validate(case["work_item"])
        assert "expected" not in case["work_item"]
        assert "expected" not in case["existing_context"]
        required_refs = case["expected"]["required_source_refs"]
        if case["expected"]["attention_projects"]:
            assert case["work_item"]["source"]["ref"] in required_refs
        else:
            assert required_refs == []
        if case["case_id"] in ("chat-with-report-context", "newer-conflicting-chat"):
            assert "eval:historical-report" in required_refs
        assert ("allowed_task_counts" in case["expected"]) is (
            case["case_id"] in ("w39-project-risk", "meeting-new-risk")
        )


def test_evaluation_replays_exact_input_without_claiming_pending_or_rewriting_runs(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "single.sqlite3")
    item = report_item()
    other = store.enqueue_work_summary_input("project_weekly_report", "other", item.model_dump_json())
    target = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    store.mark_work_summary_input_done(target)
    old_run = store.record_task_agent_run(summary_input_id=target, decision_json='{}')
    with store._connect() as db:
        old = dict(db.execute("select * from task_agent_runs where id=?", (old_run,)).fetchone())

    class Codex:
        def decide(self, **kwargs):
            assert kwargs["session_scope_id"] == tool.EVALUATION_SCOPE
            assert "evaluation_only_secret_expectation" not in kwargs["prompt"]
            return TaskAgentDecision.model_validate(decision_payload())

    from app.task_agent import TaskAgentRunner
    runner = tool.scoped_runner(TaskAgentRunner, Codex())
    result = tool.replay_input(store, runner, target, source_ref=item.source.ref)
    assert result["input_status"] == "done"
    assert result["proposal_count"] == 1
    assert result["persisted_attention_count"] == 1
    assert result["evidence_valid"] is True
    replay = tool.replay_input(store, runner, target, source_ref=item.source.ref)
    assert replay["cards"][0]["id"] == result["cards"][0]["id"]
    assert replay["changes"]["tasks"]["created_ids"] == []
    assert replay["changes"]["projects"]["created_ids"] == []
    assert replay["changes"]["attention_events"]["created_ids"] == []
    assert store.get_work_summary_input(other).status == "pending"
    assert store.get_work_summary_input(target).attempts == 0
    with store._connect() as db:
        assert dict(db.execute("select * from task_agent_runs where id=?", (old_run,)).fetchone()) == old
    with pytest.raises(ValueError, match="source_ref"):
        tool.replay_input(store, runner, target, source_ref="wrong")


def test_evaluation_metrics_use_persisted_cards_and_detect_invalid_evidence(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "metrics.sqlite3")
    seed_report(store)
    before = tool.read_domain(store)
    expected = {"attention_projects": ["示例项目"], "project_titles": ["示例项目"],
                "task_count": 1, "minimum_evidence_sources": 1}
    result = tool.readback(store, input_id=None, before=before, expected=expected)
    assert result["passed"]
    card, = store.list_business_attention_items()
    assessment = json.loads(card.assessment_json)
    assessment["evidence"][0]["source_link"] = "https://example.invalid/invented"
    with store._connect() as db:
        db.execute("update business_attention_items set assessment_json=?", (json.dumps(assessment),))
    assert tool.readback(store, input_id=None, before=before, expected=expected)["evidence_valid"] is False
    with store._connect() as db:
        db.execute("update business_attention_items set assessment_json=?", (json.dumps({
            "evidence": [{"signal_id": 999, "source_ref": "missing", "source_excerpt": "invented"}]
        }),))
    result = tool.readback(store, input_id=None, before=before, expected=expected)
    assert result["evidence_valid"] is False
    assert "unverifiable_attention_evidence" in result["failures"]


@pytest.mark.parametrize("allowed,passes", [([1, 2], True), ([2, 3], False), ([0], False)])
def test_evaluation_allows_only_explicit_reviewed_task_counts(tmp_path, allowed, passes):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "counts.sqlite3")
    seed_report(store)
    expected = {
        "attention_projects": ["示例项目"], "project_titles": ["示例项目"],
        "allowed_task_counts": allowed, "minimum_evidence_sources": 1,
    }
    result = tool.readback(store, input_id=None, before=tool.read_domain(store), expected=expected)
    assert result["passed"] is passes
    assert ("task_count_mismatch" in result["failures"]) is (not passes)


def test_evaluation_refuses_worker_database_before_open(tmp_path, monkeypatch):
    tool = evaluation_tool()
    monkeypatch.setenv("CEO_WORKER_DB", str(tmp_path / "worker.sqlite3"))
    with pytest.raises(ValueError, match="worker database"):
        tool.require_copy(tmp_path / "worker.sqlite3")


def test_fixed_cases_seed_only_existing_facts_and_never_send_expected_labels(tmp_path):
    tool = evaluation_tool()
    cases = tool.load_cases(Path(__file__).parent / "fixtures/task_attention_multisource.json")
    for case in cases:
        store = AutoReplyStore(tmp_path / f'{case["case_id"]}.sqlite3')
        input_id = tool.seed_case(store, case)
        domain = tool.read_domain(store)
        assert len(domain["tasks"]) == len(case["existing_context"]["tasks"])
        assert len(domain["projects"]) == len(case["existing_context"]["projects"])
        assert len(domain["attention"]) == len(case["existing_context"]["attention"])
        case["expected"]["secret_label"] = "evaluation_only_secret_expectation"

        class Codex:
            def decide(self, **kwargs):
                assert "evaluation_only_secret_expectation" not in kwargs["prompt"]
                return TaskAgentDecision.model_validate({"task_decisions": []})

        from app.task_agent import TaskAgentRunner
        result = tool.replay_input(store, tool.scoped_runner(TaskAgentRunner, Codex()),
                                   input_id, source_ref=case["work_item"]["source"]["ref"])
        assert result["run_status"] == "completed"


def test_evaluation_missing_run_cannot_pass_a_negative_case(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "not-run.sqlite3")
    item = report_item()
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store), expected={
        "attention_projects": [], "project_titles": [], "task_count": 0, "minimum_evidence_sources": 1})
    assert result["passed"] is False
    assert "task_agent_run_missing" in result["failures"]


def test_evaluation_new_risk_cannot_pass_with_only_historical_evidence(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "historical-only.sqlite3")
    seed = seed_report(store)
    original_card, = store.list_business_attention_items()
    quote = "示例项目客户取消验收；复核回款计划并增加供应商付款协调。"
    item = WorkItem.model_validate({
        "source": {"type": "ai_minutes", "ref": "meeting:current", "created_at": "2026-10-01T12:00:00Z"},
        "context": {"source_conversation_kind": "minutes"}, "summary": quote,
    })
    payload = decision_payload()
    row = payload["task_decisions"][0]
    row.update(action="update_task", transition="update_fields", task_id=seed.task_ids[0], source_ref=item.source.ref,
               source_excerpt=quote, description=quote)
    del row["project_proposal"]
    row["attention_proposal"]["anchor_id"] = original_card.anchor_id
    row["attention_proposal"]["evidence"][0]["signal_id"] = seed.attention_proposals[0].signal_id

    class Codex:
        def decide(self, **kwargs):
            assert "required_source_refs" not in kwargs["prompt"]
            return TaskAgentDecision.model_validate(payload)

    from app.task_agent import TaskAgentRunner
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    result = tool.replay_input(store, tool.scoped_runner(TaskAgentRunner, Codex()), input_id,
                              source_ref=item.source.ref, expected={
        "attention_projects": ["示例项目"], "project_titles": ["示例项目"], "task_count": 1,
        "minimum_evidence_sources": 1, "reuse_attention": True,
        "required_source_refs": [item.source.ref],
    })
    assert result["run_status"] == "failed"
    assert "requires current null-ID evidence" in result["execution_error"]
    assert store.list_business_attention_items()[0] == original_card
    assert result["passed"] is False


def test_evaluation_requires_source_on_each_target_project_card(tmp_path):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "each-target.sqlite3")
    seed_report(store)
    second_item = report_item()
    second_item = second_item.model_copy(update={
        "source": second_item.source.model_copy(update={"ref": "report:other"}),
        "summary": second_item.summary.replace("示例项目", "另一项目"),
    })
    second_payload = json.loads(json.dumps(decision_payload(), ensure_ascii=False)
                                .replace("示例项目", "另一项目").replace("report:fixture", "report:other"))
    apply_task_agent_decision(store, summary_input_id=2, work_item=second_item,
                             decision=TaskAgentDecision.model_validate(second_payload), record_run=False)
    result = tool.readback(store, input_id=None, before=tool.read_domain(store), expected={
        "attention_projects": ["示例项目", "另一项目"], "project_titles": ["示例项目", "另一项目"],
        "task_count": 2, "minimum_evidence_sources": 1, "required_source_refs": ["report:fixture"],
    })
    assert result["evidence_valid"]
    assert result["passed"] is False
    assert "missing_required_source" in result["failures"]


@pytest.mark.parametrize("include_second_proposal", [False, True])
def test_evaluation_checks_both_same_project_actions_are_attention_members(tmp_path, include_second_proposal):
    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "project-members.sqlite3")
    item = report_item()
    second_quote = "核对示例项目供应商延期付款安排。"
    source = json.loads(item.summary)
    source["markdown"] += "\n" + second_quote
    item = item.model_copy(update={"summary": json.dumps(source, ensure_ascii=False)})
    payload = decision_payload()
    second = decision_payload()["task_decisions"][0]
    second.update(title="核对示例项目供应商延期付款安排", source_excerpt=second_quote)
    if not include_second_proposal:
        del second["attention_proposal"]
    payload["task_decisions"].append(second)

    class Codex:
        def decide(self, **kwargs):
            assert "required_project_member_counts" not in kwargs["prompt"]
            return TaskAgentDecision.model_validate(payload)

    from app.task_agent import TaskAgentRunner
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    result = tool.replay_input(store, tool.scoped_runner(TaskAgentRunner, Codex()), input_id,
                              source_ref=item.source.ref, expected={
        "attention_projects": ["示例项目"], "project_titles": ["示例项目"], "task_count": 2,
        "minimum_evidence_sources": 1, "required_source_refs": [item.source.ref],
        "required_project_member_counts": {"示例项目": 2},
    })
    assert result["run_status"] == "completed", result.get("execution_error")
    assert result["projection"]["status"] == "completed"
    assert result["evidence_valid"]
    assert len(result["tasks"]) == 2
    assert len(result["cards"][0]["task_ids"]) == (2 if include_second_proposal else 1)
    assert result["passed"] is include_second_proposal
    assert ("missing_project_task_member" in result["failures"]) is not include_second_proposal


@pytest.mark.parametrize("status,outcome,recompute_error", [
    ("pending", "applied", ""), ("partial", "applied", ""), ("failed", "applied", ""),
    ("no_proposal", "applied", ""), ("completed", "rejected", ""),
    ("completed", "error", ""), ("completed", "applied", "member recompute failed"),
])
def test_evaluation_valid_old_card_cannot_mask_recorded_projection_failure(tmp_path, status, outcome, recompute_error):
    from app.task_models import TaskAttentionProjectionReceipt, TaskAttentionProjectionOutcome

    tool = evaluation_tool()
    store = AutoReplyStore(tmp_path / "failed-receipt.sqlite3")
    seed = seed_report(store)
    item = report_item()
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    run_id = store.record_task_agent_run(input_id, decision_json=json.dumps(decision_payload()))
    before = tool.read_domain(store)
    expected = {"attention_projects": ["示例项目"], "project_titles": ["示例项目"],
                "task_count": 1, "minimum_evidence_sources": 1, "required_source_refs": [item.source.ref]}
    # An actual baseline run without the new receipt remains evaluable.
    assert tool.readback(store, input_id=input_id, before=before, expected=expected)["passed"]
    card, = store.list_business_attention_items()
    receipt = TaskAttentionProjectionReceipt(
        status=status, source_type=item.source.type.value, task_decision_count=1,
        project_link_count=1, registry_row_count=1, proposal_count=1, applied_count=1,
        outcomes=[TaskAttentionProjectionOutcome(task_id=seed.task_ids[0], anchor_id=card.anchor_id,
                   attention_id=card.id, status=outcome, reason="persisted evaluation failure")],
        recompute_error=recompute_error,
    )
    store.record_task_agent_projection(run_id, receipt.model_dump_json())
    result = tool.readback(store, input_id=input_id, before=before, expected=expected)
    assert result["evidence_valid"]
    assert result["passed"] is False
    assert "projection_not_successful" in result["failures"]


PROJECT_ROW = "| 示例项目 | 回款复核 | 降低现金流风险 | 09-30 | 有风险 |"
RISK_QUOTE = "已交付收入因客户确认延迟，尚未进入当期确认，供应商付款需要协调。"
TASK_QUOTE = "复核示例项目回款及供应商付款计划。"


def report_item():
    return WorkItem.model_validate({
        "source": {"type": "project_weekly_report", "ref": "report:fixture", "created_at": "2026-09-30T12:00:00Z"},
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
            "anchor_id": None, "assessment_basis": "current_observation", "material_trigger": "risk_escalation",
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


def test_report_risk_outside_action_projects_one_stable_card_with_verified_assessment(tmp_path):
    store = AutoReplyStore(tmp_path / "risk.sqlite3")
    decision = TaskAgentDecision.model_validate(decision_payload())
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(),
                                      decision=decision, record_run=False)
    card, = store.list_business_attention_items()
    project, = store.list_business_projects()
    assert card.stable_key == f"project:{project.canonical_anchor_id}"
    assert "当前无需你处理" in card.ceo_action
    assessment = json.loads(card.assessment_json)
    assert assessment["inference"] == decision.task_decisions[0].attention_proposal.why_attention
    assert assessment["evidence"] == [{
        "signal_id": result.attention_proposals[0].signal_id,
        "source_ref": "report:fixture", "source_excerpt": RISK_QUOTE,
        "source_time": "2026-09-30T12:00:00Z", "source_link": "",
    }]
    receipt = result.projection_receipt
    assert (receipt.status, receipt.proposal_count, receipt.applied_count,
            receipt.project_link_count, receipt.registry_row_count) == ("completed", 1, 1, 1, 1)
    replay = apply_task_agent_decision(store, summary_input_id=2, work_item=report_item(),
                                      decision=decision, record_run=False)
    assert replay.task_ids == result.task_ids
    assert store.list_business_projects() == [project]
    assert store.list_business_attention_items() == (card,)
    assert len(store.list_business_attention_events(card.id)) == 1


def seed_report(store):
    return apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(),
                                    decision=TaskAgentDecision.model_validate(decision_payload()), record_run=False)


def update_risk(store, seed, *, source_type="reply_attempt", evidence=None, related_ids=None):
    quote = "客户确认进一步延迟，付款协调仍在推进。"
    item = WorkItem.model_validate({
        "source": {"type": source_type, "ref": f"{source_type}:new", "created_at": "2026-10-01T15:00:00Z"},
        "context": {"source_conversation_kind": "group"}, "summary": quote,
    })
    proposal = decision_payload()["task_decisions"][0]["attention_proposal"]
    citations = evidence or [{"source_ref": item.source.ref, "source_excerpt": quote}]
    historical = any(entry.get("signal_id") is not None for entry in citations)
    if historical and not any(entry.get("signal_id") is None for entry in citations):
        citations = [{"source_ref": item.source.ref, "source_excerpt": quote}, *citations]
    proposal.update(anchor_id=seed.attention_proposals[0].anchor_id,
                    assessment_basis="historical_comparison" if historical else "current_observation",
                    evidence=citations,
                    related_task_ids=related_ids or [])
    decision = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": seed.task_ids[0],
        "source_ref": item.source.ref, "source_excerpt": quote, "description": quote,
        "title": store.get_business_task(seed.task_ids[0]).title,
        "attention_proposal": proposal,
    }]})
    return item, decision


@pytest.mark.parametrize("fault", ["missing", "unlinked", "crossproject", "forged", "wrong_ref", "session_provenance", "memory_provenance"])
def test_invalid_historical_citation_rejects_whole_proposal(tmp_path, fault):
    store = AutoReplyStore(tmp_path / f"{fault}.sqlite3")
    seed = seed_report(store)
    if fault == "missing":
        signal_id = 99999
    elif fault in {"forged", "wrong_ref"}:
        signal_id = seed.attention_proposals[0].signal_id
    else:
        signal_id = store.create_business_task_signal(source_type=fault, source_ref="report:fixture",
            evidence_text=RISK_QUOTE, dedupe_key=fault)
        if fault in {"session_provenance", "memory_provenance"}:
            store.link_business_task_evidence(task_id=seed.task_ids[0], signal_id=signal_id, evidence_role="discovery")
        if fault == "crossproject":
            other = seed_report_other_project(store)
            store.link_business_task_evidence(task_id=other.task_ids[0], signal_id=signal_id, evidence_role="discovery")
    evidence = [{"signal_id": signal_id, "source_ref": "report:forged-ref" if fault == "wrong_ref" else "report:fixture",
                 "source_excerpt": "不存在的风险原文" if fault == "forged" else RISK_QUOTE}]
    item, decision = update_risk(store, seed, evidence=evidence)
    result = apply_task_agent_decision(store, summary_input_id=2, work_item=item, decision=decision, record_run=False)
    assert result.projection_receipt.status == "failed"
    assert result.projection_receipt.outcomes[0].status == "rejected"
    assert len(store.list_business_attention_events(1)) == 1


def seed_report_other_project(store):
    item = report_item()
    payload = decision_payload()
    item = item.model_copy(update={"summary": item.summary.replace("示例项目", "另一个项目"),
        "source": item.source.model_copy(update={"ref": "report:other"})})
    payload["task_decisions"][0]["source_ref"] = "report:other"
    row = payload["task_decisions"][0]
    row["title"] = row["title"].replace("示例项目", "另一个项目")
    row["source_excerpt"] = row["source_excerpt"].replace("示例项目", "另一个项目")
    row["project_proposal"].update(title="另一个项目", source_excerpt=PROJECT_ROW.replace("示例项目", "另一个项目"))
    row["attention_proposal"]["evidence"][0]["source_ref"] = "report:other"
    return apply_task_agent_decision(store, summary_input_id=3, work_item=item, decision=TaskAgentDecision.model_validate(payload), record_run=False)


def test_crossproject_related_task_rejects_proposal(tmp_path):
    store = AutoReplyStore(tmp_path / "cross-related.sqlite3")
    seed, other = seed_report(store), seed_report_other_project(store)
    item, decision = update_risk(store, seed, related_ids=[other.task_ids[0]])
    result = apply_task_agent_decision(store, summary_input_id=2, work_item=item, decision=decision, record_run=False)
    assert result.projection_receipt.status == "failed"


@pytest.mark.parametrize("distinct", [False, True])
def test_same_project_multiple_proposals_fold_only_identical_content(tmp_path, distinct):
    store = AutoReplyStore(tmp_path / "multiple.sqlite3")
    payload = decision_payload()
    second = decision_payload()["task_decisions"][0]
    second["title"] = "复核付款协调行动"
    if distinct:
        second["attention_proposal"]["category"] = "decision"
    else:
        for row in (payload["task_decisions"][0], second):
            row["attention_proposal"]["evidence"].append(
                dict(row["attention_proposal"]["evidence"][0]))
    payload["task_decisions"].append(second)
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(), decision=TaskAgentDecision.model_validate(payload), record_run=False)
    receipt = result.projection_receipt
    assert receipt.proposal_count == 2
    assert receipt.status == ("failed" if distinct else "completed")
    assert receipt.applied_count == (0 if distinct else 1)
    if distinct:
        assert all("multiple distinct proposals" in outcome.reason for outcome in receipt.outcomes)
        assert store.list_business_attention_items() == ()
    else:
        card, = store.list_business_attention_items()
        assert {link.task_id for link in store.list_business_attention_tasks(card.id)} == set(result.task_ids)
        evidence = json.loads(card.assessment_json)["evidence"]
        assert evidence == [{
            "signal_id": entry.signal_id, "source_ref": "report:fixture",
            "source_excerpt": RISK_QUOTE, "source_time": "2026-09-30T12:00:00Z",
            "source_link": "",
        } for entry in result.attention_proposals]
        assert len({entry["signal_id"] for entry in evidence}) == 2
        assert card.evidence_signal_id == result.attention_proposals[0].signal_id
        assert len(store.list_business_attention_events(card.id)) == 1
        replay = apply_task_agent_decision(store, summary_input_id=2, work_item=report_item(),
            decision=TaskAgentDecision.model_validate(payload), record_run=False)
        assert replay.projection_receipt.applied_count == 1
        assert store.get_business_attention_item(card.id).assessment_json == card.assessment_json
        assert len(store.list_business_attention_events(card.id)) == 1


@pytest.mark.parametrize("failure", ["upsert", "receipt", "recompute"])
def test_projection_failure_preserves_committed_task_input_and_run(tmp_path, monkeypatch, failure):
    store = AutoReplyStore(tmp_path / f"commit-{failure}.sqlite3")
    item = report_item()
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    work_input, = store.claim_work_summary_inputs(limit=1)
    class Runner:
        codex = type("Codex", (), {"last_session_id": ""})()
        def decide(self, *args, **kwargs):
            return TaskAgentDecision.model_validate(decision_payload())
    def fail(*args, **kwargs):
        raise RuntimeError(f"{failure} unavailable")
    if failure == "receipt":
        original = store.record_task_agent_projection
        def final_fail(run_id, projection_json, **kwargs):
            if json.loads(projection_json)["status"] != "pending":
                fail()
            return original(run_id, projection_json, **kwargs)
        monkeypatch.setattr(store, "record_task_agent_projection", final_fail)
    else:
        monkeypatch.setattr(BusinessAttentionProjection, "upsert" if failure == "upsert" else "recompute_for_tasks", fail)
    process_work_item(store, Runner(), work_input)
    assert store.get_work_summary_input(input_id).status.value == "done"
    assert len(store.list_business_tasks()) == 1
    with store._connect() as db:
        run, = db.execute("select * from task_agent_runs").fetchall()
    assert run["status"] == "completed"
    receipt = json.loads(run["projection_json"])
    assert receipt["status"] == {"upsert": "failed", "receipt": "pending", "recompute": "partial"}[failure]
    if failure == "recompute":
        assert receipt["recompute_error"] == "recompute unavailable"


def test_registry_count_without_project_or_attention_proposal(tmp_path):
    store = AutoReplyStore(tmp_path / "counts.sqlite3")
    payload = decision_payload()
    del payload["task_decisions"][0]["project_proposal"]
    del payload["task_decisions"][0]["attention_proposal"]
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(), decision=TaskAgentDecision.model_validate(payload), record_run=False)
    assert result.projection_receipt.status == "no_proposal"
    assert result.projection_receipt.registry_row_count == 1
    assert result.projection_receipt.project_link_count == 0


@pytest.mark.parametrize("source_type", ["ai_minutes", "reply_attempt"])
def test_new_source_risk_reuses_project_card_and_preserves_report_fields(tmp_path, source_type):
    store = AutoReplyStore(tmp_path / f"new-{source_type}.sqlite3")
    seed = seed_report(store)
    project, = store.list_business_projects()
    old_card, = store.list_business_attention_items()
    item, decision = update_risk(store, seed, source_type=source_type)
    if source_type == "reply_attempt":
        historical = {"signal_id": seed.attention_proposals[0].signal_id,
                      "source_ref": "report:fixture", "source_excerpt": RISK_QUOTE}
        proposal = decision.task_decisions[0].attention_proposal
        proposal.evidence.append(type(proposal.evidence[0]).model_validate(historical))
        proposal.assessment_basis = "historical_comparison"
    result = apply_task_agent_decision(store, summary_input_id=2, work_item=item, decision=decision, record_run=False)
    assert result.projection_receipt.status == "completed"
    assert result.projection_receipt.registry_row_count is None
    assert result.projection_receipt.project_link_count == 0
    card, = store.list_business_attention_items()
    assert card.id == old_card.id
    assert store.list_business_projects() == [project]
    assert store.get_business_task(seed.task_ids[0]).description == item.summary
    evidence = json.loads(card.assessment_json)["evidence"]
    assert json.loads(card.assessment_json)["assessment_basis"] == (
        "historical_comparison" if source_type == "reply_attempt" else "current_observation")
    assert evidence[0]["source_time"] == "2026-10-01T15:00:00Z"
    if source_type == "reply_attempt":
        assert evidence[1]["source_time"] == "2026-09-30T12:00:00Z"
        assert evidence[1]["signal_id"] != evidence[0]["signal_id"]


def test_later_proposal_preserves_open_sibling_and_completion_only_removes_member(tmp_path):
    store = AutoReplyStore(tmp_path / "siblings.sqlite3")
    payload = decision_payload()
    second = decision_payload()["task_decisions"][0]
    second["title"] = "复核付款协调行动"
    payload["task_decisions"].append(second)
    seed = apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(), decision=TaskAgentDecision.model_validate(payload), record_run=False)
    card, = store.list_business_attention_items()
    item, decision = update_risk(store, seed)
    apply_task_agent_decision(store, summary_input_id=2, work_item=item, decision=decision, record_run=False)
    assert {link.task_id for link in store.list_business_attention_tasks(card.id)} == set(seed.task_ids)
    completion_item = item.model_copy(update={"summary": "回款复核已经完成。", "source": item.source.model_copy(update={"ref": "chat:completed", "created_at": "2026-10-02T16:00:00Z"})})
    completion = TaskAgentDecision.model_validate({"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": seed.task_ids[0],
        "title": store.get_business_task(seed.task_ids[0]).title,
        "source_ref": completion_item.source.ref, "source_excerpt": completion_item.summary, "status": "done",
    }]})
    apply_task_agent_decision(store, summary_input_id=3, work_item=completion_item, decision=completion, record_run=False)
    assert {link.task_id for link in store.list_business_attention_tasks(card.id)} == {seed.task_ids[1]}
    assert store.get_business_attention_item(card.id).status.value == "active"


def test_quote_cannot_join_decoded_json_leaves():
    from app.task_agent import source_contains_quote
    raw = json.dumps({"parts": ["收入确认延迟", "供应商付款受影响"]}, ensure_ascii=True)
    assert source_contains_quote(raw, "收入确认延迟")
    assert not source_contains_quote(raw, "收入确认延迟供应商付款受影响")
    assert not source_contains_quote(raw, " ")


def test_skipped_noop_proposal_is_counted_and_diagnosed(tmp_path):
    store = AutoReplyStore(tmp_path / "noop.sqlite3")
    seed = seed_report(store)
    item, decision = update_risk(store, seed)
    first = apply_task_agent_decision(store, summary_input_id=2, work_item=item, decision=decision, record_run=False)
    replay = apply_task_agent_decision(store, summary_input_id=3, work_item=item, decision=decision, record_run=False)
    assert first.projection_receipt.status == "completed"
    assert replay.projection_receipt.task_decision_count == 1
    assert replay.projection_receipt.proposal_count == 1
    # Replay of an existing signal remains a valid replay; a new source without
    # actual field changes is skipped by the Task domain instead.
    newer = item.model_copy(update={"source": item.source.model_copy(update={"ref": "chat:no-change"})})
    row = decision.task_decisions[0].model_copy(update={"source_ref": newer.source.ref})
    skipped = apply_task_agent_decision(store, summary_input_id=4, work_item=newer, decision=decision.model_copy(update={"task_decisions": [row]}), record_run=False)
    assert skipped.projection_receipt.status == "failed"
    assert skipped.projection_receipt.proposal_count == 1
    assert "no applied Task" in skipped.projection_receipt.outcomes[0].reason


def test_raw_skip_proposal_does_not_invent_a_task_id(tmp_path):
    store = AutoReplyStore(tmp_path / "skip-proposal.sqlite3")
    row = decision_payload()["task_decisions"][0]
    row.update(action="skip", skip_reason="No actionable Task")
    row.pop("project_proposal")
    row["attention_proposal"]["anchor_id"] = 9
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(), decision=TaskAgentDecision.model_validate({"task_decisions": [row]}), record_run=False)
    assert result.projection_receipt.proposal_count == 1
    assert result.projection_receipt.status == "failed"
    assert result.projection_receipt.outcomes[0].task_id is None


def test_folded_group_checks_each_current_signal_not_only_first(tmp_path):
    from app.task_agent import AppliedTaskAttention, _project_task_attention
    from app.task_models import TaskAttentionProjectionReceipt
    store = AutoReplyStore(tmp_path / "folded-provenance.sqlite3")
    seed = seed_report(store)
    applied = seed.attention_proposals[0]
    cited = store.create_business_task_signal(source_type="session_provenance", source_ref="report:fixture", evidence_text=RISK_QUOTE, dedupe_key="cited")
    store.link_business_task_evidence(task_id=applied.task_id, signal_id=cited, evidence_role="discovery")
    receipt = _project_task_attention(store,
        (applied, AppliedTaskAttention(applied.decision, applied.task_id, cited, applied.anchor_id)),
        (applied.task_id,), receipt=TaskAttentionProjectionReceipt(
            status="pending", source_type="project_weekly_report", task_decision_count=2,
            project_link_count=0, proposal_count=2,
        ))
    assert receipt.status == "failed"
    assert all(outcome.status == "rejected" for outcome in receipt.outcomes)


def test_report_plain_text_with_no_registry_has_zero_rows(tmp_path):
    store = AutoReplyStore(tmp_path / "plain.sqlite3")
    item = report_item().model_copy(update={"summary": TASK_QUOTE})
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({"task_decisions": []}), record_run=False)
    assert result.projection_receipt.registry_row_count == 0
    assert result.projection_receipt.status == "no_proposal"


def test_registered_project_without_attention_still_counts_applied_link(tmp_path):
    store = AutoReplyStore(tmp_path / "project-only.sqlite3")
    payload = decision_payload()
    del payload["task_decisions"][0]["attention_proposal"]
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=report_item(), decision=TaskAgentDecision.model_validate(payload), record_run=False)
    assert result.projection_receipt.project_link_count == 1
    assert result.projection_receipt.proposal_count == result.projection_receipt.applied_count == 0
    assert result.projection_receipt.status == "no_proposal"


def test_recorded_direct_apply_saves_receipt_on_actual_run(tmp_path):
    store = AutoReplyStore(tmp_path / "direct-run.sqlite3")
    result = apply_task_agent_decision(store, summary_input_id=8, work_item=report_item(), decision=TaskAgentDecision.model_validate(decision_payload()))
    with store._connect() as db:
        run, = db.execute("select * from task_agent_runs").fetchall()
    assert run["summary_input_id"] == 8
    assert run["status"] == "completed"
    assert json.loads(run["projection_json"]) == result.projection_receipt.model_dump()


@pytest.mark.parametrize("project_header", ["项目名", "项目名称", "业务项目", "工作流", "项目/方向"])
def test_registry_row_count_excludes_repeated_table_separators(tmp_path, project_header):
    store = AutoReplyStore(tmp_path / "registry-count.sqlite3")
    item = report_item()
    payload = json.loads(item.summary)
    payload["markdown"] = payload["markdown"].replace(PROJECT_ROW,
        PROJECT_ROW + f"\n| {project_header} | 负责内容 | 目标 | DDL | 状态 |\n"
        "| :--- | ---: | --- | --- | --- |\n" + PROJECT_ROW.replace("示例项目", "第二个项目"))
    item = item.model_copy(update={"summary": json.dumps(payload, ensure_ascii=False)})
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({"task_decisions": []}), record_run=False)
    assert result.projection_receipt.registry_row_count == 2


@pytest.mark.parametrize("project_header", ["项目名称", "业务项目", "工作流", "项目/方向"])
def test_repeated_registry_header_cannot_register_project(tmp_path, project_header):
    store = AutoReplyStore(tmp_path / "header-project.sqlite3")
    item = report_item()
    source = json.loads(item.summary)
    header = f"| {project_header} | 负责内容 | 目标 | DDL | 状态 |"
    source["markdown"] = source["markdown"].replace(
        PROJECT_ROW, PROJECT_ROW + "\n" + header + "\n| --- | --- | --- | --- | --- |"
    )
    item = item.model_copy(update={"summary": json.dumps(source, ensure_ascii=False)})
    payload = decision_payload()
    payload["task_decisions"][0]["project_proposal"].update(
        title=project_header, source_excerpt=header
    )
    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item,
            decision=TaskAgentDecision.model_validate(payload), record_run=False)
    assert store.list_business_tasks() == ()
    assert store.list_business_projects() == []
    assert store.list_business_attention_items() == ()


def test_project_link_count_includes_confirmed_candidate_cluster_members(tmp_path):
    from app.task_business_resolution import BusinessResolutionService

    store = AutoReplyStore(tmp_path / "cluster-link-count.sqlite3")
    item = report_item()
    first = decision_payload()["task_decisions"][0]
    first.pop("project_proposal")
    first.pop("attention_proposal")
    second = {**first, "title": "复核付款协调行动"}
    seed = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({"task_decisions": [first, second]}), record_run=False)
    resolution = BusinessResolutionService(store)
    cluster_id = resolution.create_cluster(title="示例项目", task_ids=list(seed.task_ids))
    resolution.propose_project(cluster_id=cluster_id, title="示例项目", reason="已有项目候选")
    row = decision_payload()["task_decisions"][0]
    row.update(action="update_task", transition="update_fields", task_id=seed.task_ids[0],
        description="新来源补充回款复核细节",
        cluster_proposal={"cluster_id": cluster_id, "task_ids": list(seed.task_ids), "reason": "同一聚类"})
    row.pop("attention_proposal")
    result = apply_task_agent_decision(store, summary_input_id=2, work_item=item,
        decision=TaskAgentDecision.model_validate({"task_decisions": [row]}), record_run=False)
    project, = store.list_business_projects()
    expected = {(task_id, project.canonical_anchor_id) for task_id in seed.task_ids}
    with store._connect() as db:
        actual = {tuple(link) for link in db.execute(
            "select task_id, anchor_id from business_task_anchor_links where status='confirmed' and active=1"
        )}
    assert actual == expected
    assert set(result.project_links) == expected
    assert result.projection_receipt.project_link_count == 2
    assert result.projection_receipt.proposal_count == 0


def test_conflicting_chat_risk_does_not_replace_report_registry_summary(tmp_path):
    from app.web_api.tasks import business_project_detail
    store = AutoReplyStore(tmp_path / "conflicting-chat.sqlite3")
    seed = seed_report(store)
    project, = store.list_business_projects()
    before = business_project_detail(store, project.id).summary
    assert before.goal == "降低现金流风险"
    assert before.current_status == "有风险"
    item, decision = update_risk(store, seed)
    summary = item.summary + " 聊天提出目标改为扩张销售、状态恢复正常、DDL 改为 12-31。"
    item = item.model_copy(update={"summary": summary})
    row = decision.task_decisions[0].model_copy(update={"description": summary})
    result = apply_task_agent_decision(store, summary_input_id=2, work_item=item,
        decision=decision.model_copy(update={"task_decisions": [row]}), record_run=False)
    assert result.projection_receipt.status == "completed"
    after = business_project_detail(store, project.id).summary
    assert after == before
    card, = store.list_business_attention_items()
    assert json.loads(card.assessment_json)["evidence"][0]["source_ref"] == item.source.ref


def test_upsert_value_error_is_an_application_error_not_a_quote_rejection(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "upsert-value-error.sqlite3")
    def fail(*args, **kwargs):
        raise ValueError("card write rejected")
    monkeypatch.setattr(BusinessAttentionProjection, "upsert", fail)
    result = seed_report(store)
    assert result.projection_receipt.status == "failed"
    assert result.projection_receipt.outcomes[0].status == "error"
    assert result.projection_receipt.outcomes[0].reason == "card write rejected"
    assert len(store.list_business_tasks()) == 1


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
        seed = seed_report(store)
        del second["project_proposal"]
        second.update(action="update_task", transition="update_fields", task_id=seed.task_ids[0])
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
    decision["attention_proposal"].update(assessment_basis="historical_comparison")
    decision["attention_proposal"]["evidence"].append({"signal_id": 11,
        "source_ref": "report:earlier", "source_excerpt": RISK_QUOTE})
    decision["project_link_proposal"] = {"anchor_id": 8, "source_excerpt": TASK_QUOTE, "reason": "当前项目行动"}
    parsed = TaskAgentDecision.model_validate(payload).task_decisions[0]
    assert parsed.attention_proposal.related_task_ids == [3, 9]
    assert parsed.attention_proposal.evidence[1].signal_id == 11


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


def existing_project_link_case(tmp_path, source_type="ai_minutes"):
    from app.task_business_resolution import BusinessResolutionService
    from app.task_semantic_service import RecordCandidate, SourceSignal, TaskSemanticService
    from app.task_attention_projection import AttentionProposal

    store = AutoReplyStore(tmp_path / "existing-project-link.sqlite3")
    seed = TaskSemanticService(store).record_candidate(RecordCandidate(
        title="复核示例交付项目验收计划", description="复核已登记项目的验收计划。",
        signal=SourceSignal(source_type="project_weekly_report", source_ref="report:existing",
            evidence_text="复核示例交付项目验收计划。验收延期可能影响回款。", dedupe_key="report:existing"),
    ))
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(anchor_type="project", anchor_ref="project:existing", title="示例交付项目")
    resolution.register_official_project(anchor_id=anchor_id, registry_source="report:existing")
    resolution.confirm_anchor_match(task_id=seed.task_id, anchor_id=anchor_id, evidence_signal_id=seed.signal_id)
    card_id = BusinessAttentionProjection(store).upsert(AttentionProposal(
        stable_key=f"project:{anchor_id}", category="watch", title="原验收风险", business_area="示例交付项目",
        why_attention="验收延期影响回款", current_state="验收延期", ceo_action="当前无需处理", anchor_id=anchor_id,
        task_ids=(seed.task_id,), evidence_signal_id=seed.signal_id,
    ))
    action = "复核示例交付项目供应商延期付款安排。"
    risk = "示例交付项目客户取消验收，回款延期导致供应商本周款项无法支付。"
    item = WorkItem.model_validate({
        "source": {"type": source_type, "ref": "source:project-risk", "created_at": "2026-10-02T12:00:00Z"},
        "context": {"source_conversation_kind": "minutes" if source_type == "ai_minutes" else "group"},
        "summary": f"{risk}\n复核示例交付项目验收计划，补充客户取消验收后的调整。\n{action}",
    })
    assessment = {"category": "watch", "title": "验收取消与付款风险", "why_attention": "验收取消及回款延期影响付款",
        "current_state": risk, "ceo_action": "当前无需你处理；观察延期付款安排", "anchor_id": anchor_id,
        "assessment_basis": "current_observation", "material_trigger": "risk_escalation", "evidence": [{"source_ref": item.source.ref, "source_excerpt": risk}]}
    new_task = {"action": "record_candidate", "transition": "none", "title": action[:-1],
        "source_ref": item.source.ref, "source_excerpt": action, "description": action,
        "project_link_proposal": {"anchor_id": anchor_id, "source_excerpt": action, "reason": "当前行动明确指出已有项目"},
        "attention_proposal": assessment}
    return store, item, seed, anchor_id, card_id, new_task


@pytest.mark.parametrize("source_type", ["ai_minutes", "reply_attempt"])
def test_current_source_links_new_task_to_existing_project_and_updates_one_card(tmp_path, source_type):
    from app.task_agent import TaskAgentRunner

    store, item, seed, anchor_id, old_card_id, new_task = existing_project_link_case(tmp_path, source_type)
    registered_projects = store.list_business_projects()
    payload = {"task_decisions": [{
        "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "title": "复核示例交付项目验收计划", "description": "客户取消验收后复核计划并补充调整方案。",
        "source_ref": item.source.ref, "source_excerpt": "复核示例交付项目验收计划，补充客户取消验收后的调整。",
        "attention_proposal": new_task["attention_proposal"],
    }, new_task]}
    class Codex:
        def decide(self, **kwargs):
            return TaskAgentDecision.model_validate(payload)
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    process_work_item(store, TaskAgentRunner(Codex()), store.claim_work_summary_inputs(limit=1)[0])
    card, = store.list_business_attention_items()
    assert card.id == old_card_id
    tasks = store.list_business_tasks()
    assert len(tasks) == 2
    assert all(task.stage.value == "candidate" and task.business_relevance.value == "relevant" for task in tasks)
    assert {link.task_id for link in store.list_business_attention_tasks(card.id)} == {task.id for task in tasks}
    assert all(quote["source_ref"] == item.source.ref for quote in json.loads(card.assessment_json)["evidence"])
    assert store.list_business_projects() == registered_projects
    with store._connect() as db:
        receipt = json.loads(db.execute("select projection_json from task_agent_runs where summary_input_id=?", (input_id,)).fetchone()[0])
        link = db.execute("select * from business_task_anchor_links where task_id<>?", (seed.task_id,)).fetchone()
    assert receipt["status"] == "completed"
    assert receipt["project_link_count"] == 1
    assert link["status"] == "confirmed" and link["anchor_id"] == anchor_id
    replay = apply_task_agent_decision(store, summary_input_id=input_id, work_item=item,
        decision=TaskAgentDecision.model_validate({"task_decisions": [new_task]}), record_run=False)
    assert replay.projection_receipt.status == "completed"
    assert store.list_business_tasks() == tasks
    assert store.list_business_attention_items()[0].id == old_card_id


def test_new_task_relation_and_compound_project_link_use_actual_current_id(tmp_path):
    from app.task_agent import TaskAgentRunner

    store, item, seed, anchor_id, old_card_id, new_task = existing_project_link_case(tmp_path)
    existing = store.get_business_task(seed.task_id)
    compound = "复核示例交付项目验收计划，并补充验收取消后的供应商延期付款安排。"
    item = item.model_copy(update={"summary": f"{new_task['attention_proposal']['current_state']}\n{compound}"})
    new_task.update(title="补充供应商延期付款安排", source_excerpt="补充验收取消后的供应商延期付款安排",
        description="补充验收取消后的供应商延期付款安排")
    new_task["project_link_proposal"]["source_excerpt"] = compound
    new_task["relation_proposals"] = [{"related_task_id": seed.task_id,
        "direction": "current_to_related", "relation_type": "supports", "reason": "付款安排支持既有验收交付"}]
    new_task["attention_proposal"]["related_task_ids"] = [seed.task_id]
    old_update = {"action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "title": existing.title, "description": existing.description, "source_ref": item.source.ref,
        "source_excerpt": "复核示例交付项目验收计划"}
    payload = {"task_decisions": [old_update, new_task]}
    class Codex:
        def decide(self, **kwargs):
            return TaskAgentDecision.model_validate(payload)
    input_id = store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
    process_work_item(store, TaskAgentRunner(Codex()), store.claim_work_summary_inputs(limit=1)[0])
    tasks = store.list_business_tasks()
    assert len(tasks) == 2
    assert store.get_business_task(seed.task_id) == existing
    current = next(task for task in tasks if task.id != seed.task_id)
    relation, = store.list_business_task_relations(task_id=current.id)
    assert (relation.from_task_id, relation.to_task_id) == (current.id, seed.task_id)
    assert relation.status.value == "proposed"
    card, = store.list_business_attention_items()
    assert card.id == old_card_id and card.anchor_id == anchor_id
    assert {link.task_id for link in store.list_business_attention_tasks(card.id)} == {seed.task_id, current.id}
    with store._connect() as db:
        receipt = json.loads(db.execute("select projection_json from task_agent_runs where summary_input_id=?", (input_id,)).fetchone()[0])
    assert receipt["status"] == "completed"


@pytest.mark.parametrize("failure", ["missing", "unofficial", "inactive", "cross_project", "wrong_quote", "wrong_task_quote", "source_ref"])
def test_existing_project_link_rejects_unproven_target_or_current_action(tmp_path, failure):
    from app.task_business_resolution import BusinessResolutionService

    store, item, seed, anchor_id, card_id, payload = existing_project_link_case(tmp_path)
    resolution = BusinessResolutionService(store)
    if failure == "missing":
        payload["project_link_proposal"]["anchor_id"] = 99999
    elif failure in {"unofficial", "cross_project"}:
        other = resolution.register_anchor(anchor_type="project", anchor_ref="project:other", title="其他交付项目")
        if failure == "cross_project":
            resolution.register_official_project(anchor_id=other, registry_source="report:other")
        payload["project_link_proposal"]["anchor_id"] = other
    elif failure == "inactive":
        with store._connect() as db:
            db.execute("update business_anchors set active=0 where id=?", (anchor_id,))
    elif failure == "wrong_quote":
        payload["project_link_proposal"]["source_excerpt"] = "示例交付项目不存在于当前来源的关联句。"
    elif failure == "wrong_task_quote":
        payload["project_link_proposal"]["source_excerpt"] = item.summary.splitlines()[0]
    else:
        payload["source_ref"] = "other:source"
    payload["attention_proposal"] = None
    decision = TaskAgentDecision.model_validate({"task_decisions": [payload]})
    with pytest.raises(ValueError):
        apply_task_agent_decision(store, summary_input_id=1, work_item=item, decision=decision, record_run=False)
    assert len(store.list_business_tasks()) == 1
    assert len(store.list_business_task_signals()) == 1


def test_restating_update_does_not_apply_existing_project_link_or_attention(tmp_path):
    store, item, seed, anchor_id, card_id, new_task = existing_project_link_case(tmp_path)
    task = store.get_business_task(seed.task_id)
    payload = {**new_task, "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "title": task.title, "description": task.description}
    with store._connect() as db:
        before = tuple(db.execute("select evidence_signal_id,reason from business_task_anchor_links").fetchone())
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({"task_decisions": [payload]}), record_run=False)
    assert result.task_ids == ()
    assert result.project_links == ()
    assert result.projection_receipt.status == "failed"
    assert result.projection_receipt.outcomes[0].status == "rejected"
    with store._connect() as db:
        assert tuple(db.execute("select evidence_signal_id,reason from business_task_anchor_links").fetchone()) == before
    assert store.get_business_attention_item(card_id).title == "原验收风险"


def test_existing_unconfirmed_task_can_confirm_project_link_with_real_source_update(tmp_path):
    store, item, seed, anchor_id, card_id, new_task = existing_project_link_case(tmp_path)
    quote = item.summary.splitlines()[1]
    with store._connect() as db:
        db.execute("update business_task_anchor_links set status='proposed' where task_id=?", (seed.task_id,))
        db.execute("update business_tasks set business_relevance='unknown' where id=?", (seed.task_id,))
    payload = {**new_task, "action": "update_task", "transition": "update_fields", "task_id": seed.task_id,
        "title": "复核示例交付项目验收计划", "description": "客户取消验收后调整验收计划。", "source_excerpt": quote,
        "project_link_proposal": {"anchor_id": anchor_id, "source_excerpt": quote, "reason": "当前行动明确属于已登记项目"}}
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({"task_decisions": [payload]}), record_run=False)
    assert result.project_links == ((seed.task_id, anchor_id),)
    assert result.projection_receipt.status == "completed"
    assert store.get_business_task(seed.task_id).business_relevance.value == "relevant"
    with store._connect() as db:
        assert db.execute("select status from business_task_anchor_links where task_id=?", (seed.task_id,)).fetchone()[0] == "confirmed"


def test_uncertain_anchor_match_remains_proposed_without_deriving_relevance(tmp_path):
    store, item, seed, anchor_id, card_id, payload = existing_project_link_case(tmp_path)
    del payload["project_link_proposal"]
    payload["attention_proposal"] = None
    payload["anchor_match_proposals"] = [{"anchor_id": anchor_id, "reason": "尚待确认的相关性"}]
    result = apply_task_agent_decision(store, summary_input_id=1, work_item=item,
        decision=TaskAgentDecision.model_validate({"task_decisions": [payload]}), record_run=False)
    new_id = result.task_ids[0]
    assert result.project_links == ()
    assert store.get_business_task(new_id).business_relevance.value == "unknown"
    with store._connect() as db:
        assert db.execute("select status from business_task_anchor_links where task_id=?", (new_id,)).fetchone()[0] == "proposed"
    assert {link.task_id for link in store.list_business_attention_tasks(card_id)} == {seed.task_id}
