import sys

from ceo_agent_service.process_runner import run_process_with_idle_timeout


def test_process_runner_kills_child_after_idle_timeout():
    result = run_process_with_idle_timeout(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        ],
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=0.2,
    )

    assert result.timed_out is True
    assert result.timeout_kind == "idle"
    assert "produced no output" in result.timeout_reason


def test_process_runner_keeps_process_alive_when_output_continues():
    result = run_process_with_idle_timeout(
        [
            sys.executable,
            "-c",
            "import sys, time; print('first'); sys.stdout.flush(); time.sleep(0.1); print('second')",
        ],
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=1,
    )

    assert result.timed_out is False
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["first", "second"]
