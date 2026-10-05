from types import SimpleNamespace

import pytest

from app.attempt_projection import project_attempt_status


def test_latest_generation_run_overrides_stale_attempt_status():
    attempt = SimpleNamespace(send_status="pending")
    task = SimpleNamespace(status="done", execution_generation="g2")
    runs = [
        SimpleNamespace(id=1, execution_generation="g1", turn_attempt=0, proposal_revision=0, status="failed"),
        SimpleNamespace(id=2, execution_generation="g2", turn_attempt=0, proposal_revision=0, status="completed"),
    ]
    assert project_attempt_status(attempt, task, runs) == "done"


def test_last_effective_run_wins_within_current_generation():
    attempt = SimpleNamespace(send_status="pending")
    task = SimpleNamespace(status="processing", execution_generation="g1")
    runs = [
        SimpleNamespace(id=1, execution_generation="g1", turn_attempt=0, proposal_revision=0, status="completed"),
        SimpleNamespace(id=2, execution_generation="g1", turn_attempt=1, proposal_revision=0, status="failed"),
    ]
    assert project_attempt_status(attempt, task, runs) == "failed"


def test_no_task_preserves_historical_fallback():
    attempt = SimpleNamespace(send_status="pending")
    assert project_attempt_status(attempt, None, []) == "pending"


def test_closed_task_outranks_a_run_that_failed_inside_it():
    """Attempt 9136 read `failed` on its page and `skipped` in the list.

    Reply task 383933 closed `skipped` on 2026-09-15 because a later weekly OKR
    report succeeded, while its last run stayed `failed` from 2026-09-12. The
    detail page called it failed indefinitely, and every check that walks the
    History list saw the stored `skipped`, so nobody could reconcile the two.
    """
    attempt = SimpleNamespace(send_status="skipped")
    task = SimpleNamespace(status="skipped", execution_generation="g1")
    runs = [
        SimpleNamespace(id=1, execution_generation="g1", turn_attempt=0, proposal_revision=0, status="failed"),
    ]

    assert project_attempt_status(attempt, task, runs) == "skipped"


def test_a_live_task_still_reports_its_failing_run():
    attempt = SimpleNamespace(send_status="pending")
    task = SimpleNamespace(status="processing", execution_generation="g1")
    runs = [
        SimpleNamespace(id=1, execution_generation="g1", turn_attempt=0, proposal_revision=0, status="failed"),
    ]

    assert project_attempt_status(attempt, task, runs) == "failed"


@pytest.mark.parametrize("generation,conversation,task_id,error,expected", [
    ("g2", "current", 1, "stale_pre_action_retry_exhausted; superseded_by_principal_reply:reply-2", "skipped"),
    ("g1", "current", 1, "stale_pre_action_retry_exhausted; superseded_by_principal_reply:reply-2", "done"),
    ("g2", "other", 1, "stale_pre_action_retry_exhausted; superseded_by_principal_reply:reply-2", "done"),
    ("g2", "current", 2, "stale_pre_action_retry_exhausted; superseded_by_principal_reply:reply-2", "done"),
    ("g2", "current", 1, "expired_after_target_open_retries", "done"),
    ("g2", "current", 1, "note: superseded_by_principal_reply:reply-2", "done"),
    ("g2", "current", 1, "superseded_by_principal_reply:", "done"),
])
def test_only_current_formally_superseded_wechat_delivery_retains_skipped(
    generation, conversation, task_id, error, expected,
):
    attempt = SimpleNamespace(send_status="skipped", channel="wechat", conversation_id="current")
    task = SimpleNamespace(id=1, status="done", execution_generation="g2")
    delivery = SimpleNamespace(task_id=task_id, execution_generation=generation,
                               conversation_id=conversation, status="skipped", error=error)
    assert project_attempt_status(attempt, task, [], delivery=delivery) == expected
