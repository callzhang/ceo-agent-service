"""Disposable current State consumer/publication proposal verification."""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
PYTHON = Path("/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python")
PATCHES = (
    "2026-10-16-state-store-dao-proposal.patch",
    "2026-10-16-state-store-lifecycle-proposal.patch",
    "2026-10-16-state-store-mixed-proposal.patch",
    "2026-10-16-state-constructor-proposal.patch",
    "2026-10-16-state-email-current-proposal.patch",
    "2026-10-16-state-current-direct-proposal.patch",
    "2026-10-16-state-publication-proposal.patch",
    "2026-10-16-state-current-direct-test-proposal.patch",
    "2026-10-16-state-publication-test-proposal.patch",
)
SELECTED = (
    "test_processing_reaction.py",
    "test_quality_gate.py",
    "test_rule_question_retirement.py",
    "test_reply_task_deferral.py",
    "test_state_publication_marker.py",
    "test_email_action_external_consumers.py::test_generic_publication_readers_accept_store_only_target",
    "test_email_action_external_consumers.py::test_complete_status_and_maintenance_paths_accept_current_target",
)
with tempfile.TemporaryDirectory(prefix="state-publication-proposal-", dir="/tmp") as directory:
    temporary = Path(directory)
    shutil.copytree(ROOT / "app", temporary / "app", ignore=shutil.ignore_patterns("__pycache__"))
    (temporary / "tests").mkdir()
    # Retain the original test isolation, plus source-only fixture imports.
    (temporary / "tests/__init__.py").write_text("__path__.append(" + repr(str(ROOT / "tests")) + ")\n")
    tests = {item.split("::", 1)[0] for item in SELECTED}
    tests.add("conftest.py")
    for name in tests:
        source = ROOT / "tests" / name
        if source.exists():
            shutil.copyfile(source, temporary / "tests" / name)
    (temporary / "docs").symlink_to(ROOT / "docs", target_is_directory=True)
    (temporary / "data/config").mkdir(parents=True)
    for name in ("skill-features.json", "mcp-tool-effects.json"):
        shutil.copyfile(ROOT / "data/config" / name, temporary / "data/config" / name)
    for name in PATCHES:
        patch = ROOT / "docs/superpowers/plans" / name
        # All modified test files must exist before applying a zero-context patch.
        for target in re.findall(r"^--- a/(tests/[^\n]+)$", patch.read_text(), re.MULTILINE):
            path = temporary / target
            if not path.exists():
                shutil.copyfile(ROOT / target, path)
        subprocess.run(["git", "apply", "--unidiff-zero", "--unsafe-paths", str(patch)],
                       cwd=temporary, check=True)
        print(name, hashlib.sha256(patch.read_bytes()).hexdigest(), flush=True)
    arguments = ["-q", "--tb=short", "-p", "no:cacheprovider", "--basetemp", str(temporary / "pytest")]
    arguments.extend(str(temporary / "tests" / item) for item in SELECTED)
    driver = ("import pathlib,app,pytest;assert pathlib.Path(app.__file__).resolve().parent==pathlib.Path("
              + repr(str((temporary / "app").resolve())) + ");raise SystemExit(pytest.main("
              + repr(arguments) + "))")
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=str(temporary) + os.pathsep + str(ROOT))
    result = subprocess.run([str(PYTHON), "-c", driver], cwd=temporary, env=environment, check=False)
    status = result.returncode
print("owned_temp_removed", not temporary.exists(), flush=True)
raise SystemExit(status)
