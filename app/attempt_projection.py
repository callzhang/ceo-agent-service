"""Current Attempt state projection from the owning task generation."""

from __future__ import annotations

from typing import Any


_TERMINAL_TASK_STATES = {"done", "skipped", "needs_human"}


def project_attempt_status(attempt: Any, task: Any, runs: list[Any]) -> str:
    """Return the current state for an Attempt without rewriting history.

    Physical ``reply_attempts.send_status`` is immutable history.  The current
    state belongs to the task's current execution generation and its last
    effective run; a task already closed as done/skipped must therefore not be
    reported as the stale pending state of an older attempt row.
    """
    fallback = str(getattr(attempt, "send_status", "") or "").strip() or "failed"
    if task is None:
        return fallback
    task_status = str(getattr(task, "status", "") or "").strip()
    generation = str(getattr(task, "execution_generation", "") or "").strip()
    current_runs = [
        run
        for run in runs
        if not generation
        or str(getattr(run, "execution_generation", "") or "").strip() == generation
    ]
    if (
        fallback == "needs_human"
        and current_runs
        and int(getattr(attempt, "agent_run_id", 0) or 0) > 0
    ):
        latest_run = max(
            current_runs,
            key=lambda run: (
                int(getattr(run, "turn_attempt", 0) or 0),
                int(getattr(run, "proposal_revision", 0) or 0),
                int(getattr(run, "id", 0) or 0),
            ),
        )
        if int(getattr(latest_run, "id", 0) or 0) == int(attempt.agent_run_id):
            from app.decision_quality import (
                StoredNeedsHumanProjection,
                classify_stored_needs_human_projection,
            )

            if getattr(latest_run, "status", "") != "completed":
                return "failed"
            if (
                classify_stored_needs_human_projection(
                    getattr(latest_run, "final_result_json", "")
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
        return task_status
    if current_runs:
        last_run = max(
            current_runs,
            key=lambda run: (
                int(getattr(run, "turn_attempt", 0) or 0),
                int(getattr(run, "proposal_revision", 0) or 0),
                int(getattr(run, "id", 0) or 0),
            ),
        )
        run_status = str(getattr(last_run, "status", "") or "").strip()
        if run_status in {"pending", "running", "failed"}:
            return run_status
    if task_status in {"pending", "processing", "running"}:
        return "running" if task_status == "processing" else task_status
    return fallback
