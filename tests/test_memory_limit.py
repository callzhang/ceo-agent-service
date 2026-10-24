import subprocess
import sys
import textwrap

from tests.conftest import MEMORY_LIMIT
from tests.support.memory_limit import EXIT_CODE


def test_limit_is_three_gigabytes():
    assert MEMORY_LIMIT.limit_bytes == 3 * 1024**3


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
