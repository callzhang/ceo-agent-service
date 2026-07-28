import os
import sys
import time

import pytest

from app.process_runner import run_process_with_idle_timeout


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


def test_process_runner_emits_complete_stdout_lines():
    lines = []

    result = run_process_with_idle_timeout(
        [sys.executable, "-c", "print('one'); print('two')"],
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=5,
        on_stdout_line=lines.append,
    )

    assert result.returncode == 0
    assert lines == ["one", "two"]


def test_process_runner_preserves_split_multibyte_stdout_for_callback():
    lines = []
    script = (
        "import os, sys, time; "
        "data='你好'.encode(); "
        "os.write(sys.stdout.fileno(), data[:1]); "
        "time.sleep(0.1); "
        "os.write(sys.stdout.fileno(), data[1:] + b'\\n')"
    )

    result = run_process_with_idle_timeout(
        [sys.executable, "-c", script],
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=5,
        on_stdout_line=lines.append,
    )

    assert result.stdout == "你好"
    assert lines == ["你好"]


def test_process_runner_flushes_final_nonnewline_stdout_line():
    lines = []

    result = run_process_with_idle_timeout(
        [sys.executable, "-c", "print('tail', end='')"],
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=5,
        on_stdout_line=lines.append,
    )

    assert result.stdout == "tail"
    assert lines == ["tail"]


def test_process_runner_callback_error_terminates_child_and_propagates():
    child_pids = []

    def reject_line(line: str) -> None:
        child_pids.append(int(line))
        raise RuntimeError("event persistence failed")

    started_at = time.monotonic()
    with pytest.raises(RuntimeError, match="event persistence failed"):
        run_process_with_idle_timeout(
            [
                sys.executable,
                "-c",
                "import os, time; print(os.getpid(), flush=True); time.sleep(30)",
            ],
            prompt="",
            env=None,
            total_timeout_seconds=30,
            idle_timeout_seconds=30,
            on_stdout_line=reject_line,
        )

    assert time.monotonic() - started_at < 3
    assert len(child_pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(child_pids[0], 0)


def test_process_runner_timeout_flushes_partial_stdout_line_once():
    lines = []

    result = run_process_with_idle_timeout(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.stdout.write('tail'); sys.stdout.flush(); time.sleep(30)",
        ],
        prompt="",
        env=None,
        total_timeout_seconds=30,
        idle_timeout_seconds=0.2,
        on_stdout_line=lines.append,
    )

    assert result.timed_out is True
    assert result.timeout_kind == "idle"
    assert result.stdout == "tail"
    assert lines == ["tail"]


def test_process_runner_without_callback_preserves_existing_result_shape():
    result = run_process_with_idle_timeout(
        [sys.executable, "-c", "print('unchanged')"],
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=5,
    )

    assert result.returncode == 0
    assert result.stdout == "unchanged"
    assert result.stderr == ""
