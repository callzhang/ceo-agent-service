"""Reconcile a recorded OA return or close an already settled trigger.

The caller supplies an ordinary Store and a read-capable DingTalk client. All
database changes go through their existing execution and task-closure APIs.
"""

import json
from dataclasses import dataclass

from app.agent_contracts import ConsumerAgentResult
from app.external_action_identity import expected_external_action
from app.system_action_handlers import OaRevertHandler
from app.system_executor import SystemExecutor


@dataclass(frozen=True)
class ReturnRepair:
    task_id: int
    candidate_id: int
    review_id: int
    execution_id: int
    action_attempt_id: int


class _ReconcileOnlyReturn:
    def __init__(self, dws: object) -> None:
        self._handler = OaRevertHandler(dws)

    def dispatch(self, action, *, action_key, candidate):
        raise RuntimeError("OA dispatch is outside this repair")

    def reconcile(self, action, *, action_key, candidate):
        return self._handler.reconcile(action, action_key=action_key, candidate=candidate)


def inspect_recorded_return(store: object, dws: object, repair: ReturnRepair) -> dict[str, object]:
    """Read the exact saved action and native OA proof without changing state."""
    task = store.get_reply_task(repair.task_id)
    if task is None or task.status != "done":
        raise ValueError("settled task is unavailable")
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    if (reviewed is None or reviewed["id"] != repair.candidate_id
        or reviewed["review_id"] != repair.review_id):
        raise ValueError("current approved candidate mismatch")
    result = ConsumerAgentResult.model_validate_json(reviewed["candidate_json"])
    if result.outcome.value != "proposal" or result.proposal is None or len(result.proposal.actions) != 1:
        raise ValueError("repair requires one approved action")
    action = result.proposal.actions[0]
    if action.capability != "dingtalk-oa" or action.operation != "revert_task":
        raise ValueError("approved action is not an OA return")
    execution = store.get_candidate_execution(repair.candidate_id)
    if (execution is None or execution["id"] != repair.execution_id
        or execution["review_id"] != repair.review_id or execution["status"] != "uncertain"):
        raise ValueError("uncertain execution mismatch")
    attempts = store.list_candidate_action_attempts(repair.execution_id)
    if (len(attempts) != 1 or attempts[0]["id"] != repair.action_attempt_id
        or attempts[0]["action_index"] != 0 or attempts[0]["status"] != "uncertain"):
        raise ValueError("uncertain action mismatch")
    attempt = attempts[0]
    identity = expected_external_action(action, action_index=0, business_object_key=task.business_object_key)
    action_key = str(identity["external_action_key"])
    if attempt["external_action_key"] != action_key or store.get_candidate_external_action(action_key):
        raise ValueError("action key or existing receipt mismatch")
    proof = _ReconcileOnlyReturn(dws).reconcile(
        action, action_key=action_key, candidate={**reviewed, "action_attempt": attempt},
    )
    if proof.status != "verified":
        raise ValueError("native OA return remains unverified")
    return {
        "task_id": task.id,
        "candidate_id": repair.candidate_id,
        "review_id": repair.review_id,
        "execution_id": repair.execution_id,
        "action_attempt_id": repair.action_attempt_id,
        "external_action_key": action_key,
        "destination_task_ids": proof.provider_result["destination_task_ids"],
    }


def reconcile_recorded_return(store: object, dws: object, repair: ReturnRepair) -> dict[str, object]:
    """Let the normal SystemExecutor finish only an already verified return."""
    evidence = inspect_recorded_return(store, dws, repair)
    task = store.get_reply_task(repair.task_id)
    handler = _ReconcileOnlyReturn(dws)
    result = SystemExecutor(
        store, handlers={("dingtalk-oa", "revert_task"): handler}, dws=dws,
    ).execute(task, repair.candidate_id, repair.review_id)
    execution = store.get_candidate_execution(repair.candidate_id)
    [attempt] = store.list_candidate_action_attempts(repair.execution_id)
    if (result.outcome != "executed" or execution["status"] != "done"
        or attempt["status"] != "verified"
        or store.get_candidate_external_action(evidence["external_action_key"]) is None):
        raise ValueError("normal reconciliation did not produce a verified receipt")
    return evidence


def inspect_settled_trigger(
    store: object, dws: object, *, task_id: int, process_id: str,
    original_oa_task_id: str, changed_card_task_id: str,
) -> str:
    """Build evidence for closing one saved trigger after its OA node was agreed."""
    task = store.get_reply_task(task_id)
    if (task is None or task.channel != "dingtalk"
        or task.status not in ("failed", "needs_human")
        or original_oa_task_id not in task.trigger_text
        or changed_card_task_id in task.trigger_text):
        raise ValueError("saved trigger does not match the settled OA node")
    detail_response = dws.read_oa_approval_detail(process_id)
    detail = detail_response.get("result") if isinstance(detail_response, dict) else None
    api_response = dws.read_oa_process_instance_openapi(process_id)
    source = api_response.get("process_instance") if isinstance(api_response, dict) else None
    if (not isinstance(detail_response, dict) or detail_response.get("success") is not True
        or not isinstance(detail, dict) or detail.get("processInstanceId") != process_id
        or not isinstance(api_response, dict) or api_response.get("errcode") != 0
        or not isinstance(source, dict) or not detail.get("businessId")
        or detail["businessId"] != source.get("business_id")
        or not isinstance(source.get("tasks"), list)):
        raise ValueError("native OA identity unavailable")
    principal = dws.get_current_user_id()
    tasks = [item for item in source["tasks"] if isinstance(item, dict)]
    original = [item for item in tasks if str(item.get("taskid")) == original_oa_task_id]
    changed = [item for item in tasks if str(item.get("taskid")) == changed_card_task_id]
    if (not principal or len(original) != 1 or len(changed) != 1
        or original[0].get("userid") != principal
        or original[0].get("task_status") != "COMPLETED"
        or original[0].get("task_result") != "AGREE"
        or changed[0].get("userid") in (None, principal)
        or any(item.get("userid") == principal and item.get("task_status") in ("RUNNING", "PAUSED")
               for item in tasks)):
        raise ValueError("original OA node is not settled or current ownership is ambiguous")
    return json.dumps({
        "source": "DingTalk OA detail and OpenAPI process readback",
        "process_instance_id": process_id,
        "original_trigger_message_id": task.trigger_message_id,
        "original_task_id": original_oa_task_id,
        "original_task_status": "COMPLETED",
        "original_task_result": "AGREE",
        "changed_card_task_id": changed_card_task_id,
        "changed_card_task_status": changed[0]["task_status"],
        "changed_card_task_owned_by_other_approver": True,
        "no_current_principal_task": True,
        "process_status": source.get("status"),
    }, ensure_ascii=False, sort_keys=True)


def close_settled_trigger(
    store: object, dws: object, *, task_id: int, process_id: str,
    original_oa_task_id: str, changed_card_task_id: str,
) -> bool:
    evidence = inspect_settled_trigger(
        store, dws, task_id=task_id, process_id=process_id,
        original_oa_task_id=original_oa_task_id, changed_card_task_id=changed_card_task_id,
    )
    task = store.get_reply_task(task_id)
    return store.close_unresolved_reply_task_already_settled(
        task_id, expected_execution_generation=task.execution_generation,
        settled_evidence=evidence,
    )
