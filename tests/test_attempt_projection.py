from types import SimpleNamespace

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
