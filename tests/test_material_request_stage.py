"""A request receipt is not material arrival; fresh inbound facts reopen work."""

import json
from dataclasses import replace

from app.agent_context import AgentTaskContext
from app.agent_orchestrator import AgentOrchestrator
from app.reviewed_sources import capture_candidate_sources
from app.store import AutoReplyStore
from app.system_action_handlers import OaCommentHandler
from app.system_executor import SystemExecutor
from tests.test_reviewed_orchestration import Audit, Consumer, candidate
from tests.test_reviewed_source_revisions import question


class MaterialSource:
    def __init__(self):
        self.material = "Invoice missing"
        self.comments = []

    def read_oa_approval_detail(self, process):
        assert process == "process"
        return {"success": True, "result": {
            "processInstanceId": process,
            "formValueVOS": [{"id": "invoice", "value": self.material}],
        }}

    def comment_oa_approval(self, process, content):
        self.comments.append((process, content))
        return {"success": True, "result": {"commentId": "request-comment"}}


def material_request(context, source):
    result = candidate(continue_after=False)
    action = result.proposal.actions[0].model_copy(update={
        "action_identity": "request-invoice",
        "capability": "dingtalk-oa", "operation": "comment",
        "target": {"process_instance_id": "process"},
        "payload": {"content": "Please attach the invoice before further review."},
    })
    result = result.model_copy(update={
        "proposal": result.proposal.model_copy(update={"actions": (action,)}),
    })
    return capture_candidate_sources(result, context, source)


def inbound(store, message_id, text):
    return store.ensure_reply_task(
        channel="dingtalk", conversation_id="oa-notice",
        conversation_title="Approval", single_chat=True,
        trigger_message_id=message_id, trigger_sender="Applicant",
        trigger_create_time="2026-10-04 00:00:00", trigger_text=text,
        oa_url="https://aflow.dingtalk.com/detail?procInstId=process&taskId=task",
    )


def task_context(task):
    return AgentTaskContext(
        task_id=task.id, channel=task.channel,
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title, single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_create_time=task.trigger_create_time,
        trigger_text=task.trigger_text, messages=(), materials=(), prior_receipts=(),
    )


def test_material_request_ends_stage_then_fresh_inbound_reviews_new_facts_with_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    source = MaterialSource()
    inbound(store, "initial", "Please review my reimbursement; invoice missing")
    task = store.claim_reply_tasks(1)[0]
    context = task_context(task)
    # Supply a viable later question so accidental continuation is observed as
    # a real extra reviewed stage, rather than exhausting a test double.
    consumer = Consumer(
        store, lambda fresh: material_request(fresh, source),
        lambda fresh: question(fresh, source).model_copy(update={
            "stage_index": fresh.stage_index,
            "predecessor_review_id": fresh.predecessor_review_id,
        }),
    )
    audit = Audit(store, "approve", "approve")
    executor = SystemExecutor(
        store, {("dingtalk-oa", "comment"): OaCommentHandler(source)}, dws=source,
    )
    orchestrator = AgentOrchestrator(
        store=store, consumer=consumer, audit=audit, system_executor=executor,
    )

    result = orchestrator.process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    original = store.current_reviewed_candidate(task.id, task.execution_generation)
    assert len(consumer.calls) == len(audit.calls) == 1
    assert source.comments == [
        ("process", "Please attach the invoice before further review."),
    ]
    [receipt] = store.list_verified_candidate_actions(task.id)
    assert receipt["operation"] == "comment"
    assert json.loads(receipt["provider_result_json"])["comment_id"] == "request-comment"

    # Re-entry without an external input must not fabricate the next stage.
    again = orchestrator.process(task, context, refresh_context=lambda: context)
    assert again.status == "executed"
    assert len(consumer.calls) == len(audit.calls) == len(source.comments) == 1
    store.complete_reply_task(task.id, expected_execution_generation=task.execution_generation)

    source.material = "Invoice INV-102 attached; approval policy still requires a choice"
    updated = inbound(store, "invoice-arrived", source.material)
    assert updated.id == task.id
    assert updated.execution_generation != task.execution_generation
    fresh_task = store.claim_reply_tasks(1)[0]
    fresh_context = replace(task_context(fresh_task), trigger_raw_payload={
        "invoice": "INV-102",
    })
    fresh_consumer = Consumer(store, lambda fresh: question(fresh, source))
    fresh_audit = Audit(store, "approve")
    followup = AgentOrchestrator(
        store=store, consumer=fresh_consumer, audit=fresh_audit,
        system_executor=executor,
    ).process(fresh_task, fresh_context, refresh_context=lambda: fresh_context)

    assert followup.status == "needs_human"
    assert len(fresh_consumer.calls) == len(fresh_audit.calls) == 1
    captured = fresh_consumer.calls[0][0]
    assert captured.stage_index == 0 and captured.predecessor_review_id is None
    assert captured.trigger_message_id == "invoice-arrived"
    assert captured.trigger_raw_payload == {"invoice": "INV-102"}
    assert any(r.operation == "comment" and r.completed for r in captured.prior_receipts)
    reviewed = store.current_reviewed_candidate(task.id, fresh_task.execution_generation)
    assert reviewed["id"] != original["id"]
    assert reviewed["candidate_digest"] != original["candidate_digest"]
    bound = fresh_audit.calls[0].candidate.source_bindings
    assert next(b.value for b in bound if b.provider == "dingtalk-oa")["formValueVOS"] == [
        {"id": "invoice", "value": source.material},
    ]
    assert store.get_candidate_execution(original["id"])["status"] == "done"
    assert store.get_review_candidate(original["id"])["candidate_json"] == original["candidate_json"]
    assert len(source.comments) == 1
    assert len(store.list_verified_candidate_actions(task.id)) == 1
    assert [i["trigger_message_id"] for i in store.list_reply_task_inputs(task.id)] == [
        "initial", "invoice-arrived",
    ]
