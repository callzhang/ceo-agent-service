"""Bounded offline Email13 proof regressions against genuine published old code."""

from contextlib import closing
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from scripts import validate_action_email_copy as proof
from scripts.validate_action_email_copy import _case, _old_process, run

PUBLISHED_OLD_REF = "9cd41bcf5ea43a275eeb1376817d37f7cef6bd21"
LATEST_OLD_REF = "d06c148b90aaeee74c14bbabf0183ffd65f34048"


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
