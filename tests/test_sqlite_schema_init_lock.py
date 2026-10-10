from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time


def _wait_for(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return path.exists()


def test_email_store_waits_for_other_process_schema_initialization(
    tmp_path: Path,
) -> None:
    database = tmp_path / "shared.sqlite3"
    first_entered_initialize = tmp_path / "first-entered"
    second_entered_initialize = tmp_path / "second-entered"
    release_first = tmp_path / "release-first"
    repo_root = Path(__file__).resolve().parents[1]

    first_script = """
import sys, time
from pathlib import Path
from app.store import AutoReplyStore
original = AutoReplyStore._initialize
ready = Path(sys.argv[2])
release = Path(sys.argv[3])
def paused(self):
    ready.touch()
    deadline = time.monotonic() + 20
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('test did not release first initializer')
        time.sleep(0.01)
    return original(self)
AutoReplyStore._initialize = paused
AutoReplyStore(Path(sys.argv[1]))
"""
    second_script = """
import sys
from pathlib import Path
from app.email_store import EmailStore
original = EmailStore._initialize
entered = Path(sys.argv[2])
def observed(self, *args, **kwargs):
    entered.touch()
    return original(self, *args, **kwargs)
EmailStore._initialize = observed
EmailStore(Path(sys.argv[1]), validate_rows=False)
"""

    env = dict(os.environ)
    first = subprocess.Popen(
        [sys.executable, "-c", first_script, str(database), str(first_entered_initialize), str(release_first)],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second: subprocess.Popen[str] | None = None
    try:
        assert _wait_for(first_entered_initialize, timeout=15), "first initializer did not start"
        second = subprocess.Popen(
            [sys.executable, "-c", second_script, str(database), str(second_entered_initialize)],
            cwd=repo_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert not _wait_for(second_entered_initialize, timeout=1.0), (
            "EmailStore entered schema work before the other process released "
            "the shared database initialization lock"
        )
        release_first.touch()
        first_stdout, first_stderr = first.communicate(timeout=30)
        second_stdout, second_stderr = second.communicate(timeout=30)
        assert first.returncode == 0, first_stderr or first_stdout
        assert second.returncode == 0, second_stderr or second_stdout
        assert second_entered_initialize.exists()
    finally:
        release_first.touch()
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
