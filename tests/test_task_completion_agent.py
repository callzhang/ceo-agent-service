import json

import pytest

from app.task_completion_agent import (
    TaskCompletionDecision,
    apply_task_completion_decision,
    build_task_completion_prompt,
    process_task_completion_work_item,
    validate_task_completion_decision,
)
from app.store import AutoReplyStore
from app.task_models import WorkItem
from app.task_agent_session import TaskAgentSessionLeaseLost


def test_completion_prompt_guides_read_only_tool_use():
    prompt = build_task_completion_prompt(_work_item("todo_completion_check", {}))

    assert "use connected tools only for read-only discovery" in prompt.lower()
    assert "Do not use CLI, API, or MCP tools to create, update, delete, send, or complete" in prompt
    assert "prompt does not technically disable write-capable tools" in prompt
    assert "Prior session turns are background only" in prompt


def _work_item(source_type: str, summary: dict, *, source_ref: str = "completion-check:1") -> WorkItem:
    summary.setdefault("project", {"id": 1, "title": "客户交付"})
    summary.setdefault(
        "search_policy",
        {
            "time_window": {
                "prefer_since": "2026-09-01T00:00:00Z",
                "end": "2026-09-23T10:00:00Z",
            },
            "limits": {"max_tool_calls": 8, "max_sources_to_return": 3},
            "allowed_sources": ["dws_message", "lark_task"],
        },
    )
    if source_type == "follow_up_completion_check" and source_ref == "completion-check:1":
        follow_up = summary.get("follow_up") or {}
        source_ref = f"follow-up-repair:{follow_up.get('id', 1)}:revision"
    elif source_type == "todo_completion_check" and source_ref == "completion-check:1":
        todo = summary.get("todo") or {}
        source_ref = f"todo-completion-check:{todo.get('id', 1)}:2026-09-23"
    return WorkItem.model_validate(
        {
            "source": {
                "type": source_type,
                "ref": source_ref,
                "created_at": "2026-09-23T10:00:00Z",
            },
            "summary": json.dumps(summary),
            "context": {"source_conversation_kind": "direct"},
        }
    )


def _trace(source_ref="dws_message:msg-1", *, source_kind="dws_message"):
    return {
        "source_kind": source_kind,
        "source_ref": source_ref,
        "result": "directly confirms completion",
        "reason": "Current source directly addresses the completion condition.",
        "source_created_at": "2026-09-23T09:30:00Z",
        "retrieved_at": "2026-09-23T10:00:00Z",
    }


def _receipts(decision):
    return [
        {"tool": "dws_search", "call_id": f"call-{i}", "input": trace.source_ref}
        | {"output": trace.source_ref}
        for i, trace in enumerate(decision.search_trace, 1)
    ]


def test_completion_candidate_must_only_close_its_linked_todo():
    work_item = _work_item(
        "todo_completion_evidence_candidate",
        {
            "todo": {"id": 31},
            "evidence_candidate": {"id": 8, "source_ref": "dws_message:msg-1"},
        },
        source_ref="todo-evidence:8",
    )
    decision = TaskCompletionDecision.model_validate(
        {
            "todo_changes": [
                {
                    "todo_id": 31,
                    "action": "close",
                    "completion_evidence": {
                    "source": "dws_message:msg-1",
                        "reason": "Alex confirmed the ETA was sent.",
                        "description": "The message explicitly confirms the deliverable.",
                        "completed_at": "2026-09-23T09:30:00Z",
                        "checked_at": "1900-01-01T00:00:00Z",
                    },
                }
            ],
            "search_trace": [_trace()],
            "update_summary": "Current message directly confirms completion.",
        }
    )

    validate_task_completion_decision(
        work_item, decision, audit_tool_events=_receipts(decision)
    )

    decision.todo_changes[0].todo_id = 32
    with pytest.raises(ValueError, match="linked TODO"):
        validate_task_completion_decision(
            work_item, decision, audit_tool_events=_receipts(decision)
        )


def test_business_task_completion_check_accepts_only_its_linked_task():
    work_item = _work_item(
        "todo_completion_check", {"business_task": {"id": 42}},
        source_ref="business-task-completion-check:42:2026-09-23",
    )
    decision = TaskCompletionDecision.model_validate({
        "todo_changes": [{"business_task_id": 42, "action": "close",
            "completion_evidence": {"source": "dws_message:msg-1",
                "reason": "Owner confirmed delivery", "description": "The task is done.",
                "completed_at": "2026-09-23T09:30:00Z", "checked_at": "2026-09-23T10:00:00Z"}}],
        "search_trace": [_trace()], "update_summary": "Task is done.",
    })

    validate_task_completion_decision(
        work_item, decision, audit_tool_events=_receipts(decision)
    )
    decision.todo_changes[0].business_task_id = 43
    with pytest.raises(ValueError, match="linked Task"):
        validate_task_completion_decision(
            work_item, decision, audit_tool_events=_receipts(decision)
        )


def test_business_task_completion_operation_closes_task_and_queues_external_sync(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id = store.create_business_task(
        title="交付报价", stage="formal", formal_basis="explicit_assignment",
        owner_user_id="alex", owner_name="Alex", commitment_status="accepted",
    )
    store.create_business_task_dingtalk_link(
        business_task_id=task_id, dingtalk_task_id="dt-task-1", status="active"
    )
    item = _work_item(
        "todo_completion_check", {"business_task": {"id": task_id, "title": "交付报价"}},
        source_ref=f"business-task-completion-check:{task_id}:2026-09-23",
    )
    input_id = store.enqueue_work_summary_input(
        source_type=item.source.type.value, source_ref=item.source.ref,
        payload_json=item.model_dump_json(),
    )
    decision = TaskCompletionDecision.model_validate({
        "todo_changes": [{"business_task_id": task_id, "action": "close",
            "completion_evidence": {"source": "dws_message:msg-1",
                "reason": "Owner confirmed delivery", "description": "The quote was delivered.",
                "completed_at": "2026-09-23T09:30:00Z", "checked_at": "2026-09-23T10:00:00Z"}}],
        "search_trace": [_trace()], "update_summary": "Task is done.",
    })

    changed = apply_task_completion_decision(
        store, summary_input_id=input_id, work_item=item, decision=decision,
        now="2026-09-23T10:00:00Z", sync_external_todo=True,
        audit_tool_events=_receipts(decision),
    )

    assert changed
    assert store.get_business_task(task_id).status.value == "done"
    [intent] = store.list_business_task_todo_sync_outbox()
    assert intent["business_task_id"] == task_id
    assert intent["operation"] == "complete"


def test_business_task_follow_up_repair_reschedules_only_linked_draft(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id = store.create_business_task(
        title="交付报价", stage="formal", formal_basis="explicit_assignment",
        owner_user_id="alex", owner_name="Alex",
    )
    with store.business_task_transaction() as db:
        signal_id = store.create_business_task_signal_in_transaction(
            source_type="reply_attempt", source_ref="message:1", dedupe_key="message:1",
            evidence_text="Alex owns quote", conversation_id="cid-1", _db=db,
        )
        store.link_business_task_evidence_in_transaction(
            task_id=task_id, signal_id=signal_id, evidence_role="assignment", _db=db,
        )
        draft_id = store.create_business_task_follow_up(
            business_task_id=task_id, source_signal_id=signal_id,
            target_conversation_id="cid-1", target_kind="group",
            question_text="请确认报价进度", scheduled_at="2026-09-23T09:00:00Z",
            owner_user_id="alex", owner_name="Alex", dedupe_key="draft:1", _db=db,
        )
        sibling_id = store.create_business_task_follow_up(
            business_task_id=task_id, source_signal_id=signal_id,
            target_conversation_id="cid-1", target_kind="group",
            question_text="请确认合同进度", scheduled_at="2026-09-23T09:00:00Z",
            owner_user_id="alex", owner_name="Alex", dedupe_key="draft:2", _db=db,
        )
    item = _work_item(
        "follow_up_completion_check",
        {"business_task": {"id": task_id}, "follow_up": {"id": draft_id}},
        source_ref=f"business-task-follow-up-repair:{draft_id}:1",
    )
    input_id = store.enqueue_work_summary_input(
        source_type=item.source.type.value, source_ref=item.source.ref,
        payload_json=item.model_dump_json(),
    )
    decision = TaskCompletionDecision.model_validate({
        "follow_up_changes": [{"follow_up_id": draft_id, "action": "reschedule",
            "next_due_at": "2026-09-24T01:00:00Z", "reason": "Need a fresh check"}],
        "search_trace": [_trace()], "update_summary": "Reschedule current follow-up.",
    })

    assert apply_task_completion_decision(
        store, summary_input_id=input_id, work_item=item, decision=decision,
        now="2026-09-23T10:00:00Z", audit_tool_events=_receipts(decision),
    )
    drafts = {row["id"]: row for row in store.list_business_task_follow_ups(business_task_id=task_id)}
    assert drafts[draft_id]["scheduled_at"] == "2026-09-24T01:00:00Z"
    assert drafts[draft_id]["revision"] == 2
    assert drafts[sibling_id]["revision"] == 1
    assert store.list_business_task_follow_ups(
        business_task_id=task_id, limit=1, offset=1
    )[0]["id"] == draft_id


def test_stale_business_task_follow_up_repair_cannot_change_newer_revision(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    task_id = store.create_business_task(
        title="交付报价", stage="formal", formal_basis="explicit_assignment",
        owner_user_id="alex", owner_name="Alex",
    )
    with store.business_task_transaction() as db:
        signal_id = store.create_business_task_signal_in_transaction(
            source_type="reply_attempt", source_ref="message:1", dedupe_key="message:1",
            evidence_text="Alex owns quote", conversation_id="cid-1", _db=db,
        )
        store.link_business_task_evidence_in_transaction(
            task_id=task_id, signal_id=signal_id, evidence_role="assignment", _db=db,
        )
        draft_id = store.create_business_task_follow_up(
            business_task_id=task_id, source_signal_id=signal_id,
            target_conversation_id="cid-1", target_kind="group",
            question_text="请确认报价进度", scheduled_at="2026-09-23T09:00:00Z",
            owner_user_id="alex", owner_name="Alex", dedupe_key="draft:stale", _db=db,
        )
        db.execute(
            "update business_task_follow_ups set revision=2, scheduled_at=? where id=?",
            ("2026-09-25T09:00:00Z", draft_id),
        )
    item = _work_item(
        "follow_up_completion_check",
        {"business_task": {"id": task_id}, "follow_up": {"id": draft_id}},
        source_ref=f"business-task-follow-up-repair:{draft_id}:1",
    )
    input_id = store.enqueue_work_summary_input(
        source_type=item.source.type.value, source_ref=item.source.ref,
        payload_json=item.model_dump_json(),
    )
    decision = TaskCompletionDecision.model_validate({
        "follow_up_changes": [{"follow_up_id": draft_id, "action": "close",
            "reason": "The original failed send is resolved.",
            "evidence_check": {"source": "dws_message:msg-1", "reason": "Owner confirmed", "description": "The follow-up is no longer needed.", "checked_at": "2026-09-23T10:00:00Z"}}],
        "search_trace": [_trace()], "update_summary": "The old repair must not close the newer revision.",
    })

    with pytest.raises(ValueError, match="revision"):
        apply_task_completion_decision(
            store, summary_input_id=input_id, work_item=item, decision=decision,
            now="2026-09-23T10:00:00Z", audit_tool_events=_receipts(decision),
        )

    [draft] = store.list_business_task_follow_ups(business_task_id=task_id)
    assert draft["status"] == "draft"
    assert draft["revision"] == 2
    assert draft["scheduled_at"] == "2026-09-25T09:00:00Z"


def test_completion_check_rejects_follow_up_operation_for_unlinked_draft():
    work_item = _work_item(
        "todo_completion_check",
        {"todo": {"id": 31}, "follow_ups": [{"id": 44}]},
    )
    decision = TaskCompletionDecision.model_validate(
        {
            "follow_up_changes": [
                {
                    "follow_up_id": 45,
                    "action": "close",
                    "reason": "No longer needed.",
                }
            ],
            "search_trace": [_trace()],
            "update_summary": "The source does not support closing the TODO.",
        }
    )

    with pytest.raises(ValueError, match="linked follow-up"):
        validate_task_completion_decision(work_item, decision)


def test_follow_up_completion_check_rejects_todo_mutation():
    work_item = _work_item(
        "follow_up_completion_check",
        {"todo": {"id": 31}, "follow_up": {"id": 44}},
    )
    decision = TaskCompletionDecision.model_validate(
        {
            "todo_changes": [
                {
                        "todo_id": 31,
                        "action": "close",
                        "completion_evidence": {"source": "test"},
                    }
            ]
        }
    )

    with pytest.raises(ValueError, match="cannot mutate TODO"):
        validate_task_completion_decision(work_item, decision)


def test_unified_task_agent_decision_accepts_completion_fields():
    decision = TaskCompletionDecision.model_validate(
        {
            "task_decisions": [],
            "todo_changes": [
                {
                    "action": "close",
                    "todo_id": 31,
                    "completion_evidence": {
                        "source": "message:done",
                        "reason": "The owner confirmed delivery.",
                        "description": "The exact deliverable was submitted.",
                        "completed_at": "2026-09-23T10:00:00+08:00",
                        "checked_at": "2026-09-23T10:05:00+08:00",
                    },
                }
            ],
            "search_trace": [_trace("message:done")],
        }
    )

    assert decision.task_decisions == []
    assert decision.todo_changes[0].todo_id == 31


def test_completion_trace_requires_allowed_source_and_source_bound_runtime_receipt():
    item = _work_item("todo_completion_check", {"todo": {"id": 31}})
    decision = TaskCompletionDecision.model_validate(
        {
            "search_trace": [_trace("dws_message:msg-1", source_kind="web_search")],
            "update_summary": "Checked current evidence.",
        }
    )
    with pytest.raises(ValueError, match="outside search_policy"):
        validate_task_completion_decision(
            item, decision, audit_tool_events=_receipts(decision)
        )
    decision.search_trace[0].source_kind = "dws_message"
    with pytest.raises(ValueError, match="cannot be matched"):
        validate_task_completion_decision(
            item,
            decision,
            audit_tool_events=[{"tool": "dws_search", "call_id": "real-call", "output": "other-ref"}],
        )

    with pytest.raises(ValueError, match="cannot be matched"):
        validate_task_completion_decision(
            item,
            decision,
            audit_tool_events=[
                {"tool": "dws_search", "call_id": "input-only", "input": "dws_message:msg-1"}
            ],
        )


def test_memory_recall_trace_cannot_support_todo_completion():
    summary = {
        "todo": {"id": 31},
        "project": {"id": 1},
        "search_policy": {
            "time_window": {
                "prefer_since": "2026-09-01T00:00:00Z",
                "end": "2026-09-23T10:00:00Z",
            },
            "limits": {"max_tool_calls": 8, "max_sources_to_return": 3},
            "allowed_sources": ["memory_recall_for_background_only"],
        },
    }
    item = _work_item("todo_completion_check", summary)
    decision = TaskCompletionDecision.model_validate(
        {
            "todo_changes": [
                {
                    "todo_id": 31,
                    "action": "close",
                    "completion_evidence": {
                        "source": "memory:fact-1",
                        "reason": "Memory recalled a prior completion claim.",
                        "description": "Historical context only.",
                        "completed_at": "2026-09-23T09:30:00Z",
                        "checked_at": "2026-09-23T10:00:00Z",
                    },
                }
            ],
            "search_trace": [
                {
                    "source_kind": "memory_recall_for_background_only",
                    "source_ref": "memory:fact-1",
                    "result": "Prior completion claim found.",
                    "reason": "Memory recall is background only.",
                    "source_created_at": "2026-09-23T09:30:00Z",
                }
            ],
            "update_summary": "Current completion evidence was not found.",
        }
    )

    with pytest.raises(ValueError, match="memory_recall cannot be completion evidence"):
        validate_task_completion_decision(
            item,
            decision,
            now="2026-09-23T10:00:00Z",
            audit_tool_events=[
                {"tool": "memory_recall", "call_id": "memory-1", "output": "memory:fact-1"}
            ],
        )

def test_todo_completion_check_closes_linked_todo_and_finishes_run_atomically(tmp_path):
    store = AutoReplyStore(tmp_path / "completion.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="conversation-1",
        target_kind="direct",
        question_text="请确认 ETA 是否已同步。",
        scheduled_at="2026-09-24 10:00:00",
        status="sent",
        sent_at="2026-09-22 10:00:00",
    )
    item = _work_item(
        "todo_completion_check",
        {
            "todo": {"id": todo_id},
            "follow_ups": [{"id": follow_up_id}],
            "evidence_context": "Owner explicitly assigned Alex to synchronize acceptance ETA.",
        },
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    decision = TaskCompletionDecision.model_validate(
        {
            "task_decisions": [
                {
                    "action": "record_candidate",
                    "transition": "none",
                    "source_excerpt": "Owner explicitly assigned Alex to synchronize acceptance ETA.",
                    "source_ref": item.source.ref,
                    "title": "Synchronize acceptance ETA",
                    "missing_evidence": ["formal assignment"],
                }
            ],
            "todo_changes": [
                {
                    "todo_id": todo_id,
                    "action": "close",
                    "completion_evidence": {
                        "source": "dws_message:msg-1",
                        "reason": "Alex confirmed the ETA was sent.",
                        "description": "The message explicitly confirms the deliverable.",
                        "completed_at": "2026-09-23T09:30:00Z",
                        "checked_at": "2026-09-23T10:00:00Z",
                    },
                }
            ],
            "search_trace": [_trace()],
            "update_summary": "Current message directly confirms completion.",
        }
    )

    class CompletionRunner:
        last_session_id = "completion-session"

        def __init__(self):
            self.calls = []

        def decide(self, **kwargs):
            self.calls.append(kwargs)
            return decision

        last_audit_tool_events = _receipts(decision)

    runner = CompletionRunner()
    process_task_completion_work_item(
        store,
        runner,
        work_input,
        now="2026-09-23T10:00:00Z",
        dws=object(),
    )

    completed_todo = store.get_work_todo(todo_id)
    assert completed_todo.status == "done"
    assert json.loads(completed_todo.completion_evidence_json)["search_trace"][0][
        "source_ref"
    ] == "dws_message:msg-1"
    assert json.loads(completed_todo.completion_evidence_json)["checked_at"] == (
        "2026-09-23T10:00:00Z"
    )
    assert store.get_follow_up_draft(follow_up_id).status == "completed"
    assert len(store.list_business_tasks()) == 1
    assert store.get_work_summary_input(input_id).status.value == "done"
    assert runner.calls[0]["session_scope_id"] == "task-agent:work-tracking:v1"
    with store._connect() as db:
        runs = db.execute(
            "select status, audit_summary from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchall()
        syncs = db.execute(
            "select operation from task_todo_sync_outbox where operation_key like ?",
            (f"task-agent:{input_id}:todo:{todo_id}:%",),
        ).fetchall()
    assert runs[0]["status"] == "completed"
    assert "search_trace" in runs[0]["audit_summary"]
    assert [row["operation"] for row in syncs] == ["complete"]


def test_completion_handler_does_not_close_todo_after_session_lease_loss(tmp_path):
    store = AutoReplyStore(tmp_path / "completion-session-lease-lost.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
    )
    item = _work_item("todo_completion_check", {"todo": {"id": todo_id}})
    input_id = store.enqueue_work_summary_input(
        item.source.type.value,
        item.source.ref,
        item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    decision = TaskCompletionDecision.model_validate(
        {
            "task_decisions": [],
            "todo_changes": [
                {
                    "todo_id": todo_id,
                    "action": "close",
                    "completion_evidence": {
                        "source": "dws_message:msg-1",
                        "reason": "Alex confirmed the ETA was sent.",
                        "description": "The message explicitly confirms the deliverable.",
                        "completed_at": "2026-09-23T09:30:00Z",
                        "checked_at": "2026-09-23T10:00:00Z",
                    },
                }
            ],
            "search_trace": [_trace()],
            "update_summary": "Current message directly confirms completion.",
        }
    )

    class Runner:
        last_session_id = "completion-session"
        last_audit_tool_events = _receipts(decision)

        def decide(self, **kwargs):
            return decision

    class LeaseLostBeforeApply:
        calls = 0

        def assert_owned(self):
            self.calls += 1
            if self.calls == 2:
                raise TaskAgentSessionLeaseLost("session lease expired")

    with pytest.raises(TaskAgentSessionLeaseLost):
        process_task_completion_work_item(
            store,
            Runner(),
            work_input,
            now="2026-09-23T10:00:00Z",
            session_lease=LeaseLostBeforeApply(),
        )

    assert store.get_work_todo(todo_id).status == "open"
    assert store.get_work_summary_input(input_id).status.value == "failed"
    with store._connect() as db:
        run = db.execute(
            "select status from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchone()
        syncs = db.execute(
            "select count(*) from task_todo_sync_outbox where operation_key like ?",
            (f"task-agent:{input_id}:todo:{todo_id}:%",),
        ).fetchone()[0]
    assert run["status"] == "failed"
    assert syncs == 0


def test_completion_candidate_must_match_persisted_candidate_and_updates_its_status(tmp_path):
    store = AutoReplyStore(tmp_path / "candidate-completion.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
    )
    candidate = store.upsert_todo_evidence_candidate(
        project_id=project_id,
        todo_id=todo_id,
        source_type="dws_message",
        source_ref="dws_message:msg-candidate",
        evidence_text="Alex confirmed the ETA was sent.",
        reason="Potential direct completion statement.",
        confidence=0.9,
    )
    item = _work_item(
        "todo_completion_evidence_candidate",
        {
            "todo": {"id": todo_id},
            "evidence_candidate": {
                "id": candidate.id,
                "source_ref": candidate.source_ref,
                "source_type": candidate.source_type,
            },
        },
        source_ref=f"todo-evidence:{candidate.id}",
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    store.mark_todo_evidence_candidate_enqueued(candidate.id, input_id)
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    decision = TaskCompletionDecision.model_validate(
        {
            "todo_changes": [
                {
                    "todo_id": todo_id,
                    "action": "close",
                    "completion_evidence": {
                        "source": candidate.source_ref,
                        "reason": "Alex explicitly confirmed the requested ETA was sent.",
                        "description": "The message is tied to the exact completion condition.",
                        "completed_at": "2026-09-23T09:30:00Z",
                        "checked_at": "2026-09-23T10:00:00Z",
                    },
                }
            ],
            "search_trace": [
                {
                    **_trace(candidate.source_ref, source_kind=candidate.source_type),
                }
            ],
            "update_summary": "The candidate message directly confirms completion.",
        }
    )

    class CompletionRunner:
        def decide(self, **kwargs):
            return decision

        last_audit_tool_events = _receipts(decision)

    process_task_completion_work_item(store, CompletionRunner(), work_input)

    updated = store.get_todo_evidence_candidate(candidate.id)
    assert updated.status.value == "accepted"
    assert store.get_work_todo(todo_id).status == "done"
    assert store.get_work_summary_input(input_id).status.value == "done"


def test_no_completion_found_persists_search_trace_without_closing_todo(tmp_path):
    store = AutoReplyStore(tmp_path / "no-completion.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
    )
    item = _work_item("todo_completion_check", {"todo": {"id": todo_id}})
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    decision = TaskCompletionDecision.model_validate(
        {
            "search_trace": [_trace()],
            "update_summary": "No current source confirms the ETA was sent; TODO remains open.",
        }
    )

    class CompletionRunner:
        def decide(self, **kwargs):
            return decision

        last_audit_tool_events = _receipts(decision)

    process_task_completion_work_item(store, CompletionRunner(), work_input)

    assert store.get_work_todo(todo_id).status == "open"
    assert store.get_work_summary_input(input_id).status.value == "skipped"
    with store._connect() as db:
        run = db.execute(
            "select decision_json, audit_summary from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchone()
    assert json.loads(run["decision_json"])["search_trace"][0]["source_kind"] == "dws_message"
    assert "search_trace" in run["audit_summary"]


def test_invalid_typed_completion_result_fails_run_and_leaves_todo_open(tmp_path):
    store = AutoReplyStore(tmp_path / "invalid-completion.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="conversation-1",
        target_kind="direct",
        question_text="请确认 ETA 是否已同步。",
        scheduled_at="2026-09-24 10:00:00",
        status="sent",
        sent_at="2026-09-22 10:00:00",
    )
    item = _work_item(
        "follow_up_completion_check",
        {"todo": {"id": todo_id}, "follow_up": {"id": follow_up_id}},
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    invalid_decision = TaskCompletionDecision.model_validate(
        {
            "follow_up_changes": [
                {
                    "follow_up_id": follow_up_id + 1,
                    "action": "close",
                    "reason": "No longer needed.",
                }
            ]
        }
    )

    class CompletionRunner:
        def decide(self, **kwargs):
            return invalid_decision

    with pytest.raises(ValueError, match="unlinked follow-up"):
        process_task_completion_work_item(
            store,
            CompletionRunner(),
            work_input,
            now="2026-09-23T10:00:00Z",
        )

    assert store.get_work_todo(todo_id).status == "open"
    assert store.get_follow_up_draft(follow_up_id).status == "sent"
    assert store.get_work_summary_input(input_id).status.value == "failed"
    with store._connect() as db:
        runs = db.execute(
            "select status from task_agent_runs where summary_input_id=?",
            (input_id,),
        ).fetchall()
    assert runs[0]["status"] == "failed"


def test_follow_up_reassignment_requires_owner_identity_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "follow-up-owner-evidence.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="conversation-1",
        target_kind="direct",
        question_text="请确认 ETA 是否已同步。",
        scheduled_at="2026-09-24 10:00:00",
        status="sent",
    )
    item = _work_item(
        "follow_up_completion_check",
        {"todo": {"id": todo_id}, "follow_up": {"id": follow_up_id}},
    )
    decision = TaskCompletionDecision.model_validate(
        {
            "follow_up_changes": [
                {
                    "follow_up_id": follow_up_id,
                    "todo_id": todo_id,
                    "action": "reassign",
                    "reason": "Source requests a handoff.",
                    "owner_user_id": "owner-2",
                    "owner_name": "Blair",
                    "owner_evidence": {
                        "source": "dws_message:msg-2",
                        "reason": "The owner change is explicit.",
                        "description": "Mina named Blair as the new owner.",
                    },
                }
            ],
            "search_trace": [_trace("dws_message:msg-2")],
        }
    )

    store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]

    class CompletionRunner:
        def decide(self, **kwargs):
            return decision

        last_audit_tool_events = _receipts(decision)

    with pytest.raises(ValueError, match="does not support assigned identity"):
        process_task_completion_work_item(
            store,
            CompletionRunner(),
            work_input,
            now="2026-09-23T10:00:00Z",
        )

    draft = store.get_follow_up_draft(follow_up_id)
    assert draft.owner_user_id == "owner-1"
    assert draft.owner_name == "Alex"


def test_completion_candidate_without_close_is_rejected_with_run_skipped(tmp_path):
    store = AutoReplyStore(tmp_path / "candidate-rejected.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = store.create_work_todo(project_id=project_id, title="同步验收 ETA", status="open")
    candidate = store.upsert_todo_evidence_candidate(
        project_id=project_id,
        todo_id=todo_id,
        source_type="dws_message",
        source_ref="dws_message:msg-rejected",
        evidence_text="We will get to it later.",
        reason="Weak completion signal.",
        confidence=0.2,
    )
    item = _work_item(
        "todo_completion_evidence_candidate",
        {
            "todo": {"id": todo_id},
            "evidence_candidate": {
                "id": candidate.id,
                "source_ref": candidate.source_ref,
                "source_type": candidate.source_type,
            },
        },
        source_ref=f"todo-evidence:{candidate.id}",
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    store.mark_todo_evidence_candidate_enqueued(candidate.id, input_id)
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    decision = TaskCompletionDecision.model_validate(
        {
            "search_trace": [_trace(candidate.source_ref)],
            "update_summary": "The candidate does not establish completion; the TODO remains open.",
        }
    )

    class CompletionRunner:
        def decide(self, **kwargs):
            return decision

        last_audit_tool_events = _receipts(decision)

    process_task_completion_work_item(store, CompletionRunner(), work_input)
    assert store.get_todo_evidence_candidate(candidate.id).status.value == "rejected"
    assert store.get_work_todo(todo_id).status == "open"
    assert store.get_work_summary_input(input_id).status.value == "skipped"


def test_completion_candidate_validation_error_marks_candidate_error(tmp_path):
    store = AutoReplyStore(tmp_path / "candidate-error.sqlite3")
    project_id = store.create_work_project(title="客户交付")
    todo_id = store.create_work_todo(project_id=project_id, title="同步验收 ETA", status="open")
    candidate = store.upsert_todo_evidence_candidate(
        project_id=project_id,
        todo_id=todo_id,
        source_type="dws_message",
        source_ref="dws_message:msg-error",
        evidence_text="The unrelated project is complete.",
        reason="Potential signal.",
        confidence=0.7,
    )
    item = _work_item(
        "todo_completion_evidence_candidate",
        {
            "todo": {"id": todo_id},
            "evidence_candidate": {
                "id": candidate.id + 1,
                "source_ref": candidate.source_ref,
                "source_type": candidate.source_type,
            },
        },
        source_ref=f"todo-evidence:{candidate.id + 1}",
    )
    input_id = store.enqueue_work_summary_input(
        item.source.type.value, item.source.ref, item.model_dump_json()
    )
    store.mark_todo_evidence_candidate_enqueued(candidate.id, input_id)
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    decision = TaskCompletionDecision.model_validate(
        {
            "search_trace": [_trace(candidate.source_ref)],
            "update_summary": "Candidate source identity was inconsistent.",
        }
    )

    class CompletionRunner:
        def decide(self, **kwargs):
            return decision

        last_audit_tool_events = _receipts(decision)

    with pytest.raises(ValueError, match="candidate identity"):
        process_task_completion_work_item(store, CompletionRunner(), work_input)
    assert store.get_todo_evidence_candidate(candidate.id).status.value == "error"
    assert store.get_work_summary_input(input_id).status.value == "failed"
