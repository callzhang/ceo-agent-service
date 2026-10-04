"""Evaluation readback follows persisted Project evidence, not Task carriers."""

import importlib.util
import json
from pathlib import Path

import pytest

from app.project_context_service import ProjectContextService
from app.store import AutoReplyStore
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_models import AttentionCategory


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
        "project_assessments": [{"attention_proposal": {"action": "upsert"}}],
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
    input_id = store.enqueue_work_summary_input("reply_attempt", "m:receipt", "{}")
    assessment = {
        "project_title": "交付项目", "outcome": "not_needed",
        "reason": "已有人推进，当前不需关注", "evidence": [citation],
        "attention_proposal": None,
    }
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
        "project_decisions": [], "task_decisions": [], "project_assessments": [{
            "attention_proposal": {"action": "upsert"}, "outcome": "needs_attention",
        }],
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
        "project_decisions": [], "task_decisions": [], "project_assessments": [{
            "outcome": "not_needed", "attention_proposal": None,
        }],
    }))
    tool = _tool()
    result = tool.readback(store, input_id=input_id, before=tool.read_domain(store))
    assert "project_assessment_receipt_mismatch" in result["failures"]
