import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.store import AutoReplyStore
from app.task_attention_projection import AttentionProposal, BusinessAttentionProjection
from app.task_models import WorkItem
from app.task_semantic_models import AttentionCategory
from app.todo_completion import complete_business_task_from_external_todo
from app.todo_completion import (
    close_todo_with_completion_evidence,
)
from app.task_completion_agent import TaskCompletionDecision, process_task_completion_work_item


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def _project_todo_follow_up(store: AutoReplyStore):
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        description="客户需要验收 ETA 和下一步安排。",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="基于客户交付项目，请确认验收 ETA 是否已同步。",
        scheduled_at="2026-06-27 09:00:00",
        status="sent",
        sent_at="2026-06-27 09:05:00",
        send_result_json=json.dumps({"message_id": "msg-1"}, ensure_ascii=False),
    )
    return project_id, todo_id, follow_up_id


def test_external_dingtalk_completion_closes_only_the_linked_business_task(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    first = store.create_business_task(
        title="客户验收", stage="formal", formal_basis="explicit_assignment",
        commitment_status="accepted", owner_user_id="alex", owner_name="Alex",
    )
    sibling = store.create_business_task(
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
        event = db.execute("select after_json from business_task_events where task_id=?", (first,)).fetchone()
        signal = db.execute(
            "select s.source_ref from business_task_signals s "
            "join business_task_evidence e on e.signal_id=s.id "
            "where e.task_id=? and e.evidence_role='completion'", (first,)
        ).fetchone()
    assert 'dingtalk_todo:dt-1' in event["after_json"]
    assert signal["source_ref"] == "dingtalk_todo:dt-1"


def _business_task_for_completion(store, *, title="客户验收", with_follow_up=False):
    task_id = store.create_business_task(
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
            "update business_tasks set created_at=?, updated_at=? where id=?",
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


@pytest.mark.parametrize("completion_source", ["dingtalk", "task_agent"])
def test_business_task_completion_recomputes_attention_membership(tmp_path, completion_source):
    store = _store(tmp_path)
    task_id, signal_id = _business_task_for_completion(store)
    sibling_id, _ = _business_task_for_completion(store, title="客户合同")
    with store.business_task_transaction() as db:
        anchor_id = store.create_business_anchor_in_transaction(
            anchor_type="customer", anchor_ref="customer:1", title="客户", _db=db,
        )
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
        "source": "dingtalk_todo:dt-task-1" if completion_source == "dingtalk" else "dws_message:done-1",
        "reason": "负责人确认已交付", "description": "客户验收材料已提交。",
        "completed_at": "2026-09-24T09:00:00Z", "checked_at": "2026-09-24T10:00:00Z",
    }
    if completion_source == "dingtalk":
        store.create_business_task_dingtalk_link(
            business_task_id=task_id, dingtalk_task_id="dt-task-1", status="active",
        )
        assert complete_business_task_from_external_todo(
            store, business_task_id=task_id, evidence=evidence,
        )
    else:
        item = WorkItem.model_validate({
            "source": {"type": "todo_completion_check", "ref": f"business-task-completion-check:{task_id}:2026-09-24"},
            "context": {"source_conversation_kind": "group"},
            "summary": json.dumps({
                "business_task": {"id": task_id},
                "search_policy": {
                    "time_window": {"prefer_since": "2026-09-23T09:00:00Z", "end": "2026-09-24T10:00:00Z"},
                    "limits": {"max_tool_calls": 8, "max_sources_to_return": 3},
                    "allowed_sources": ["dws_message"],
                },
            }),
        })
        store.enqueue_work_summary_input(item.source.type.value, item.source.ref, item.model_dump_json())
        [work_input] = store.claim_work_summary_inputs(limit=1)
        decision = TaskCompletionDecision.model_validate({
            "todo_changes": [{"action": "close", "business_task_id": task_id, "completion_evidence": evidence}],
            "search_trace": [{
                "source_kind": "dws_message", "source_ref": evidence["source"],
                "source_created_at": evidence["completed_at"],
                "result": "负责人确认已交付", "reason": "原始回复直接确认该交付物完成。",
            }],
            "update_summary": "负责人已完成客户验收交付。",
        })
        runner = SimpleNamespace(
            decide=lambda **kwargs: decision,
            last_audit_tool_events=[{"tool": "dws_message_get", "call_id": "read-1", "output": evidence["source"]}],
        )
        process_task_completion_work_item(store, runner, work_input, now="2026-09-24T10:00:00Z")

    assert store.get_business_task(task_id).status.value == "done"
    assert [link.task_id for link in store.list_business_attention_tasks(attention_id)] == [sibling_id]
    events = store.list_business_attention_events(attention_id)
    assert json.loads(events[-1].after_json)["task_ids"] == [sibling_id]


def test_close_todo_completion_evidence_completes_bound_follow_ups(tmp_path):
    store = _store(tmp_path)
    _, todo_id, follow_up_id = _project_todo_follow_up(store)

    closed = close_todo_with_completion_evidence(
        store,
        todo_id=todo_id,
        evidence={
            "source": "reply_attempt:7",
            "reason": "Alex 明确回复验收 ETA 已同步。",
            "completed_at": "2026-06-27 10:00:00",
        },
        now="2026-06-27 10:00:00",
        source_type="reply_attempt",
        source_ref="7",
        merge_reason="reply_completion_evidence",
    )

    assert closed is True
    todo = store.get_work_todo(todo_id)
    assert todo is not None
    assert todo.status == "done"
    evidence = json.loads(todo.completion_evidence_json)
    assert evidence["source"] == "reply_attempt:7"
    assert evidence["reason"] == "Alex 明确回复验收 ETA 已同步。"
    follow_up = store.get_follow_up_draft(follow_up_id)
    assert follow_up is not None
    assert follow_up.status == "completed"
    assert json.loads(follow_up.send_result_json) == {"message_id": "msg-1"}
    check = json.loads(follow_up.evidence_check_json)
    assert check["source"] == "reply_attempt:7"
    assert check["reason"] == "Alex 明确回复验收 ETA 已同步。"


def test_external_todo_completion_rejects_unlinked_task(tmp_path):
    store = _store(tmp_path)
    task_id = store.create_business_task(title="Unlinked", stage="formal", formal_basis="explicit_assignment", owner_user_id="alex", owner_name="Alex")

    assert not complete_business_task_from_external_todo(
        store, business_task_id=task_id,
        evidence={"source": "dingtalk_todo:dt-1", "reason": "done"},
    )
    assert store.get_business_task(task_id).status.value == "open"
