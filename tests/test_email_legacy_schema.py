"""Run historical Email schema assertions with their published source code."""

from collections import Counter
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_REF = "0441cf15fafe800f9ca4d67f667e87062ca62109"
SOURCE_PATHS = (
    "app",
    "tests",
    "pyproject.toml",
    "docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json",
)

STORE_TESTS = (
    "test_training_snapshot_migration_preserves_existing_rows",
    "test_v23_snapshot_migration_freezes_and_preserves_existing_observations",
    "test_v23_snapshot_migration_preserves_legacy_signed_manifest",
    "test_v18_feedback_table_migrates_and_accepts_custom_human_confirmation",
    "test_reopen_rehydrates_legacy_reserved_classification_plan_and_feedback",
    "test_v10_unsubscribe_claim_migrates_to_v11_effect_prefix_chain",
    "test_exact_v14_unsubscribe_schema_migrates_with_nullable_positive_audit_run",
    "test_v11_unsubscribe_schema_migrates_missing_phase_and_receipt_evidence",
    "test_v9_store_atomically_adds_unsubscribe_durability_without_data_loss",
    "test_migration_preserves_prototype_feedback_config_and_unrelated_state",
    "test_prototype_migration_rejects_non_text_column_metadata_atomically",
    "test_prototype_migration_recognizes_quoted_mixed_case_existing_columns",
    "test_task10_schema_migrations_replay_full_chain_from_each_version",
    "test_legitimate_v16_upgrades_to_v17_with_receipt_integrity_metadata",
    "test_v2_processed_without_plan_upgrades_to_explicit_legacy_once",
    "test_exact_v15_legacy_action_plan_upgrades_without_rewriting_history",
    "test_new_v16_action_plan_rejects_legacy_canonical_json_without_provenance",
    "test_upgraded_database_does_not_extend_legacy_permission_to_new_v16_plan",
    "test_startup_rejects_noncanonical_current_action_plan_snapshot",
    "test_v2_upgrade_does_not_reapply_prototype_classification_backfill",
    "test_prototype_plan_migration_backfills_plan_and_direct_actions",
    "test_migration_failure_rolls_back_and_can_recover",
    "test_concurrent_v16_to_v17_migration_is_transactionally_idempotent",
    "test_versioned_task3_upgrade_does_not_run_prototype_backfill",
    "test_v21_schema_migrates_to_allow_flag_important_actions",
    "test_unsubscribe_schema_migrates_and_durable_journal_receipt_survive_restart",
    "test_v16_migration_preserves_legacy_full_observation_digest_for_truncation",
    "test_v16_migration_rejects_corrupt_untruncated_observation_digest",
    "test_v19_category_config_migration_maps_active_rows_and_preserves_history",
    "test_v19_migration_disables_categories_when_enabled_account_has_no_binding",
    "test_v19_migration_rejects_structured_legacy_hybrid_table",
    "test_v20_folder_binding_schema_migrates_without_stripping_provider_names",
    "test_v36_drops_policy_keys_from_immutable_email_task_inputs",
    "test_v42_and_v43_give_existing_mailboxes_default_dual_windows",
    "test_v43_preserves_agent_window_and_adds_one_year_model_window",
)
REFERENCE_TESTS = (
    "test_v48_reference_migration_preserves_business_and_api",
    "test_v48_reference_migration_rejects_corruption_and_rolls_back",
    "test_v48_legacy_processed_without_plan_preserves_null_projection",
)
RETRY_TESTS = (
    "test_schema51_empty_inverse_preserves_data_and_versions",
    "test_schema51_inverse_refuses_to_discard_request",
)
WEB_API_TESTS = (
    "test_audit_app_starts_when_email_schema_is_unavailable",
)
UNSUBSCRIBE_DIRECT_TESTS = (
    "test_cancellation_storage_migrates_schema49_without_changing_plan_or_receipt",
    "test_cancellation_cli_preview_refuses_schema49_without_store_initialization",
)
REPLY_DELIVERY_TESTS = (
    "test_v7_store_migrates_fenced_reply_dispatch_claims_without_changing_rows",
    "test_v8_store_preserves_existing_claim_as_fenced_history",
)
WORKER_TESTS = (
    "test_v29_historical_queue_migration_securely_redacts_legacy_provider_record",
)
TASK_ADAPTER_TESTS = (
    "test_v25_classifier_queue_migration_preserves_task_and_scrubs_private_url",
    "test_v25_completed_null_unsubscribe_result_migrates_nullable_projection",
    "test_v25_completed_selected_result_preserves_schema_keys_and_replays",
    "test_v25_migration_redacts_only_sensitive_query_and_fragment_values",
    "test_v25_migration_classifies_shared_sensitive_parameter_families",
    "test_v25_migration_structurally_redacts_unicode_url_and_token_everywhere",
)
CLASSIFICATION_LOCATOR_INDEX_TESTS = (
    "test_uid_locator_query_uses_covering_folder_and_epoch_index",
    "test_locator_migration_preserves_business_rows_uid_metadata_and_existing_order",
    "test_locator_migration_validation_failure_rolls_back_index_and_version",
)
PROVIDER_LOOKUP_INDEX_TESTS = (
    "test_v47_migration_preserves_business_rows_and_reopens_idempotently",
    "test_failed_migration_restores_lookup_version_and_rows",
)
MODEL_CONTROLS_TESTS = ("test_v33_migration_backup_and_restart",)
TRAINING_STORAGE_TESTS = (
    "test_training_storage_migrates_latest_complete_legacy_snapshot",
    "test_training_migration_retains_latest_complete_snapshot_per_type",
)
SCHEMA_RELEASE_TESTS = (
    "test_schema50_publication_has_verified_inverse_for_empty_cancellation_relation",
    "test_schema50_rollback_refuses_new_human_decision_or_unknown_effect",
    "test_plan_inverse_preserves_new_classification_and_canonical_versions",
    "test_plan_inverse_failure_is_atomic",
    "test_plan_inverse_preserves_original_legacy_snapshot_byte_form",
)


@pytest.fixture(scope="module")
def published_source():
    with TemporaryDirectory(prefix="email13-5b12-oldsource-", dir="/tmp") as directory:
        source = Path(directory)
        bound = subprocess.run(
            ["git", "cat-file", "-e", f"{SOURCE_REF}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert bound.returncode == 0, (
            f"required published Email source {SOURCE_REF} is unavailable; "
            f"the checkout needs full history:\n{bound.stderr}"
        )
        archive = subprocess.run(
            ["git", "archive", SOURCE_REF, *SOURCE_PATHS],
            cwd=ROOT,
            capture_output=True,
        )
        assert archive.returncode == 0, (
            f"failed to archive required Email source {SOURCE_REF}:\n"
            f"{archive.stderr.decode(errors='replace')}"
        )
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
            bundle.extractall(source, filter="data")
        assert (source / "tests" / "conftest.py").is_file()
        assert (source / "app" / "email_store.py").is_file()
        yield source


def _run_published_tests(
    source: Path,
    *,
    group: str,
    module: str,
    names: tuple[str, ...],
    extra_cases: dict[str, int],
    expected_functions: int,
    expected_cases: int,
) -> None:
    report = source / f"{group}-junit.xml"
    expected = Counter({name: extra_cases.get(name, 1) for name in names})
    assert len(names) == len(set(names)) == expected_functions
    assert sum(expected.values()) == expected_cases
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            f"--junitxml={report}",
            "--basetemp",
            str(source / f"{group}-pytest"),
            *(f"tests/{module}.py::{name}" for name in names),
        ],
        cwd=source,
        env={**os.environ, "PYTHONPATH": str(source), "PYTEST_ADDOPTS": ""},
        text=True,
        capture_output=True,
        timeout=300,
    )
    observed = Counter()
    failed = []
    if report.exists():
        for case in ElementTree.parse(report).iter("testcase"):
            observed[case.attrib["name"].split("[", 1)[0]] += 1
            if any(case.find(outcome) is not None for outcome in ("failure", "error", "skipped")):
                failed.append(case.attrib["name"])
    assert result.returncode == 0 and observed == expected and not failed, (
        f"published {group} tests at {SOURCE_REF}: exit={result.returncode}, "
        f"expected={expected}, observed={observed}, failed={failed}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_published_email_store_migrations(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="store",
        module="test_email_store",
        names=STORE_TESTS,
        extra_cases={
            "test_reopen_rehydrates_legacy_reserved_classification_plan_and_feedback": 3,
            "test_task10_schema_migrations_replay_full_chain_from_each_version": 4,
        },
        expected_functions=35,
        expected_cases=40,
    )


def test_published_email_reference_migrations(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="reference",
        module="test_email_action_plan_reference",
        names=REFERENCE_TESTS,
        extra_cases={"test_v48_reference_migration_rejects_corruption_and_rolls_back": 4},
        expected_functions=3,
        expected_cases=6,
    )


def test_published_email_retry_migrations(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="retry",
        module="test_email_retry_schema_release",
        names=RETRY_TESTS,
        extra_cases={},
        expected_functions=2,
        expected_cases=2,
    )


def test_published_email_web_api_schema_isolation(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="web-api",
        module="test_email_web_api",
        names=WEB_API_TESTS,
        extra_cases={},
        expected_functions=1,
        expected_cases=1,
    )


def test_published_email_unsubscribe_direct_migrations(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="unsubscribe-direct",
        module="test_email_unsubscribe_direct",
        names=UNSUBSCRIBE_DIRECT_TESTS,
        extra_cases={},
        expected_functions=2,
        expected_cases=2,
    )


def test_published_email_reply_delivery_migrations(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="reply-delivery",
        module="test_email_reply_delivery",
        names=REPLY_DELIVERY_TESTS,
        extra_cases={},
        expected_functions=2,
        expected_cases=2,
    )


def test_published_email_worker_migration(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="worker",
        module="test_email_worker",
        names=WORKER_TESTS,
        extra_cases={},
        expected_functions=1,
        expected_cases=1,
    )


def test_published_email_task_adapter_migrations(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="task-adapter",
        module="test_email_task_adapter",
        names=TASK_ADAPTER_TESTS,
        extra_cases={
            "test_v25_migration_classifies_shared_sensitive_parameter_families": 46,
        },
        expected_functions=6,
        expected_cases=51,
    )


def test_published_email_classification_locator_index(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="classification-locator-index",
        module="test_email_classification_locator_index",
        names=CLASSIFICATION_LOCATOR_INDEX_TESTS,
        extra_cases={},
        expected_functions=3,
        expected_cases=3,
    )


def test_published_email_provider_lookup_index(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="provider-lookup-index",
        module="test_email_provider_lookup_index",
        names=PROVIDER_LOOKUP_INDEX_TESTS,
        extra_cases={},
        expected_functions=2,
        expected_cases=2,
    )


def test_published_email_model_controls(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="model-controls",
        module="test_email_model_controls",
        names=MODEL_CONTROLS_TESTS,
        extra_cases={},
        expected_functions=1,
        expected_cases=1,
    )


def test_published_email_training_storage(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="training-storage",
        module="test_email_training_storage",
        names=TRAINING_STORAGE_TESTS,
        extra_cases={"test_training_storage_migrates_latest_complete_legacy_snapshot": 2},
        expected_functions=2,
        expected_cases=3,
    )


def test_published_email_schema_release(published_source: Path) -> None:
    _run_published_tests(
        published_source,
        group="schema-release",
        module="test_email_schema_release",
        names=SCHEMA_RELEASE_TESTS,
        extra_cases={
            "test_schema50_rollback_refuses_new_human_decision_or_unknown_effect": 2,
            "test_plan_inverse_preserves_original_legacy_snapshot_byte_form": 2,
        },
        expected_functions=5,
        expected_cases=7,
    )
