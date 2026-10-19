"""Actual feedback INSERT scalar expressions keep native execution timing."""

import json
from pathlib import Path
import sqlite3

import pytest

from app.task12_storage import task12_insert_parts, insert_task12
from app.task8_storage import register_task8_functions
from tests.test_task12_storage import target as target, snapshot, supplied
from tests.test_tasks12_native_source import insert

pytest_plugins = ("tests.test_tasks12_native_source",)
CAPTURE = json.loads(
    (
        Path(__file__).parents[1]
        / "docs/superpowers/specs/2026-10-16-feedback-insert-expression-source.json"
    ).read_text()
)["statements"]
KIND = "feedback_processing_items"


def prepare(connection):
    register_task8_functions(connection)
    connection.create_function("current_timestamp", 0, lambda: "fixed-clock")


def feedback(connection, value):
    connection.execute(
        "create table feedback_events(key text primary key,resolved_at text)"
    )
    if value is not None:
        connection.execute(
            "insert into feedback_events values('feedback-a',?)", (value,)
        )


def parts(connection, owner):
    values = {"feedback_key": "feedback-a"}
    if owner == "resolve_feedback_event":
        expressions = {"status": "'resolved'", "resolved_at": "current_timestamp"}
        parameters = ()
    else:
        if owner == "create_feedback_processing_batch":
            values["batch_id"] = "seed"
            condition = "trim(coalesce((select resolved_at from feedback_events where key=?), ''))"
        else:
            condition = "trim((select resolved_at from feedback_events where key=?))"
        expressions = {
            "status": "case when "
            + condition
            + " <> '' then 'resolved' else 'pending' end",
            "resolved_at": "coalesce((select resolved_at from feedback_events where key=?), '')",
        }
        parameters = ("feedback-a", "feedback-a")
    return task12_insert_parts(
        connection, KIND, values, insert_sql=expressions, insert_parameters=parameters
    )


@pytest.mark.parametrize("owner", tuple(CAPTURE))
@pytest.mark.parametrize("resolved", (None, "", " resolved-original "))
def test_actual_three_insert_values_match_native_source(db, target, owner, resolved):
    for connection in (db, target):
        prepare(connection)
        feedback(connection, resolved)
    native_sql = CAPTURE[owner]["sql"].split("on conflict", 1)[0]
    native_parameters = {
        "upsert_feedback_event": ("feedback-a", "feedback-a", "feedback-a"),
        "create_feedback_processing_batch": (
            "feedback-a",
            "seed",
            "feedback-a",
            "feedback-a",
        ),
        "resolve_feedback_event": ("feedback-a",),
    }[owner]
    proposed = parts(target, owner)
    native = db.execute(native_sql, native_parameters)
    actual = target.execute(proposed.sql, proposed.parameters)
    assert actual.rowcount == native.rowcount == 1
    assert proposed.source_rowid == native.lastrowid
    snapshot(db, target, KIND)


@pytest.mark.parametrize(
    "owner", ("upsert_feedback_event", "create_feedback_processing_batch")
)
def test_subquery_is_not_preselected_or_evaluated_by_parts(db, target, owner):
    for connection in (db, target):
        prepare(connection)
    # The queried table does not yet exist when SQL parts are prepared.
    proposed = parts(target, owner)
    assert not target.execute(
        "select 1 from tasks where record_type=?", (KIND,)
    ).fetchall()
    for connection in (db, target):
        feedback(connection, "after-prepare")
    native_sql = CAPTURE[owner]["sql"].split("on conflict", 1)[0]
    parameters = (
        ("feedback-a", "seed", "feedback-a", "feedback-a")
        if owner == "create_feedback_processing_batch"
        else ("feedback-a",) * 3
    )
    db.execute(native_sql, parameters)
    target.execute(proposed.sql, proposed.parameters)
    rows = snapshot(db, target, KIND)
    assert rows[0]["resolved_at"] == "after-prepare"


@pytest.mark.parametrize("value", (1.2345678901234567, 2**53 + 1, 0.0))
def test_scalar_real_is_exact_and_evaluated_once_at_execution(db, target, value):
    kind = KIND
    calls = [0, 0]
    for n, connection in enumerate((db, target)):
        prepare(connection)

        def rhs(n=n):
            calls[n] += 1
            return value

        connection.create_function("rhs", 0, rhs)
    values = supplied(kind)
    values.pop("attempt_id", None)
    proposed = task12_insert_parts(
        target, kind, values, insert_sql={"attempt_id": "rhs()"}
    )
    assert calls == [0, 0]
    db.execute(
        "insert into feedback_processing_items("
        + ",".join(values)
        + ",attempt_id) values("
        + ",".join("?" for _ in values)
        + ",rhs())",
        tuple(values.values()),
    )
    target.execute(proposed.sql, proposed.parameters)
    assert calls == [1, 1]
    snapshot(db, target, kind)


def test_lazy_case_and_expression_override_do_not_evaluate_unused_default(db, target):
    def forbidden():
        raise AssertionError("unused native expression/default evaluated")

    for connection in (db, target):
        register_task8_functions(connection)
        connection.create_function("forbidden", 0, forbidden)
        connection.create_function("current_timestamp", 0, forbidden)
    values = {"feedback_key": "feedback-a", "updated_at": "bound-clock"}
    proposed = task12_insert_parts(
        target,
        KIND,
        values,
        insert_sql={"created_at": "case when 1 then ? else forbidden() end"},
        insert_parameters=("expression-clock",),
    )
    db.execute(
        "insert into feedback_processing_items(feedback_key,updated_at,created_at) values(?,?,case when 1 then ? else forbidden() end)",
        ("feedback-a", "bound-clock", "expression-clock"),
    )
    target.execute(proposed.sql, proposed.parameters)
    snapshot(db, target, KIND)


def test_native_expression_constraint_failure_preserves_caller_work(db, target):
    for connection in (db, target):
        prepare(connection)
        connection.execute("create table caller_sentinel(value)")
        connection.execute("insert into caller_sentinel values(7)")
    before = snapshot(db, target, KIND)
    proposed = task12_insert_parts(
        target, KIND, {"feedback_key": "feedback-a"}, insert_sql={"status": "null"}
    )
    for connection, sql, parameters in (
        (
            db,
            "insert into feedback_processing_items(feedback_key,status) values(?,null)",
            ("feedback-a",),
        ),
        (target, proposed.sql, proposed.parameters),
    ):
        with pytest.raises(sqlite3.IntegrityError) as error:
            connection.execute(sql, parameters)
        assert error.value.sqlite_errorcode == (
            sqlite3.SQLITE_CONSTRAINT_NOTNULL
            if connection is db
            else sqlite3.SQLITE_CONSTRAINT_CHECK
        )
        # Existing finite JSON target expresses native NOT NULL as a CHECK.
        assert error.value.sqlite_errorcode & 255 == sqlite3.SQLITE_CONSTRAINT
        assert connection.in_transaction
        assert (
            connection.execute("select value from caller_sentinel").fetchone()[0] == 7
        )
    assert snapshot(db, target, KIND) == before


@pytest.mark.parametrize(
    "kind,field",
    (
        (KIND, "feedback_key"),
        ("feedback_iteration_decisions", "batch_id"),
        ("okr_review_items", "id"),
    ),
)
def test_expression_identity_dependencies_are_bound_not_scalar_sql(
    db, target, kind, field
):
    trace = []
    target.set_trace_callback(trace.append)
    with pytest.raises(ValueError, match="nonidentity payload"):
        task12_insert_parts(target, kind, {}, insert_sql={field: "1"})
    assert not trace


def test_bound_and_expression_duplicate_rejected_before_write(db, target):
    trace = []
    target.set_trace_callback(trace.append)
    with pytest.raises(ValueError, match="supplied twice"):
        task12_insert_parts(
            target, KIND, {"status": "pending"}, insert_sql={"status": "'resolved'"}
        )
    assert not trace


def test_no_expression_path_keeps_point_writer_identity_and_payload(db, target):
    kind = "wechat_memory_import_jobs"
    native = insert(db, kind, supplied(kind))
    actual = insert_task12(target, kind, supplied(kind))
    assert actual.lastrowid == native.lastrowid
    snapshot(db, target, kind)


def test_replace_null_expression_uses_native_default_and_new_source_rowid(db, target):
    for connection in (db, target):
        prepare(connection)
    values = supplied(KIND)
    insert(db, KIND, values)
    insert_task12(target, KIND, values)
    proposed = task12_insert_parts(
        target,
        KIND,
        {"feedback_key": values["feedback_key"]},
        replace=True,
        insert_sql={"status": "null"},
    )
    native = db.execute(
        "insert or replace into feedback_processing_items(feedback_key,status) values(?,null)",
        (values["feedback_key"],),
    )
    actual = target.execute(proposed.sql, proposed.parameters)
    assert actual.rowcount == native.rowcount == 1
    assert proposed.source_rowid == native.lastrowid == 2
    rows = snapshot(db, target, KIND)
    assert rows[0]["status"] == "pending"


@pytest.mark.parametrize("value", (None, "expression-clock"))
def test_replace_expression_and_default_evaluate_once_and_lazily(db, target, value):
    calls = [0, 0]
    defaults = [0, 0]
    for n, connection in enumerate((db, target)):
        register_task8_functions(connection)

        def rhs(n=n):
            calls[n] += 1
            return value

        def default_clock(n=n):
            defaults[n] += 1
            return "native-default-clock"

        connection.create_function("rhs", 0, rhs)
        connection.create_function("current_timestamp", 0, default_clock)
    values = {"feedback_key": "feedback-a", "updated_at": "bound-clock"}
    proposed = task12_insert_parts(
        target, KIND, values, replace=True, insert_sql={"created_at": "rhs()"}
    )
    assert calls == defaults == [0, 0]
    db.execute(
        "insert or replace into feedback_processing_items(feedback_key,updated_at,created_at) values(?,?,rhs())",
        tuple(values.values()),
    )
    target.execute(proposed.sql, proposed.parameters)
    assert calls == [1, 1]
    assert defaults == ([1, 1] if value is None else [0, 0])
    snapshot(db, target, KIND)


def test_replace_null_required_expression_without_default_still_rejects(db, target):
    kind = "workbench_artifacts"
    for connection in (db, target):
        prepare(connection)
    values = supplied(kind)
    values.pop("label")
    proposed = task12_insert_parts(
        target, kind, values, replace=True, insert_sql={"label": "null"}
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "insert or replace into workbench_artifacts("
            + ",".join(values)
            + ",label) values("
            + ",".join("?" for _ in values)
            + ",null)",
            tuple(values.values()),
        )
    with pytest.raises(sqlite3.IntegrityError):
        target.execute(proposed.sql, proposed.parameters)
    snapshot(db, target, kind)


@pytest.mark.parametrize("exhausted", (False, True))
def test_preinsert_round_guard_input_remains_bound_before_any_preparation(
    db, target, exhausted
):
    kind = "feedback_processing_rounds"
    if exhausted:
        data = supplied(kind) | {"id": 2**63 - 1}
        insert(db, kind, data)
        insert_task12(target, kind, data)
        for connection in (db, target):
            connection.commit()
            connection.execute("create table caller_sentinel(value)")
            connection.execute("insert into caller_sentinel values(7)")
    values = supplied(kind, "b")
    unbound = dict(values)
    unbound.pop("round_number")
    trace = []
    target.set_trace_callback(trace.append)
    # Scalar SQL is unsupported for an input the existing allocator must inspect.
    # Reject before locks, source allocator checks, or any caller-visible write.
    with pytest.raises(ValueError, match="non-AUTOINCREMENT"):
        task12_insert_parts(target, kind, unbound, insert_sql={"round_number": "1"})
    assert trace == []
    target.set_trace_callback(None)
    if exhausted:
        for connection, operation in (
            (db, lambda: insert(db, kind, values)),
            (target, lambda: insert_task12(target, kind, values)),
        ):
            with pytest.raises(sqlite3.OperationalError) as error:
                operation()
            assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL
            assert connection.in_transaction
            assert (
                connection.execute("select value from caller_sentinel").fetchone()[0]
                == 7
            )
    else:
        native = insert(db, kind, values)
        actual = insert_task12(target, kind, values)
        assert actual.lastrowid == native.lastrowid == 1
    snapshot(db, target, kind)


@pytest.mark.parametrize(
    "kind",
    (
        "feedback_iteration_decisions",
        "feedback_processing_rounds",
        "oa_notification_events",
        "okr_review_items",
        "wechat_memory_import_jobs",
    ),
)
def test_independent_autoincrement_sources_keep_bound_insert_inputs(db, target, kind):
    data = supplied(kind)
    data.pop("created_at")
    trace = []
    target.set_trace_callback(trace.append)
    with pytest.raises(ValueError, match="non-AUTOINCREMENT"):
        task12_insert_parts(
            target, kind, data, insert_sql={"created_at": "'scalar-clock'"}
        )
    assert trace == []
    target.set_trace_callback(None)
    native = insert(db, kind, supplied(kind))
    actual = insert_task12(target, kind, supplied(kind))
    assert actual.lastrowid == native.lastrowid == 1
    snapshot(db, target, kind)
