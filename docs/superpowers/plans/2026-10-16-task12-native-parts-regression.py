"""Real source implicit transactions, bulk RHS and historical native UPSERT."""

import sqlite3

import pytest

from app.task12_storage import (
    insert_task12,
    update_task12,
    delete_task12,
    task12_insert_parts,
    task12_set_parts,
)
from app.task20_definitions import task20_column
from app.task8_storage import register_task8_functions
from tests.test_task12_storage import target as target, supplied, snapshot
from tests.test_tasks12_native_source import NAMES, insert

pytest_plugins = ("tests.test_tasks12_native_source",)


@pytest.mark.parametrize("kind", NAMES)
def test_insert_opens_only_native_implicit_transaction(db, target, kind):
    data = supplied(kind)
    for connection in (db, target):
        connection.commit()
    native = insert(db, kind, data)
    actual = insert_task12(target, kind, data)
    assert native.rowcount == actual.rowcount == 1
    assert native.lastrowid == actual.lastrowid
    assert db.in_transaction == target.in_transaction is True
    snapshot(db, target, kind)
    for connection in (db, target):
        connection.rollback()
    assert not db.in_transaction and not target.in_transaction
    snapshot(db, target, kind)


@pytest.mark.parametrize("operation", ("update", "delete"))
def test_point_update_delete_native_implicit_transaction(db, target, operation):
    kind = "wechat_memory_import_jobs"
    data = supplied(kind)
    ident = insert(db, kind, data).lastrowid
    insert_task12(target, kind, data)
    for connection in (db, target):
        connection.commit()
    if operation == "update":
        native = db.execute(
            "update wechat_memory_import_jobs set error=? where id=?",
            ("new error", ident),
        ).rowcount
        actual = update_task12(target, kind, ident, {"error": "new error"})
    else:
        native = db.execute(
            "delete from wechat_memory_import_jobs where id=?", (ident,)
        ).rowcount
        actual = delete_task12(target, kind, ident)
    assert native == actual == 1
    assert db.in_transaction == target.in_transaction is True
    snapshot(db, target, kind)
    for connection in (db, target):
        connection.rollback()
    snapshot(db, target, kind)


def seed_classification(db, target):
    kind = "email_agent_classification_tasks"
    for suffix in ("a", "b", "c"):
        values = supplied(kind, suffix) | {
            "generation": 1,
            "attempt_count": 2,
            "owner": "owner" if suffix != "c" else "other",
        }
        insert(db, kind, values)
        insert_task12(target, kind, values)
    for connection in (db, target):
        connection.commit()
    register_task8_functions(target)
    return kind


@pytest.mark.parametrize("explicit", (False, True))
def test_bulk_set_keeps_native_counter_rhs_cas_and_rowcount(db, target, explicit):
    kind = seed_classification(db, target)
    if explicit:
        for connection in (db, target):
            connection.execute("begin immediate")
    native = db.execute(
        "update email_agent_classification_tasks set status=?,generation=generation+1,attempt_count=attempt_count+1 where owner=? and generation=?",
        ("running", "owner", 1),
    )
    parts = task12_set_parts(
        kind,
        {"status": "running"},
        update_sql={
            "generation": task20_column(kind, "generation") + "+1",
            "attempt_count": task20_column(kind, "attempt_count") + "+1",
        },
    )
    cursor = target.execute(
        "update tasks set "
        + parts.sql
        + " where record_type=? and "
        + task20_column(kind, "owner")
        + "=? and "
        + task20_column(kind, "generation")
        + "=?",
        (*parts.parameters, kind, "owner", 1),
    )
    assert cursor.rowcount == native.rowcount == 2
    assert db.in_transaction == target.in_transaction is True
    rows = snapshot(db, target, kind)
    assert [(r["generation"], r["attempt_count"]) for r in rows] == [
        (2, 3),
        (2, 3),
        (1, 2),
    ]
    for connection in (db, target):
        connection.rollback()
    snapshot(db, target, kind)


def test_bulk_constraint_abort_keeps_original_rows_and_earlier_work(db, target):
    kind = seed_classification(db, target)
    for connection in (db, target):
        connection.execute("create table caller_sentinel(value)")
        connection.execute("insert into caller_sentinel values(7)")
    expression = "case when task_id='task-a' then '{\"changed\":1}' else 'bad-json' end"
    with pytest.raises(sqlite3.IntegrityError) as left:
        db.execute(
            "update email_agent_classification_tasks set input_json=" + expression
        )
    parts = task12_set_parts(
        kind,
        {},
        update_sql={
            "input_json": "case when "
            + task20_column(kind, "task_id")
            + "='task-a' then '{\"changed\":1}' else 'bad-json' end"
        },
    )
    with pytest.raises(sqlite3.IntegrityError) as right:
        target.execute(
            "update tasks set " + parts.sql + " where record_type=?",
            (*parts.parameters, kind),
        )
    assert (
        left.value.sqlite_errorcode
        == right.value.sqlite_errorcode
        == sqlite3.SQLITE_CONSTRAINT_CHECK
    )
    assert db.in_transaction and target.in_transaction
    assert (
        db.execute("select value from caller_sentinel").fetchone()[0]
        == target.execute("select value from caller_sentinel").fetchone()[0]
        == 7
    )
    snapshot(db, target, kind)


@pytest.mark.parametrize("value", (1.2345678901234567, 2**53 + 1, 0.0))
def test_native_computed_set_keeps_exact_rhs_and_one_evaluation(db, target, value):
    kind = seed_classification(db, target)
    counters = [0, 0]
    for n, connection in enumerate((db, target)):

        def once(n=n):
            counters[n] += 1
            return value

        connection.create_function("rhs", 0, once)
    native = db.execute(
        "update email_agent_classification_tasks set generation=rhs()+1 where task_id='task-a'"
    )
    parts = task12_set_parts(kind, {}, update_sql={"generation": "rhs()+1"})
    actual = target.execute(
        "update tasks set "
        + parts.sql
        + " where record_type=? and "
        + task20_column(kind, "task_id")
        + "='task-a'",
        (*parts.parameters, kind),
    )
    assert native.rowcount == actual.rowcount == 1
    assert counters == [1, 1]
    snapshot(db, target, kind)


def test_historical_single_native_upsert_preserves_source_retry_state_and_identity(
    db, target
):
    kind = "email_historical_candidates"
    values = supplied(kind) | {
        "state": "deferred",
        "reason": "keep reason",
        "attempted_at": "attempted",
        "next_retry_at": "retry",
    }
    ident = insert(db, kind, values).lastrowid
    insert_task12(target, kind, values)
    for connection in (db, target):
        connection.commit()
        connection.execute("begin immediate")
    register_task8_functions(target)
    incoming = values | {
        "state": "pending",
        "reason": "",
        "attempted_at": "",
        "next_retry_at": "",
        "normalized_text": "new digest",
        "provider_message_json": '{"uid":9}',
        "uidvalidity": 9,
        "uid": 9,
        "updated_at": "new-clock",
    }
    keys = ("account_id", "folder", "model_id", "stable_message_identity")
    changed = (
        "normalized_text",
        "provider_message_json",
        "uidvalidity",
        "uid",
        "updated_at",
    )
    fields = tuple(incoming)
    sql = (
        "insert into "
        + kind
        + "("
        + ",".join(fields)
        + ") values("
        + ",".join("?" for _ in fields)
        + ") on conflict("
        + ",".join(keys)
        + ") do update set "
        + ",".join(f + "=excluded." + f for f in changed)
    )
    left = db.execute(sql, tuple(incoming.values()))
    parts = task12_insert_parts(target, kind, incoming)
    set_parts = task12_set_parts(
        kind, {}, update_sql={f: "excluded." + task20_column(kind, f) for f in changed}
    )
    tail = (
        " on conflict("
        + ",".join(task20_column(kind, f) for f in keys)
        + ") where record_type='"
        + kind
        + "' do update set "
        + set_parts.sql
    )
    right = target.execute(parts.sql + tail, (*parts.parameters, *set_parts.parameters))
    assert left.rowcount == right.rowcount == 1
    rows = snapshot(db, target, kind)
    assert len(rows) == 1 and rows[0]["rowid"] == ident
    assert tuple(
        rows[0][f] for f in ("state", "reason", "attempted_at", "next_retry_at")
    ) == ("deferred", "keep reason", "attempted", "retry")
    assert tuple(rows[0][f] for f in changed) == tuple(incoming[f] for f in changed)
    for connection in (db, target):
        connection.rollback()
    snapshot(db, target, kind)


def test_set_builder_does_not_touch_connection_or_begin_transaction(target):
    target.commit()
    kind = "email_agent_classification_tasks"
    parts = task12_set_parts(
        kind, {"status": "failed"}, update_sql={"updated_at": "current_timestamp"}
    )
    assert parts.parameters and not target.in_transaction
    with pytest.raises(ValueError):
        task12_set_parts("feedback_processing_rounds", {"id": 4})
    with pytest.raises(ValueError):
        task12_set_parts(kind, {}, update_sql={"invented_field": "1"})
    with pytest.raises(ValueError):
        task12_set_parts(kind, {"status": "done"}, update_sql={"status": "'failed'"})


def test_interdependent_old_rhs_storage_diagnostic(db, target):
    """Storage diagnostic; real classifier claim uses independent counters."""
    kind = seed_classification(db, target)
    db.execute(
        "update email_agent_classification_tasks set generation=generation+1,attempt_count=generation+attempt_count where task_id='task-a'"
    )
    parts = task12_set_parts(
        kind,
        {},
        update_sql={
            "generation": task20_column(kind, "generation") + "+1",
            "attempt_count": task20_column(kind, "generation")
            + "+"
            + task20_column(kind, "attempt_count"),
        },
    )
    target.execute(
        "update tasks set "
        + parts.sql
        + " where record_type=? and "
        + task20_column(kind, "task_id")
        + "='task-a'",
        (*parts.parameters, kind),
    )
    rows = snapshot(db, target, kind)
    assert (rows[0]["generation"], rows[0]["attempt_count"]) == (2, 3)
