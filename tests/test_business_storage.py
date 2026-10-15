"""Compare unselected four-kind Business storage to accepted native SQLite SQL."""

from contextlib import ExitStack, closing
import json
from pathlib import Path
import sqlite3

import pytest

from app.business_definitions import (
    BUSINESS_FIELDS, BUSINESS_FRESH_PROJECTIONS, BUSINESS_PROJECTIONS,
    BUSINESS_SOURCE_TABLES,
)
from app.business_storage import (
    create_business_schema, delete_business, insert_business, update_business,
)

CAPTURE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json").read_text())
KINDS = ("task", "project", "anchor", "project_context_revision")
BASE = {
    "task": {"title": "synthetic", "stage": "candidate"},
    "project": {"canonical_anchor_id": -16, "title": "synthetic", "registry_source": "synthetic"},
    "anchor": {"anchor_type": "project", "anchor_ref": "new", "title": "synthetic"},
    "project_context_revision": {"project_id": -15, "context_json": "not JSON", "evidence_json": "not JSON"},
}
INVALID = (
    ("task", {"title": "\u00a0"}), ("task", {"stage": "unknown"}),
    ("task", {"status": "unknown"}), ("task", {"commitment_status": "unknown"}),
    ("task", {"stage": "formal", "formal_basis": "unknown"}),
    ("task", {"business_relevance": "unknown2"}), ("task", {"owner_evidence_json": "[]"}),
    ("task", {"missing_evidence_json": "{}"}), ("task", {"origin": "unknown"}),
    ("task", {"suggestion_json": "[]"}), ("task", {"stage": "formal"}),
    ("task", {"status": "merged"}), ("task", {"id": 8, "status": "merged", "merged_into_task_id": 8}),
    ("project", {"anchor_type": "customer"}), ("project", {"title": "\u3000"}),
    ("project", {"registry_source": "\t"}), ("project", {"crm_customer_lookup_status": "unknown"}),
    ("project", {"crm_customer_candidates_json": "{}"}), ("project", {"crm_customer_evidence_json": "[]"}),
    ("anchor", {"anchor_type": "unknown"}), ("anchor", {"anchor_ref": "\n"}),
    ("anchor", {"title": "\u2000"}), ("anchor", {"active": 2}),
)
NOT_NULL = tuple((kind, c["name"]) for kind, table in BUSINESS_SOURCE_TABLES.items()
                 for c in CAPTURE["actual"]["tables"][table]["columns"] if c["notnull"])


@pytest.fixture(params=("actual", "fresh"))
def pair(request):
    with ExitStack() as stack:
        source = stack.enter_context(closing(sqlite3.connect(":memory:")))
        target = stack.enter_context(closing(sqlite3.connect(":memory:")))
        for db in (source, target):
            db.execute("pragma foreign_keys=on")
        tables = CAPTURE[request.param]["tables"]
        for table in tables.values():
            source.execute(table["sql"])
        for table in tables.values():
            for index in reversed(table["indexes"]):
                if index["sql"]:
                    source.execute(index["sql"])
            for trigger in table["triggers"]:
                source.execute(trigger["sql"])
        target.execute("begin")
        create_business_schema(target)
        for db in (source, target):
            db.commit()
            db.execute("begin")
        yield source, target, request.param


def native_insert(db, kind, values, *, ignore=False):
    table = BUSINESS_SOURCE_TABLES[kind]
    cursor = db.execute("insert " + ("or ignore " if ignore else "") + "into " + table +
                        "(" + ",".join(values) + ") values(" + ",".join("?" for _ in values) +
                        ")", tuple(values.values()))
    return cursor.lastrowid if cursor.rowcount else None, cursor.rowcount


def both_insert(pair, kind, values, *, ignore=False, defaults=False):
    if not defaults:
        times = {c["name"]: "stamp" for c in CAPTURE["actual"]["tables"][BUSINESS_SOURCE_TABLES[kind]]["columns"] if c["dflt_value"] == "current_timestamp"}
        values = times | values
    native = native_insert(pair[0], kind, values, ignore=ignore)
    target = insert_business(pair[1], kind, values, ignore=ignore)
    assert (target.lastrowid, target.rowcount) == native
    return native[0]


def native_update(db, kind, identity, values, *, ignore=False):
    return db.execute("update " + ("or ignore " if ignore else "") + BUSINESS_SOURCE_TABLES[kind] +
                      " set " + ",".join(f"{f}=?" for f in values) + " where id=?",
                      (*values.values(), identity)).rowcount


def both_update(pair, kind, identity, values, *, ignore=False):
    assert update_business(pair[1], kind, identity, values, ignore=ignore) == native_update(
        pair[0], kind, identity, values, ignore=ignore)


def rows(pair, target, kind):
    db = pair[1 if target else 0]
    projection = BUSINESS_FRESH_PROJECTIONS if pair[2] == "fresh" else BUSINESS_PROJECTIONS
    sql = projection[kind] if target else "select * from " + BUSINESS_SOURCE_TABLES[kind]
    cursor = db.execute("select * from (" + sql + ") order by id")
    return tuple(c[0] for c in cursor.description), [tuple(r) for r in cursor]


def waters(pair, target):
    return {kind: (row[0] if (row := pair[1 if target else 0].execute(
        "select seq from sqlite_sequence where name=?",
        (("business:" + kind) if target else table,)).fetchone()) else None)
        for kind, table in BUSINESS_SOURCE_TABLES.items()}


def assert_same(pair):
    for kind in KINDS:
        assert rows(pair, False, kind) == rows(pair, True, kind)
    assert waters(pair, False) == waters(pair, True)
    assert pair[0].execute("pragma foreign_key_check").fetchall() == []
    assert pair[1].execute("pragma foreign_key_check").fetchall() == []


def seed(pair):
    both_insert(pair, "task", {"id": -11, **BASE["task"]})
    both_insert(pair, "anchor", {"id": -14, **BASE["anchor"], "anchor_ref": "seed"})
    both_insert(pair, "anchor", {"id": -16, **BASE["anchor"], "anchor_ref": "unused"})
    both_insert(pair, "project", {"id": -15, **BASE["project"], "canonical_anchor_id": -14})
    for db in pair[:2]:
        db.commit()
        db.execute("begin")


def failure(call):
    with pytest.raises(sqlite3.IntegrityError) as error:
        call()
    return error.value.sqlite_errorname


def test_exact_columns_defaults_and_source_ordinals(pair):
    assert tuple(len(BUSINESS_FIELDS[k]) for k in KINDS) == (19, 12, 6, 5)
    assert len(INVALID) == 23
    assert all(v is None for v in waters(pair, True).values())
    seed(pair)
    both_insert(pair, "task", BASE["task"], defaults=True)
    both_insert(pair, "anchor", BASE["anchor"] | {"anchor_ref": "defaults"}, defaults=True)
    both_insert(pair, "project", BASE["project"], defaults=True)
    both_insert(pair, "project_context_revision", BASE["project_context_revision"], defaults=True)
    for kind in KINDS:
        expected = tuple(c["name"] for c in CAPTURE[pair[2]]["tables"][BUSINESS_SOURCE_TABLES[kind]]["columns"])
        assert rows(pair, True, kind)[0] == expected
    # Remove independently evaluated wall-clock seconds from cross-connection comparison.
    for kind in KINDS:
        fields, native = rows(pair, False, kind)
        _, target = rows(pair, True, kind)
        time_fields = {f for f, default in ((c["name"], c["dflt_value"]) for c in CAPTURE[pair[2]]["tables"][BUSINESS_SOURCE_TABLES[kind]]["columns"]) if default == "current_timestamp"}
        for row in native + target:
            times = [row[fields.index(f)] for f in time_fields]
            assert len(set(times)) == 1
            assert all(isinstance(t, str) and (len(t) == 19 or t == "stamp") for t in times)
        keep = [i for i, f in enumerate(fields) if f not in time_fields]
        assert [tuple(r[i] for i in keep) for r in native] == [tuple(r[i] for i in keep) for r in target]


@pytest.mark.parametrize("kind,overrides", INVALID)
@pytest.mark.parametrize("ignore", (False, True))
def test_all_23_source_checks_and_ignore_order(pair, kind, overrides, ignore):
    seed(pair)
    values = BASE[kind] | overrides
    # Every invalid row also has a missing parent when that kind owns an edge.
    if kind == "project":
        values["canonical_anchor_id"] = 999
    if ignore:
        assert both_insert(pair, kind, values, ignore=True) is None
    else:
        assert failure(lambda: native_insert(pair[0], kind, values)) == failure(
            lambda: insert_business(pair[1], kind, values)) == "SQLITE_CONSTRAINT_CHECK"
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind,field", NOT_NULL)
@pytest.mark.parametrize("ignore", (False, True))
def test_every_native_not_null_and_ignore(pair, kind, field, ignore):
    seed(pair)
    values = BASE[kind] | {field: None}
    if ignore:
        assert both_insert(pair, kind, values, ignore=True) is None
    else:
        assert failure(lambda: native_insert(pair[0], kind, values)) == failure(
            lambda: insert_business(pair[1], kind, values)) == "SQLITE_CONSTRAINT_NOTNULL"
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind,overrides", (
    ("task", {"description": 1.25, "owner_name": b"\x00\xff", "deadline_at": float("inf")}),
    ("task", {"description": True, "owner_user_id": memoryview(b"A"), "deadline_at": float("-inf")}),
    ("task", {"description": " a\n\u0000b ", "owner_evidence_json": b'{"x": 1}', "missing_evidence_json": b'[1]'}),
    ("task", {"owner_evidence_json": b"\xff"}),
    ("project", {"crm_customer_id": b"\x00\xff", "crm_customer_name": 42, "crm_customer_evidence_json": '{ "b":2, "a":1 }'}),
    ("anchor", {"anchor_ref": bytearray(b"blob"), "active": "1e0"}),
    ("project_context_revision", {"context_json": b"\x00\xff", "evidence_json": float("inf")}),
    ("project_context_revision", {"context_json": "{ bad JSON", "evidence_json": "  not JSON \n"}),
))
def test_native_storage_classes_exact_text_and_affinity(pair, kind, overrides):
    seed(pair)
    values = BASE[kind] | overrides | {"created_at": "stamp", **({"updated_at": "stamp", "last_activity_at": "stamp"} if kind == "task" else {})}
    def snapshot():
        return (
            tuple(rows(pair, target, current_kind) for target in (False, True) for current_kind in KINDS),
            waters(pair, False), waters(pair, True),
        )

    before = snapshot()
    try:
        native = native_insert(pair[0], kind, values)
    except sqlite3.IntegrityError as error:
        assert failure(lambda: insert_business(pair[1], kind, values)) == error.sqlite_errorname
        assert snapshot() == before
        return
    target_result = insert_business(pair[1], kind, values)
    assert (target_result.lastrowid, target_result.rowcount) == native
    fields, native = rows(pair, False, kind)
    _, target = rows(pair, True, kind)
    assert native[-1] == target[-1]
    for field in overrides:
        assert type(native[-1][fields.index(field)]) is type(target[-1][fields.index(field)])
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("identity", (-9, 0, "2.0", 3.0, True))
def test_signed_explicit_public_ids_and_ordering(pair, kind, identity):
    seed(pair)
    both_insert(pair, kind, BASE[kind] | {"id": identity, "created_at": "stamp"})
    assert waters(pair, False) == waters(pair, True)
    assert rows(pair, False, kind) == rows(pair, True, kind)
    assert [r[0] for r in rows(pair, True, kind)[1]] == sorted(r[0] for r in rows(pair, True, kind)[1])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("identity", ("junk", b"1", 1.5))
def test_native_integer_pk_rejection(pair, kind, identity):
    seed(pair)
    assert failure(lambda: native_insert(pair[0], kind, BASE[kind] | {"id": identity})) == failure(
        lambda: insert_business(pair[1], kind, BASE[kind] | {"id": identity})) == "SQLITE_MISMATCH"


@pytest.mark.parametrize("kind", KINDS)
def test_independent_waters_deleted_high_update_and_ignore(pair, kind):
    seed(pair)
    if kind == "project":
        both_insert(pair, "anchor", BASE["anchor"] | {"id": -20, "anchor_ref": "second"})
    base = BASE[kind] | ({"canonical_anchor_id": -20} if kind == "project" else {})
    both_insert(pair, kind, base | {"id": 1000})
    before_other = {k: v for k, v in waters(pair, True).items() if k != kind}
    assert delete_business(pair[1], kind, 1000) == pair[0].execute(
        "delete from " + BUSINESS_SOURCE_TABLES[kind] + " where id=1000").rowcount == 1
    assert both_insert(pair, kind, base) == 1001
    both_update(pair, kind, 1001, {"id": 3000})
    assert waters(pair, True)[kind] == 1001
    assert delete_business(pair[1], kind, 3000) == pair[0].execute(
        "delete from " + BUSINESS_SOURCE_TABLES[kind] + " where id=3000").rowcount == 1
    assert both_insert(pair, kind, base) == 1002
    # Explicit duplicate PK is ignored, advances no further than that supplied key.
    assert both_insert(pair, kind, base | {"id": 1002}, ignore=True) is None
    assert {k: v for k, v in waters(pair, True).items() if k != kind} == before_other
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind,values", (
    ("task", {"status": "merged", "merged_into_task_id": 999}),
    ("project", {"canonical_anchor_id": 999}),
    ("project_context_revision", {"project_id": 999}),
))
@pytest.mark.parametrize("ignore", (False, True))
def test_real_fk_after_row_checks_and_ignore(pair, kind, values, ignore):
    seed(pair)
    before = waters(pair, True)
    assert failure(lambda: native_insert(pair[0], kind, BASE[kind] | values, ignore=ignore)) == failure(
        lambda: insert_business(pair[1], kind, BASE[kind] | values, ignore=ignore)) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert waters(pair, False) == waters(pair, True) == before


def test_binary_unique_and_typed_composite_anchor_parent(pair):
    seed(pair)
    assert failure(lambda: native_insert(pair[0], "anchor", BASE["anchor"] | {"anchor_ref": "seed"})) == failure(
        lambda: insert_business(pair[1], "anchor", BASE["anchor"] | {"anchor_ref": "seed"})) == "SQLITE_CONSTRAINT_UNIQUE"
    assert both_insert(pair, "anchor", BASE["anchor"] | {"anchor_ref": "seed"}, ignore=True) is None
    both_insert(pair, "anchor", BASE["anchor"] | {"anchor_ref": "SEED"})
    customer = both_insert(pair, "anchor", BASE["anchor"] | {"anchor_type": "customer", "anchor_ref": "other"})
    assert failure(lambda: native_insert(pair[0], "project", BASE["project"] | {"canonical_anchor_id": customer})) == failure(
        lambda: insert_business(pair[1], "project", BASE["project"] | {"canonical_anchor_id": customer})) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert failure(lambda: native_insert(pair[0], "project", BASE["project"] | {"canonical_anchor_id": -14})) == failure(
        lambda: insert_business(pair[1], "project", BASE["project"] | {"canonical_anchor_id": -14})) == "SQLITE_CONSTRAINT_UNIQUE"


def test_public_parent_updates_delete_and_native_statement_end_fk(pair):
    seed(pair)
    both_insert(pair, "task", BASE["task"] | {"id": 10})
    both_insert(pair, "task", BASE["task"] | {"id": 11, "status": "merged", "merged_into_task_id": 10})
    both_insert(pair, "project_context_revision", BASE["project_context_revision"])
    for kind, identity in (("task", 10), ("anchor", -14), ("project", -15)):
        for ignore in (False, True):
            assert failure(lambda: native_update(pair[0], kind, identity, {"id": 88}, ignore=ignore)) == failure(
                lambda: update_business(pair[1], kind, identity, {"id": 88}, ignore=ignore)) == "SQLITE_CONSTRAINT_FOREIGNKEY"
        assert failure(lambda: pair[0].execute("delete from " + BUSINESS_SOURCE_TABLES[kind] + " where id=?", (identity,))) == failure(
            lambda: delete_business(pair[1], kind, identity)) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert failure(lambda: native_update(pair[0], "anchor", -14, {"anchor_type": "matter"})) == failure(
        lambda: update_business(pair[1], "anchor", -14, {"anchor_type": "matter"})) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    # One native Task UPDATE may coordinate a parent's public key and child FK.
    pair[0].execute("update business_tasks set id=case id when 10 then 20 else id end, merged_into_task_id=case id when 11 then 20 else merged_into_task_id end where id in (10,11)")
    pair[1].execute("update business set legacy_key=case public_id when 10 then '20' else legacy_key end, payload_json=case public_id when 11 then json_set(payload_json,'$.merged_into_task_id',20) else payload_json end where record_type='task' and public_id in (10,11)")
    assert rows(pair, False, "task") == rows(pair, True, "task")
    assert pair[1].execute("pragma foreign_key_check").fetchall() == []


def test_late_fk_failure_caller_rolls_back_rows_and_all_four_waters(pair):
    seed(pair)
    before_rows = {k: rows(pair, True, k) for k in KINDS}
    before_water = waters(pair, True)
    with pytest.raises(sqlite3.IntegrityError):
        with pair[0], pair[1]:
            both_insert(pair, "task", BASE["task"])
            anchor = both_insert(pair, "anchor", BASE["anchor"] | {"anchor_ref": "late"})
            project = both_insert(pair, "project", BASE["project"] | {"canonical_anchor_id": anchor})
            both_insert(pair, "project_context_revision", BASE["project_context_revision"] | {"project_id": project})
            # Execute independently so both connections reach the same late failure.
            assert failure(lambda: native_insert(pair[0], "project_context_revision", BASE["project_context_revision"] | {"project_id": 999})) == "SQLITE_CONSTRAINT_FOREIGNKEY"
            insert_business(pair[1], "project_context_revision", BASE["project_context_revision"] | {"project_id": 999})
    assert {k: rows(pair, True, k) for k in KINDS} == before_rows
    assert waters(pair, False) == waters(pair, True) == before_water
    assert not pair[0].in_transaction and not pair[1].in_transaction


@pytest.mark.parametrize("kind", KINDS)
def test_native_auto_exhaustion_rolls_back_plain_source_caller_transaction(pair, kind):
    seed(pair)
    base = dict(BASE[kind])
    if kind == "project":
        both_insert(pair, "anchor", BASE["anchor"] | {"id": -20, "anchor_ref": "limit"})
        base["canonical_anchor_id"] = -20
    both_insert(pair, kind, base | {"id": 2**63 - 1})
    for db in pair[:2]:
        db.commit()
        db.execute("begin")
    before = waters(pair, True)
    both_insert(pair, "task", BASE["task"] | {"id": -99})
    for call in (lambda: native_insert(pair[0], kind, base), lambda: insert_business(pair[1], kind, base)):
        with pytest.raises(sqlite3.OperationalError) as error:
            call()
        assert error.value.sqlite_errorname == "SQLITE_FULL"
    assert waters(pair, False) == waters(pair, True) == before
    assert not pair[0].in_transaction and not pair[1].in_transaction
    assert pair[1].execute("select count(*) from business where record_type='task' and public_id=-99").fetchone()[0] == 0


def test_no_self_commit_read_mutation_or_schema_selection(pair):
    seed(pair)
    both_update(pair, "task", -11, {"description": "changed", "owner_name": b"new"})
    both_update(pair, "task", 999, {"title": "absent"})
    assert delete_business(pair[1], "task", 999) == 0
    both_insert(pair, "project_context_revision", BASE["project_context_revision"])
    before = pair[1].total_changes
    for kind in KINDS:
        rows(pair, True, kind)
    assert pair[1].total_changes == before
    assert pair[1].in_transaction
    for db in pair[:2]:
        db.rollback()
    assert rows(pair, False, "task") == rows(pair, True, "task")
    assert rows(pair, True, "project_context_revision")[1] == []
    with pytest.raises(sqlite3.ProgrammingError, match="caller transaction"):
        insert_business(pair[1], "task", BASE["task"])
    assert pair[1].execute("select name from sqlite_schema where name like 'business_%' and type='table'").fetchall() == []


def test_installer_ddl_is_caller_transactional():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("begin")
        create_business_schema(db)
        assert db.in_transaction
        db.rollback()
        assert db.execute("select name from sqlite_schema where name='business'").fetchone() is None


def test_retained_children_are_future_release_closure_only():
    for variant in ("actual", "fresh"):
        source = CAPTURE[variant]
        selected = set(BUSINESS_SOURCE_TABLES.values())
        incoming = [r for r in source["incoming_foreign_keys"] if r["table"] in selected and r["child_table"] not in selected]
        assert len({(r["child_table"], r["id"]) for r in incoming}) == 19
        assert len({r["child_table"] for r in incoming}) == 15
        assert all(r["on_delete"] == r["on_update"] == "NO ACTION" for r in incoming)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("returning", (False, True))
def test_native_exhaustion_statement_shape_control(pair, kind, returning):
    seed(pair)
    base = dict(BASE[kind])
    native_insert(pair[0], kind, base | {"id": 2**63 - 1})
    pair[0].commit()
    pair[0].execute("begin")
    native_insert(pair[0], "task", BASE["task"] | {"id": -99})
    sql = "insert into " + BUSINESS_SOURCE_TABLES[kind] + "(" + ",".join(base) + ") values(" + ",".join("?" for _ in base) + ")"
    with pytest.raises(sqlite3.OperationalError) as error:
        pair[0].execute(sql + (" returning id" if returning else ""), tuple(base.values())).fetchall()
    assert error.value.sqlite_errorname == "SQLITE_FULL"
    assert pair[0].in_transaction is returning
    assert pair[0].execute("select count(*) from business_tasks where id=-99").fetchone()[0] == int(returning)


def test_omitted_task_timestamps_share_statement_time(pair):
    # Synthetic clock changes at every SQLite statement, never by wall clock.
    # Native INSERT is the control; three per-field default SELECTs would fail.
    for db in pair[:2]:
        ticks = [0]
        def trace(sql, ticks=ticks):
            ticks[0] += 1
        db.set_trace_callback(trace)
        db.create_function("current_timestamp", 0, lambda ticks=ticks: "clock-" + str(ticks[0]))
    both_insert(pair, "task", BASE["task"], defaults=True)
    for target in (False, True):
        fields, result = rows(pair, target, "task")
        assert len({result[0][fields.index(f)] for f in ("created_at", "updated_at", "last_activity_at")}) == 1


def test_structural_physical_public_coupling_cannot_be_bypassed(pair):
    seed(pair)
    task_physical = pair[1].execute("select id from business where record_type='task' and public_id=-11").fetchone()[0]
    project_physical = pair[1].execute("select id from business where record_type='project' and public_id=-15").fetchone()[0]
    child = both_insert(pair, "task", BASE["task"] | {"status": "merged", "merged_into_task_id": -11})
    assert failure(lambda: pair[1].execute("update business set merged_task_ref=? where record_type='task' and public_id=?", (project_physical, child))) == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert failure(lambda: pair[1].execute("update business set merged_task_ref=? where record_type='task' and public_id=-11", (task_physical,))) == "SQLITE_CONSTRAINT_CHECK"
    assert pair[1].execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("identity", (None, float("nan")))
def test_null_and_nan_public_ids_allocate_natively(pair, kind, identity):
    seed(pair)
    assert both_insert(pair, kind, BASE[kind] | {"id": identity}) == 1
    assert rows(pair, False, kind) == rows(pair, True, kind)
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("identity", (2**63, -(2**63) - 1))
def test_bound_integer_overflow_is_native_and_keeps_transaction(pair, kind, identity):
    seed(pair)
    for call in (lambda: native_insert(pair[0], kind, BASE[kind] | {"id": identity}),
                 lambda: insert_business(pair[1], kind, BASE[kind] | {"id": identity})):
        with pytest.raises(OverflowError):
            call()
    assert pair[0].in_transaction and pair[1].in_transaction
    assert waters(pair, False) == waters(pair, True)


@pytest.mark.parametrize("kind", KINDS)
def test_id_update_affinity_null_rejection_and_ignored_not_null(pair, kind):
    seed(pair)
    both_insert(pair, kind, BASE[kind] | {"id": 7})
    both_update(pair, kind, 7, {"id": "8.0"})
    assert rows(pair, False, kind) == rows(pair, True, kind)
    assert failure(lambda: native_update(pair[0], kind, 8, {"id": None})) == failure(
        lambda: update_business(pair[1], kind, 8, {"id": None})) == "SQLITE_MISMATCH"
    field = "context_json" if kind == "project_context_revision" else "title"
    both_update(pair, kind, 8, {field: None}, ignore=True)
    assert rows(pair, False, kind) == rows(pair, True, kind)
    assert waters(pair, False) == waters(pair, True)
