"""Confined committed progress binding proposal regression."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
PYTHON = Path("/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python")
with tempfile.TemporaryDirectory(prefix="state-progress-binding-", dir="/tmp") as directory:
    temporary = Path(directory)
    shutil.copytree(ROOT / "app", temporary / "app", ignore=shutil.ignore_patterns("__pycache__"))
    (temporary / "tests").mkdir()
    (temporary / "tests/__init__.py").write_text("__path__.append(" + repr(str(ROOT / "tests")) + ")\n")
    for name in ("conftest.py", "test_state_upgrade_progress.py", "test_repository_updater_publication.py"):
        shutil.copyfile(ROOT / "tests" / name, temporary / "tests" / name)
    (temporary / "docs").symlink_to(ROOT / "docs", target_is_directory=True)
    regression = ROOT / "docs/superpowers/plans/2026-10-16-state-progress-binding-regression.py"
    shutil.copyfile(regression, temporary / "tests/test_state_progress_binding.py")
    patch = ROOT / "docs/superpowers/plans/2026-10-16-state-progress-binding-proposal.patch"
    subprocess.run(["git", "apply", "--unidiff-zero", "--unsafe-paths", str(patch)],
                   cwd=temporary, check=True)
    print("patch", hashlib.sha256(patch.read_bytes()).hexdigest(), flush=True)
    files = ("test_state_progress_binding.py", "test_state_upgrade_progress.py",
             "test_repository_updater_publication.py")
    arguments = ["-q", "--tb=short", "-p", "no:cacheprovider", "--basetemp", str(temporary / "pytest")]
    arguments.extend(str(temporary / "tests" / name) for name in files)
    driver = ("import pathlib,app,pytest;assert pathlib.Path(app.__file__).resolve().parent==pathlib.Path("
              + repr(str((temporary / "app").resolve())) + ");raise SystemExit(pytest.main("
              + repr(arguments) + "))")
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=str(temporary) + os.pathsep + str(ROOT))
    result = subprocess.run([str(PYTHON), "-c", driver], cwd=temporary, env=environment, check=False)
    status = result.returncode
print("owned_temp_removed", not temporary.exists(), flush=True)
raise SystemExit(status)
