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


def test_source_ref_must_be_current_published_checkout(tmp_path):
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
                       "where record_type='email_action_plans'")
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
