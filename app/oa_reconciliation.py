"""Narrow, no-dispatch reconciliation for an existing OA return Attempt."""

from __future__ import annotations

from typing import Any

from app.agent_contracts import ProposedAction, SystemExecutionResult, parse_persisted_consumer_result
from app.external_action_identity import expected_external_action
from app.system_executor import SystemExecutor


def reconcile_oa_return_attempt(
    store: Any,
    dws: Any,
    *,
    attempt_id: int,
    candidate_id: int,
    review_id: int,
) -> SystemExecutionResult:
    """Re-read and persist proof for one already-uncertain OA return.

    This path requires the current, exact reviewed candidate and an existing
    uncertain action attempt. ``reconciliation_only`` prevents dispatch even
    if the persisted state changes between validation and execution.
    """
    attempt = store.get_reply_attempt(attempt_id)
    candidate_row = store.get_review_candidate(candidate_id)
    if attempt is None or candidate_row is None:
        return SystemExecutor._failure(
            "oa_reconciliation_record_missing", "Attempt or reviewed candidate is missing"
        )
    task = store.get_reply_task(candidate_row["task_id"])
    if task is None or task.execution_generation != candidate_row["execution_generation"]:
        return SystemExecutor._failure(
            "oa_reconciliation_task_stale", "Reviewed candidate is not bound to the current task generation"
        )
    current = store.current_reviewed_candidate(task.id, task.execution_generation)
    if (current is None or current["id"] != candidate_id or current["review_id"] != review_id
        or current.get("decision") != "approve"
        or candidate_row.get("review_id") not in (None, review_id)):
        return SystemExecutor._failure(
            "oa_reconciliation_review_stale", "Attempt is not bound to the current reviewed candidate"
        )
    try:
        candidate = parse_persisted_consumer_result(candidate_row["candidate_json"])
    except ValueError:
        return SystemExecutor._failure(
            "oa_reconciliation_candidate_invalid", "Persisted reviewed candidate is invalid"
        )
    plan = candidate.proposal
    if plan is None or len(plan.actions) != 1:
        return SystemExecutor._failure(
            "oa_reconciliation_action_invalid", "Attempt does not contain one exact OA action"
        )
    action: ProposedAction = plan.actions[0]
    if (action.capability != "dingtalk-oa" or action.operation != "revert_task"
        or action.target.get("process_instance_id") != attempt.oa_process_instance_id
        or action.target.get("task_id") != attempt.oa_task_id
        or action.payload.get("remark") != attempt.oa_remark):
        return SystemExecutor._failure(
            "oa_reconciliation_action_mismatch", "Reviewed action is not this Attempt's OA return"
        )
    execution = store.get_candidate_execution(candidate_id)
    if execution is None or execution["status"] != "uncertain":
        return SystemExecutor._failure(
            "oa_reconciliation_not_uncertain", "Only an uncertain existing execution can be reconciled"
        )
    action_attempts = store.list_candidate_action_attempts(execution["id"])
    identity = expected_external_action(
        action, action_index=0, business_object_key=task.business_object_key
    )
    if (len(action_attempts) != 1 or action_attempts[0]["action_index"] != 0
        or action_attempts[0]["status"] != "uncertain"
        or action_attempts[0]["external_action_key"] != identity["external_action_key"]):
        return SystemExecutor._failure(
            "oa_reconciliation_action_attempt_mismatch",
            "Existing uncertain action attempt does not match the reviewed OA action",
        )
    return SystemExecutor(store=store, dws=dws).execute(
        task, candidate_id, review_id, reconciliation_only=True
    )
