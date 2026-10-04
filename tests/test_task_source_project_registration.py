"""Current authoritative Project identity across report and meeting registration."""

import json
from pathlib import Path

import pytest

from app.store import AutoReplyStore
from app.task_agent import apply_task_agent_decision, build_task_agent_prompt
from app.task_business_resolution import BusinessResolutionService
from app.task_models import ProjectProposal, TaskAgentDecision, WorkItem


def source_registration(source_type, task_id, *, with_attention=False):
    title = "示例创智"
    action = "复核示例创智回款计划，补充供应商付款安排。"
    report_row = "| 示例创智 | 回款及付款 | 降低现金流风险 | 待确认 | 有风险 |"
    meeting_quote = "会议决定立项示例创智，统筹回款及供应商付款。"
    risk = "示例创智客户确认延迟导致回款推迟，供应商到期款项无法按原计划支付。"
    if source_type == "project_weekly_report":
        quote = report_row
        summary = json.dumps(
            {
                "markdown": "## 手头项目\n"
                "| 项目名 | 负责内容 | 目标 | DDL | 状态 |\n|---|---|---|---|---|\n"
                + quote
                + "\n## 下周工作重点\n"
                + action
                + (("\n" + risk) if with_attention else "")
            },
            ensure_ascii=False,
        )
        authority = source_type
    else:
        quote = meeting_quote
        summary = quote + "\n" + action + (("\n" + risk) if with_attention else "")
        authority = "meeting_decision"
    item = WorkItem.model_validate(
        {
            "source": {"type": source_type, "ref": "source:current"},
            "context": {
                "source_conversation_kind": "minutes"
                if source_type == "ai_minutes"
                else "file"
            },
            "summary": summary,
        }
    )
    payload = {
        "project_decisions": [
            {
                "registration": {
                    "title": title,
                    "authority": authority,
                    "source_excerpt": quote,
                    "reason": "当前明确的正式项目定义",
                },
                "evidence": [{"source_ref": item.source.ref, "source_excerpt": quote}],
                "reason": "按权威定义登记或复用项目，不依赖 Task 名称",
            }
        ],
        "task_decisions": [
            {
                "action": "update_task",
                "transition": "update_fields",
                "task_id": task_id,
                "source_ref": item.source.ref,
                "source_excerpt": action,
                "source_description": "当前权威来源",
                "description": action,
                "project": {"project_decision_index": 0},
                "project_link_evidence": [
                    {"source_ref": item.source.ref, "source_excerpt": action}
                ],
            }
        ],
    }
    attention = None
    if with_attention:
        attention = {
            "assessment_basis": "current_observation",
            "category": "watch",
            "title": "示例创智回款与付款风险",
            "why_attention": "收入兑现延迟影响到期供应商付款。",
            "current_state": risk,
            "ceo_action": "当前无需你处理；观察回款及供应商付款安排。",
            "material_trigger": "risk_escalation",
            "evidence": [
                {
                    "signal_id": None,
                    "source_ref": item.source.ref,
                    "source_excerpt": risk,
                }
            ],
        }
    payload["project_assessments"] = [
        {
            "project_title": title,
            "outcome": "needs_attention" if with_attention else "not_needed",
            "reason": (
                "客户确认延迟同时影响回款和到期供应商付款。"
                if with_attention
                else "项目身份与具体安排已明确，本轮无明确重大经营影响。"
            ),
            "assessment_basis": "current_observation",
            "evidence": [
                {
                    "signal_id": None,
                    "source_ref": item.source.ref,
                    "source_excerpt": risk if with_attention else quote,
                }
            ],
            "project_decision_index": 0,
            "decision_indexes": [0],
            "task_ids": [task_id],
            "attention_proposal": attention,
        }
    ]
    decision = TaskAgentDecision.model_validate(payload)
    return item, decision


def seed_project(store, *, title="示例创智", ref="legacy:project", active=True):
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref=ref, title=title
    )
    project_id = resolution.register_official_project(
        anchor_id=anchor_id, registry_source="registry:original"
    )
    if not active:
        with store._connect() as db:
            db.execute("update business_anchors set active=0 where id=?", (anchor_id,))
    return store.get_business_project(project_id)


@pytest.mark.parametrize("source_type", ["project_weekly_report", "ai_minutes"])
def test_current_source_reuses_exact_title_legacy_project_and_links_changed_task(
    tmp_path, source_type
):
    store = AutoReplyStore(tmp_path / "source.sqlite3")
    project = seed_project(store)
    task_id = store.create_business_task(
        title="复核示例创智回款计划", stage="candidate", description="此前待完善"
    )
    item, decision = source_registration(source_type, task_id, with_attention=True)
    anchors_before = store.list_business_anchors()
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert store.list_business_projects() == [project]
    assert store.list_business_anchors() == anchors_before
    assert result.project_links == ((task_id, project.canonical_anchor_id),)
    (link,) = store.list_business_task_anchor_links(task_id=task_id)
    assert (
        link.anchor_id == project.canonical_anchor_id
        and link.status.value == "confirmed"
    )
    assert (
        store.get_business_task(task_id).description
        == decision.task_decisions[0].description
    )
    assert store.get_business_task(task_id).stage.value == "candidate"
    (card,) = store.list_business_attention_items()
    assert card.anchor_id == project.canonical_anchor_id
    assert card.stable_key == f"project:{project.canonical_anchor_id}"
    assert result.projection_receipt.status == "completed"
    (member,) = store.list_business_attention_tasks(card.id)
    assert member.task_id == task_id
    events_before = store.list_business_task_events(task_id)
    attention_events_before = store.list_business_attention_events(card.id)
    apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert store.list_business_projects() == [project]
    assert store.list_business_anchors() == anchors_before
    assert store.list_business_task_events(task_id) == events_before
    assert store.list_business_attention_items() == (card,)
    assert store.list_business_attention_events(card.id) == attention_events_before


@pytest.mark.parametrize("source_type", ["project_weekly_report", "ai_minutes"])
def test_current_source_does_not_reuse_a_shorter_project_name(tmp_path, source_type):
    store = AutoReplyStore(tmp_path / "prefix.sqlite3")
    old = seed_project(store, title="示例")
    task_id = store.create_business_task(
        title="复核示例创智回款计划", stage="candidate"
    )
    item, decision = source_registration(source_type, task_id)
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    projects = store.list_business_projects()
    assert old in projects
    (current,) = [project for project in projects if project.title == "示例创智"]
    assert current.canonical_anchor_id != old.canonical_anchor_id
    assert result.project_links == ((task_id, current.canonical_anchor_id),)
    assert (
        current.registry_source
        == f"{decision.project_decisions[0].registration.authority}:{item.source.ref}"
    )


@pytest.mark.parametrize("source_type", ["project_weekly_report", "ai_minutes"])
def test_multiple_active_exact_title_projects_are_an_identity_conflict(
    tmp_path, source_type
):
    store = AutoReplyStore(tmp_path / "ambiguous.sqlite3")
    seed_project(store, ref="legacy:first")
    seed_project(store, ref="legacy:second")
    task_id = store.create_business_task(
        title="复核示例创智回款计划", stage="candidate", description="此前待完善"
    )
    item, decision = source_registration(source_type, task_id)
    before = (
        store.list_business_projects(),
        store.list_business_anchors(),
        store.get_business_task(task_id),
    )
    with pytest.raises(ValueError, match="Project identity conflict"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=item,
            decision=decision,
            record_run=False,
        )
    assert (
        store.list_business_projects(),
        store.list_business_anchors(),
        store.get_business_task(task_id),
    ) == before
    assert not store.list_business_task_anchor_links(task_id=task_id)


@pytest.mark.parametrize("source_type", ["project_weekly_report", "ai_minutes"])
def test_only_active_official_exact_title_project_is_reused(tmp_path, source_type):
    store = AutoReplyStore(tmp_path / "active.sqlite3")
    inactive = seed_project(store, ref="legacy:inactive", active=False)
    active = seed_project(store, ref="legacy:active")
    BusinessResolutionService(store).register_anchor(
        anchor_type="project", anchor_ref="unregistered:same-name", title="示例创智"
    )
    task_id = store.create_business_task(
        title="复核示例创智回款计划", stage="candidate"
    )
    item, decision = source_registration(source_type, task_id)
    result = apply_task_agent_decision(
        store, summary_input_id=1, work_item=item, decision=decision, record_run=False
    )
    assert set(store.list_business_projects()) == {inactive, active}
    assert result.project_links == ((task_id, active.canonical_anchor_id),)


def test_project_guidance_resolves_current_authoritative_name_before_existing_link():
    item, _ = source_registration("project_weekly_report", 1)
    text = build_task_agent_prompt(item, "当前已有项目上下文")
    skill = (
        Path(__file__).parents[1] / "ci/shared-skills/ceo-work-tracking/SKILL.md"
    ).read_text()
    for guidance in (text, skill):
        guidance = " ".join(guidance.split())
        assert (
            "Adopt the exact current authoritative Project definition with registration to register or reuse"
            in guidance
        )
        assert (
            "A different stored name cannot replace that definition merely because the action uses its shorter name"
            in guidance
        )
    description = ProjectProposal.model_fields["source_excerpt"].description
    assert "register or reuse" in description
