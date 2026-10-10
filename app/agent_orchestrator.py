from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from app.agent_context import AgentTaskContext, AuditTurnContext, PriorReceipt
from app.agent_contracts import (
    AuditAgentResult,
    AuditFeedback,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
    ConsumerProposal,
    SystemExecutionResult,
)
from app.agent_result import AgentError, ResultParseError
from app.agent_turn_runner import AgentTurnRunResult
from app.codex_capacity import is_codex_provider_recovery_code
from app.external_retry import retry_delay_seconds
from app.reviewed_candidates import rejected_content_changed
from app.store import AgentRole, AgentRun, AutoReplyStore, ReplyTask

MAX_TURNS_PER_PROCESS = 32
MAX_CONTENT_FEEDBACK_CYCLES = 3
MAX_ROLE_ATTEMPTS_PER_PROCESS = 2
MAX_CONSECUTIVE_FAILED_TURNS = MAX_ROLE_ATTEMPTS_PER_PROCESS * 3
EXTERNAL_DEPENDENCY_WAIT_ERRORS = frozenset(
    {"dependency_read_unavailable", "agent_context_refresh_failed"}
)
MAX_EXECUTION_STAGES = 16


class ConsumerRunner(Protocol):
    def run(
        self,
        task: ReplyTask,
        context: AgentTaskContext,
        *,
        proposal_revision: int,
        parent_agent_run_id: int | None,
        feedback: AuditFeedback | None = None,
    ) -> AgentTurnRunResult[ConsumerAgentResult]: ...


class AuditRunner(Protocol):
    def run(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        turn_attempt: int,
        parent_agent_run_id: int,
    ) -> AgentTurnRunResult[AuditAgentResult]: ...


class CandidateExecutor(Protocol):
    def execute(
        self, task: ReplyTask, candidate_id: int, review_id: int, *, context: AgentTaskContext
    ) -> SystemExecutionResult: ...


@dataclass(frozen=True)
class OrchestrationResult:
    status: str
    final_run_id: int
    final_role: AgentRole
    summary: str
    error: AgentError
    feedback_cycles: int
    feedback: AuditFeedback | None = None
    consumer_result: ConsumerAgentResult | None = None
    audit_result: AuditAgentResult | None = None
    retry_after_seconds: float = 0.0
    execution_result: SystemExecutionResult | None = None
    candidate_id: int | None = None
    review_id: int | None = None


@dataclass(frozen=True)
class _NextConsumer:
    proposal_revision: int
    parent_run_id: int | None
    feedback: AuditFeedback | None
    deferred_error_code: str = ""
    stage_index: int = 0
    predecessor_review_id: int | None = None


@dataclass(frozen=True)
class _NextAudit:
    proposal_revision: int
    turn_attempt: int
    parent_run_id: int
    candidate: ConsumerAgentResult
    candidate_id: int
    candidate_digest: str
    deferred_error_code: str = ""


@dataclass(frozen=True)
class _NextExecution:
    consumer_run: AgentRun
    audit_run: AgentRun
    consumer_result: ConsumerAgentResult
    audit_result: AuditAgentResult
    candidate_id: int
    review_id: int
    feedback_cycles: int


@dataclass(frozen=True)
class _Deferred:
    run: AgentRun | None
    code: str
    feedback_cycles: int
    detail: str = ""
    source_error: AgentError | None = None


def _enrich_oa_applicant_target(
    proposal: ConsumerProposal,
    context: AgentTaskContext,
) -> ConsumerProposal:
    open_dingtalk_id = str(
        context.trigger_raw_payload.get("originatorOpenDingTalkId") or ""
    ).strip()
    applicant_user_id = str(
        context.trigger_raw_payload.get("originatorUserid") or ""
    ).strip()
    if not open_dingtalk_id or not applicant_user_id:
        return proposal
    actions = []
    changed = False
    for action in proposal.actions:
        target = action.target
        if (
            not isinstance(target, dict)
            or str(target.get("user_id") or "").strip() != applicant_user_id
        ):
            actions.append(action)
            continue
        if target.get("open_dingtalk_id") == open_dingtalk_id:
            actions.append(action)
            continue
        updated_target = dict(target)
        updated_target["open_dingtalk_id"] = open_dingtalk_id
        actions.append(action.model_copy(update={"target": updated_target}))
        changed = True
    if not changed:
        return proposal
    return proposal.model_copy(update={"actions": tuple(actions)})


class AgentOrchestrator:
    """Resume persisted whole-candidate reviews and execute only their bound plans."""

    def __init__(
        self,
        *,
        store: AutoReplyStore,
        consumer: ConsumerRunner,
        audit: AuditRunner,
        system_executor: CandidateExecutor | None = None,
    ):
        self.store, self.consumer, self.audit, self.system_executor = (
            store,
            consumer,
            audit,
            system_executor,
        )

    def process(
        self,
        task: ReplyTask,
        context: AgentTaskContext,
        *,
        refresh_context: Callable[[], AgentTaskContext],
    ) -> OrchestrationResult:
        _validate_refreshed_task_context(task, context)
        attempts: dict[tuple[AgentRole, int], int] = {}
        for _ in range(MAX_TURNS_PER_PROCESS):
            state = self._derive_state(task)
            if isinstance(state, OrchestrationResult):
                return state
            if isinstance(state, _Deferred):
                return self._deferred_result(state)
            if isinstance(state, _NextExecution):
                try:
                    current_context = refresh_context()
                    _validate_refreshed_task_context(task, current_context)
                except Exception as exc:
                    return self._deferred_result(_Deferred(state.audit_run,
                        "agent_context_refresh_failed", state.feedback_cycles,
                        _context_refresh_failure_detail(exc), _context_refresh_error(exc)))
                if self.system_executor is None:
                    return self._candidate_terminal(
                        state,
                        "failed_terminal",
                        AgentError(code="system_executor_unavailable", retryable=False),
                    )
                executed = self.system_executor.execute(
                    task, state.candidate_id, state.review_id, context=current_context
                )
                if executed.error.code == "business_state_changed":
                    state = self._business_revision(state)
                elif (
                    executed.outcome != "executed"
                    or not state.consumer_result.continue_after_execution
                ):
                    status = (
                        _failure_status(executed.error)
                        if executed.outcome == "failed"
                        else executed.outcome
                    )
                    return self._candidate_terminal(
                        state, status, executed.error, executed
                    )
                elif state.consumer_result.stage_index + 1 >= MAX_EXECUTION_STAGES:
                    return self._candidate_terminal(
                        state,
                        "failed_terminal",
                        AgentError(
                            code="execution_stage_limit_reached", retryable=False
                        ),
                        executed,
                    )
                else:
                    state = _NextConsumer(
                        state.consumer_run.proposal_revision + 1,
                        state.audit_run.id,
                        None,
                        stage_index=state.consumer_result.stage_index + 1,
                        predecessor_review_id=state.review_id,
                    )
            role = (
                AgentRole.CONSUMER
                if isinstance(state, _NextConsumer)
                else AgentRole.AUDIT
            )
            key = (role, state.proposal_revision)
            bound = 1 if state.deferred_error_code else MAX_ROLE_ATTEMPTS_PER_PROCESS
            if attempts.get(key, 0) >= bound:
                return self._retry_exhausted_result(
                    task, role=role, proposal_revision=state.proposal_revision
                )
            attempts[key] = attempts.get(key, 0) + 1
            try:
                fresh = refresh_context()
                _validate_refreshed_task_context(task, fresh)
                fresh = self._with_persisted_evidence(task, fresh, state)
            except Exception as exc:
                return self._deferred_result(
                    _Deferred(
                        None,
                        "agent_context_refresh_failed",
                        self._feedback_cycles(task),
                        _context_refresh_failure_detail(exc),
                        _context_refresh_error(exc),
                    )
                )
            try:
                if isinstance(state, _NextConsumer):
                    fresh = replace(
                        fresh,
                        stage_index=state.stage_index,
                        predecessor_review_id=state.predecessor_review_id,
                    )
                    self.consumer.run(
                        task,
                        fresh,
                        proposal_revision=state.proposal_revision,
                        parent_agent_run_id=state.parent_run_id,
                        feedback=state.feedback,
                    )
                else:
                    self._validate_audit_parent(task, state)
                    self.audit.run(
                        task,
                        AuditTurnContext(
                            task=fresh,
                            proposal_revision=state.proposal_revision,
                            operation_id=_operation_id(task, state.proposal_revision),
                            candidate=state.candidate,
                            candidate_digest=state.candidate_digest,
                            audit_rules="",
                        ),
                        turn_attempt=state.turn_attempt,
                        parent_agent_run_id=state.parent_run_id,
                    )
            except (RuntimeError, ResultParseError) as exc:
                if str(exc) in {
                    "agent_run_unavailable",
                    "audit_consumer_parent_invalid",
                    "codex_session_locked",
                    "runtime_provider_unreachable",
                }:
                    return self._deferred_result(
                        _Deferred(None, str(exc), self._feedback_cycles(task))
                    )
                # The runner persists its classified failure before raising; derive
                # the next bounded technical attempt from that durable run.
                if not self.store.list_agent_runs_for_task_generation(
                    task.id, task.execution_generation, load_events=False
                ):
                    raise
        return self._deferred_result(
            _Deferred(None, "agent_turn_limit_reached", self._feedback_cycles(task))
        )

    def _validate_audit_parent(self, task: ReplyTask, state: _NextAudit) -> None:
        parent = self.store.get_agent_run(state.parent_run_id, load_events=False)
        if (
            parent is None
            or parent.reply_task_id != task.id
            or parent.execution_generation != task.execution_generation
            or parent.role is not AgentRole.CONSUMER
            or parent.proposal_revision != state.proposal_revision
            or parent.status != "completed"
        ):
            raise RuntimeError("audit_consumer_parent_invalid")

    def _consumer_result(self, run: AgentRun) -> ConsumerAgentResult:
        candidate = self.store.adopted_candidate_for_consumer_run(run.id)
        if candidate is None:
            raise ValueError("completed Consumer candidate unavailable")
        return ConsumerAgentResult.model_validate_json(candidate["candidate_json"])

    def _audit_result(self, run: AgentRun) -> AuditAgentResult:
        review = self.store.get_candidate_review_for_audit_run(run.id)
        if review is None:
            raise ValueError("completed Audit review unavailable")
        return AuditAgentResult.model_validate_json(review["result_json"])

    def _derive_state(self, task: ReplyTask):
        runs = self.store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation, load_events=False
        )
        consumers = sorted(
            (r for r in runs if r.role is AgentRole.CONSUMER),
            key=lambda r: (r.proposal_revision, r.turn_attempt, r.id),
        )
        if not consumers:
            return _NextConsumer(0, None, None)
        consumer = consumers[-1]
        # A later technical turn cannot erase the durable complete candidate
        # in this exact revision and parent lineage. Its failure remains history.
        for prior in reversed(consumers):
            if (prior.proposal_revision, prior.parent_agent_run_id) != (
                consumer.proposal_revision, consumer.parent_agent_run_id
            ) or prior.status != "completed":
                continue
            try:
                completed = self._consumer_result(prior)
            except ValueError:
                continue
            if completed.outcome is not ConsumerOutcome.FAILED:
                consumer = prior
                break
        cycles = self._feedback_cycles_by_runs(runs)
        technical = self._technical_state(task, consumer, cycles)
        if technical is not None:
            if isinstance(technical, _Deferred):
                return technical
            if technical.status != "failed_retryable":
                return technical
            parent = (
                self.store.get_agent_run(consumer.parent_agent_run_id, load_events=False)
                if consumer.parent_agent_run_id
                else None
            )
            feedback = (
                self._audit_result(parent).feedback
                if parent and parent.status == "completed"
                else None
            )
            # Technical attempts stay in the same revision and use its original
            # parent; they do not spend a content feedback cycle.
            return _NextConsumer(
                consumer.proposal_revision,
                consumer.parent_agent_run_id,
                feedback,
                technical.error.code if _is_waiting_failure(technical.error) else "",
                **self._stage_for_parent(parent),
            )
        try:
            result = self._consumer_result(consumer)
        except ValueError:
            return self._run_terminal(
                consumer, "failed_terminal",
                AgentError(code="completed_consumer_candidate_unavailable", retryable=False), cycles,
            )
        if result.outcome is ConsumerOutcome.FAILED:
            return self._run_terminal(
                consumer, _failure_status(result.error), result.error, cycles, result
            )
        parent = (
            self.store.get_agent_run(consumer.parent_agent_run_id, load_events=False)
            if consumer.parent_agent_run_id
            else None
        )
        if parent:
            previous = self._audit_result(parent)
            expected_stage = self._stage_for_parent(parent)
            if (
                result.stage_index != expected_stage["stage_index"]
                or result.predecessor_review_id
                != expected_stage["predecessor_review_id"]
            ):
                return self._run_terminal(
                    consumer,
                    "failed_terminal",
                    AgentError(code="candidate_stage_lineage_invalid", retryable=False),
                    cycles,
                    result,
                )
            if previous.outcome is AuditOutcome.REJECT:
                original = self._consumer_result(
                    self.store.get_agent_run(parent.parent_agent_run_id, load_events=False)
                )
                if not rejected_content_changed(original, result):
                    return self._run_terminal(
                        consumer,
                        "failed_terminal",
                        AgentError(
                            code="rejected_candidate_unchanged", retryable=False
                        ),
                        cycles,
                        result,
                    )
        elif result.stage_index != 0 or result.predecessor_review_id is not None:
            return self._run_terminal(
                consumer,
                "failed_terminal",
                AgentError(code="candidate_stage_lineage_invalid", retryable=False),
                cycles,
                result,
            )
        candidate = self.store.persist_review_candidate(task, consumer, result)
        audits = sorted(
            (
                r
                for r in runs
                if r.role is AgentRole.AUDIT and r.parent_agent_run_id == consumer.id
            ),
            key=lambda r: (r.turn_attempt, r.id),
        )
        if not audits:
            return _NextAudit(
                consumer.proposal_revision,
                0,
                consumer.id,
                result,
                candidate["id"],
                candidate["candidate_digest"],
            )
        audit = audits[-1]
        technical = self._technical_state(task, audit, cycles)
        if technical is not None:
            if (
                isinstance(technical, _Deferred)
                or technical.status != "failed_retryable"
            ):
                return technical
            return _NextAudit(
                consumer.proposal_revision,
                audit.turn_attempt + 1,
                consumer.id,
                result,
                candidate["id"],
                candidate["candidate_digest"],
                technical.error.code if _is_waiting_failure(technical.error) else "",
            )
        try:
            review_result = self._audit_result(audit)
        except ValueError:
            return self._run_terminal(
                audit, "failed_terminal",
                AgentError(code="completed_audit_result_unavailable", retryable=False), cycles, result,
            )
        if review_result.outcome is AuditOutcome.FAILED:
            return self._run_terminal(
                audit,
                _failure_status(review_result.error),
                review_result.error,
                cycles,
                result,
                review_result,
            )
        if (
            review_result.proposal_revision != consumer.proposal_revision
            or review_result.candidate_digest != candidate["candidate_digest"]
        ):
            return self._run_terminal(
                audit,
                "failed_terminal",
                AgentError(code="candidate_review_binding_invalid", retryable=False),
                cycles,
                result,
                review_result,
            )
        prior_review = self.store.get_candidate_review_for_audit_run(audit.id)
        if candidate["invalidated_at"]:
            execution = self.store.get_candidate_execution(candidate["id"])
            if execution and execution["result_json"]:
                result_after_execution = SystemExecutionResult.model_validate_json(execution["result_json"])
                if result_after_execution.error.code == "business_state_changed" and prior_review:
                    return self._business_revision(_NextExecution(consumer, audit, result, review_result, candidate["id"], prior_review["id"], cycles))
            return self._run_terminal(audit, "failed_terminal", AgentError(code="review_candidate_invalidated", retryable=False), cycles, result, review_result)
        review = self.store.record_candidate_review(
            candidate["id"], audit.id, review_result
        )
        stage_cycles = self._feedback_cycles_by_runs(
            runs, stage_index=result.stage_index
        )
        if review_result.outcome in (AuditOutcome.RETURN, AuditOutcome.REJECT):
            if stage_cycles > MAX_CONTENT_FEEDBACK_CYCLES:
                return self._run_terminal(
                    audit,
                    "failed_terminal",
                    AgentError(code="audit_revision_exhausted", retryable=False),
                    cycles,
                    result,
                    review_result,
                )
            return _NextConsumer(
                consumer.proposal_revision + 1,
                audit.id,
                review_result.feedback,
                stage_index=result.stage_index,
                predecessor_review_id=result.predecessor_review_id,
            )
        current = self.store.current_reviewed_candidate(
            task.id, task.execution_generation
        )
        if current is None:
            # A supplement is preserved and the standard user-input rerun path
            # creates a new generation; this old candidate cannot execute.
            return self._run_terminal(
                audit,
                "failed_terminal",
                AgentError(code="review_candidate_invalidated", retryable=False),
                cycles,
                result,
                review_result,
            )
        state = _NextExecution(
            consumer,
            audit,
            result,
            review_result,
            candidate["id"],
            review["id"],
            cycles,
        )
        if (
            result.outcome is ConsumerOutcome.NEEDS_HUMAN
            and not current["selection_id"]
        ):
            return self._candidate_terminal(state, "needs_human", review_result.error)
        if result.outcome is ConsumerOutcome.NO_ACTION:
            return self._candidate_terminal(state, "no_action", review_result.error)
        execution = self.store.get_candidate_execution(candidate["id"])
        if execution and execution["status"] in (
            "done",
            "skipped",
            "failed",
        ):
            executed = SystemExecutionResult.model_validate_json(
                execution["result_json"]
            )
            if executed.outcome == "executed" and result.continue_after_execution:
                if result.stage_index + 1 >= MAX_EXECUTION_STAGES:
                    return self._candidate_terminal(
                        state,
                        "failed_terminal",
                        AgentError(
                            code="execution_stage_limit_reached", retryable=False
                        ),
                        executed,
                    )
                return _NextConsumer(
                    consumer.proposal_revision + 1,
                    audit.id,
                    None,
                    stage_index=result.stage_index + 1,
                    predecessor_review_id=review["id"],
                )
            status = (
                _failure_status(executed.error)
                if executed.outcome == "failed"
                else executed.outcome
            )
            return self._candidate_terminal(state, status, executed.error, executed)
        return state

    def _with_persisted_evidence(self, task, context, state):
        receipts = {receipt.receipt_id: receipt for receipt in context.prior_receipts}
        for row in self.store.list_verified_candidate_actions(task.id):
            receipts[row["external_action_key"]] = PriorReceipt(
                receipt_id=row["external_action_key"], operation=row["operation"],
                summary=json.dumps({"stage_index": row["stage_index"],
                    "action_identity": row["action_identity"],
                    "target": json.loads(row["target_identifiers_json"]),
                    "result": json.loads(row["provider_result_json"])}, ensure_ascii=False),
                completed=True,
            )
        changes = context.business_state_changes
        parent_id = state.parent_run_id
        review = self.store.get_candidate_review_for_audit_run(parent_id) if parent_id else None
        execution = self.store.get_candidate_execution(review["candidate_id"]) if review else None
        if execution and execution["result_json"]:
            result = SystemExecutionResult.model_validate_json(execution["result_json"])
            if result.error.code == "business_state_changed" and result.external_result:
                changes = (*changes, result.external_result.live_result_reference)
        return replace(context,
            prior_receipts=tuple(receipts.values()),
            prior_human_decisions=tuple(self.store.list_human_decision_evidence(task.id)),
            business_state_changes=changes,
        )

    @staticmethod
    def _business_revision(state: _NextExecution) -> _NextConsumer:
        return _NextConsumer(
            state.consumer_run.proposal_revision + 1,
            state.audit_run.id,
            None,
            stage_index=state.consumer_result.stage_index,
            predecessor_review_id=state.consumer_result.predecessor_review_id,
        )

    def _stage_for_parent(self, parent: AgentRun | None) -> dict[str, object]:
        if parent is None:
            return {"stage_index": 0, "predecessor_review_id": None}
        previous_candidate = self._consumer_result(
            self.store.get_agent_run(parent.parent_agent_run_id, load_events=False)
        )
        review = self.store.get_candidate_review_for_audit_run(parent.id)
        execution = self.store.get_candidate_execution(review["candidate_id"]) if review else None
        if (self._audit_result(parent).outcome is AuditOutcome.APPROVE
            and previous_candidate.continue_after_execution
            and execution and execution["status"] == "done"):
            review_id = review["id"]
            return {
                "stage_index": previous_candidate.stage_index + 1,
                "predecessor_review_id": review_id,
            }
        return {
            "stage_index": previous_candidate.stage_index,
            "predecessor_review_id": previous_candidate.predecessor_review_id,
        }

    def _technical_state(self, task: ReplyTask, run: AgentRun, cycles: int):
        if run.status == "completed":
            return None
        if run.status == "running":
            if self.store.agent_run_lease_is_active(run.id):
                return _Deferred(run, "agent_run_active", cycles)
            self.store.fail_expired_agent_run(
                run.id,
                {"code": f"{run.role.value}_lease_expired", "retryable": True},
                expected_execution_generation=task.execution_generation,
            )
            run = self.store.get_agent_run(run.id, load_events=False)
        error = _run_error(run)
        if error.code == "provider_risk_rejected" or error.source_code == "provider_risk_rejected":
            return self._run_terminal(run, "failed_terminal", error.model_copy(update={"retryable":False}), cycles)
        if error.code in EXTERNAL_DEPENDENCY_WAIT_ERRORS and error.retryable:
            if task.error != error.code:
                return _Deferred(
                    run,
                    error.code,
                    cycles,
                    error.source_code or error.code,
                    error,
                )
            return self._run_terminal(run, "failed_retryable", error, cycles)
        if error.retryable and not error.authorization_required:
            runs = self.store.list_agent_runs_for_task_generation(task.id, task.execution_generation, load_events=False)
            failed_turns = _consecutive_failed_turns([
                prior for prior in runs
                if prior.role is run.role and prior.proposal_revision == run.proposal_revision
            ])
            if failed_turns >= MAX_CONSECUTIVE_FAILED_TURNS:
                return self._retry_exhausted_result(
                    task, role=run.role, proposal_revision=run.proposal_revision,
                )
        if (
            error.retryable
            and _is_waiting_failure(error)
            and not _retryable_route_error_can_resume(task, error)
        ):
            return _Deferred(run, error.code, cycles)
        return self._run_terminal(run, _failure_status(error), error, cycles)

    @staticmethod
    def _run_terminal(run, status, error, cycles, consumer=None, audit=None):
        source = error.source_code or error.code
        summary = audit.summary if audit else consumer.summary if consumer else source
        if error.code:
            summary = f"{source}: {summary}" if source != summary else summary
        return OrchestrationResult(
            status,
            run.id,
            run.role,
            summary,
            error,
            cycles,
            consumer_result=consumer,
            audit_result=audit,
            feedback=audit.feedback if audit else None,
        )

    @staticmethod
    def _candidate_terminal(state, status, error, execution=None):
        return OrchestrationResult(
            status,
            state.audit_run.id,
            AgentRole.AUDIT,
            execution.summary if execution else state.consumer_result.summary,
            error,
            state.feedback_cycles,
            consumer_result=state.consumer_result,
            audit_result=state.audit_result,
            execution_result=execution,
            candidate_id=state.candidate_id,
            review_id=state.review_id,
        )

    def _feedback_cycles(self, task):
        return self._feedback_cycles_by_runs(
            self.store.list_agent_runs_for_task_generation(
                task.id, task.execution_generation, load_events=False
            )
        )

    def _feedback_cycles_by_runs(self, runs, stage_index=None):
        count = 0
        by_id = {r.id: r for r in runs}
        for run in runs:
            if run.role is not AgentRole.AUDIT or run.status != "completed":
                continue
            try:
                review = self._audit_result(run)
                parent = by_id.get(run.parent_agent_run_id)
                if review.outcome in (AuditOutcome.RETURN, AuditOutcome.REJECT) and (
                    stage_index is None
                    or (parent and self._consumer_result(parent).stage_index == stage_index)
                ):
                    count += 1
            except ValueError:
                continue
        return count

    def _retry_exhausted_result(
        self,
        task: ReplyTask,
        *,
        role: AgentRole,
        proposal_revision: int,
    ) -> OrchestrationResult:
        runs = self.store.list_agent_runs_for_task_generation(
            task.id,
            task.execution_generation,
            load_events=False,
        )
        latest = next(
            (
                run
                for run in reversed(runs)
                if run.role is role and run.proposal_revision == proposal_revision
            ),
            None,
        )
        if latest is None:
            raise RuntimeError("agent retry exhausted without a persisted role turn")
        # Keep the persisted run's real failure code.  The retry budget is an
        # orchestration detail and must not hide the source failure (for
        # example, a live OKR read failure) behind a generic wrapper.
        underlying = _run_error(latest)
        code = underlying.code or f"{role.value}_retry_exhausted"
        # The Agent's own wording is the only concrete cause of a
        # `agent_reported_failure`; keep it beside the code.
        cause = (
            f"{code} ({underlying.source_code})"
            if underlying.source_code and underlying.source_code != code
            else code
        )
        summary = code
        if code != f"{role.value}_retry_exhausted":
            summary = f"{cause}; {role.value} retry attempts exhausted"
        diagnostics = {
            "stage": underlying.stage,
            "source": underlying.source,
            "source_code": underlying.source_code,
        }
        failed_turns = _consecutive_failed_turns(
            [
                run
                for run in runs
                if run.role is role and run.proposal_revision == proposal_revision
            ]
        )
        if (
            underlying.retryable
            and not underlying.authorization_required
            and failed_turns >= MAX_CONSECUTIVE_FAILED_TURNS
        ):
            # Every pass reached the same wall. Scheduled executions and email
            # tasks hand the attempt back when they defer, so nothing else
            # counts these passes: 2026-09-26 an Audit turn on reply task
            # 385880 was run 310 times in one generation, each refusing to
            # resubmit an already executed proposal.
            return OrchestrationResult(
                status="failed_terminal",
                final_run_id=latest.id,
                final_role=role,
                summary=(
                    f"{cause}; {role.value} turns failed {failed_turns} times in a "
                    "row, retries stopped"
                ),
                error=AgentError(code=code, retryable=False, **diagnostics),
                feedback_cycles=self._feedback_cycles(task),
            )
        # Inner turn retries are only a transport/runtime budget. Preserve
        # the persisted error's retryability so the task-level worker can
        # apply the single exponential-backoff ceiling consistently.
        retry_after = 0.0
        if underlying.retryable and not underlying.authorization_required:
            retry_after = _next_pass_delay_seconds(failed_turns)
        return OrchestrationResult(
            status=_failure_status(underlying),
            final_run_id=latest.id,
            final_role=role,
            summary=summary,
            error=AgentError(
                code=code,
                retryable=underlying.retryable,
                authorization_required=underlying.authorization_required,
                **diagnostics,
            ),
            feedback_cycles=self._feedback_cycles(task),
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def _deferred_result(state: _Deferred) -> OrchestrationResult:
        run = state.run
        summary = state.detail or state.code
        if state.code == "confirmation_required":
            summary = (
                "外部提供方拒绝执行：需要运行时确认；这不是业务决策，未执行外部动作。"
            )
        return OrchestrationResult(
            status=_failure_status(state.source_error) if state.source_error else "failed_retryable",
            final_run_id=run.id if run is not None else 0,
            final_role=run.role if run is not None else AgentRole.CONSUMER,
            summary=summary,
            error=state.source_error or AgentError(
                code=state.code,
                retryable=True,
            ),
            feedback_cycles=state.feedback_cycles,
        )


def _validate_refreshed_task_context(
    task: ReplyTask, context: AgentTaskContext
) -> None:
    if not isinstance(context, AgentTaskContext) or (
        context.task_id,
        context.channel,
        context.conversation_id,
        context.trigger_message_id,
    ) != (task.id, task.channel, task.conversation_id, task.trigger_message_id):
        raise ValueError("refreshed agent context does not match reply task")


def _context_refresh_error(exc: Exception) -> AgentError:
    from app.dws_client import DwsError
    return AgentError(
        code="agent_context_refresh_failed",
        retryable=exc.retryable_external_dependency if isinstance(exc, DwsError) else True,
        authorization_required=isinstance(exc, DwsError) and (exc.needs_authorization or exc.needs_login),
        stage="context_refresh",
        source=str(getattr(exc, "server_key", "") or type(exc).__name__),
        source_code=str(getattr(exc, "code", "") or ""),
    )


def _context_refresh_failure_detail(exc: Exception) -> str:
    from app.agent_turn_runner import _runtime_failure_detail
    return f"agent_context_refresh_failed: {_runtime_failure_detail(exc)}"


def _operation_id(task: ReplyTask, revision: int) -> str:
    return f"agent-task:{task.id}:{task.execution_generation}:proposal:{revision}"


def _failure_status(error: AgentError) -> str:
    return "failed_retryable" if error.retryable else "failed_terminal"


def _run_error(run: AgentRun) -> AgentError:
    try:
        payload = json.loads(run.structured_error_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    # Never hide the run's concrete failure behind a catch-all code. A
    # missing/malformed code is classified as an execution failure with
    # bounded diagnostics by the caller.
    code = str(payload.get("code") or "execution_failed")
    return AgentError.model_validate(
        {
            "code": code,
            "retryable": payload.get("retryable") is True,
            "authorization_required": payload.get("authorization_required") is True,
            "stage": str(payload.get("stage") or ""),
            "source": str(payload.get("source") or ""),
            "source_code": str(payload.get("source_code") or ""),
            "session_continuable": payload.get("session_continuable") is True,
        }
    )


def _is_waiting_failure(error: AgentError) -> bool:
    """A failure that waits for the runtime or a person, not the Agent's fault."""
    return (
        error.authorization_required
        or error.code in EXTERNAL_DEPENDENCY_WAIT_ERRORS
        or error.code
        in {
            "runtime_execution_failed",
            "runtime_provider_unreachable",
            "runtime_provider_auth_failed",
        }
        or is_codex_provider_recovery_code(error.code)
    )


def _consecutive_failed_turns(role_runs: list[AgentRun]) -> int:
    """Count the failed turns a role has run in a row on one proposal revision."""
    count = 0
    newest_first = sorted(
        role_runs, key=lambda item: (item.turn_attempt, item.id), reverse=True
    )
    for run in newest_first:
        if (
            run.status != "failed"
            or _run_error(run).code in EXTERNAL_DEPENDENCY_WAIT_ERRORS
        ):
            break
        count += 1
    return count


def _next_pass_delay_seconds(failed_turns: int) -> float:
    """The wait before the next pass, on the schedule the DingTalk worker uses."""
    # Imported here: the worker imports this module.
    from app.worker import (
        REPLY_TASK_RETRY_BASE_DELAY_SECONDS,
        REPLY_TASK_RETRY_MAX_DELAY_SECONDS,
    )

    passes = max(failed_turns // MAX_ROLE_ATTEMPTS_PER_PROCESS, 1)
    return retry_delay_seconds(
        REPLY_TASK_RETRY_BASE_DELAY_SECONDS,
        passes - 1,
        max_delay_seconds=REPLY_TASK_RETRY_MAX_DELAY_SECONDS,
    )


def _retryable_route_error_can_resume(task: ReplyTask, error: AgentError) -> bool:
    """A deferred error or an explicit safe recovery permits one fresh turn."""
    return task.error == error.code or bool(task.recovery_code)
