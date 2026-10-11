"""Whole candidate review and execution, independent of the model runtime."""

from collections import deque

from app.agent_context import AgentTaskContext
from app.agent_contracts import (
    ConsumerAgentResult,
    AuditAgentResult,
    SystemExecutionResult,
)
from app.agent_orchestrator import AgentOrchestrator, _NextAudit
from app.agent_result import AgentError
from app.agent_turn_runner import AgentTurnRunResult
from app.store import AgentRole, AutoReplyStore
from app.system_action_handlers import OaDecisionHandler
from app.system_executor import ActionOutcome, SystemExecutor


def candidate(
    text="message", *, human=False, stage=0, predecessor=None, continue_after=False
):
    plan = dict(
        objective="Notify",
        actions=[
            dict(
                description="Notify",
                action_identity="notice",
                capability="dingtalk-chat",
                operation="send_message",
                target={"user_id": "recipient"},
                payload={"text": text},
                effect="external",
            )
        ],
        sourced_facts=[],
        authored_judgment="Relevant",
    )
    values = dict(
        outcome="needs_human" if human else "proposal",
        summary="Candidate",
        proposal=None if human else plan,
        risk="low",
        confidence=1.0,
        rule_coverage=1.0,
        information_completeness=1.0,
        error=AgentError(code="", retryable=False),
        stage_index=stage,
        predecessor_review_id=predecessor,
        continue_after_execution=continue_after,
    )
    if human:
        values.update(
            needs_human_reason="A business preference is missing",
            decision_options=[
                dict(
                    key="send",
                    label="Notify",
                    instruction="Notify once",
                    consequence="Recipient is informed",
                    plan=plan,
                ),
                dict(
                    key="stop",
                    label="Stop",
                    instruction="Stop",
                    consequence="No notification",
                    terminal_outcome="skipped",
                    reason="No contact desired",
                ),
            ],
            decision_basis=dict(
                verified_facts=[
                    dict(assertion="Recipient identified", references=["task:1"])
                ],
                rule_evidence=[
                    dict(
                        assertion="No preference rule applies",
                        references=["skill:review"],
                    )
                ],
                quality_explanation="Two viable outcomes require a choice",
                no_external_action_evidence=[
                    dict(assertion="No action attempted", references=["task:1"])
                ],
                conclusion="Choose this instance",
            ),
        )
    return ConsumerAgentResult.model_validate(values)


def setup(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.enqueue_reply_task(
        channel="dingtalk",
        conversation_id="conversation",
        conversation_title="Test",
        single_chat=True,
        trigger_message_id="message",
        trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:00:00",
        trigger_text="Review",
    )
    task = store.claim_reply_tasks(1)[0]
    context = AgentTaskContext(
        task_id=task.id,
        channel=task.channel,
        conversation_id=task.conversation_id,
        conversation_title="Test",
        single_chat=True,
        trigger_message_id=task.trigger_message_id,
        trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:00:00",
        trigger_text="Review",
        messages=(),
        materials=(),
        prior_receipts=(),
    )
    return store, task, context


class Consumer:
    def __init__(self, store, *results):
        self.store, self.results, self.calls = store, deque(results), []

    def run(
        self, task, context, *, proposal_revision, parent_agent_run_id, feedback=None
    ):
        run = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=proposal_revision,
            turn_attempt=self.store.next_agent_run_turn_attempt(
                task.id,
                task.execution_generation,
                role=AgentRole.CONSUMER,
                proposal_revision=proposal_revision,
            ),
            parent_agent_run_id=parent_agent_run_id,
            operation_id="",
            owner="consumer",
        ).run
        result = self.results.popleft()
        if callable(result):
            result = result(context)
        if result.outcome == "failed":
            self.store.fail_agent_run(run.id, result.error.model_dump(mode="json"), owner="consumer")
        else:
            self.store.complete_agent_run(
                run.id, result.model_dump(mode="json"), owner="consumer"
            )
        self.calls.append((context, feedback))
        return AgentTurnRunResult(run.id, result, 0, 1)


class Audit:
    def __init__(self, store, *outcomes):
        self.store, self.outcomes, self.calls = store, deque(outcomes), []

    def run(self, task, context, *, turn_attempt, parent_agent_run_id):
        run = self.store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.AUDIT,
            proposal_revision=context.proposal_revision,
            turn_attempt=turn_attempt,
            parent_agent_run_id=parent_agent_run_id,
            operation_id=context.operation_id,
            owner="audit",
        ).run
        outcome = self.outcomes.popleft()
        result = AuditAgentResult(
            outcome=outcome,
            summary=outcome,
            proposal_revision=context.proposal_revision,
            error=AgentError(code="", retryable=False),
            candidate_digest=context.candidate_digest,
            feedback=(
                dict(
                    rule="evidence",
                    observation="Incomplete",
                    requested_revision="Replace complete candidate",
                )
                if outcome in ("return", "reject")
                else None
            ),
            risk="low",
            confidence=1.0,
            rule_coverage=1.0,
            information_completeness=1.0,
        )
        self.store.complete_agent_run(
            run.id, result.model_dump(mode="json"), owner="audit"
        )
        self.calls.append(context)
        return AgentTurnRunResult(run.id, result, 0, 1)


class Executor:
    def __init__(self, store):
        self.calls = []
        self.store = store

    def execute(self, task, candidate_id, review_id, *, context=None):
        self.calls.append((candidate_id, review_id))
        claim = self.store.claim_candidate_execution(
            candidate_id, review_id, "executor", 60
        )
        import json

        plan = json.loads(
            self.store.get_review_candidate(candidate_id)["candidate_json"]
        )["proposal"]
        for index, action in enumerate(plan["actions"]):
            key = f"stage:{candidate_id}:{index}"
            self.store.begin_candidate_action(claim["id"], "executor", index, key)
            self.store.record_candidate_external_action(
                claim["id"],
                "executor",
                index,
                key,
                action["operation"],
                action["target"],
                {"receipt": "verified"},
            )
        result = SystemExecutionResult(outcome="executed", summary="Verified execution")
        self.store.finish_candidate_execution(claim["id"], "executor", "done", result)
        return result


def test_human_candidate_waits_for_audit(tmp_path):
    store, task, context = setup(tmp_path)
    consumer = Consumer(store, candidate(human=True))
    audit = Audit(store, "approve")
    consumer.run(task, context, proposal_revision=0, parent_agent_run_id=None)
    orchestrator = AgentOrchestrator(store=store, consumer=consumer, audit=audit)
    assert isinstance(orchestrator._derive_state(task), _NextAudit)
    result = orchestrator.process(task, context, refresh_context=lambda: context)
    assert result.status == "needs_human"
    assert result.final_role is AgentRole.AUDIT
    assert len(audit.calls) == 1
    assert (
        store.current_reviewed_candidate(task.id, task.execution_generation)[
            "review_id"
        ]
        == result.review_id
    )


def test_approved_plan_invokes_system_after_review(tmp_path):
    store, task, context = setup(tmp_path)
    executor = Executor(store)
    audit = Audit(store, "approve")
    result = AgentOrchestrator(
        store=store,
        consumer=Consumer(store, candidate()),
        audit=audit,
        system_executor=executor,
    ).process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    assert result.execution_result.outcome == "executed"
    assert len(executor.calls) == 1
    assert audit.calls[0].candidate.proposal.actions[0].payload == {"text": "message"}


def test_return_can_keep_exact_action_but_reject_cannot(tmp_path):
    store, task, context = setup(tmp_path)
    executor = Executor(store)
    consumer = Consumer(store, candidate(), candidate())
    result = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=Audit(store, "return", "approve"),
        system_executor=executor,
    ).process(task, context, refresh_context=lambda: context)
    assert result.status == "executed" and result.feedback_cycles == 1
    store2, task2, context2 = setup(tmp_path / "other")
    executor2 = Executor(store2)
    result = AgentOrchestrator(
        store=store2,
        consumer=Consumer(store2, candidate(), candidate()),
        audit=Audit(store2, "reject"),
        system_executor=executor2,
    ).process(task2, context2, refresh_context=lambda: context2)
    assert result.status == "failed_terminal"
    assert result.error.code == "rejected_candidate_unchanged"
    assert not executor2.calls


def test_four_business_returns_fail_without_human_question(tmp_path):
    store, task, context = setup(tmp_path)
    consumer = Consumer(store, *[candidate(str(i)) for i in range(4)])
    result = AgentOrchestrator(
        store=store, consumer=consumer, audit=Audit(store, *["return"] * 4)
    ).process(task, context, refresh_context=lambda: context)
    assert result.status == "failed_terminal"
    assert result.error.code == "audit_revision_exhausted"
    assert len(consumer.calls) == 4
    assert result.consumer_result.decision_options == ()


def test_stage_has_receipt_lineage_and_fresh_content_budget(tmp_path):
    store, task, context = setup(tmp_path)
    consumer = Consumer(
        store,
        candidate(continue_after=True),
        lambda c: candidate("second", stage=1, predecessor=c.predecessor_review_id),
    )
    result = AgentOrchestrator(
        store=store,
        consumer=consumer,
        audit=Audit(store, "approve", "approve"),
        system_executor=Executor(store),
    ).process(task, context, refresh_context=lambda: context)
    assert result.status == "executed"
    assert consumer.calls[1][0].stage_index == 1
    assert consumer.calls[1][0].predecessor_review_id is not None


def test_runtime_risk_refusal_keeps_source_and_stops_without_retry(tmp_path):
    store,task,context=setup(tmp_path)
    consumer=Consumer(store,candidate())
    consumer.run(task,context,proposal_revision=0,parent_agent_run_id=None)
    parent=store.list_agent_runs_for_task_generation(task.id,task.execution_generation)[0]
    run=store.claim_agent_run(task.id,task.execution_generation,role=AgentRole.AUDIT,proposal_revision=0,
        turn_attempt=0,parent_agent_run_id=parent.id,operation_id='risk',owner='audit').run
    store.fail_agent_run(run.id,AgentError(code='agent_reported_failure',source='agent',source_code='provider_risk_rejected',retryable=True).model_dump(mode='json'),owner='audit')
    audit=Audit(store,'approve')
    result=AgentOrchestrator(store=store,consumer=consumer,audit=audit).process(task,context,refresh_context=lambda:context)
    assert result.status=='failed_terminal'
    assert result.error.source_code=='provider_risk_rejected'
    assert not audit.calls


def test_completed_stage_restart_uses_exact_predecessor_review(tmp_path):
    store,task,context=setup(tmp_path)
    consumer=Consumer(store,candidate(continue_after=True),lambda c:candidate('second',stage=1,predecessor=c.predecessor_review_id))
    orchestrator=AgentOrchestrator(store=store,consumer=consumer,audit=Audit(store,'approve','approve'),system_executor=Executor(store))
    first=orchestrator.process(task,context,refresh_context=lambda:context)
    resumed=orchestrator.process(task,context,refresh_context=lambda:context)
    assert resumed.status==first.status=='executed'
    assert len(consumer.calls)==2


def test_uncertain_execution_reconciles_on_later_pass_without_model_rerun(tmp_path):
    store, task, context = setup(tmp_path)

    class UncertainThenVerified(Executor):
        def execute(self, task, candidate_id, review_id, *, context=None):
            if not self.calls:
                self.calls.append((candidate_id, review_id))
                claim = self.store.claim_candidate_execution(candidate_id, review_id, 'executor', 60)
                result = SystemExecutionResult(outcome='failed', summary='Receipt is pending', error=AgentError(code='external_action_uncertain', retryable=True))
                self.store.finish_candidate_execution(claim['id'], 'executor', 'uncertain', result)
                return result
            return super().execute(task, candidate_id, review_id)

    consumer, audit = Consumer(store, candidate()), Audit(store, 'approve')
    executor = UncertainThenVerified(store)
    orchestrator = AgentOrchestrator(store=store, consumer=consumer, audit=audit, system_executor=executor)
    first = orchestrator.process(task, context, refresh_context=lambda: context)
    assert first.status == 'failed_retryable'
    resumed = orchestrator.process(task, context, refresh_context=lambda: context)
    assert resumed.status == 'executed'
    assert len(executor.calls) == 2
    assert len(consumer.calls) == len(audit.calls) == 1


def test_changed_business_state_revises_same_stage_then_reviews_again(tmp_path):
    store, task, context = setup(tmp_path)

    class ChangedThenVerified(Executor):
        def execute(self, task, candidate_id, review_id, *, context=None):
            if not self.calls:
                self.calls.append((candidate_id, review_id))
                claim = self.store.claim_candidate_execution(candidate_id, review_id, 'executor', 60)
                result = SystemExecutionResult(outcome='failed', summary='Current owner changed', error=AgentError(code='business_state_changed', retryable=False))
                self.store.finish_candidate_execution(claim['id'], 'executor', 'failed', result)
                self.store.invalidate_review_candidate(candidate_id, 'Current owner changed')
                return result
            return super().execute(task, candidate_id, review_id)

    consumer = Consumer(store, candidate(), candidate('new owner'))
    audit = Audit(store, 'approve', 'approve')
    executor = ChangedThenVerified(store)
    result = AgentOrchestrator(store=store, consumer=consumer, audit=audit, system_executor=executor).process(task, context, refresh_context=lambda: context)
    assert result.status == 'executed'
    assert len(consumer.calls) == len(audit.calls) == len(executor.calls) == 2
    assert consumer.calls[1][0].stage_index == 0
    assert consumer.calls[1][0].predecessor_review_id is None
    assert result.feedback_cycles == 0


def test_later_consumer_read_failure_reuses_completed_candidate_after_restart(tmp_path):
    store, task, context = setup(tmp_path)
    consumer = Consumer(
        store,
        candidate("Original applicant notice"),
        ConsumerAgentResult.model_validate({
            "outcome": "failed", "summary": "Live source temporarily unavailable",
            "proposal": None,
            "error": AgentError(code="provider_read_failed", retryable=True),
            "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
            "information_completeness": 1.0,
        }),
    )
    consumer.run(task, context, proposal_revision=0, parent_agent_run_id=None)
    consumer.run(task, context, proposal_revision=0, parent_agent_run_id=None)
    original = store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation,
    )[0]
    original_json = original.adopted_result_json

    reopened = AutoReplyStore(store.path)
    fresh_consumer = Consumer(reopened)
    audit = Audit(reopened, "approve")
    executor = Executor(reopened)
    result = AgentOrchestrator(
        store=reopened, consumer=fresh_consumer, audit=audit,
        system_executor=executor,
    ).process(task, context, refresh_context=lambda: context)

    assert result.status == "executed"
    assert fresh_consumer.calls == []
    assert len(audit.calls) == len(executor.calls) == 1
    assert audit.calls[0].candidate.proposal.actions[0].payload == {
        "text": "Original applicant notice",
    }
    runs = reopened.list_agent_runs_for_task_generation(
        task.id, task.execution_generation,
    )
    assert len(runs) == 3
    assert runs[0].id == original.id
    assert runs[0].adopted_result_json == original_json
    assert runs[1].status == "failed"  # The later technical failure stays in history.
    assert runs[2].parent_agent_run_id == original.id


def test_sqlite_oa_then_notice_reconciles_delayed_receipt_without_replaying_oa(tmp_path):
    store, task, context = setup(tmp_path)
    result = candidate("Approval")
    plan = result.proposal.model_dump(mode="json")
    plan["actions"] = [
        {
            "description": "Approve exact OA task", "action_identity": "oa-approve",
            "capability": "dingtalk-oa", "operation": "approve",
            "target": {"process_instance_id": "process-1", "task_id": "task-1"},
            "payload": {"remark": "Approved after review"},
        },
        {
            "description": "Notify applicant", "action_identity": "applicant-notice",
            "capability": "dingtalk-chat", "operation": "send_direct_message",
            "target": {"user_id": "applicant"},
            "payload": {"content": "Your request was approved."},
        },
    ]
    result = ConsumerAgentResult.model_validate({
        **result.model_dump(mode="json"), "proposal": plan,
    })

    class Provider:
        def __init__(self):
            self.approval_calls = 0
            self.approved = False

        def get_current_user_id(self):
            return "derek"

        def read_oa_approval_detail(self, process_id):
            assert process_id == "process-1"
            return {"result": {"processInstanceId": process_id, "tasks": [{
                "taskId": "task-1", "userid": "derek",
                "taskStatus": "COMPLETED" if self.approved else "RUNNING",
                "taskResult": "AGREE" if self.approved else "",
            }]}}

        def execute_oa_approval_action(self, process_id, task_id, action, remark):
            assert (process_id, task_id, action, remark) == (
                "process-1", "task-1", "通过", "Approved after review",
            )
            self.approval_calls += 1
            self.approved = True
            return {"success": True}

    class DelayedNotice:
        def __init__(self):
            self.dispatch_calls = 0
            self.reconcile_calls = 0
            self.readback_visible = False

        def dispatch(self, action, *, action_key, candidate):
            assert action.target == {"user_id": "applicant"}
            assert action.payload == {"content": "Your request was approved."}
            self.dispatch_calls += 1
            return ActionOutcome("uncertain", {"reason": "provider_timeout_after_send"})

        def reconcile(self, action, *, action_key, candidate):
            self.reconcile_calls += 1
            return (ActionOutcome("verified", {"message_id": "notice-1"})
                    if self.readback_visible else ActionOutcome(
                        "uncertain", {"reason": "provider_readback_delayed"},
                    ))

    provider, notice = Provider(), DelayedNotice()

    def executor(current_store):
        return SystemExecutor(current_store, {
            ("dingtalk-oa", "approve"): OaDecisionHandler(provider),
            ("dingtalk-chat", "send_direct_message"): notice,
        }, owner="system-executor")

    consumer, audit = Consumer(store, result), Audit(store, "approve")
    first = AgentOrchestrator(
        store=store, consumer=consumer, audit=audit,
        system_executor=executor(store),
    ).process(task, context, refresh_context=lambda: context)
    assert first.status == "failed_retryable"
    assert first.error.code == "external_action_uncertain"
    assert first.execution_result.completed_action_keys == ("oa-approve",)
    assert provider.approval_calls == notice.dispatch_calls == 1
    assert len(consumer.calls) == len(audit.calls) == 1
    execution_id = store.get_candidate_execution(first.candidate_id)["id"]
    first_attempts = store.list_candidate_action_attempts(execution_id)
    assert [item["status"] for item in first_attempts] == ["verified", "uncertain"]
    assert store.get_candidate_external_action(first_attempts[0]["external_action_key"])
    assert store.get_candidate_external_action(first_attempts[1]["external_action_key"]) is None

    reopened = AutoReplyStore(store.path)
    resume_consumer, resume_audit = Consumer(reopened), Audit(reopened)
    resumed_driver = AgentOrchestrator(
        store=reopened, consumer=resume_consumer, audit=resume_audit,
        system_executor=executor(reopened),
    )
    pending = resumed_driver.process(task, context, refresh_context=lambda: context)
    assert pending.status == "failed_retryable"
    assert provider.approval_calls == notice.dispatch_calls == 1
    assert notice.reconcile_calls >= 1
    assert [item["status"] for item in reopened.list_candidate_action_attempts(
        execution_id,
    )] == ["verified", "uncertain"]

    notice.readback_visible = True
    finished = resumed_driver.process(task, context, refresh_context=lambda: context)
    assert finished.status == "executed"
    assert finished.execution_result.completed_action_keys == (
        "oa-approve", "applicant-notice",
    )
    assert provider.approval_calls == notice.dispatch_calls == 1
    assert resume_consumer.calls == resume_audit.calls == []
    finished_attempts = reopened.list_candidate_action_attempts(execution_id)
    assert [item["status"] for item in finished_attempts] == ["verified", "verified"]
    assert reopened.get_candidate_external_action(
        finished_attempts[1]["external_action_key"],
    )
    assert reopened.get_candidate_execution(finished.candidate_id)["status"] == "done"
