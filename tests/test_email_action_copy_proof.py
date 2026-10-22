"""Bounded offline Email13 proof regressions against genuine published old code."""

from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from scripts import validate_action_email_copy as proof
from scripts.validate_action_email_copy import _case, _old_process, run

PUBLISHED_OLD_REF = "9cd41bcf5ea43a275eeb1376817d37f7cef6bd21"
LATEST_OLD_REF = "7dadc492c1c62d5b0624f2f97dd0c89f4f358646"


@pytest.fixture
def old_fresh(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    old = tmp_path / "old"
    old.mkdir()
    archive = subprocess.check_output(["git", "-C", str(repo), "archive", PUBLISHED_OLD_REF, "app"])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(old, filter="data")
    path = tmp_path / "fresh.sqlite3"
    subprocess.run([
        sys.executable, "-c",
        "from pathlib import Path; import sys; from app.store import AutoReplyStore; "
        "from app.email_store import EmailStore; p=Path(sys.argv[1]); AutoReplyStore(p); EmailStore(p)",
        str(path),
    ], cwd=old, env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True, text=True, check=True, timeout=300)
    return old, path


def test_old_constructor_rejects_null_plan_on_unmigrated_source(old_fresh):
    old, path = old_fresh
    seeded = _old_process(old, path, "seed")
    with closing(sqlite3.connect(path)) as db:
        columns = [row[1] for row in db.execute("pragma table_xinfo(email_action_plans)") if row[6] == 0]
        original = dict(zip(columns, db.execute(
            "select * from email_action_plans where action_plan_id=?",
            (seeded["receipt_plan_id"],)).fetchone(), strict=True))
        for version in (2, 3):
            row = original | {"action_plan_id": None, "action_plan_version": version}
            db.execute(f"insert into email_action_plans({','.join(columns)}) "
                       f"values({','.join('?' for _ in columns)})",
                       tuple(row[column] for column in columns))
        assert db.execute("select count(distinct rowid) from email_action_plans "
                          "where action_plan_id is null").fetchone() == (2,)
        db.commit()
    result = subprocess.run([
        sys.executable, "-c",
        "from pathlib import Path; import sys; from app.store import AutoReplyStore; "
        "from app.email_store import EmailStore; p=Path(sys.argv[1]); AutoReplyStore(p); EmailStore(p)",
        str(path),
    ], cwd=old, env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True, text=True, timeout=300)
    assert result.returncode != 0
    assert "EmailPersistenceCorruption: invalid immutable ActionPlan None" in result.stderr


def test_sql_legal_null_plans_survive_fresh_forward_inverse_reforward(old_fresh):
    old, path = old_fresh
    result = _case(path, old, fresh=True, null_plans=True)
    assert result["sql_legal_null_plan_rows"] == 2
    assert result["reforward_values_types_locators_waters_equal"]
    assert not result["old_public_feedback_replay"]


def test_source_ref_must_be_current_published_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(proof, "PRODUCTION_ROOT", Path(__file__).resolve().parents[1])
    with pytest.raises(AssertionError):
        run(tmp_path / "not-opened.sqlite3", "not-a-published-ref", Path(__file__).resolve().parents[1])


@pytest.mark.parametrize("damage", ("value", "rowid", "water"))
def test_copy_harness_rejects_current_fact_loss(old_fresh, monkeypatch, damage):
    old, path = old_fresh
    original = proof.migrate_email_action_schema

    def damaged_migration(db):
        state = original(db)
        if damage == "value":
            db.execute("update actions set payload_json=json_set(payload_json,'$.category','changed') "
                       "where record_type='email_action_plans'")
        elif damage == "rowid":
            db.execute("update actions set legacy_key='999999' "
                       "where record_type='email_action_plans' and id=(select min(id) "
                       "from actions where record_type='email_action_plans')")
        else:
            db.execute("delete from sqlite_sequence where name='actions:email_action_attempts'")
            db.execute("insert into sqlite_sequence(name,seq) "
                       "values('actions:email_action_attempts',99)")
        return state

    monkeypatch.setattr(proof, "migrate_email_action_schema", damaged_migration)
    with pytest.raises(AssertionError):
        _case(path, old, fresh=True, null_plans=False)


def test_copy_harness_requires_real_old_public_write(old_fresh, monkeypatch):
    old, path = old_fresh
    original = proof._old_process

    def no_write(root, database, mode, *args):
        if mode == "write":
            return {"feedback_applied": True, "feedback_replayed": True,
                    "receipt_read_write": True}
        return original(root, database, mode, *args)

    monkeypatch.setattr(proof, "_old_process", no_write)
    with pytest.raises((AssertionError, RuntimeError)):
        _case(path, old, fresh=True, null_plans=False)


def test_blank_receipt_is_seeded_before_first_forward(old_fresh, monkeypatch):
    old, path = old_fresh
    original = proof.migrate_email_action_schema
    first = []

    def checked_migration(db):
        if not first:
            first.append(True)
            assert db.execute("select count(*) from email_reply_receipts where "
                              "action_identity='copy-only-reply' and "
                              "json_extract(provider_receipt_json,'$.smtp_acceptance_id')=''"
                              ).fetchone() == (1,)
        return original(db)

    monkeypatch.setattr(proof, "migrate_email_action_schema", checked_migration)
    assert _case(path, old, fresh=True, null_plans=False)["current_fact_inverse"]
    assert first == [True]


def test_missing_target_current_facts_fails_before_inverse(old_fresh, monkeypatch):
    old, path = old_fresh
    monkeypatch.setattr(proof, "_target_current_from_donor", lambda *args: None)
    with pytest.raises(AssertionError):
        _case(path, old, fresh=True, null_plans=True)


def test_fixture_account_row_corruption_is_not_excluded(old_fresh, monkeypatch):
    old, path = old_fresh
    original = proof.migrate_email_action_schema

    def changed_account(db):
        state = original(db)
        db.execute("update email_accounts set display_name='corrupted' "
                   "where account_id='copy-only-account'")
        return state

    monkeypatch.setattr(proof, "migrate_email_action_schema", changed_account)
    with pytest.raises(AssertionError):
        _case(path, old, fresh=True, null_plans=False)


def test_second_forward_fixture_corruption_is_rejected(old_fresh, monkeypatch):
    old, path = old_fresh
    original = proof.migrate_email_action_schema
    calls = []

    def corrupt_only_reforward(db):
        state = original(db)
        calls.append(True)
        if len(calls) == 2:
            db.execute("update email_accounts set display_name='REFORWARD CORRUPTION' "
                       "where account_id='copy-only-account'")
        return state

    monkeypatch.setattr(proof, "migrate_email_action_schema", corrupt_only_reforward)
    with pytest.raises(AssertionError):
        _case(path, old, fresh=True, null_plans=False)
    assert len(calls) == 2


def test_unexplained_fixture_message_water_is_rejected(old_fresh, monkeypatch):
    old, path = old_fresh
    original = proof._old_process

    def changed_water(root, database, mode, *args):
        result = original(root, database, mode, *args)
        if mode == "seed":
            with closing(sqlite3.connect(database)) as db:
                db.execute("update sqlite_sequence set seq=seq+999 "
                           "where name='email_messages'")
                db.commit()
        return result

    monkeypatch.setattr(proof, "_old_process", changed_water)
    with pytest.raises(AssertionError):
        _case(path, old, fresh=True, null_plans=False)


def test_original_email_row_change_is_not_a_new_oracle():
    original = {name: ((), ()) for name in proof.SOURCE_TABLES}
    current = dict(original)
    original["email_actions"] = (((7, "2026-10-14T00:00:00+00:00"),),
                                 (("integer", "text"),))
    current["email_actions"] = (((7, "changed"),), (("integer", "text"),))
    with pytest.raises(AssertionError):
        proof._original_source_rows_preserved(original, current)


def test_old_public_write_cannot_change_original_email_row(old_fresh, monkeypatch):
    old, path = old_fresh
    proof._old_process_genuine(old, path, "seed", "baseline-proof-account")
    original = proof._old_process

    def corrupt_after_public_write(root, database, mode, *args):
        result = original(root, database, mode, *args)
        if mode == "write":
            with closing(sqlite3.connect(database)) as db:
                db.execute("update email_actions set updated_at='2001-01-01T00:00:00+00:00' "
                           "where rowid=(select min(rowid) from email_actions "
                           "where account_id='baseline-proof-account')")
                db.commit()
        return result

    monkeypatch.setattr(proof, "_old_process", corrupt_after_public_write)
    with pytest.raises(AssertionError):
        _case(path, old, fresh=True, null_plans=False)


def test_old_public_write_cannot_raise_attempt_water_unexplained(old_fresh, monkeypatch):
    old, path = old_fresh
    original = proof._old_process

    def corrupt_after_public_write(root, database, mode, *args):
        result = original(root, database, mode, *args)
        if mode == "write":
            with closing(sqlite3.connect(database)) as db:
                db.execute("update sqlite_sequence set seq=seq+999 "
                           "where name='email_action_attempts'")
                db.commit()
        return result

    monkeypatch.setattr(proof, "_old_process", corrupt_after_public_write)
    with pytest.raises(AssertionError):
        _case(path, old, fresh=True, null_plans=False)


def test_public_seed_clock_normalization_is_bounded(old_fresh, monkeypatch):
    old, path = old_fresh
    original = proof._old_process

    def stale_seed_clock(root, database, mode, *args):
        result = original(root, database, mode, *args)
        if mode == "seed":
            with closing(sqlite3.connect(database)) as db:
                db.execute("update email_classifications "
                           "set updated_at='2001-01-01T00:00:00+00:00' "
                           "where account_id='copy-only-account'")
                db.commit()
        return result

    monkeypatch.setattr(proof, "_old_process", stale_seed_clock)
    with pytest.raises(AssertionError):
        _case(path, old, fresh=True, null_plans=False)


def test_email_auto_allocator_exhaustion_at_signed_max(old_fresh):
    _old, path = old_fresh
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        proof.migrate_email_action_schema(db)
        proof._set_sequence(db, "actions:email_action_attempts", 2**63 - 1)
        db.commit()
        with pytest.raises(sqlite3.OperationalError, match="database or disk is full"):
            proof.action_storage._email_auto_next(db, "email_action_attempts")


@pytest.fixture
def latest_fresh(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    old = tmp_path / "latest-old"
    old.mkdir()
    archive = subprocess.check_output([
        "git", "-C", str(repo), "archive", LATEST_OLD_REF, "app",
        "docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json",
    ])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(old, filter="data")
    path = tmp_path / "latest-fresh.sqlite3"
    subprocess.run([
        sys.executable, "-c",
        "from pathlib import Path; import sys; from app.store import AutoReplyStore; "
        "from app.email_store import EmailStore; p=Path(sys.argv[1]); AutoReplyStore(p); EmailStore(p)",
        str(path),
    ], cwd=old, env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True, text=True, check=True, timeout=300)
    return old, path, repo


def test_latest_paired_publication_current_public_inverse_and_reforward(latest_fresh):
    old, path, repo = latest_fresh
    result = proof._paired_case(path, old, repo)
    assert result["paired_default_constructors"]
    assert result["target_public_writes"]
    assert result["old_public_cold_writes"]
    assert result["current_fact_inverse"]
    assert result["reforward_current_facts"]
    assert result["target_tables"] == 46
    assert all(result["provider_current_public_writes"].values())
    assert all(result["provider_old_public_writes"].values())
    assert result["provider_final_summary"]["counts"]["email_provider_observations"] == (
        result["provider_source_summary"]["counts"]["email_provider_observations"] + 2
    )
    assert set(result["business_current_public_ids"]) == {
        "task", "anchor", "project", "project_context_revision",
        "service_bugfix_candidate", "work_cluster", "todo_evidence_candidate",
        "work_project", "work_todo",
    }
    assert result["business_old_public_ids"]["email_cold_read"]
    assert {
        "task_id", "anchor_id", "project_id", "context_id",
        "service_bugfix_candidate_id", "work_cluster_id",
        "todo_evidence_candidate_id", "work_project_id", "work_todo_id",
    } <= set(result["business_old_public_ids"])


def test_latest_paired_copy_requires_real_publication_before_default_constructors(
    latest_fresh, monkeypatch
):
    from app.action_email_schema_release import ActionEmailSchemaRelease

    old, path, repo = latest_fresh
    monkeypatch.setattr(ActionEmailSchemaRelease, "publish", lambda self: None)
    with pytest.raises(
        sqlite3.IntegrityError,
        match="Store19/Email51 requires the paired schema release before target constructors",
    ):
        proof._paired_case(path, old, repo)


def test_latest_paired_copy_cannot_substitute_donor_for_target_public_writes(
    latest_fresh, monkeypatch
):
    old, path, repo = latest_fresh
    # Even a success-shaped report cannot replace the missing durable facts.
    monkeypatch.setattr(
        proof, "_current_public_writes",
        lambda *args, **kwargs: dict.fromkeys(
            ("receipt_enriched", "direct_completed", "failed_visible", "attempt_water_advanced",
             "unsubscribe_terminal", "unsubscribe_step", "explicit_retry", "history_source_id"),
            True,
        ),
    )
    with pytest.raises(AssertionError):
        proof._paired_case(path, old, repo)


def test_latest_paired_copy_rejects_rollback_fact_loss(latest_fresh, monkeypatch):
    from app.action_email_schema_release import ActionEmailSchemaRelease

    old, path, repo = latest_fresh
    rollback = ActionEmailSchemaRelease.rollback

    def corrupt_after_rollback(release):
        rollback(release)
        with closing(sqlite3.connect(release.database_path)) as db:
            db.execute(
                "update email_actions set updated_at='2001-01-01T00:00:00+00:00' "
                "where account_id='copy-only-account' and action_type='mark_read'"
            )
            db.commit()

    monkeypatch.setattr(ActionEmailSchemaRelease, "rollback", corrupt_after_rollback)
    with pytest.raises(AssertionError):
        proof._paired_case(path, old, repo)


def test_latest_paired_copy_rejects_original_provider_fact_change(latest_fresh, monkeypatch):
    old, path, repo = latest_fresh
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "insert into email_provider_observations "
            "(account_id,stable_message_identity,state,provider_folder_id,"
            "provider_folder_name,category_key,important,observed_at,important_signals_json) "
            "values('baseline-provider','original-message','available','INBOX',"
            "'Inbox',NULL,0,'2026-10-14T00:00:00+00:00','[]')"
        )
        db.commit()
    original = proof._current_provider_public_writes

    def corrupt_original(candidate, seeded):
        result = original(candidate, seeded)
        with closing(sqlite3.connect(candidate)) as db:
            db.execute(
                "update provider_state set observed_at='changed' "
                "where record_type='observation' and account_id='baseline-provider'"
            )
            db.commit()
        return result

    monkeypatch.setattr(proof, "_current_provider_public_writes", corrupt_original)
    with pytest.raises(AssertionError):
        proof._paired_case(path, old, repo)


def test_attention_counter_includes_unrelated_console_category():
    fixture_only = [{"category": "Email action", "status": "failed"}]
    unrelated = fixture_only + [{"category": "Work item", "status": "failed"}]
    assert proof._attention_category_status_counts(fixture_only) == [
        ["Email action", "failed", 1]
    ]
    assert proof._attention_category_status_counts(unrelated) == [
        ["Email action", "failed", 1], ["Work item", "failed", 1]
    ]


def test_current_attention_counter_reads_actual_console_surface(tmp_path, monkeypatch):
    import app.audit_web as audit_web
    from app.email_store import EmailStore
    from app.store import AutoReplyStore

    path = tmp_path / "current-attention.sqlite3"
    AutoReplyStore(path)
    EmailStore(path)
    rows = [{"category": "Work item", "status": "failed"}]
    monkeypatch.setattr(audit_web, "_queue_attention_rows", lambda store: rows)
    assert proof._current_counters(path)["attention_counts"] == [["Work item", "failed", 1]]


def test_old_attention_counter_includes_another_accounts_failed_action(latest_fresh):
    old, path, _repo = latest_fresh
    seeded = proof._old_process(old, path, "seed", "baseline-proof-account")
    assert proof._old_process(old, path, "fail_other_account", seeded["failed_action_id"]) == {
        "failed": True
    }
    assert proof._old_process(old, path, "counters")["attention_counts"] == [
        ["Email action", "failed", 1]
    ]


@pytest.mark.parametrize(
    "entry",
    (
        ["email-action:999", 999, "failed"],
        ["email-action:42", 42, "done"],
        ["email-action:999", 42, "failed"],
    ),
)
def test_old_history_guard_rejects_wrong_locator_status_or_logical_id(entry):
    with pytest.raises(AssertionError):
        proof._assert_old_failed_history({"native_rowid": 42, "entries": [entry]})


@pytest.fixture
def bounded_run(tmp_path, monkeypatch):
    """Exercise the real report coordinator without production copies or old CLI calls."""
    repo = Path(__file__).resolve().parents[1]
    source = tmp_path / "source.sqlite3"
    sqlite3.connect(source).close()
    source_ref = proof.PUBLISHED_SCHEMA_REF
    heads = [source_ref, "published-next"]
    binding = {"production_checkout_head_before": proof.PUBLISHED_SCHEMA_REF,
               "production_checkout_head_after": proof.PUBLISHED_SCHEMA_REF,
               "store_marker": {"value": 19, "updated_at": "now"},
               "email_migration_ledger": [], "foreign_key_findings": [],
               "actions": {"variant": "old", "declaration_fingerprint_sha256": "fingerprint"},
               "email_declaration_fingerprint_sha256": "fingerprint", "schema_user_table_count": 0,
               "business": {"variant": "actual",
                            "parent_declaration_fingerprint_sha256": "fingerprint",
                            "child_declaration_fingerprint_sha256": "fingerprint"}}
    binding_path = tmp_path / "binding.json"
    binding_path.write_text(json.dumps(binding))
    monkeypatch.setattr(proof, "BINDING", binding_path)
    monkeypatch.setattr(proof, "_marker", lambda db: (19, "now"))
    monkeypatch.setattr(proof, "_email_ledger", lambda db: ())
    monkeypatch.setattr(proof, "capture_email_action_source", lambda db: {
        "action_variant": "old", "source_actions": {}, "source_tables": {}})
    monkeypatch.setattr(proof, "capture_business_source", lambda db: {
        "variant": "actual", "parents": {}, "children": {}, "fk_findings": (),
    })
    monkeypatch.setattr(proof, "capture_provider_state_source", lambda db: {
        "variant": "actual", "tables": {},
    })
    monkeypatch.setattr(proof, "_fingerprint", lambda value: "fingerprint")
    monkeypatch.setattr(proof, "_consistency", lambda db: ())
    monkeypatch.setattr(proof, "_copy", lambda origin, target: shutil.copyfile(origin, target))
    monkeypatch.setattr(proof, "_paired_case", lambda *args: {"paired": True})
    case_results = iter(({"case": index} for index in range(3)))
    monkeypatch.setattr(proof, "_case", lambda *args, **kwargs: next(case_results))
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        for name in (*("app/" + p for p in ("store.py", "email_store.py",
                     "action_storage.py", "action_email_storage.py",
                     "action_email_definitions.py", "project_context_service.py",
                     "task_semantic_models.py", "email_classifier_contracts.py",
                     "email_unsubscribe.py", "audit_web.py")), proof.SOURCE_ARCHIVE_PATHS[1]):
            payload = b"published source"
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            archive.addfile(entry, io.BytesIO(payload))

    def check_output(command, **kwargs):
        if "archive" in command:
            return archive_bytes.getvalue()
        if "status" in command:
            return ""
        if "rev-parse" in command and str(proof.PRODUCTION_ROOT) in command:
            return heads.pop(0)
        if "rev-parse" in command:
            return "candidate-commit"
        raise AssertionError(command)

    def initialize(command, **kwargs):
        sqlite3.connect(command[-1]).close()

    monkeypatch.setattr(proof.subprocess, "check_output", check_output)
    monkeypatch.setattr(proof.subprocess, "run", initialize)
    return source, source_ref, repo, heads


def test_final_production_head_change_keeps_all_completed_case_evidence(bounded_run):
    source, source_ref, repo, heads = bounded_run
    report = run(source, source_ref, repo)
    assert report["status"] == "production_changed"
    assert report["production_head_before"] == source_ref
    assert report["production_head_after"] == "published-next"
    assert report["published_source_ref"] == source_ref
    assert report["executed_candidate_commit"] == "candidate-commit"
    assert len(report["candidate_module_sha256"]) == 18
    assert {"app/email_classifier_contracts.py", "app/email_unsubscribe.py",
            "app/audit_web.py"}.issubset(report["candidate_module_sha256"])
    assert "app/process_runner.py" not in report["candidate_module_sha256"]
    assert "finite" in report["candidate_module_hash_scope"].lower()
    assert "app/" in report["candidate_module_hash_scope"]
    assert len(report["candidate_proof_sha256"]) == 21
    assert report["candidate_proof_sha256"]["tests/test_provider_state_source.py"] == hashlib.sha256(
        (repo / "tests/test_provider_state_source.py").read_bytes()
    ).hexdigest()
    assert proof.BINDING_RELATIVE in report["candidate_proof_sha256"]
    assert report["scope"] == "combined Provider2/Business9/Email13 paired copy and three Email-only SQL-boundary cases"
    assert len(report["published_old_module_sha256"]) == 10
    assert report["cases"] == {
        "paired_actual_online_backup": {"paired": True},
        "actual_online_backup_sql_boundary": {"case": 0},
        "published_old_fresh_sql_legal": {"case": 1},
        "published_old_fresh_application": {"case": 2},
    }
    assert heads == []


def test_unchanged_production_head_still_passes(bounded_run):
    source, source_ref, repo, heads = bounded_run
    heads[-1] = source_ref
    report = run(source, source_ref, repo)
    assert report["status"] == "passed"
    assert report["production_head_before"] == report["production_head_after"] == source_ref


def test_latest_copy_requires_actual_published_business_source(bounded_run, monkeypatch):
    source, source_ref, repo, heads = bounded_run
    monkeypatch.setattr(proof, "capture_business_source", lambda db: {
        "variant": "fresh", "parents": {}, "children": {}, "fk_findings": (),
    })
    with pytest.raises(AssertionError, match="published Business source variant"):
        run(source, source_ref, repo)
    assert heads == ["published-next"]


@pytest.mark.parametrize("field", (
    "parent_declaration_fingerprint_sha256", "child_declaration_fingerprint_sha256",
))
def test_latest_copy_rejects_business_source_declaration_drift(bounded_run, field):
    source, source_ref, repo, heads = bounded_run
    binding = json.loads(proof.BINDING.read_text())
    binding["business"][field] = "different"
    proof.BINDING.write_text(json.dumps(binding))
    with pytest.raises(AssertionError, match="published Business source declaration"):
        run(source, source_ref, repo)
    assert heads == ["published-next"]


@pytest.mark.parametrize("field", ("store_marker", "foreign_key_findings"))
def test_latest_copy_rejects_published_marker_or_fk_drift(bounded_run, field):
    source, source_ref, repo, heads = bounded_run
    binding = json.loads(proof.BINDING.read_text())
    if field == "store_marker":
        binding[field]["updated_at"] = "different"
    else:
        binding[field] = [["runs", 1, "tasks", 1]]
    proof.BINDING.write_text(json.dumps(binding))
    with pytest.raises(AssertionError):
        run(source, source_ref, repo)
    assert heads == ["published-next"]


@pytest.mark.parametrize("path", (
    "app/store.py", "app/email_store.py", "app/project_context_service.py",
    "app/task_semantic_models.py", "app/provider_state_schema_release.py",
    "tests/test_provider_state_copy_proof.py", "tests/test_provider_state_source.py",
    "docs/superpowers/specs/2026-10-15-database-redesign-business-email-published-source.json",
))
def test_latest_copy_rejects_dirty_executed_module_or_binding(bounded_run, monkeypatch, path):
    source, source_ref, repo, heads = bounded_run
    original = proof.subprocess.check_output

    def dirty_if_checked(command, **kwargs):
        if "status" in command:
            pathspecs = command[command.index("--") + 1:]
            if any(path == spec or path.startswith(f"{spec.rstrip('/')}/")
                   for spec in pathspecs):
                return f" M {path}\n"
        return original(command, **kwargs)

    monkeypatch.setattr(proof.subprocess, "check_output", dirty_if_checked)
    with pytest.raises(AssertionError, match="proof code is uncommitted"):
        run(source, source_ref, repo)
    assert heads == ["published-next"]


def test_latest_copy_guards_app_module_outside_finite_hash_list(bounded_run, monkeypatch):
    source, source_ref, repo, heads = bounded_run
    path = "app/process_runner.py"
    original = proof.subprocess.check_output

    def dirty_if_app_tree_checked(command, **kwargs):
        if "status" in command:
            pathspecs = command[command.index("--") + 1:]
            assert path not in pathspecs
            if "app" in pathspecs:
                return f" M {path}\n"
        return original(command, **kwargs)

    monkeypatch.setattr(proof.subprocess, "check_output", dirty_if_app_tree_checked)
    with pytest.raises(AssertionError, match="proof code is uncommitted"):
        run(source, source_ref, repo)
    assert heads == ["published-next"]


def test_latest_copy_rejects_descendant_source_ref_even_with_same_old_schema(bounded_run):
    source, _source_ref, repo, heads = bounded_run
    heads[0] = "published-descendant"
    with pytest.raises(AssertionError, match="published source ref mismatch"):
        run(source, "published-descendant", repo)
    assert heads == ["published-descendant", "published-next"]


def test_case_failure_still_propagates_without_success_report(bounded_run, monkeypatch):
    source, source_ref, repo, heads = bounded_run

    def fail_case(*args):
        raise AssertionError("case failed")

    monkeypatch.setattr(proof, "_paired_case", fail_case)
    with pytest.raises(AssertionError, match="case failed"):
        run(source, source_ref, repo)
    assert heads == ["published-next"]


@pytest.mark.parametrize("status,exit_code", (("production_changed", 1), ("passed", 0)))
def test_cli_writes_completed_report_before_exit(tmp_path, monkeypatch, capsys, status, exit_code):
    source = tmp_path / "source.sqlite3"
    source.touch()
    evidence = tmp_path / "evidence.json"
    report = {"status": status, "cases": {"paired_actual_online_backup": {"paired": True}},
              "production_head_before": "published-current",
              "production_head_after": "published-next" if status == "production_changed"
              else "published-current"}
    monkeypatch.setattr(proof, "run", lambda *args: report)
    assert proof.main(["--source", str(source), "--source-ref", "published-current",
                       "--evidence", str(evidence)]) == exit_code
    assert json.loads(evidence.read_text()) == report
    assert json.loads(capsys.readouterr().out)["status"] == status
