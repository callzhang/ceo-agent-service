"""Confined State paired-forward/current-fact inverse proposal verification."""
from pathlib import Path
import hashlib
import os
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
    "2026-10-16-state-pair-proposal.patch",
    "2026-10-16-state-pair-test-proposal.patch",
)
CASES = (
    "test_paired_publication_contains_email_and_business",
    "test_prepare_is_readonly_and_captures_exact_pair_schema_aliases_and_ledger",
    "test_publish_commits_both_markers_with_complete_target_then_inverse_is_repeatable",
    "test_state_forward_failure_restores_complete_source",
    "test_state_inverse_failure_restores_complete_target",
    "test_current_state_facts_survive_inverse_old_write_and_reforward",
    "test_state_marker_blob_timestamp_and_pipe_payload_round_trip",
    "test_unsupported_current_inverse_preserves_entire_database",
    "test_paired_state_declaration_drift_refuses_without_mutation",
)
with tempfile.TemporaryDirectory(prefix="state-pair-proposal-", dir="/tmp") as directory:
    temporary = Path(directory)
    shutil.copytree(ROOT / "app", temporary / "app", ignore=shutil.ignore_patterns("__pycache__"))
    (temporary / "tests").mkdir()
    (temporary / "tests/__init__.py").write_text("__path__.append(" + repr(str(ROOT / "tests")) + ")\n")
    for name in ("conftest.py", "test_email_action_schema_release.py"):
        shutil.copyfile(ROOT / "tests" / name, temporary / "tests" / name)
    (temporary / "docs").symlink_to(ROOT / "docs", target_is_directory=True)
    (temporary / "tests/fixtures").symlink_to(ROOT / "tests/fixtures", target_is_directory=True)
    for name in PATCHES:
        patch = ROOT / "docs/superpowers/plans" / name
        subprocess.run(["git", "apply", "--unidiff-zero", "--unsafe-paths", str(patch)],
                       cwd=temporary, check=True)
        print(name, hashlib.sha256(patch.read_bytes()).hexdigest(), flush=True)
    arguments = ["-q", "--tb=short", "-p", "no:cacheprovider", "--basetemp", str(temporary / "pytest")]
    arguments.extend(str(temporary / "tests/test_email_action_schema_release.py") + "::" + name
                     for name in CASES)
    driver = ("import pathlib,app,pytest;assert pathlib.Path(app.__file__).resolve().parent==pathlib.Path("
              + repr(str((temporary / "app").resolve())) + ");raise SystemExit(pytest.main("
              + repr(arguments) + "))")
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=str(temporary) + os.pathsep + str(ROOT))
    result = subprocess.run([str(PYTHON), "-c", driver], cwd=temporary, env=environment, check=False)
    status = result.returncode
print("owned_temp_removed", not temporary.exists(), flush=True)
raise SystemExit(status)
