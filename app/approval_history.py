"""Resolve approval history rows from structured, persisted evidence."""

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import ValidationError

from app.agent_contracts import (
    AuditAgentResult,
    AuditOutcome,
    ConsumerAgentResult,
    ConsumerOutcome,
    ProposedAction,
)
from app.legacy_receipt import legacy_receipt_has_explicit_failure
from app.native_cli_metadata import describe_native_command, native_command_argv
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


_STRUCTURED_COMMAND_RESULTS = {
    "oa approval approve": ApprovalHistoryResult.APPROVED,
    "oa approval oa-comments": ApprovalHistoryResult.COMMENTED_PENDING,
    "oa approval reject": ApprovalHistoryResult.REJECTED,
    "oa approval revert-task": ApprovalHistoryResult.RETURNED,
}
_TASK_REQUIRED_COMMANDS = {
    "oa approval approve",
    "oa approval reject",
    "oa approval revert-task",
}
_DWS_REVERT_ACTIONS = {"REVERT_FOR_APPROVAL", "REVERT_FOR_RESUBMIT"}
_DIRECT_CONFIRMED_ACTIONS = {
    "approve": ApprovalHistoryResult.APPROVED,
    "approved": ApprovalHistoryResult.APPROVED,
    "同意": ApprovalHistoryResult.APPROVED,
    "通过": ApprovalHistoryResult.APPROVED,
    "reject": ApprovalHistoryResult.REJECTED,
    "rejected": ApprovalHistoryResult.REJECTED,
    "拒绝": ApprovalHistoryResult.REJECTED,
}
_TERMINAL_BUSINESS_RESULTS = {
    ApprovalHistoryResult.APPROVED,
    ApprovalHistoryResult.RETURNED,
    ApprovalHistoryResult.REJECTED,
}


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

    confirmed_results = _confirmed_business_results(
        attempt,
        agent_runs,
        consumer_runs=consumer_runs,
        consumer_results=consumer_results,
    )
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
            latest_result = ConsumerAgentResult.model_validate_json(
                latest_consumer.final_result_json
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
    if latest_completed_consumer is not None:
        try:
            ConsumerAgentResult.model_validate_json(
                latest_completed_consumer.final_result_json
            )
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
            return ApprovalHistoryResult.UNKNOWN

    latest_comment: ApprovalHistoryResult | None = None
    for attempt in ordered:
        runs = agent_runs_by_attempt.get(attempt.id, ())
        consumers = [run for run in runs if run.role is AgentRole.CONSUMER]
        results = _confirmed_business_results(
            attempt,
            runs,
            consumer_runs={run.id: run for run in consumers},
            consumer_results=_consumer_results(consumers),
        )
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
        )
        or ApprovalHistoryResult.UNKNOWN
    )


def _confirmed_business_results(
    attempt: ReplyAttempt,
    agent_runs: Sequence[AgentRun],
    *,
    consumer_runs: dict[int, AgentRun],
    consumer_results: dict[int, ConsumerAgentResult],
) -> set[ApprovalHistoryResult]:
    results = _confirmed_structured_results(
        attempt,
        agent_runs,
        consumer_runs,
        consumer_results,
    )
    results.update(_direct_results(attempt))
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
            audit = _parse_persisted_audit_result(run.final_result_json)
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
            continue
        if (
            _normalize(run.status) != "completed"
            or audit.outcome is not AuditOutcome.EXECUTED
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
            result = _structured_action_result(attempt, action, audit)
            if result is not None:
                approval_actions.append(result)
        results.update(approval_actions)
    return results


def _parse_persisted_audit_result(raw: str) -> AuditAgentResult:
    """Read historical audit rows without widening the current wire contract."""
    try:
        return AuditAgentResult.model_validate_json(raw)
    except ValidationError:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise
        historical_only = {"reconciliation", "side_effect_state"}
        if not historical_only.intersection(payload):
            raise
        normalized = {
            key: value for key, value in payload.items() if key not in historical_only
        }
        return AuditAgentResult.model_validate(normalized)


def _structured_action_result(
    attempt: ReplyAttempt,
    action: ProposedAction,
    audit: AuditAgentResult,
) -> ApprovalHistoryResult | None:
    descriptor = describe_native_command(
        {"type": "command_execution", **action.payload}
    )
    if descriptor is None or descriptor.cli != "dws":
        return None
    result = _STRUCTURED_COMMAND_RESULTS.get(descriptor.command_path)
    if result is None:
        return None

    process_instance_id = attempt.oa_process_instance_id.strip()
    command_process_id = descriptor.target_identifiers.get("instance-id", "")
    if (
        not process_instance_id
        or not command_process_id
        or command_process_id != process_instance_id
    ):
        return None

    if descriptor.command_path in _TASK_REQUIRED_COMMANDS:
        command_task_id = descriptor.target_identifiers.get("task-id", "")
        if not command_task_id:
            return None
        task_id = attempt.oa_task_id.strip()
        if task_id and command_task_id != task_id:
            return None

    if descriptor.command_path == "oa approval revert-task":
        argv = native_command_argv(
            {"type": "command_execution", **action.payload}
        )
        if argv is None or _argv_option_value(argv, "--action") not in _DWS_REVERT_ACTIONS:
            return None

    live_reference = audit.external_result.live_result_reference if audit.external_result else {}
    if "process_instance_id" in live_reference:
        if live_reference["process_instance_id"] != command_process_id:
            return None
    return result


def _argv_option_value(argv: tuple[str, ...], option: str) -> str:
    for index, value in enumerate(argv):
        if value == option:
            if index + 1 < len(argv):
                return argv[index + 1]
            return ""
        prefix = f"{option}="
        if value.startswith(prefix):
            return value[len(prefix) :]
    return ""


def _direct_results(attempt: ReplyAttempt) -> set[ApprovalHistoryResult]:
    raw = attempt.oa_action_result_json.strip()
    if not raw:
        return (
            {ApprovalHistoryResult.COMMENTED_PENDING}
            if _normalize(attempt.send_status) == "commented"
            else set()
        )
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    if legacy_receipt_has_explicit_failure(payload):
        return set()
    if not _direct_receipt_identifiers_match(attempt, payload):
        return set()
    if _normalize(attempt.send_status) == "commented":
        return {ApprovalHistoryResult.COMMENTED_PENDING}

    typed_result = _typed_legacy_result(attempt, payload)
    if typed_result is not None:
        return {typed_result}
    if _legacy_comment_receipt_matches(attempt, payload):
        return {ApprovalHistoryResult.COMMENTED_PENDING}
    if _running_comment_receipt_matches(attempt, payload):
        return {ApprovalHistoryResult.COMMENTED_PENDING}
    direct_result = _DIRECT_CONFIRMED_ACTIONS.get(_normalize(attempt.oa_action))
    if direct_result is not None and _receipt_proves_success(payload):
        return {direct_result}
    return set()


def _typed_legacy_result(
    attempt: ReplyAttempt,
    payload: dict[str, object],
) -> ApprovalHistoryResult | None:
    readback = payload.get("readback")
    typed = readback if isinstance(readback, dict) else payload
    task_status = _normalize(str(typed.get("taskStatus") or ""))
    task_result = str(typed.get("taskResult") or "").strip().upper()
    if task_status != "completed":
        return None

    if task_result == "AGREE":
        return ApprovalHistoryResult.APPROVED
    if task_result == "REFUSE":
        return ApprovalHistoryResult.REJECTED
    if task_result != "REDIRECT_PROCESS":
        return None
    invocation = payload.get("invocation")
    if not isinstance(invocation, dict):
        return None
    if _normalize(str(invocation.get("canonical_path") or "")) != "oa approval revert-task":
        return None
    return ApprovalHistoryResult.RETURNED


def _legacy_comment_receipt_matches(
    attempt: ReplyAttempt,
    payload: dict[str, object],
) -> bool:
    invocation = payload.get("invocation")
    if not isinstance(invocation, dict):
        return False
    if _normalize(str(invocation.get("canonical_path") or "")) not in {
        "oa approval oa-comments",
        "oa.dingflow_comments",
    }:
        return False
    params = invocation.get("params")
    if not isinstance(params, dict):
        return False
    process_id = str(
        params.get("processInstanceId") or params.get("instance-id") or ""
    ).strip()
    if not process_id or (
        attempt.oa_process_instance_id.strip()
        and process_id != attempt.oa_process_instance_id.strip()
    ):
        return False
    response = payload.get("response")
    if isinstance(response, dict) and isinstance(response.get("content"), dict):
        return _receipt_proves_success(response["content"])
    return _receipt_proves_success(payload)


def _running_comment_receipt_matches(
    attempt: ReplyAttempt,
    payload: dict[str, object],
) -> bool:
    if (
        _normalize(attempt.oa_action) != "comment"
        or _normalize(attempt.send_status) != "decision_selected"
        or payload.get("success") is not True
        or payload.get("read_back_comment_found") is not True
        or _normalize(str(payload.get("status") or "")) != "running"
        or not attempt.oa_process_instance_id.strip()
        or not attempt.oa_task_id.strip()
    ):
        return False
    current_task = payload.get("current_task")
    return bool(
        isinstance(current_task, dict)
        and _normalize(str(current_task.get("taskStatus") or "")) == "running"
        and str(current_task.get("taskId") or "").strip()
        == attempt.oa_task_id.strip()
    )


def _direct_receipt_identifiers_match(
    attempt: ReplyAttempt,
    payload: dict[str, object],
) -> bool:
    process_ids, task_ids = _receipt_identifiers(payload)
    return _all_identifiers_match(
        process_ids,
        attempt.oa_process_instance_id,
    ) and _all_identifiers_match(task_ids, attempt.oa_task_id)


def _all_identifiers_match(values: list[str], expected: str) -> bool:
    if not values:
        return True
    normalized_expected = expected.strip()
    return bool(normalized_expected) and all(
        value == normalized_expected for value in values
    )


def _receipt_identifiers(value: object) -> tuple[list[str], list[str]]:
    process_ids: list[str] = []
    task_ids: list[str] = []

    def collect(candidate: object) -> None:
        if isinstance(candidate, list):
            for item in candidate:
                collect(item)
            return
        if not isinstance(candidate, dict):
            return
        for key, item in candidate.items():
            if key in {
                "process_instance_id",
                "processInstanceId",
                "instance-id",
                "--instance-id",
            }:
                process_ids.append(str(item or "").strip())
            elif key in {"task_id", "taskId", "task-id", "--task-id"}:
                task_ids.append(str(item or "").strip())
            elif key == "argv" and isinstance(item, list):
                argv = tuple(str(part) for part in item)
                for option, target in (
                    ("--instance-id", process_ids),
                    ("--task-id", task_ids),
                ):
                    identifier = _argv_option_value(argv, option)
                    if identifier:
                        target.append(identifier.strip())
            collect(item)

    collect(value)
    return process_ids, task_ids


def _receipt_proves_success(payload: dict[str, object]) -> bool:
    if legacy_receipt_has_explicit_failure(payload):
        return False
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
