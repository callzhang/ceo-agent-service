"""A conversation Agent asks the OA Agent to look at one approval again.

Derek, 2026-11-02: an applicant who answers the OA review in a private chat
("权限已经开了") never reaches the OA task. The scanner skips an unchanged
approval that Derek has already commented on, so the review waited for a new
OA record that never came while the chat Agent kept replying about it.

The chat Agent proposes `dingtalk-oa` `request_review`; once Audit approves,
this handler records the request and starts the OA scan now. The scanner
stays the only place that queues OA review, so the request gets the OA task
its usual Skills, session, Audit and lifecycle: for an approval Derek still
holds it queues a new input carrying the reason, the same way it queues new
OA evidence for that approval.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.agent_contracts import ProposedAction
from app.system_executor import ActionOutcome

OA_SCAN_COMMAND = "scan-oa-approvals"
_STATE_PREFIX = "oa_review_request:v1:"


def _state_key(process_instance_id: str) -> str:
    return _STATE_PREFIX + process_instance_id


def pending_oa_review_request(store, process_instance_id: str) -> dict | None:
    """The request the next scan must honour for this approval, if any."""
    raw = store.get_service_state(_state_key(process_instance_id))
    try:
        request = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None
    if not isinstance(request, dict) or request.get("status") != "pending":
        return None
    return request


def mark_oa_review_request_queued(
    store, process_instance_id: str, request: dict, trigger_message_id: str,
) -> None:
    store.set_service_state(_state_key(process_instance_id), json.dumps(
        {**request, "status": "queued", "trigger_message_id": trigger_message_id},
        ensure_ascii=False,
    ))


class OaReviewRequestHandler:
    def __init__(self, store: object) -> None:
        self.store = store

    def dispatch(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome:
        process_id = action.target.get("process_instance_id")
        reason = action.payload.get("reason")
        if not isinstance(process_id, str) or not process_id.strip() or not isinstance(reason, str) or not reason.strip():
            return ActionOutcome("failed", {"reason": "oa_review_request_parameters_invalid"})
        schedule = next((
            task for task in self.store.list_scheduled_tasks()
            if task.command == OA_SCAN_COMMAND and task.enabled
        ), None)
        if schedule is None:
            return ActionOutcome("failed", {"reason": "oa_scan_schedule_unavailable"})
        now = datetime.now(timezone.utc)
        recorded = self._recorded(process_id, action_key)
        if recorded is None:
            source = self.store.get_reply_task(int(candidate["task_id"])) if candidate.get("task_id") else None
            recorded = {
                "request_key": action_key,
                "status": "pending",
                "reason": reason,
                "requested_at": now.isoformat(timespec="seconds"),
                "source_conversation_id": getattr(source, "conversation_id", ""),
                "source_message_id": getattr(source, "trigger_message_id", ""),
            }
            self.store.set_service_state(_state_key(process_id), json.dumps(recorded, ensure_ascii=False))
        run = self.store.create_scheduled_task_run(
            schedule.id, trigger_kind="manual", scheduled_for=now, now=now,
        )
        return ActionOutcome("verified", {
            "process_instance_id": process_id,
            "request_key": action_key,
            "scheduled_task_run_id": run.id,
        })

    def reconcile(self, action: ProposedAction, *, action_key: str, candidate: dict[str, object]) -> ActionOutcome | None:
        process_id = action.target.get("process_instance_id")
        if not isinstance(process_id, str) or self._recorded(process_id, action_key) is None:
            return None
        return ActionOutcome("verified", {"process_instance_id": process_id, "request_key": action_key})

    def _recorded(self, process_id: str, action_key: str) -> dict | None:
        raw = self.store.get_service_state(_state_key(process_id))
        try:
            request = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return None
        if isinstance(request, dict) and request.get("request_key") == action_key:
            return request
        return None
