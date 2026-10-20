"""Native actual/fresh SQLite versus the twelve-kind target writer."""

from contextlib import closing
import sqlite3

import pytest

from app.task12_storage import (
    delete_task12,
    insert_task12,
    task12_projection,
    update_task12,
)
from app.task20_definitions import (
    task20_column,
    task20_projection,
)
from tests.task_lifecycle_test_support import create_task20_schema, insert as seed_task
from tests.test_tasks12_native_source import (
    ACTUAL,
    AUTO,
    NAMES,
    TEXT_PK,
    insert,
    values,
)

pytest_plugins = ("tests.test_tasks12_native_source",)


@pytest.fixture
def target(db):
    db.execute(
        "update feedback_processing_batches set created_at='seed-clock',updated_at='seed-clock' where batch_id='seed'"
    )
    db.commit()
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys=on")
        create_task20_schema(connection)
        seed_task(connection, "reply_tasks", physical_id=-11, legacy_key="-11")
        seed_task(
            connection, "workbench_tasks", physical_id=-12, legacy_key="public-task"
        )
        seed_task(
            connection,
            "workbench_turns",
            physical_id=-13,
            legacy_key="public-turn",
            parent_id=-12,
        )
        insert_task12(
            connection,
            "feedback_processing_batches",
            dict(batch_id="seed", created_at="seed-clock", updated_at="seed-clock"),
            source_rowid=1,
        )
        connection.commit()
        connection.execute("begin")
        yield connection


def supplied(kind, suffix="a"):
    data = values(kind, suffix)
    for field in ACTUAL["tables"][kind]["table_xinfo"]:
        if field["dflt_value"] == "current_timestamp":
            data[field["name"]] = "source-clock"
    return data


def snapshot(db, target, kind, *, ignore_default_clocks=False):
    names = [f["name"] for f in ACTUAL["tables"][kind]["table_xinfo"]]
    native = db.execute(
        "select rowid as __source_rowid,* from " + kind + " order by rowid"
    ).fetchall()
    current = target.execute(
        "select * from("
        + task12_projection(kind, include_rowid=True)
        + ") order by rowid"
    ).fetchall()
    fields = [
        n
        for n in names
        if not (ignore_default_clocks and n in ("created_at", "updated_at"))
    ]
    left = [
        (r["__source_rowid"], *((type(r[n]), r[n]) for n in fields)) for r in native
    ]
    right = [(r["rowid"], *((type(r[n]), r[n]) for n in fields)) for r in current]
    assert left == right
    return current


@pytest.mark.parametrize("kind", NAMES)
def test_minimal_source_values_defaults_and_identity(db, target, kind):
    data = values(kind)
    before = target.execute("select current_timestamp").fetchone()[0]
    native = insert(db, kind, supplied=data)
    result = insert_task12(target, kind, data)
    after = target.execute("select current_timestamp").fetchone()[0]
    assert (result.lastrowid, result.rowcount) == (native.lastrowid, native.rowcount)
    rows = snapshot(db, target, kind, ignore_default_clocks=True)
    row = next(r for r in rows if r["rowid"] == result.lastrowid)
    for c in ACTUAL["tables"][kind]["table_xinfo"]:
        if c["dflt_value"] == "current_timestamp" and c["name"] not in data:
            assert before <= row[c["name"]] <= after
    assert not list(target.execute("pragma foreign_key_check"))


@pytest.mark.parametrize("kind", AUTO)
def test_independent_auto_streams_delete_and_explicit_high_water(db, target, kind):
    data = supplied(kind)
    native = insert(db, kind, supplied=data, id=-7)
    result = insert_task12(target, kind, data | {"id": -7})
    assert result.lastrowid == native.lastrowid == -7
    data = supplied(kind, "b")
    native = insert(db, kind, supplied=data, id=17)
    assert (
        insert_task12(target, kind, data | {"id": 17}).lastrowid
        == native.lastrowid
        == 17
    )
    if kind != "feedback_iteration_decisions":
        db.execute("delete from " + kind + " where id=17")
        assert delete_task12(target, kind, 17) == 1
    data = supplied(kind, "c")
    assert (
        insert(db, kind, supplied=data).lastrowid
        == insert_task12(target, kind, data).lastrowid
        == 18
    )
    snapshot(db, target, kind)
    assert (
        db.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone()[
            0
        ]
        == 18
    )
    assert (
        target.execute(
            "select seq from sqlite_sequence where name=?", ("tasks:" + kind,)
        ).fetchone()[0]
        == 18
    )


@pytest.mark.parametrize("kind", [k for k in NAMES if k not in AUTO])
def test_signed_source_rowids_and_native_reuse(db, target, kind):
    data = supplied(kind)
    assert (
        insert(db, kind, supplied=data, rowid=-9).lastrowid
        == insert_task12(target, kind, data, source_rowid=-9).lastrowid
        == -9
    )
    data = supplied(kind, "b")
    expected = insert(db, kind, supplied=data).lastrowid
    result = insert_task12(target, kind, data)
    assert result.lastrowid == expected
    db.execute("delete from " + kind + " where rowid=?", (expected,))
    assert delete_task12(target, kind, expected) == 1
    data = supplied(kind, "c")
    assert (
        insert(db, kind, supplied=data).lastrowid
        == insert_task12(target, kind, data).lastrowid
        == expected
    )
    snapshot(db, target, kind)


@pytest.mark.parametrize(
    "kind", ("feedback_processing_rounds", "oa_notification_events")
)
def test_ignored_natural_conflict_consumes_public_water(db, target, kind):
    data = supplied(kind)
    insert(db, kind, supplied=data)
    insert_task12(target, kind, data)
    ignored = insert(db, kind, supplied=data, verb="insert or ignore", id=9)
    result = insert_task12(target, kind, data | {"id": 9}, ignore=True)
    assert ignored.rowcount == result.rowcount == 0 and result.lastrowid is None
    data = supplied(kind, "b")
    assert (
        insert(db, kind, supplied=data).lastrowid
        == insert_task12(target, kind, data).lastrowid
        == 10
    )
    snapshot(db, target, kind)


@pytest.mark.parametrize("kind", AUTO)
def test_ignored_required_null_consumes_water_and_replace_defaults(db, target, kind):
    field = {
        "feedback_iteration_decisions": "decision_json",
        "feedback_processing_rounds": "feedback_key",
        "oa_notification_events": "conversation_id",
        "okr_review_items": "objective_title",
        "wechat_memory_import_jobs": "account_id",
    }[kind]
    bad = supplied(kind) | {field: None}
    native = insert(db, kind, supplied=bad, verb="insert or ignore")
    result = insert_task12(target, kind, bad, ignore=True)
    assert result.rowcount == native.rowcount == 0
    data = supplied(kind, "b")
    assert (
        insert(db, kind, supplied=data).lastrowid
        == insert_task12(target, kind, data).lastrowid
        == 2
    )
    snapshot(db, target, kind)


@pytest.mark.parametrize("kind", AUTO)
def test_full_scope_matches_native_source(db, target, kind):
    data = supplied(kind)
    insert(db, kind, supplied=data, id=2**63 - 1)
    insert_task12(target, kind, data | {"id": 2**63 - 1})
    for connection in (db, target):
        connection.commit()
        connection.execute("create table caller_sentinel(value)")
        connection.execute("insert into caller_sentinel values(1)")
    for operation in (
        lambda: insert(db, kind, supplied=supplied(kind, "b")),
        lambda: insert_task12(target, kind, supplied(kind, "b")),
    ):
        with pytest.raises(sqlite3.OperationalError) as error:
            operation()
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    expected = kind == "feedback_processing_rounds"
    assert db.in_transaction == target.in_transaction == expected
    assert (
        db.execute("select count(*) from caller_sentinel").fetchone()[0]
        == target.execute("select count(*) from caller_sentinel").fetchone()[0]
        == int(expected)
    )


@pytest.mark.parametrize("value", (None, 0, -1, "bad", 2.5))
def test_round_before_full_error_precedence(db, target, value):
    kind = "feedback_processing_rounds"
    data = supplied(kind)
    insert(db, kind, supplied=data, id=2**63 - 1)
    insert_task12(target, kind, data | {"id": 2**63 - 1})
    data = supplied(kind, "b") | {"round_number": value}
    for operation in (
        lambda: insert(db, kind, supplied=data),
        lambda: insert_task12(target, kind, data),
    ):
        with pytest.raises(sqlite3.IntegrityError) as error:
            operation()
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_TRIGGER
        assert str(error.value) == "feedback_processing_round_number_must_be_positive"
    assert db.in_transaction and target.in_transaction


@pytest.mark.parametrize("kind", TEXT_PK)
def test_nullable_text_keys_and_replace_hidden_rowid(db, target, kind):
    key = next(f["name"] for f in ACTUAL["tables"][kind]["table_xinfo"] if f["pk"])
    for suffix, keyvalue in (("a", None), ("b", None), ("c", b"blob-key")):
        data = supplied(kind, suffix) | {key: keyvalue}
        assert (
            insert(db, kind, supplied=data).lastrowid
            == insert_task12(target, kind, data).lastrowid
        )
    data = supplied(kind, "d") | {key: b"blob-key"}
    native = insert(db, kind, supplied=data, verb="insert or replace")
    result = insert_task12(target, kind, data, replace=True)
    assert native.lastrowid == result.lastrowid
    snapshot(db, target, kind)


def test_batch_insert_replace_preserves_referenced_endpoint(db, target):
    batch = supplied("feedback_processing_batches") | {"batch_id": "linked"}
    insert(db, "feedback_processing_batches", supplied=batch)
    first = insert_task12(target, "feedback_processing_batches", batch)
    decision = supplied("feedback_iteration_decisions") | {"batch_id": "linked"}
    insert(db, "feedback_iteration_decisions", supplied=decision)
    insert_task12(target, "feedback_iteration_decisions", decision)
    batch["updated_at"] = "replaced-clock"
    expected = insert(
        db, "feedback_processing_batches", supplied=batch, verb="insert or replace"
    ).lastrowid
    result = insert_task12(target, "feedback_processing_batches", batch, replace=True)
    assert result.lastrowid == expected and result.physical_id == first.physical_id
    snapshot(db, target, "feedback_processing_batches")
    assert not list(target.execute("pragma foreign_key_check"))


def test_batch_update_replace_preserves_other_referenced_endpoint(db, target):
    a = supplied("feedback_processing_batches") | {
        "batch_id": "A",
        "created_at": "clock-A",
    }
    b = supplied("feedback_processing_batches") | {
        "batch_id": "B",
        "created_at": "clock-B",
    }
    a_id = insert(db, "feedback_processing_batches", supplied=a).lastrowid
    left = insert_task12(target, "feedback_processing_batches", a)
    insert(db, "feedback_processing_batches", supplied=b)
    right = insert_task12(target, "feedback_processing_batches", b)
    decision = supplied("feedback_iteration_decisions") | {"batch_id": "B"}
    insert(db, "feedback_iteration_decisions", supplied=decision)
    insert_task12(target, "feedback_iteration_decisions", decision)
    db.execute(
        "update or replace feedback_processing_batches set batch_id=? where rowid=?",
        ("B", a_id),
    )
    assert (
        update_task12(
            target,
            "feedback_processing_batches",
            left.lastrowid,
            {"batch_id": "B"},
            replace=True,
        )
        == 1
    )
    row = target.execute(
        task12_projection("feedback_processing_batches", include_rowid=True)
        + " and "
        + task20_column("feedback_processing_batches", "batch_id")
        + "='B'"
    ).fetchone()
    assert row["rowid"] == a_id and row["created_at"] == "clock-A"
    physical = target.execute(
        "select id from tasks where record_type='feedback_processing_batches' and legacy_key=?",
        (str(a_id),),
    ).fetchone()[0]
    assert physical == right.physical_id
    snapshot(db, target, "feedback_processing_batches")
    assert not list(target.execute("pragma foreign_key_check"))


@pytest.mark.parametrize(
    "kind", [k for k in NAMES if k != "feedback_iteration_decisions"]
)
def test_guarded_update_and_delete_match_native(db, target, kind):
    data = supplied(kind)
    expected = insert(db, kind, supplied=data).lastrowid
    result = insert_task12(target, kind, data)
    field = next(
        f["name"]
        for f in ACTUAL["tables"][kind]["table_xinfo"]
        if f["name"] in ("updated_at", "label", "kr_title", "filename")
    )
    db.execute(
        "update " + kind + " set " + field + "=? where rowid=?",
        ("updated-value", expected),
    )
    assert (
        update_task12(
            target,
            kind,
            result.lastrowid,
            {field: "updated-value"},
            where_sql=field + "=?",
            parameters=("wrong-value",),
        )
        == 0
    )
    assert update_task12(target, kind, result.lastrowid, {field: "updated-value"}) == 1
    snapshot(db, target, kind)
    assert (
        delete_task12(
            target,
            kind,
            result.lastrowid,
            where_sql=field + "=?",
            parameters=("wrong-value",),
        )
        == 0
    )
    assert (
        delete_task12(
            target,
            kind,
            result.lastrowid,
            where_sql=field + "=?",
            parameters=("updated-value",),
        )
        == 1
    )
    db.execute("delete from " + kind + " where rowid=?", (expected,))
    snapshot(db, target, kind)


@pytest.mark.parametrize(
    "kind", ("business_object_tasks", "workbench_artifacts", "workbench_attachments")
)
def test_missing_physical_parent_cannot_be_satisfied_by_child_itself(kind):
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("pragma foreign_keys=on")
        create_task20_schema(connection)
        connection.execute("begin")
        field = {
            "business_object_tasks": "reply_task_id",
            "workbench_artifacts": "turn_id",
            "workbench_attachments": "task_id",
        }[kind]
        with pytest.raises(sqlite3.IntegrityError):
            insert_task12(connection, kind, supplied(kind) | {field: 1})
        assert connection.execute("select count(*) from tasks").fetchone()[0] == 0


def test_new_source_stream_survives_old_physical_allocator_full(db, target):
    from app.task_storage import insert_task

    insert_task(
        db,
        "workbench_tasks",
        dict(id=2**63 - 1, legacy_key="max-task", title="Max", runtime_kind="codex"),
    )
    seed_task(target, "workbench_tasks", physical_id=2**63 - 1, legacy_key="max-task")
    data = supplied("wechat_memory_import_jobs")
    native = insert(db, "wechat_memory_import_jobs", supplied=data)
    result = insert_task12(target, "wechat_memory_import_jobs", data)
    assert result.lastrowid == native.lastrowid == 1
    assert result.physical_id != 2**63 - 1
    snapshot(db, target, "wechat_memory_import_jobs")


def test_writes_use_native_implicit_transaction_without_commit(target):
    target.commit()
    result = insert_task12(
        target, "wechat_memory_import_jobs", supplied("wechat_memory_import_jobs")
    )
    assert result.rowcount == 1 and target.in_transaction
    target.rollback()
    assert not target.execute(task12_projection("wechat_memory_import_jobs")).fetchall()


@pytest.mark.parametrize(
    "value",
    (
        0,
        0.0,
        2,
        "2",
        "2.5",
        "native-text",
        b"weight",
        float("inf"),
        float("-inf"),
        5e-324,
    ),
)
def test_ordered_real_projection_preserves_native_value_and_type(db, target, value):
    data = supplied("okr_review_items") | {
        "objective_weight": value,
        "kr_weight": value,
    }
    insert(db, "okr_review_items", supplied=data)
    insert_task12(target, "okr_review_items", data)
    snapshot(db, target, "okr_review_items")


@pytest.mark.parametrize("kind", NAMES)
def test_replace_null_uses_only_existing_native_defaults(db, target, kind):
    defaulted = {
        "business_object_tasks": "created_at",
        "email_agent_classification_tasks": "owner",
        "email_historical_candidates": "reason",
        "feedback_iteration_decisions": "created_at",
        "feedback_processing_batches": "status",
        "feedback_processing_items": "status",
        "feedback_processing_rounds": "attempt_id",
        "oa_notification_events": "requires_reply",
        "okr_review_items": "objective_weight",
        "wechat_memory_import_jobs": "status",
        "workbench_artifacts": "created_at",
        "workbench_attachments": "created_at",
    }[kind]
    data = supplied(kind)
    insert(db, kind, supplied=data)
    first = insert_task12(target, kind, data)
    if kind in AUTO:
        data["id"] = first.lastrowid
    data[defaulted] = None
    before = target.execute("select current_timestamp").fetchone()[0]
    native = insert(db, kind, supplied=data, verb="insert or replace")
    result = insert_task12(target, kind, data, replace=True)
    after = target.execute("select current_timestamp").fetchone()[0]
    assert result.lastrowid == native.lastrowid
    snapshot(db, target, kind, ignore_default_clocks=defaulted == "created_at")
    if defaulted == "created_at":
        row = target.execute(
            "select * from("
            + task12_projection(kind, include_rowid=True)
            + ") where rowid=?",
            (result.lastrowid,),
        ).fetchone()
        assert before <= row["created_at"] <= after


def test_interleaved_five_auto_streams_remain_independent(db, target):
    for expected, suffix in ((1, "a"), (2, "b")):
        for kind in AUTO:
            data = supplied(kind, suffix)
            assert insert(db, kind, supplied=data).lastrowid == expected
            assert insert_task12(target, kind, data).lastrowid == expected
    for kind in AUTO:
        snapshot(db, target, kind)


def test_classifier_owner_generation_finish_compare_and_swap(db, target):
    kind = "email_agent_classification_tasks"
    data = supplied(kind)
    identity = insert(db, kind, supplied=data).lastrowid
    assert insert_task12(target, kind, data).lastrowid == identity
    steps = (
        (
            {"status": "running", "owner": "A", "generation": 1},
            "status='pending'",
            (),
            1,
        ),
        (
            {"status": "done"},
            "status='running' and owner=? and generation=?",
            ("B", 1),
            0,
        ),
        (
            {"status": "done"},
            "status='running' and owner=? and generation=?",
            ("A", 0),
            0,
        ),
        (
            {"status": "done", "result_json": '{"adopted":true}'},
            "status='running' and owner=? and generation=?",
            ("A", 1),
            1,
        ),
    )
    for changes, guard, parameters, count in steps:
        native = db.execute(
            "update "
            + kind
            + " set "
            + ",".join(f + "=?" for f in changes)
            + " where rowid=? and "
            + guard,
            (*changes.values(), identity, *parameters),
        )
        assert native.rowcount == count
        assert (
            update_task12(
                target, kind, identity, changes, where_sql=guard, parameters=parameters
            )
            == count
        )
        snapshot(db, target, kind)


def test_missing_batch_fk_is_not_suppressed_by_ignore(db, target):
    kind = "feedback_iteration_decisions"
    data = supplied(kind) | {"batch_id": "missing"}
    for operation in (
        lambda: insert(db, kind, supplied=data, verb="insert or ignore"),
        lambda: insert_task12(target, kind, data, ignore=True),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            operation()
    invalid = data | {"decision_json": None}
    assert insert(db, kind, supplied=invalid, verb="insert or ignore").rowcount == 0
    assert insert_task12(target, kind, invalid, ignore=True).rowcount == 0
    valid = supplied(kind, "b")
    assert (
        insert(db, kind, supplied=valid).lastrowid
        == insert_task12(target, kind, valid).lastrowid
        == 2
    )


def test_batch_replace_failure_rolls_back_both_conflicting_rows(db, target):
    for key in ("A", "B"):
        data = supplied("feedback_processing_batches", key) | {"batch_id": key}
        insert(db, "feedback_processing_batches", supplied=data)
        insert_task12(target, "feedback_processing_batches", data)
    decision = supplied("feedback_iteration_decisions") | {"batch_id": "A"}
    insert(db, "feedback_iteration_decisions", supplied=decision)
    insert_task12(target, "feedback_iteration_decisions", decision)
    for operation in (
        lambda: db.execute(
            "update or replace feedback_processing_batches set batch_id='B' where batch_id='A'"
        ),
        lambda: update_task12(
            target, "feedback_processing_batches", 2, {"batch_id": "B"}, replace=True
        ),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            operation()
    snapshot(db, target, "feedback_processing_batches")
    assert not list(target.execute("pragma foreign_key_check"))


def test_source_identity_counter_and_cas_queries_use_numeric_index(target):
    kind = "wechat_memory_import_jobs"
    statements = []
    target.set_trace_callback(statements.append)
    result = insert_task12(target, kind, supplied(kind))
    assert update_task12(target, kind, result.lastrowid, {"status": "completed"}) == 1
    target.set_trace_callback(None)
    probes = [
        sql
        for sql in statements
        if sql.lower().startswith("select")
        and "cast(legacy_key as integer)" in sql
        and "record_type=" in sql
    ]
    assert probes
    for sql in probes:
        details = " ".join(
            row[3] for row in target.execute("explain query plan " + sql)
        )
        assert "idx_task20_numeric_identity" in details


def test_native_immutable_decision_update_and_delete(db, target):
    data = supplied("feedback_iteration_decisions")
    identity = insert(db, "feedback_iteration_decisions", supplied=data).lastrowid
    assert (
        insert_task12(target, "feedback_iteration_decisions", data).lastrowid
        == identity
    )
    for operation in (
        lambda: db.execute(
            "update feedback_iteration_decisions set decision_json=decision_json where id=?",
            (identity,),
        ),
        lambda: update_task12(target, "feedback_iteration_decisions", identity, {}),
        lambda: db.execute(
            "delete from feedback_iteration_decisions where id=?", (identity,)
        ),
        lambda: delete_task12(target, "feedback_iteration_decisions", identity),
    ):
        with pytest.raises(sqlite3.IntegrityError) as error:
            operation()
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_TRIGGER
        assert str(error.value) == "feedback iteration decisions are append-only"
    snapshot(db, target, "feedback_iteration_decisions")


@pytest.mark.parametrize(
    "kind", [k for k in NAMES if k != "feedback_iteration_decisions"]
)
def test_source_identity_update_and_next_allocation(db, target, kind):
    data = supplied(kind)
    previous = insert(db, kind, supplied=data).lastrowid
    assert insert_task12(target, kind, data).lastrowid == previous
    db.execute("update " + kind + " set rowid=17 where rowid=?", (previous,))
    if kind in AUTO:
        assert update_task12(target, kind, previous, {"id": 17}) == 1
    else:
        assert update_task12(target, kind, previous, {}, new_source_rowid=17) == 1
    snapshot(db, target, kind)
    data = supplied(kind, "b")
    assert (
        insert(db, kind, supplied=data).lastrowid
        == insert_task12(target, kind, data).lastrowid
        == 18
    )
    snapshot(db, target, kind)


def test_delete_source_rowid_guard_is_available(db, target):
    data = supplied("wechat_memory_import_jobs")
    identity = insert(db, "wechat_memory_import_jobs", supplied=data).lastrowid
    insert_task12(target, "wechat_memory_import_jobs", data)
    assert (
        delete_task12(
            target,
            "wechat_memory_import_jobs",
            identity,
            where_sql="rowid=?",
            parameters=(identity + 1,),
        )
        == 0
    )
    assert (
        db.execute(
            "delete from wechat_memory_import_jobs where rowid=?", (identity,)
        ).rowcount
        == 1
    )
    assert (
        delete_task12(
            target,
            "wechat_memory_import_jobs",
            identity,
            where_sql="rowid=?",
            parameters=(identity,),
        )
        == 1
    )
    snapshot(db, target, "wechat_memory_import_jobs")


@pytest.mark.parametrize("field", ("objective_weight", "kr_weight"))
@pytest.mark.parametrize(
    "stored,probe",
    ((2, "2"), ("2.5", "2.5"), ("native-text", "native-text"), (b"weight", b"weight")),
)
def test_real_comparison_affinity_reads_and_guarded_writes(
    db, target, field, stored, probe
):
    kind = "okr_review_items"
    data = supplied(kind) | {field: stored}
    source_id = insert(db, kind, supplied=data).lastrowid
    result = insert_task12(target, kind, data)
    predicate = field + "=?"
    native_count = db.execute(
        "select count(*) from " + kind + " where " + predicate, (probe,)
    ).fetchone()[0]
    assert native_count == 1
    for projection in (task12_projection(kind), task20_projection(kind)):
        assert (
            target.execute(
                "select count(*) from (" + projection + ") where " + predicate, (probe,)
            ).fetchone()[0]
            == native_count
        )
        native = db.execute(
            "select " + field + " from " + kind + " order by id"
        ).fetchone()[0]
        projected = target.execute(
            "select " + field + " from (" + projection + ") order by id"
        ).fetchone()[0]
        assert (type(projected), projected) == (type(native), native)
    assert (
        update_task12(
            target,
            kind,
            result.lastrowid,
            {"kr_title": "bad"},
            where_sql=predicate,
            parameters=("wrong",),
        )
        == 0
    )
    expected = db.execute(
        "update " + kind + " set kr_title='updated' where rowid=? and " + predicate,
        (source_id, probe),
    ).rowcount
    assert expected == 1
    assert (
        update_task12(
            target,
            kind,
            result.lastrowid,
            {"kr_title": "updated"},
            where_sql=predicate,
            parameters=(probe,),
        )
        == expected
    )
    snapshot(db, target, kind)
    assert (
        delete_task12(
            target, kind, result.lastrowid, where_sql=predicate, parameters=("wrong",)
        )
        == 0
    )
    expected = db.execute(
        "delete from " + kind + " where rowid=? and " + predicate, (source_id, probe)
    ).rowcount
    assert expected == 1
    assert (
        delete_task12(
            target, kind, result.lastrowid, where_sql=predicate, parameters=(probe,)
        )
        == expected
    )
    snapshot(db, target, kind)


def test_real_guard_select_limits_before_materialization(db, target):
    kind = "okr_review_items"
    for suffix in ("a", "b"):
        data = supplied(kind, suffix) | {"objective_weight": 2}
        insert(db, kind, supplied=data)
        result = insert_task12(target, kind, data)
    statements = []
    target.set_trace_callback(statements.append)
    try:
        assert (
            update_task12(
                target,
                kind,
                result.lastrowid,
                {"kr_title": "updated"},
                where_sql="objective_weight=?",
                parameters=("2",),
            )
            == 1
        )
        assert (
            delete_task12(
                target,
                kind,
                result.lastrowid,
                where_sql="objective_weight=?",
                parameters=("2",),
            )
            == 1
        )
    finally:
        target.set_trace_callback(None)
    guarded = [
        sql
        for sql in statements
        if "limit -1 offset 0" in sql
        and (
            sql.startswith("select * from(select")
            or sql.startswith("delete from tasks")
        )
    ]
    guarded = list(dict.fromkeys(guarded))
    assert len(guarded) == 2
    for sql in guarded:
        assert (
            "cast(legacy_key as integer)="
            + str(result.lastrowid)
            + " limit -1 offset 0"
            in sql
        )
        plan = " ".join(
            str(tuple(row)) for row in target.execute("explain query plan " + sql)
        )
        assert "idx_task20_numeric_identity" in plan
    remaining = target.execute(
        "select count(*) from (" + task12_projection(kind) + ")"
    ).fetchone()[0]
    assert remaining == 1


@pytest.mark.parametrize("field", ("objective_weight", "kr_weight"))
def test_real_public_projection_supports_filter_and_order_suffix(db, target, field):
    kind = "okr_review_items"
    for suffix, weight in (("a", 2), ("b", 3)):
        data = supplied(kind, suffix) | {field: weight}
        insert(db, kind, supplied=data)
        insert_task12(target, kind, data)
    for projection in (task12_projection(kind), task20_projection(kind)):
        actual = target.execute(
            projection + " and " + field + "=? order by id", ("2",)
        ).fetchall()
        expected = db.execute(
            "select * from " + kind + " where " + field + "=? order by id", ("2",)
        ).fetchall()
        assert len(actual) == len(expected) == 1
        assert (type(actual[0][field]), actual[0][field]) == (
            type(expected[0][field]),
            expected[0][field],
        )


@pytest.mark.parametrize("value", (None, 0, -1, "bad", 2.5))
def test_round_regular_insert_update_preserve_native_error(db, target, value):
    kind = "feedback_processing_rounds"
    data = supplied(kind) | {"round_number": value}
    for operation in (
        lambda: insert(db, kind, supplied=data),
        lambda: insert_task12(target, kind, data),
    ):
        with pytest.raises(sqlite3.IntegrityError) as error:
            operation()
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_TRIGGER
        assert str(error.value) == "feedback_processing_round_number_must_be_positive"
    assert db.in_transaction and target.in_transaction
    good = supplied(kind)
    identity = insert(db, kind, supplied=good).lastrowid
    assert insert_task12(target, kind, good).lastrowid == identity
    for operation in (
        lambda: db.execute(
            "update feedback_processing_rounds set round_number=? where id=?",
            (value, identity),
        ),
        lambda: update_task12(target, kind, identity, {"round_number": value}),
    ):
        with pytest.raises(sqlite3.IntegrityError) as error:
            operation()
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_TRIGGER
        assert str(error.value) == "feedback_processing_round_number_must_be_positive"
    snapshot(db, target, kind)
