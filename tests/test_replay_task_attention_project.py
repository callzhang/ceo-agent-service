"""Evaluation readback follows persisted Project evidence, not Task carriers."""

import importlib.util
import json
import sqlite3
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.project_context_service import ProjectContextService
from app.store import AutoReplyStore
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import AttentionCategory
from app.task_models import TaskAgentDecision
from tests.support.task_native import task_native_records  # noqa: F401


def _assessment(*, proposal=False, anchor_id=1, evidence=None):
    evidence = evidence or [{"signal_id": None, "source_ref": "message:readback",
                             "source_excerpt": "验收日期未确定，影响本期回款", "source_time": "", "source_link": ""}]
    current = [{"signal_id": None, "source_ref": item["source_ref"],
                "source_excerpt": item["source_excerpt"]} for item in evidence]
    result = {
        "project_title": "交付项目", "anchor_id": anchor_id,
        "outcome": "needs_attention" if proposal else "not_needed",
        "reason": "验收排期影响回款" if proposal else "已有人推进，当前不需关注",
        "assessment_basis": "current_observation", "evidence": current,
        "attention_proposal": None,
    }
    if proposal:
        result["attention_proposal"] = {
            "assessment_basis": "current_observation", "category": "watch",
            "title": "交付项目", "why_attention": "本期回款有风险",
            "current_state": "验收日期未确定", "ceo_action": "关注验收排期",
            "material_trigger": "threatened_commitment", "evidence": current,
        }
    TaskAgentDecision.model_validate({"project_decisions": [], "task_decisions": [],
                                     "project_assessments": [result]})
    return result


def _tool():
    path = Path(__file__).parents[1] / "scripts/replay_task_attention.py"
    spec = importlib.util.spec_from_file_location("project_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(store):
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref="project:readback", title="交付项目",
    )
    project_id = resolution.register_official_project(
        anchor_id=anchor_id, registry_source="meeting:readback",
    )
    signal_id = store.create_business_task_signal(
        source_type="message", source_ref="message:readback",
        evidence_text="验收日期未确定，影响本期回款。", dedupe_key="readback:source",
    )
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(
            project_id=project_id, context=None, signal_ids=(signal_id,), db=db,
        )
    citation = {
        "signal_id": signal_id, "source_ref": "message:readback",
        "source_excerpt": "验收日期未确定，影响本期回款", "source_time": "",
        "source_link": "",
    }
    return project_id, anchor_id, signal_id, citation


@pytest.mark.parametrize("remove_project_proof,primary_missing", [(False, False), (True, False), (False, True)])
def test_readback_zero_task_attention_requires_actual_project_proof(tmp_path, remove_project_proof, primary_missing):
    store = AutoReplyStore(tmp_path / "project.sqlite3")
    project_id, anchor_id, signal_id, citation = _project(store)
    BusinessAttentionProjection(store).upsert(AttentionProposal(
        stable_key=f"project:{anchor_id}", category=AttentionCategory.WATCH,
        title="交付项目", business_area="交付", why_attention="本期回款有风险",
        current_state="验收日期未确定", ceo_action="关注验收排期",
        anchor_id=anchor_id, task_ids=(), evidence_signal_id=signal_id,
        assessment_json=json.dumps({"evidence": [citation]}),
    ))
    if remove_project_proof:
        with store._connect() as db:
            db.execute("delete from business_project_evidence where project_id=?", (project_id,))
    if primary_missing:
        other_id = store.create_business_task_signal(source_type="message", source_ref="m:other", evidence_text="同项目另一资料。", dedupe_key="other")
        with store.business_task_transaction() as db:
            ProjectContextService(store).apply(project_id=project_id, context=None, signal_ids=(other_id,), db=db)
            db.execute("update business_attention_items set evidence_signal_id=?", (other_id,))
    tool = _tool()
    result = tool.readback(store, input_id=None, before=tool.read_domain(store))
    assert result["cards"][0]["task_ids"] == []
    assert result["evidence_valid"] == (not remove_project_proof and not primary_missing)
    assert result["passed"] == (not remove_project_proof and not primary_missing)


@pytest.mark.parametrize("save_receipt", [False, True])
def test_readback_counts_project_owned_proposal_and_rejects_no_proposal_receipt(tmp_path, save_receipt):
    store = AutoReplyStore(tmp_path / "proposal.sqlite3")
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:proposal", "{}")
    run_id = store.record_task_agent_run(input_id, decision_json=json.dumps({
        "project_decisions": [], "task_decisions": [],
        "project_assessments": [_assessment(proposal=True)],
    }))
    if save_receipt:
        store.record_task_agent_projection(run_id, json.dumps({
            "status": "no_proposal", "outcomes": [], "recompute_error": "",
            "source_type": "reply_attempt", "task_decision_count": 0,
            "project_link_count": 0, "proposal_count": 0,
        }))
    tool = _tool()
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert result["proposal_count"] == 1
    assert "projection_not_successful" in result["failures"]


@pytest.mark.parametrize("remove_project_proof", [False, True])
def test_readback_not_needed_receipt_uses_project_proof_without_task_or_card(tmp_path, remove_project_proof):
    store = AutoReplyStore(tmp_path / "receipt.sqlite3")
    project_id, anchor_id, _, citation = _project(store)
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:receipt", json.dumps({
        "source": {"ref": citation["source_ref"]},
        "summary": citation["source_excerpt"],
    }))
    assessment = _assessment(anchor_id=anchor_id, evidence=[citation])
    assessment["assessment_basis"] = "historical_comparison"
    assessment["evidence"].append({key: citation[key] for key in
                                   ("signal_id", "source_ref", "source_excerpt")})
    TaskAgentDecision.model_validate({"project_decisions": [], "task_decisions": [],
                                     "project_assessments": [assessment]})
    run_id = store.record_task_agent_run(input_id, decision_json=json.dumps({
        "project_decisions": [], "task_decisions": [], "project_assessments": [assessment],
    }))
    receipt = {
        "assessment_index": 0, "status": "recorded", "task_ids": [],
        "anchor_id": anchor_id, "attention_id": None, "evidence": [citation],
    }
    store.record_task_agent_projection(run_id, json.dumps({
        "status": "no_proposal", "outcomes": [], "recompute_error": "",
        "source_type": "reply_attempt", "task_decision_count": 0,
        "project_link_count": 0, "proposal_count": 0,
        "project_assessments": [receipt],
    }))
    expected = {
        "attention_projects": [], "project_titles": ["交付项目"], "task_count": 0,
        "minimum_evidence_sources": 1, "project_assessments": [{
            "project_title": "交付项目", "outcome": "not_needed", "evidence": [citation],
            "application_status": "recorded", "task_count": 0,
            "attention_required": False,
        }],
    }
    tool = _tool()
    if remove_project_proof:
        with store._connect() as db:
            db.execute("delete from business_project_evidence where project_id=?", (project_id,))
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store), expected=expected)
    assert result["passed"] is not remove_project_proof, result["failures"]


@pytest.mark.parametrize("outcomes", [[], [{"assessment_index": 0, "anchor_id": 1, "attention_id": 999,
                                          "task_id": None, "status": "applied"}]])
def test_completed_receipt_does_not_prove_a_missing_attention_result(tmp_path, outcomes):
    store = AutoReplyStore(tmp_path / "missing-card.sqlite3")
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:missing-card", "{}")
    run_id = store.record_task_agent_run(input_id, decision_json=json.dumps({
        "project_decisions": [], "task_decisions": [], "project_assessments": [_assessment(proposal=True)],
    }))
    store.record_task_agent_projection(run_id, json.dumps({
        "status": "completed", "proposal_count": 1, "applied_count": len(outcomes),
        "outcomes": outcomes, "recompute_error": "", "project_assessments": [],
        "source_type": "reply_attempt", "task_decision_count": 0, "project_link_count": 0,
    }))
    tool = _tool()
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert "projection_not_successful" in result["failures"]


def test_current_negative_assessment_requires_a_saved_receipt_without_oracle_labels(tmp_path):
    store = AutoReplyStore(tmp_path / "missing-assessment.sqlite3")
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:no-receipt", "{}")
    store.record_task_agent_run(input_id, decision_json=json.dumps({
        "project_decisions": [], "task_decisions": [], "project_assessments": [_assessment()],
    }))
    tool = _tool()
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert "project_assessment_receipt_mismatch" in result["failures"]


def test_seed_independent_project_context_and_old_card_with_no_task(tmp_path):
    store = AutoReplyStore(tmp_path / "seed-project.sqlite3")
    case = {
        "existing_context": {"projects": [{
            "ref": "project:seed", "title": "交付项目", "registry_source": "meeting:seed",
            "evidence": [{"source_type": "meeting", "source_ref": "m:seed",
                          "evidence_text": "张三总负责交付；付款日期未定。", "dedupe_key": "seed"}],
            "context": {"goal": "完成交付", "scope": "一期", "overall_owner": {
                "person_name": "张三", "responsibility": "交付", "evidence": [{
                    "signal_id": None, "source_ref": "m:seed", "source_excerpt": "张三总负责交付",
                }],
            }, "responsibilities": [], "facts": []},
        }], "tasks": [], "attention": [{
            "project_title": "交付项目", "task_titles": [], "title": "回款关注",
            "why_attention": "影响现金安排", "current_state": "付款日期未定", "ceo_action": "观察",
            "assessment": {"evidence_source_ref": "m:seed", "source_ref": "m:seed",
                           "source_excerpt": "付款日期未定", "source_time": "", "source_link": "",
                           "assessment_basis": "current_observation", "material_trigger": "material_change"},
        }]},
        "work_item": {"source": {"type": "reply_attempt", "ref": "m:now"}, "context": {"source_conversation_kind": "group"}, "summary": "付款仍未确定。"},
    }
    tool = _tool()
    input_id = tool.seed_case(store, case)
    project = store.list_business_projects()[0]
    assert store.get_business_project_context(project.id).overall_owner.person_name == "张三"
    assert store.list_business_tasks() == ()
    [card] = store.list_business_attention_items()
    assert store.list_business_attention_tasks(card.id) == ()
    assert tool.readback(store, input_id=None, before=tool.read_domain(store))["evidence_valid"]
    assert store.get_work_summary_input(input_id).source_ref == "m:now"


@pytest.mark.parametrize("missing_capabilities", [False, True])
def test_case_replay_enqueues_each_source_version_only_when_its_turn_begins(tmp_path, monkeypatch, missing_capabilities):
    from app.task_models import TaskAgentDecision

    store = AutoReplyStore(tmp_path / "sequence.sqlite3")
    first = {"source": {"type": "ai_minutes", "ref": "m:same", "created_at": "2026-10-01T10:00:00Z"},
             "context": {"source_conversation_kind": "group"}, "summary": "会议确定交付项目，目标完成一期验收，目前按计划推进。"}
    second = {"source": {"type": "ai_minutes", "ref": "m:same", "created_at": "2026-10-02T10:00:00Z"},
              "context": {"source_conversation_kind": "group"}, "summary": "会议确定交付项目，目标完成一期验收，材料已经收齐。"}
    seen = []

    class Runner:
        codex = None

        def decide(self, item, *args, **kwargs):
            seen.append(item.summary)
            projects = store.list_business_projects()
            identity = {"anchor_id": projects[0].canonical_anchor_id} if projects else {"registration": {
                    "title": "交付项目", "authority": "meeting_decision", "source_excerpt": "会议确定交付项目", "reason": "会议明确项目目标",
            }}
            quote = {"source_ref": item.source.ref, "source_excerpt": item.summary}
            return TaskAgentDecision.model_validate({
                "project_decisions": [{**identity, "context": {"goal": "完成一期验收", "scope": "一期",
                    "overall_owner": None, "responsibilities": [], "facts": [{"key": "进展", "text": item.summary, "evidence": [quote]}]},
                    "evidence": [quote], "reason": "保存来源中的项目进展"}],
                "task_decisions": [], "project_assessments": [{"project_decision_index": 0, "project_title": "交付项目",
                    "outcome": "not_needed", "reason": "当前按计划推进", "assessment_basis": "current_observation", "evidence": [quote]}],
                "update_summary": "保存明确项目进展，没有新增任务",
            })

    case = {"source_inputs": [first, second], "work_item": second,
            "existing_context": {"projects": [], "tasks": [], "attention": []},
            "expected": {"project_titles": ["交付项目"], "attention_projects": [], "task_count": 0,
                         "minimum_evidence_sources": 1, "minimum_signal_versions_for_ref": {"m:same": 2}}}
    tool = _tool()
    if missing_capabilities:
        monkeypatch.setattr(tool, "comparison_capabilities", lambda _: {"missing_tables": ["business_project_evidence"]})
    result = tool.replay_case(store, Runner(), case)
    assert seen == [first["summary"], second["summary"]], [(step.get("execution_error"), step["failures"]) for step in result["source_steps"]]
    assert result["passed"] is (not missing_capabilities), result["failures"]
    assert "source_sequence_incomplete" not in result["failures"]
    if missing_capabilities:
        assert "project_centered_storage_missing" in result["failures"]
    assert len(result["source_steps"]) == 2
    assert [step["source_time"] for step in result["source_steps"]] == [first["source"]["created_at"], second["source"]["created_at"]]
    assert len(store.list_business_project_context_revisions(store.list_business_projects()[0].id)) == 2


def test_scoped_runner_records_delivered_metrics_and_ranges_without_source_body():
    class Base:
        def __init__(self, codex):
            self.codex = codex

        def decide(self, *args, **kwargs):
            assert kwargs["session_scope_id"] == _tool().EVALUATION_SCOPE
            return "decision"

    runner = _tool().scoped_runner(Base, None)
    context = {"source_metrics": {"signal_count": 3, "document_count": 1, "unique_body_chars": 9000, "visible_body_chars": 2048},
               "source_documents": [{"document_id": 7, "full_length": 9000, "truncated": True,
                                     "visible_ranges": [{"start": 0, "end": 2048, "text": "private-original-text"}],
                                     "decoded_excerpts": []}]}
    assert runner.decide(None, json.dumps(context) + "\nCorrection after rejected decision") == "decision"
    [delivery] = runner.context_deliveries
    assert delivery["source_metrics"] == context["source_metrics"]
    assert delivery["source_documents"][0]["visible_ranges"] == [{"start": 0, "end": 2048}]
    assert "private-original-text" not in json.dumps(delivery)

    runner.decide(None, "{}")
    assert runner.context_deliveries[-1]["source_metrics"] is None  # old absent metrics are not reconstructed


def test_baseline_missing_project_storage_is_an_explicit_comparison_failure(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        for name in ("business_projects", "business_tasks", "business_attention_items", "business_attention_events",
                     "business_task_events", "business_task_anchor_links", "business_anchors"):
            db.execute(f"create table {name} (id integer, canonical_anchor_id integer, active integer)")
        db.execute("create table agent_runtime_attempts (id integer,route_name text,runtime_kind text,model text,status text,failure_code text,attempt_purpose text,workload_kind text,workload_key text,session_id text,transcript_start integer,transcript_end integer)")
        db.execute("create table task_agent_runs (id integer,summary_input_id integer,status text,decision_json text,projection_json text,error text)")
        db.execute("insert into task_agent_runs values (1,1,'completed',?,?, '')", (
            json.dumps({"task_decisions": [], "project_assessments": []}),
            json.dumps({"status": "no_proposal", "outcomes": [], "recompute_error": ""}),
        ))

    class LegacyStore:
        def _connect(self):
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            return connection

        def get_work_summary_input(self, _):
            return SimpleNamespace(status="done")

    tool = _tool()
    store = LegacyStore()
    before = path.read_bytes()
    result = tool.readback(store, input_id=1, before=tool.read_domain(store))
    assert "project_centered_storage_missing" in result["failures"]
    assert result["comparison_capabilities"]["missing_tables"] == ["business_project_context_revisions", "business_project_evidence", "business_source_documents"]
    assert not result["passed"]
    assert path.read_bytes() == before
