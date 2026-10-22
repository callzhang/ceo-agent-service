"""Whole State candidate integration before application selection."""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
APPLIED = "--applied" in sys.argv[1:]
PYTHON = Path("/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python")
PATCHES = (
    "2026-10-16-state-store-dao-proposal.patch",
    "2026-10-16-state-store-lifecycle-proposal.patch",
    "2026-10-16-state-store-mixed-proposal.patch",
    "2026-10-16-state-dispatcher-proposal.patch",
    "2026-10-16-state-constructor-proposal.patch",
    "2026-10-16-state-email-current-proposal.patch",
    "2026-10-16-state-current-direct-proposal.patch",
    "2026-10-16-state-publication-proposal.patch",
    "2026-10-16-state-pair-proposal.patch",
    "2026-10-16-state-progress-binding-proposal.patch",
    "2026-10-16-state-email-current-test-proposal.patch",
    "2026-10-16-state-current-direct-test-proposal.patch",
    "2026-10-16-state-publication-test-proposal.patch",
    "2026-10-16-state-pair-test-proposal.patch",
)
SELECTED = (
    "test_processing_reaction.py",
    "test_quality_gate.py",
    "test_rule_question_retirement.py",
    "test_reply_task_deferral.py",
    "test_state_publication_marker.py",
    "test_email_action_external_consumers.py::test_generic_publication_readers_accept_store_only_target",
    "test_email_action_external_consumers.py::test_complete_status_and_maintenance_paths_accept_current_target",
    'test_state_constructor_integration.py',
    'test_state_progress_binding.py',
    'test_state_upgrade_progress.py',
    'test_repository_updater_publication.py',
    'test_dispatcher_idle_claims.py',
    'test_email_action_schema_release.py::test_paired_publication_contains_email_and_business',
    'test_email_action_schema_release.py::test_prepare_is_readonly_and_captures_exact_pair_schema_aliases_and_ledger',
    'test_email_action_schema_release.py::test_publish_commits_both_markers_with_complete_target_then_inverse_is_repeatable',
    'test_email_action_schema_release.py::test_state_forward_failure_restores_complete_source',
    'test_email_action_schema_release.py::test_state_inverse_failure_restores_complete_target',
    'test_email_action_schema_release.py::test_current_state_facts_survive_inverse_old_write_and_reforward',
    'test_email_action_schema_release.py::test_state_marker_blob_timestamp_and_pipe_payload_round_trip',
    'test_email_action_schema_release.py::test_unsupported_current_inverse_preserves_entire_database',
    'test_email_action_schema_release.py::test_paired_state_declaration_drift_refuses_without_mutation',
    'test_email_store.py::test_cursor_creation_and_same_generation_advancement_are_monotonic',
    'test_email_store.py::test_empty_scan_cursor_initialization_and_reset_use_compare_and_set',
    'test_email_store.py::test_cursor_generation_reset_requires_compare_and_set',
    'test_email_store.py::test_same_generation_update_rejects_stale_uidvalidity_expectation_atomically',
    'test_email_store.py::test_cursor_expectation_requires_cursor_progress',
    'test_email_store.py::test_stale_cursor_generation_commit_rolls_back_all_scan_state',
    'test_email_store.py::test_atomic_scan_persistence_rolls_back_message_plan_actions_and_cursor',
    'test_email_model_progress.py::test_progress_is_summed_over_every_folder',
    'test_email_model_progress.py::test_no_progress_is_none_rather_than_a_full_bar',
    'test_email_model_progress.py::test_a_malformed_progress_row_is_skipped',
    'test_email_worker.py::test_historical_deferred_queue_is_fair_durable_and_not_a_busy_loop',
    'test_email_worker.py::test_run_historical_once_uses_provider_rereads_cached_batch_and_durable_history',
    'test_email_web_api.py::test_processing_progress_reports_exact_queue_counts_and_scan_cursors',
    'test_email_web_api.py::test_processing_progress_counts_a_failed_action_the_way_the_list_filter_does',
    'test_email_web_api.py::test_processing_progress_and_the_list_filter_split_failed_actions_by_whether_they_retry',
    'test_email_unsubscribe_direct.py::test_principal_cancellation_preserves_subscription_without_provider_receipt',
    'test_email_unsubscribe_direct.py::test_principal_cancellation_rejects_changed_or_active_identity',
    'test_email_unsubscribe_direct.py::test_cancellation_cannot_interleave_before_direct_receipt_persistence',
    'test_email_unsubscribe_direct.py::test_failed_receipt_persistence_requires_reconciliation_before_cancellation',
    'test_email_unsubscribe_direct.py::test_cancellation_owner_check_uses_dispatcher_namespace',
    'test_email_action_validation.py::test_public_readonly_cancellation_preview_closes_shape_dependency',
)
with tempfile.TemporaryDirectory(prefix="state-whole-integration-", dir="/tmp") as directory:
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
    shutil.copyfile(ROOT / "docs/superpowers/plans/2026-10-16-state-constructor-regression.py",
                    temporary / "tests/test_state_constructor_integration.py")
    shutil.copyfile(ROOT / "docs/superpowers/plans/2026-10-16-state-progress-binding-regression.py",
                    temporary / "tests/test_state_progress_binding.py")
    (temporary / "tests/fixtures").symlink_to(ROOT / "tests/fixtures", target_is_directory=True)
    (temporary / "docs").symlink_to(ROOT / "docs", target_is_directory=True)
    (temporary / "data/config").mkdir(parents=True)
    for name in ("skill-features.json", "mcp-tool-effects.json"):
        shutil.copyfile(ROOT / "data/config" / name, temporary / "data/config" / name)
    if not APPLIED:
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
    for module in (temporary / "app").rglob("*.py"):
        compile(module.read_bytes(), str(module), "exec")
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
