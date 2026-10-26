from pathlib import Path
import subprocess
import sys
import textwrap

from tests.conftest import MEMORY_LIMIT
from tests.support.memory_limit import EXIT_CODE, TIME_EXIT_CODE


def test_limits_are_two_gigabytes_and_five_minutes():
    # Derek, 2026-10-25. Changing either number is a decision, not a tuning knob.
    assert MEMORY_LIMIT.limit_bytes == 2 * 1024**3
    assert MEMORY_LIMIT.time_limit_seconds == 5 * 60


def test_limits_cannot_be_changed_from_outside():
    import ast

    module = ast.parse(Path("tests/support/memory_limit.py").read_text())
    names = {node.attr for node in ast.walk(module) if isinstance(node, ast.Attribute)}
    assert not names & {"environ", "getenv"}

    conftest = ast.parse(Path("tests/conftest.py").read_text())
    creation = next(
        node.value for node in conftest.body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "MEMORY_LIMIT"
    )
    literal = [*creation.args, *(keyword.value for keyword in creation.keywords)]
    assert all(
        all(isinstance(part, (ast.Constant, ast.BinOp, ast.operator)) for part in ast.walk(argument))
        for argument in literal
    )


def test_process_over_the_limit_is_ended_and_names_the_test(tmp_path):
    script = tmp_path / "hog.py"
    script.write_text(
        textwrap.dedent(
            """
            import time
            from tests.support.memory_limit import MemoryLimit

            limit = MemoryLimit(150 * 1024**2)
            limit.current_test = "tests/test_hog.py::test_allocates"
            limit.start()
            block = bytearray(400 * 1024**2)
            for offset in range(0, len(block), 4096):
                block[offset] = 1
            time.sleep(10)
            print("survived")
            """
        )
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PYTHONPATH": "."},
    )
    assert result.returncode == EXIT_CODE
    assert "survived" not in result.stdout
    assert "MEMORY LIMIT EXCEEDED" in result.stderr
    assert "tests/test_hog.py::test_allocates" in result.stderr
    assert "must not be raised" in result.stderr


def test_process_under_the_limit_is_left_alone(tmp_path):
    script = tmp_path / "small.py"
    script.write_text(
        "import time\n"
        "from tests.support.memory_limit import MemoryLimit\n"
        "MemoryLimit(2 * 1024**3).start()\n"
        "time.sleep(0.6)\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PYTHONPATH": "."},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_process_over_the_time_limit_is_ended_and_names_the_test(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text(
        textwrap.dedent(
            """
            import time
            from tests.support.memory_limit import MemoryLimit

            limit = MemoryLimit(2 * 1024**3, time_limit_seconds=1)
            limit.current_test = "tests/test_slow.py::test_sleeps"
            limit.start()
            time.sleep(10)
            print("survived")
            """
        )
    )
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=30, env={"PYTHONPATH": "."},
    )
    assert result.returncode == TIME_EXIT_CODE
    assert "survived" not in result.stdout
    assert "TIME LIMIT EXCEEDED" in result.stderr
    assert "tests/test_slow.py::test_sleeps" in result.stderr
    assert "must not be raised" in result.stderr


def test_process_inside_the_time_limit_is_left_alone(tmp_path):
    script = tmp_path / "quick.py"
    script.write_text(
        "import time\n"
        "from tests.support.memory_limit import MemoryLimit\n"
        "MemoryLimit(2 * 1024**3, time_limit_seconds=30).start()\n"
        "time.sleep(0.6)\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=30, env={"PYTHONPATH": "."},
    )
    assert result.returncode == 0 and result.stdout.strip() == "ok"
