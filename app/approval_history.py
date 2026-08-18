"""Resolve approval history rows from structured, persisted evidence."""

import json
from collections.abc import Sequence
from enum import StrEnum

from pydantic import ValidationError

from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
    ProposedAction,
)
from app.agent_result import SideEffectState
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


_STRUCTURED_OPERATIONS = {
    "oa approval approve": ApprovalHistoryResult.APPROVED,
    "oa approval return": ApprovalHistoryResult.RETURNED,
    "oa approval reject": ApprovalHistoryResult.REJECTED,
    "oa approval comment": ApprovalHistoryResult.COMMENTED_PENDING,
}
_DIRECT_ACTIONS = {
    "approve": ApprovalHistoryResult.APPROVED,
    "approved": ApprovalHistoryResult.APPROVED,
    "同意": ApprovalHistoryResult.APPROVED,
    "通过": ApprovalHistoryResult.APPROVED,
    "return": ApprovalHistoryResult.RETURNED,
    "returned": ApprovalHistoryResult.RETURNED,
    "退回": ApprovalHistoryResult.RETURNED,
    "reject": ApprovalHistoryResult.REJECTED,
    "rejected": ApprovalHistoryResult.REJECTED,
    "拒绝": ApprovalHistoryResult.REJECTED,
    "comment": ApprovalHistoryResult.COMMENTED_PENDING,
    "commented": ApprovalHistoryResult.COMMENTED_PENDING,
    "评论": ApprovalHistoryResult.COMMENTED_PENDING,
    "留言": ApprovalHistoryResult.COMMENTED_PENDING,
}
_DIRECT_TERMINAL_STATUSES = {"sent", "commented", "completed"}


def resolve_approval_history_result(
    attempt: ReplyAttempt,
    agent_runs: Sequence[AgentRun],
) -> ApprovalHistoryResult | None:
    """Resolve one approval attempt without interpreting any prose fields."""

    if not _is_approval_attempt(attempt):
        return None

    consumers = [run for run in agent_runs if run.role is AgentRole.CONSUMER]
    consumer_runs = {run.id: run for run in consumers}
    consumer_results = _consumer_results(consumers)

    structured_results = _confirmed_structured_results(
        attempt, agent_runs, consumer_runs, consumer_results
    )
    if len(structured_results) == 1:
        return next(iter(structured_results))
    if len(structured_results) > 1:
        return ApprovalHistoryResult.UNKNOWN

    direct = _direct_result(attempt)
    if direct is not None:
        return direct

    workflow = _workflow_result(attempt.send_status)
    if workflow is not ApprovalHistoryResult.UNKNOWN:
        return workflow

    latest_consumer = _latest_consumer(consumers, consumer_results)
    latest_result = consumer_results.get(latest_consumer.id) if latest_consumer else None
    if latest_result is not None and latest_result.outcome is ConsumerOutcome.NO_ACTION:
        return ApprovalHistoryResult.NO_ACTION
    if latest_result is not None and latest_result.outcome is ConsumerOutcome.PROPOSAL:
        return ApprovalHistoryResult.UNKNOWN

    return workflow


def _is_approval_attempt(attempt: ReplyAttempt) -> bool:
    return (
        _normalize(attempt.action) == "oa_approval"
        or bool(attempt.oa_process_instance_id.strip())
    )


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _latest_consumer(
    consumers: list[AgentRun],
    valid_results: dict[int, ConsumerAgentResult] | None = None,
) -> AgentRun | None:
    if valid_results is not None:
        consumers = [run for run in consumers if run.id in valid_results]
    if not consumers:
        return None
    return max(consumers, key=lambda run: (run.created_at, run.id))


def _consumer_results(
    consumers: list[AgentRun],
) -> dict[int, ConsumerAgentResult]:
    results: dict[int, ConsumerAgentResult] = {}
    for run in consumers:
        try:
            results[run.id] = ConsumerAgentResult.model_validate_json(run.final_result_json)
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
            continue
    return results


def _confirmed_structured_results(
    attempt: ReplyAttempt,
    agent_runs: Sequence[AgentRun],
    consumer_runs: dict[int, AgentRun],
    consumer_results: dict[int, ConsumerAgentResult],
) -> set[ApprovalHistoryResult]:
    results: set[ApprovalHistoryResult] = set()
    for run in agent_runs:
        if run.role is not AgentRole.AUDIT:
            continue
        try:
            audit = AuditAgentResult.model_validate_json(run.final_result_json)
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
            continue
        if (
            _normalize(run.status) != "completed"
            or audit.outcome is not AuditOutcome.EXECUTED
            or audit.side_effect_state is not SideEffectState.CONFIRMED
            or run.parent_agent_run_id not in consumer_results
        ):
            continue
        consumer_run = consumer_runs[run.parent_agent_run_id]
        if (
            _normalize(consumer_run.status) != "completed"
            or run.reply_task_id != consumer_run.reply_task_id
            or run.execution_generation != consumer_run.execution_generation
            or audit.proposal_revision != run.proposal_revision
            or audit.proposal_revision != consumer_run.proposal_revision
            or audit.external_result is None
            or not audit.external_result.operation_id.strip()
            or audit.external_result.operation_id != run.operation_id
        ):
            continue
        consumer = consumer_results[run.parent_agent_run_id]
        if consumer.outcome is not ConsumerOutcome.PROPOSAL or consumer.proposal is None:
            continue
        approval_actions: list[ApprovalHistoryResult] = []
        for action in consumer.proposal.actions:
            result = _STRUCTURED_OPERATIONS.get(_normalize(action.operation))
            if result is not None:
                if not _approval_action_target_matches(attempt, action, audit):
                    approval_actions = []
                    break
                approval_actions.append(result)
        results.update(approval_actions)
    return results


def _approval_action_target_matches(
    attempt: ReplyAttempt,
    action: ProposedAction,
    audit: AuditAgentResult,
) -> bool:
    process_instance_id = attempt.oa_process_instance_id.strip()
    if not process_instance_id:
        return True
    target_values = [
        action.target[key]
        for key in ("process_instance_id", "instance_id")
        if key in action.target
    ]
    if (
        not target_values
        or any(type(value) is not str or value != process_instance_id for value in target_values)
    ):
        return False
    live_reference = audit.external_result.live_result_reference if audit.external_result else {}
    if "process_instance_id" in live_reference:
        return live_reference["process_instance_id"] == process_instance_id
    return True


def _direct_result(attempt: ReplyAttempt) -> ApprovalHistoryResult | None:
    action = _DIRECT_ACTIONS.get(_normalize(attempt.oa_action))
    if action is None:
        return None
    raw = attempt.oa_action_result_json.strip()
    if not raw:
        return action if _normalize(attempt.send_status) in _DIRECT_TERMINAL_STATUSES else None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not _receipt_proves_success(payload):
        return None
    return action


def _receipt_proves_success(payload: dict[str, object]) -> bool:
    indicators: list[bool] = []

    if "success" in payload:
        success = payload["success"]
        if type(success) is not bool:
            return False
        indicators.append(success)

    for key in ("result", "dws_action_result"):
        if key not in payload:
            continue
        nested = payload[key]
        if not isinstance(nested, dict) or "success" not in nested:
            continue
        success = nested["success"]
        if type(success) is not bool:
            return False
        indicators.append(success)

    if "errcode" in payload:
        errcode = payload["errcode"]
        if type(errcode) is not int:
            return False
        indicators.append(errcode == 0)

    return bool(indicators) and all(indicators)


def _workflow_result(status: str) -> ApprovalHistoryResult:
    normalized = _normalize(status)
    if normalized == "needs_human":
        return ApprovalHistoryResult.NEEDS_HUMAN
    if normalized in {"pending", "processing", "pending_reconciliation"}:
        return ApprovalHistoryResult.PROCESSING
    if normalized in {"failed", "blocked"}:
        return ApprovalHistoryResult.FAILED
    return ApprovalHistoryResult.UNKNOWN
