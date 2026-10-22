"""Disposable execution of the finite Tasks8 native SQL-parts regression module."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
PYTHON = "/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python"
with TemporaryDirectory(prefix="ceo-task8-parts-", dir="/tmp") as owned:
    temporary = Path(owned)
    shutil.copytree(
        ROOT / "app", temporary / "app", ignore=shutil.ignore_patterns("__pycache__")
    )
    (temporary / "tests").mkdir()
    (temporary / "tests/__init__.py").write_text("")
    shutil.copyfile(ROOT / "tests/conftest.py", temporary / "tests/conftest.py")
    applied = "--applied" in sys.argv
    source = ROOT / (
        "tests/test_task8_storage.py"
        if applied
        else "docs/superpowers/plans/2026-10-16-task8-parts-regression.py"
    )
    shutil.copyfile(source, temporary / "tests/test_task8_storage.py")
    for name in ("test_task20_definitions.py",):
        shutil.copyfile(ROOT / "tests" / name, temporary / "tests" / name)
    if not applied:
        shutil.copyfile(
            ROOT / "docs/superpowers/plans/2026-10-16-task8-parts-candidate.py",
            temporary / "app/task8_storage.py",
        )
    (temporary / "docs").symlink_to(ROOT / "docs", target_is_directory=True)
    (temporary / "data/config").mkdir(parents=True)
    for name in ("skill-features.json", "mcp-tool-effects.json"):
        shutil.copyfile(ROOT / "data/config" / name, temporary / "data/config" / name)
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(temporary),
    }
    driver = (
        "from pathlib import Path;import app,pytest;"
        "assert Path(app.__file__).resolve().parent==Path("
        + repr(str(temporary / "app"))
        + ").resolve();"
        "raise SystemExit(pytest.main("
        + repr(
            [
                "-q",
                "--tb=short",
                "-p",
                "no:cacheprovider",
                str(temporary / "tests/test_task8_storage.py"),
                *(
                    [str(temporary / "tests/test_task20_definitions.py")]
                    if "--with-declarations" in sys.argv
                    else []
                ),
                "--basetemp",
                str(temporary / "pytest"),
                *[
                    arg
                    for arg in sys.argv[1:]
                    if arg
                    not in ("--applied", "--with-declarations", "--before-repair")
                ],
            ]
        )
        + "))"
    )
    result = subprocess.run(
        [PYTHON, "-c", driver], cwd=temporary, env=environment, check=False
    )
    code = result.returncode
print("OWNED_TEMP_REMOVED", not temporary.exists(), flush=True)
raise SystemExit(code)
