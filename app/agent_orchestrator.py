from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.agent_context import AgentTaskContext, AuditTurnContext
from app.agent_contracts import (
    AuditAgentResult,
    AuditFeedback,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
    ConsumerProposal,
    DecisionOption,
)
from app.agent_result import AgentError, ResultParseError, SideEffectState
from app.agent_skill_usage import LoadedSkillReceipt, loaded_skill_receipts
from app.agent_turn_runner import AgentTurnRunResult
from app.codex_capacity import is_codex_provider_recovery_code
from app.store import AgentRole, AgentRun, AutoReplyStore, ReplyTask

MAX_CONTENT_FEEDBACK_CYCLES = 2
MAX_TURNS_PER_PROCESS = 32
MAX_ROLE_ATTEMPTS_PER_PROCESS = 2


def _bounded_fact_finding_feedback(
    result: ConsumerAgentResult,
) -> AuditFeedback | None:
    """Ask Consumer to materialize its own safe fact-finding option as a proposal."""
    if result.outcome is not ConsumerOutcome.NEEDS_HUMAN:
        return None
    fact_markers = (
        "询问", "确认", "调研", "核实", "fact-finding", "fact finding", "inquir", "gather"
    )
    boundary_markers = (
        "不构成采购", "不构成预算", "不构成合作", "不代表公司作出采购",
        "不代表公司作出预算", "不代表公司作出合作", "不作出采购",
        "不作出预算", "不作出合作", "不得确认采购", "不得确认报价",
        "不得确认订单", "不得确认预算", "不得确认合作", "不得下单", "不得付款",
        "no purchase", "no budget", "no partnership", "without.*commit",
    )
    commitment_terms = ("采购", "预算", "合作", "下单", "付款", "承诺", "purchase", "budget", "partnership", "commit")
    for option in result.decision_options:
        text = " ".join((option.label, option.instruction, option.consequence)).lower()
        has_boundary = any(marker.lower() in text for marker in boundary_markers) or (
            "不得" in text and any(term.lower() in text for term in commitment_terms)
        )
        if any(marker.lower() in text for marker in fact_markers) and has_boundary:
            return AuditFeedback(
                rule=(
                    "A decision option already defines a bounded fact-finding inquiry "
                    "with no purchase, budget, or partnership commitment."
                ),
                observation=(
                    "The candidate's own option states the facts to gather and the "
                    "prohibition on committing or spending."
                ),
                requested_revision=(
                    "Regenerate the same task as an executable proposal for that "
                    "bounded inquiry. Put the risk boundary in the outgoing message "
                    "itself; do not ask Derek to choose between options and do not "
                    "authorize a quote, order, agreement, or spend."
                ),
            )
    return None


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
        frozen_delivery_retry: bool = False,
    ) -> AgentTurnRunResult[AuditAgentResult]: ...

    def recover(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
    ) -> AgentTurnRunResult[AuditAgentResult]: ...

    def execute_recovery(
        self,
        task: ReplyTask,
        context: AuditTurnContext,
        *,
        run: AgentRun,
    ) -> AgentTurnRunResult[AuditAgentResult]: ...


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


@dataclass(frozen=True)
class _NextConsumer:
    proposal_revision: int
    parent_run_id: int | None
    feedback: AuditFeedback | None
    authorization_error_code: str = ""
    deferred_error_code: str = ""


@dataclass(frozen=True)
class _NextAudit:
    proposal_revision: int
    turn_attempt: int
    parent_run_id: int
    proposal: ConsumerProposal | None
    authorization_error_code: str = ""
    deferred_error_code: str = ""
    frozen_delivery_retry: bool = False


@dataclass(frozen=True)
class _RecoverAudit:
    run: AgentRun
    proposal: ConsumerProposal


@dataclass(frozen=True)
class _ExecuteAuditRecovery:
    run: AgentRun
    proposal: ConsumerProposal


@dataclass(frozen=True)
class _Deferred:
    run: AgentRun | None
    code: str
    feedback_cycles: int
    authorization_required: bool = False
    detail: str = ""


class AgentOrchestrator:
    def __init__(
        self,
        *,
        store: AutoReplyStore,
        consumer: ConsumerRunner,
        audit: AuditRunner,
    ) -> None:
        self.store = store
        self.consumer = consumer
        self.audit = audit

    def process(
        self,
        task: ReplyTask,
        context: AgentTaskContext,
        *,
        refresh_context: Callable[[], AgentTaskContext],
    ) -> OrchestrationResult:
        if context.task_id != task.id:
            raise ValueError("agent context task does not match reply task")
        role_attempts: dict[tuple[AgentRole, int, str], int] = {}
        for _ in range(MAX_TURNS_PER_PROCESS):
            state = self._derive_state(task)
            if isinstance(state, OrchestrationResult):
                return state
            if isinstance(state, _Deferred):
                return self._deferred_result(state)
            try:
                if isinstance(state, _NextConsumer):
                    attempt_key = (AgentRole.CONSUMER, state.proposal_revision, "run")
                    max_attempts = (
                        1
                        if (
                            state.authorization_error_code
                            or state.deferred_error_code
                        )
                        else MAX_ROLE_ATTEMPTS_PER_PROCESS
                    )
                    if role_attempts.get(attempt_key, 0) >= max_attempts:
                        if not (
                            state.authorization_error_code
                            or state.deferred_error_code
                        ):
                            return self._retry_exhausted_result(
                                task,
                                role=AgentRole.CONSUMER,
                                proposal_revision=state.proposal_revision,
                            )
                        return self._deferred_result(
                            _Deferred(
                                run=None,
                                code=(
                                    state.authorization_error_code
                                    or state.deferred_error_code
                                    or "consumer_retry_deferred"
                                ),
                                feedback_cycles=self._feedback_cycles(task),
                                authorization_required=bool(
                                    state.authorization_error_code
                                ),
                            )
                        )
                    role_attempts[attempt_key] = role_attempts.get(attempt_key, 0) + 1
                    self.consumer.run(
                        task,
                        context,
                        proposal_revision=state.proposal_revision,
                        parent_agent_run_id=state.parent_run_id,
                        feedback=state.feedback,
                    )
                else:
                    if isinstance(state, (_RecoverAudit, _ExecuteAuditRecovery)):
                        phase = "reconcile" if isinstance(state, _RecoverAudit) else "execute"
                        attempt_key = (AgentRole.AUDIT, state.run.proposal_revision, phase)
                        if role_attempts.get(attempt_key, 0) >= 1:
                            result = _failed_audit_result(
                                state.run,
                                AuditOutcome.UNKNOWN,
                                _run_error(state.run),
                            )
                            return _audit_terminal(
                                "unknown",
                                state.run,
                                result,
                                self._feedback_cycles(task),
                            )
                    else:
                        attempt_key = (AgentRole.AUDIT, state.proposal_revision, "run")
                    max_attempts = (
                        1
                        if isinstance(state, (_RecoverAudit, _ExecuteAuditRecovery))
                        or state.authorization_error_code
                        or state.deferred_error_code
                        else MAX_ROLE_ATTEMPTS_PER_PROCESS
                    )
                    if role_attempts.get(attempt_key, 0) >= max_attempts:
                        if not (
                            state.authorization_error_code
                            or state.deferred_error_code
                        ):
                            return self._retry_exhausted_result(
                                task,
                                role=AgentRole.AUDIT,
                                proposal_revision=state.proposal_revision,
                            )
                        return self._deferred_result(
                            _Deferred(
                                run=None,
                                code=(
                                    state.authorization_error_code
                                    or state.deferred_error_code
                                    or "audit_retry_deferred"
                                ),
                                feedback_cycles=self._feedback_cycles(task),
                                authorization_required=bool(
                                    state.authorization_error_code
                                ),
                            )
                        )
                    role_attempts[attempt_key] = role_attempts.get(attempt_key, 0) + 1
                    assert state.proposal is not None
                    try:
                        audit_task_context = refresh_context()
                        if audit_task_context.task_id != task.id:
                            raise ValueError(
                                "refreshed agent context does not match reply task"
                            )
                    except Exception as exc:
                        return self._deferred_result(
                            _Deferred(
                                run=None,
                                code="agent_context_refresh_failed",
                                feedback_cycles=self._feedback_cycles(task),
                                detail=_context_refresh_failure_detail(exc),
                            )
                        )
                    if isinstance(state, _ExecuteAuditRecovery):
                        consumer_skills = self._consumer_skills(
                            task,
                            state.run.parent_agent_run_id,
                            state.run.proposal_revision,
                        )
                        self.audit.execute_recovery(
                            task,
                            AuditTurnContext(
                                task=audit_task_context,
                                proposal_revision=state.run.proposal_revision,
                                operation_id=state.run.operation_id,
                                proposal=state.proposal,
                                audit_rules="",
                                consumer_skills=consumer_skills,
                            ),
                            run=state.run,
                        )
                    elif isinstance(state, _RecoverAudit):
                        consumer_skills = self._consumer_skills(
                            task,
                            state.run.parent_agent_run_id,
                            state.run.proposal_revision,
                        )
                        self.audit.recover(
                            task,
                            AuditTurnContext(
                                task=audit_task_context,
                                proposal_revision=state.run.proposal_revision,
                                operation_id=state.run.operation_id,
                                proposal=state.proposal,
                                audit_rules="",
                                consumer_skills=consumer_skills,
                            ),
                            run=state.run,
                        )
                    else:
                        consumer_skills = self._consumer_skills(
                            task,
                            state.parent_run_id,
                            state.proposal_revision,
                        )
                        audit_context = AuditTurnContext(
                            task=audit_task_context,
                            proposal_revision=state.proposal_revision,
                            operation_id=_operation_id(task, state.proposal_revision),
                            proposal=state.proposal,
                            audit_rules="",
                            consumer_skills=consumer_skills,
                        )
                        run_kwargs = {
                            "turn_attempt": state.turn_attempt,
                            "parent_agent_run_id": state.parent_run_id,
                        }
                        if state.frozen_delivery_retry:
                            run_kwargs["frozen_delivery_retry"] = True
                        self.audit.run(task, audit_context, **run_kwargs)
            except (RuntimeError, ResultParseError) as exc:
                if str(exc) in {
                    "agent_run_unavailable",
                    "audit_consumer_parent_invalid",
                    "codex_session_locked",
                }:
                    return self._deferred_result(
                        _Deferred(
                            run=None,
                            code=str(exc),
                            feedback_cycles=self._feedback_cycles(task),
                        )
                    )
                next_state = self._derive_state(task)
                if isinstance(
                    next_state,
                    (_NextConsumer, _NextAudit, _RecoverAudit, _ExecuteAuditRecovery),
                ):
                    continue
                if isinstance(next_state, _Deferred):
                    return self._deferred_result(next_state)
                return next_state
        return self._deferred_result(
            _Deferred(
                run=None,
                code="agent_turn_limit_reached",
                feedback_cycles=self._feedback_cycles(task),
            )
        )

    def _consumer_skills(
        self,
        task: ReplyTask,
        parent_run_id: int | None,
        proposal_revision: int,
    ) -> tuple[LoadedSkillReceipt, ...]:
        if parent_run_id is None:
            raise RuntimeError("audit_consumer_parent_invalid")
        parent = self.store.get_agent_run(parent_run_id)
        if (
            parent is None
            or parent.reply_task_id != task.id
            or parent.execution_generation != task.execution_generation
            or parent.role is not AgentRole.CONSUMER
            or parent.proposal_revision != proposal_revision
            or parent.status != "completed"
        ):
            raise RuntimeError("audit_consumer_parent_invalid")
        return loaded_skill_receipts(parent.tool_events)

    def _derive_state(
        self,
        task: ReplyTask,
    ) -> OrchestrationResult | _NextConsumer | _NextAudit | _RecoverAudit | _ExecuteAuditRecovery | _Deferred:
        runs = self.store.list_agent_runs_for_task_generation(
            task.id,
            task.execution_generation,
        )
        by_revision: dict[int, list[AgentRun]] = {}
        for run in runs:
            by_revision.setdefault(run.proposal_revision, []).append(run)
        highest_materialized_revision = max(
            (
                run.proposal_revision
                for run in runs
                if run.role is AgentRole.CONSUMER
            ),
            default=0,
        )

        frozen_delivery_retry = (
            task.recovery_code == "legacy_sessionless_audit_delivery_replay"
        )
        # A frozen recovery is a delivery retry, not a new content-review loop.
        # The preserved Consumer result can legitimately be revision 1 or 2 from
        # the old workflow.  Starting from revision 0 would invite a fresh
        # Consumer decision and silently turn a resend into ``no_action``.
        frozen_consumer: AgentRun | None = None
        if frozen_delivery_retry:
            frozen_candidates = sorted(
                (
                    run
                    for run in runs
                    if run.role is AgentRole.CONSUMER and run.status == "completed"
                ),
                key=lambda run: (run.proposal_revision, run.turn_attempt, run.id),
                reverse=True,
            )
            for candidate in frozen_candidates:
                state = self._consumer_state(
                    task, candidate, candidate.proposal_revision
                )
                if isinstance(state, ConsumerAgentResult) and state.proposal is not None:
                    frozen_consumer = candidate
                    break
            if frozen_consumer is None:
                return _Deferred(
                    None,
                    "frozen_delivery_replay_proposal_missing",
                    0,
                )

        revisions = (
            (frozen_consumer.proposal_revision,)
            if frozen_consumer is not None
            else range(MAX_CONTENT_FEEDBACK_CYCLES + 1)
        )
        for revision in revisions:
            revision_runs = by_revision.get(revision, [])
            consumer_turns = sorted(
                (run for run in revision_runs if run.role is AgentRole.CONSUMER),
                key=lambda run: (run.turn_attempt, run.id),
            )
            consumer = consumer_turns[-1] if consumer_turns else None
            if consumer is None:
                if revision == 0:
                    return _NextConsumer(0, None, None)
                previous_audit = self._revision_feedback_run(by_revision, revision - 1)
                if previous_audit is None:
                    return _Deferred(None, "agent_turn_state_incomplete", revision - 1)
                previous_result = _audit_result(previous_audit)
                if previous_result.feedback is None:
                    return _Deferred(
                        previous_audit,
                        "agent_feedback_missing",
                        revision - 1,
                    )
                return _NextConsumer(
                    revision,
                    previous_audit.id,
                    previous_result.feedback,
                )
            consumer_state = self._consumer_state(task, consumer, revision)
            # A route/process failure can leave a durable proposal from an
            # earlier Consumer run in the same revision. Reuse that proposal
            # and continue with Audit instead of regenerating Consumer output.
            if (
                isinstance(consumer_state, ConsumerAgentResult)
                and consumer_state.outcome is ConsumerOutcome.FAILED
                and consumer_state.error.code
                in {"runtime_route_unavailable", "codex_process_failed", "service_restart_before_effect"}
            ):
                prior = [
                    run for run in consumer_turns[:-1]
                    if run.status == "completed"
                ]
                for candidate in reversed(prior):
                    candidate_state = self._consumer_state(task, candidate, revision)
                    if (
                        isinstance(candidate_state, ConsumerAgentResult)
                        and candidate_state.proposal is not None
                    ):
                        consumer = candidate
                        consumer_state = candidate_state
                        break
            if not isinstance(consumer_state, ConsumerAgentResult):
                return consumer_state
            if consumer_state.outcome is ConsumerOutcome.NO_ACTION:
                return _consumer_terminal("no_action", consumer, consumer_state, revision)
            if consumer_state.outcome is ConsumerOutcome.NEEDS_HUMAN:
                bounded_feedback = _bounded_fact_finding_feedback(consumer_state)
                if bounded_feedback is not None:
                    return _NextConsumer(revision, None, bounded_feedback)
                return _consumer_terminal(
                    "needs_human", consumer, consumer_state, revision
                )
            if consumer_state.outcome is ConsumerOutcome.FAILED:
                return _consumer_terminal(
                    _failure_status(consumer_state.error),
                    consumer,
                    consumer_state,
                    revision,
                )
            assert consumer_state.proposal is not None

            audits = sorted(
                (run for run in revision_runs if run.role is AgentRole.AUDIT),
                key=lambda run: (run.turn_attempt, run.id),
            )
            if not audits:
                return _NextAudit(
                    revision,
                    0,
                    consumer.id,
                    consumer_state.proposal,
                    frozen_delivery_retry=frozen_delivery_retry,
                )
            latest = audits[-1]
            if latest.status == "unknown":
                if latest.final_result_json:
                    reconciled = _audit_result(latest)
                    if reconciled.outcome is AuditOutcome.RECONCILED:
                        if any(
                            entry.disposition.value == "superseded"
                            for entry in reconciled.reconciliation
                        ):
                            return self._finalize_reconciled_audit(
                                latest,
                                reconciled,
                                feedback_cycles=revision,
                                needs_human=False,
                                superseded=True,
                            )
                        if any(
                            entry.disposition.value == "ambiguous"
                            for entry in reconciled.reconciliation
                        ):
                            return self._finalize_reconciled_audit(
                                latest,
                                reconciled,
                                feedback_cycles=revision,
                                needs_human=True,
                            )
                        if any(
                            entry.disposition.value == "absent"
                            for entry in reconciled.reconciliation
                        ):
                            return _ExecuteAuditRecovery(
                                latest, consumer_state.proposal
                            )
                        return self._finalize_reconciled_audit(
                            latest,
                            reconciled,
                            feedback_cycles=revision,
                            needs_human=(
                                not reconciled.reconciliation
                                and latest.side_effect_state
                                != SideEffectState.CONFIRMED.value
                            ),
                        )
                return _RecoverAudit(latest, consumer_state.proposal)
            audit_state = self._audit_state(task, latest, revision)
            if isinstance(audit_state, _NextAudit):
                return _NextAudit(
                    revision,
                    audit_state.turn_attempt,
                    consumer.id,
                    consumer_state.proposal,
                    audit_state.authorization_error_code,
                    audit_state.deferred_error_code,
                    frozen_delivery_retry=frozen_delivery_retry,
                )
            if not isinstance(audit_state, AuditAgentResult):
                return audit_state
            if audit_state.outcome is AuditOutcome.EXECUTED:
                if revision < highest_materialized_revision:
                    revision += 1
                    continue
                return _audit_terminal("executed", latest, audit_state, revision)
            if audit_state.outcome is AuditOutcome.NEEDS_HUMAN:
                # A normal Audit turn can occasionally emit the recovery-only
                # sentinel after a malformed/partial model response even though
                # no recovery is being performed. That is not a real management
                # decision. Give Audit one bounded fresh attempt before exposing
                # needs_human; the per-process role-attempt cap prevents loops.
                if (
                    audit_state.error.code == "audit_recovery_ambiguous"
                    and latest.side_effect_state == SideEffectState.NONE.value
                ):
                    return _NextAudit(
                        revision,
                        latest.turn_attempt + 1,
                        consumer.id,
                        consumer_state.proposal,
                    )
                return _audit_terminal("needs_human", latest, audit_state, revision)
            if audit_state.outcome is AuditOutcome.DRY_RUN:
                return _audit_terminal("dry_run", latest, audit_state, revision)
            if audit_state.outcome is AuditOutcome.UNKNOWN:
                return _audit_terminal("unknown", latest, audit_state, revision)
            if audit_state.outcome is AuditOutcome.FAILED:
                return _audit_terminal(
                    _failure_status(audit_state.error),
                    latest,
                    audit_state,
                    revision,
                )
            if frozen_delivery_retry:
                # The frozen prompt forbids content revisions.  If a model still
                # asks for one, retry Audit on the same persisted proposal; never
                # hand the decision back to Consumer.
                return _NextAudit(
                    revision,
                    latest.turn_attempt + 1,
                    consumer.id,
                    consumer_state.proposal,
                    frozen_delivery_retry=True,
                )
            if revision == MAX_CONTENT_FEEDBACK_CYCLES:
                exhausted = _failed_audit_result(
                    latest,
                    AuditOutcome.FAILED,
                    AgentError(code="audit_revision_exhausted", retryable=False),
                )
                return _audit_terminal(
                    "failed_terminal",
                    latest,
                    exhausted,
                    MAX_CONTENT_FEEDBACK_CYCLES,
                )
        return _Deferred(None, "agent_turn_state_incomplete", 0)

    def _finalize_reconciled_audit(
        self,
        run: AgentRun,
        reconciled: AuditAgentResult,
        *,
        feedback_cycles: int,
        needs_human: bool,
        superseded: bool = False,
    ) -> OrchestrationResult | _Deferred:
        owner = f"agent-orchestrator-reconciled-{run.id}"
        claim = self.store.claim_unknown_agent_run(run.id, owner=owner)
        if not claim.claimed:
            return _Deferred(run, "agent_run_unavailable", feedback_cycles)
        if needs_human:
            result = _failed_audit_result(
                claim.run,
                AuditOutcome.NEEDS_HUMAN,
                AgentError(code="audit_recovery_ambiguous", retryable=False),
            )
            status = "needs_human"
            side_effect_state = SideEffectState.UNKNOWN.value
        else:
            summary = reconciled.summary
            side_effect_state = SideEffectState.CONFIRMED.value
            if superseded:
                summary = (
                    "A later target-scoped message resolved the trigger; the "
                    "unconfirmed prior action was not replayed."
                )
                side_effect_state = SideEffectState.NONE.value
            result = AuditAgentResult(
                outcome=AuditOutcome.EXECUTED,
                summary=summary,
                proposal_revision=run.proposal_revision,
                side_effect_state=SideEffectState(side_effect_state),
                feedback=None,
                external_result={
                    "operation_id": run.operation_id,
                    "verification_summary": summary,
                    "live_result_reference": {
                        "reconciliation": [
                            entry.model_dump(mode="json")
                            for entry in reconciled.reconciliation
                        ]
                    },
                },
                reconciliation=(),
                error=AgentError(),
            )
            status = "executed"
        completed = self.store.update_agent_run_projection(
            run.id,
            status="completed" if status == "executed" else "failed",
            final_result_json=json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
            owner=owner,
            side_effect_state=side_effect_state,
        )
        return _audit_terminal(status, completed, result, feedback_cycles)

    def _consumer_state(
        self,
        task: ReplyTask,
        run: AgentRun,
        feedback_cycles: int,
    ) -> ConsumerAgentResult | _NextConsumer | _Deferred | OrchestrationResult:
        if run.status == "completed":
            return _consumer_result(run)
        error = _run_error(run)
        if run.status == "failed" and error.authorization_required:
            if task.error == error.code:
                feedback = self._retry_feedback(run)
                if run.proposal_revision > 0 and feedback is None:
                    return _Deferred(run, "agent_feedback_missing", feedback_cycles)
                return _NextConsumer(
                    run.proposal_revision,
                    run.parent_agent_run_id,
                    feedback,
                    error.code or "authorization_required",
                )
            return _Deferred(
                run,
                error.code or "authorization_required",
                feedback_cycles,
                authorization_required=True,
            )
        if run.status == "failed" and error.retryable:
            if error.code == "runtime_route_unavailable":
                if _retryable_route_error_can_resume(task, error):
                    feedback = self._retry_feedback(run)
                    if run.proposal_revision > 0 and feedback is None:
                        return _Deferred(
                            run, "agent_feedback_missing", feedback_cycles
                        )
                    return _NextConsumer(
                        run.proposal_revision,
                        run.parent_agent_run_id,
                        feedback,
                        deferred_error_code=error.code,
                    )
                return _Deferred(run, error.code, feedback_cycles)
            if is_codex_provider_recovery_code(error.code):
                if task.error == error.code:
                    feedback = self._retry_feedback(run)
                    if run.proposal_revision > 0 and feedback is None:
                        return _Deferred(
                            run, "agent_feedback_missing", feedback_cycles
                        )
                    return _NextConsumer(
                        run.proposal_revision,
                        run.parent_agent_run_id,
                        feedback,
                        deferred_error_code=error.code,
                    )
                return _Deferred(run, error.code, feedback_cycles)
            feedback = self._retry_feedback(run)
            if run.proposal_revision > 0 and feedback is None:
                return _Deferred(run, "agent_feedback_missing", feedback_cycles)
            return _NextConsumer(
                run.proposal_revision,
                run.parent_agent_run_id,
                feedback,
            )
        if run.status == "running":
            if self.store.agent_run_lease_is_active(run.id):
                return _Deferred(run, "agent_run_active", feedback_cycles)
            self.store.fail_expired_agent_run(
                run.id,
                {"code": "consumer_lease_expired", "retryable": True},
                expected_execution_generation=task.execution_generation,
            )
            feedback = self._retry_feedback(run)
            if run.proposal_revision > 0 and feedback is None:
                return _Deferred(run, "agent_feedback_missing", feedback_cycles)
            return _NextConsumer(
                run.proposal_revision,
                run.parent_agent_run_id,
                feedback,
            )
        result = ConsumerAgentResult(
            outcome=ConsumerOutcome.FAILED,
            summary=error.code or "Consumer Agent failed.",
            proposal=None,
            error=error,
        )
        return _consumer_terminal(_failure_status(error), run, result, feedback_cycles)

    def _audit_state(
        self,
        task: ReplyTask,
        run: AgentRun,
        feedback_cycles: int,
    ) -> AuditAgentResult | _NextAudit | _Deferred | OrchestrationResult:
        if run.status == "completed":
            return _audit_result(run)
        if run.status == "unknown":
            error = _run_error(run)
            result = _failed_audit_result(run, AuditOutcome.UNKNOWN, error)
            return _audit_terminal("unknown", run, result, feedback_cycles)
        error = _run_error(run)
        if run.status == "failed" and error.authorization_required:
            if task.error == error.code:
                return _NextAudit(
                    run.proposal_revision,
                    run.turn_attempt,
                    run.parent_agent_run_id or 0,
                    None,
                    error.code or "authorization_required",
                )
            return _Deferred(
                run,
                error.code or "authorization_required",
                feedback_cycles,
                authorization_required=True,
            )
        if run.status == "failed" and error.retryable and run.side_effect_state == "none":
            if error.code == "runtime_route_unavailable":
                if _retryable_route_error_can_resume(task, error):
                    return _NextAudit(
                        run.proposal_revision,
                        run.turn_attempt,
                        run.parent_agent_run_id or 0,
                        None,
                        deferred_error_code=error.code,
                    )
                return _Deferred(run, error.code, feedback_cycles)
            if is_codex_provider_recovery_code(error.code):
                if task.error == error.code:
                    return _NextAudit(
                        run.proposal_revision,
                        run.turn_attempt,
                        run.parent_agent_run_id or 0,
                        None,
                        deferred_error_code=error.code,
                    )
                return _Deferred(run, error.code, feedback_cycles)
            return _NextAudit(
                run.proposal_revision,
                run.turn_attempt + 1,
                run.parent_agent_run_id or 0,
                None,
            )
        if run.status == "running":
            if self.store.agent_run_lease_is_active(run.id):
                return _Deferred(run, "agent_run_active", feedback_cycles)
            if run.side_effect_state != SideEffectState.NONE.value:
                run = self.store.mark_expired_agent_run_unknown(
                    run.id,
                    {
                        "code": "expired_audit_effect_requires_reconciliation",
                        "retryable": False,
                    },
                    expected_execution_generation=task.execution_generation,
                )
                result = _failed_audit_result(
                    run,
                    AuditOutcome.UNKNOWN,
                    AgentError(code="expired_audit_effect_requires_reconciliation"),
                )
                return _audit_terminal("unknown", run, result, feedback_cycles)
            return _NextAudit(
                run.proposal_revision,
                run.turn_attempt,
                run.parent_agent_run_id or 0,
                None,
            )
        result = _failed_audit_result(run, AuditOutcome.FAILED, error)
        return _audit_terminal(_failure_status(error), run, result, feedback_cycles)

    def _retry_feedback(self, run: AgentRun) -> AuditFeedback | None:
        if run.proposal_revision == 0 or run.parent_agent_run_id is None:
            return None
        parent = self.store.get_agent_run(run.parent_agent_run_id)
        if parent is None or parent.role is not AgentRole.AUDIT or parent.status != "completed":
            return None
        result = _audit_result(parent)
        if result.outcome is not AuditOutcome.REVISION_REQUIRED:
            return None
        return result.feedback

    @staticmethod
    def _revision_feedback_run(
        by_revision: dict[int, list[AgentRun]],
        revision: int,
    ) -> AgentRun | None:
        audits = sorted(
            (
                run
                for run in by_revision.get(revision, [])
                if run.role is AgentRole.AUDIT and run.status == "completed"
            ),
            key=lambda run: (run.turn_attempt, run.id),
        )
        if not audits:
            return None
        latest = audits[-1]
        result = _audit_result(latest)
        return latest if result.outcome is AuditOutcome.REVISION_REQUIRED else None

    def _feedback_cycles(self, task: ReplyTask) -> int:
        return max(
            (
                run.proposal_revision
                for run in self.store.list_agent_runs_for_task_generation(
                    task.id, task.execution_generation
                )
            ),
            default=0,
        )

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
        code = f"{role.value}_retry_exhausted"
        return OrchestrationResult(
            status="failed_retryable",
            final_run_id=latest.id,
            final_role=role,
            summary=code,
            error=AgentError(code=code, retryable=True),
            feedback_cycles=self._feedback_cycles(task),
        )

    @staticmethod
    def _deferred_result(state: _Deferred) -> OrchestrationResult:
        run = state.run
        return OrchestrationResult(
            status="failed_retryable",
            final_run_id=run.id if run is not None else 0,
            final_role=run.role if run is not None else AgentRole.CONSUMER,
            summary=state.detail or state.code,
            error=AgentError(
                code=state.code,
                retryable=True,
                authorization_required=state.authorization_required,
            ),
            feedback_cycles=state.feedback_cycles,
        )


def _context_refresh_failure_detail(exc: Exception) -> str:
    """Return a stable retry explanation without exposing DWS arguments or data."""
    code = str(getattr(exc, "code", "") or "").strip()
    normalized = str(exc).casefold()
    if code:
        return f"agent_context_refresh_failed: DingTalk read unavailable ({code})"
    if "not_authenticated" in normalized or "not authenticated" in normalized:
        return "agent_context_refresh_failed: DingTalk login is unavailable"
    if "permission" in normalized or "forbidden" in normalized:
        return "agent_context_refresh_failed: DingTalk read permission is unavailable"
    if "timeout" in normalized or "timed out" in normalized:
        return "agent_context_refresh_failed: DingTalk read timed out"
    if "database is locked" in normalized or "database is busy" in normalized:
        return "agent_context_refresh_failed: local task database is busy"
    if "conversation_context_refresh_forbidden" in normalized:
        return "agent_context_refresh_failed: current conversation cannot be refreshed"
    return "agent_context_refresh_failed: context source is temporarily unavailable"


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
    return AgentError.model_validate(
        {
            "code": str(payload.get("code") or "agent_run_failed"),
            "retryable": payload.get("retryable") is True,
            "authorization_required": payload.get("authorization_required") is True,
        }
    )


def _retryable_route_error_can_resume(task: ReplyTask, error: AgentError) -> bool:
    """A deferred error or an explicit safe recovery permits one fresh turn."""
    return task.error == error.code or bool(task.recovery_code)


def _consumer_result(run: AgentRun) -> ConsumerAgentResult:
    return ConsumerAgentResult.model_validate_json(run.final_result_json)


def _audit_result(run: AgentRun) -> AuditAgentResult:
    return AuditAgentResult.model_validate_json(run.final_result_json)


def _consumer_terminal(
    status: str,
    run: AgentRun,
    result: ConsumerAgentResult,
    feedback_cycles: int,
) -> OrchestrationResult:
    return OrchestrationResult(
        status=status,
        final_run_id=run.id,
        final_role=AgentRole.CONSUMER,
        summary=result.summary,
        error=result.error,
        feedback_cycles=feedback_cycles,
        consumer_result=result,
    )


def _audit_terminal(
    status: str,
    run: AgentRun,
    result: AuditAgentResult,
    feedback_cycles: int,
) -> OrchestrationResult:
    return OrchestrationResult(
        status=status,
        final_run_id=run.id,
        final_role=AgentRole.AUDIT,
        summary=result.summary,
        error=result.error,
        feedback_cycles=feedback_cycles,
        feedback=result.feedback,
        audit_result=result,
    )


def _failed_audit_result(
    run: AgentRun,
    outcome: AuditOutcome,
    error: AgentError,
) -> AuditAgentResult:
    decision_options: tuple[DecisionOption, ...] = ()
    if outcome is AuditOutcome.NEEDS_HUMAN:
        decision_options = (
            DecisionOption(
                key="confirmed_occurred",
                label="确认已执行",
                instruction="确认外部动作已经发生，并结束当前任务。",
                consequence="不会重放外部动作。",
            ),
            DecisionOption(
                key="confirmed_not_occurred",
                label="确认未执行",
                instruction="确认外部动作没有发生，并安全重开当前任务。",
                consequence="Agent 会重新审核后再决定是否执行。",
            ),
            DecisionOption(
                key="terminate_unrecoverable",
                label="无法确认并停止",
                instruction="无法确认外部结果，停止当前任务且不自动重放。",
                consequence="保留审计记录，不执行新的外部动作。",
            ),
        )
    return AuditAgentResult(
        outcome=outcome,
        summary=error.code or "Audit Agent failed.",
        proposal_revision=run.proposal_revision,
        side_effect_state=(
            SideEffectState.UNKNOWN
            if outcome in {AuditOutcome.UNKNOWN, AuditOutcome.RECONCILED}
            else SideEffectState.NONE
        ),
        feedback=None,
        external_result=None,
        decision_options=decision_options,
        error=error,
    )
