import pytest

from app.external_retry import (
    ExternalAttempt,
    ExternalDependencyError,
    retry_delay_seconds,
    run_external,
)


def test_run_external_retries_then_returns_value():
    attempts = []
    sleeps = []

    def operation():
        attempts.append("call")
        if len(attempts) < 3:
            raise RuntimeError(f"transient {len(attempts)}")
        return {"ok": True}

    failures: list[ExternalAttempt] = []
    result = run_external(
        "dws okr fetch",
        operation,
        max_attempts=3,
        delay_seconds=1,
        sleep=sleeps.append,
        on_failure=failures.append,
    )

    assert result == {"ok": True}
    assert len(attempts) == 3
    assert sleeps == [1, 2]
    assert [failure.attempt for failure in failures] == [1, 2]
    assert failures[0].operation == "dws okr fetch"
    assert "transient 1" in failures[0].error


def test_run_external_preserves_external_dependency_after_max_attempts():
    failures: list[ExternalAttempt] = []

    def operation():
        raise RuntimeError("still down")

    with pytest.raises(ExternalDependencyError, match="still down") as excinfo:
        run_external(
            "codex exec",
            operation,
            max_attempts=2,
            delay_seconds=0,
            dependency="codex",
            sleep=lambda seconds: None,
            on_failure=failures.append,
        )

    assert [failure.attempt for failure in failures] == [1, 2]
    assert excinfo.value.operation == "codex exec"
    assert excinfo.value.dependency == "codex"
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_run_external_rejects_invalid_attempt_count():
    with pytest.raises(ValueError, match="max_attempts"):
        run_external("dws", lambda: None, max_attempts=0)


def test_run_external_rejects_invalid_backoff_multiplier():
    with pytest.raises(ValueError, match="backoff_multiplier"):
        run_external("dws", lambda: None, backoff_multiplier=0)


def test_retry_delay_seconds_uses_exponential_backoff_with_ceiling():
    assert retry_delay_seconds(0.25, 0, max_delay_seconds=0.75) == 0.25
    assert retry_delay_seconds(0.25, 1, max_delay_seconds=0.75) == 0.5
    assert retry_delay_seconds(0.25, 2, max_delay_seconds=0.75) == 0.75
    assert retry_delay_seconds(0.25, 3, max_delay_seconds=0.75) == 0.75
