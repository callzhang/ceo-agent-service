"""Resolve approval history rows from structured, persisted evidence."""

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import ValidationError

from app.agent_contracts import (
    ConsumerOutcome,
    parse_persisted_consumer_result,
)
from app.store import AgentRole, AgentRun, ReplyAttempt


class ApprovalHistoryResult(StrEnum):
    APPROVED = "approved"
    RETURNED = "returned"
    REJECTED = "rejected"
    COMMENTED_PENDING = "commented_pending"
    NO_ACTION = "no_action"
    NEEDS_HUMAN = "needs_human"
    PROCESSING = "processing"
    FAILED = "failed"
    UNKNOWN = "unknown"


_VERIFIED_ACTION_RESULTS = {
    "approve": ApprovalHistoryResult.APPROVED,
    "reject": ApprovalHistoryResult.REJECTED,
    "revert_task": ApprovalHistoryResult.RETURNED,
    "comment": ApprovalHistoryResult.COMMENTED_PENDING,
}
_VERIFIED_TASK_REQUIRED_ACTIONS = {"approve", "reject", "revert_task"}
_TERMINAL_BUSINESS_RESULTS = {
    ApprovalHistoryResult.APPROVED,
    ApprovalHistoryResult.RETURNED,
    ApprovalHistoryResult.REJECTED,
}


def resolve_approval_history_result(
    attempt: ReplyAttempt,
    agent_runs: Sequence[AgentRun],
    *,
    verified_actions: Sequence[Mapping[str, object]] = (),
    recorded_actions: Sequence[Mapping[str, object]] = (),
) -> ApprovalHistoryResult | None:
    """Resolve one approval attempt without interpreting any prose fields."""

    if not _is_approval_attempt(attempt):
        return None

    consumers = [run for run in agent_runs if run.role is AgentRole.CONSUMER]
    confirmed_results = _recorded_results(attempt, recorded_actions)
    confirmed_results.update(_verified_approval_results(attempt, verified_actions, agent_runs))
    if len(confirmed_results) == 1:
        return next(iter(confirmed_results))
    if len(confirmed_results) > 1:
        return ApprovalHistoryResult.UNKNOWN

    workflow = _workflow_result(attempt.send_status)
    if workflow is not ApprovalHistoryResult.UNKNOWN:
        return workflow

    latest_consumer = _latest_completed_consumer(consumers)
    latest_result = None
    if latest_consumer is not None:
        try:
            latest_result = parse_persisted_consumer_result(
                latest_consumer.adopted_result_json
            )
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
            return ApprovalHistoryResult.UNKNOWN
    if latest_result is not None and latest_result.outcome is ConsumerOutcome.NO_ACTION:
        return ApprovalHistoryResult.NO_ACTION
    if latest_result is not None and latest_result.outcome is ConsumerOutcome.PROPOSAL:
        return ApprovalHistoryResult.UNKNOWN

    return workflow


def resolve_approval_history_group_result(
    attempts: Sequence[ReplyAttempt],
    agent_runs_by_attempt: Mapping[int, Sequence[AgentRun]],
    *,
    verified_actions: Sequence[Mapping[str, object]] = (),
    recorded_actions: Sequence[Mapping[str, object]] = (),
) -> ApprovalHistoryResult:
    """Resolve one process while keeping business evidence separate from workflow."""

    ordered = sorted(
        attempts,
        key=lambda attempt: (attempt.created_at, attempt.id),
        reverse=True,
    )
    if not ordered:
        return ApprovalHistoryResult.UNKNOWN

    latest_runs = agent_runs_by_attempt.get(ordered[0].id, ())
    latest_completed_consumer = _latest_completed_consumer(
        [run for run in latest_runs if run.role is AgentRole.CONSUMER]
    )
    latest_consumer_invalid = False
    if latest_completed_consumer is not None:
        try:
            parse_persisted_consumer_result(
                latest_completed_consumer.adopted_result_json
            )
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
            latest_consumer_invalid = True
    if latest_consumer_invalid and not any(
        _verified_approval_results(
            attempt,
            verified_actions,
            agent_runs_by_attempt.get(attempt.id, ()),
        )
        for attempt in ordered
    ):
        return ApprovalHistoryResult.UNKNOWN

    latest_comment: ApprovalHistoryResult | None = None
    for attempt in ordered:
        runs = agent_runs_by_attempt.get(attempt.id, ())
        results = _recorded_results(attempt, recorded_actions)
        results.update(_verified_approval_results(attempt, verified_actions, runs))
        if len(results) > 1:
            return ApprovalHistoryResult.UNKNOWN
        if not results:
            continue
        result = next(iter(results))
        if result in _TERMINAL_BUSINESS_RESULTS:
            return result
        if latest_comment is None and result is ApprovalHistoryResult.COMMENTED_PENDING:
            latest_comment = result

    if latest_comment is not None:
        return latest_comment
    newest = ordered[0]
    return (
        resolve_approval_history_result(
            newest,
            agent_runs_by_attempt.get(newest.id, ()),
            verified_actions=verified_actions,
            recorded_actions=recorded_actions,
        )
        or ApprovalHistoryResult.UNKNOWN
    )


def _recorded_results(attempt: ReplyAttempt, actions: Sequence[Mapping[str, object]]) -> set[ApprovalHistoryResult]:
    results = set()
    for action in actions:
        if action.get("attempt_id") != attempt.id or action.get("status") != "done":
            continue
        try:
            target = json.loads(str(action.get("target") or "{}"))
        except (TypeError, ValueError):
            continue
        if not isinstance(target, dict) or target.get("process_instance_id") != attempt.oa_process_instance_id or target.get("task_id") != attempt.oa_task_id:
            continue
        result = _VERIFIED_ACTION_RESULTS.get(str(action.get("operation") or ""))
        if result is not None:
            results.add(result)
    if not attempt.oa_action_result_json.strip() and _normalize(attempt.send_status) == "commented":
        results.add(ApprovalHistoryResult.COMMENTED_PENDING)
    return results


def _verified_approval_results(
    attempt: ReplyAttempt,
    actions: Sequence[Mapping[str, object]],
    agent_runs: Sequence[AgentRun],
) -> set[ApprovalHistoryResult]:
    """Project verified receipts linked to this attempt's task generation and OA process."""
    results: set[ApprovalHistoryResult] = set()
    process_id = attempt.oa_process_instance_id.strip()
    run_generations = {
        (run.reply_task_id, run.execution_generation) for run in agent_runs
    }
    if not process_id or not run_generations:
        return results
    for action in actions:
        if (action.get("task_id"), action.get("execution_generation")) not in run_generations:
            continue
        operation = _normalize(str(action.get("operation") or ""))
        outcome = _VERIFIED_ACTION_RESULTS.get(operation)
        if outcome is None:
            continue
        try:
            target = json.loads(str(action.get("target_identifiers_json") or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(target, dict):
            continue
        target_process = str(target.get("process_instance_id") or "").strip()
        if target_process != process_id:
            continue
        if operation in _VERIFIED_TASK_REQUIRED_ACTIONS:
            task_id = str(target.get("task_id") or "").strip()
            if not task_id or (attempt.oa_task_id.strip() and task_id != attempt.oa_task_id.strip()):
                continue
        if operation == "revert_task" and not str(target.get("target_activity_id") or "").strip():
            continue
        results.add(outcome)
    return results


def _is_approval_attempt(attempt: ReplyAttempt) -> bool:
    return (
        _normalize(attempt.action) == "oa_approval"
        or bool(attempt.oa_process_instance_id.strip())
    )


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _latest_completed_consumer(consumers: list[AgentRun]) -> AgentRun | None:
    completed = [
        run for run in consumers if _normalize(run.status) == "completed"
    ]
    if not completed:
        return None
    return max(completed, key=lambda run: (run.created_at, run.id))


def _workflow_result(status: str) -> ApprovalHistoryResult:
    normalized = _normalize(status)
    if normalized == "needs_human":
        return ApprovalHistoryResult.NEEDS_HUMAN
    if normalized in {"pending", "processing", "pending_reconciliation"}:
        return ApprovalHistoryResult.PROCESSING
    if normalized in {"failed", "blocked"}:
        return ApprovalHistoryResult.FAILED
    return ApprovalHistoryResult.UNKNOWN
