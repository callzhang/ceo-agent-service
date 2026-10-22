"""Raw migration copies retain source facts without a VM reset per row."""

import sqlite3

import pytest

from app.action_email_storage import _insert_rows


def test_raw_copy_batches_within_the_connection_variable_limit_and_keeps_values():
    rows = [(i, b"\x00\xff" if i % 2 else "原值", i + 0.5) for i in range(1, 13)]
    with sqlite3.connect(":memory:") as db:
        db.execute("create table target(id integer primary key,payload text,weight real)")
        db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 9)
        statements = []
        db.set_trace_callback(statements.append)
        _insert_rows(db, "target", ("id", "payload", "weight"), rows)
        inserts = [sql for sql in statements if sql.startswith('insert into main."target"')]
        assert len(inserts) < len(rows), "raw transfer still resets the SQLite VM for every row"
        assert db.in_transaction
        assert db.execute("select id,payload,weight from target order by id").fetchall() == rows
        assert db.execute("select distinct typeof(payload),typeof(weight) from target order by 1").fetchall() == [("blob", "real"), ("text", "real")]
        db.rollback()
        assert db.execute("select count(*) from target").fetchone()[0] == 0


def test_late_copy_failure_leaves_rollback_with_the_transaction_owner():
    with sqlite3.connect(":memory:") as db:
        db.execute("create table target(id integer primary key,payload text)")
        db.execute("create table witness(value text)")
        db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 4)
        db.execute("begin")
        db.execute("insert into witness values('earlier work')")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_rows(db, "target", ("id", "payload"), [(1, "a"), (2, "b"), (3, "c"), (1, "conflict")])
        assert db.in_transaction
        db.rollback()
        assert db.execute("select count(*) from target").fetchone()[0] == 0
        assert db.execute("select count(*) from witness").fetchone()[0] == 0


def test_empty_copy_keeps_native_preparation_and_implicit_transaction_semantics():
    with sqlite3.connect(":memory:") as db:
        db.execute("create table target(id integer)")
        assert not db.in_transaction
        _insert_rows(db, "target", ("id",), [])
        assert db.in_transaction
        db.rollback()
        with pytest.raises(sqlite3.OperationalError):
            _insert_rows(db, "missing", ("id",), [])


def test_malformed_snapshot_rows_cannot_be_flattened_across_row_boundaries():
    with sqlite3.connect(":memory:") as db:
        db.execute("create table target(id integer primary key,payload text)")
        with pytest.raises((ValueError, sqlite3.ProgrammingError)):
            _insert_rows(db, "target", ("id", "payload"), [(1,), (2, "b", "extra")])
        assert db.execute("select count(*) from target").fetchone()[0] == 0
