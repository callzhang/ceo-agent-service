"""Run retired Store bootstrap tests against the published predecessor code."""

from collections import Counter
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
SOURCE_REF = "7dadc492c1c62d5b0624f2f97dd0c89f4f358646"
SOURCE_PATHS = ("app", "tests", "pyproject.toml", "docs/superpowers/specs")
HISTORICAL_TESTS = (
    "test_existing_pre_semantic_store_initializes_without_rewriting_legacy",
    "test_previous_semantic_schema_migrates_without_classifying_legacy_deadline",
    "test_event_constraint_migration_rolls_back_when_copy_fails",
    "test_event_constraint_migration_recovers_stranded_rename",
    "test_legacy_signal_bodies_migrate_without_changing_evidence_identity_or_history",
    "test_source_body_migration_rolls_back_on_foreign_key_failure",
    "test_source_body_migration_preserves_exact_preexisting_foreign_key_violations",
    "test_source_body_migration_rolls_back_if_it_adds_a_foreign_key_violation",
)
HISTORICAL_CRM_TEST = "test_existing_project_schema_migration_keeps_identity_and_leaves_customer_null"


def test_retired_store_bootstrap_contracts_under_published_source():
    assert len(HISTORICAL_TESTS) == len(set(HISTORICAL_TESTS)) == 8
    expected = Counter({name: 1 for name in HISTORICAL_TESTS})
    expected["test_legacy_signal_bodies_migrate_without_changing_evidence_identity_or_history"] = 2
    expected[HISTORICAL_CRM_TEST] = 1
    assert sum(expected.values()) == 10
    with TemporaryDirectory(prefix="business-retired-store-source-", dir="/tmp") as directory:
        source = Path(directory)
        bound = subprocess.run(
            ["git", "cat-file", "-e", f"{SOURCE_REF}^{{commit}}"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert bound.returncode == 0, f"published Store source unavailable: {bound.stderr}"
        archive = subprocess.run(
            ["git", "archive", SOURCE_REF, *SOURCE_PATHS],
            cwd=ROOT, capture_output=True,
        )
        assert archive.returncode == 0, archive.stderr.decode(errors="replace")
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
            bundle.extractall(source, filter="data")
        assert (source / "tests/test_task_semantic_store.py").is_file()
        assert (source / "app/store.py").is_file()
        report = source / "historical-store-junit.xml"
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", f"--junitxml={report}",
                "--basetemp", str(source / "pytest"),
                *(f"tests/test_task_semantic_store.py::{name}" for name in HISTORICAL_TESTS),
                f"tests/test_project_crm_customer.py::{HISTORICAL_CRM_TEST}",
            ],
            cwd=source,
            env={**os.environ, "PYTHONPATH": str(source), "PYTEST_ADDOPTS": ""},
            text=True, capture_output=True, timeout=300,
        )
        observed = Counter()
        failed = []
        if report.exists():
            for case in ElementTree.parse(report).iter("testcase"):
                observed[case.attrib["name"].split("[", 1)[0]] += 1
                if any(case.find(outcome) is not None for outcome in ("failure", "error", "skipped")):
                    failed.append(case.attrib["name"])
        assert result.returncode == 0 and observed == expected and not failed, (
            f"published Store bootstrap at {SOURCE_REF}: exit={result.returncode}, "
            f"expected={expected}, observed={observed}, failed={failed}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
