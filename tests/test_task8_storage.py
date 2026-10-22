"""Frozen native eight-kind Tasks versus finite target SQL parts."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from app.task8_storage import (
    TASK8_KINDS,
    task8_insert_parts,
    register_task8_functions,
    task8_meeting_returning,
    task8_set_parts,
)
from app.task20_definitions import (
    task20_column,
    task20_projection,
)
from app.task_lifecycle_definitions import TASK20_LAYOUTS
from tests.task_lifecycle_test_support import create_task20_schema, insert as seed_target
from tests.test_task20_definitions import payload

SOURCE = json.loads(
    (
        Path(__file__).parents[1]
        / "docs/superpowers/specs/2026-10-16-task8-native-source.json"
    ).read_text()
)


def native_insert(db, kind, values, *, ignore=False, tail="", parameters=()):
    defaults = {
        f: sql
        for f, sql in SOURCE["source_insert_defaults"][kind].items()
        if f not in values
    }
    columns = ["record_type", *values, *defaults]
    expressions = ["?"] * (1 + len(values)) + list(defaults.values())
    return db.execute(
        "insert "
        + ("or ignore " if ignore else "")
        + "into tasks("
        + ",".join(columns)
        + ") values("
        + ",".join(expressions)
        + ")"
        + tail,
        (kind, *values.values(), *parameters),
    )


def target_insert(db, kind, values, *, ignore=False, tail="", parameters=(), **kwargs):
    parts = task8_insert_parts(kind, values, **kwargs)
    return db.execute(
        "insert "
        + ("or ignore " if ignore else "")
        + "into tasks("
        + parts.columns
        + ") values("
        + parts.values
        + ")"
        + tail,
        (*parts.parameters, *parameters),
    )


def base(db, kind):
    values = payload(db, kind)
    for field, default in SOURCE["source_insert_defaults"][kind].items():
        if default == "current_timestamp":
            values[field] = "source-clock"
    if kind == "reply_task_inputs":
        values["parent_id"] = -11
    elif kind == "workbench_turns":
        values["parent_id"] = -12
        values["legacy_key"] = "new-turn"
    elif kind == "workbench_tasks":
        values["legacy_key"] = "new-task"
    return values


@pytest.fixture(params=("actual", "fresh"))
def pair(request):
    with (
        closing(sqlite3.connect(":memory:")) as native,
        closing(sqlite3.connect(":memory:")) as target,
    ):
        for db in (native, target):
            db.row_factory = sqlite3.Row
            db.execute("pragma foreign_keys=on")
        objects = SOURCE[request.param]["objects"]
        for category in ("table", "index", "trigger"):
            for obj in objects:
                if obj["type"] == category and obj["sql"]:
                    native.execute(obj["sql"])
        create_task20_schema(target)
        register_task8_functions(target)
        for kind, physical, public in (
            ("reply_tasks", -11, "-11"),
            ("workbench_tasks", -12, "parent-task"),
        ):
            values = base(native, kind)
            values.pop("legacy_key", None)
            if kind == "reply_tasks":
                values.update(
                    conversation_id="parent-chat", trigger_message_id="parent-trigger"
                )
            native_insert(native, kind, values | {"id": physical, "legacy_key": public})
            seed_target(
                target, kind, physical_id=physical, legacy_key=public, values=values
            )
        native.commit()
        target.commit()
        yield native, target


def compare(native, target, kind):
    left = native.execute(
        "select * from(" + SOURCE["source_projections"][kind] + ") order by id"
    ).fetchall()
    right = target.execute(
        "select * from(" + task20_projection(kind) + ") order by id"
    ).fetchall()

    def normalized(rows):
        return [
            tuple((key, type(row[key]), row[key]) for key in row.keys()) for row in rows
        ]

    assert normalized(left) == normalized(right)
    assert not list(native.execute("pragma foreign_key_check"))
    assert not list(target.execute("pragma foreign_key_check"))
    return right


@pytest.mark.parametrize("kind", TASK8_KINDS)
def test_complete_native_insert_roundtrip(pair, kind):
    native, target = pair
    values = base(native, kind)
    a = native_insert(native, kind, values)
    b = target_insert(target, kind, values)
    assert (a.lastrowid, a.rowcount, native.in_transaction) == (
        b.lastrowid,
        b.rowcount,
        target.in_transaction,
    )
    compare(native, target, kind)


@pytest.mark.parametrize("kind", TASK8_KINDS)
def test_missing_defaults_are_statement_sql_and_nullable_fields_exist(pair, kind):
    native, target = pair
    values = {
        f: value
        for f, value in base(native, kind).items()
        if f not in SOURCE["source_insert_defaults"][kind] and value is not None
    }
    before = native.execute("select current_timestamp").fetchone()[0]
    a = native_insert(native, kind, values)
    b = target_insert(target, kind, values)
    after = target.execute("select current_timestamp").fetchone()[0]
    assert (a.lastrowid, a.rowcount) == (b.lastrowid, b.rowcount)
    left = dict(
        native.execute(
            "select * from(" + SOURCE["source_projections"][kind] + ") where id=?",
            (values.get("legacy_key", a.lastrowid),),
        ).fetchone()
    )
    right = dict(
        target.execute(
            "select * from(" + task20_projection(kind) + ") where id=?",
            (values.get("legacy_key", b.lastrowid),),
        ).fetchone()
    )
    for field, default in SOURCE["source_insert_defaults"][kind].items():
        if default == "current_timestamp":
            assert before <= left.pop(field) <= after
            assert before <= right.pop(field) <= after
    assert {f: (type(v), v) for f, v in left.items()} == {
        f: (type(v), v) for f, v in right.items()
    }


SCALARS = (
    None,
    "text",
    "2",
    "2.5",
    2,
    2.5,
    b"blob",
    memoryview(b"view"),
    float("inf"),
    float("-inf"),
    float("nan"),
    5e-324,
)


@pytest.mark.parametrize("kind", TASK8_KINDS)
@pytest.mark.parametrize("value", SCALARS)
def test_bound_insert_and_update_match_native_affinity_and_constraints(
    pair, kind, value
):
    native, target = pair
    field = (
        "title"
        if kind == "workbench_tasks"
        else "error_detail"
        if kind == "workbench_turns"
        else "trigger_text"
        if kind == "reply_task_inputs"
        else "error"
    )
    outcomes = []
    for db, insert in ((native, native_insert), (target, target_insert)):
        data = base(native, kind) | {field: value}
        try:
            cursor = insert(db, kind, data)
            outcomes.append((cursor.rowcount, None))
        except sqlite3.IntegrityError as error:
            outcomes.append((None, error.sqlite_errorcode))
    assert outcomes[0] == outcomes[1]
    compare(native, target, kind)
    if outcomes[0][0] is None:
        native_insert(native, kind, base(native, kind))
        target_insert(target, kind, base(native, kind))
    outcomes = []
    for db, column in ((native, field), (target, task20_column(kind, field))):
        try:
            if db is native:
                cursor = db.execute(
                    "update tasks set " + field + "=? where record_type=? and id>0",
                    (value, kind),
                )
            else:
                parts = task8_set_parts(kind, {field: value})
                cursor = db.execute(
                    "update tasks set " + parts.sql + " where record_type=? and id>0",
                    (*parts.parameters, kind),
                )
            outcomes.append((cursor.rowcount, None))
        except sqlite3.IntegrityError as error:
            outcomes.append((None, error.sqlite_errorcode))
    assert outcomes[0] == outcomes[1]
    compare(native, target, kind)


@pytest.mark.parametrize("kind", TASK8_KINDS)
def test_ignore_required_null_preserves_native_counter(pair, kind):
    native, target = pair
    field = (
        "title"
        if kind == "workbench_tasks"
        else "user_text"
        if kind == "workbench_turns"
        else "trigger_text"
        if kind == "reply_task_inputs"
        else "error"
    )
    values = base(native, kind) | {field: None}
    a = native_insert(native, kind, values, ignore=True)
    b = target_insert(target, kind, values, ignore=True)
    assert a.rowcount == b.rowcount == 0
    a = native_insert(native, kind, base(native, kind))
    b = target_insert(target, kind, base(native, kind))
    assert a.lastrowid == b.lastrowid == 2
    compare(native, target, kind)


def test_old_rhs_input_versions_and_physical_pointers_remain_atomic(pair):
    native, target = pair
    kind = "reply_tasks"
    values = base(native, kind) | {"input_version": 7}
    for db, insert in ((native, native_insert), (target, target_insert)):
        parent = insert(db, kind, values).lastrowid
        assert parent == 1
        inp = base(native, "reply_task_inputs") | {
            "parent_id": parent,
            "legacy_key": "999",
        }
        assert insert(db, "reply_task_inputs", inp).lastrowid == 2
        db.execute("update tasks set current_input_id=2 where id=1")
    native.execute(
        "update tasks set input_version=input_version+1,claimed_input_version=input_version,current_input_id=null,claimed_input_id=current_input_id where id=1"
    )
    parts = task8_set_parts(
        kind,
        {"current_input_id": None},
        update_sql={
            "input_version": "json_quote("
            + task20_column(kind, "input_version")
            + "+1)",
            "claimed_input_version": task20_column(kind, "input_version"),
        },
        structural_sql={"claimed_input_id": "current_input_id"},
    )
    assert (
        target.execute(
            "update tasks set " + parts.sql + " where id=1", parts.parameters
        ).rowcount
        == 1
    )
    rows = compare(native, target, kind)
    current = next(row for row in rows if row["id"] == 1)
    assert (
        current["input_version"],
        current["claimed_input_version"],
        current["current_input_id"],
        current["claimed_input_id"],
    ) == (8, 7, None, 999)
    assert (
        target.execute("select claimed_input_id from tasks where id=1").fetchone()[0]
        == 2
    )


@pytest.mark.parametrize("with_cte", (False, True))
def test_native_update_returning_keeps_implicit_transaction_and_physical_identity(
    pair, with_cte
):
    native, target = pair
    kind = "meeting_alignment_jobs"
    values = base(native, kind) | {"legacy_key": "901"}
    native_insert(native, kind, values)
    target_insert(target, kind, values)
    for db in (native, target):
        db.commit()
    prefix = "with candidates as(select id from tasks where id=1) " if with_cte else ""
    where = " where id in(select id from candidates)" if with_cte else " where id=1"
    a = native.execute(
        prefix + "update tasks set status='processing'" + where + " returning *"
    )
    left = a.fetchall()
    parts = task8_set_parts(kind, {"status": "processing"})
    b = target.execute(
        prefix
        + "update tasks set "
        + parts.sql
        + where
        + " returning "
        + task8_meeting_returning(),
        parts.parameters,
    )
    right = b.fetchall()
    assert len(left) == len(right) == 1
    assert (
        (a.rowcount, native.in_transaction)
        == (b.rowcount, target.in_transaction)
        == ((-1, False) if with_cte else (1, True))
    )
    for row in (left[0], right[0]):
        assert (row["id"], row["legacy_key"], row["status"]) == (1, "901", "processing")
    for name in right[0].keys():
        assert (type(left[0][name]), left[0][name]) == (
            type(right[0][name]),
            right[0][name],
        )
    for db in (native, target):
        db.rollback()
    rows = compare(native, target, kind)
    assert rows[0]["status"] == ("processing" if with_cte else "waiting")


@pytest.mark.parametrize(
    "kind,keys",
    (
        ("reply_tasks", ("channel", "conversation_id", "trigger_message_id")),
        ("work_summary_inputs", ("source_type", "source_ref")),
        ("okr_review_requests", ("conversation_id", "trigger_message_id")),
    ),
)
def test_native_upsert_copies_encoded_excluded_values_and_condition(pair, kind, keys):
    native, target = pair
    values = base(native, kind) | {"status": "failed"}
    native_insert(native, kind, values)
    target_insert(target, kind, values)
    for status, expected in (("pending", 1), ("done", 0)):
        data = values | {"error": b"excluded-blob", "status": status}
        source_tail = (
            " on conflict("
            + ",".join(keys)
            + ") where record_type="
            + repr(kind)
            + " do update set error=excluded.error,status=excluded.status where tasks.status='failed'"
        )
        a = native_insert(native, kind, data, tail=source_tail)
        parts = task8_set_parts(
            kind,
            {},
            update_sql={
                "error": "excluded." + task20_column(kind, "error"),
                "status": "excluded." + task20_column(kind, "status"),
            },
        )
        target_tail = (
            " on conflict("
            + ",".join(task20_column(kind, f) for f in keys)
            + ") where record_type="
            + repr(kind)
            + " do update set "
            + parts.sql
            + " where "
            + task20_column(kind, "status")
            + "='failed'"
        )
        b = target_insert(
            target, kind, data, tail=target_tail, parameters=parts.parameters
        )
        assert a.rowcount == b.rowcount == expected
        compare(native, target, kind)
    next_values = values | {keys[-1]: "next-key"}
    a = native_insert(native, kind, next_values)
    b = target_insert(target, kind, next_values)
    assert a.lastrowid == b.lastrowid == 4
    compare(native, target, kind)


def test_update_from_keeps_receipt_rhs_in_native_statement(pair):
    native, target = pair
    kind = "reply_tasks"
    for db, insert in ((native, native_insert), (target, target_insert)):
        insert(db, kind, base(native, kind))
        db.execute("create table receipts(task_id integer,outcome text)")
        db.execute("insert into receipts values(1,'done')")
    a = native.execute(
        "update tasks as reply_tasks set status=receipts.outcome from receipts where reply_tasks.id=receipts.task_id returning id"
    ).fetchall()
    parts = task8_set_parts(
        kind,
        {},
        update_sql={"status": "receipts.outcome"},
        payload_source="reply_tasks.payload_json",
    )
    b = target.execute(
        "update tasks as reply_tasks set "
        + parts.sql
        + " from receipts where reply_tasks.id=receipts.task_id returning id",
        parts.parameters,
    ).fetchall()
    assert [tuple(r) for r in a] == [tuple(r) for r in b] == [(1,)]
    compare(native, target, kind)


def test_parameter_order_across_bound_expression_and_structural_fields(pair):
    native, target = pair
    kind = "reply_tasks"
    data = base(native, kind)
    original = data.pop("error")
    parts = task8_insert_parts(
        kind,
        data,
        insert_sql={"error": "?"},
        insert_parameters=("insert-error",),
    )
    target.execute(
        "insert into tasks(" + parts.columns + ") values(" + parts.values + ")",
        parts.parameters,
    )
    native_insert(native, kind, data | {"error": "insert-error"})
    parts = task8_set_parts(
        kind,
        {"error": "bound-error", "current_input_id": None},
        update_sql={"recovery_code": "?"},
        update_parameters=("expression-code",),
        structural_sql={"claimed_input_id": "?"},
        structural_parameters=(None,),
    )
    target.execute("update tasks set " + parts.sql + " where id=1", parts.parameters)
    native.execute(
        "update tasks set error=?,current_input_id=?,recovery_code=?,claimed_input_id=? where id=1",
        ("bound-error", None, "expression-code", None),
    )
    compare(native, target, kind)
    assert original == ""


@pytest.mark.parametrize(
    "value", (1.2345678901234567, 1.1, 5e-324, float("inf"), float("-inf"))
)
def test_sql_arithmetic_keeps_exact_native_scalar(pair, value):
    native, target = pair
    kind = "reply_tasks"
    data = base(native, kind) | {"attempts": value}
    native_insert(native, kind, data)
    target_insert(target, kind, data)
    native.execute("update tasks set attempts=attempts+1 where id=1")
    parts = task8_set_parts(
        kind,
        {},
        update_sql={"attempts": task20_column(kind, "attempts") + "+1"},
    )
    target.execute("update tasks set " + parts.sql + " where id=1", parts.parameters)
    compare(native, target, kind)


def test_scalar_expression_is_lazy_and_evaluated_once(pair):
    native, target = pair
    kind = "reply_tasks"
    for db, insert in ((native, native_insert), (target, target_insert)):
        insert(db, kind, base(native, kind))
    counts = {"native": 0, "target": 0}

    def tick(side):
        counts[side] += 1
        return 1.2345678901234567

    def forbidden():
        raise AssertionError("unselected CASE branch was evaluated")

    native.create_function("tick", 0, lambda: tick("native"))
    target.create_function("tick", 0, lambda: tick("target"))
    for db in (native, target):
        db.create_function("forbidden", 0, forbidden)
    expression = "case when id=1 then tick() else forbidden() end"
    native.execute("update tasks set attempts=" + expression + " where id=1")
    parts = task8_set_parts(kind, {}, update_sql={"attempts": expression})
    target.execute("update tasks set " + parts.sql + " where id=1", parts.parameters)
    assert counts == {"native": 1, "target": 1}
    compare(native, target, kind)


def test_scalar_codec_exact_binary_values_and_registration_transaction(pair):
    from app.action_email_definitions import _encoded_value
    import math
    import random
    import struct

    native, target = pair
    original_transaction = target.in_transaction
    register_task8_functions(target)
    assert target.in_transaction == original_transaction
    target.execute("create table caller_marker(value)")
    target.execute("insert into caller_marker values(1)")
    register_task8_functions(target)
    assert target.in_transaction
    target.rollback()
    assert target.execute("select count(*) from caller_marker").fetchone()[0] == 0
    rng = random.Random(20261016)
    scalars = [
        None,
        0,
        2**63 - 1,
        -(2**63),
        "nul\x00text",
        b"\x00\xff",
        0.0,
        -0.0,
        5e-324,
        float.fromhex("0x1.fffffffffffffp+1023"),
        float("inf"),
        float("-inf"),
    ]
    for _ in range(512):
        value = struct.unpack("!d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        if math.isfinite(value):
            scalars.append(value)
    decoded = _encoded_value("j", "$.value")
    query = "select " + decoded + " from(select ? as j)"
    for value in scalars:
        encoded = target.execute(
            "select json_object('value',json(ceo_native_json(?)))", (value,)
        ).fetchone()[0]
        actual = target.execute(query, (encoded,)).fetchone()[0]
        assert type(actual) is type(value)
        if isinstance(value, float):
            assert actual.hex() == value.hex()
        else:
            assert actual == value


def test_native_bulk_abort_rolls_back_preceding_row_updates(pair):
    native, target = pair
    kind = "reply_tasks"
    for db, insert in ((native, native_insert), (target, target_insert)):
        for number in (1, 2):
            insert(
                db,
                kind,
                base(native, kind) | {"trigger_message_id": "trigger-" + str(number)},
            )
    for db in (native, target):
        with pytest.raises(sqlite3.IntegrityError) as error:
            if db is native:
                db.execute(
                    "update tasks set attempts=attempts+1,error=case when id=2 then null else 'updated' end where id>0"
                )
            else:
                parts = task8_set_parts(
                    kind,
                    {},
                    update_sql={
                        "attempts": task20_column(kind, "attempts") + "+1",
                        "error": "case when id=2 then null else 'updated' end",
                    },
                )
                db.execute(
                    "update tasks set " + parts.sql + " where id>0", parts.parameters
                )
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_CHECK
        assert db.in_transaction
    rows = compare(native, target, kind)
    assert [(r["attempts"], r["error"]) for r in rows if r["id"] > 0] == [
        (0, ""),
        (0, ""),
    ]


def test_insert_structural_and_scalar_expression_binding_order(pair):
    native, target = pair
    kind = "reply_task_inputs"
    values = base(native, kind)
    values.pop("parent_id")
    values.pop("trigger_text")
    values["legacy_key"] = "41"
    native_insert(
        native, kind, values | {"parent_id": -11, "trigger_text": b"\x00\xff"}
    )
    target_insert(
        target,
        kind,
        values,
        structural_sql={"parent_id": "?"},
        structural_parameters=(-11,),
        insert_sql={"trigger_text": "?"},
        insert_parameters=(b"\x00\xff",),
    )
    compare(native, target, kind)


def test_work_summary_upsert_copies_business_leaf_not_container(pair):
    native, target = pair
    kind = "work_summary_inputs"
    values = base(native, kind) | {"payload_json": '{"old":"body"}'}
    native_insert(native, kind, values)
    target_insert(target, kind, values)
    revised = values | {"payload_json": '{"new":"body"}'}
    native_insert(
        native,
        kind,
        revised,
        tail=" on conflict(source_type,source_ref) where record_type='work_summary_inputs' do update set payload_json=excluded.payload_json",
    )
    parts = task8_set_parts(
        kind,
        {},
        update_sql={"payload_json": "excluded." + task20_column(kind, "payload_json")},
    )
    tail = (
        " on conflict("
        + task20_column(kind, "source_type")
        + ","
        + task20_column(kind, "source_ref")
        + ") where record_type='work_summary_inputs' do update set "
        + parts.sql
    )
    target_insert(target, kind, revised, tail=tail, parameters=parts.parameters)
    row = compare(native, target, kind)[0]
    assert row["payload_json"] == '{"new":"body"}'
    outer = TASK20_LAYOUTS[kind].unpack(
        target.execute("select json(payload_json) from tasks where id=1").fetchone()[0]
    )
    assert outer["payload_json"] == '{"new":"body"}'
