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
    consumer_results = _consumer_results(consumers)

    structured_results = _confirmed_structured_results(agent_runs, consumer_results)
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
    agent_runs: Sequence[AgentRun],
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
            audit.outcome is not AuditOutcome.EXECUTED
            or audit.side_effect_state is not SideEffectState.CONFIRMED
            or run.parent_agent_run_id not in consumer_results
        ):
            continue
        consumer = consumer_results[run.parent_agent_run_id]
        if consumer.outcome is not ConsumerOutcome.PROPOSAL or consumer.proposal is None:
            continue
        for action in consumer.proposal.actions:
            result = _STRUCTURED_OPERATIONS.get(_normalize(action.operation))
            if result is not None:
                results.add(result)
    return results


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
    if payload.get("success") is True:
        return True
    result = payload.get("result")
    if isinstance(result, dict) and result.get("success") is True:
        return True
    dws_result = payload.get("dws_action_result")
    if isinstance(dws_result, dict) and dws_result.get("success") is True:
        return True
    return payload.get("errcode") == 0


def _workflow_result(status: str) -> ApprovalHistoryResult:
    normalized = _normalize(status)
    if normalized == "needs_human":
        return ApprovalHistoryResult.NEEDS_HUMAN
    if normalized in {"pending", "processing", "pending_reconciliation"}:
        return ApprovalHistoryResult.PROCESSING
    if normalized in {"failed", "blocked"}:
        return ApprovalHistoryResult.FAILED
    return ApprovalHistoryResult.UNKNOWN
