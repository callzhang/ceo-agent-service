import json
from pathlib import Path

from app.store import AutoReplyStore
from app.action_business_definitions import business_action_projection
from app.provenance_storage import signal_projection
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_semantic_models import AttentionCategory
from app.todo_completion import complete_business_task_from_external_todo
from tests.support.business_task_seed import seed_task


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def test_external_dingtalk_completion_closes_only_the_linked_business_task(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    first = seed_task(store,
        title="客户验收", stage="formal", formal_basis="explicit_assignment",
        commitment_status="accepted", owner_user_id="alex", owner_name="Alex",
    )
    sibling = seed_task(store,
        title="客户合同", stage="formal", formal_basis="explicit_assignment",
        commitment_status="accepted", owner_user_id="alex", owner_name="Alex",
    )
    store.create_business_task_dingtalk_link(
        business_task_id=first, dingtalk_task_id="dt-1", status="active"
    )

    assert complete_business_task_from_external_todo(
        store, business_task_id=first,
        evidence={"source": "dingtalk_todo:dt-1", "reason": "DingTalk Todo marked done"},
    )

    assert store.get_business_task(first).status.value == "done"
    assert store.get_business_task(sibling).status.value == "open"
    with store._connect() as db:
        from app.action_followup_sync_definitions import SYNC_ACTION_PROJECTIONS

        event = db.execute(
            f"select after_json from ({SYNC_ACTION_PROJECTIONS['business_task_events']}) where task_id=?",
            (first,),
        ).fetchone()
        signal = db.execute(
            f"select s.source_ref from ({signal_projection()}) s "
            f"join ({business_action_projection('business_task_evidence')}) e on e.signal_id=s.id "
            "where e.task_id=? and e.evidence_role='completion'", (first,)
        ).fetchone()
    assert 'dingtalk_todo:dt-1' in event["after_json"]
    assert signal["source_ref"] == "dingtalk_todo:dt-1"


def _business_task_for_completion(store, *, title="客户验收", with_follow_up=False):
    task_id = seed_task(store,
        title=title, stage="formal", formal_basis="explicit_assignment",
        owner_user_id="alex", owner_name="Alex", business_relevance="relevant",
    )
    with store.business_task_transaction() as db:
        signal_id = store.create_business_task_signal_in_transaction(
            source_type="reply_attempt", source_ref=f"message:assigned:{task_id}",
            source_time="2026-09-23T09:00:00Z", conversation_id="cid-customer",
            evidence_text=f"Alex 负责{title}，2026-09-24 10:00 检查进展。",
            dedupe_key=f"assignment:{task_id}", _db=db,
        )
        store.link_business_task_evidence_in_transaction(
            task_id=task_id, signal_id=signal_id, evidence_role="assignment", _db=db,
        )
        db.execute(
            "update business set created_at=?, updated_at=? "
            "where record_type='task' and public_id=?",
            ("2026-09-23T09:00:00Z", "2026-09-23T09:00:00Z", task_id),
        )
        if with_follow_up:
            store.create_business_task_follow_up(
                business_task_id=task_id, source_signal_id=signal_id,
                target_conversation_id="cid-customer", target_kind="group",
                question_text="请确认验收进展", scheduled_at="2026-09-24T10:00:00Z",
                owner_user_id="alex", owner_name="Alex",
                dedupe_key=f"check:{task_id}", _db=db,
            )
    return task_id, signal_id


def test_business_task_completion_recomputes_attention_membership(tmp_path):
    store = _store(tmp_path)
    task_id, signal_id = _business_task_for_completion(store)
    sibling_id, _ = _business_task_for_completion(store, title="客户合同")
    with store.business_task_transaction() as db:
        anchor_id = store.create_business_anchor_in_transaction(
            anchor_type="project", anchor_ref="project:1", title="客户交付项目", _db=db,
        )
        project_id = store.create_business_project_in_transaction(
            canonical_anchor_id=anchor_id, title="客户交付项目", registry_source="meeting:delivery", _db=db,
        )
        from app.project_context_service import ProjectContextService

        ProjectContextService(store).apply(project_id=project_id, context=None, signal_ids=(signal_id,), db=db)
        for linked_task_id in (task_id, sibling_id):
            store.create_business_task_anchor_link_in_transaction(
                task_id=linked_task_id, anchor_id=anchor_id, status="confirmed",
                active=True, evidence_signal_id=signal_id, _db=db,
            )
    attention_id = BusinessAttentionProjection(store).upsert(AttentionProposal(
        stable_key="customer:1:delivery", category=AttentionCategory.PUSH,
        title="客户交付需关注", business_area="交付", why_attention="客户等待交付",
        current_state="推进中", ceo_action="确认交付安排", anchor_id=anchor_id,
        task_ids=(task_id, sibling_id), evidence_signal_id=signal_id,
    ))
    evidence = {
        "source": "dingtalk_todo:dt-task-1",
        "reason": "负责人确认已交付", "description": "客户验收材料已提交。",
        "completed_at": "2026-09-24T09:00:00Z", "checked_at": "2026-09-24T10:00:00Z",
    }
    store.create_business_task_dingtalk_link(
        business_task_id=task_id, dingtalk_task_id="dt-task-1", status="active",
    )
    assert complete_business_task_from_external_todo(
        store, business_task_id=task_id, evidence=evidence,
    )

    assert store.get_business_task(task_id).status.value == "done"
    assert [link.task_id for link in store.list_business_attention_tasks(attention_id)] == [sibling_id]
    events = store.list_business_attention_events(attention_id)
    assert json.loads(events[-1].after_json)["task_ids"] == [sibling_id]
    assert store.get_business_attention_item(attention_id).status.value == "active"
    assert store.get_business_attention_item(attention_id).category is AttentionCategory.PUSH
    assert store.get_business_attention_item(attention_id).current_state == "推进中"


def test_store_completes_business_task_follow_ups_in_caller_transaction(tmp_path):
    store = _store(tmp_path)
    task_id, _ = _business_task_for_completion(store, with_follow_up=True)
    before = store.list_business_task_follow_ups(business_task_id=task_id)
    assert len(before) == 1
    evidence_json = json.dumps({"reason": "owner confirmed"}, ensure_ascii=False)

    with store.business_task_transaction() as db:
        changed = store.complete_business_task_follow_ups(
            business_task_id=task_id,
            evidence_check_json=evidence_json,
            suppressed_reason="owner confirmed",
            _db=db,
        )

    assert changed == 1
    [follow_up] = store.list_business_task_follow_ups(
        business_task_id=task_id, statuses=("completed",)
    )
    assert follow_up["evidence_check_json"] == evidence_json
    assert follow_up["suppressed_reason"] == "owner confirmed"


def test_business_task_link_list_can_reuse_caller_transaction(tmp_path):
    store = _store(tmp_path)
    task_id, _ = _business_task_for_completion(store)
    link_id = store.create_business_task_dingtalk_link(
        business_task_id=task_id, dingtalk_task_id="dt-1", status="active"
    )

    with store.business_task_transaction() as db:
        links = store.list_business_task_dingtalk_links(
            business_task_id=task_id,
            statuses=("active",),
            _db=db,
        )

    assert [link["id"] for link in links] == [link_id]


def test_business_task_link_lookup_by_provider_task_returns_newest_match(tmp_path):
    store = _store(tmp_path)
    task_id, _ = _business_task_for_completion(store)
    old_link_id = store.create_business_task_dingtalk_link(
        business_task_id=task_id, dingtalk_task_id="dt-duplicate", status="done"
    )
    new_link_id = store.create_business_task_dingtalk_link(
        business_task_id=task_id, dingtalk_task_id="dt-duplicate", status="active"
    )

    with store.business_task_transaction() as db:
        link = store.get_business_task_dingtalk_link_for_provider_task(
            business_task_id=task_id,
            dingtalk_task_id="dt-duplicate",
            statuses=("active", "done"),
            _db=db,
        )

    assert link["id"] == new_link_id
    assert link["id"] != old_link_id


def test_external_todo_completion_rejects_unlinked_task(tmp_path):
    store = _store(tmp_path)
    task_id = seed_task(store, title="Unlinked", stage="formal", formal_basis="explicit_assignment", owner_user_id="alex", owner_name="Alex")

    assert not complete_business_task_from_external_todo(
        store, business_task_id=task_id,
        evidence={"source": "dingtalk_todo:dt-1", "reason": "done"},
    )
    assert store.get_business_task(task_id).status.value == "open"
