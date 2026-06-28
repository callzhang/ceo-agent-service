import pytest

from app.external_retry import ExternalAttempt, run_external


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


def test_run_external_reraises_final_error_after_max_attempts():
    failures: list[ExternalAttempt] = []

    def operation():
        raise RuntimeError("still down")

    with pytest.raises(RuntimeError, match="still down"):
        run_external(
            "codex exec",
            operation,
            max_attempts=2,
            delay_seconds=0,
            sleep=lambda seconds: None,
            on_failure=failures.append,
        )

    assert [failure.attempt for failure in failures] == [1, 2]


def test_run_external_rejects_invalid_attempt_count():
    with pytest.raises(ValueError, match="max_attempts"):
        run_external("dws", lambda: None, max_attempts=0)


def test_run_external_rejects_invalid_backoff_multiplier():
    with pytest.raises(ValueError, match="backoff_multiplier"):
        run_external("dws", lambda: None, backoff_multiplier=0)
