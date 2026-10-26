"""Email schema-shape validation on the current database."""

from contextlib import closing
import sqlite3

import pytest

from app.email_store import EmailStore, EmailPersistenceCorruption


def test_current_schema_shape_refuses_a_missing_or_altered_canonical_trigger():
    from app import current_database_schema as current
    from app.email_current_schema import create_current_email_migration_ledger

    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        current.create_current_database_schema(db)
        create_current_email_migration_ledger(db)
        db.commit()
        EmailStore._validate_schema_shape(db)
        name = "trg_emails_direct_action_blocks_plan_switch"
        sql = db.execute("select sql from sqlite_schema where type='trigger' and name=?", (name,)).fetchone()[0]
        db.execute("savepoint corrupt_shape")
        db.execute(f"drop trigger {name}")
        with pytest.raises(EmailPersistenceCorruption, match="required current Store/Email declaration is missing or malformed"):
            EmailStore._validate_schema_shape(db)
        db.execute("rollback to corrupt_shape")
        db.execute("release corrupt_shape")
        EmailStore._validate_schema_shape(db)
        db.execute("savepoint corrupt_shape")
        db.execute(f"drop trigger {name}")
        corrupt = sql.replace("action_status='processing'", "action_status='pending'")
        assert corrupt != sql
        db.execute(corrupt)
        with pytest.raises(EmailPersistenceCorruption, match="required current Store/Email declaration is missing or malformed"):
            EmailStore._validate_schema_shape(db)
        db.execute("rollback to corrupt_shape")
        db.execute("release corrupt_shape")
        EmailStore._validate_schema_shape(db)


@pytest.mark.parametrize("variant", ["actual_no_memory_fk", "fresh_memory_fk"])
def test_source_phase_external_installer_is_transactional_idempotent_and_nonrepairing(variant):
    from app.action_storage import create_action_schema
    from app.action_email_storage import (
        create_email_action_external_triggers,
        finite_target_external_sql,
    )
    from app.email_current_schema import create_current_email_non_action_schema

    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text, legacy_key text)")
        create_current_email_non_action_schema(db)
        create_action_schema(db, include_reply=True, reply_variant=variant, include_email=True)
        db.commit()
        external = finite_target_external_sql()
        assert all(db.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone() is None
                   for name in external)
        db.execute("begin")
        db.execute("savepoint install")
        create_email_action_external_triggers(db)
        assert {name: db.execute("select sql from sqlite_schema where name=?", (name,)).fetchone()[0]
                for name in external} == external
        db.execute("rollback to install")
        db.execute("release install")
        assert all(db.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone() is None
                   for name in external)
        create_email_action_external_triggers(db)
        before = tuple(db.execute("select name,sql from sqlite_schema where type='trigger'"))
        create_email_action_external_triggers(db)
        assert tuple(db.execute("select name,sql from sqlite_schema where type='trigger'")) == before
        name = "trg_email_direct_action_blocks_account_delete"
        db.execute(f"drop trigger {name}")
        corrupt = external[name].replace("email_status='processing'", "email_status='pending'")
        assert corrupt != external[name]
        db.execute(corrupt)
        create_email_action_external_triggers(db)
        assert db.execute("select sql from sqlite_schema where name=?", (name,)).fetchone()[0] == corrupt
        assert corrupt != external[name]
        db.rollback()

