"""Published Tasks allocator provenance using frozen native actual/fresh DDL."""
import sqlite3

import pytest

from app.task20_definitions import initialize_task8_sequence
from app.task12_storage import insert_task12
from app.task8_storage import TASK8_KINDS
from tests.test_task20_definitions import payload
from tests.task_lifecycle_test_support import create_task20_schema, insert as explicit_insert
from tests.test_task8_storage import (
    pair as pair,
    base, native_insert, target_insert, compare,
)


def water(db, name="tasks"):
    row = db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone()
    return None if row is None else row[0]


@pytest.fixture
def allocated(pair):
    native, target = pair
    native.execute("begin")
    target.execute("begin")
    initialize_task8_sequence(target, water(native) or 0)
    return native, target


@pytest.mark.parametrize("kind", TASK8_KINDS)
@pytest.mark.parametrize("mode", ("success", "ignore", "abort", "check_ignore", "check_abort"))
def test_native_allocation_and_statement_outcome(allocated, kind, mode):
    native, target = allocated
    values = base(native, kind)
    native_insert(native, kind, values)
    target_insert(target, kind, values)
    assert water(native) == water(target) == water(target, "tasks:published")
    if mode == "success":
        compare(native, target, kind)
        return
    if mode.startswith("check"):
        # This field is NOT NULL for every finite published kind.
        field = {
            "reply_tasks": "conversation_title", "reply_task_inputs": "trigger_text",
            "work_summary_inputs": "source_type", "meeting_alignment_jobs": "title",
            "okr_review_requests": "trigger_text", "weekly_okr_analysis_jobs": "manager_user_id",
            "workbench_tasks": "title", "workbench_turns": "user_text",
        }[kind]
        values = values | {field: None}
    observations = []
    for db, writer in ((native, native_insert), (target, target_insert)):
        try:
            cursor = writer(db, kind, values, ignore=mode in ("ignore", "check_ignore"))
            observation = (cursor.rowcount, cursor.lastrowid, None)
        except sqlite3.IntegrityError as error:
            observation = (None, None, (type(error), error.sqlite_errorcode))
        observations.append((observation, db.in_transaction, db.execute("select last_insert_rowid()").fetchone()[0]))
    assert observations[0] == observations[1]
    assert water(native) == water(target) == water(target, "tasks:published")
    compare(native, target, kind)


@pytest.mark.parametrize("mode", ("plain", "ignore", "upsert"))
def test_native_full_preserves_earlier_caller_work(allocated, mode):
    native, target = allocated
    values = base(native, "reply_tasks")
    for db, writer in ((native, native_insert), (target, target_insert)):
        writer(db, "reply_tasks", values)
        db.execute("update sqlite_sequence set seq=? where name='tasks'", (2**63-1,))
        db.execute("create table sentinel(value integer)")
        db.execute("insert into sentinel values(7)")
    prefix = water(target, "tasks:published")
    for db, writer in ((native, native_insert), (target, target_insert)):
        kwargs = {"ignore": True} if mode == "ignore" else {}
        if mode == "upsert":
            kwargs["tail"] = " on conflict do update set id=id"
        with pytest.raises(sqlite3.OperationalError) as raised:
            writer(db, "reply_tasks", values, **kwargs)
        assert raised.value.sqlite_errorcode == sqlite3.SQLITE_FULL
        assert db.in_transaction
        assert db.execute("select value from sentinel").fetchone()[0] == 7
    assert water(target, "tasks:published") == prefix


def test_identical_rows_different_ignored_allocation_history(pair):
    native, target = pair
    values = base(native, "reply_tasks")
    target.execute("begin")
    initialize_task8_sequence(target, 0)
    target_insert(target, "reply_tasks", values)
    insert_task12(target, "feedback_processing_batches", payload(target, "feedback_processing_batches"))
    target.commit()
    before = tuple(tuple(row) for row in target.execute("select id,record_type,legacy_key,payload_json,parent_id,current_input_id,claimed_input_id from tasks order by id"))
    target.execute("begin")
    insert_task12(target, "feedback_processing_batches", payload(target, "feedback_processing_batches"), ignore=True)
    assert water(target) == 3
    assert water(target, "tasks:published") == 1
    target.rollback()
    target.execute("begin")
    target_insert(target, "reply_tasks", values, ignore=True)
    assert water(target) == 3
    assert water(target, "tasks:published") == 3
    assert before == tuple(tuple(row) for row in target.execute("select id,record_type,legacy_key,payload_json,parent_id,current_input_id,claimed_input_id from tasks order by id"))


@pytest.mark.parametrize("rollback", ("transaction", "savepoint"))
def test_allocator_rolls_back_with_caller(allocated, rollback):
    native, target = allocated
    before = water(target, "tasks:published")
    if rollback == "savepoint":
        target.execute("savepoint caller")
    target_insert(target, "reply_tasks", base(native, "reply_tasks"))
    assert water(target, "tasks:published") == 1
    if rollback == "savepoint":
        target.execute("rollback to caller")
        assert water(target, "tasks:published") == before
    else:
        target.rollback()
        assert water(target, "tasks:published") is None


@pytest.mark.parametrize("source_water", (0, 17, 2048))
def test_migration_resets_bulk_explicit_ids_to_captured_water(allocated, source_water):
    native, target = allocated
    values = base(native, "reply_tasks")
    for physical in (-99, -1, 23):
        explicit_insert(target, "reply_tasks", physical_id=physical, legacy_key=str(physical), values=values | {"conversation_id": str(physical)})
    initialize_task8_sequence(target, source_water)
    assert water(target, "tasks:published") == source_water
    assert target.in_transaction


def test_schema_creation_is_ddl_only_and_initializer_has_no_commit():
    db = sqlite3.connect(":memory:")
    try:
        create_task20_schema(db)
        assert not db.in_transaction
        with pytest.raises(ValueError):
            initialize_task8_sequence(db, 0)
        db.execute("begin")
        initialize_task8_sequence(db, 0)
        assert db.in_transaction
        db.rollback()
        assert water(db, "tasks:published") is None
    finally:
        db.close()


@pytest.mark.parametrize("kind", TASK8_KINDS)
def test_upsert_consumes_source_water_with_native_cursor(allocated, kind):
    native, target = allocated
    values = base(native, kind)
    observations = []
    for db, writer in ((native, native_insert), (target, target_insert)):
        writer(db, kind, values)
        cursor = writer(db, kind, values, tail=" on conflict do update set id=id")
        observations.append((cursor.rowcount, cursor.lastrowid, db.execute("select last_insert_rowid()").fetchone()[0], db.in_transaction))
    assert observations[0] == observations[1]
    assert water(native) == water(target) == water(target, "tasks:published") == 2
    compare(native, target, kind)


@pytest.mark.parametrize("initial", (None, 0, 900))
def test_source_absence_zero_and_deleted_high_provenance(pair, initial):
    native, target = pair
    for db in (native, target):
        db.execute("begin")
        db.execute("delete from sqlite_sequence where name='tasks'")
        if initial is not None:
            db.execute("insert into sqlite_sequence(name,seq) values('tasks',?)", (initial,))
    initialize_task8_sequence(target, 0 if initial is None else initial)
    # Original metadata keeps absence distinct; the runtime counter alone is not
    # advertised as a whole inverse. No row can reconstruct deleted-high water.
    assert water(native) is initial if initial is None else water(native) == initial
    assert water(target, "tasks:published") == (initial or 0)
    values = base(native, "reply_tasks")
    native_insert(native, "reply_tasks", values)
    target_insert(target, "reply_tasks", values)
    assert water(native) == water(target) == water(target, "tasks:published") == (initial or 0)+1


def test_initializer_keeps_prior_caller_work_rollbackable(pair):
    native, target = pair
    for db in (native, target):
        db.execute("create table sentinel(value integer)")
        db.execute("begin")
        db.execute("insert into sentinel values(7)")
    initialize_task8_sequence(target, 0)
    for db in (native, target):
        db.rollback()
    assert list(native.execute("select * from sentinel")) == list(target.execute("select * from sentinel")) == []


def test_initializer_retains_callers_savepoint(pair):
    native, target = pair
    for db in (native, target):
        db.execute("begin")
        db.execute("savepoint caller")
    initialize_task8_sequence(target, 0)
    for db in (native, target):
        db.execute("rollback to caller")
    assert target.execute("select seq from sqlite_sequence where name='tasks:published'").fetchone() is None
