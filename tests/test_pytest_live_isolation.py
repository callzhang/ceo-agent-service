import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_TEST = "tests/test_meeting_alignment_eval.py"


def _collect(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "--collect-only", "-q", LIVE_TEST],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_default_collection_excludes_live_tests():
    result = _collect()

    assert result.returncode == 5
    assert "no tests collected (9 deselected)" in result.stdout
    assert "test_live_meeting_alignment_semantics" not in result.stdout


def test_ordinary_marker_expression_does_not_enable_live_tests():
    result = _collect("-m", "not nonexistent_marker")

    assert result.returncode == 5
    assert "no tests collected (9 deselected)" in result.stdout
    assert "test_live_meeting_alignment_semantics" not in result.stdout


def test_live_marker_without_opt_in_does_not_enable_live_tests():
    result = _collect("-m", "live")

    assert result.returncode == 5
    assert "no tests collected (9 deselected)" in result.stdout


def test_explicit_live_opt_in_selects_live_tests():
    result = _collect("--run-live", "-m", "live")

    assert result.returncode == 0
    assert "9 tests collected" in result.stdout
    assert result.stdout.count("test_live_meeting_alignment_semantics") == 9


def test_live_opt_in_is_available_during_collect_only():
    result = _collect("--run-live")

    assert result.returncode == 0
    assert "9 tests collected" in result.stdout
