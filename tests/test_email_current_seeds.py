"""Unselected E1 seeds on the retained factory; no constructor activation proof."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID

import pytest

from app.email_current_schema import create_current_email_non_action_schema
from app.email_store import EmailStore
from app.configs_definitions import config_projection
from app.configs_storage import create_configs_schema, insert_config, update_config
from app.action_storage import create_action_schema


_FIXED = datetime(2026, 10, 14, 10, tzinfo=timezone.utc)
_TABLES = (
    "email_category_configs",
    "email_model_promotion_configs",
    "email_category_description_revisions",
)


def _facts(db):
    return {
        table: [
            dict(row)
            for row in db.execute(
                f"select * from ({config_projection(table)}) order by "
                + (
                    "category_key"
                    if table == "email_category_configs"
                    else "config_version"
                    if table == "email_model_promotion_configs"
                    else "created_at,revision_id"
                )
            )
        ]
        for table in _TABLES
    }


def _fresh(db):
    db.row_factory = sqlite3.Row
    create_current_email_non_action_schema(db)
    create_configs_schema(db)
    db.execute("begin")
    store = object.__new__(EmailStore)
    store._now = lambda: _FIXED.isoformat()
    store._account_now = lambda: _FIXED
    return store


def _old_fresh(tmp_path):
    import io
    import os
    import subprocess
    import sys
    import tarfile

    old = tmp_path / "published-old"
    old.mkdir()
    archive = subprocess.check_output(
        ["git", "archive", "2773d6f4", "app"], cwd=Path(__file__).resolve().parents[1]
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(old, filter="data")
    source = tmp_path / "source.sqlite3"
    code = """
import json,sys,sqlite3,itertools
from pathlib import Path
from datetime import datetime,timezone
from uuid import UUID
from app.store import AutoReplyStore
from app import email_store as m
p=Path(sys.argv[1]);AutoReplyStore(p)
ids=itertools.count(1);calls=[]
def new_id():
    value=UUID(int=next(ids),version=4);calls.append(str(value));return value
m.uuid4=new_id
fixed=datetime(2026,10,14,10,tzinfo=timezone.utc)
m.EmailStore._now=staticmethod(lambda:fixed.isoformat())
m.EmailStore._account_now=staticmethod(lambda:fixed)
m.EmailStore(p)
with sqlite3.connect(p) as db:
    db.row_factory=sqlite3.Row
    facts={table:[dict(row) for row in db.execute('select * from '+table+' order by '+order)] for table,order in (
        ('email_category_configs','category_key'),('email_model_promotion_configs','config_version'),
        ('email_category_description_revisions','created_at,revision_id'))}
    assert db.execute('select max(version) from email_schema_migrations').fetchone()[0]==51
    populated={row['name']:db.execute('select count(*) from '+row['name']).fetchone()[0]
        for row in db.execute("select name from sqlite_schema where type='table' and name like 'email_%' and name<>'email_schema_migrations'").fetchall()}
    populated={name:count for name,count in populated.items() if count}
print(json.dumps(dict(facts=facts,uuid_calls=calls,populated=populated)))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(source)],
        cwd=old,
        env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_unselected_current_seeds_equal_genuine_default_fresh_email51(
    tmp_path, monkeypatch
):
    import itertools
    import app.email_store as module

    expected = _old_fresh(tmp_path)
    ids = itertools.count(1)
    calls = []

    def new_id():
        value = UUID(int=next(ids), version=4)
        calls.append(str(value))
        return value

    monkeypatch.setattr(module, "uuid4", new_id)
    with closing(sqlite3.connect(":memory:")) as db:
        store = _fresh(db)
        store._seed_current_fresh_state(db)
        assert _facts(db) == expected["facts"]
        assert calls == expected["uuid_calls"]
        assert expected["populated"] == {
            table: len(rows) for table, rows in _facts(db).items()
        }
        assert (
            db.execute(
                "select 1 from sqlite_schema where name='email_schema_migrations'"
            ).fetchone()
            is None
        )
        assert (
            db.execute(
                "select 1 from sqlite_schema where name='email_action_plans'"
            ).fetchone()
            is None
        )
        assert (
            db.execute("select 1 from sqlite_schema where name='actions'").fetchone()
            is None
        )


def test_repeated_fresh_helper_preserves_seeded_facts_without_new_ids_or_clocks(
    monkeypatch,
):
    import app.email_store as module

    with closing(sqlite3.connect(":memory:")) as db:
        store = _fresh(db)
        store._seed_current_fresh_state(db)
        db.commit()
        before = _facts(db)
        changes = db.total_changes

        def unexpected():
            raise AssertionError("already seeded bootstrap allocated a new ID/clock")

        monkeypatch.setattr(module, "uuid4", unexpected)
        store._now = unexpected
        store._account_now = unexpected
        store._seed_current_fresh_state(db)
        assert _facts(db) == before and db.total_changes == changes
        assert not db.in_transaction


@pytest.mark.parametrize("has_revision", [False, True])
def test_fresh_helper_boundary_preserves_existing_config_set_and_promotion(
    has_revision,
):
    with closing(sqlite3.connect(":memory:")) as template:
        template_store = _fresh(template)
        template_store._seed_current_fresh_state(template)
        config = next(
            row
            for row in _facts(template)["email_category_configs"]
            if row["category_key"] == "work"
        )
    config.update(threshold=0.73, config_version="existing-config")
    with closing(sqlite3.connect(":memory:")) as db:
        store = _fresh(db)
        insert_config(db, "email_category_configs", config)
        insert_config(db, "email_model_promotion_configs", dict(zip(
            ("config_version", "micro_f1_min", "category_precision_min",
             "category_validation_samples_min", "p95_latency_max_ms", "created_at"),
            ("existing-promotion", 0.92, 0.91, 25, 250.0, _FIXED.isoformat()),
        )))
        if has_revision:
            create_action_schema(db, include_reply=True, reply_variant="fresh_memory_fk",
                                 include_email=True, include_skill_config_receipts=True)
            row = db.execute(config_projection("email_category_configs")).fetchone()
            store._append_category_description_revision(db, row, current=True)
            rowid = db.execute(
                f"select rowid from ({config_projection('email_category_configs', include_rowid=True)})"
            ).fetchone()[0]
            assert update_config(db, "email_category_configs", rowid, {
                "config_version": "new-current-config", "threshold": 0.74,
            }) == 1
        before = _facts(db)
        store._seed_current_fresh_state(db)
        after = _facts(db)
        assert after["email_category_configs"] == before["email_category_configs"]
        assert (
            after["email_model_promotion_configs"]
            == before["email_model_promotion_configs"]
        )
        assert (
            len(after["email_category_configs"])
            == len(after["email_category_description_revisions"])
            == 1
        )
        if has_revision:
            assert (
                after["email_category_description_revisions"]
                == before["email_category_description_revisions"]
            )
        else:
            revision = after["email_category_description_revisions"][0]
            assert revision["config_version"] == "existing-config"
            assert json.loads(revision["config_json"]) == store._category_config_row(
                db.execute(config_projection("email_category_configs")).fetchone()
            )
        store._seed_current_fresh_state(db)
        assert _facts(db) == after


def test_fresh_helper_keeps_native_uuid4_and_monotonic_revision_clock():
    from datetime import timedelta

    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        create_current_email_non_action_schema(db)
        create_configs_schema(db)
        db.execute("begin")
        store = object.__new__(EmailStore)
        before = datetime.now(timezone.utc)
        store._seed_current_fresh_state(db)
        after = datetime.now(timezone.utc)
        facts = _facts(db)
        promotion = facts["email_model_promotion_configs"][0]
        revisions = facts["email_category_description_revisions"]
        ids = [promotion["config_version"], *[row["revision_id"] for row in revisions]]
        assert len(set(ids)) == len(ids)
        assert all(UUID(value).version == 4 for value in ids)
        assert datetime.fromisoformat(promotion["created_at"]).microsecond == 0
        assert all(
            datetime.fromisoformat(row["updated_at"]).microsecond == 0
            for row in facts["email_category_configs"]
        )
        timestamps = [row["created_at"] for row in revisions]
        assert timestamps == sorted(set(timestamps))
        for timestamp in [
            promotion["created_at"],
            *timestamps,
            *[row["updated_at"] for row in facts["email_category_configs"]],
        ]:
            assert (
                before.replace(microsecond=0)
                <= datetime.fromisoformat(timestamp)
                <= after + timedelta(seconds=1)
            )


def test_fresh_seed_success_remains_inside_caller_savepoint():
    with closing(sqlite3.connect(":memory:")) as db:
        store = _fresh(db)
        db.commit()
        db.execute("create table caller_note(value)")
        db.execute("begin immediate")
        db.execute("insert into caller_note values('keep')")
        db.execute("savepoint bootstrap")
        store._seed_current_fresh_state(db)
        assert all(_facts(db).values()) and db.in_transaction
        db.execute("rollback to bootstrap")
        db.execute("release bootstrap")
        assert all(not rows for rows in _facts(db).values())
        assert db.execute("select value from caller_note").fetchone()[0] == "keep"
        db.commit()
        assert (
            db.execute(
                "select 1 from sqlite_schema where name='email_schema_migrations'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize("boundary", ["transaction", "savepoint"])
def test_seed_failure_leaves_rollback_control_with_original_caller(boundary):
    with closing(sqlite3.connect(":memory:")) as db:
        store = _fresh(db)
        db.execute("""create trigger seed_fault before insert on configs
            when new.record_type='email_category_description_revisions'
             and new.revision_category_key='work'
            begin select raise(abort,'seed_fault');end""")
        db.execute("create table caller_note(value)")
        db.commit()
        if boundary == "transaction":
            with pytest.raises(sqlite3.IntegrityError, match="seed_fault"):
                with db:
                    db.execute("begin immediate")
                    db.execute("insert into caller_note values('rolled-back')")
                    store._seed_current_fresh_state(db)
            assert db.execute("select count(*) from caller_note").fetchone()[0] == 0
        else:
            db.execute("begin immediate")
            db.execute("insert into caller_note values('keep')")
            db.execute("savepoint bootstrap")
            with pytest.raises(sqlite3.IntegrityError, match="seed_fault"):
                store._seed_current_fresh_state(db)
            assert db.in_transaction and all(_facts(db).values())
            db.execute("rollback to bootstrap")
            db.execute("release bootstrap")
            assert db.execute("select value from caller_note").fetchone()[0] == "keep"
            db.commit()
        assert all(not rows for rows in _facts(db).values())
