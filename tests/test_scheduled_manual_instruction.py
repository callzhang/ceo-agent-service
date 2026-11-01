"""Manual corrections reach scheduled Consumer/Audit without replacing history."""

from app.agent_contracts import AuditAgentResult
from app.agent_cron.consumer import ScheduledAgentConsumer
from app.agent_orchestrator import AgentOrchestrator
from app.dispatcher.adapters import ScheduledExecutionQueueAdapter
from tests.test_scheduled_agent_consumer import (
    NOW, Audit, Consumer, claim, dispatch, fixture, no_action, proposal,
)


def test_scheduled_manual_revision_receives_instruction_and_prior_audit_feedback(tmp_path):
    store, occurrence, options = fixture(tmp_path, notify_on_completion=False)
    task_id = int(dispatch(store, occurrence, options).execution_id)
    original = store.get_reply_task(task_id)
    original_input = original.trigger_message_json
    adapter = ScheduledExecutionQueueAdapter(store, owner_alive=lambda _pid: False)
    envelope, guard = claim(adapter, task_id, "original")
    ScheduledAgentConsumer(
        store=store, option_service=options, now=lambda: NOW,
        orchestrator_factory=lambda _built: AgentOrchestrator(
            store=store,
            consumer=Consumer(store, proposal("original draft"), no_action("revised draft")),
            audit=Audit(store, "return", "approve"),
        ),
    )(envelope, guard)
    guard.complete(NOW)
    source = store.get_latest_reply_attempt_for_trigger(
        original.conversation_id, original.trigger_message_id
    )
    previous_runs = store.list_agent_runs_for_task_generation(
        task_id, original.execution_generation
    )
    previous_facts = [(run.id, run.status, run.adopted_result_json) for run in previous_runs]
    expected_feedback = [
        store.get_candidate_review_for_audit_run(run.id)["result_json"]
        for run in previous_runs if run.role.value == "audit"
    ]
    feedback = "Only revise the public message; keep published materials unchanged."
    suggestion = "A proposed correction, still requiring independent Audit."
    assert store.record_reply_feedback(
        source.id, feedback=f"  {feedback}  ", corrected_reply_text=f"  {suggestion}  "
    )
    revised = store.enqueue_manual_rerun_reply_task(
        conversation_id=original.conversation_id,
        conversation_title=original.conversation_title,
        single_chat=original.single_chat,
        trigger_message_id=original.trigger_message_id,
        trigger_create_time=original.trigger_create_time,
        trigger_sender=original.trigger_sender,
        trigger_text=original.trigger_text,
        trigger_message_json=original_input,
        attempt_id=source.id,
        channel="scheduled",
    )
    observed = []
    factory_contexts = []
    audit_contexts = []

    class RecordingConsumer(Consumer):
        def run(self, task, context, **kwargs):
            observed.append(context)
            return super().run(task, context, **kwargs)

    class RecordingAudit(Audit):
        def run(self, task, context, **kwargs):
            audit_contexts.append(context)
            return super().run(task, context, **kwargs)

    real_orchestrator = AgentOrchestrator(
        store=store, consumer=RecordingConsumer(store, no_action("corrected draft")),
        audit=RecordingAudit(store, "approve"),
    )

    class RecordingOrchestrator:
        def process(self, task, context, *, refresh_context):
            assert refresh_context().manual_rerun == context.manual_rerun
            return real_orchestrator.process(task, context, refresh_context=refresh_context)

    def factory(built):
        factory_contexts.append(built.context)
        return RecordingOrchestrator()

    envelope, guard = claim(adapter, task_id, "revision")
    ScheduledAgentConsumer(
        store=store, option_service=options, now=lambda: NOW,
        orchestrator_factory=factory,
    )(envelope, guard)

    assert len(observed) == 1
    instruction = observed[0].manual_rerun
    assert instruction is not None
    assert factory_contexts[0].manual_rerun == instruction
    assert instruction.source_attempt_id == source.id
    assert instruction.reviewer_feedback == feedback
    assert instruction.suggested_reply_text == suggestion
    assert instruction.feedback_scope == "one_time"
    assert not instruction.skill_update_requested
    assert instruction.skill_update_receipts_json == "[]"
    old_return = AuditAgentResult.model_validate_json(expected_feedback[0])
    assert instruction.prior_audit_feedback == (old_return.feedback,)
    assert len(audit_contexts) == 1
    assert audit_contexts[0].task.manual_rerun == instruction
    assert feedback in observed[0].render()
    assert suggestion in observed[0].render()
    assert observed[0].trigger_text == original.trigger_text
    assert observed[0].trigger_raw_payload["scheduled_task_run_id"] == occurrence.id
    assert observed[0].task_id == task_id
    assert store.get_reply_task(task_id).execution_generation == revised.execution_generation
    assert store.get_reply_task(task_id).status == "done"
    assert store.get_scheduled_task_run(occurrence.id).execution_id == str(task_id)
    assert [(run.role.value, run.status) for run in store.list_agent_runs_for_task_generation(
        task_id, revised.execution_generation
    )] == [("consumer", "completed"), ("audit", "completed")]
    assert [(run.id, run.status, run.adopted_result_json) for run in
            store.list_agent_runs_for_task_generation(task_id, original.execution_generation)] == previous_facts


def test_ordinary_message_keeps_the_same_manual_correction_fields(tmp_path, monkeypatch):
    from tests.test_worker import FakeCodex, FakeDws, conversation, make_worker, message

    trigger = message("Please revise the reply")
    worker = make_worker(tmp_path, FakeDws([], {}), FakeCodex([]), monkeypatch)
    source_id = worker.store.record_reply_attempt(
        conversation_id="cid-1", conversation_title="Friday",
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name, trigger_text=trigger.content,
        action="send_reply", sensitivity_kind="general", send_status="failed",
        draft_reply_text="previous draft",
    )
    worker.store.record_reply_feedback(
        source_id, feedback="  Keep the original material unchanged.  ",
        corrected_reply_text="  Revised reply for Audit.  ",
    )
    task = worker.store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-1", conversation_title="Friday", single_chat=False,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name, trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(), attempt_id=source_id,
    )
    context = worker._build_agent_task_context(
        conversation=conversation(), task=task, trigger=trigger,
        context_messages=[trigger],
    )
    assert context.manual_rerun.source_attempt_id == source_id
    assert context.manual_rerun.reviewer_feedback == "Keep the original material unchanged."
    assert context.manual_rerun.suggested_reply_text == "Revised reply for Audit."
    assert context.manual_rerun.feedback_scope == "one_time"
    assert context.manual_rerun.prior_audit_feedback == ()
    assert not context.manual_rerun.skill_update_requested
    assert context.manual_rerun.skill_update_receipts_json == "[]"
