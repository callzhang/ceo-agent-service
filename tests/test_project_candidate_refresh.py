"""A confirmed Project candidate refreshes only its affected card members."""

import json

from app.project_context_service import ProjectContextService
from app.store import AutoReplyStore
from app.task_agent import apply_task_agent_decision
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_business_resolution import BusinessResolutionService
from app.task_models import TaskAgentDecision, WorkItem
from app.task_semantic_models import AttentionCategory, BusinessRelevance


PROJECT_ROW = "| 示例项目 | 回款复核 | 降低现金流风险 | 09-30 | 有风险 |"
TASK_QUOTE = "复核示例项目回款及供应商付款计划。"


def test_report_confirmation_refreshes_existing_card_peer_without_swallowing_other_tasks(tmp_path):
    store = AutoReplyStore(tmp_path / "candidate-refresh.sqlite3")
    resolver = BusinessResolutionService(store)
    projection = BusinessAttentionProjection(store)
    task_id = store.create_business_task(title="复核回款计划", stage="candidate")
    peer_id = store.create_business_task(title="协调付款计划", stage="candidate")
    other_id = store.create_business_task(title="整理项目培训资料", stage="candidate")
    anchor_id = resolver.register_anchor(
        anchor_type="project", anchor_ref="project:example", title="示例项目"
    )
    project_id = resolver.register_official_project(
        anchor_id=anchor_id, registry_source="project_weekly_report:old"
    )
    old_proof = store.create_business_task_signal(
        source_type="message", source_ref="message:old-risk",
        evidence_text="示例项目回款风险尚未解决。", dedupe_key="message:old-risk",
    )
    with store.business_task_transaction() as db:
        ProjectContextService(store).apply(
            project_id=project_id, context=None, signal_ids=(old_proof,), db=db
        )
    for linked_task in (peer_id, other_id):
        resolver.confirm_anchor_match(
            task_id=linked_task, anchor_id=anchor_id, evidence_signal_id=old_proof
        )
    card_id = projection.upsert(AttentionProposal(
        stable_key=f"project:{anchor_id}", category=AttentionCategory.WATCH,
        title="回款风险", business_area="", why_attention="回款仍有风险",
        current_state="尚未解决", ceo_action="观察进展", anchor_id=anchor_id,
        task_ids=(peer_id,), evidence_signal_id=old_proof,
        assessment_json=json.dumps({"evidence": [{
            "signal_id": old_proof, "source_ref": "message:old-risk",
            "source_excerpt": "示例项目回款风险尚未解决。",
        }]}, ensure_ascii=False),
    ))
    original_card = store.get_business_attention_item(card_id)
    resolver.confirm_anchor_match(
        task_id=peer_id, anchor_id=anchor_id, evidence_signal_id=old_proof,
        relevance=BusinessRelevance.NOT_RELEVANT,
    )
    assert projection.recompute_for_tasks((peer_id,)) == (card_id,)
    assert store.list_business_attention_tasks(card_id) == ()
    before_events = store.list_business_attention_events(card_id)

    cluster_id = resolver.create_cluster(
        title="示例项目", task_ids=[task_id, peer_id]
    )
    resolver.propose_project(
        cluster_id=cluster_id, title="示例项目", reason="既有聚类待确认"
    )
    item = WorkItem.model_validate({
        "source": {
            "type": "project_weekly_report", "ref": "report:current",
            "created_at": "2026-10-04T12:00:00Z",
        },
        "context": {"source_conversation_kind": "group"},
        "summary": json.dumps({
            "report": {"reporting_period": "2026-W40"},
            "markdown": (
                "## 手头项目\n| 项目名 | 负责内容 | 目标 | DDL | 状态 |\n"
                "| --- | --- | --- | --- | --- |\n"
                f"{PROJECT_ROW}\n## 下周工作重点\n{TASK_QUOTE}"
            ),
        }, ensure_ascii=False),
    })
    decision = TaskAgentDecision.model_validate({
        "project_decisions": [{
            "registration": {
                "title": "示例项目", "authority": "project_weekly_report",
                "source_excerpt": PROJECT_ROW, "reason": "正式项目登记行",
            },
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": PROJECT_ROW}],
            "reason": "当前周报确认正式项目",
        }],
        "task_decisions": [{
            "action": "update_task", "transition": "update_fields", "task_id": task_id,
            "description": "本轮补充回款复核范围", "source_ref": item.source.ref,
            "source_excerpt": TASK_QUOTE,
            "project": {"project_decision_index": 0},
            "project_link_evidence": [{
                "source_ref": item.source.ref, "source_excerpt": TASK_QUOTE,
            }],
            "cluster_proposal": {
                "cluster_id": cluster_id, "task_ids": [task_id, peer_id],
                "reason": "同一项目行动聚类",
            },
        }],
        "project_assessments": [{
            "project_title": "示例项目", "project_decision_index": 0,
            "outcome": "not_needed", "reason": "本轮仅确认项目归属，无新风险判断",
            "assessment_basis": "current_observation", "decision_indexes": [0],
            "evidence": [{"source_ref": item.source.ref, "source_excerpt": PROJECT_ROW}],
        }],
    })

    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert (peer_id, anchor_id) in result.project_links
    assert store.get_business_task(peer_id).business_relevance is BusinessRelevance.RELEVANT
    assert [link.task_id for link in store.list_business_attention_tasks(card_id)] == [peer_id]
    assert other_id not in [link.task_id for link in store.list_business_attention_tasks(card_id)]
    card = store.get_business_attention_item(card_id)
    assert (card.status, card.category, card.why_attention, card.current_state) == (
        original_card.status, original_card.category,
        original_card.why_attention, original_card.current_state,
    )
    events = store.list_business_attention_events(card_id)
    assert len(events) == len(before_events) + 1
    assert events[-1].event_type.value == "updated"

    apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert store.list_business_attention_events(card_id) == events
