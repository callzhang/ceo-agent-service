"""Current Attempt state projection from the owning task generation."""

from __future__ import annotations

import json
from typing import Any

from app.wechat.models import principal_reply_supersession


_TERMINAL_TASK_STATES = {"done", "skipped", "needs_human", "failed"}
_TERMINAL_OA_PROCESS_STATES = {"COMPLETED", "TERMINATED"}
_TERMINAL_OA_TASK_STATES = {"COMPLETED", "FINISHED", "CANCELED", "CANCELLED", "TERMINATED"}
_TERMINAL_OA_CLOSURE_SOURCE = "exact original DingTalk message and native OA detail/OpenAPI readback"


def terminal_oa_source_closure_is_skipped(attempt: Any) -> bool:
    """Recognize a verified closure of an obsolete OA follow-up, not delivery.

    The stored delivery outcome and error remain unchanged. Only the current
    handling status becomes skipped after the exact OA source/task readback.
    """
    if (
        str(getattr(attempt, "send_status", "") or "").strip()
        not in {"uncertain", "failed", "skipped"}
        or str(getattr(attempt, "send_error", "") or "").strip()
        != "external_action_uncertain"
        or str(getattr(attempt, "channel", "") or "").strip() != "dingtalk"
        or not str(getattr(attempt, "resolved_at", "") or "").strip()
    ):
        return False
    try:
        evidence = json.loads(str(getattr(attempt, "resolution", "") or ""))
    except (TypeError, json.JSONDecodeError):
        return False
    return (
        isinstance(evidence, dict)
        and evidence.get("source") == _TERMINAL_OA_CLOSURE_SOURCE
        and evidence.get("native_and_openapi_business_id_match") is True
        and evidence.get("external_write_count") == 0
        and evidence.get("current_principal_task_ids") == []
        and evidence.get("process_status") in _TERMINAL_OA_PROCESS_STATES
        and evidence.get("original_oa_task_status") in _TERMINAL_OA_TASK_STATES
        and bool(str(evidence.get("process_instance_id") or "").strip())
        and bool(str(evidence.get("original_oa_task_id") or "").strip())
        and str(evidence.get("original_message_id") or "")
        == str(getattr(attempt, "trigger_message_id", "") or "")
        and str(evidence.get("historical_chat_delivery") or "").startswith("unverified")
    )


def current_wechat_delivery_matches(attempt: Any, task: Any, delivery: Any) -> bool:
    """Require the exact owning task, execution generation and conversation."""
    return task is not None and (
        str(getattr(attempt, "channel", "") or "") == "wechat"
        and delivery is not None
        and int(getattr(delivery, "task_id", 0) or 0) == int(task.id)
        and str(getattr(delivery, "execution_generation", "") or "") == str(getattr(task, "execution_generation", "") or "").strip()
        and str(getattr(delivery, "conversation_id", "") or "")
        == str(getattr(attempt, "conversation_id", "") or "")
    )


def project_attempt_status(
    attempt: Any,
    task: Any,
    runs: list[Any],
    *,
    delivery: Any = None,
    current_execution_status: str = "",
) -> str:
    """Return the current state for an Attempt without rewriting history.

    Stored Attempt status may change as the current execution is reconciled.
    The projected current state belongs to the task's current execution generation and its last
    effective run; a task already closed as done/skipped must therefore not be
    reported as the stale pending state of an older attempt row.
    """
    fallback = str(getattr(attempt, "send_status", "") or "").strip() or "failed"
    if fallback == "needs_human" and str(getattr(attempt, "resolved_at", "") or "").strip():
        return "skipped"
    if terminal_oa_source_closure_is_skipped(attempt):
        return "skipped"
    if (
        current_execution_status == "uncertain"
        and fallback != "skipped"
    ):
        return "uncertain"
    if task is None:
        return fallback
    task_status = str(getattr(task, "status", "") or "").strip()
    generation = str(getattr(task, "execution_generation", "") or "").strip()
    if current_wechat_delivery_matches(attempt, task, delivery):
        delivery_status = str(getattr(delivery, "status", "") or "")
        if fallback == "failed" and delivery_status in {"failed", "send_unknown"}:
            return "failed"
        if delivery_status == "skipped" and principal_reply_supersession(str(getattr(delivery, "error", "") or "")):
            return "skipped"
    current_runs = [
        run
        for run in runs
        if not generation
        or str(getattr(run, "execution_generation", "") or "").strip() == generation
    ]
    latest_run = max(
        current_runs,
        key=lambda run: (
            int(getattr(run, "proposal_revision", 0) or 0),
            getattr(run, "role", "") == "audit",
            int(getattr(run, "turn_attempt", 0) or 0),
            int(getattr(run, "id", 0) or 0),
        ),
        default=None,
    )
    if (
        fallback == "needs_human"
        and current_runs
        and int(getattr(attempt, "agent_run_id", 0) or 0) > 0
    ):
        if int(getattr(latest_run, "id", 0) or 0) == int(attempt.agent_run_id):
            from app.decision_quality import (
                StoredNeedsHumanProjection,
                classify_stored_needs_human_projection,
            )

            if getattr(latest_run, "status", "") != "completed":
                return "failed"
            if (
                classify_stored_needs_human_projection(
                    getattr(latest_run, "adopted_result_json", "")
                )
                is StoredNeedsHumanProjection.NEEDS_HUMAN
            ):
                return "needs_human"
            return "failed"
    # A closed task outranks a run that failed inside it. Attempt 9136 is the
    # live case: reply task 383933 was closed `skipped` on 2026-09-15 because a
    # later weekly OKR report succeeded, while its last run stayed `failed`
    # from 2026-09-12 (the runtime route was paused). The detail page therefore
    # called it failed forever, while the History list read the stored
    # `skipped` -- so the item looked failed to a reader and resolved to every
    # check that walks the list.
    if task_status in _TERMINAL_TASK_STATES:
        # Task completion closes the processing cycle; the latest bound
        # Attempt still records whether that cycle intentionally did no action.
        if (
            task_status == "done"
            and fallback == "skipped"
            and latest_run is not None
            and getattr(latest_run, "status", "") == "completed"
            and int(getattr(attempt, "agent_run_id", 0) or 0) == int(latest_run.id)
        ):
            return "skipped"
        return task_status
    if latest_run is not None:
        run_status = str(getattr(latest_run, "status", "") or "").strip()
        if run_status in {"pending", "running", "failed"}:
            return run_status
    if task_status in {"pending", "processing", "running"}:
        return "running" if task_status == "processing" else task_status
    return fallback
