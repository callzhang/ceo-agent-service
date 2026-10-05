import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.store import AutoReplyStore
from app.agent_runtime_router import CodexCommandFactory, RoutedResultValidationError
from app.task_agent import (
    TaskAgentCodexRunner,
    TaskAgentRunner,
    apply_task_agent_decision,
    build_task_agent_prompt,
    process_work_item,
    _parse_task_agent_decision,
    _canonicalize_current_source_provenance,
    _report_project_registry_title,
    _task_result_validation_repair_prompt,
)
from app.leak_check import contains_credential, contains_local_runtime_leak
from app.task_models import (
    TaskAgentDecision,
    TaskDecision,
    WorkItem,
    WorkItemSourceKind,
    WorkItemSourceType,
    task_agent_output_schema,
)
from app.task_business_resolution import BusinessResolutionService
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_semantic_service import (
    RecordCandidate,
    RecordFormalTask,
    SourceSignal,
    TaskSemanticService,
)
from app.task_semantic_rules import FormalityEvidence
from app.task_semantic_models import (
    AttentionCategory,
    BusinessActorKind,
    BusinessRelevance,
    BusinessTaskDateType,
    FormalTaskBasis,
)
from app.task_agent_session import TaskAgentSessionLeaseLost


def _independent_project_work_item():
    return WorkItem.model_validate(
        {
            "summary": "会议决定启动客户验收项目，目标完成本轮验收。李四总负责交付，王五负责商务。当前验收按计划推进。",
            "source": {
                "type": "ai_minutes",
                "ref": "meeting:independent-project",
                "title": "交付周会",
                "created_at": "2026-10-04T09:00:00Z",
            },
            "context": {"source_conversation_kind": "minutes"},
        }
    )


def _independent_project_decision():
    return TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "客户验收项目",
                        "authority": "meeting_decision",
                        "reason": "会议明确立项",
                        "source_excerpt": "会议决定启动客户验收项目",
                    },
                    "context": {
                        "goal": "完成本轮验收",
                        "scope": "本轮交付",
                        "overall_owner": {
                            "person_name": "李四",
                            "responsibility": "总负责交付",
                            "evidence": [
                                {
                                    "source_ref": "meeting:independent-project",
                                    "source_excerpt": "李四总负责交付",
                                }
                            ],
                        },
                        "responsibilities": [
                            {
                                "person_name": "王五",
                                "responsibility": "商务",
                                "evidence": [
                                    {
                                        "source_ref": "meeting:independent-project",
                                        "source_excerpt": "王五负责商务",
                                    }
                                ],
                            }
                        ],
                        "facts": [],
                    },
                    "evidence": [
                        {
                            "source_ref": "meeting:independent-project",
                            "source_excerpt": "会议决定启动客户验收项目",
                        }
                    ],
                    "reason": "项目资料与 Task 独立保存",
                }
            ],
            "task_decisions": [],
            "project_assessments": [
                {
                    "project_decision_index": 0,
                    "project_title": "客户验收项目",
                    "outcome": "not_needed",
                    "reason": "验收按计划推进",
                    "assessment_basis": "current_observation",
                    "evidence": [
                        {
                            "source_ref": "meeting:independent-project",
                            "source_excerpt": "当前验收按计划推进",
                        }
                    ],
                }
            ],
            "update_summary": "保存项目定义、分工与正常进展，没有新任务",
        }
    )


def test_independent_project_application_saves_zero_task_context_and_real_receipt(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "project-only.sqlite3")
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=_independent_project_work_item(),
        decision=_independent_project_decision(),
        record_run=False,
    )
    project = store.list_business_projects()[0]
    assert result.task_ids == ()
    assert store.list_business_tasks() == ()
    assert project.context.overall_owner.person_name == "李四"
    assert project.context.responsibilities[0].person_name == "王五"
    applied = result.applied_projects[0]
    assert applied.project_decision_index == 0
    assert applied.project_id == project.id
    assert applied.anchor_id == project.canonical_anchor_id
    assert applied.revision_id is not None
    assert len(applied.signal_ids) == 1
    assert result.projection_receipt.project_decisions[0].project_id == project.id


def test_independent_project_replay_does_not_create_task_or_context_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "project-repeat.sqlite3")
    first = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=_independent_project_work_item(),
        decision=_independent_project_decision(),
        record_run=False,
    )
    second = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=_independent_project_work_item(),
        decision=_independent_project_decision(),
        record_run=False,
    )
    assert second.applied_projects[0].project_id == first.applied_projects[0].project_id
    assert second.applied_projects[0].signal_ids == first.applied_projects[0].signal_ids
    assert second.applied_projects[0].revision_id is None
    assert len(store.list_business_projects()) == 1
    assert store.list_business_tasks() == ()


def test_independent_project_zero_task_positive_assessment_has_actual_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "zero-task-positive.sqlite3")
    item = _independent_project_work_item()
    risk = "客户验收标准未确认，影响本轮交付。"
    item = item.model_copy(update={"summary": item.summary + risk})
    payload = _independent_project_decision().model_dump(mode="json")
    assessment = payload["project_assessments"][0]
    proof = {"source_ref": item.source.ref, "source_excerpt": risk}
    assessment.update(
        outcome="needs_attention",
        reason="验收标准未确认，影响交付",
        evidence=[proof],
        attention_proposal={
            "category": "watch",
            "title": "客户验收需关注",
            "why_attention": "验收标准未确认，影响交付",
            "current_state": risk,
            "ceo_action": "持续观察，当前无需决策",
            "assessment_basis": "current_observation",
            "material_trigger": "risk_escalation",
            "evidence": [proof],
        },
    )
    decision = TaskAgentDecision.model_validate(payload)
    first = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    receipt = first.projection_receipt
    assert receipt.status == "completed"
    assert receipt.applied_count == 1
    assert receipt.task_decisions == []
    project_result = receipt.project_decisions[0]
    [readback] = receipt.project_assessments
    assert readback.status == "applied"
    assert readback.anchor_id == project_result.anchor_id
    assert readback.task_ids == []
    assert readback.evidence[0].signal_id == first.current_signal_id
    card = store.get_business_attention_item(readback.attention_id)
    assert card.evidence_signal_id == first.current_signal_id
    assert store.list_business_tasks() == ()
    before = store.list_business_attention_events(card.id)
    repeated = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )
    assert repeated.projection_receipt.project_assessments[0].attention_id == card.id
    assert store.list_business_attention_events(card.id) == before
    assert (
        len(store.list_business_project_context_revisions(project_result.project_id))
        == 1
    )


def test_independent_project_invalid_role_quote_rolls_back_registration(tmp_path):
    store = AutoReplyStore(tmp_path / "project-rollback.sqlite3")
    payload = _independent_project_decision().model_dump(mode="json")
    payload["project_decisions"][0]["context"]["overall_owner"]["evidence"][0][
        "source_excerpt"
    ] = "张三总负责交付"
    with pytest.raises(ValueError, match="quote"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=_independent_project_work_item(),
            decision=TaskAgentDecision.model_validate(payload),
            record_run=False,
        )
    assert store.list_business_projects() == []
    assert store.list_business_tasks() == ()


def test_independent_project_two_projects_share_one_source_without_tasks(tmp_path):
    store = AutoReplyStore(tmp_path / "two-projects.sqlite3")
    item = _independent_project_work_item().model_copy(
        update={
            "summary": _independent_project_work_item().summary
            + "会议决定启动二期交付项目。二期按计划推进。"
        }
    )
    payload = _independent_project_decision().model_dump(mode="json")
    payload["project_decisions"].append(
        {
            "registration": {
                "title": "二期交付项目",
                "authority": "meeting_decision",
                "reason": "会议明确二期目标",
                "source_excerpt": "会议决定启动二期交付项目",
            },
            "evidence": [
                {
                    "source_ref": item.source.ref,
                    "source_excerpt": "会议决定启动二期交付项目",
                }
            ],
            "reason": "独立的二期项目，不合并进一期",
        }
    )
    payload["project_assessments"].append(
        {
            "project_decision_index": 1,
            "project_title": "二期交付项目",
            "outcome": "not_needed",
            "reason": "二期按计划推进",
            "assessment_basis": "current_observation",
            "evidence": [
                {"source_ref": item.source.ref, "source_excerpt": "二期按计划推进"}
            ],
        }
    )
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    assert len(result.applied_projects) == 2
    assert len(store.list_business_projects()) == 2
    assert (
        result.applied_projects[0].signal_ids == result.applied_projects[1].signal_ids
    )
    assert result.task_ids == ()
    assert len(result.projection_receipt.project_assessments) == 2


def test_independent_project_context_update_preserves_task_truth_and_historical_roles(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "context-update.sqlite3")
    first = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=_independent_project_work_item(),
        decision=_independent_project_decision(),
        record_run=False,
    )
    project = store.list_business_projects()[0]
    task = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="独立事项",
            signal=SourceSignal(
                source_type="message",
                source_ref="chat:independent",
                evidence_text="确认独立事项",
                dedupe_key="independent-task",
            ),
        )
    )
    before = store.get_business_task(task.task_id).model_dump(mode="json")
    events = store.list_business_task_events(task.task_id)
    item = _independent_project_work_item().model_copy(
        update={
            "source": _independent_project_work_item().source.model_copy(
                update={
                    "ref": "chat:project-progress",
                    "type": WorkItemSourceType.REPLY_ATTEMPT,
                }
            ),
            "summary": "客户验收项目已完成第一阶段，按计划推进。",
        }
    )
    context = project.context.model_dump(mode="json")
    context["facts"] = [
        {
            "key": "first-stage",
            "text": "第一阶段已完成",
            "evidence": [
                {"source_ref": item.source.ref, "source_excerpt": "已完成第一阶段"}
            ],
        }
    ]
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "anchor_id": project.canonical_anchor_id,
                    "context": context,
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "客户验收项目已完成第一阶段",
                        }
                    ],
                    "reason": "新聊天补充阶段进展，职责沿用原会议证明",
                }
            ],
            "task_decisions": [],
            "project_assessments": [
                {
                    "anchor_id": project.canonical_anchor_id,
                    "project_title": project.title,
                    "outcome": "not_needed",
                    "reason": "按计划推进",
                    "assessment_basis": "current_observation",
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": "按计划推进"}
                    ],
                }
            ],
            "update_summary": "项目进展更新，任务无变化",
        }
    )
    result = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )
    after = store.list_business_projects()[0]
    assert (
        after.context.overall_owner.evidence[0].signal_id
        == first.applied_projects[0].signal_ids[0]
    )
    assert after.context.facts[0].evidence[0].signal_id == result.current_signal_id
    assert store.get_business_task(task.task_id).model_dump(mode="json") == before
    assert store.list_business_task_events(task.task_id) == events


def test_independent_project_unknown_clue_is_recorded_without_fake_project_or_task(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "unknown-clue.sqlite3")
    item = _independent_project_work_item().model_copy(
        update={"summary": "新客户方向还未确定项目范围。"}
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [],
            "project_assessments": [
                {
                    "project_title": "新客户方向",
                    "outcome": "insufficient_evidence",
                    "reason": "项目范围与正式身份未确定",
                    "assessment_basis": "current_observation",
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "还未确定项目范围",
                        }
                    ],
                }
            ],
            "update_summary": "保存待明确线索，不创建正式项目",
        }
    )
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert store.list_business_projects() == []
    assert store.list_business_tasks() == ()
    assert result.projection_receipt.project_assessments[0].anchor_id is None
    assert (
        result.projection_receipt.project_assessments[0].evidence[0].signal_id
        == result.current_signal_id
    )


def test_independent_project_prompt_and_skill_have_one_current_contract():
    prompt = build_task_agent_prompt(
        _independent_project_work_item(), "bounded context"
    )
    assert "project_decisions" in prompt
    assert "Task-first decision envelope" not in prompt
    assert "project_proposal" not in prompt
    assert "project_link_proposal" not in prompt
    assert "related_task_ids" not in prompt
    assert "Project can have zero Tasks" in prompt
    assert "suggestion" in prompt
    assert "suggested_owner_name" in prompt
    prompt = " ".join(prompt.split())
    assert "For project_assessments, use exactly one selector" in prompt
    assert "Never include a skip decision in decision_indexes" in prompt
    assert "status and business_relevance may only change through update_fields" in prompt
    assert "They remain top-level fields" in prompt
    assert "New, record_candidate and skip decisions must leave status and business_relevance unset" in prompt
    assert "When promoting a suggestion, omit the suggestion field" in prompt
    assert "requires that Project selector" in prompt
    assert "return the complete current ProjectContext whenever new facts or roles are learned" in prompt
    assert "unresolved material Project risk needs an actionable next step" in prompt
    assert "meeting action item is an actual assignment only when" in prompt
    assert "assignment_authorized=true" in prompt
    assert "A Project role or responsibility is not itself a Task" in prompt
    assert "A bare responsibility clause (" in prompt
    assert "Do not reclassify the competing overall-owner candidates as responsibilities" in prompt
    assert "Missing ownership alone is not a material risk" in prompt
    assert "trailing rank/honorific" in prompt
    assert "task_ids must be copied only from that card's actual stored member IDs" in prompt
    assert "directly supports this specific Project assessment" in prompt
    assert "completed or unrelated Project Tasks are not members" in prompt
    assert "explicit unresolved dispute over who" in prompt
    assert "Missing overall ownership without a stated impact or dispute" in prompt


def test_independent_project_process_marks_project_only_input_done(tmp_path):
    store = AutoReplyStore(tmp_path / "project-only-run.sqlite3")
    item = _independent_project_work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    process_work_item(
        store,
        TaskAgentRunner(
            FakeCodex(_independent_project_decision().model_dump(mode="json"))
        ),
        store.claim_work_summary_inputs(limit=1)[0],
    )
    assert store.get_work_summary_input(input_id).status.value == "done"
    with store._connect() as db:
        run = db.execute(
            "select * from task_agent_runs where summary_input_id=?", (input_id,)
        ).fetchone()
    assert run["status"] == "completed"
    assert (
        json.loads(run["projection_json"])["project_decisions"][0]["project_id"]
        == store.list_business_projects()[0].id
    )


def test_independent_project_suggests_from_historical_role_then_human_promotes_same_id(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "suggestion-agent.sqlite3")
    first = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=_independent_project_work_item(),
        decision=_independent_project_decision(),
        record_run=False,
    )
    project = store.list_business_projects()[0]
    role = project.context.responsibilities[0].evidence[0].model_dump(mode="json")
    item = _independent_project_work_item().model_copy(
        update={
            "source": _independent_project_work_item().source.model_copy(
                update={
                    "type": WorkItemSourceType.REPLY_ATTEMPT,
                    "ref": "chat:payment",
                    "conversation_id": "group:payment",
                }
            ),
            "summary": "客户验收项目款项尚未到账，请核实到账情况。",
        }
    )
    payload = {
        "project_decisions": [],
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "title": "核实到账情况",
                "source_ref": item.source.ref,
                "source_excerpt": "款项尚未到账",
                "project": {"anchor_id": project.canonical_anchor_id},
                "suggestion": {
                    "reason": "按商务职责建议核实款项",
                    "suggested_owner_name": "王五",
                    "responsibility_evidence": [role],
                    "basis_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "款项尚未到账",
                        }
                    ],
                },
            }
        ],
        "project_assessments": [
            {
                "anchor_id": project.canonical_anchor_id,
                "project_title": project.title,
                "outcome": "not_needed",
                "reason": "本轮先核实，没有明确重大经营影响",
                "assessment_basis": "current_observation",
                "decision_indexes": [0],
                "evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": "请核实到账情况"}
                ],
            }
        ],
        "update_summary": "按已存职责展示下一步建议，非人类指派",
    }
    decision = TaskAgentDecision.model_validate(payload)
    result = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )
    task_id = result.task_ids[0]
    suggested = store.get_business_task(task_id)
    assert suggested.origin == "agent_suggestion"
    assert suggested.owner_name == ""
    assert suggested.commitment_status.value == "none"
    assert json.loads(suggested.suggestion_json)["suggested_owner_name"] == "王五"
    assert role["signal_id"] == first.applied_projects[0].signal_ids[0]
    repeated = apply_task_agent_decision(
        store, summary_input_id=3, work_item=item, decision=decision, record_run=False
    )
    assert repeated.task_ids == (task_id,)
    with store._connect() as db:
        assert (
            db.execute(
                "select count(*) from business_task_todo_sync_outbox"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute("select count(*) from business_task_follow_ups").fetchone()[0]
            == 0
        )

    assigned_item = item.model_copy(
        update={
            "source": item.source.model_copy(update={"ref": "chat:assigned-payment"}),
            "summary": "王五负责核实到账情况。",
            "context": item.context.model_copy(
                update={
                    "assignment_authorized": True,
                    "owner_identity": {"name": "王五", "user_id": "wang-5"},
                }
            ),
        }
    )
    assigned = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "promote_candidate",
                    "task_id": task_id,
                    "source_ref": assigned_item.source.ref,
                    "source_excerpt": "王五负责核实到账情况",
                    "formal_basis": "explicit_assignment",
                    "owner_name": "王五",
                    "owner_user_id": "wang-5",
                    "owner_evidence": {
                        "source_ref": assigned_item.source.ref,
                        "excerpt": "王五负责核实到账情况",
                    },
                }
            ],
            "project_assessments": [
                {
                    "anchor_id": project.canonical_anchor_id,
                    "project_title": project.title,
                    "outcome": "not_needed",
                    "reason": "具体工作已明确安排，暂无重大经营风险",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [task_id],
                    "evidence": [
                        {
                            "source_ref": assigned_item.source.ref,
                            "source_excerpt": "王五负责核实到账情况",
                        }
                    ],
                }
            ],
            "update_summary": "按真实指派晋升已有建议，不创建第二个任务",
        }
    )
    promoted = apply_task_agent_decision(
        store,
        summary_input_id=4,
        work_item=assigned_item,
        decision=assigned,
        record_run=False,
    )
    actual = store.get_business_task(task_id)
    assert promoted.task_ids == (task_id,)
    assert actual.stage.value == "formal"
    assert actual.origin == "agent_suggestion"
    assert actual.owner_name == "王五"
    assert actual.commitment_status.value == "assigned_unaccepted"


@pytest.mark.parametrize("change_description", [False, True])
def test_independent_project_link_preserves_all_original_proofs(
    tmp_path, change_description
):
    store = AutoReplyStore(tmp_path / "association.sqlite3")
    apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=_independent_project_work_item(),
        decision=_independent_project_decision(),
        record_run=False,
    )
    project = store.list_business_projects()[0]
    service = TaskSemanticService(store)
    old = service.record_candidate(
        RecordCandidate(
            title="准备验收材料",
            description="保留原描述",
            signal=SourceSignal(
                source_type="message",
                source_ref="chat:old-deliverable",
                evidence_text="准备验收材料。",
                dedupe_key="original-deliverable",
            ),
        )
    )
    with store.business_task_transaction() as db:
        proof_ids = [
            service._signal_id_or_create(
                signal=SourceSignal(
                    source_type="message",
                    source_ref=f"chat:link-proof-{i}",
                    evidence_text=f"准备验收材料属于客户验收项目，佐证 {i}。",
                    dedupe_key=f"link-proof-{i}",
                ),
                db=db,
                now=service._now(),
            )
            for i in (1, 2)
        ]
    proofs = [
        {
            "signal_id": signal_id,
            "source_ref": f"chat:link-proof-{i}",
            "source_excerpt": "准备验收材料属于客户验收项目",
        }
        for i, signal_id in zip((1, 2), proof_ids)
    ]
    item = _independent_project_work_item().model_copy(
        update={
            "source": _independent_project_work_item().source.model_copy(
                update={
                    "ref": "chat:new-association",
                    "type": WorkItemSourceType.REPLY_ATTEMPT,
                }
            ),
            "summary": "准备验收材料属于客户验收项目，目前按计划推进。",
        }
    )
    task_decision = {
        "action": "update_task",
        "transition": "update_fields",
        "task_id": old.task_id,
        "source_ref": item.source.ref,
        "source_excerpt": "准备验收材料属于客户验收项目",
        "project": {"anchor_id": project.canonical_anchor_id},
        "project_link_evidence": proofs,
    }
    if change_description:
        task_decision["description"] = "已核对材料范围"
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "task_decisions": [task_decision],
            "project_assessments": [
                {
                    "anchor_id": project.canonical_anchor_id,
                    "project_title": project.title,
                    "decision_indexes": [0],
                    "outcome": "not_needed",
                    "reason": "材料按计划准备，无经营风险",
                    "assessment_basis": "current_observation",
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "目前按计划推进",
                        }
                    ],
                }
            ],
            "update_summary": "确认已有任务的项目归属",
        }
    )
    result = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )
    actual = store.get_business_task(old.task_id)
    assert result.task_ids == (old.task_id,)
    assert result.affected_task_ids == (old.task_id,)
    assert result.applied_decisions[0].anchor_id == project.canonical_anchor_id
    assert actual.title == "准备验收材料"
    assert actual.description == (
        "已核对材料范围" if change_description else "保留原描述"
    )
    assert {
        proof.signal_id for proof in store.list_business_task_evidence(old.task_id)
    } >= set(proof_ids)
    assert {
        proof.signal_id for proof in store.list_business_project_evidence(project.id)
    } >= set(proof_ids)
    events = store.list_business_task_events(old.task_id)
    details = [event for event in events if event.event_type.value == "details_changed"]
    assert len(details) == int(change_description)
    with store._connect() as db:
        assert (
            db.execute("select count(*) from business_task_follow_ups").fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "select count(*) from business_task_todo_sync_outbox"
            ).fetchone()[0]
            == 0
        )


def test_independent_project_suggestion_reason_update_preserves_omitted_description(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "suggestion-details.sqlite3")
    apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=_independent_project_work_item(),
        decision=_independent_project_decision(),
        record_run=False,
    )
    project = store.list_business_projects()[0]
    item = _independent_project_work_item()
    suggestion = {
        "reason": "按项目职责建议核对商务进展",
        "suggested_owner_name": "王五",
        "responsibility_evidence": [
            project.context.responsibilities[0].evidence[0].model_dump(mode="json")
        ],
        "basis_evidence": [
            {"source_ref": item.source.ref, "source_excerpt": "当前验收按计划推进"}
        ],
    }
    payload = {
        "project_decisions": [],
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "title": "核对商务进展",
                "description": "保留这段描述",
                "source_ref": item.source.ref,
                "source_excerpt": "当前验收按计划推进",
                "project": {"anchor_id": project.canonical_anchor_id},
                "suggestion": suggestion,
            }
        ],
        "project_assessments": [
            {
                "anchor_id": project.canonical_anchor_id,
                "project_title": project.title,
                "outcome": "not_needed",
                "reason": "按计划推进",
                "assessment_basis": "current_observation",
                "decision_indexes": [0],
                "evidence": [
                    {
                        "source_ref": item.source.ref,
                        "source_excerpt": "当前验收按计划推进",
                    }
                ],
            }
        ],
        "update_summary": "保存建议",
    }
    first = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=item,
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    payload["task_decisions"][0].update(
        action="update_task", transition="update_fields", task_id=first.task_ids[0]
    )
    payload["task_decisions"][0].pop("title")
    payload["task_decisions"][0].pop("description")
    payload["task_decisions"][0]["suggestion"]["reason"] = (
        "补充建议理由，事项和说明不变"
    )
    second = apply_task_agent_decision(
        store,
        summary_input_id=3,
        work_item=item,
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    actual = store.get_business_task(first.task_ids[0])
    assert second.task_ids == first.task_ids
    assert actual.title == "核对商务进展"
    assert actual.description == "保留这段描述"
    assert (
        json.loads(actual.suggestion_json)["reason"] == "补充建议理由，事项和说明不变"
    )


def test_independent_project_old_card_proof_does_not_mask_rejected_current_proposal(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "old-proof-current-rejected.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    card_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    item = _work_item()
    historical = {
        "signal_id": seed.signal_id,
        "source_ref": "seed:售前知识库",
        "source_excerpt": "售前知识库历史交付风险",
    }
    current = {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"}
    assessment = _stored_project_assessment(
        item,
        seed,
        anchor_id,
        outcome="needs_attention",
        existing_attention_id=card_id,
        assessment_basis="historical_comparison",
        evidence=[current, historical],
        attention_proposal={
            "category": "watch",
            "title": "当前风险更新",
            "why_attention": "风险待观察",
            "current_state": "补充新事实",
            "ceo_action": "观察结果",
            "assessment_basis": "historical_comparison",
            "material_trigger": "risk_escalation",
            "evidence": [current, historical],
        },
    )

    def rejected(self, proposal):
        raise ValueError("current proposal rejected by projection")

    monkeypatch.setattr(BusinessAttentionProjection, "upsert", rejected)
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "task_decisions": [],
                "project_assessments": [assessment],
            }
        ),
        record_run=False,
    )
    receipt = result.projection_receipt.project_assessments[0]
    assert receipt.status == "rejected"
    assert "current proposal rejected by projection" in receipt.reason
    assert receipt.attention_id == card_id


def test_task_agent_parser_uses_valid_result_after_failed_tool_event():
    decision = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "skip",
                "transition": "none",
                "skip_reason": "No durable work was identified.",
            }
        ],
    }
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {"type": "McpToolCall", "status": "failed"},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "last_agent_message": json.dumps(decision),
                    },
                }
            ),
        ]
    )

    assert _parse_task_agent_decision(raw) == TaskAgentDecision.model_validate(decision)


def test_task_agent_parser_recovers_complete_object_with_repeated_continuation():
    decision = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "skip",
                "transition": "none",
                "skip_reason": "The source is informational only.",
            }
        ],
    }
    malformed = (
        json.dumps(decision)
        + ',"project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions":[]}'
    )

    assert _parse_task_agent_decision(malformed) == TaskAgentDecision.model_validate(
        decision
    )


def test_task_agent_parser_marks_missing_decision_as_validation_failure():
    with pytest.raises(
        RoutedResultValidationError, match="No TaskAgentDecision JSON found"
    ) as raised:
        _parse_task_agent_decision("not a decision")

    assert raised.value.raw_output == "not a decision"


def _agent_message_jsonl(*messages: str) -> str:
    return "\n".join(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": message},
            }
        )
        for message in messages
    )


def _null_evidence_decision(**overrides) -> dict:
    decision = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "skip",
                "transition": "none",
                "skip_reason": "No change.",
                "untrusted_runtime_value": "Confirm the vendor quote with Zhang",
            }
        ],
    }
    decision.update(overrides)
    return decision


def test_task_agent_parser_reports_field_errors_of_last_candidate():
    raw = _agent_message_jsonl(json.dumps(_null_evidence_decision()))

    with pytest.raises(
        RoutedResultValidationError,
        match=r"task_decisions\.0\.untrusted_runtime_value: Extra inputs are not permitted",
    ) as raised:
        _parse_task_agent_decision(raw)

    message = str(raised.value)
    assert "untrusted_runtime_value" in message
    assert "No TaskAgentDecision JSON found" not in message
    assert raised.value.raw_output == raw


def test_task_agent_parser_accepts_minimax_null_optional_fields():
    decision = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "skip",
                "transition": "none",
                "skip_reason": "No source-grounded Task.",
            }
        ],
    }
    parsed = _parse_task_agent_decision(_agent_message_jsonl(json.dumps(decision)))
    assert parsed == TaskAgentDecision.model_validate(decision)


def test_task_agent_parser_ignores_event_objects_when_naming_failing_candidate():
    raw = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "mcp_tool_call", "status": "failed"},
                }
            ),
            _agent_message_jsonl(json.dumps(_null_evidence_decision())),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
        ]
    )

    with pytest.raises(RoutedResultValidationError) as raised:
        _parse_task_agent_decision(raw)

    message = str(raised.value)
    assert "task_decisions.0.untrusted_runtime_value" in message
    assert "type:" not in message
    assert "item:" not in message
    assert "usage:" not in message
    assert "action: Field required" not in message


def test_task_agent_parser_message_never_echoes_field_values():
    decision = _null_evidence_decision()
    decision["task_decisions"][0]["untrusted_runtime_value"] = (
        "Read /tmp/ceo-agent-service/notes.md with sk-proj-abcdefghijklmnop"
    )
    raw = json.dumps(decision)
    assert contains_local_runtime_leak(raw)
    assert contains_credential(raw)

    with pytest.raises(RoutedResultValidationError) as raised:
        _parse_task_agent_decision(raw)

    message = str(raised.value)
    assert not contains_local_runtime_leak(message)
    assert not contains_credential(message)
    assert "notes.md" not in message


def test_task_result_validation_repair_prompt_lists_field_errors_and_rules():
    raw = _agent_message_jsonl(json.dumps(_null_evidence_decision()))

    prompt = _task_result_validation_repair_prompt(raw)

    assert (
        "- task_decisions.0.untrusted_runtime_value: Extra inputs are not permitted"
        in prompt
    )
    assert "task_decisions" in prompt
    assert "apply_acceptance requires accepted polarity" in prompt
    assert "verified reply-to source reference" in prompt
    assert "Any non-empty owner_name or owner_user_id requires owner_evidence" in prompt
    assert "memory_recall" in prompt
    assert "live directory read" in prompt
    assert "For project_assessments, use exactly one selector" in prompt
    assert "Never include a skip decision in decision_indexes" in prompt
    assert "status and business_relevance may only change through update_fields" in prompt
    assert "New, record_candidate and skip decisions must leave status and business_relevance unset" in prompt
    assert "When promoting a suggestion, omit the suggestion field" in prompt
    assert "project_link_evidence requires a Project selector" in prompt
    assert (
        "A source path may appear only in an evidence field whose key is "
        "exactly source or source_ref"
    ) in prompt
    assert "Confirm the vendor quote" not in prompt
    assert "Vendor quote still pending" not in prompt


def test_task_result_validation_repair_prompt_for_prose_only_output():
    prompt = _task_result_validation_repair_prompt(
        "I could not find any durable work here."
    )

    assert "did not contain a TaskAgentDecision JSON object" in prompt
    assert "without prose or code fences" in prompt
    assert "Do not include local filesystem paths" in prompt


def test_task_result_validation_repair_prompt_after_runtime_path_leak():
    decision = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "skip",
                "transition": "none",
                "skip_reason": "Could not read /tmp/ceo-agent-service/todo.md",
            }
        ],
    }

    prompt = _task_result_validation_repair_prompt(
        _agent_message_jsonl(json.dumps(decision))
    )

    assert (
        "satisfied the schema but a business field contained a runtime path" in prompt
    )
    assert "Do not include local filesystem paths" in prompt
    assert not contains_local_runtime_leak(prompt)


def test_task_result_validation_repair_prompt_caps_problem_list():
    decision = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {"action": "skip", "transition": "none", "untrusted_field": str(index)}
            for index in range(15)
        ],
    }

    prompt = _task_result_validation_repair_prompt(json.dumps(decision))

    problem_section = prompt.split("Rules that must hold:")[0]
    assert problem_section.count("\n- ") == 12
    assert "task_decisions.11.untrusted_field" in problem_section
    assert "task_decisions.12.untrusted_field" not in problem_section


class FakeCodex:
    last_session_id = "task-session-1"
    last_transcript_start_line = 1
    last_transcript_end_line = 10

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []
        self.calls = []

    def decide(
        self, *, prompt, session_id=None, workload_key=None, session_scope_id=None
    ):
        self.prompts.append(prompt)
        self.calls.append(
            {
                "workload_key": workload_key,
                "session_scope_id": session_scope_id,
            }
        )
        return TaskAgentDecision.model_validate(self.payload)


class FakeCodexWithoutSession(FakeCodex):
    last_session_id = None


class FakeCodexWithAuditEvents(FakeCodex):
    def __init__(self, payload, audit_tool_events):
        super().__init__(payload)
        self.last_audit_tool_events = audit_tool_events


class FakeRoutedTaskExecution:
    def __init__(self, raw, *, session_id="", transcript_end=0):
        self.raw = raw
        self.session_id = session_id
        self.transcript_end = transcript_end
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        raw = self.raw() if callable(self.raw) else self.raw
        value = kwargs["parser"](raw)
        value = kwargs["result_codec"].decode(kwargs["result_codec"].encode(value))
        return SimpleNamespace(
            value=value,
            route_name="codex_oauth",
            attempt_id=1,
            session_id=self.session_id,
            transcript_start=0,
            transcript_end=self.transcript_end,
        )


@pytest.mark.parametrize(
    "fault,problem",
    [
        ("link", "new supporting Task requires a Project selector"),
        ("history", "historical_comparison requires"),
        ("relation", "relation_proposals.0.direction"),
    ],
)
def test_attention_shape_omissions_use_existing_same_session_repair(fault, problem):
    import copy

    attention = {
        "assessment_basis": "historical_comparison",
        "category": "watch",
        "title": "交付风险",
        "why_attention": "当前延期较原计划扩大",
        "current_state": "验收再次延期",
        "ceo_action": "观察验收",
        "material_trigger": "risk_escalation",
        "evidence": [
            {"source_ref": "chat:current", "source_excerpt": "验收再次延期"},
            {
                "signal_id": 7,
                "source_ref": "report:prior",
                "source_excerpt": "原计划延期",
            },
        ],
    }
    valid = {
        "project_decisions": [],
        "project_assessments": [
            {
                "project_title": "示例项目",
                "anchor_id": 3,
                "outcome": "needs_attention",
                "reason": "当前延期较原计划扩大，可能影响交付。",
                "assessment_basis": "historical_comparison",
                "evidence": attention["evidence"],
                "decision_indexes": [0],
                "task_ids": [],
                "attention_proposal": attention,
            }
        ],
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "title": "核对验收计划",
                "source_ref": "chat:current",
                "source_excerpt": "复核示例项目验收计划",
                "project": {"anchor_id": 3},
                "project_link_evidence": [
                    {
                        "source_ref": "chat:current",
                        "source_excerpt": "复核示例项目验收计划",
                    }
                ],
            }
        ],
    }
    if fault == "relation":
        valid["task_decisions"][0]["relation_proposals"] = [
            {
                "related_task_id": 7,
                "direction": "current_to_related",
                "relation_type": "supports",
            }
        ]
    invalid = copy.deepcopy(valid)
    if fault == "link":
        invalid["task_decisions"][0].pop("project")
        invalid["task_decisions"][0].pop("project_link_evidence")
    elif fault == "history":
        invalid["project_assessments"][0]["attention_proposal"]["evidence"].pop()
    else:
        invalid["task_decisions"][0]["relation_proposals"][0].pop("direction")

    class RepairingExecution:
        def execute(self, **kwargs):
            retry = kwargs["result_validation_retry"]
            assert retry.resume_same_session
            assert kwargs["conversation_id"] == "contract-repair-session"
            raw = _agent_message_jsonl(json.dumps(invalid))
            with pytest.raises(RoutedResultValidationError):
                kwargs["parser"](raw)
            correction = retry.correction_prompt(raw)
            assert problem in correction
            assert "same Agent turn" in correction
            value = kwargs["parser"](_agent_message_jsonl(json.dumps(valid)))
            return SimpleNamespace(
                value=value,
                session_id="contract-repair-session",
                transcript_start=0,
                transcript_end=2,
            )

    decision = TaskAgentCodexRunner(routed_execution=RepairingExecution()).decide(
        prompt="decide", workload_key="1", session_scope_id="contract-repair-session"
    )
    assert decision == TaskAgentDecision.model_validate(valid)


@pytest.mark.parametrize("fault", ["missing_assessment", "contradictory_attention"])
def test_project_assessment_envelope_uses_one_same_session_parser_correction(fault):
    import copy

    valid = {
        "project_decisions": [],
        "project_assessments": [
            {
                "project_title": "示例项目",
                "anchor_id": 3,
                "outcome": "needs_attention",
                "reason": "验收延期会影响客户上线。",
                "assessment_basis": "current_observation",
                "decision_indexes": [0],
                "task_ids": [],
                "evidence": [
                    {"source_ref": "chat:current", "source_excerpt": "示例项目验收延期"}
                ],
                "attention_proposal": {
                    "assessment_basis": "current_observation",
                    "category": "watch",
                    "title": "验收延期",
                    "why_attention": "影响客户上线",
                    "current_state": "验收延期",
                    "ceo_action": "观察验收",
                    "material_trigger": "risk_escalation",
                    "evidence": [
                        {
                            "source_ref": "chat:current",
                            "source_excerpt": "示例项目验收延期",
                        }
                    ],
                },
            }
        ],
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "title": "核对验收计划",
                "source_ref": "chat:current",
                "source_excerpt": "示例项目验收延期",
                "project": {"anchor_id": 3},
                "project_link_evidence": [
                    {"source_ref": "chat:current", "source_excerpt": "示例项目验收延期"}
                ],
            }
        ],
    }
    invalid = copy.deepcopy(valid)
    if fault == "missing_assessment":
        invalid.pop("project_assessments")
    else:
        invalid["project_assessments"][0]["outcome"] = "not_needed"

    class OneRepairExecution:
        execute_calls = 0
        parser_calls = 0
        correction_calls = 0
        store_calls = 0

        def execute(self, **kwargs):
            self.execute_calls += 1
            assert kwargs["conversation_id"] == "assessment-repair-session"
            retry = kwargs["result_validation_retry"]
            assert retry.resume_same_session
            raw = _agent_message_jsonl(json.dumps(invalid))
            self.parser_calls += 1
            with pytest.raises(RoutedResultValidationError):
                kwargs["parser"](raw)
            self.correction_calls += 1
            correction = retry.correction_prompt(raw)
            assert "project_assessments" in correction
            assert "one outcome, concrete reason, and original evidence" in correction
            assert (
                "Every attention_proposal belongs to its needs_attention assessment"
                in correction
            )
            assert (
                "current source and current Tasks' confirmed Project links"
                in correction
            )
            assert "not limited to structured selectors" in correction
            self.parser_calls += 1
            value = kwargs["parser"](_agent_message_jsonl(json.dumps(valid)))
            return SimpleNamespace(
                value=value,
                session_id="assessment-repair-session",
                transcript_start=0,
                transcript_end=2,
            )

    routed = OneRepairExecution()
    decision = TaskAgentCodexRunner(routed_execution=routed).decide(
        prompt="decide", workload_key="1", session_scope_id="assessment-repair-session"
    )

    assert decision == TaskAgentDecision.model_validate(valid)
    assert (
        routed.execute_calls,
        routed.parser_calls,
        routed.correction_calls,
        routed.store_calls,
    ) == (1, 2, 1, 0)


def _work_item(project_name="售前知识库"):
    return WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1",
                "title": "售前推进",
                "conversation_id": "cid-1",
                "conversation_title": "售前群",
                "created_at": "2026-06-07 09:00:00",
            },
            "summary": "售前知识库需要补齐来源链接，owner 是 Alex。",
            "project_name": project_name,
            "context": {
                "sender": "Avery",
                "participants": ["Alex"],
                "source_conversation_kind": "group",
                "source_conversation_title": "售前群",
            },
        }
    )


def _low_confidence_minutes_work_item() -> WorkItem:
    return WorkItem.model_validate(
        {
            "source": {
                "type": "local_file",
                "ref": "/tmp/HR周例会.md#sha256=abc",
                "title": "HR周例会.md",
                "conversation_id": "",
                "conversation_title": "",
                "created_at": "2026-06-22 13:33:31",
            },
            "summary": "\n".join(
                [
                    "> **参与人**: 磊哥, susu, 刘瑞安Alan, 胡明, 张静, Avery",
                    "# Transcript",
                    "[00:01] 刘瑞安Alan: 第一段",
                    "[00:02] 刘瑞安Alan: 第二段",
                    "[00:03] 刘瑞安Alan: 第三段",
                    "[00:04] 刘瑞安Alan: 第四段",
                    "[00:05] 刘瑞安Alan: 第五段",
                ]
            ),
            "project_name": "HR周例会.md",
            "context": {
                "sender": "",
                "participants": [],
                "source_conversation_kind": "file",
                "source_conversation_title": "HR周例会.md",
            },
        }
    )


def test_process_work_item_opens_and_completes_runtime_parent_before_decision(tmp_path):
    store = AutoReplyStore(tmp_path / "task-lifecycle.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    payload = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {"action": "skip", "transition": "none", "skip_reason": "no durable update"}
        ],
    }

    class LifecycleCodex(FakeCodex):
        last_audit_tool_events = []

        def decide(self, **kwargs):
            run_id = int(kwargs["workload_key"])
            with store._connect() as db:
                row = db.execute(
                    "select status from task_agent_runs where id=?", (run_id,)
                ).fetchone()
            assert row["status"] == "running"
            return super().decide(**kwargs)

    codex = LifecycleCodex(payload)
    process_work_item(store, TaskAgentRunner(codex), work_input)

    with store._connect() as db:
        runs = db.execute(
            "select * from task_agent_runs where summary_input_id=?", (input_id,)
        ).fetchall()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert json.loads(runs[0]["decision_json"])["task_decisions"][0]["action"] == "skip"
    assert store.get_work_summary_input(input_id).status.value == "skipped"
    assert codex.calls[0]["session_scope_id"] == "task-agent:work-tracking:v1"


def test_process_work_items_keep_run_keys_but_share_task_agent_session_scope(tmp_path):
    store = AutoReplyStore(tmp_path / "task-shared-session-scope.sqlite3")
    first_item = _work_item()
    second_payload = first_item.model_dump(mode="json")
    second_payload["source"]["ref"] = "2"
    second_item = WorkItem.model_validate(second_payload)
    for item in (first_item, second_item):
        store.enqueue_work_summary_input(
            item.source.type.value,
            item.source.ref,
            item.model_dump_json(),
        )
    work_inputs = store.claim_work_summary_inputs(limit=2)
    codex = FakeCodex(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "skip",
                    "transition": "none",
                    "skip_reason": "No new durable work.",
                }
            ],
        }
    )
    runner = TaskAgentRunner(codex)

    for work_input in work_inputs:
        process_work_item(store, runner, work_input)

    assert [call["session_scope_id"] for call in codex.calls] == [
        "task-agent:work-tracking:v1",
        "task-agent:work-tracking:v1",
    ]
    assert codex.calls[0]["workload_key"] != codex.calls[1]["workload_key"]


def test_process_work_item_does_not_apply_decision_after_session_lease_loss(tmp_path):
    store = AutoReplyStore(tmp_path / "task-session-lease-lost.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    decision = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "source_excerpt": "补齐来源链接",
                "source_ref": item.source.ref,
                "title": "补齐报价来源链接",
                "missing_evidence": ["owner"],
            }
        ],
    }

    class LeaseLostBeforeApply:
        calls = 0

        def assert_owned(self):
            self.calls += 1
            if self.calls == 2:
                raise TaskAgentSessionLeaseLost("session lease expired")

    with pytest.raises(TaskAgentSessionLeaseLost):
        process_work_item(
            store,
            TaskAgentRunner(FakeCodex(decision)),
            work_input,
            session_lease=LeaseLostBeforeApply(),
        )

    assert not store.list_business_tasks()
    assert store.get_work_summary_input(input_id).status.value == "failed"
    with store._connect() as db:
        run = db.execute(
            "select status from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchone()
    assert run["status"] == "failed"


def test_process_work_item_success_commits_task_and_terminal_run_and_input(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "task-success-lifecycle.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    payload = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "source_excerpt": "补齐来源链接",
                "source_ref": item.source.ref,
                "title": "补齐报价来源链接",
                "missing_evidence": ["owner"],
            }
        ],
    }

    process_work_item(
        store, TaskAgentRunner(FakeCodexWithAuditEvents(payload, [])), work_input
    )

    assert len(store.list_business_tasks()) == 1
    assert store.get_work_summary_input(input_id).status.value == "done"
    with sqlite3.connect(tmp_path / "task-success-lifecycle.sqlite3") as db:
        run = db.execute(
            "select status, error from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchone()
    assert run == ("completed", "")


def test_process_work_item_persists_final_assessment_readback_without_rewriting_judgment(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "task-assessment-readback.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    payload = {
        "project_decisions": [],
        "project_assessments": [_stored_project_assessment(item, seed, anchor_id)],
        "task_decisions": [],
    }

    process_work_item(store, TaskAgentRunner(FakeCodex(payload)), work_input)

    with store._connect() as db:
        run = db.execute(
            "select decision_json, projection_json from task_agent_runs "
            "where summary_input_id=?",
            (input_id,),
        ).fetchone()
    stored_decision = json.loads(run["decision_json"])
    stored_projection = json.loads(run["projection_json"])
    assert stored_decision["project_assessments"][0]["outcome"] == "not_needed"
    assert (
        stored_decision["project_assessments"][0]["reason"]
        == payload["project_assessments"][0]["reason"]
    )
    assert "assessment_results" not in stored_decision
    assert stored_projection["status"] == "no_proposal"
    assert stored_projection["project_assessments"][0]["status"] == "recorded"
    assert stored_projection["project_assessments"][0]["anchor_id"] == anchor_id
    assert stored_projection["project_assessments"][0]["task_ids"] == [seed.task_id]


def test_work_item_accepts_task_routing_signals():
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1992",
                "title": "Riley",
                "conversation_id": "cid-lily",
                "conversation_title": "Riley",
                "created_at": "2026-06-28 09:44:05",
            },
            "summary": "Riley反馈海外数据合规P0追错owner。",
            "project_name": "",
            "context": {
                "sender": "Riley",
                "sender_user_id": "lily-user-1",
                "participants": ["Riley"],
                "source_conversation_kind": "direct",
                "source_conversation_title": "Riley",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": True,
                "progress_claim": False,
                "owner_correction": True,
                "complaint_about_followup": True,
                "signal_reason": "同一会话里有近期已发送follow-up，且用户反馈追错owner。",
            },
        }
    )

    assert item.task_signals.possible_task_update is True
    assert item.context.sender_user_id == "lily-user-1"
    assert item.task_signals.owner_correction is True
    assert item.task_signals.complaint_about_followup is True
    assert "追错owner" in item.task_signals.signal_reason


def test_task_agent_prompt_does_not_embed_candidate_specific_workflow():
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "local_file",
                "ref": "/tmp/刘芸婷一面.md#sha256=abc",
                "title": "刘芸婷 - 国际销售工程师（北京） - 一面",
                "conversation_id": "",
                "conversation_title": "",
                "created_at": "2026-07-10T13:59:28+08:00",
            },
            "summary": "刘芸婷一面记录显示需要判断后续推进状态。",
            "project_name": "刘芸婷国际销售工程师候选人评估与后续推进",
            "context": {
                "sender": "张静",
                "participants": ["张静", "Morgan", "刘芸婷"],
                "source_conversation_kind": "minutes",
                "source_conversation_title": "刘芸婷一面",
            },
            "task_signals": {
                "possible_task_update": True,
                "mentions_follow_up": False,
                "signal_reason": "关键候选人流程状态需要跟进。",
            },
        }
    )

    from app.task_source_documents import source_bundle

    prompt = build_task_agent_prompt(
        item, json.dumps(source_bundle((), current_work_item=item), ensure_ascii=False)
    )

    assert "xiaoqing_interview" not in prompt
    assert "当前阶段、最终决策、决策时间和决策说明" not in prompt
    assert "刘芸婷一面记录显示需要判断后续推进状态" in prompt


def test_process_work_item_continues_when_memory_connector_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.task_agent.memory_connector_config_issue",
        lambda: "memory connector token is expired",
    )
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodexWithAuditEvents(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "skip",
                    "transition": "none",
                    "skip_reason": "No source-grounded task.",
                }
            ],
        },
        audit_tool_events=[],
    )

    process_work_item(store, TaskAgentRunner(codex), work_input)

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        input_row = db.execute(
            "select status, error from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
        run_count = db.execute("select count(*) from task_agent_runs").fetchone()[0]
    assert input_row[0] == "skipped"
    assert run_count == 1
    assert "不可用：memory connector token is expired" in codex.prompts[0]
    assert (
        "Memory is background/discovery, not original observed evidence or human acceptance"
        in codex.prompts[0]
    )


def test_task_agent_codex_runner_uses_standard_runtime_factory():
    routed = FakeRoutedTaskExecution(
        json.dumps(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [
                    {
                        "action": "skip",
                        "transition": "none",
                        "skip_reason": "输入不足以形成稳定事项。",
                    }
                ],
            }
        )
    )
    runner = TaskAgentCodexRunner(routed_execution=routed)

    runner.decide(prompt="{}", workload_key="1")

    assert routed.calls[0]["workload_kind"] == "task"
    assert isinstance(routed.calls[0]["command_factory"], CodexCommandFactory)


def test_task_agent_prompt_loads_work_tracking_skill_and_schema_contract(monkeypatch):
    monkeypatch.setattr(
        "app.task_agent.WORK_TRACKING_SKILL_PATH",
        Path(__file__).parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md",
    )
    prompt = " ".join(build_task_agent_prompt(_work_item(), "无候选项目").split())
    assert "# CEO Work Tracking" in prompt
    assert '"title": "TaskAgentDecision"' in prompt
    assert "Memory connector status:" in prompt
    assert "read-only discovery" in prompt
    assert "Do not create, update, delete, send, or complete external records" in prompt
    assert "not an enforced" in prompt
    assert "not original observed evidence or human acceptance" in prompt
    assert "source-derived typed deadlines" in prompt
    assert "exact named person" in prompt
    assert "Memory is discovery/background, not observed source proof" in " ".join(
        prompt.split()
    )


def _current_task_guidance(surface, monkeypatch):
    import app.task_agent as task_agent

    monkeypatch.setattr(task_agent, "load_skill_text", lambda paths: "")
    text = (
        build_task_agent_prompt(_work_item(), "候选上下文为空。")
        if surface == "prompt"
        else (
            Path(__file__).parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md"
        ).read_text()
    )
    return " ".join(text.split())


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_task_agent_source_authority_and_independent_project_output(
    monkeypatch, surface
):
    text = _current_task_guidance(surface, monkeypatch)
    assert "project_decisions" in text
    assert "Project can have zero Tasks" in text
    assert (
        "Adopt the exact current authoritative Project definition with registration to register or reuse"
        in text
    )
    assert (
        "A different stored name cannot replace that definition merely because the action uses its shorter name"
        in text
    )
    assert "project_proposal" not in text
    assert "project_link_proposal" not in text
    assert "related_task_ids" not in text
    assert "Chats/emails" in text
    assert "A bare responsibility clause" in text
    assert "only independently evidenced, distinct work" in text
    assert (
        "one overall owner" in text
        if surface == "prompt"
        else "one `overall_owner`" in text
    )


def test_action_and_date_guidance_is_delivered_next_to_output_fields(monkeypatch):
    from app.task_models import TaskDecision, TaskDateEvidence

    text = _current_task_guidance("prompt", monkeypatch)
    action_field = TaskDecision.model_json_schema()["properties"]["source_excerpt"]
    date_fields = TaskDateEvidence.model_json_schema()["properties"]
    checks = [
        (
            action_field,
            "not Project registration scope already covered by concrete actions",
        ),
        (date_fields["value"], "Do not move Project registry deadlines onto Tasks"),
        (
            date_fields["source_excerpt"],
            "Quote only the complete parseable date phrase",
        ),
        (date_fields["actor_user_id"], "trusted WorkItem.context.sender_user_id"),
        (date_fields["actor_name"], "Report/document names are not date actors"),
    ]
    for field, rule in checks:
        descriptions = [
            field.get("description", ""),
            *[branch.get("description", "") for branch in field.get("anyOf", [])],
        ]
        assert any(rule in description for description in descriptions)
        assert any(
            rule in description and " ".join(description.split()) in text
            for description in descriptions
        )


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_task_agent_original_history_and_current_evidence_guidance(
    monkeypatch, surface
):
    text = _current_task_guidance(surface, monkeypatch)
    assert "historical_comparison" in text
    assert "null-ID" in text
    assert "positive" in text
    assert "original" in text
    assert (
        "not session or Memory provenance" in text
        if surface == "prompt"
        else "not memory_provenance or session_provenance" in text
    )
    assert "uncertainty" in text
    assert "current_project_attention" in text
    assert "assessment_json.evidence" in text
    assert "source_ref" in text and "source_excerpt" in text


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_task_agent_existing_project_link_contract_is_distinct_from_registration(
    monkeypatch, surface
):
    text = _current_task_guidance(surface, monkeypatch)
    assert "project_link_evidence" in text
    assert "project_decision_index" in text
    assert (
        "confirmed links" in text
        if surface == "skill"
        else "existing confirmed association" in text
    )
    assert "registration" in text
    assert (
        "`Task.project` selects an actual `anchor_id` or `project_decision_index`"
        in text
        if surface == "skill"
        else "Task.project uses anchor_id or project_decision_index" in text
    )


@pytest.mark.parametrize("action", ["record_candidate", "update_task"])
def test_project_link_proof_does_not_change_task_effect_identity(action):
    from app.task_agent import _task_source_signal

    item = _work_item()
    base = {
        "action": action,
        "transition": "update_fields" if action == "update_task" else "none",
        "task_id": 1 if action == "update_task" else None,
        "source_ref": item.source.ref,
        "source_excerpt": "复核验收及付款计划。",
        "title": "复核验收付款计划",
        "description": "当前来源补充。",
        "project": {"anchor_id": 1},
        "project_link_evidence": [
            {"source_ref": item.source.ref, "source_excerpt": "复核验收及付款计划"}
        ],
    }
    changed = {
        **base,
        "project": {"anchor_id": 2},
        "project_link_evidence": [
            {"source_ref": item.source.ref, "source_excerpt": "另一份原始归属证明"}
        ],
    }
    # Project association has its own immutable source proof and durable relationship,
    # not a second Task creation/update command identity.
    assert _task_source_signal(item, TaskDecision.model_validate(base)).dedupe_key == (
        _task_source_signal(item, TaskDecision.model_validate(changed)).dedupe_key
    )


@pytest.mark.parametrize("surface", ["prompt", "skill", "schema"])
def test_negative_assessment_guidance_distinguishes_normal_progress_from_missing_facts(
    monkeypatch, surface
):
    from app.task_models import TaskProjectAssessment

    text = (
        " ".join(TaskProjectAssessment.model_fields["outcome"].description.split())
        if surface == "schema"
        else _current_task_guidance(surface, monkeypatch)
    )
    assert (
        "normal progress" in text
        if surface != "prompt"
        else "routine progress is not_needed" in text
    )
    assert "insufficient_evidence" in text
    assert (
        "No Tasks or no reported risk alone is not insufficient_evidence" in text
        if surface == "schema"
        else (
            "No reported risk or zero Tasks alone is not missing evidence" in text
            if surface == "skill"
            else "No Task is not insufficient evidence" in text
        )
    )


def test_existing_attention_schema_explains_original_proof_not_upsert_target():
    from app.task_models import TaskProjectAssessment

    description = TaskProjectAssessment.model_fields[
        "existing_attention_id"
    ].description
    assert "original-proof claim, not an update target" in description
    assert "Project key reuses the existing card" in description
    assert (
        "null unless you cite and verify that card's stored original evidence"
        in description
    )


@pytest.mark.parametrize("surface", ["prompt", "skill"])
def test_task_agent_owns_one_attention_proposal_in_assessment_without_task_carrier(
    monkeypatch, surface
):
    text = _current_task_guidance(surface, monkeypatch)
    assert "attention_proposal" in text
    assert (
        "one assessment" in text if surface == "skill" else "one factual reason" in text
    )
    assert (
        "never copy it onto Task decisions" in text
        if surface == "skill"
        else "Attention belongs once to its assessment" in text
    )
    assert "zero Tasks" in text
    assert "not_needed" in text
    assert "not mean" in text if surface == "prompt" else "does not imply" in text
    assert "Project-level risk facts" in text
    assert "not per-Task action summaries" in text


def test_quote_schema_descriptions_preserve_source_line_breaks():
    schema = TaskAgentDecision.model_json_schema()
    for model in ("TaskAttentionEvidence", "TaskDecision"):
        field = schema["$defs"][model]["properties"]["source_excerpt"]
        description = " ".join(
            [
                field.get("description", ""),
                *[branch.get("description", "") for branch in field.get("anyOf", [])],
            ]
        )
        assert "contiguous verbatim" in description
        assert "line breaks" in description


def test_attention_current_state_schema_describes_shared_project_facts():
    from app.task_models import TaskAttentionProposal

    description = TaskAttentionProposal.model_fields["current_state"].description
    assert "Project-level risk facts, not per-Task action summaries" in description
    assert "one Project assessment, not each supporting Task" in description


def test_task_agent_prompt_allows_initial_risk_with_project_registered_this_turn(
    monkeypatch,
):
    text = _current_task_guidance("prompt", monkeypatch)
    assert "meeting decision, or updates an existing active anchor" in text
    assert (
        "First assessment of a source-observed unresolved material business risk"
        in text
    )
    assert (
        "require a prior card or a fresh delta against a nonexistent assessment" in text
    )
    assert (
        "An existing card already reflecting the same facts does not need a new proposal"
        in text
    )
    assert (
        "Tasks may support Attention without a formal owner or accepted commitment"
        in text
    )
    assert (
        "Labels, relevance, routine progress, and date proximity alone do not explain material impact"
        in text
    )
    assert (
        "Never invent a Task, owner, assignment, commitment, or date to fill a card"
        in text
    )


def test_fresh_task_agent_loads_current_project_contract_from_selected_skill_root(
    monkeypatch,
):
    import runpy

    root = Path(__file__).parents[1]
    monkeypatch.setenv("CEO_SKILLS_ROOT", str(root / "ci/shared-skills"))
    fresh_agent = runpy.run_path(str(root / "app/task_agent.py"))
    skill = fresh_agent["WORK_TRACKING_SKILL_PATH"].read_text()
    prompt = fresh_agent["build_task_agent_prompt"](_work_item(), "无候选项目")
    assert skill in prompt
    assert "version: 5" in prompt
    assert "project_decisions" in prompt
    assert "Project can have zero Tasks" in prompt
    assert "project_proposal" not in prompt
    assert "project_link_proposal" not in prompt
    assert "repeat the identical" not in prompt


def test_task_agent_prompt_uses_scheduled_consumer_prompt_and_targeted_skill(
    monkeypatch,
):
    import app.task_agent as task_agent

    skill_path = (
        Path(__file__).parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md"
    )
    monkeypatch.setattr(task_agent, "WORK_TRACKING_SKILL_PATH", skill_path)
    payload = _work_item().model_dump(mode="json")
    payload["scheduled_consumer"] = {
        "schema": "scheduled_consumer.v1",
        "scheduled_task_id": 7,
        "scheduled_task_run_id": 11,
        "prompt": "只处理 $ceo-work-tracking 能确认的真实工作项。",
        "skill_names": ["ceo-work-tracking"],
        "skill_protocol": "# Old Work Tracking Snapshot\nReturn update_project with todo_changes.",
    }
    item = WorkItem.model_validate(payload)
    original = item.scheduled_consumer.copy()
    prompt = build_task_agent_prompt(item, "无候选项目")
    assert "## Scheduled Consumer Prompt\n只处理" in prompt
    assert "# Old Work Tracking Snapshot" not in prompt
    assert "Return update_project with todo_changes." not in prompt
    assert '"scheduled_task_run_id": 11' in prompt
    assert "version: 5" in prompt
    assert "native CLI manages compaction" in prompt
    assert (
        "current independent Project/Task/assessment envelope controls output" in prompt
    )
    assert item.scheduled_consumer == original


def test_task_agent_prompt_does_not_inject_retrieved_business_examples():
    work_item = _work_item(project_name="宝马项目客户 Demo 推进")
    work_item.summary = "宝马项目周末攻坚要准备客户 Demo 原型，之前拆给测试不对。"
    prompt = build_task_agent_prompt(work_item, candidate_prompt="候选上下文为空。")
    assert "可召回样例" not in prompt
    assert "不要把“做原型”拆给测试" not in prompt
    assert "候选上下文为空。" in prompt


def test_process_work_item_does_not_require_memory_recall_receipt(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item("客户交付")
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    update = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "skip",
                "transition": "none",
                "skip_reason": "No source-grounded task; memory receipt is not a service gate.",
            }
        ],
    }

    class CodexWithoutMemoryRecallReceipt(FakeCodexWithAuditEvents):
        def __init__(self):
            super().__init__(update, [])

    codex = CodexWithoutMemoryRecallReceipt()
    process_work_item(store, TaskAgentRunner(codex), work_input)

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        input_row = db.execute(
            "select status, error from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
        runs = db.execute(
            "select status, error from task_agent_runs "
            "where summary_input_id=? order by id",
            (input_id,),
        ).fetchall()

    assert input_row[0] == "skipped"
    assert runs == [("completed", "")]
    assert len(codex.prompts) == 1


def test_process_work_item_rolls_back_batch_and_marks_input_and_run_failed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "task-batch-failure.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    payload = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "source_excerpt": "补齐来源链接",
                "source_ref": item.source.ref,
                "title": "有效候选",
                "missing_evidence": ["owner"],
            },
            {
                "action": "record_candidate",
                "transition": "none",
                "source_excerpt": "另一个来源里的话",
                "source_ref": "other-source-ref",
                "title": "必须回滚",
                "missing_evidence": ["owner"],
            },
        ],
    }
    codex = FakeCodexWithAuditEvents(payload, [])

    process_work_item(store, TaskAgentRunner(codex), work_input)

    assert len(store.list_business_tasks()) == 2
    with sqlite3.connect(tmp_path / "task-batch-failure.sqlite3") as db:
        input_status = db.execute(
            "select status from work_summary_inputs where id=?", (input_id,)
        ).fetchone()[0]
        run = db.execute(
            "select status, error from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchone()
    assert input_status == "done"
    assert run[0] == "completed"
    assert run[1] == ""


def test_process_work_item_accepts_none_session_id(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodexWithoutSession(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {"action": "skip", "transition": "none", "skip_reason": "一次性对话。"}
            ],
        }
    )

    process_work_item(store, TaskAgentRunner(codex), work_input)

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        run_row = db.execute(
            "select summary_input_id, codex_session_id from task_agent_runs",
        ).fetchone()
    assert run_row == (input_id, "")


def test_task_agent_codex_runner_parses_jsonl_payload(tmp_path):
    from app.task_agent import TaskAgentCodexRunner

    def executor(command, prompt):
        return "\n".join(
            [
                json.dumps(
                    {"type": "session_meta", "payload": {"id": "session-task-1"}}
                ),
                json.dumps(
                    {
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps(
                                {
                                    "project_decisions": [],
                                    "project_assessments": [],
                                    "update_summary": "本轮没有相关 Project。",
                                    "task_decisions": [
                                        {
                                            "action": "skip",
                                            "transition": "none",
                                            "skip_reason": "没有状态变化",
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    },
                    ensure_ascii=False,
                ),
            ]
        )

    runner = TaskAgentCodexRunner(
        routed_execution=FakeRoutedTaskExecution(
            executor([], "x"), session_id="session-task-1"
        )
    )
    decision = runner.decide(prompt="x", workload_key="1")

    assert decision.task_decisions[0].action == "skip"
    assert runner.last_session_id == "session-task-1"


def test_task_agent_codex_runner_parses_response_item_output_text(tmp_path):
    from app.task_agent import TaskAgentCodexRunner

    def executor(command, prompt):
        return "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "session-task-2"}),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "project_decisions": [],
                                            "project_assessments": [],
                                            "update_summary": "本轮没有相关 Project。",
                                            "task_decisions": [
                                                {
                                                    "action": "skip",
                                                    "transition": "none",
                                                    "skip_reason": "只是确认收到",
                                                }
                                            ],
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        )

    runner = TaskAgentCodexRunner(
        routed_execution=FakeRoutedTaskExecution(
            executor([], "x"), session_id="session-task-2"
        )
    )
    decision = runner.decide(prompt="x", workload_key="1")

    assert decision.task_decisions[0].action == "skip"
    assert decision.task_decisions[0].skip_reason == "只是确认收到"
    assert runner.last_session_id == "session-task-2"


def test_task_agent_prompt_schema_is_generated_from_validation_model():
    prompt = build_task_agent_prompt(
        WorkItem.model_validate(
            {
                "project_decisions": [],
                "source": {"type": "local_file", "ref": "schema-test"},
                "summary": "schema contract test",
                "context": {"source_conversation_kind": "file"},
            }
        ),
        "候选项目:\n[]\n\n近期 follow-up 候选:\n[]",
    )
    schema_text = prompt.split("TaskAgentDecision Pydantic JSON schema:\n", 1)[1]
    prompt_schema, _ = json.JSONDecoder().raw_decode(schema_text.lstrip())

    assert prompt_schema == task_agent_output_schema()


def test_task_agent_codex_runner_uses_routed_execution_contract():
    routed = FakeRoutedTaskExecution(
        json.dumps(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [
                    {
                        "action": "skip",
                        "transition": "none",
                        "skip_reason": "没有状态变化",
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    runner = TaskAgentCodexRunner(routed_execution=routed)

    decision = runner.decide(prompt="decide", workload_key="9")

    assert decision.task_decisions[0].action == "skip"
    assert routed.calls[0]["workload_key"] == "9"
    assert routed.calls[0]["conversation_id"] is None
    command_factory = routed.calls[0]["command_factory"]
    assert command_factory.use_output_schema is True
    assert command_factory.output_schema_path == (
        Path(__file__).resolve().parents[1]
        / "app"
        / "schemas"
        / "task_agent_decision.schema.json"
    )
    assert routed.calls[0]["required_capabilities"] == frozenset(
        {
            "structured_output",
            "local_schema_validation",
        }
    )


def test_task_agent_codex_runner_requires_injected_execution():
    with pytest.raises(TypeError):
        TaskAgentCodexRunner()


def test_task_agent_codex_runner_reads_audit_events_from_session():
    routed = FakeRoutedTaskExecution(
        json.dumps(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [
                    {
                        "action": "skip",
                        "transition": "none",
                        "skip_reason": "无需记录候选人 follow-up。",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        session_id="019f0000-0000-7000-8000-000000000000",
        transcript_end=8,
    )
    runner = TaskAgentCodexRunner(routed_execution=routed)
    observed_limits = []

    def fake_session_events(session_id, start_line=0, end_line=None, limit=40):
        observed_limits.append(limit)
        if limit <= 40:
            return [{"tool": "exec_command", "arguments": "{}"}]
        return [{"tool": "mcp__memory_connector__memory_recall", "arguments": "{}"}]

    runner._extract_codex_audit_events_from_session = fake_session_events

    decision = runner.decide(prompt="decide", workload_key="1")

    assert decision.task_decisions[0].action == "skip"
    assert runner.last_transcript_start_line == 0
    assert runner.last_transcript_end_line == 8
    assert observed_limits == [200]
    assert runner.last_audit_tool_events == [
        {"tool": "mcp__memory_connector__memory_recall", "arguments": "{}"}
    ]


def test_task_agent_codex_runner_propagates_routed_failure():
    from app.agent_runtime_router import RoutedCodexExecutionError

    class FailingRoutedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError("runtime_execution_failed")

    runner = TaskAgentCodexRunner(routed_execution=FailingRoutedExecution())

    with pytest.raises(RoutedCodexExecutionError, match="runtime_execution_failed"):
        runner.decide(prompt="decide", workload_key="1")


def test_task_agent_codex_runner_translates_exhausted_transport_for_outer_retry():
    from app.agent_runtime_contracts import RuntimeFailureClass
    from app.agent_runtime_router import RoutedCodexExecutionError
    from app.external_retry import ExternalDependencyError

    class FailingRoutedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                failure_class=RuntimeFailureClass.TRANSPORT,
                failure_code="codex_total_timeout",
                retryable_external_dependency=True,
            )

    runner = TaskAgentCodexRunner(routed_execution=FailingRoutedExecution())

    with pytest.raises(ExternalDependencyError) as raised:
        runner.decide(prompt="decide", workload_key="1")
    assert raised.value.dependency == "codex"


@pytest.mark.parametrize(
    "failure_code", ["codex_login_required", "runtime_result_invalid"]
)
def test_task_agent_codex_runner_keeps_nonretryable_routed_failures_terminal(
    failure_code,
):
    from app.agent_runtime_router import RoutedCodexExecutionError

    class FailingRoutedExecution:
        def execute(self, **kwargs):
            raise RoutedCodexExecutionError(
                "runtime_execution_failed",
                failure_code=failure_code,
                retryable_external_dependency=False,
            )

    runner = TaskAgentCodexRunner(routed_execution=FailingRoutedExecution())
    with pytest.raises(RoutedCodexExecutionError) as raised:
        runner.decide(prompt="decide", workload_key="1")
    assert raised.value.failure_code == failure_code


def test_task_agent_parser_finds_decision_embedded_in_prose():
    decision = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "skip",
                "transition": "none",
                "skip_reason": "No source-grounded task was found.",
            }
        ],
    }
    message = (
        "Based on my search within the allowed sources, I found:\n\n"
        "1. **Memory**: background only {not a decision}.\n\n"
        + json.dumps(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [],
            }
        )
        + "\n\nFinal decision:\n\n"
        + json.dumps(decision, indent=2)
        + "\n"
    )
    raw = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": message},
        }
    )

    assert _parse_task_agent_decision(raw) == TaskAgentDecision.model_validate(decision)
    assert _parse_task_agent_decision(message) == TaskAgentDecision.model_validate(
        decision
    )


def _repair_codex(payloads):
    class RepairingCodex(FakeCodexWithAuditEvents):
        def __init__(self):
            super().__init__(payloads[0], [])
            self.payloads = list(payloads)
            self.calls = 0

        def decide(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            payload = self.payloads[self.calls]
            self.calls += 1
            self.last_audit_tool_events = (
                [] if self.calls == 1 else [{"tool": "memory_recall"}]
            )
            return TaskAgentDecision.model_validate(payload)

    return RepairingCodex()


def _claimed_work_input(store):
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    return input_id, store.claim_work_summary_inputs(limit=1)[0]


def _work_item(project_name="售前知识库", **context):
    return WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "message:1",
                "title": project_name,
                "conversation_id": "conversation:1",
                "conversation_title": "客户群",
                "created_at": "2026-09-20T09:00:00+08:00",
            },
            "summary": "补齐来源链接；owner 是 Alex。",
            "context": {
                "sender": "Avery",
                "sender_user_id": "avery-id",
                "source_conversation_kind": "group",
                **context,
            },
        }
    )


def _candidate_decision(item, *, excerpt="补齐来源链接", title="补齐报价来源链接"):
    return TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": excerpt,
                    "source_ref": item.source.ref,
                    "title": title,
                    "missing_evidence": ["owner"],
                }
            ],
        }
    )


def test_process_work_item_repairs_owner_citation_before_atomic_apply(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "owner-citation.sqlite3")
    item = _work_item(assignment_authorized=True).model_copy(
        update={
            "summary": "Avery: Please prepare the report. Alex: I will prepare it.",
        }
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    bad = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "No relevant Project is present in this source.",
        "task_decisions": [
            {
                "action": "create_task",
                "transition": "none",
                "title": "Prepare report",
                "source_ref": item.source.ref,
                "source_excerpt": item.summary,
                "formal_basis": "explicit_assignment",
                "owner_name": "Alex",
                "owner_evidence": {
                    "source_ref": item.source.ref,
                    "excerpt": "Avery: Please prepare the report.",
                },
            }
        ],
    }

    class RepairingCodex(FakeCodex):
        def decide(self, **kwargs):
            if self.calls:
                assert not store.list_business_tasks()
                self.payload = json.loads(json.dumps(bad))
                self.payload["task_decisions"][0]["owner_evidence"]["excerpt"] = (
                    "Alex: I will prepare it."
                )
            return super().decide(**kwargs)

    codex = RepairingCodex(bad)
    process_work_item(
        store, TaskAgentRunner(codex), store.claim_work_summary_inputs(limit=1)[0]
    )

    assert len(codex.calls) == 2
    assert codex.calls[1]["workload_key"] == codex.calls[0]["workload_key"] + ":decision_repair.1"
    assert codex.calls[0]["session_scope_id"] == codex.calls[1]["session_scope_id"]
    assert "owner identity" in codex.prompts[1]
    assert len(store.list_business_tasks()) == 1
    assert store.get_work_summary_input(input_id).status.value == "done"


def test_owner_citation_repair_is_bounded_and_never_applies_invalid_owner(
    tmp_path, monkeypatch
):
    from app.task_agent import TASK_DECISION_REPAIR_ROUNDS, TaskDecisionRepairExhausted

    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "")
    store = AutoReplyStore(tmp_path / "owner-repair-exhausted.sqlite3")
    item = _work_item(assignment_authorized=True)
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    codex = FakeCodex(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "No relevant Project is present in this source.",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "title": "Prepare report",
                    "source_ref": item.source.ref,
                    "source_excerpt": item.summary,
                    "formal_basis": "explicit_assignment",
                    "owner_name": "Unidentified",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "Avery assigns it.",
                    },
                }
            ],
        }
    )

    with pytest.raises(TaskDecisionRepairExhausted):
        process_work_item(
            store, TaskAgentRunner(codex), store.claim_work_summary_inputs(limit=1)[0]
        )

    assert len(codex.calls) == 1 + TASK_DECISION_REPAIR_ROUNDS
    assert not store.list_business_tasks()
    assert store.get_work_summary_input(input_id).status.value == "failed"


@pytest.mark.parametrize("registry", [False, True])
def test_retired_project_anchor_does_not_fail_task_or_get_reactivated(
    tmp_path, registry
):
    import hashlib

    store = AutoReplyStore(tmp_path / "retired-anchor.sqlite3")
    title = "Report preparation"
    key = hashlib.sha256(title.casefold().encode()).hexdigest()
    resolver = BusinessResolutionService(store)
    anchor_id = resolver.register_anchor(
        anchor_type="project",
        anchor_ref=f"task-agent-project:{key}",
        title=title,
        active=False,
    )
    row = f"| Alex | {title} | Prepare report |"
    item = _work_item(assignment_authorized=True).model_copy(
        update={
            "source": _work_item().source.model_copy(
                update={
                    "type": (
                        WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT
                        if registry
                        else WorkItemSourceType.AI_MINUTES
                    ),
                }
            ),
            "summary": (
                json.dumps(
                    {
                        "markdown": (
                            f"## **手头项目**\n\n| 负责人 | 项目 | 当前状态 |\n|---|---|---|\n{row}\n"
                        )
                    }
                )
                if registry
                else row
            ),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": title,
                        "reason": "Source assigns report preparation",
                        "authority": (
                            "management_weekly_report"
                            if registry
                            else "meeting_decision"
                        ),
                        "source_excerpt": row,
                    },
                    "reason": "采用权威项目定义",
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "project_assessments": [
                {
                    "project_title": title,
                    "project_decision_index": 0,
                    "outcome": "insufficient_evidence",
                    "reason": "The cited Project is retired and has no active official identity.",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "title": "Prepare report",
                    "source_ref": item.source.ref,
                    "source_excerpt": row,
                    "formal_basis": "explicit_assignment",
                    "owner_name": "Alex",
                    "owner_evidence": {"source_ref": item.source.ref, "excerpt": row},
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert len(result.task_ids) == 1
    assert not store.list_business_projects()
    assert not store.list_business_task_anchor_links(task_id=result.task_ids[0])
    assert any("retired" in reason for reason in result.skipped_reasons)
    with store._connect() as db:
        assert (
            db.execute(
                "select active from business_anchors where id=?", (anchor_id,)
            ).fetchone()[0]
            == 0
        )


def test_report_row_outside_registry_cannot_register_retired_project(tmp_path):
    import hashlib

    store = AutoReplyStore(tmp_path / "outside-registry-retired.sqlite3")
    title = "Report preparation"
    key = hashlib.sha256(title.casefold().encode()).hexdigest()
    anchor_id = BusinessResolutionService(store).register_anchor(
        anchor_type="project",
        anchor_ref=f"task-agent-project:{key}",
        title=title,
        active=False,
    )
    row = f"| Alex | {title} | Prepare report |"
    item = _work_item(assignment_authorized=True).model_copy(
        update={
            "source": _work_item().source.model_copy(
                update={
                    "type": WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT,
                }
            ),
            "summary": json.dumps({"markdown": row}),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": title,
                        "reason": "Source assigns report preparation",
                        "authority": "management_weekly_report",
                        "source_excerpt": row,
                    },
                    "reason": "采用权威项目定义",
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "project_assessments": [
                {
                    "project_title": title,
                    "project_decision_index": 0,
                    "outcome": "insufficient_evidence",
                    "reason": "The report row is outside an authoritative Project registry.",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "title": "Prepare report",
                    "source_ref": item.source.ref,
                    "source_excerpt": row,
                    "formal_basis": "explicit_assignment",
                    "owner_name": "Alex",
                    "owner_evidence": {"source_ref": item.source.ref, "excerpt": row},
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="must match the cited report registry row"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == ()
    assert store.list_business_projects() == []
    with store._connect() as db:
        assert (
            db.execute(
                "select active from business_anchors where id=?", (anchor_id,)
            ).fetchone()[0]
            == 0
        )


def test_current_ai_minutes_provenance_is_canonical_and_not_external_todo():
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={
                    "type": WorkItemSourceType.AI_MINUTES,
                    "ref": "minutes:1#todos-sha256=abc",
                }
            )
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "source_excerpt": "补齐来源链接",
                    "source_ref": "wrong-ref",
                    "title": "补齐报价来源链接",
                    "formal_basis": "external_todo",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": "wrong-ref",
                        "excerpt": "Alex 负责补齐来源链接",
                    },
                }
            ],
        }
    )

    normalized = _canonicalize_current_source_provenance(decision, work_item=item)
    normalized_item = normalized.task_decisions[0]
    assert normalized_item.source_ref == item.source.ref
    assert normalized_item.owner_evidence["source_ref"] == item.source.ref
    assert normalized_item.formal_basis is FormalTaskBasis.MEETING_ACTION_ITEM


def test_current_ai_minutes_commitment_is_canonicalized_to_meeting_action_item():
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={
                    "type": WorkItemSourceType.AI_MINUTES,
                    "ref": "minutes:health-metrics#todos-sha256=abc",
                }
            ),
            "context": base.context.model_copy(
                update={
                    "source_conversation_kind": WorkItemSourceKind.MINUTES,
                }
            ),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "source_excerpt": "张玲玲更新数据",
                    "source_ref": item.source.ref,
                    "title": "更新健康度数据",
                    "formal_basis": "explicit_commitment",
                    "owner_name": "张玲玲",
                    "owner_kind": "individual",
                    "owner_relation": "self_commitment",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "张玲玲更新数据",
                    },
                }
            ],
        }
    )
    normalized = _canonicalize_current_source_provenance(decision, work_item=item)
    assert (
        normalized.task_decisions[0].formal_basis is FormalTaskBasis.MEETING_ACTION_ITEM
    )


def test_current_ai_minutes_assignment_is_canonicalized_to_meeting_action_item():
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={
                    "type": WorkItemSourceType.AI_MINUTES,
                    "ref": "minutes:health-metrics#todos-sha256=abc",
                }
            ),
            "context": base.context.model_copy(
                update={
                    "source_conversation_kind": WorkItemSourceKind.MINUTES,
                }
            ),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "source_excerpt": "张玲玲更新数据",
                    "source_ref": item.source.ref,
                    "title": "更新健康度数据",
                    "formal_basis": "explicit_assignment",
                    "owner_name": "张玲玲",
                    "owner_kind": "individual",
                    "owner_relation": "explicit_assignment",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "张玲玲更新数据",
                    },
                }
            ],
        }
    )
    normalized = _canonicalize_current_source_provenance(decision, work_item=item)
    assert (
        normalized.task_decisions[0].formal_basis is FormalTaskBasis.MEETING_ACTION_ITEM
    )


@pytest.mark.parametrize(
    "owner_kind, owner_relation, expected_action",
    [
        ("individual", "explicit_assignment", "create_task"),
        ("individual", "self_commitment", "create_task"),
        ("individual", "meeting_summary_action_item", "create_task"),
        ("team", "explicit_assignment", "record_candidate"),
        ("individual", "speaker_only", "record_candidate"),
    ],
)
def test_ai_minutes_owner_relation_controls_formal_task_promotion(
    owner_kind, owner_relation, expected_action
):
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={
                    "type": WorkItemSourceType.AI_MINUTES,
                    "ref": "minutes:marketing#todos-sha256=abc",
                }
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": "明天补齐技术问题并预约沟通",
                    "source_ref": "wrong-ref",
                    "title": "补齐技术问题并预约沟通",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": "wrong-ref",
                        "excerpt": "Alex：我来负责补齐技术问题并预约沟通",
                    },
                    "owner_kind": owner_kind,
                    "owner_relation": owner_relation,
                }
            ],
        }
    )

    normalized = _canonicalize_current_source_provenance(decision, work_item=item)

    normalized_item = normalized.task_decisions[0]
    assert normalized_item.action == expected_action
    if expected_action == "create_task":
        assert normalized_item.formal_basis is FormalTaskBasis.MEETING_ACTION_ITEM
    else:
        assert normalized_item.formal_basis is None


def test_session_provenance_is_not_rewritten():
    item = _work_item()
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "evidence_origin": "session",
                    "source_excerpt": "旧来源证据",
                    "source_ref": "message:original",
                    "source_description": "群聊 / Avery",
                    "title": "候选任务",
                    "missing_evidence": ["owner"],
                }
            ],
        }
    )

    normalized = _canonicalize_current_source_provenance(decision, work_item=item)
    assert normalized.task_decisions[0].source_ref == "message:original"


def test_parser_accepts_zero_to_many_task_decisions():
    assert (
        _parse_task_agent_decision(
            '{"project_decisions": [], "project_assessments": [], "update_summary": "本轮没有相关 Project。", "task_decisions": []}'
        ).task_decisions
        == []
    )
    decision = _parse_task_agent_decision(
        json.dumps(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [
                    {"action": "skip", "transition": "none", "skip_reason": "no task"},
                    {
                        "action": "record_candidate",
                        "transition": "none",
                        "source_excerpt": "补齐来源链接",
                        "source_ref": "message:1",
                        "title": "补齐来源链接",
                        "missing_evidence": ["owner"],
                    },
                ],
            },
            ensure_ascii=False,
        )
    )
    assert len(decision.task_decisions) == 2


def test_parser_rejects_project_first_decision():
    with pytest.raises(ValueError, match="No TaskAgentDecision"):
        _parse_task_agent_decision('{"action":"update_project","project":{"id":1}}')


def test_multi_decision_batch_records_each_source_grounded_item(tmp_path):
    store = AutoReplyStore(tmp_path / "multi-task.sqlite3")
    item = _work_item(assignment_authorized=True)
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": "补齐来源链接",
                    "source_ref": item.source.ref,
                    "title": "补齐报价来源链接",
                    "missing_evidence": ["owner"],
                },
                {
                    "action": "create_task",
                    "transition": "none",
                    "source_excerpt": "owner 是 Alex",
                    "source_ref": item.source.ref,
                    "title": "确认负责人",
                    "formal_basis": "explicit_assignment",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "owner 是 Alex",
                    },
                },
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert len(result.task_ids) == 2
    assert [task.stage.value for task in store.list_business_tasks()] == [
        "candidate",
        "formal",
    ]
    assert len(store.list_business_task_signals()) == 2


def test_explicit_meeting_project_proposal_registers_project_and_links_task(tmp_path):
    store = AutoReplyStore(tmp_path / "meeting-project.sqlite3")
    base = _work_item(assignment_authorized=True, source_conversation_kind="minutes")
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "summary": json.dumps(
                {
                    "meeting": {
                        "summary": "会议明确决定启动美国客户成交项目，并由 Alex 负责报价"
                    }
                },
                ensure_ascii=True,
            ),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "美国客户成交",
                        "reason": "会议明确决定启动该项目",
                        "authority": "meeting_decision",
                        "source_excerpt": "会议明确决定启动美国客户成交项目，并由 Alex 负责报价",
                    },
                    "reason": "采用权威项目定义",
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议明确决定启动美国客户成交项目，并由 Alex 负责报价",
                        }
                    ],
                }
            ],
            "project_assessments": [
                {
                    "project_title": "美国客户成交",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "本轮为正常立项和报价行动，没有额外重大风险。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议明确决定启动美国客户成交项目，并由 Alex 负责报价",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "source_excerpt": "会议明确决定启动美国客户成交项目，并由 Alex 负责报价",
                    "source_ref": item.source.ref,
                    "title": "准备美国客户报价",
                    "formal_basis": "explicit_assignment",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "Alex 负责报价",
                    },
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议明确决定启动美国客户成交项目，并由 Alex 负责报价",
                        }
                    ],
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert len(result.task_ids) == 1
    projects = store.list_business_projects()
    assert len(projects) == 1
    assert projects[0].title == "美国客户成交"
    assert projects[0].registry_source.startswith("meeting_decision:")
    assert [
        link.anchor_id
        for link in store.list_business_task_anchor_links(task_id=result.task_ids[0])
    ] == [projects[0].canonical_anchor_id]
    assert (
        store.get_business_task(result.task_ids[0]).business_relevance.value
        == "relevant"
    )

    replay = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=decision, record_run=False
    )
    assert replay.task_ids == result.task_ids
    assert len(store.list_business_projects()) == 1
    assert len(store.list_business_task_anchor_links(task_id=result.task_ids[0])) == 1

    second_item = item.model_copy(
        update={
            "source": item.source.model_copy(update={"ref": "minutes:second"}),
        }
    )
    second_payload = decision.model_dump(mode="json")
    second_payload["project_decisions"][0]["evidence"][0]["source_ref"] = (
        second_item.source.ref
    )
    second_payload["project_assessments"][0]["evidence"][0]["source_ref"] = (
        second_item.source.ref
    )
    second_payload["task_decisions"][0]["source_ref"] = second_item.source.ref
    second_payload["task_decisions"][0]["project_link_evidence"][0]["source_ref"] = (
        second_item.source.ref
    )
    second_payload["task_decisions"][0]["owner_evidence"]["source_ref"] = (
        second_item.source.ref
    )
    second_decision = TaskAgentDecision.model_validate(second_payload)
    second_result = apply_task_agent_decision(
        store,
        summary_input_id=3,
        work_item=second_item,
        decision=second_decision,
        record_run=False,
    )
    assert len(store.list_business_projects()) == 1
    assert (
        len(store.list_business_task_anchor_links(task_id=second_result.task_ids[0]))
        == 1
    )


def test_generic_department_project_proposal_is_not_promoted(tmp_path):
    store = AutoReplyStore(tmp_path / "generic-project.sqlite3")
    item = _work_item(assignment_authorized=True).model_copy(
        update={
            "source": _work_item().source.model_copy(
                update={"type": WorkItemSourceType.PROJECT_WEEKLY_REPORT}
            ),
            "summary": "## 项目管理部\n补齐来源链接；owner 是 Alex。",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "项目管理部",
                        "reason": "报告章节标题",
                        "authority": "project_weekly_report",
                        "source_excerpt": "项目管理部",
                    },
                    "reason": "采用权威项目定义",
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": "项目管理部"}
                    ],
                }
            ],
            "project_assessments": [
                {
                    "project_title": "项目管理部",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "该标题不构成有效 Project 登记，且没有重大风险。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "补齐来源链接",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "source_excerpt": "补齐来源链接；owner 是 Alex。",
                    "source_ref": item.source.ref,
                    "title": "补齐交付清单",
                    "formal_basis": "explicit_assignment",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "owner 是 Alex",
                    },
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "补齐来源链接；owner 是 Alex。",
                        }
                    ],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_projects() == []


def test_weekly_report_task_section_project_proposal_is_not_promoted(tmp_path):
    store = AutoReplyStore(tmp_path / "report-task-section-project.sqlite3")
    item = _work_item(assignment_authorized=True).model_copy(
        update={
            "source": _work_item().source.model_copy(
                update={
                    "type": WorkItemSourceType.PROJECT_WEEKLY_REPORT,
                }
            ),
            "summary": "## 下周工作重点\n中汽对账；补齐来源链接；owner 是 Alex。",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "中汽对账",
                        "reason": "周报将“中汽对账”列为下周工作重点",
                        "authority": "project_weekly_report",
                        "source_excerpt": "中汽对账",
                    },
                    "reason": "采用权威项目定义",
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": "中汽对账"}
                    ],
                }
            ],
            "project_assessments": [
                {
                    "project_title": "中汽对账",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "任务章节不构成有效 Project 登记，且没有重大风险。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "补齐来源链接",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "source_excerpt": "补齐来源链接；owner 是 Alex。",
                    "source_ref": item.source.ref,
                    "title": "中汽对账",
                    "formal_basis": "explicit_assignment",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "owner 是 Alex",
                    },
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "补齐来源链接；owner 是 Alex。",
                        }
                    ],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="cited report registry row"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_projects() == []


def test_project_weekly_report_registry_row_becomes_official_project(tmp_path):
    store = AutoReplyStore(tmp_path / "report-project.sqlite3")
    row = "| 大众底盘采集 | 100 张内部试标 | 下周起量 | 下周 | ⌛️进行中 |"
    item = _work_item().model_copy(
        update={
            "source": _work_item().source.model_copy(
                update={
                    "type": WorkItemSourceType.PROJECT_WEEKLY_REPORT,
                    "ref": "report:project-weekly",
                }
            ),
            "summary": json.dumps(
                {
                    "report": {"title": "项目周报"},
                    "markdown": f"## **手头项目**\n\n| 项目名 | 负责内容 |\n|---|---|\n{row}\n",
                },
                ensure_ascii=False,
            ),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "大众底盘采集",
                        "reason": "登记表列出项目",
                        "authority": "project_weekly_report",
                        "source_excerpt": row,
                    },
                    "reason": "采用权威项目定义",
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "project_assessments": [
                {
                    "project_title": "大众底盘采集",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "登记行显示正常推进，没有需要 CEO 关注的重大影响。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": row,
                    "source_ref": item.source.ref,
                    "title": "完成大众底盘采集内部试标、规则固化、数据打包与算法预标注",
                    "missing_evidence": ["owner"],
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
        }
    )

    assert (
        _report_project_registry_title(
            item, decision.project_decisions[0].registration.source_excerpt
        )
        == "大众底盘采集"
    )
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert len(result.task_ids) == 1
    projects = store.list_business_projects()
    assert [project.title for project in projects] == ["大众底盘采集"]
    with store._connect() as db:
        assert (
            db.execute("select count(*) from business_project_candidates").fetchone()[0]
            == 0
        )
    assert [
        project.id
        for project in store.list_business_task_project_links(
            task_id=result.task_ids[0]
        )
    ] == [projects[0].id]


def test_management_weekly_report_registry_row_becomes_official_project(tmp_path):
    store = AutoReplyStore(tmp_path / "management-report-project.sqlite3")
    row = "| 陈凯 | 江淮私有化 | 本周交付与合同边界确认 | ⌛️进行中 |"
    item = _work_item().model_copy(
        update={
            "source": _work_item().source.model_copy(
                update={
                    "type": WorkItemSourceType.MANAGEMENT_WEEKLY_REPORT,
                    "ref": "report:management-weekly",
                }
            ),
            "summary": json.dumps(
                {
                    "report": {"title": "管理周报"},
                    "markdown": f"## **手头项目**\n\n| 负责人 | 项目 | 当前状态 |\n|---|---|---|\n{row}\n",
                },
                ensure_ascii=False,
            ),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "江淮私有化",
                        "reason": "登记表列出项目",
                        "authority": "management_weekly_report",
                        "source_excerpt": row,
                    },
                    "reason": "采用权威项目定义",
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "project_assessments": [
                {
                    "project_title": "江淮私有化",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "登记行显示正常推进，没有需要 CEO 关注的重大影响。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": row,
                    "source_ref": item.source.ref,
                    "title": "完成江淮私有化本周交付和合同边界确认",
                    "missing_evidence": ["owner"],
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert len(result.task_ids) == 1
    assert [project.title for project in store.list_business_projects()] == [
        "江淮私有化"
    ]


def test_weekly_report_registry_accepts_excerpt_without_leading_pipe(tmp_path):
    store = AutoReplyStore(tmp_path / "report-project-unwrapped-row.sqlite3")
    row = "标注工厂（大同标注基地） | 亏损类型止损和后续产能确认 | 停止 OD，仅保留可持平或盈利的 LD、OCC | 待客户确认 | ⌛️待确认"
    item = _work_item().model_copy(
        update={
            "source": _work_item().source.model_copy(
                update={
                    "type": WorkItemSourceType.PROJECT_WEEKLY_REPORT,
                    "ref": "report:project-weekly-unwrapped",
                }
            ),
            "summary": json.dumps(
                {
                    "report": {"title": "项目周报"},
                    "markdown": "## **手头项目**\n\n| 项目名 | 负责内容 | 目标 | DDL | 状态 |\n|---|---|---|---|---|\n| "
                    + row
                    + " |\n",
                },
                ensure_ascii=False,
            ),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "标注工厂（大同标注基地）",
                        "reason": "登记表列出项目",
                        "authority": "project_weekly_report",
                        "source_excerpt": row,
                    },
                    "reason": "采用权威项目定义",
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "project_assessments": [
                {
                    "project_title": "标注工厂（大同标注基地）",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "本测试只验证登记行解析，不新增 Attention 提案。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": row,
                    "source_ref": item.source.ref,
                    "title": "停止大同标注基地亏损业务类型并确认后续产能量级",
                    "missing_evidence": ["owner"],
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {"source_ref": item.source.ref, "source_excerpt": row}
                    ],
                }
            ],
        }
    )

    assert (
        _report_project_registry_title(
            item, decision.project_decisions[0].registration.source_excerpt
        )
        == "标注工厂（大同标注基地）"
    )
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert len(result.task_ids) == 1
    assert [project.title for project in store.list_business_projects()] == [
        "标注工厂（大同标注基地）"
    ]


def test_source_dedupe_distinguishes_owner_but_replays_identical_decision(tmp_path):
    store = AutoReplyStore(tmp_path / "owner-sensitive-dedupe.sqlite3")
    excerpt = "Alex and Bob are assigned to prepare the release report."
    work_items = [
        _work_item(
            assignment_authorized=True,
            owner_identity={"name": "Alex", "user_id": "alex-id"},
        ).model_copy(update={"summary": excerpt}),
        _work_item(
            assignment_authorized=True,
            owner_identity={"name": "Bob", "user_id": "bob-id"},
        ).model_copy(update={"summary": excerpt}),
    ]
    decisions = [
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [
                    {
                        "action": "create_task",
                        "transition": "none",
                        "formal_basis": "explicit_assignment",
                        "source_excerpt": excerpt,
                        "source_ref": item.source.ref,
                        "title": "Prepare release report",
                        "owner_name": owner_name,
                        "owner_evidence": {
                            "source_ref": item.source.ref,
                            "excerpt": excerpt,
                            "name": owner_name,
                            "user_id": owner_id,
                        },
                    }
                ],
            }
        )
        for item, owner_name, owner_id in zip(
            work_items, ("Alex", "Bob"), ("alex-id", "bob-id"), strict=True
        )
    ]

    (alex_task_id,) = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=work_items[0],
        decision=decisions[0],
        record_run=False,
    )
    (replayed_alex_task_id,) = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=work_items[0],
        decision=decisions[0],
        record_run=False,
    )
    (bob_task_id,) = apply_task_agent_decision(
        store,
        summary_input_id=3,
        work_item=work_items[1],
        decision=decisions[1],
        record_run=False,
    )

    assert replayed_alex_task_id == alex_task_id
    assert bob_task_id != alex_task_id
    assert len(store.list_business_tasks()) == 2
    assert {task.owner_user_id for task in store.list_business_tasks()} == {
        "alex-id",
        "bob-id",
    }


def test_creation_replay_ignores_description_and_audit_wording(tmp_path):
    store = AutoReplyStore(tmp_path / "presentation-independent-dedupe.sqlite3")
    item = _work_item(
        assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"},
    ).model_copy(update={"summary": "Alex is assigned to prepare the release report."})
    base = {
        "action": "create_task",
        "transition": "none",
        "formal_basis": "explicit_assignment",
        "source_excerpt": item.summary,
        "source_ref": item.source.ref,
        "title": "Prepare release report",
        "owner_name": "Alex",
        "owner_evidence": {
            "source_ref": item.source.ref,
            "excerpt": item.summary,
            "name": "Alex",
        },
    }
    first = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    **base,
                    "description": "Prepare the report for launch.",
                    "update_summary": "Owner confirmed.",
                }
            ],
        }
    )
    replay = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    **base,
                    "description": "The release report needs preparation.",
                    "update_summary": "Clear owner evidence.",
                }
            ],
        }
    )

    (first_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=first, record_run=False
    )
    (replay_id,) = apply_task_agent_decision(
        store, summary_input_id=2, work_item=item, decision=replay, record_run=False
    )

    assert replay_id == first_id
    assert len(store.list_business_tasks()) == 1


def test_replayed_creation_with_new_date_requires_explicit_task_update(tmp_path):
    store = AutoReplyStore(tmp_path / "replay-date-effect.sqlite3")
    item = _work_item().model_copy(
        update={"summary": "补齐来源链接；Requested due 2026-09-25."}
    )
    common = {
        "action": "record_candidate",
        "transition": "none",
        "source_excerpt": item.summary,
        "source_ref": item.source.ref,
        "title": "报价候选",
        "missing_evidence": ["owner"],
    }
    without_date = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [common],
        }
    )
    with_new_date = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    **common,
                    "date_evidence": [
                        {
                            "kind": "requested_deadline_at",
                            "value": "2026-09-25",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-09-25",
                        }
                    ],
                }
            ],
        }
    )

    (task_id,) = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=without_date,
        record_run=False,
    )
    with pytest.raises(ValueError, match="update the existing Task explicitly"):
        apply_task_agent_decision(
            store,
            summary_input_id=2,
            work_item=item,
            decision=with_new_date,
            record_run=False,
        )

    assert [task.id for task in store.list_business_tasks()] == [task_id]
    assert store.list_business_task_date_evidence(task_id) == ()


def test_update_dedupe_identity_preserves_a_real_status_transition(tmp_path):
    store = AutoReplyStore(tmp_path / "status-effect-dedupe.sqlite3")
    task = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="报价跟进",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:status",
                evidence_text="报价跟进",
                dedupe_key="seed:status",
            ),
        )
    )
    item = _work_item().model_copy(update={"summary": "报价任务状态已更新"})

    for status in ("waiting", "done"):
        decision = TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [
                    {
                        "action": "update_task",
                        "transition": "update_fields",
                        "task_id": task.task_id,
                        "source_excerpt": item.summary,
                        "source_ref": item.source.ref,
                        "title": "报价跟进",
                        "status": status,
                    }
                ],
            }
        )
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )

    assert store.get_business_task(task.task_id).status.value == "done"
    assert len(store.list_business_task_signals()) == 3


@pytest.mark.parametrize("change", ["owner", "status", "unchanged", "promotion"])
def test_existing_task_update_can_omit_title(tmp_path, change):
    store = AutoReplyStore(tmp_path / "title-update.sqlite3")
    seed = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:title",
                evidence_text="提交报价",
                dedupe_key="seed:title",
            ),
        )
    )
    item = _work_item(
        assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"},
    ).model_copy(
        update={
            "summary": "Avery assigns Alex to submit the quote; the quote is waiting."
        }
    )
    payload = {
        "action": "update_task",
        "transition": "update_fields",
        "task_id": seed.task_id,
        "source_ref": item.source.ref,
        "source_excerpt": item.summary,
    }
    if change in {"owner", "promotion"}:
        payload.update(
            owner_name="Alex",
            owner_evidence={
                "source_ref": item.source.ref,
                "excerpt": item.summary,
                "name": "Alex",
                "user_id": "alex-id",
            },
        )
    if change == "status":
        payload["status"] = "waiting"
    if change == "promotion":
        payload.update(
            transition="promote_candidate", formal_basis="explicit_assignment"
        )
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [payload],
            }
        ),
        record_run=False,
    )
    task = store.get_business_task(seed.task_id)
    assert task.title == "提交报价"
    if change == "owner":
        assert task.owner_name == "Alex"
    elif change == "status":
        assert task.status.value == "waiting"
    elif change == "promotion":
        assert task.stage.value == "formal"
        assert task.owner_user_id == "alex-id"
        assert task.formal_basis is FormalTaskBasis.EXPLICIT_ASSIGNMENT
    else:
        assert result.skipped_reasons
        assert len(store.list_business_task_signals()) == 1


@pytest.mark.parametrize("action", ["record_candidate", "create_task"])
@pytest.mark.parametrize("title", [None, "", "   "])
def test_new_task_requires_title_in_decision_shape(action, title):
    payload = {
        "action": action,
        "transition": "none",
        "source_ref": "source:1",
        "source_excerpt": "Submit the quote",
    }
    if title is not None:
        payload["title"] = title
    if action == "create_task":
        payload["formal_basis"] = "explicit_assignment"
    with pytest.raises(ValidationError, match="new task decision requires title"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [payload],
            }
        )


def test_update_whitespace_title_rejected_in_shape_before_domain_write(tmp_path):
    store = AutoReplyStore(tmp_path / "whitespace-title.sqlite3")
    seed = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:whitespace",
                evidence_text="提交报价",
                dedupe_key="seed:whitespace",
            ),
        )
    )
    original = store.get_business_task(seed.task_id)
    signals = store.list_business_task_signals()
    payload = {
        "action": "update_task",
        "transition": "update_fields",
        "task_id": seed.task_id,
        "source_ref": "source:update",
        "source_excerpt": "报价等待回复",
        "title": "   ",
        "status": "waiting",
    }
    with pytest.raises(ValidationError, match="provided update title must be nonblank"):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [payload],
            }
        )
    assert store.get_business_task(seed.task_id) == original
    assert store.list_business_task_signals() == signals


@pytest.mark.parametrize("title", [None, "", "核对报价"])
def test_update_title_boundary_preserves_omitted_empty_or_applies_valid_title(
    tmp_path, title
):
    store = AutoReplyStore(tmp_path / "title-boundary.sqlite3")
    seed = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:boundary",
                evidence_text="提交报价",
                dedupe_key="seed:boundary",
            ),
        )
    )
    item = _work_item().model_copy(update={"summary": "报价等待客户回复"})
    payload = {
        "action": "update_task",
        "transition": "update_fields",
        "task_id": seed.task_id,
        "source_ref": item.source.ref,
        "source_excerpt": item.summary,
        "status": "waiting",
    }
    if title is not None:
        payload["title"] = title
    apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [payload],
            }
        ),
        record_run=False,
    )
    task = store.get_business_task(seed.task_id)
    assert task.title == (title if title else "提交报价")
    assert task.status.value == "waiting"


def test_missing_new_task_title_uses_existing_same_session_repair():
    valid = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "source_ref": "source:1",
                "source_excerpt": "Submit the quote",
                "title": "Submit quote",
            }
        ],
    }
    invalid = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                key: value
                for key, value in valid["task_decisions"][0].items()
                if key != "title"
            }
        ],
    }

    class RepairingExecution:
        def execute(self, **kwargs):
            retry = kwargs["result_validation_retry"]
            assert retry.resume_same_session
            raw = _agent_message_jsonl(json.dumps(invalid))
            with pytest.raises(RoutedResultValidationError):
                kwargs["parser"](raw)
            assert "new task decision requires title" in retry.correction_prompt(raw)
            value = kwargs["parser"](_agent_message_jsonl(json.dumps(valid)))
            return SimpleNamespace(
                value=value,
                session_id="title-repair",
                transcript_start=0,
                transcript_end=2,
            )

    decision = TaskAgentCodexRunner(routed_execution=RepairingExecution()).decide(
        prompt="decide", workload_key="1", session_scope_id="title-repair"
    )
    assert decision == TaskAgentDecision.model_validate(valid)


def test_process_work_item_commits_titleless_update_and_new_candidate_batch(tmp_path):
    store = AutoReplyStore(tmp_path / "title-batch.sqlite3")
    seed = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:batch",
                evidence_text="提交报价",
                dedupe_key="seed:batch",
            ),
        )
    )
    item = _work_item().model_copy(
        update={"summary": "报价等待客户回复；另需准备独立演示。"}
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    (work_input,) = store.claim_work_summary_inputs(limit=1)
    decision = {
        "project_decisions": [],
        "project_assessments": [],
        "update_summary": "本轮没有相关 Project。",
        "task_decisions": [
            {
                "action": "update_task",
                "transition": "update_fields",
                "task_id": seed.task_id,
                "source_ref": item.source.ref,
                "source_excerpt": item.summary,
                "status": "waiting",
            },
            {
                "action": "record_candidate",
                "transition": "none",
                "title": "准备独立演示",
                "source_ref": item.source.ref,
                "source_excerpt": "另需准备独立演示",
            },
        ],
    }
    process_work_item(store, TaskAgentRunner(FakeCodex(decision)), work_input)
    old = store.get_business_task(seed.task_id)
    assert (old.title, old.status.value) == ("提交报价", "waiting")
    assert {task.title for task in store.list_business_tasks()} == {
        "提交报价",
        "准备独立演示",
    }
    with store._connect() as db:
        assert (
            db.execute(
                "select status from work_summary_inputs where id=?", (input_id,)
            ).fetchone()[0]
            == "done"
        )
        assert (
            db.execute(
                "select status from task_agent_runs where summary_input_id=?",
                (input_id,),
            ).fetchone()[0]
            == "completed"
        )


def test_update_task_decision_applies_evidence_backed_description_change(tmp_path):
    store = AutoReplyStore(tmp_path / "description-update.sqlite3")
    task = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="核对客户材料",
            description="整理收到的客户材料。",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:description-update",
                evidence_text="核对客户材料",
                dedupe_key="seed:description-update",
            ),
        )
    )
    item = _work_item().model_copy(
        update={
            "summary": "客户材料已到齐并补齐缺项",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": task.task_id,
                    "source_excerpt": "客户材料已到齐并补齐缺项",
                    "source_ref": item.source.ref,
                    "title": "核对客户材料",
                    "description": "客户材料已到齐，核对后补齐缺项。",
                    "update_summary": "The latest source clarifies the remaining deliverable.",
                }
            ],
        }
    )

    apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    updated = store.get_business_task(task.task_id)
    assert updated.title == "核对客户材料"
    assert updated.description == "客户材料已到齐，核对后补齐缺项。"
    assert (
        store.list_business_task_events(task.task_id)[-1].event_type.value
        == "details_changed"
    )


def test_owner_evidence_keeps_the_citation_the_agent_gave(tmp_path):
    """Derek 2026-09-25: a citation is a sentence from the source; it need not be word for word."""
    store = AutoReplyStore(tmp_path / "owner-evidence-excerpt.sqlite3")
    item = _work_item(
        assignment_authorized=True,
    ).model_copy(update={"summary": "Alex 负责提交周报，周五前完成。"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "source_excerpt": item.summary,
                    "source_ref": item.source.ref,
                    "title": "提交周报",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "Alex负责提交周报",
                    },
                    "formal_basis": "explicit_assignment",
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    task = store.get_business_task(result.task_ids[0])
    assert task is not None
    evidence = json.loads(task.owner_evidence_json)
    assert evidence["excerpt"] == "Alex负责提交周报"


def _seed_identity_task(store, source_ref, *, external_task_id=""):
    context = {"owner_identity": {"name": "Alex", "user_id": "alex-id"}}
    if external_task_id:
        context["external_task_id"] = external_task_id
    return TaskSemanticService(store).record_formal_task(
        RecordFormalTask(
            title="提交周报",
            signal=SourceSignal(
                source_type="message",
                source_ref=source_ref,
                evidence_text="Alex 负责提交周报",
                dedupe_key=source_ref,
                conversation_id="conversation:weekly",
                author_user_id="avery-id",
                author_name="Avery",
                author_kind=BusinessActorKind.HUMAN,
                context_json=json.dumps(context),
            ),
            formality=FormalityEvidence(
                basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                assigner_is_authorized=True,
                deliverable_is_explicit=True,
                owner_is_explicit=True,
            ),
            owner_user_id="alex-id",
            owner_name="Alex",
            owner_evidence_json=json.dumps(
                {
                    "source_ref": source_ref,
                    "excerpt": "Alex 负责提交周报",
                    "user_id": "alex-id",
                    "name": "Alex",
                }
            ),
        )
    )


def test_recurring_same_title_tasks_cannot_be_identity_merged(tmp_path):
    store = AutoReplyStore(tmp_path / "weekly-no-merge.sqlite3")
    first = _seed_identity_task(store, "message:week-1")
    second = _seed_identity_task(store, "message:week-2")
    item = _work_item().model_copy(update={"summary": "本周周报已提交"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "merge_identity",
                    "task_id": first.task_id,
                    "target_task_id": second.task_id,
                    "source_excerpt": "本周周报已提交",
                    "source_ref": item.source.ref,
                    "title": "提交周报",
                    "identity_proposal": {
                        "source_task_id": first.task_id,
                        "target_task_id": second.task_id,
                        "identity_evidence": {
                            "basis": "same_deliverable_owner_context_time",
                            "source_signal_id": first.signal_id,
                            "target_signal_id": second.signal_id,
                        },
                    },
                }
            ],
        }
    )

    with pytest.raises(
        ValueError, match="can link or cluster Tasks but cannot merge identity"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.get_business_task(first.task_id).status.value != "merged"
    assert store.get_business_task(second.task_id).status.value != "merged"


@pytest.mark.parametrize("omit_title", [False, True])
def test_same_external_task_id_can_support_identity_merge_and_recompute_both_tasks(
    tmp_path, monkeypatch, omit_title
):
    from app.task_agent import BusinessAttentionProjection

    store = AutoReplyStore(tmp_path / "external-id-merge.sqlite3")
    first = _seed_identity_task(
        store, "message:external-1", external_task_id="dingtalk:task-44"
    )
    second = _seed_identity_task(
        store, "message:external-2", external_task_id="dingtalk:task-44"
    )
    item = _work_item().model_copy(update={"summary": "同步外部待办记录"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "merge_identity",
                    "task_id": first.task_id,
                    "target_task_id": second.task_id,
                    "source_excerpt": "同步外部待办记录",
                    "source_ref": item.source.ref,
                    "title": "提交周报",
                    "identity_proposal": {
                        "source_task_id": first.task_id,
                        "target_task_id": second.task_id,
                        "reason": "Same external task ID dingtalk:task-44",
                        "identity_evidence": {
                            "basis": "same_external_task_id",
                            "source_signal_id": first.signal_id,
                            "target_signal_id": second.signal_id,
                        },
                    },
                }
            ],
        }
    )

    if omit_title:
        payload = decision.model_dump(mode="json")
        del payload["task_decisions"][0]["title"]
        decision = TaskAgentDecision.model_validate(payload)
    recomputed = []
    original_recompute = BusinessAttentionProjection.recompute_for_tasks

    def capture_recompute(self, task_ids):
        recomputed.append(tuple(task_ids))
        return original_recompute(self, task_ids)

    monkeypatch.setattr(
        BusinessAttentionProjection, "recompute_for_tasks", capture_recompute
    )
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert result.task_ids == (second.task_id,)
    assert result.affected_task_ids == (second.task_id, first.task_id)
    assert recomputed == [(second.task_id, first.task_id)]
    assert store.get_business_task(first.task_id).status.value == "merged"
    assert store.get_business_task(second.task_id).status.value != "merged"
    assert store.get_business_task(first.task_id).title == "提交周报"
    assert store.get_business_task(second.task_id).title == "提交周报"


def test_batch_rolls_back_task_signal_and_all_proposals_on_later_invalid_evidence(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "atomic-batch.sqlite3")
    semantic = TaskSemanticService(store)
    source_id = semantic.record_candidate(
        RecordCandidate(
            title="报价跟进",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:1",
                evidence_text="报价",
                dedupe_key="seed:1",
            ),
        )
    ).task_id
    target_id = semantic.record_candidate(
        RecordCandidate(
            title="准备客户材料",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:2",
                evidence_text="材料",
                dedupe_key="seed:2",
            ),
        )
    ).task_id
    resolution = BusinessResolutionService(store)
    cluster_id = resolution.create_cluster(
        title="客户事项", task_ids=[source_id, target_id]
    )
    anchor_id = resolution.register_anchor(
        anchor_type="customer", anchor_ref="customer:1", title="客户"
    )
    item = _work_item()
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": source_id,
                    "source_excerpt": "补齐来源链接",
                    "source_ref": item.source.ref,
                    "title": "报价跟进",
                    "status": "waiting",
                    "relation_proposals": [
                        {
                            "related_task_id": target_id,
                            "direction": "current_to_related",
                            "relation_type": "related_to",
                            "reason": "共享客户目标",
                        }
                    ],
                    "cluster_proposal": {
                        "cluster_id": cluster_id,
                        "task_ids": [source_id, target_id],
                        "reason": "同一目标",
                    },
                    "anchor_match_proposals": [
                        {"anchor_id": anchor_id, "reason": "客户事项"}
                    ],
                    "project_candidate_proposal": {
                        "cluster_id": cluster_id,
                        "title": "客户项目候选",
                        "reason": "持续任务",
                    },
                },
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": "另一个来源里的话",
                    "source_ref": "other-source-ref",
                    "title": "无来源候选",
                    "missing_evidence": ["source"],
                },
            ],
        }
    )
    original = store.get_business_task(source_id)

    with pytest.raises(ValueError, match="must match the Work Item source"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )

    assert store.get_business_task(source_id) == original
    assert store.list_business_task_relations(task_id=source_id) == ()
    assert store.list_business_task_anchor_links(task_id=source_id) == ()
    assert len(store.list_business_task_signals()) == 2
    assert len(store.list_business_work_cluster_tasks(cluster_id=cluster_id)) == 2
    with store._connect() as db:
        assert (
            db.execute("select count(*) from business_project_candidates").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("action", ["record_candidate", "update_task"])
@pytest.mark.parametrize("direction", ["current_to_related", "related_to_current"])
def test_decision_relative_relation_binds_actual_applied_task_and_replays(
    tmp_path, action, direction
):
    store = AutoReplyStore(tmp_path / "relative-relation.sqlite3")
    semantic = TaskSemanticService(store)
    seeds = [
        semantic.record_candidate(
            RecordCandidate(
                title=f"已有交付{i}",
                signal=SourceSignal(
                    source_type="seed",
                    source_ref=f"seed:{i}",
                    evidence_text="既有交付",
                    dedupe_key=f"seed:{i}",
                ),
            )
        )
        for i in range(2)
    ]
    item = _work_item().model_copy(update={"summary": "核对供应商延期付款安排。"})
    payload = {
        "action": action,
        "transition": "update_fields" if action == "update_task" else "none",
        "task_id": seeds[0].task_id if action == "update_task" else None,
        "source_ref": item.source.ref,
        "source_excerpt": item.summary,
        "title": "核对付款安排",
        "description": item.summary,
        "relation_proposals": [
            {
                "related_task_id": seeds[1].task_id,
                "direction": direction,
                "relation_type": "supports",
                "reason": "当前行动支持已有交付",
            }
        ],
    }
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [payload],
        }
    )
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    (current_id,) = result.task_ids
    assert current_id == (seeds[0].task_id if action == "update_task" else 3)
    (relation,) = store.list_business_task_relations(task_id=current_id)
    assert (relation.from_task_id, relation.to_task_id) == (
        (current_id, seeds[1].task_id)
        if direction == "current_to_related"
        else (seeds[1].task_id, current_id)
    )
    assert relation.status.value == "proposed"
    assert relation.supporting_signal_id == store.list_business_task_signals()[-1].id
    tasks = store.list_business_tasks()
    apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert store.list_business_tasks() == tasks
    assert store.list_business_task_relations(task_id=current_id) == (relation,)


@pytest.mark.parametrize("actual_self", [False, True])
def test_new_action_relation_rejects_missing_or_deduped_actual_self_target_atomically(
    tmp_path, actual_self
):
    store = AutoReplyStore(tmp_path / "invalid-relative.sqlite3")
    item = _work_item().model_copy(update={"summary": "复核付款安排。"})
    payload = {
        "action": "record_candidate",
        "transition": "none",
        "source_ref": item.source.ref,
        "source_excerpt": item.summary,
        "title": "复核付款安排",
    }
    if actual_self:
        seed = apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=TaskAgentDecision.model_validate(
                {
                    "project_decisions": [],
                    "project_assessments": [],
                    "update_summary": "本轮没有相关 Project。",
                    "task_decisions": [payload],
                }
            ),
            record_run=False,
        )
        target_id = seed.task_ids[0]
    else:
        target_id = 999
    tasks, signals = store.list_business_tasks(), store.list_business_task_signals()
    payload["relation_proposals"] = [
        {
            "related_task_id": target_id,
            "direction": "current_to_related",
            "relation_type": "related_to",
        }
    ]
    with pytest.raises(ValueError):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=TaskAgentDecision.model_validate(
                {
                    "project_decisions": [],
                    "project_assessments": [],
                    "update_summary": "本轮没有相关 Project。",
                    "task_decisions": [payload],
                }
            ),
            record_run=False,
        )
    assert store.list_business_tasks() == tasks
    assert store.list_business_task_signals() == signals


@pytest.mark.parametrize("action", ["record_candidate", "update_task"])
def test_relative_relation_effect_fingerprint_preserves_direction_and_ignores_reason(
    action,
):
    from app.task_agent import _task_source_signal

    item = _work_item()
    payload = {
        "action": action,
        "transition": "update_fields" if action == "update_task" else "none",
        "task_id": 1 if action == "update_task" else None,
        "source_ref": item.source.ref,
        "source_excerpt": "复核付款安排。",
        "title": "付款复核",
        "description": "补充调整方案",
        "relation_proposals": [
            {
                "related_task_id": 2,
                "direction": "current_to_related",
                "relation_type": "supports",
                "reason": "解释",
            }
        ],
    }

    def key(relation):
        decision = TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [{**payload, "relation_proposals": [relation]}],
            }
        ).task_decisions[0]
        return _task_source_signal(item, decision).dedupe_key

    original = payload["relation_proposals"][0]
    assert key(original) == key({**original, "reason": "同一业务关系的新解释"})
    for change in (
        {"direction": "related_to_current"},
        {"related_task_id": 3},
        {"relation_type": "blocks"},
    ):
        assert (key(original) != key({**original, **change})) is (
            action == "update_task"
        )


def test_same_task_multiple_decisions_keep_positional_task_signal_mapping(tmp_path):
    store = AutoReplyStore(tmp_path / "same-task.sqlite3")
    service = TaskSemanticService(store)
    created = service.record_candidate(
        RecordCandidate(
            title="报价跟进",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:1",
                evidence_text="报价跟进",
                dedupe_key="seed:1",
            ),
        )
    )
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref="project:quote", title="报价项目"
    )
    resolution.register_official_project(
        anchor_id=anchor_id, registry_source="report:quote"
    )
    resolution.confirm_anchor_match(
        task_id=created.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=created.signal_id,
        reason="正式报价项目",
        relevance=BusinessRelevance.RELEVANT,
    )
    item = _work_item()
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "报价项目",
                    "anchor_id": anchor_id,
                    "outcome": "needs_attention",
                    "reason": "报价延期存在明确风险。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [created.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "补齐来源链接",
                        }
                    ],
                    "attention_proposal": {
                        "category": "watch",
                        "title": "报价延期风险",
                        "why_attention": "有明确风险",
                        "current_state": "待补来源",
                        "ceo_action": "确认推进",
                        "assessment_basis": "current_observation",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "补齐来源链接",
                            }
                        ],
                    },
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": created.task_id,
                    "source_excerpt": "补齐来源链接",
                    "source_ref": item.source.ref,
                    "title": "报价跟进",
                    "status": "waiting",
                },
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": created.task_id,
                    "source_excerpt": "owner 是 Alex",
                    "source_ref": item.source.ref,
                    "title": "报价跟进",
                    "business_relevance": "relevant",
                },
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert result.task_ids == (created.task_id, created.task_id)
    assert len(result.attention_proposals) == 1
    assert result.attention_proposals[0].task_ids == (created.task_id,)
    assert result.attention_proposals[0].signal_id != created.signal_id


def test_attention_projection_runs_after_outer_domain_transaction_commit(
    tmp_path, monkeypatch
):
    from app.task_agent import BusinessAttentionProjection
    from app.task_semantic_service import TaskDateInput

    store = AutoReplyStore(tmp_path / "attention-after-commit.sqlite3")
    deadline = "2026-09-25"
    seed = TaskSemanticService(store).record_formal_task(
        RecordFormalTask(
            title="报价方案",
            signal=SourceSignal(
                source_type="message",
                source_ref="message:attention-assignment",
                evidence_text=f"Alex 承诺 {deadline} 交付报价方案",
                dedupe_key="seed:attention",
                conversation_id="conversation:1",
                author_kind=BusinessActorKind.HUMAN,
                author_user_id="alex-id",
                author_name="Alex",
            ),
            formality=FormalityEvidence(
                basis=FormalTaskBasis.EXPLICIT_COMMITMENT,
                assigner_is_authorized=True,
                deliverable_is_explicit=True,
                owner_is_explicit=True,
            ),
            owner_user_id="alex-id",
            owner_name="Alex",
            owner_evidence_json=json.dumps(
                {
                    "source_ref": "message:attention-assignment",
                    "excerpt": f"Alex 承诺 {deadline} 交付报价方案",
                    "user_id": "alex-id",
                    "name": "Alex",
                }
            ),
            date_facts=(
                TaskDateInput(
                    date_type=BusinessTaskDateType.COMMITTED_DEADLINE_AT,
                    value_at=deadline,
                    raw_phrase=deadline,
                    actor_kind=BusinessActorKind.HUMAN,
                    actor_user_id="alex-id",
                    actor_name="Alex",
                ),
            ),
        )
    )
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref="project:attention", title="关键客户交付"
    )
    resolution.register_official_project(
        anchor_id=anchor_id, registry_source="report:attention"
    )
    resolution.confirm_anchor_match(
        task_id=seed.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=seed.signal_id,
        reason="确认是关键客户业务事项",
    )
    item = _work_item(sender="Alex", sender_user_id="alex-id").model_copy(
        update={"summary": "Alex says the delivery is at risk."}
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "关键客户交付",
                    "anchor_id": anchor_id,
                    "outcome": "needs_attention",
                    "reason": "负责人报告已接受承诺存在交付风险。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [seed.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "delivery is at risk",
                        }
                    ],
                    "attention_proposal": {
                        "category": "watch",
                        "title": "承诺交付风险",
                        "why_attention": "负责人报告已接受承诺有风险",
                        "current_state": "交付存在风险",
                        "ceo_action": "核实交付状态",
                        "assessment_basis": "current_observation",
                        "material_trigger": "threatened_commitment",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "delivery is at risk",
                            }
                        ],
                    },
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "source_excerpt": item.summary,
                    "source_ref": item.source.ref,
                    "title": "报价方案",
                    "business_relevance": "relevant",
                }
            ],
        }
    )
    seen = []
    original_upsert = BusinessAttentionProjection.upsert

    def check_committed(self, proposal):
        with store._connect() as other:
            task = other.execute(
                "select business_relevance from business_tasks where id=?",
                (seed.task_id,),
            ).fetchone()
            linked = other.execute(
                "select count(*) from business_task_evidence where task_id=? and signal_id=?",
                (seed.task_id, proposal.evidence_signal_id),
            ).fetchone()[0]
            confirmed = other.execute(
                "select count(*) from business_task_anchor_links where task_id=? and anchor_id=? "
                "and status='confirmed' and active=1",
                (seed.task_id, anchor_id),
            ).fetchone()[0]
            project_linked = other.execute(
                "select count(*) from business_project_evidence evidence "
                "join business_projects project on project.id=evidence.project_id "
                "where project.canonical_anchor_id=? and evidence.signal_id=?",
                (anchor_id, proposal.evidence_signal_id),
            ).fetchone()[0]
        seen.append((task[0], linked, confirmed, project_linked))
        return original_upsert(self, proposal)

    monkeypatch.setattr(BusinessAttentionProjection, "upsert", check_committed)
    with store.task_agent_domain_apply_transaction() as db:
        result = apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
            _db=db,
        )
        assert seen == []
    from app.task_agent import _project_task_attention

    _project_task_attention(
        store,
        result.attention_proposals,
        result.affected_task_ids,
        receipt=result.projection_receipt,
    )

    assert seen == [("relevant", 0, 1, 1)]
    (attention_item,) = store.list_business_attention_items()
    assert attention_item.why_attention == "负责人报告已接受承诺有风险"
    assessment = json.loads(attention_item.assessment_json)
    assert assessment["material_trigger"] == "threatened_commitment"
    assert assessment["evidence"][0]["source_excerpt"] == "delivery is at risk"


def test_two_applied_project_proposals_fold_into_one_assessment_readback(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-folded-proposals.sqlite3")
    first, anchor_id = _stored_project_task(store)
    second = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="售前知识库回款",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:folded-second",
                evidence_text="售前知识库回款",
                dedupe_key="seed:folded-second",
            ),
        )
    )
    BusinessResolutionService(store).confirm_anchor_match(
        task_id=second.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=second.signal_id,
        reason="同一正式 Project",
        relevance=BusinessRelevance.RELEVANT,
    )
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险需要观察。"})
    proposal = {
        "category": "watch",
        "title": "售前知识库交付风险",
        "why_attention": "交付风险需要观察",
        "current_state": "等待交付与回款结果",
        "ceo_action": "观察结果",
        "assessment_basis": "current_observation",
        "material_trigger": "risk_escalation",
        "evidence": [
            {"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}
        ],
    }
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "anchor_id": anchor_id,
                    "outcome": "needs_attention",
                    "reason": "交付风险同时影响交付与回款。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0, 1],
                    "task_ids": [first.task_id, second.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                    "attention_proposal": proposal,
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": first.task_id,
                    "title": "售前知识库交付",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险需要观察",
                },
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": second.task_id,
                    "title": "售前知识库回款",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险需要观察",
                },
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "applied"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == sorted([first.task_id, second.task_id])
    assert readback.attention_id == store.list_business_attention_items()[0].id
    assert (
        len({outcome.attention_id for outcome in result.projection_receipt.outcomes})
        == 1
    )


def test_changed_and_evidence_only_support_share_applied_card(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-mixed-proposal-outcomes.sqlite3")
    first, anchor_id = _stored_project_task(store)
    second = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="售前知识库回款",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:mixed-second",
                evidence_text="售前知识库回款",
                dedupe_key="seed:mixed-second",
            ),
        )
    )
    BusinessResolutionService(store).confirm_anchor_match(
        task_id=second.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=second.signal_id,
        reason="同一正式 Project",
        relevance=BusinessRelevance.RELEVANT,
    )
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险需要观察。"})
    proposal = {
        "category": "watch",
        "title": "售前知识库交付风险",
        "why_attention": "交付风险需要观察",
        "current_state": "等待交付与回款结果",
        "ceo_action": "观察结果",
        "assessment_basis": "current_observation",
        "material_trigger": "risk_escalation",
        "evidence": [
            {"source_ref": item.source.ref, "source_excerpt": "交付风险需要观察"}
        ],
    }
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "anchor_id": anchor_id,
                    "outcome": "needs_attention",
                    "reason": "交付风险同时影响交付与回款。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0, 1],
                    "task_ids": [first.task_id, second.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                    "attention_proposal": proposal,
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": first.task_id,
                    "title": "售前知识库交付",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险需要观察",
                },
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": second.task_id,
                    "title": "售前知识库回款",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险需要观察",
                },
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.projection_receipt is not None
    assert result.projection_receipt.status == "completed"
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "applied"
    assert readback.reason == "Attention proposal applied."
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == sorted([first.task_id, second.task_id])
    assert readback.attention_id is not None
    assert [
        link.task_id
        for link in store.list_business_attention_tasks(readback.attention_id)
    ] == [first.task_id, second.task_id]


def test_task_ids_only_support_receives_recompute_error(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "assessment-task-id-recompute-error.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item().model_copy(update={"summary": "售前知识库交付补齐本轮来源。"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "anchor_id": anchor_id,
                    "outcome": "not_needed",
                    "reason": "本轮只补来源，没有新增经营影响。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [],
                    "task_ids": [seed.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "补齐本轮来源",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "售前知识库交付",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "补齐本轮来源",
                }
            ],
        }
    )

    def fail_recompute(_self, *_args, **_kwargs):
        raise RuntimeError("review recompute exploded")

    monkeypatch.setattr(
        BusinessAttentionProjection, "recompute_for_tasks", fail_recompute
    )
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.projection_receipt is not None
    assert result.projection_receipt.recompute_error == "review recompute exploded"
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "error"
    assert "recompute_error: review recompute exploded" in readback.reason
    assert readback.task_ids == [seed.task_id]
    assert readback.evidence[0].signal_id == result.current_signal_id
    assert decision.project_assessments[0].outcome == "not_needed"


def test_no_change_known_project_support_keeps_verified_decision_task_id(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-no-change-known-task-id.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险需要观察。"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "anchor_id": anchor_id,
                    "outcome": "needs_attention",
                    "reason": "交付风险需要观察。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                    "attention_proposal": {
                        "category": "watch",
                        "title": "售前知识库交付风险",
                        "why_attention": "交付风险需要观察",
                        "current_state": "等待交付结果",
                        "ceo_action": "观察结果",
                        "assessment_basis": "current_observation",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "交付风险需要观察",
                            }
                        ],
                    },
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "售前知识库交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险需要观察",
                }
            ],
        }
    )
    signals_before = store.list_business_task_signals()

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.applied_decisions == ()
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "applied"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id is not None
    assert readback.evidence[0].signal_id == result.current_signal_id
    assert len(store.list_business_task_signals()) == len(signals_before) + 1


@pytest.mark.parametrize(
    ("failure_method", "failure_message"),
    [("upsert", "projection exploded"), ("recompute_for_tasks", "recompute exploded")],
)
def test_projection_error_is_preserved_in_assessment_application_readback(
    tmp_path,
    monkeypatch,
    failure_method,
    failure_message,
):
    store = AutoReplyStore(tmp_path / "assessment-projection-error.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险需要观察。"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "anchor_id": anchor_id,
                    "outcome": "needs_attention",
                    "reason": "交付风险需要观察。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [seed.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                    "attention_proposal": {
                        "category": "watch",
                        "title": "售前知识库交付风险",
                        "why_attention": "交付风险需要观察",
                        "current_state": "等待交付结果",
                        "ceo_action": "观察结果",
                        "assessment_basis": "current_observation",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "交付风险需要观察",
                            }
                        ],
                    },
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "售前知识库交付",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险需要观察",
                }
            ],
        }
    )

    def fail_projection(_self, *_args, **_kwargs):
        raise RuntimeError(failure_message)

    monkeypatch.setattr(BusinessAttentionProjection, failure_method, fail_projection)
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "error"
    assert failure_message in readback.reason
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert (readback.attention_id is None) is (failure_method == "upsert")


def test_routine_progress_without_attention_proposal_is_not_projected(tmp_path):
    store = AutoReplyStore(tmp_path / "ordinary-progress-not-attention.sqlite3")
    seed = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="报价跟进",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:attention-candidate",
                evidence_text="报价跟进",
                dedupe_key="seed:attention-candidate",
            ),
        )
    )
    item = _work_item().model_copy(update={"summary": "补齐来源链接"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "source_excerpt": "补齐来源链接",
                    "source_ref": item.source.ref,
                    "title": "报价跟进",
                    "business_relevance": "relevant",
                }
            ],
        }
    )

    apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert store.list_business_attention_items() == ()


@pytest.mark.parametrize(
    "change",
    [
        {"status": "done"},
        {"status": "cancelled"},
        {"business_relevance": "not_relevant"},
    ],
)
def test_agent_recomputes_existing_attention_after_terminal_or_irrelevant_change(
    tmp_path, change
):
    from app.task_attention_projection import (
        AttentionProposal,
        BusinessAttentionProjection,
    )

    store = AutoReplyStore(
        tmp_path / f"attention-recompute-{next(iter(change.values()))}.sqlite3"
    )
    seed = _seed_identity_task(
        store, f"message:attention-recompute-{next(iter(change.values()))}"
    )
    relevance_item = _work_item().model_copy(
        update={"summary": "报价任务进入主营业务范围"}
    )
    relevance = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "source_excerpt": relevance_item.summary,
                    "source_ref": relevance_item.source.ref,
                    "title": "提交周报",
                    "business_relevance": "relevant",
                }
            ],
        }
    )
    apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=relevance_item,
        decision=relevance,
        record_run=False,
    )
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref=f"project:{seed.task_id}", title="客户交付"
    )
    project_id = resolution.register_official_project(
        anchor_id=anchor_id, registry_source="meeting:delivery"
    )
    with store.business_task_transaction() as db:
        db.execute(
            "insert into business_project_evidence(project_id, signal_id) values (?, ?)",
            (project_id, seed.signal_id),
        )
    resolution.confirm_anchor_match(
        task_id=seed.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=seed.signal_id,
        reason="已确认主营业务锚点",
    )
    projection = BusinessAttentionProjection(store)
    attention_id = projection.upsert(
        AttentionProposal(
            stable_key=f"test:task:{seed.task_id}",
            category="watch",
            title="任务需关注",
            business_area="客户交付",
            why_attention="source-backed material trigger: 交付受到影响",
            current_state="处理中",
            ceo_action="核实推进",
            anchor_id=anchor_id,
            task_ids=(seed.task_id,),
            evidence_signal_id=seed.signal_id,
        )
    )
    change_item = _work_item().model_copy(update={"summary": "Task finished."})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "anchor_id": anchor_id,
                    "project_title": "客户交付",
                    "outcome": "not_needed",
                    "reason": "本轮仅更新任务，未请求改变既有项目关注。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "evidence": [
                        {
                            "source_ref": change_item.source.ref,
                            "source_excerpt": "Task finished.",
                        }
                    ],
                }
            ],
            "update_summary": "更新成员任务，不自动关闭项目关注。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "source_excerpt": change_item.summary,
                    "source_ref": change_item.source.ref,
                    "title": "提交周报",
                    **change,
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=change_item,
        decision=decision,
        record_run=False,
    )

    assert result.affected_task_ids == (seed.task_id,)
    assert store.list_business_attention_tasks(attention_id) == ()
    assert store.get_business_attention_item(attention_id).status.value == "active"


def test_unlinked_owner_reply_does_not_accept_any_task(tmp_path):
    store = AutoReplyStore(tmp_path / "unlinked-acceptance.sqlite3")
    service = TaskSemanticService(store)
    assigned = service.record_formal_task(
        RecordFormalTask(
            title="报价方案",
            signal=SourceSignal(
                source_type="message",
                source_ref="message:assignment",
                evidence_text="Alex 负责报价方案",
                dedupe_key="assignment:1",
                author_user_id="alex-id",
                author_name="Alex",
                author_kind=BusinessActorKind.HUMAN,
            ),
            formality=FormalityEvidence(
                basis=FormalTaskBasis.MEETING_ACTION_ITEM,
                assigner_is_authorized=True,
                deliverable_is_explicit=True,
                owner_is_explicit=True,
            ),
            owner_user_id="alex-id",
            owner_name="Alex",
            owner_evidence_json=json.dumps(
                {
                    "source_ref": "message:assignment",
                    "excerpt": "Alex 负责报价方案",
                    "user_id": "alex-id",
                    "name": "Alex",
                }
            ),
        )
    )
    item = _work_item(sender="Alex", sender_user_id="alex-id")
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "apply_acceptance",
                    "task_id": assigned.task_id,
                    "acceptance_polarity": "accepted",
                    "acceptance_target_signal_id": assigned.signal_id,
                    "source_excerpt": "补齐来源链接",
                    "source_ref": item.source.ref,
                    "title": "报价方案",
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    task = store.get_business_task(assigned.task_id)
    assert result.task_ids == ()
    assert (
        result.skipped_reasons
        and "no verified reply-to reference" in result.skipped_reasons[0]
    )
    assert task.commitment_status.value == "assigned_unaccepted"


def test_owner_evidence_does_not_itself_authorize_assignment(tmp_path):
    store = AutoReplyStore(tmp_path / "unauthorized-assignment.sqlite3")
    item = _work_item()
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "source_excerpt": "owner 是 Alex",
                    "source_ref": item.source.ref,
                    "title": "确认负责人",
                    "formal_basis": "explicit_assignment",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "owner 是 Alex",
                    },
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="authorized source metadata"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == ()


@pytest.mark.parametrize(
    "sender, sender_user_id, accepted",
    [
        ("Avery", "avery-id", False),
        ("Alex", "alex-id", True),
    ],
)
def test_new_commitment_requires_the_named_owner_to_author_it(
    tmp_path, sender, sender_user_id, accepted
):
    store = AutoReplyStore(tmp_path / f"owner-authored-commitment-{sender}.sqlite3")
    excerpt = "Alex 承诺 2026-09-25 交付报价方案"
    item = _work_item(
        sender=sender,
        sender_user_id=sender_user_id,
        owner_identity={"name": "Alex", "user_id": "alex-id"},
    ).model_copy(update={"summary": excerpt})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "formal_basis": "explicit_commitment",
                    "source_excerpt": excerpt,
                    "source_ref": item.source.ref,
                    "title": "交付报价方案",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": excerpt,
                        "name": "Alex",
                        "user_id": "alex-id",
                    },
                    "date_evidence": [
                        {
                            "kind": "committed_deadline_at",
                            "value": "2026-09-25",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-09-25",
                            "actor_user_id": "alex-id",
                            "actor_name": "Alex",
                        }
                    ],
                }
            ],
        }
    )

    if accepted:
        (task_id,) = apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
        task = store.get_business_task(task_id)
        assert task.commitment_status.value == "accepted"
        date_fact = next(
            fact
            for fact in store.list_business_task_date_evidence(task_id)
            if fact.date_type.value == "committed_deadline_at"
        )
        assert (date_fact.actor_user_id, date_fact.actor_name) == ("alex-id", "Alex")
    else:
        with pytest.raises(ValueError, match="authored by its identified owner"):
            apply_task_agent_decision(
                store,
                summary_input_id=1,
                work_item=item,
                decision=decision,
                record_run=False,
            )
        assert store.list_business_tasks() == ()


@pytest.mark.parametrize(
    "basis, expected_error",
    [
        ("meeting_action_item", "sourced meeting action-item record"),
        ("external_todo", "trusted external TODO source metadata"),
    ],
)
def test_formal_basis_cannot_be_selected_for_an_unrelated_reply_source(
    tmp_path, basis, expected_error
):
    store = AutoReplyStore(tmp_path / f"invalid-source-basis-{basis}.sqlite3")
    item = _work_item(
        owner_identity={"name": "Alex", "user_id": "alex-id"},
        external_task_id="dingtalk-task-1",
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "formal_basis": basis,
                    "source_excerpt": "补齐来源链接",
                    "source_ref": item.source.ref,
                    "title": "补齐报价来源链接",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "补齐来源链接; owner 是 Alex",
                        "name": "Alex",
                        "user_id": "alex-id",
                    },
                }
            ],
        }
    )

    with pytest.raises(ValueError, match=expected_error):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == ()


def test_meeting_action_item_requires_minutes_action_item_source(tmp_path):
    store = AutoReplyStore(tmp_path / "minutes-action-item-source.sqlite3")
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "ai_minutes",
                "ref": "minutes-1#todos-sha256=abc",
                "title": "客户交付会议行动项",
                "created_at": "2026-09-20T09:00:00+08:00",
            },
            "summary": "Alex 负责补齐报价来源链接。",
            "context": {
                "sender": "",
                "source_conversation_kind": "minutes",
                "owner_identity": {"name": "Alex", "user_id": "alex-id"},
            },
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "formal_basis": "meeting_action_item",
                    "source_excerpt": "Alex 负责补齐报价来源链接。",
                    "source_ref": item.source.ref,
                    "title": "补齐报价来源链接",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "Alex 负责补齐报价来源链接。",
                        "name": "Alex",
                        "user_id": "alex-id",
                    },
                    "owner_kind": "individual",
                    "owner_relation": "meeting_summary_action_item",
                }
            ],
        }
    )

    (task_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert (
        store.get_business_task(task_id).commitment_status.value
        == "assigned_unaccepted"
    )


def test_next_check_date_uses_identified_agent_actor(tmp_path):
    store = AutoReplyStore(tmp_path / "next-check-date.sqlite3")
    item = _work_item().model_copy(
        update={"summary": "补齐来源链接；下次检查 2026-10-01T09:00:00+08:00"}
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": item.summary,
                    "source_ref": item.source.ref,
                    "title": "补齐报价来源链接",
                    "missing_evidence": ["owner"],
                    "date_evidence": [
                        {
                            "kind": "next_check_at",
                            "value": "2026-10-01T09:00:00+08:00",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-10-01T09:00:00+08:00",
                        }
                    ],
                }
            ],
        }
    )

    (task_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    (date_fact,) = store.list_business_task_date_evidence(task_id)
    assert (
        date_fact.actor_kind.value,
        date_fact.actor_user_id,
        date_fact.actor_name,
    ) == ("agent", "task-agent", "CEO Agent")


def test_source_target_and_next_check_create_task_keyed_follow_up(tmp_path):
    store = AutoReplyStore(tmp_path / "task-follow-up.sqlite3")
    item = _work_item(
        assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"},
    ).model_copy(
        update={
            "summary": "Avery assigns Alex to confirm quote. Check 2026-10-01T09:00:00+08:00."
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "formal_basis": "explicit_assignment",
                    "source_excerpt": item.summary,
                    "source_ref": item.source.ref,
                    "title": "Confirm quote",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "Avery assigns Alex",
                        "name": "Alex",
                        "user_id": "alex-id",
                    },
                    "date_evidence": [
                        {
                            "kind": "next_check_at",
                            "value": "2026-10-01T09:00:00+08:00",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-10-01T09:00:00+08:00",
                        }
                    ],
                }
            ],
        }
    )

    (task_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    [draft] = store.list_business_task_follow_ups(business_task_id=task_id)
    assert draft["business_task_id"] == task_id
    assert draft["target_conversation_id"] == item.source.conversation_id
    assert draft["target_kind"] == "group"
    assert draft["owner_user_id"] == "alex-id"
    assert draft["scheduled_at"] == "2026-10-01T09:00:00+08:00"


def test_noncommitment_date_types_keep_exact_source_and_human_actor(tmp_path):
    store = AutoReplyStore(tmp_path / "typed-date-provenance.sqlite3")
    item = _work_item(
        sender="Avery",
        sender_user_id="avery-id",
        assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"},
    ).model_copy(
        update={
            "summary": "Avery assigns Alex on 2026-09-20. Request due 2026-09-25; "
            "external deadline 2026-09-26; estimate 2026-09-27."
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "create_task",
                    "transition": "none",
                    "formal_basis": "explicit_assignment",
                    "source_excerpt": item.summary,
                    "source_ref": item.source.ref,
                    "title": "客户报价跟进",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": "Avery assigns Alex",
                        "name": "Alex",
                        "user_id": "alex-id",
                    },
                    "date_evidence": [
                        {
                            "kind": "requested_deadline_at",
                            "value": "2026-09-25",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-09-25",
                        },
                        {
                            "kind": "external_deadline_at",
                            "value": "2026-09-26",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-09-26",
                        },
                        {
                            "kind": "estimated_deadline_at",
                            "value": "2026-09-27",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-09-27",
                        },
                    ],
                }
            ],
        }
    )

    (task_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    facts = store.list_business_task_date_evidence(task_id)
    facts_by_type = {fact.date_type.value: fact for fact in facts}
    assert set(facts_by_type) == {
        "assigned_at",
        "requested_deadline_at",
        "external_deadline_at",
        "estimated_deadline_at",
    }
    assert {kind: fact.value_at for kind, fact in facts_by_type.items()} == {
        "assigned_at": item.source.created_at,
        "requested_deadline_at": "2026-09-25",
        "external_deadline_at": "2026-09-26",
        "estimated_deadline_at": "2026-09-27",
    }
    assert all(
        fact.source_signal_id == store.list_business_task_evidence(task_id)[0].signal_id
        for fact in facts
    )
    assert {kind: fact.raw_phrase for kind, fact in facts_by_type.items()} == {
        "assigned_at": item.source.created_at,
        "requested_deadline_at": "2026-09-25",
        "external_deadline_at": "2026-09-26",
        "estimated_deadline_at": "2026-09-27",
    }
    assert {
        kind: (fact.actor_kind.value, fact.actor_user_id, fact.actor_name)
        for kind, fact in facts_by_type.items()
    } == {
        "assigned_at": ("human", "avery-id", "Avery"),
        "requested_deadline_at": ("human", "avery-id", "Avery"),
        "external_deadline_at": ("human", "avery-id", "Avery"),
        "estimated_deadline_at": ("human", "avery-id", "Avery"),
    }


@pytest.mark.parametrize(
    "date_patch, message",
    [
        ({"source_ref": "message:other"}, "date evidence source_ref must match"),
        (
            {"source_excerpt": "猜测出来的日期"},
            "date evidence source_excerpt must be an exact source substring",
        ),
        (
            {"value": "2026-09-26"},
            "date value must match an exact, parseable date phrase",
        ),
        (
            {"value": "2026-09-25T00:00:00"},
            "date value must match an exact, parseable date phrase",
        ),
        (
            {"actor_user_id": "other-id"},
            "date actor_user_id must match the trusted date actor",
        ),
        (
            {"actor_name": "Other person"},
            "date actor_name must match the trusted date actor",
        ),
    ],
)
def test_date_evidence_rejects_wrong_reference_or_non_source_excerpt(
    tmp_path, date_patch, message
):
    store = AutoReplyStore(tmp_path / "invalid-date-provenance.sqlite3")
    item = _work_item().model_copy(
        update={"summary": "补齐来源链接；请求截止日期为 2026-09-25。"}
    )
    date_fact = {
        "kind": "requested_deadline_at",
        "value": "2026-09-25",
        "source_ref": item.source.ref,
        "source_excerpt": "2026-09-25",
        **date_patch,
    }
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": "补齐来源链接",
                    "source_ref": item.source.ref,
                    "title": "报价候选",
                    "missing_evidence": ["owner"],
                    "date_evidence": [date_fact],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match=message):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == ()


def test_ai_minutes_date_actor_requires_trusted_speaker_mapping(tmp_path):
    store = AutoReplyStore(tmp_path / "minutes-date-attribution.sqlite3")
    item = _work_item(sender="Meeting host", sender_user_id="host-id").model_copy(
        update={
            "source": _work_item().source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "summary": "Alex 承诺 2026-09-25 交付报价方案",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": item.summary,
                    "source_ref": item.source.ref,
                    "title": "报价方案",
                    "missing_evidence": ["owner"],
                    "date_evidence": [
                        {
                            "kind": "requested_deadline_at",
                            "value": "2026-09-25",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-09-25",
                            "actor_user_id": "alex-id",
                            "actor_name": "Alex",
                        }
                    ],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="AI Minutes date actor cannot be attributed"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == ()


def test_candidate_cannot_record_committed_deadline_without_owner_acceptance(tmp_path):
    store = AutoReplyStore(tmp_path / "unaccepted-commitment-date.sqlite3")
    item = _work_item().model_copy(update={"summary": "补齐来源链接；2026-09-25"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": "补齐来源链接",
                    "source_ref": item.source.ref,
                    "title": "报价候选",
                    "missing_evidence": ["owner"],
                    "date_evidence": [
                        {
                            "kind": "committed_deadline_at",
                            "value": "2026-09-25",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-09-25",
                            "actor_user_id": "avery-id",
                            "actor_name": "Avery",
                        }
                    ],
                }
            ],
        }
    )

    with pytest.raises(
        ValueError, match="committed deadline requires owner acceptance"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == ()


def test_promoting_candidate_derives_assigned_at_from_explicit_assignment_source(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "promote-assignment-date.sqlite3")
    candidate = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="提交报价",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:promotion",
                evidence_text="报价",
                dedupe_key="seed:promotion",
            ),
        )
    )
    item = _work_item(
        assignment_authorized=True,
        owner_identity={"name": "Alex", "user_id": "alex-id"},
    ).model_copy(update={"summary": "Avery formally assigns Alex on 2026-09-22."})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "promote_candidate",
                    "task_id": candidate.task_id,
                    "formal_basis": "explicit_assignment",
                    "source_excerpt": item.summary,
                    "source_ref": item.source.ref,
                    "title": "提交报价",
                    "owner_name": "Alex",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": item.summary,
                        "name": "Alex",
                        "user_id": "alex-id",
                    },
                }
            ],
        }
    )

    apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    (date_fact,) = store.list_business_task_date_evidence(candidate.task_id)
    assert (date_fact.date_type.value, date_fact.value_at, date_fact.raw_phrase) == (
        "assigned_at",
        item.source.created_at,
        item.source.created_at,
    )


def test_unparseable_relative_date_stays_only_in_linked_source_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "relative-date-not-normalized.sqlite3")
    item = _work_item().model_copy(update={"summary": "提交报价，下周五前完成"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": item.summary,
                    "source_ref": item.source.ref,
                    "title": "提交报价",
                    "missing_evidence": ["owner"],
                    "date_evidence": [
                        {
                            "kind": "requested_deadline_at",
                            "value": "2026-09-25",
                            "source_ref": item.source.ref,
                            "source_excerpt": "下周五前",
                        }
                    ],
                }
            ],
        }
    )

    (task_id,) = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert store.list_business_task_date_evidence(task_id) == ()
    (evidence,) = store.list_business_task_evidence(task_id)
    signal = store.get_business_task_signal(evidence.signal_id)
    assert "下周五前" in signal.evidence_text


def test_estimate_keeps_source_speaker_and_rejects_model_attribution_override(tmp_path):
    store = AutoReplyStore(tmp_path / "estimate-source-actor.sqlite3")
    item = _work_item(sender="Avery", sender_user_id="avery-id").model_copy(
        update={"summary": "Avery estimates completion on 2026-09-27."}
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": item.summary,
                    "source_ref": item.source.ref,
                    "title": "报价交付",
                    "missing_evidence": ["owner"],
                    "date_evidence": [
                        {
                            "kind": "estimated_deadline_at",
                            "value": "2026-09-27",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-09-27",
                            "actor_user_id": "alex-id",
                            "actor_name": "Alex",
                        }
                    ],
                }
            ],
        }
    )

    with pytest.raises(
        ValueError, match="date actor_user_id must match the trusted date actor"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == ()


def _assigned_formal_task_for_acceptance(store):
    service = TaskSemanticService(store)
    return service.record_formal_task(
        RecordFormalTask(
            title="报价方案",
            signal=SourceSignal(
                source_type="message",
                source_ref="message:assignment",
                evidence_text="Alex 负责报价方案",
                dedupe_key="assignment:exact",
                conversation_id="conversation:1",
                author_user_id="avery-id",
                author_name="Avery",
                author_kind=BusinessActorKind.HUMAN,
                context_json=json.dumps(
                    {
                        "owner_identity": {"name": "Alex", "user_id": "alex-id"},
                        "assignment_authorized": True,
                    }
                ),
            ),
            formality=FormalityEvidence(
                basis=FormalTaskBasis.EXPLICIT_ASSIGNMENT,
                assigner_is_authorized=True,
                deliverable_is_explicit=True,
                owner_is_explicit=True,
            ),
            owner_user_id="alex-id",
            owner_name="Alex",
            owner_evidence_json=json.dumps(
                {
                    "source_ref": "message:assignment",
                    "excerpt": "Alex 负责报价方案",
                    "user_id": "alex-id",
                    "name": "Alex",
                }
            ),
        )
    )


@pytest.mark.parametrize("omit_title", [False, True])
def test_exact_owner_reply_accepts_only_cited_assignment_and_records_committed_date(
    tmp_path, omit_title
):
    store = AutoReplyStore(tmp_path / "linked-acceptance.sqlite3")
    assigned = _assigned_formal_task_for_acceptance(store)
    item = _work_item(
        sender="Alex",
        sender_user_id="alex-id",
        reply_to_source_ref="message:assignment",
    ).model_copy(update={"summary": "我接受报价方案，承诺于 2026-09-25 交付"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "apply_acceptance",
                    "task_id": assigned.task_id,
                    "acceptance_polarity": "accepted",
                    "acceptance_target_signal_id": assigned.signal_id,
                    "source_excerpt": "我接受报价方案，承诺于 2026-09-25 交付",
                    "source_ref": item.source.ref,
                    "title": "报价方案",
                    "date_evidence": [
                        {
                            "kind": "committed_deadline_at",
                            "value": "2026-09-25",
                            "source_ref": item.source.ref,
                            "source_excerpt": "2026-09-25",
                            "actor_user_id": "alex-id",
                            "actor_name": "Alex",
                        }
                    ],
                }
            ],
        }
    )

    if omit_title:
        payload = decision.model_dump(mode="json")
        del payload["task_decisions"][0]["title"]
        decision = TaskAgentDecision.model_validate(payload)
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert result.task_ids == (assigned.task_id,)
    task = store.get_business_task(assigned.task_id)
    assert task.title == "报价方案"
    assert task.commitment_status.value == "accepted"
    (fact,) = store.list_business_task_date_evidence(assigned.task_id)
    assert (fact.date_type.value, fact.value_at, fact.raw_phrase) == (
        "committed_deadline_at",
        "2026-09-25",
        "2026-09-25",
    )
    assert (
        fact.source_signal_id,
        fact.actor_kind.value,
        fact.actor_user_id,
        fact.actor_name,
    ) == (
        result.task_ids[0]
        and store.list_business_task_evidence(assigned.task_id)[-1].signal_id,
        "human",
        "alex-id",
        "Alex",
    )
    [mirror_intent] = store.list_business_task_todo_sync_outbox()
    assert mirror_intent["business_task_id"] == assigned.task_id
    assert mirror_intent["operation"] == "create"


def test_source_grounded_completion_updates_task_status_without_todo_write(tmp_path):
    store = AutoReplyStore(tmp_path / "source-task-completion.sqlite3")
    assigned = _assigned_formal_task_for_acceptance(store)
    store.create_business_task_dingtalk_link(
        business_task_id=assigned.task_id,
        dingtalk_task_id="dt-task-1",
        status="active",
    )
    item = _work_item().model_copy(
        update={"summary": "客户验收已完成，报价方案交付物通过验收。"}
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": assigned.task_id,
                    "source_excerpt": "客户验收已完成，报价方案交付物通过验收。",
                    "source_ref": item.source.ref,
                    "title": "报价方案",
                    "status": "done",
                    "update_summary": "来源明确记录交付物通过客户验收。",
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert result.task_ids == (assigned.task_id,)
    assert store.get_business_task(assigned.task_id).status.value == "done"
    with store._connect() as db:
        assert db.execute("select count(*) from work_todos").fetchone()[0] == 0
    [intent] = store.list_business_task_todo_sync_outbox()
    assert intent["business_task_id"] == assigned.task_id
    assert intent["operation"] == "complete"


@pytest.mark.parametrize(
    "reply_context",
    [
        {
            "conversation_id": "conversation:other",
            "reply_to_source_ref": "message:assignment",
        },
        {"conversation_id": "conversation:1", "reply_to_source_ref": "message:other"},
    ],
)
def test_acceptance_with_wrong_conversation_or_reply_reference_is_not_applied(
    tmp_path, reply_context
):
    store = AutoReplyStore(tmp_path / "mismatched-acceptance.sqlite3")
    assigned = _assigned_formal_task_for_acceptance(store)
    item = _work_item(
        sender="Alex",
        sender_user_id="alex-id",
        reply_to_source_ref=reply_context["reply_to_source_ref"],
    ).model_copy(
        update={
            "source": _work_item().source.model_copy(
                update={"conversation_id": reply_context["conversation_id"]}
            ),
            "summary": "我接受报价方案",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "apply_acceptance",
                    "task_id": assigned.task_id,
                    "acceptance_polarity": "accepted",
                    "acceptance_target_signal_id": assigned.signal_id,
                    "source_excerpt": "我接受报价方案",
                    "source_ref": item.source.ref,
                    "title": "报价方案",
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert result.task_ids == ()
    assert result.skipped_reasons
    assert (
        store.get_business_task(assigned.task_id).commitment_status.value
        == "assigned_unaccepted"
    )
    assert store.list_business_task_date_evidence(assigned.task_id) == ()


def test_task_agent_prompt_reads_minutes_owners_from_the_conversation_around_each_item():
    """DingTalk leaves an action item's executor empty; the scanner attaches the conversation, and the prompt says how to use it."""
    prompt = build_task_agent_prompt(_work_item(), "context")

    assert "transcript_excerpts" in prompt
    assert "speaker label included" in prompt
    assert '"excerpt"' in prompt  # the owner_evidence key the service reads
    assert (
        "发言人 N" in prompt
    )  # DingTalk's placeholder for an unnamed speaker is not an owner
    # Derek 2026-09-28: a narrow transcript window can miss the sentence that names the owner;
    # the meeting's own DingTalk summary is a second source and must be checked too.
    assert "meeting_summary" in prompt
    assert "nor is a team or department" in prompt


def test_update_restating_current_fields_adds_the_owner_and_an_unchanged_item_is_skipped_not_fatal(
    tmp_path,
):
    """One item that only repeats what a Task already says must not fail the meeting's other items."""
    store = AutoReplyStore(tmp_path / "restated.sqlite3")
    service = TaskSemanticService(store)

    def seed(title, key):
        return service.record_candidate(
            RecordCandidate(
                title=title,
                signal=SourceSignal(
                    source_type="ai_minutes",
                    source_ref=f"m:{key}#todos-sha256=old",
                    evidence_text=title,
                    dedupe_key=f"seed:{key}",
                ),
            )
        ).task_id

    first, second = seed("整理访谈问题清单", "a"), seed("收集用户诉求", "b")
    line = "Zoey：那这个我们可以先列一个list吧，给你看一下。"
    item = _work_item().model_copy(
        update={
            "summary": json.dumps(
                {"transcript_excerpts": [{"lines": [line]}]}, ensure_ascii=False
            )
        }
    )

    def update(task_id, title, **extra):
        return {
            "action": "update_task",
            "transition": "update_fields",
            "task_id": task_id,
            "source_excerpt": line,
            "source_ref": item.source.ref,
            "title": title,
            "status": "open",
            "business_relevance": "unknown",
            **extra,
        }

    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                update(
                    first,
                    "整理访谈问题清单",
                    owner_name="Zoey",
                    owner_evidence={"excerpt": line},
                ),
                update(second, "收集用户诉求"),
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert store.get_business_task(first).owner_name == "Zoey"
    assert [
        event.event_type.value for event in store.list_business_task_events(first)
    ] == ["created", "owner_changed"]
    assert store.get_business_task(second).owner_name == ""
    assert [
        event.event_type.value for event in store.list_business_task_events(second)
    ] == ["created"]
    assert any(
        f"Task {second} already matches this source" in reason
        for reason in result.skipped_reasons
    )


def test_an_exact_owner_excerpt_from_another_line_is_kept_when_the_work_was_handed_over(
    tmp_path,
):
    """磊哥 assigns (“你写下来”) and Claire takes it on: her line, not the assigning one, names the owner."""
    store = AutoReplyStore(tmp_path / "handover.sqlite3")
    task = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="结构化撰写内容产出计划",
            signal=SourceSignal(
                source_type="ai_minutes",
                source_ref="m:1#todos-sha256=old",
                evidence_text="结构化撰写内容产出计划",
                dedupe_key="seed:handover",
            ),
        )
    )
    assigning, taking = (
        "磊哥：你写下来，结构化的写下来。",
        "Claire：你认的话我就写下来呗。",
    )
    item = _work_item().model_copy(
        update={
            "summary": json.dumps({"lines": [assigning, taking]}, ensure_ascii=False)
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": task.task_id,
                    "source_excerpt": assigning,
                    "source_ref": item.source.ref,
                    "title": "结构化撰写内容产出计划",
                    "owner_name": "Claire",
                    "owner_evidence": {
                        "source_ref": item.source.ref,
                        "excerpt": taking,
                    },
                }
            ],
        }
    )

    apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    updated = store.get_business_task(task.task_id)
    assert updated.owner_name == "Claire"
    assert json.loads(updated.owner_evidence_json)["excerpt"] == taking


def test_an_owner_the_source_does_not_establish_skips_that_item_and_not_the_whole_meeting(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "bad-owner.sqlite3")
    service = TaskSemanticService(store)

    def seed(title, key):
        return service.record_candidate(
            RecordCandidate(
                title=title,
                signal=SourceSignal(
                    source_type="ai_minutes",
                    source_ref=f"m:{key}#todos-sha256=old",
                    evidence_text=title,
                    dedupe_key=f"seed:{key}",
                ),
            )
        ).task_id

    first, second = seed("整理清单", "a"), seed("发送文档", "b")
    good, other = "Zoey：我先列一个list。", "陈思睿：好的。"
    item = _work_item().model_copy(
        update={"summary": json.dumps({"lines": [good, other]}, ensure_ascii=False)}
    )

    def update(task_id, title, name, excerpt):
        return {
            "action": "update_task",
            "transition": "update_fields",
            "task_id": task_id,
            "title": title,
            "source_excerpt": excerpt,
            "source_ref": item.source.ref,
            "owner_name": name,
            "owner_evidence": {"source_ref": item.source.ref, "excerpt": excerpt},
        }

    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                update(
                    first, "整理清单", "陈思睿", good
                ),  # the quoted line is Zoey's: it does not name 陈思睿
                update(second, "发送文档", "陈思睿", other),
            ],
        }
    )

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    assert store.get_business_task(first).owner_name == ""
    assert store.get_business_task(second).owner_name == "陈思睿"
    assert any(
        f"Task {first} owner was not applied" in reason
        for reason in result.skipped_reasons
    )


def _earlier_evidence_decision(existing_task_id, item, **overrides):
    return {
        "action": "update_task",
        "transition": "update_fields",
        "task_id": existing_task_id,
        "evidence_origin": "memory",
        "source_ref": "meeting:2026-09-10#todos-sha256=abc",
        "source_link": "https://shanji.example/transcribes/abc",
        "source_excerpt": "Zoey：我来负责访谈问题清单。",
        "title": "整理访谈问题清单",
        "owner_name": "Zoey",
        "owner_evidence": {"excerpt": "Zoey：我来负责访谈问题清单。"},
        **overrides,
    }


def test_earlier_or_remembered_evidence_can_refine_a_task_and_is_kept_as_its_own_source(
    tmp_path,
):
    """Derek 2026-09-25: the Agent may use earlier evidence and Memory provenance; it is recorded as cited, not observed."""
    store = AutoReplyStore(tmp_path / "earlier.sqlite3")
    task = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="整理访谈问题清单",
            signal=SourceSignal(
                source_type="ai_minutes",
                source_ref="m:1#todos-sha256=a",
                evidence_text="整理访谈问题清单",
                dedupe_key="seed:earlier",
            ),
        )
    )
    item = _work_item().model_copy(update={"summary": "本次来源没有提到负责人。"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [_earlier_evidence_decision(task.task_id, item)],
        }
    )

    apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )

    updated = store.get_business_task(task.task_id)
    assert updated.owner_name == "Zoey"
    signals = {signal.id: signal for signal in store.list_business_task_signals()}
    cited = [
        signals[row.signal_id]
        for row in store.list_business_task_evidence(task.task_id)
        if signals[row.signal_id].source_type == "memory_provenance"
    ]
    assert [(s.source_ref, s.evidence_text) for s in cited] == [
        ("meeting:2026-09-10#todos-sha256=abc", "Zoey：我来负责访谈问题清单。")
    ]
    assert json.loads(cited[0].context_json) == {
        "evidence_origin": "memory",
        "cited_while_processing": item.source.ref,
        "source_link": "https://shanji.example/transcribes/abc",
    }


@pytest.mark.parametrize(
    "extra",
    [
        {
            "action": "create_task",
            "transition": "none",
            "formal_basis": "explicit_assignment",
            "task_id": None,
        },
        {"transition": "promote_candidate"},
        {"transition": "apply_acceptance", "acceptance_polarity": "accepted"},
        {
            "date_evidence": [
                {
                    "kind": "next_check_at",
                    "value": "2026-10-01",
                    "raw_phrase": "x",
                    "source_ref": "meeting:2026-09-10#todos-sha256=abc",
                    "source_excerpt": "x",
                }
            ]
        },
    ],
)
def test_earlier_evidence_does_not_stand_in_for_the_current_sources_authority_or_dates(
    extra,
):
    item = _work_item()
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [_earlier_evidence_decision(1, item, **extra)],
            }
        )


def test_earlier_evidence_needs_its_link_or_else_a_description_of_where_it_is():
    """A link whenever there is one; without a link, words (a DingTalk message is its group and person)."""
    item = _work_item()
    for missing in (
        {"source_link": ""},
        {"source_link": "", "source_group": "产品群"},
        {"source_link": "", "source_person": "Zoey"},
    ):
        with pytest.raises(
            ValidationError,
            match="its source link, or, when there is none, a description",
        ):
            TaskAgentDecision.model_validate(
                {
                    "project_decisions": [],
                    "project_assessments": [],
                    "update_summary": "本轮没有相关 Project。",
                    "task_decisions": [_earlier_evidence_decision(1, item, **missing)],
                }
            )
    for given in (
        {"source_link": "", "source_description": "产品群里 Zoey 的消息"},
        {"source_link": "", "source_group": "产品群", "source_person": "Zoey"},
    ):
        TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [],
                "update_summary": "本轮没有相关 Project。",
                "task_decisions": [_earlier_evidence_decision(1, item, **given)],
            }
        )


def test_the_current_sources_link_or_group_and_person_are_recorded_and_the_excerpt_may_be_an_extract(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "locator.sqlite3")
    minutes = _work_item().model_copy(
        update={
            "summary": json.dumps(
                {
                    "meeting": {"shareUrl": "https://shanji.example/transcribes/1"},
                    "lines": ["Zoey：我先列一个list给你看。"],
                },
                ensure_ascii=False,
            )
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "列问题清单",
                    "source_ref": minutes.source.ref,
                    "source_excerpt": "Zoey 说先列个清单给看",  # an extract, not word for word
                }
            ],
        }
    )
    apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=minutes,
        decision=decision,
        record_run=False,
    )

    [signal] = store.list_business_task_signals()
    assert (
        json.loads(signal.context_json)["source_link"]
        == "https://shanji.example/transcribes/1"
    )

    chat = _work_item().model_copy(update={"summary": "王明：周五前交报价。"})
    chat = chat.model_copy(
        update={
            "source": chat.source.model_copy(
                update={"ref": "message:9", "conversation_title": "报价群"}
            ),
            "context": chat.context.model_copy(update={"sender": "王明"}),
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "交报价",
                    "source_ref": "message:9",
                    "source_excerpt": "周五前交报价",
                }
            ],
        }
    )
    apply_task_agent_decision(
        store, summary_input_id=2, work_item=chat, decision=decision, record_run=False
    )

    signal = next(
        row
        for row in store.list_business_task_signals()
        if row.source_ref == "message:9"
    )
    assert (signal.conversation_title, signal.author_name) == ("报价群", "王明")


def _stored_project_task(store, *, title="售前知识库", source_type="seed"):
    semantic = TaskSemanticService(store)
    seed = semantic.record_candidate(
        RecordCandidate(
            title=f"{title}交付",
            signal=SourceSignal(
                source_type=source_type,
                source_ref=f"{source_type}:{title}",
                evidence_text=f"{title}历史交付风险",
                dedupe_key=f"{source_type}:{title}",
            ),
        )
    )
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project",
        anchor_ref=f"project:{title}",
        title=title,
    )
    project_id = resolution.register_official_project(
        anchor_id=anchor_id,
        registry_source=f"report:{title}",
    )
    from app.task_source_documents import source_is_observed

    if source_is_observed(source_type):
        with store.business_task_transaction() as db:
            from app.project_context_service import ProjectContextService

            ProjectContextService(store).apply(
                project_id=project_id, context=None, signal_ids=(seed.signal_id,), db=db
            )
    resolution.confirm_anchor_match(
        task_id=seed.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=seed.signal_id,
        reason="正式 Project 的现有 Task",
        relevance=BusinessRelevance.RELEVANT,
    )
    return seed, anchor_id


def _stored_project_assessment(item, seed, anchor_id, **updates):
    payload = {
        "project_title": "售前知识库",
        "anchor_id": anchor_id,
        "outcome": "not_needed",
        "reason": "本轮仅补齐来源，没有新增经营影响。",
        "assessment_basis": "current_observation",
        "evidence": [
            {
                "source_ref": item.source.ref,
                "source_excerpt": "补齐来源链接",
            }
        ],
        "decision_indexes": [],
        "task_ids": [seed.task_id],
    }
    payload.update(updates)
    return payload


def _stored_project_identity_merge(store, item, *, include_assessment):
    source = _seed_identity_task(
        store,
        "message:merge-project-source",
        external_task_id="dingtalk:task-88",
    )
    target = _seed_identity_task(
        store,
        "message:merge-project-target",
        external_task_id="dingtalk:task-88",
    )
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project",
        anchor_ref="project:售前知识库",
        title="售前知识库",
    )
    resolution.register_official_project(
        anchor_id=anchor_id,
        registry_source="report:售前知识库",
    )
    resolution.confirm_anchor_match(
        task_id=target.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=target.signal_id,
        reason="目标 Task 已确认属于正式 Project",
        relevance=BusinessRelevance.RELEVANT,
    )
    assessments = []
    if include_assessment:
        assessments.append(
            {
                "project_title": "售前知识库",
                "anchor_id": anchor_id,
                "outcome": "not_needed",
                "reason": "本轮只合并重复身份。",
                "assessment_basis": "current_observation",
                "decision_indexes": [],
                "task_ids": [target.task_id],
                "evidence": [
                    {
                        "source_ref": item.source.ref,
                        "source_excerpt": "同步外部待办记录",
                    }
                ],
            }
        )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": assessments,
            "update_summary": "本轮没有相关 Project。"
            if not assessments
            else "已判断目标 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "merge_identity",
                    "task_id": source.task_id,
                    "target_task_id": target.task_id,
                    "source_excerpt": "同步外部待办记录",
                    "source_ref": item.source.ref,
                    "identity_proposal": {
                        "source_task_id": source.task_id,
                        "target_task_id": target.task_id,
                        "reason": "Same external task ID dingtalk:task-88",
                        "identity_evidence": {
                            "basis": "same_external_task_id",
                            "source_signal_id": source.signal_id,
                            "target_signal_id": target.signal_id,
                        },
                    },
                }
            ],
        }
    )
    return source, target, anchor_id, decision


def test_identity_merge_target_confirmed_project_requires_assessment_before_writes(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-merge-target-coverage.sqlite3")
    item = _work_item().model_copy(update={"summary": "同步外部待办记录"})
    source, target, _anchor_id, decision = _stored_project_identity_merge(
        store,
        item,
        include_assessment=False,
    )
    signals_before = store.list_business_task_signals()

    with pytest.raises(
        ValueError, match=f"current Task {target.task_id} confirmed Project"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )

    assert store.get_business_task(source.task_id).status.value != "merged"
    assert store.list_business_task_signals() == signals_before


def test_identity_merge_target_confirmed_project_accepts_explicit_target_assessment(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-merge-target-covered.sqlite3")
    item = _work_item().model_copy(update={"summary": "同步外部待办记录"})
    source, target, anchor_id, decision = _stored_project_identity_merge(
        store,
        item,
        include_assessment=True,
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.task_ids == (target.task_id,)
    assert result.applied_decisions[0].task_id == target.task_id
    assert result.applied_decisions[0].anchor_id == anchor_id
    assert store.get_business_task(source.task_id).status.value == "merged"


def _stored_attention_card(store, *, seed, anchor_id, title="售前知识库"):
    assessment_json = json.dumps(
        {
            "assessment_basis": "historical_comparison",
            "material_trigger": "risk_escalation",
            "inference": "历史交付风险仍需观察",
            "evidence": [
                {
                    "signal_id": seed.signal_id,
                    "source_ref": f"seed:{title}",
                    "source_excerpt": f"{title}历史交付风险",
                    "source_time": "",
                    "source_link": "",
                }
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return BusinessAttentionProjection(store).upsert(
        AttentionProposal(
            stable_key=f"project:{anchor_id}",
            category=AttentionCategory.WATCH,
            title=f"{title}交付风险",
            business_area="",
            why_attention="历史交付风险仍需观察",
            current_state="等待新的交付结果",
            ceo_action="暂不介入，观察结果",
            anchor_id=anchor_id,
            task_ids=(seed.task_id,),
            evidence_signal_id=seed.signal_id,
            assessment_json=assessment_json,
        )
    )


def test_stored_assessment_requires_current_known_project_link_coverage(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-known-project-coverage.sqlite3")
    seed, _anchor_id = _stored_project_task(store)
    item = _work_item()
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [],
            "update_summary": "错误地声称本轮没有相关 Project。",
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "售前知识库交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "补齐来源链接",
                }
            ],
        }
    )

    with pytest.raises(
        ValueError, match="confirmed Project.*requires exactly one assessment"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )

    assert len(store.list_business_task_signals()) == 1


@pytest.mark.parametrize(
    ("evidence_update", "problem"),
    [
        (
            {"source_ref": "message:guessed"},
            "current Project evidence must cite the immutable Work Item",
        ),
        ({"source_excerpt": "不存在的当前原文"}, "current Project quote is absent"),
    ],
)
def test_stored_assessment_rejects_wrong_current_citation_atomically(
    tmp_path,
    evidence_update,
    problem,
):
    store = AutoReplyStore(tmp_path / "assessment-current-citation.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item()
    assessment = _stored_project_assessment(item, seed, anchor_id)
    assessment["evidence"] = [{**assessment["evidence"][0], **evidence_update}]
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [assessment],
            "task_decisions": [],
        }
    )

    with pytest.raises(ValueError, match=problem):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )

    assert len(store.list_business_task_signals()) == 1


@pytest.mark.parametrize(
    ("historical_update", "source_type", "problem"),
    [
        (
            {"signal_id": 999},
            "seed",
            "historical Project evidence signal does not exist",
        ),
        (
            {"source_ref": "seed:wrong"},
            "seed",
            "historical Project evidence signal/source_ref does not match",
        ),
        (
            {"source_excerpt": "不存在的历史原文"},
            "seed",
            "historical Project quote is absent",
        ),
        ({}, "memory_provenance", "Project evidence must be observed original source"),
    ],
)
def test_stored_assessment_rejects_false_historical_provenance(
    tmp_path,
    historical_update,
    source_type,
    problem,
):
    store = AutoReplyStore(tmp_path / "assessment-historical-citation.sqlite3")
    seed, anchor_id = _stored_project_task(store, source_type=source_type)
    item = _work_item()
    historical = {
        "signal_id": seed.signal_id,
        "source_ref": f"{source_type}:售前知识库",
        "source_excerpt": "售前知识库历史交付风险",
        **historical_update,
    }
    assessment = _stored_project_assessment(
        item,
        seed,
        anchor_id,
        outcome="needs_attention",
        reason="当前信息与历史风险需要一起判断。",
        assessment_basis="historical_comparison",
        existing_attention_id=1,
        evidence=[
            {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"},
            historical,
        ],
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [assessment],
            "task_decisions": [],
        }
    )

    with pytest.raises(ValueError, match=problem):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ("guessed_anchor", "registered active official Project"),
        ("inactive_anchor", "registered active official Project"),
        ("wrong_title", "project_title must match the canonical stored Project title"),
        ("missing_task", "supporting Task 999 does not exist"),
        ("unrelated_task", "supporting Task is not confirmed to the assessed Project"),
    ],
)
def test_stored_assessment_rejects_guessed_project_or_unrelated_task(
    tmp_path, change, problem
):
    store = AutoReplyStore(tmp_path / "assessment-project-identity.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    unrelated = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="无关工作",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:unrelated",
                evidence_text="无关工作",
                dedupe_key="seed:unrelated",
            ),
        )
    )
    item = _work_item()
    assessment = _stored_project_assessment(item, seed, anchor_id)
    if change == "guessed_anchor":
        assessment["anchor_id"] = 999
    elif change == "inactive_anchor":
        with store.business_task_transaction() as db:
            db.execute("update business_anchors set active=0 where id=?", (anchor_id,))
    elif change == "wrong_title":
        assessment["project_title"] = "猜测的项目名"
    elif change == "missing_task":
        assessment["task_ids"] = [999]
    else:
        assessment["task_ids"] = [unrelated.task_id]
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [assessment],
            "task_decisions": [],
        }
    )

    with pytest.raises(ValueError, match=problem):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )


def test_existing_attention_repeat_with_no_task_field_change_has_no_new_effect(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-existing-card-repeat.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    item = _work_item()
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                _stored_project_assessment(
                    item,
                    seed,
                    anchor_id,
                    outcome="needs_attention",
                    reason="同一事实已由现有关注卡表示。",
                    existing_attention_id=attention_id,
                    decision_indexes=[0],
                    assessment_basis="historical_comparison",
                    evidence=[
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "补齐来源链接",
                        },
                        {
                            "signal_id": seed.signal_id,
                            "source_ref": "seed:售前知识库",
                            "source_excerpt": "售前知识库历史交付风险",
                        },
                    ],
                )
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "售前知识库交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "补齐来源链接",
                }
            ],
        }
    )
    signals_before = store.list_business_task_signals()
    events_before = store.list_business_attention_events(attention_id)

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.task_ids == ()
    assert result.applied_decisions == ()
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "existing"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id == attention_id
    assert [citation.signal_id for citation in readback.evidence] == [
        result.current_signal_id,
        seed.signal_id,
    ]
    assert len(store.list_business_task_signals()) == len(signals_before) + 1
    assert store.list_business_attention_events(attention_id) == events_before

    replay = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert replay.projection_receipt is not None
    assert (
        replay.projection_receipt.project_assessments
        == result.projection_receipt.project_assessments
    )
    assert len(store.list_business_task_signals()) == len(signals_before) + 1
    assert store.list_business_attention_events(attention_id) == events_before


def test_existing_attention_keeps_card_id_but_reports_current_proposal_error(
    tmp_path,
    monkeypatch,
):
    store = AutoReplyStore(tmp_path / "assessment-existing-card-proposal-error.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    item = _work_item().model_copy(update={"summary": "售前知识库交付风险继续扩大。"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                _stored_project_assessment(
                    item,
                    seed,
                    anchor_id,
                    outcome="needs_attention",
                    reason="当前风险扩大，仍需保留关注。",
                    existing_attention_id=attention_id,
                    decision_indexes=[0],
                    assessment_basis="historical_comparison",
                    evidence=[
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险继续扩大",
                        },
                        {
                            "signal_id": seed.signal_id,
                            "source_ref": "seed:售前知识库",
                            "source_excerpt": "售前知识库历史交付风险",
                        },
                    ],
                    attention_proposal={
                        "category": "watch",
                        "title": "售前知识库交付风险",
                        "why_attention": "交付风险继续扩大",
                        "current_state": "等待交付结果",
                        "ceo_action": "观察结果",
                        "assessment_basis": "historical_comparison",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "交付风险继续扩大",
                            },
                            {
                                "signal_id": seed.signal_id,
                                "source_ref": "seed:售前知识库",
                                "source_excerpt": "售前知识库历史交付风险",
                            },
                        ],
                    },
                )
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "售前知识库交付",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险继续扩大",
                }
            ],
        }
    )
    events_before = store.list_business_attention_events(attention_id)

    def fail_projection(_self, _proposal):
        raise RuntimeError("existing card proposal failed")

    monkeypatch.setattr(BusinessAttentionProjection, "upsert", fail_projection)
    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.projection_receipt is not None
    assert result.projection_receipt.status == "failed"
    assert (
        result.projection_receipt.outcomes[0].reason == "existing card proposal failed"
    )
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "error"
    assert readback.reason == "existing card proposal failed"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id == attention_id
    assert decision.project_assessments[0].outcome == "needs_attention"
    assert store.list_business_attention_events(attention_id) == events_before


def test_negative_assessment_records_known_project_without_closing_existing_card(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-negative-keeps-card.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    item = _work_item()
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [_stored_project_assessment(item, seed, anchor_id)],
            "task_decisions": [],
        }
    )
    events_before = store.list_business_attention_events(attention_id)

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "recorded"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id is None
    assert store.get_business_attention_item(attention_id).status.value == "active"
    assert store.list_business_attention_events(attention_id) == events_before


@pytest.mark.parametrize(
    ("card_change", "problem"),
    [
        ("guessed", "existing Attention card does not exist"),
        ("unrelated", "existing Attention card belongs to a different Project"),
        ("inactive", "existing Attention card is not active"),
        ("wrong_member", "does not contain the assessment's supporting Tasks"),
        ("bad_proof", "historical Project evidence signal/source_ref does not match"),
    ],
)
def test_existing_attention_identity_and_original_proof_are_stored_facts(
    tmp_path,
    card_change,
    problem,
):
    store = AutoReplyStore(tmp_path / "assessment-existing-card-proof.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    if card_change == "unrelated":
        other, other_anchor = _stored_project_task(store, title="其他项目")
        attention_id = _stored_attention_card(
            store,
            seed=other,
            anchor_id=other_anchor,
            title="其他项目",
        )
    elif card_change == "bad_proof":
        with store.business_task_transaction() as db:
            row = store.get_business_attention_item_in_transaction(
                item_id=attention_id, _db=db
            )
            assert row is not None
            broken = json.loads(row.assessment_json)
            broken["evidence"][0]["source_ref"] = "seed:wrong"
            store.update_business_attention_item_in_transaction(
                item=row.model_copy(
                    update={
                        "assessment_json": json.dumps(
                            broken, ensure_ascii=False, sort_keys=True
                        )
                    }
                ),
                _db=db,
            )
    elif card_change == "inactive":
        BusinessAttentionProjection(store).resolve(
            item_id=attention_id,
            resolution_signal_id=seed.signal_id,
            reason="历史卡片已解决",
        )
    elif card_change == "wrong_member":
        with store.business_task_transaction() as db:
            store.replace_business_attention_tasks_in_transaction(
                attention_item_id=attention_id,
                task_ids=(),
                _db=db,
            )
    elif card_change == "guessed":
        attention_id = 999
    item = _work_item()
    assessment = _stored_project_assessment(
        item,
        seed,
        anchor_id,
        outcome="needs_attention",
        reason="同一事实已由现有关注卡表示。",
        existing_attention_id=attention_id,
    )

    with pytest.raises(ValueError, match=problem):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=TaskAgentDecision.model_validate(
                {
                    "project_decisions": [],
                    "project_assessments": [assessment],
                    "task_decisions": [],
                }
            ),
            record_run=False,
        )


def test_existing_attention_rejects_same_project_peer_outside_saved_membership(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-existing-card-project-peer.sqlite3")
    member, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=member, anchor_id=anchor_id)
    peer = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="售前知识库回款",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:collection",
                evidence_text="售前知识库回款",
                dedupe_key="seed:collection",
            ),
        )
    )
    BusinessResolutionService(store).confirm_anchor_match(
        task_id=peer.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=peer.signal_id,
        reason="同一正式 Project",
        relevance=BusinessRelevance.RELEVANT,
    )
    item = _work_item()
    assessment = _stored_project_assessment(
        item,
        member,
        anchor_id,
        outcome="needs_attention",
        reason="复用现有卡片",
        existing_attention_id=attention_id,
        assessment_basis="historical_comparison",
        task_ids=[member.task_id, peer.task_id],
        evidence=[
            {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"},
            {
                "signal_id": member.signal_id,
                "source_ref": "seed:售前知识库",
                "source_excerpt": "售前知识库历史交付风险",
            },
        ],
    )

    with pytest.raises(
        ValueError, match="does not contain the assessment's supporting Tasks"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=TaskAgentDecision.model_validate(
                {
                    "project_decisions": [],
                    "project_assessments": [assessment],
                    "task_decisions": [],
                }
            ),
            record_run=False,
        )
    assert [
        row.task_id for row in store.list_business_attention_tasks(attention_id)
    ] == [
        member.task_id,
    ]


def test_two_current_tasks_share_one_stored_project_judgment(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-two-tasks.sqlite3")
    first, anchor_id = _stored_project_task(store)
    second = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="售前知识库回款",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:collection",
                evidence_text="售前知识库回款",
                dedupe_key="seed:collection",
            ),
        )
    )
    BusinessResolutionService(store).confirm_anchor_match(
        task_id=second.task_id,
        anchor_id=anchor_id,
        evidence_signal_id=second.signal_id,
        reason="同一正式 Project",
        relevance=BusinessRelevance.RELEVANT,
    )
    item = _work_item()
    assessment = _stored_project_assessment(
        item,
        first,
        anchor_id,
        decision_indexes=[0, 1],
        task_ids=[first.task_id, second.task_id],
    )
    decisions = [
        {
            "action": "update_task",
            "transition": "update_fields",
            "task_id": seed.task_id,
            "title": title,
            "status": "waiting",
            "source_ref": item.source.ref,
            "source_excerpt": "补齐来源链接",
        }
        for seed, title in ((first, "售前知识库交付"), (second, "售前知识库回款"))
    ]

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [assessment],
                "task_decisions": decisions,
            }
        ),
        record_run=False,
    )

    assert result.task_ids == (first.task_id, second.task_id)
    assert [
        (entry.decision_index, entry.task_id, entry.anchor_id)
        for entry in result.applied_decisions
    ] == [
        (0, first.task_id, anchor_id),
        (1, second.task_id, anchor_id),
    ]


def test_current_project_proposal_reuses_stored_exact_title_and_maps_actual_identity(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-proposal-existing-project.sqlite3")
    _seed, anchor_id = _stored_project_task(store)
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议决定启动售前知识库，先完成试点交付。",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "售前知识库",
                        "authority": "meeting_decision",
                        "source_excerpt": "会议决定启动售前知识库",
                        "reason": "会议明确立项",
                    },
                    "reason": "采用权威项目定义",
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议决定启动售前知识库",
                        }
                    ],
                }
            ],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "当前是按计划启动，没有新增经营风险。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "启动售前知识库",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "完成试点交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "完成试点交付",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "完成试点交付",
                        }
                    ],
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert len(store.list_business_projects()) == 1
    assert result.applied_decisions[0].decision_index == 0
    assert result.applied_decisions[0].task_id == result.task_ids[0]
    assert result.applied_decisions[0].signal_id > 0
    assert result.applied_decisions[0].anchor_id == anchor_id


@pytest.mark.parametrize("known_project", [False, True])
def test_assessment_receipt_retains_applied_support_task_without_project_link(
    tmp_path, known_project
):
    store = AutoReplyStore(tmp_path / "assessment-unlinked-support.sqlite3")
    anchor_id = None
    if known_project:
        _seed, anchor_id = _stored_project_task(store)
    item = _work_item().model_copy(
        update={
            "summary": "售前知识库：有风险。行动：汇总当前进度。",
        }
    )
    projects_before = store.list_business_projects()
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "anchor_id": anchor_id,
                    "outcome": "insufficient_evidence",
                    "reason": "未说明风险的具体经营影响。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0] if not known_project else [],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "售前知识库：有风险。",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "汇总当前进度",
                    "source_ref": item.source.ref,
                    "source_excerpt": "行动：汇总当前进度。",
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    [applied] = result.applied_decisions
    assert applied.anchor_id is None
    assert store.get_business_task(applied.task_id).title == "汇总当前进度"
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.task_ids == []
    assert readback.anchor_id == anchor_id
    assert readback.status == "recorded"
    assert readback.attention_id is None
    assert readback.evidence[0].signal_id == result.current_signal_id
    assert readback.evidence[0].source_time == item.source.created_at
    assert store.list_business_task_anchor_links(task_id=applied.task_id) == ()
    assert store.list_business_projects() == projects_before
    assert store.list_business_attention_items() == ()


def test_unknown_current_project_clue_saves_source_without_fake_business_objects(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-unknown-clue.sqlite3")
    item = _work_item().model_copy(
        update={"summary": "也许与远期海外机会有关，但无法确认 Project 或行动。"}
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "远期海外机会",
                    "outcome": "insufficient_evidence",
                    "reason": "只有线索，无法确认 Project 身份、Task 或经营影响。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "远期海外机会",
                        }
                    ],
                }
            ],
            "task_decisions": [],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.task_ids == ()
    assert result.applied_decisions == ()
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.assessment_index == 0
    assert readback.status == "recorded"
    assert readback.anchor_id is None
    assert readback.task_ids == []
    assert readback.attention_id is None
    assert (
        readback.reason
        == "Assessment recorded without an applied Project or Attention identity."
    )
    assert readback.evidence[0].model_dump() == {
        "source_ref": item.source.ref,
        "source_excerpt": "远期海外机会",
        "signal_id": result.current_signal_id,
        "source_time": item.source.created_at,
        "source_link": "",
    }
    assert store.list_business_tasks() == ()
    assert len(store.list_business_task_signals()) == 1
    assert (
        store.get_business_task_signal(result.current_signal_id).evidence_text
        == item.summary
    )
    assert store.list_business_projects() == []
    assert store.list_business_attention_items() == ()


def test_project_and_attention_apply_without_task_lifecycle_changes(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-no-change-proposal.sqlite3")
    seed = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="完成试点交付",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:trial",
                evidence_text="完成试点交付",
                dedupe_key="seed:trial",
            ),
        )
    )
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议决定启动新试点项目，并继续完成试点交付。验收标准未确认，影响试点交付。",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "新试点项目",
                        "authority": "meeting_decision",
                        "source_excerpt": "会议决定启动新试点项目",
                        "reason": "会议明确立项",
                    },
                    "context": None,
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议决定启动新试点项目",
                        }
                    ],
                    "reason": "会议明确立项",
                }
            ],
            "project_assessments": [
                {
                    "project_title": "新试点项目",
                    "project_decision_index": 0,
                    "outcome": "needs_attention",
                    "reason": "当前原文提出风险，但 Task 字段没有变化。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [seed.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "验收标准未确认，影响试点交付",
                        }
                    ],
                    "attention_proposal": {
                        "category": "watch",
                        "title": "新试点项目风险",
                        "why_attention": "需要观察",
                        "current_state": "等待来源",
                        "ceo_action": "暂不介入",
                        "assessment_basis": "current_observation",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "验收标准未确认，影响试点交付",
                            }
                        ],
                    },
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "完成试点交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "完成试点交付",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "完成试点交付",
                        }
                    ],
                }
            ],
        }
    )
    signals_before = store.list_business_task_signals()
    task_before = store.get_business_task(seed.task_id)
    events_before = store.list_business_task_events(seed.task_id)
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert len(result.applied_decisions) == 1
    assert len(result.attention_proposals) == 1
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "applied"
    assert readback.anchor_id == result.applied_projects[0].anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id is not None
    assert len(store.list_business_task_signals()) == len(signals_before) + 1
    assert len(store.list_business_projects()) == 1
    assert len(store.list_business_task_anchor_links(task_id=seed.task_id)) == 1
    task_after = store.get_business_task(seed.task_id)
    for field in (
        "title",
        "stage",
        "status",
        "owner_name",
        "owner_user_id",
        "deadline_at",
        "commitment_status",
    ):
        assert getattr(task_after, field) == getattr(task_before, field)
    assert [
        event.event_type.value
        for event in store.list_business_task_events(seed.task_id)
    ] == [*(event.event_type.value for event in events_before), "relevance_changed"]
    assert len(store.list_business_attention_items()) == 1


def test_evidence_only_exact_title_proposal_reuses_existing_project_and_task_ids(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-existing-project-no-change.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    task_before = store.get_business_task(seed.task_id)
    events_before = store.list_business_task_events(seed.task_id)
    links_before = store.list_business_task_anchor_links(task_id=seed.task_id)
    assert store.list_business_attention_items() == ()
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议再次确认售前知识库，售前知识库交付风险需要观察。",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "售前知识库",
                        "authority": "meeting_decision",
                        "source_excerpt": "会议再次确认售前知识库",
                        "reason": "会议确认既有项目",
                    },
                    "context": None,
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议再次确认售前知识库",
                        }
                    ],
                    "reason": "会议确认既有项目",
                }
            ],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "project_decision_index": 0,
                    "outcome": "needs_attention",
                    "reason": "交付风险需要观察。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [seed.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                    "attention_proposal": {
                        "category": "watch",
                        "title": "售前知识库交付风险",
                        "why_attention": "交付风险需要观察",
                        "current_state": "等待交付结果",
                        "ceo_action": "观察结果",
                        "assessment_basis": "current_observation",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "交付风险需要观察",
                            }
                        ],
                    },
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "售前知识库交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "售前知识库交付风险需要观察",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "售前知识库交付风险需要观察",
                        }
                    ],
                }
            ],
        }
    )
    signals_before = store.list_business_task_signals()
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert len(result.applied_decisions) == 1
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "applied"
    assert readback.anchor_id == anchor_id
    assert readback.task_ids == [seed.task_id]
    assert readback.attention_id is not None
    assert readback.evidence[0].signal_id == result.applied_decisions[0].signal_id
    assert len(store.list_business_task_signals()) == len(signals_before) + 1
    assert len(store.list_business_projects()) == 1
    assert store.list_business_attention_items()[0].id == readback.attention_id
    card_evidence = json.loads(
        store.list_business_attention_items()[0].assessment_json
    )["evidence"]
    assert card_evidence[0]["signal_id"] == readback.evidence[0].signal_id
    assert card_evidence[0]["source_ref"] == item.source.ref
    assert store.get_business_task(seed.task_id) == task_before
    assert store.list_business_task_events(seed.task_id) == events_before
    assert store.list_business_task_anchor_links(task_id=seed.task_id) == links_before


def test_assessment_task_id_maps_matching_signal_without_decision_index(tmp_path):
    store = AutoReplyStore(tmp_path / "assessment-task-id-signal-readback.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item().model_copy(update={"summary": "售前知识库交付补齐本轮来源。"})
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "anchor_id": anchor_id,
                    "outcome": "not_needed",
                    "reason": "本轮只补来源，没有新增经营影响。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [],
                    "task_ids": [seed.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "补齐本轮来源",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "售前知识库交付",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "补齐本轮来源",
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.applied_decisions[0].task_id == seed.task_id
    assert result.projection_receipt is not None
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "recorded"
    assert readback.evidence[0].signal_id == result.current_signal_id


def test_stored_assessment_rejects_project_identity_contradicting_canonical_title_before_writes(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-selector-contradiction.sqlite3")
    _first, first_anchor = _stored_project_task(store)
    _second, second_anchor = _stored_project_task(store, title="其他项目")
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议决定启动售前知识库，但交付风险需要观察。",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "售前知识库",
                        "authority": "meeting_decision",
                        "source_excerpt": "会议决定启动售前知识库",
                        "reason": "会议明确立项",
                    },
                    "context": None,
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议决定启动售前知识库",
                        }
                    ],
                    "reason": "会议明确立项",
                }
            ],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "project_decision_index": 0,
                    "outcome": "needs_attention",
                    "reason": "当前交付风险需要观察。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                    "attention_proposal": {
                        "category": "watch",
                        "title": "交付风险",
                        "why_attention": "风险待核实",
                        "current_state": "等待交付",
                        "ceo_action": "观察结果",
                        "assessment_basis": "current_observation",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "交付风险需要观察",
                            }
                        ],
                    },
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "完成交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险需要观察",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                }
            ],
        }
    )
    payload = decision.model_dump(mode="json")
    payload["project_decisions"][0].update(registration=None, anchor_id=second_anchor)
    decision = TaskAgentDecision.model_validate(payload)
    tasks_before = store.list_business_tasks()
    signals_before = store.list_business_task_signals()
    with pytest.raises(
        ValueError, match="project_title must match the canonical stored Project title"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert first_anchor != second_anchor
    assert store.list_business_tasks() == tasks_before
    assert store.list_business_task_signals() == signals_before


def test_stored_assessment_accepts_project_proposal_and_attention_on_same_canonical_project(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-selector-canonical-control.sqlite3")
    _seed, anchor_id = _stored_project_task(store)
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议决定启动售前知识库，但交付风险需要观察。",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "售前知识库",
                        "authority": "meeting_decision",
                        "source_excerpt": "会议决定启动售前知识库",
                        "reason": "会议明确立项",
                    },
                    "context": None,
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议决定启动售前知识库",
                        }
                    ],
                    "reason": "会议明确立项",
                }
            ],
            "project_assessments": [
                {
                    "project_title": "售前知识库",
                    "project_decision_index": 0,
                    "outcome": "needs_attention",
                    "reason": "当前交付风险需要观察。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                    "attention_proposal": {
                        "category": "watch",
                        "title": "交付风险",
                        "why_attention": "风险待核实",
                        "current_state": "等待交付",
                        "ceo_action": "观察结果",
                        "assessment_basis": "current_observation",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "交付风险需要观察",
                            }
                        ],
                    },
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "完成交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险需要观察",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                }
            ],
        }
    )
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert result.applied_decisions[0].anchor_id == anchor_id
    assert result.attention_proposals[0].anchor_id == anchor_id


def test_new_project_assessment_rejects_task_from_different_registered_project_before_writes(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-new-selector-contradiction.sqlite3")
    _other, other_anchor = _stored_project_task(store, title="另一项目")
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议决定启动新业务项目，但交付风险需要观察。",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "新业务项目",
                        "authority": "meeting_decision",
                        "source_excerpt": "会议决定启动新业务项目",
                        "reason": "会议明确立项",
                    },
                    "context": None,
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议决定启动新业务项目",
                        }
                    ],
                    "reason": "会议明确立项",
                }
            ],
            "project_assessments": [
                {
                    "project_title": "新业务项目",
                    "project_decision_index": 0,
                    "outcome": "needs_attention",
                    "reason": "当前交付风险需要观察。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                    "attention_proposal": {
                        "category": "watch",
                        "title": "新业务交付风险",
                        "why_attention": "风险待核实",
                        "current_state": "等待交付",
                        "ceo_action": "观察结果",
                        "assessment_basis": "current_observation",
                        "material_trigger": "risk_escalation",
                        "evidence": [
                            {
                                "source_ref": item.source.ref,
                                "source_excerpt": "交付风险需要观察",
                            }
                        ],
                    },
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "完成新业务交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "交付风险需要观察",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                }
            ],
        }
    )
    payload = decision.model_dump(mode="json")
    payload["project_assessments"][0]["task_ids"] = [_other.task_id]
    decision = TaskAgentDecision.model_validate(payload)
    tasks_before = store.list_business_tasks()
    signals_before = store.list_business_task_signals()
    projects_before = store.list_business_projects()
    with pytest.raises(
        ValueError, match="supporting Task is not confirmed to the assessed Project"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == tasks_before
    assert store.list_business_task_signals() == signals_before
    assert store.list_business_projects() == projects_before


def test_new_project_attention_rejects_guessed_future_anchor_and_accepts_null_resolution(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-new-project-future-anchor.sqlite3")
    _first, _first_anchor = _stored_project_task(store)
    _second, _second_anchor = _stored_project_task(store, title="另一项目")
    guessed_future_anchor = 3
    with store.business_task_transaction() as db:
        assert (
            store.get_business_anchor_in_transaction(
                anchor_id=guessed_future_anchor, _db=db
            )
            is None
        )
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议决定启动新业务项目，但交付风险需要观察。",
        }
    )
    payload = {
        "project_decisions": [
            {
                "registration": {
                    "title": "新业务项目",
                    "authority": "meeting_decision",
                    "source_excerpt": "会议决定启动新业务项目",
                    "reason": "会议明确立项",
                },
                "context": None,
                "evidence": [
                    {
                        "source_ref": item.source.ref,
                        "source_excerpt": "会议决定启动新业务项目",
                    }
                ],
                "reason": "会议明确立项",
            }
        ],
        "project_assessments": [
            {
                "project_title": "新业务项目",
                "project_decision_index": 0,
                "outcome": "needs_attention",
                "reason": "当前交付风险需要观察。",
                "assessment_basis": "current_observation",
                "decision_indexes": [0],
                "task_ids": [],
                "evidence": [
                    {
                        "source_ref": item.source.ref,
                        "source_excerpt": "交付风险需要观察",
                    }
                ],
                "attention_proposal": {
                    "category": "watch",
                    "title": "新业务交付风险",
                    "why_attention": "风险待核实",
                    "current_state": "等待交付",
                    "ceo_action": "观察结果",
                    "assessment_basis": "current_observation",
                    "material_trigger": "risk_escalation",
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "交付风险需要观察",
                        }
                    ],
                },
            }
        ],
        "task_decisions": [
            {
                "action": "record_candidate",
                "transition": "none",
                "title": "完成新业务交付",
                "source_ref": item.source.ref,
                "source_excerpt": "交付风险需要观察",
                "project": {"project_decision_index": 0},
                "project_link_evidence": [
                    {
                        "source_ref": item.source.ref,
                        "source_excerpt": "交付风险需要观察",
                    }
                ],
            }
        ],
    }
    registration = payload["project_decisions"][0]["registration"]
    payload["project_decisions"][0].update(
        registration=None, anchor_id=guessed_future_anchor
    )
    tasks_before = store.list_business_tasks()
    signals_before = store.list_business_task_signals()
    with pytest.raises(ValueError, match="registered active official Project"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=TaskAgentDecision.model_validate(payload),
            record_run=False,
        )
    assert store.list_business_tasks() == tasks_before
    assert store.list_business_task_signals() == signals_before
    payload["project_decisions"][0].update(registration=registration, anchor_id=None)
    result = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=item,
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    project = next(
        (
            project
            for project in store.list_business_projects()
            if project.title == "新业务项目"
        )
    )
    assert result.applied_decisions[0].anchor_id == project.canonical_anchor_id
    assert result.attention_proposals[0].anchor_id == project.canonical_anchor_id
    assert result.projection_receipt is not None
    assert result.projection_receipt.status == "completed"


def test_existing_attention_requires_assessment_to_cite_that_cards_original_proof(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-card-proof-binding.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    other_signal_id = store.create_business_task_signal(
        source_type="seed",
        source_ref="seed:other-proof",
        evidence_text="另一条真实历史事实",
        dedupe_key="seed:other-proof",
    )
    store.link_business_task_evidence(
        task_id=seed.task_id,
        signal_id=other_signal_id,
        evidence_role="discovery",
    )
    item = _work_item()
    current_only = _stored_project_assessment(
        item,
        seed,
        anchor_id,
        outcome="needs_attention",
        reason="声称现有卡片已经表示该事实。",
        existing_attention_id=attention_id,
    )
    unrelated_history = _stored_project_assessment(
        item,
        seed,
        anchor_id,
        outcome="needs_attention",
        reason="引用同项目的另一条历史事实。",
        existing_attention_id=attention_id,
        assessment_basis="historical_comparison",
        evidence=[
            {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"},
            {
                "signal_id": other_signal_id,
                "source_ref": "seed:other-proof",
                "source_excerpt": "另一条真实历史事实",
            },
        ],
        task_ids=[seed.task_id],
    )

    for assessment in (current_only, unrelated_history):
        with pytest.raises(
            ValueError, match="must cite this card's stored original evidence"
        ):
            apply_task_agent_decision(
                store,
                summary_input_id=1,
                work_item=item,
                decision=TaskAgentDecision.model_validate(
                    {
                        "project_decisions": [],
                        "project_assessments": [assessment],
                        "task_decisions": [],
                    }
                ),
                record_run=False,
            )


def test_existing_attention_changed_current_words_with_actual_old_proof_is_idempotent(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-card-old-proof-repeat.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    item = _work_item().model_copy(
        update={"summary": "这次使用不同措辞说明仍需观察，但没有新的 Task 字段。"}
    )
    assessment = _stored_project_assessment(
        item,
        seed,
        anchor_id,
        outcome="needs_attention",
        reason="新消息与卡片保存的原始风险一起支持继续观察。",
        existing_attention_id=attention_id,
        assessment_basis="historical_comparison",
        evidence=[
            {"source_ref": item.source.ref, "source_excerpt": "不同措辞说明仍需观察"},
            {
                "signal_id": seed.signal_id,
                "source_ref": "seed:售前知识库",
                "source_excerpt": "售前知识库历史交付风险",
            },
        ],
    )
    events_before = store.list_business_attention_events(attention_id)

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [assessment],
                "task_decisions": [],
            }
        ),
        record_run=False,
    )

    assert result.task_ids == ()
    assert result.applied_decisions == ()
    assert store.list_business_attention_events(attention_id) == events_before
    assert len(store.list_business_attention_items()) == 1


def test_stored_assessment_membership_checks_do_not_materialize_full_task_evidence(
    tmp_path,
    monkeypatch,
):
    store = AutoReplyStore(tmp_path / "assessment-bounded-evidence-membership.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    second_signal_id = store.create_business_task_signal(
        source_type="seed",
        source_ref="seed:second-card-proof",
        evidence_text="第二条历史交付事实",
        dedupe_key="seed:second-card-proof",
    )
    store.link_business_task_evidence(
        task_id=seed.task_id,
        signal_id=second_signal_id,
        evidence_role="discovery",
    )
    stored_evidence = [
        {
            "signal_id": seed.signal_id,
            "source_ref": "seed:售前知识库",
            "source_excerpt": "售前知识库历史交付风险",
            "source_time": "",
            "source_link": "",
        },
        {
            "signal_id": second_signal_id,
            "source_ref": "seed:second-card-proof",
            "source_excerpt": "第二条历史交付事实",
            "source_time": "",
            "source_link": "",
        },
    ]
    attention_id = BusinessAttentionProjection(store).upsert(
        AttentionProposal(
            stable_key=f"project:{anchor_id}",
            category=AttentionCategory.WATCH,
            title="售前知识库交付风险",
            business_area="",
            why_attention="两条历史事实仍需观察",
            current_state="等待新的交付结果",
            ceo_action="暂不介入，观察结果",
            anchor_id=anchor_id,
            task_ids=(seed.task_id,),
            evidence_signal_id=seed.signal_id,
            assessment_json=json.dumps(
                {"evidence": stored_evidence}, ensure_ascii=False
            ),
        )
    )
    item = _work_item()
    assessment = _stored_project_assessment(
        item,
        seed,
        anchor_id,
        outcome="needs_attention",
        reason="当前来源与两条卡片原始事实支持继续观察。",
        existing_attention_id=attention_id,
        assessment_basis="historical_comparison",
        evidence=[
            {"source_ref": item.source.ref, "source_excerpt": "补齐来源链接"},
            *(
                {
                    "signal_id": proof["signal_id"],
                    "source_ref": proof["source_ref"],
                    "source_excerpt": proof["source_excerpt"],
                }
                for proof in stored_evidence
            ),
        ],
    )

    def reject_full_history_materialization(*_args, **_kwargs):
        raise AssertionError(
            "stored assessment validation must use bounded membership lookup"
        )

    monkeypatch.setattr(
        store,
        "list_business_task_evidence_in_transaction",
        reject_full_history_materialization,
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=TaskAgentDecision.model_validate(
            {
                "project_decisions": [],
                "project_assessments": [assessment],
                "task_decisions": [],
            }
        ),
        record_run=False,
    )

    assert result.task_ids == ()
    assert result.applied_decisions == ()


def test_existing_attention_same_ref_quote_with_different_source_identity_requires_original_signal(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-card-current-repeat.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    attention_id = _stored_attention_card(store, seed=seed, anchor_id=anchor_id)
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(update={"ref": "seed:售前知识库"}),
            "summary": "售前知识库历史交付风险",
        }
    )
    assessment = _stored_project_assessment(
        item,
        seed,
        anchor_id,
        outcome="needs_attention",
        reason="同一原始来源重放，现有卡片继续代表该事实。",
        existing_attention_id=attention_id,
        evidence=[
            {
                "source_ref": "seed:售前知识库",
                "source_excerpt": "售前知识库历史交付风险",
            }
        ],
    )
    events_before = store.list_business_attention_events(attention_id)

    payload = {
        "project_decisions": [],
        "project_assessments": [assessment],
        "task_decisions": [],
    }
    original_signal = store.get_business_task_signal(seed.signal_id)
    assert original_signal.source_type != item.source.type.value
    signals_before = store.list_business_task_signals()
    with pytest.raises(
        ValueError, match="must cite this card's stored original evidence"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=TaskAgentDecision.model_validate(payload),
            record_run=False,
        )
    assert store.list_business_task_signals() == signals_before
    assert store.list_business_attention_events(attention_id) == events_before
    # Exact old Signal identity, not matching text/ref alone, verifies the old card.
    assessment["assessment_basis"] = "historical_comparison"
    assessment["evidence"].append(
        {
            "signal_id": seed.signal_id,
            "source_ref": original_signal.source_ref,
            "source_excerpt": "售前知识库历史交付风险",
        }
    )
    result = apply_task_agent_decision(
        store,
        summary_input_id=2,
        work_item=item,
        decision=TaskAgentDecision.model_validate(payload),
        record_run=False,
    )
    [readback] = result.projection_receipt.project_assessments
    assert readback.status == "existing"
    assert readback.attention_id == attention_id
    assert readback.evidence[-1].signal_id == seed.signal_id
    assert store.list_business_attention_events(attention_id) == events_before


def test_applied_mapping_does_not_claim_project_from_assessment_without_actual_link(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-truthful-actual-map.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item()
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [
                _stored_project_assessment(
                    item,
                    seed,
                    anchor_id,
                    decision_indexes=[],
                    task_ids=[],
                )
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "新建但未关联的 Task",
                    "source_ref": item.source.ref,
                    "source_excerpt": "补齐来源链接",
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.applied_decisions[0].anchor_id is None
    assert store.list_business_task_anchor_links(task_id=result.task_ids[0]) == ()


def test_applied_mapping_uses_unique_actual_confirmed_project_without_support_index(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-map-unique-confirmed-project.sqlite3")
    seed, anchor_id = _stored_project_task(store)
    item = _work_item()
    assessment = _stored_project_assessment(
        item,
        seed,
        anchor_id,
        decision_indexes=[],
        task_ids=[seed.task_id],
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [],
            "project_assessments": [assessment],
            "task_decisions": [
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": seed.task_id,
                    "title": "售前知识库交付",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "补齐来源链接",
                }
            ],
        }
    )

    result = apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=item,
        decision=decision,
        record_run=False,
    )

    assert result.applied_decisions[0].anchor_id == anchor_id


def test_new_project_rejects_unrelated_existing_task_without_matching_supported_decision(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-new-project-unrelated-task.sqlite3")
    unrelated = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="无关旧 Task",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:unrelated-old",
                evidence_text="无关旧 Task",
                dedupe_key="seed:unrelated-old",
            ),
        )
    )
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议决定启动全新项目，并完成新的交付。",
        }
    )
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": {
                        "title": "全新项目",
                        "authority": "meeting_decision",
                        "source_excerpt": "会议决定启动全新项目",
                        "reason": "会议明确立项",
                    },
                    "context": None,
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议决定启动全新项目",
                        }
                    ],
                    "reason": "会议明确立项",
                }
            ],
            "project_assessments": [
                {
                    "project_title": "全新项目",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "当前只是正常立项。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0],
                    "task_ids": [unrelated.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "启动全新项目",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "完成新的交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "完成新的交付",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "完成新的交付",
                        }
                    ],
                }
            ],
        }
    )
    tasks_before = store.list_business_tasks()
    with pytest.raises(
        ValueError, match="supporting Task is not confirmed to the assessed Project"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == tasks_before
    assert store.list_business_projects() == []


def test_new_project_rejects_indexed_existing_task_without_matching_project_selector(
    tmp_path,
):
    store = AutoReplyStore(
        tmp_path / "assessment-new-project-indexed-unrelated.sqlite3"
    )
    unrelated = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="无关旧 Task",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:indexed-unrelated",
                evidence_text="无关旧 Task",
                dedupe_key="seed:indexed-unrelated",
            ),
        )
    )
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议决定启动全新项目，完成新的交付，并更新无关旧 Task。",
        }
    )
    proposal = {
        "title": "全新项目",
        "authority": "meeting_decision",
        "source_excerpt": "会议决定启动全新项目",
        "reason": "会议明确立项",
    }
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": proposal,
                    "context": None,
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议决定启动全新项目",
                        }
                    ],
                    "reason": "会议明确立项",
                }
            ],
            "project_assessments": [
                {
                    "project_title": "全新项目",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "当前只是正常立项。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0, 1],
                    "task_ids": [],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "启动全新项目",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "完成新的交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "完成新的交付",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "完成新的交付",
                        }
                    ],
                },
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": unrelated.task_id,
                    "title": "无关旧 Task",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "更新无关旧 Task",
                },
            ],
        }
    )
    tasks_before = store.list_business_tasks()
    signals_before = store.list_business_task_signals()
    with pytest.raises(
        ValueError, match="supporting Task is not confirmed to the assessed Project"
    ):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert store.list_business_tasks() == tasks_before
    assert store.list_business_task_signals() == signals_before


def test_new_project_accepts_existing_task_only_when_matching_current_decision_links_it(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "assessment-new-project-supported-task.sqlite3")
    existing = TaskSemanticService(store).record_candidate(
        RecordCandidate(
            title="既有交付",
            signal=SourceSignal(
                source_type="seed",
                source_ref="seed:existing-delivery",
                evidence_text="既有交付",
                dedupe_key="seed:existing-delivery",
            ),
        )
    )
    base = _work_item()
    item = base.model_copy(
        update={
            "source": base.source.model_copy(
                update={"type": WorkItemSourceType.AI_MINUTES}
            ),
            "context": base.context.model_copy(
                update={"source_conversation_kind": WorkItemSourceKind.MINUTES}
            ),
            "summary": "会议决定启动全新项目，完成新的交付，并更新既有交付。",
        }
    )
    proposal = {
        "title": "全新项目",
        "authority": "meeting_decision",
        "source_excerpt": "会议决定启动全新项目",
        "reason": "会议明确立项",
    }
    decision = TaskAgentDecision.model_validate(
        {
            "project_decisions": [
                {
                    "registration": proposal,
                    "context": None,
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "会议决定启动全新项目",
                        }
                    ],
                    "reason": "会议明确立项",
                }
            ],
            "project_assessments": [
                {
                    "project_title": "全新项目",
                    "project_decision_index": 0,
                    "outcome": "not_needed",
                    "reason": "当前只是正常立项。",
                    "assessment_basis": "current_observation",
                    "decision_indexes": [0, 1],
                    "task_ids": [existing.task_id],
                    "evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "启动全新项目",
                        }
                    ],
                }
            ],
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "title": "完成新的交付",
                    "source_ref": item.source.ref,
                    "source_excerpt": "完成新的交付",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "完成新的交付",
                        }
                    ],
                },
                {
                    "action": "update_task",
                    "transition": "update_fields",
                    "task_id": existing.task_id,
                    "title": "既有交付",
                    "status": "waiting",
                    "source_ref": item.source.ref,
                    "source_excerpt": "更新既有交付",
                    "project": {"project_decision_index": 0},
                    "project_link_evidence": [
                        {
                            "source_ref": item.source.ref,
                            "source_excerpt": "更新既有交付",
                        }
                    ],
                },
            ],
        }
    )
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    project = store.list_business_projects()[0]
    assert all(
        (
            entry.anchor_id == project.canonical_anchor_id
            for entry in result.applied_decisions
        )
    )
    assert (
        store.list_business_task_anchor_links(task_id=existing.task_id)[0].anchor_id
        == project.canonical_anchor_id
    )
