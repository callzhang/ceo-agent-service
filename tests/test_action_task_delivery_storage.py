"""The four disabled task-delivery Action kinds preserve source contracts."""

import sqlite3
from contextlib import closing
import json
from pathlib import Path

import pytest

from tests.support.current_inventory import CURRENT_TABLES

from app import action_storage
from app.action_followup_sync_storage import LINK_TRIGGER_SQL
from app.action_task_delivery_definitions import TASK_DELIVERY_PARENTS

SOURCE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                     "2026-10-17-task-delivery-source-metadata.json").read_text())


def _insert_task_delivery_text(db, kind, values, *, ignore=False):
    """Write through the frozen object-payload Actions layout used by these tests."""
    action_storage._task_delivery_fields(kind, values)
    parent, parent_column, expected_parent = TASK_DELIVERY_PARENTS[kind]
    identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
    if identity == "job_id" and action_storage._source_bound_null(values.get(identity)):
        raise ValueError("claim insert requires explicit non-NULL job_id")

    def operation():
        action_storage._email_lock(db, tuple(values.values()))
        auto_id = identity != "job_id" and action_storage._source_bound_null(values.get(identity))
        high = action_storage._autoincrement_high_water(db, kind) if auto_id else 0
        if auto_id and high == 2**63 - 1:
            parent_id = values.get(parent)
            if db.execute(
                "select 1 from main.tasks where id=? and record_type<>? limit 1",
                (parent_id, expected_parent),
            ).fetchone():
                # Let the frozen table's own BEFORE trigger preserve native
                # wrong-parent error order ahead of allocator exhaustion.
                db.execute(
                    f"insert into actions(record_type,legacy_key,payload_json,{parent_column}) "
                    "values(?,?,?,?)",
                    (kind, str(high), "{}", parent_id),
                )
            action_storage._raise_autoincrement_full()
        # Keep production validation/defaults/allocator/payload construction,
        # then translate the resulting vector into the historical object row.
        columns, arguments = action_storage.task_delivery_insert_parts(db, kind, values)
        packed = db.execute("select json(?)", (arguments[2],)).fetchone()[0]
        layout = action_storage._action_layout(kind)
        vector = json.loads(packed)
        payload = json.dumps(dict(zip(layout.fields, vector, strict=True)),
                             ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        cursor = db.execute(
            f"insert {'or ignore ' if ignore else ''}into actions(record_type,legacy_key,payload_json,{parent_column}) "
            "values(?,?,?,?) returning cast(legacy_key as integer)",
            (kind, str(arguments[1]), payload, arguments[3]),
        )
        row = cursor.fetchone()
        cursor.fetchall()
        return action_storage.ActionWriteResult(row[0] if row else None, cursor.rowcount)

    return action_storage._email_unit(db, "action_task_delivery_insert", operation)


def _task_delivery_set_parts_text(kind, values, *, update_sql=None, update_parameters=()):
    action_storage._task_delivery_fields(kind, values)
    expressions = {} if update_sql is None else update_sql
    action_storage._task_delivery_fields(kind, expressions)
    if not values and not expressions:
        raise ValueError("task delivery update requires fields")
    if set(values) & set(expressions):
        raise ValueError("duplicate task delivery update field")
    parent, target, _ = TASK_DELIVERY_PARENTS[kind]
    identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
    if identity in expressions or parent in expressions:
        raise ValueError("task delivery structural SQL update is unsupported")
    assignments, arguments, paths = [], [], []
    if identity in values:
        assignments.append("legacy_key=?")
        arguments.append(str(action_storage._native_integer_primary_key(values[identity], update=True)))
        if parent == identity:
            assignments.append(target + "=?")
            arguments.append(values[identity])
    if parent != identity and parent in values:
        assignments.append(target + "=?")
        arguments.append(values[parent])
    for field, value in values.items():
        if field not in (identity, parent):
            paths.extend(("$." + field, json.dumps(action_storage._encoded_native_sqlite_value(value),
                                                     ensure_ascii=False, allow_nan=False, separators=(",", ":"))))
    payload_updates = ["?,json(?)" for _ in range(len(paths) // 2)]
    payload_updates.extend(repr("$." + field) + ",json(ceo_native_json(" + expression + "))"
                           for field, expression in expressions.items())
    if payload_updates:
        assignments.append("payload_json=json_set(payload_json," + ",".join(payload_updates) + ")")
        arguments.extend(paths)
        arguments.extend(update_parameters)
    return ",".join(assignments), tuple(arguments)


def _update_task_delivery_text(db, kind, public_id, values, *, where_sql="", parameters=()):
    action_storage._task_delivery_fields(kind, values)

    def operation():
        action_storage._email_lock(db, (*values.values(), public_id, *parameters))
        predicate = "record_type=? and " + action_storage._EMAIL_NUMERIC_SCOPE + " and cast(legacy_key as integer)=?"
        identity_args = [kind, public_id]
        identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
        if where_sql:
            projection = action_storage.task_delivery_projection(
                kind, source_variant="actual" if action_storage._reply_variant_for(db) == "actual_no_memory_fk" else "fresh",
            )
            predicate += (" and cast(legacy_key as integer) in "
                          f"(select {identity} from ({projection}) where {identity}=? and ({where_sql}))")
            identity_args.extend((public_id, *parameters))
        if identity in values and db.execute(
            "select 1 from actions where " + predicate + " limit 1", identity_args,
        ).fetchone() is None:
            return db.execute("update actions set legacy_key=legacy_key where 0")
        assignment, arguments = _task_delivery_set_parts_text(kind, values)
        return db.execute("update actions set " + assignment + " where " + predicate,
                          (*arguments, *identity_args))

    return action_storage._email_unit(db, "action_task_delivery_update", operation)


@pytest.fixture
def _frozen_task_delivery_text_writer(monkeypatch):
    monkeypatch.setattr(action_storage, "insert_task_delivery_action", _insert_task_delivery_text)
    monkeypatch.setattr(action_storage, "task_delivery_set_parts", _task_delivery_set_parts_text)
    monkeypatch.setattr(action_storage, "update_task_delivery_action", _update_task_delivery_text)


def test_fresh_final_schema_failure_does_not_publish_current(tmp_path, monkeypatch):
    from app import current_database_schema, store as store_module

    path = tmp_path / "fresh.sqlite3"
    path.touch()

    def fail_conversion(*_args, **_kwargs):
        raise RuntimeError("conversion interrupted")

    monkeypatch.setattr(current_database_schema, "create_current_database_schema", fail_conversion)
    with pytest.raises(RuntimeError, match="conversion interrupted"):
        store_module.AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "select name from sqlite_schema where type='table' and name in "
            "('meeting_alignment_delivery_claims','meeting_memory_write_events',"
            "'task_memory_write_events','wechat_deliveries')"
        ).fetchall() == []
        assert db.execute(
            "select 1 from sqlite_schema where type='table' and name='state'"
        ).fetchone() is None


@pytest.mark.parametrize("first", ("store", "email"))
def test_fresh_paired_constructors_publish_final_inventory(tmp_path, first):
    from app.store import AutoReplyStore
    from app.email_store import EmailStore

    path = tmp_path / "fresh.sqlite3"
    constructors = {
        "store": AutoReplyStore,
        "email": EmailStore,
    }
    constructors[first](path)
    constructors["email" if first == "store" else "store"](path)
    store = AutoReplyStore(path)
    from app.current_schema_manifest import current_objects, schema_objects

    with store._connect() as db:
        expected_tables = {
            name for kind, name, _table, _sql in current_objects(email_ledger=True)
            if kind == "table"
        }
        actual_tables = {
            name for kind, name, _table, _sql in schema_objects(db)
            if kind == "table"
        }
        assert expected_tables == CURRENT_TABLES
        assert actual_tables == CURRENT_TABLES
        assert schema_objects(db) == current_objects(email_ledger=True)
        assert store._schema_manifest_is_current_in_connection(
            db, include_run_snapshots=True,
        )


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
def test_task_delivery_schema_has_four_typed_kinds_and_source_constraints(variant):
    assert hasattr(action_storage, "task_delivery_projection")
    assert hasattr(action_storage, "TASK_DELIVERY_FIELDS")
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
        db.execute("insert into tasks values(1,'meeting_alignment_jobs'),(2,'reply_tasks')")
        action_storage.create_action_schema(db, include_reply=True, reply_variant=variant,
                             include_email=True, include_followup_sync=True,
                             include_business_sync=True, include_task_delivery=True)
        assert set(action_storage.TASK_DELIVERY_FIELDS) == {
            "meeting_alignment_delivery_claims", "meeting_memory_write_events",
            "task_memory_write_events", "wechat_deliveries",
        }
        projection = action_storage.task_delivery_projection("wechat_deliveries")
        assert "reply_task_id" in projection
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into actions(record_type,legacy_key,payload_json) "
                       "values('meeting_memory_write_events','8','{}')")


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
@pytest.mark.usefixtures("_frozen_task_delivery_text_writer")
def test_task_delivery_set_parts_uses_original_row_values_and_statement_clock(variant):
    from app.task8_storage import register_task8_functions

    with closing(sqlite3.connect(":memory:")) as db:
        register_task8_functions(db)
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
        db.execute("insert into tasks values(1,'meeting_alignment_jobs')")
        action_storage.create_action_schema(
            db, include_reply=True, reply_variant=variant, include_email=True,
            include_followup_sync=True, include_business_sync=True,
            include_task_delivery=True,
        )
        event = action_storage.insert_task_delivery_action(
            db, "meeting_memory_write_events", {
                "meeting_job_id": 1, "payload_json": "{}", "execution_generation": "g1",
            },
        )
        parts, parameters = action_storage.task_delivery_set_parts(
            "meeting_memory_write_events", {"status": "failed"},
            update_sql={"attempts": "td_attempts+1", "updated_at": "current_timestamp"},
        )
        db.execute(
            "update actions set " + parts + " where record_type='meeting_memory_write_events' "
            "and cast(legacy_key as integer)=?", (*parameters, event.lastrowid),
        )
        row = db.execute(
            "select status,attempts,updated_at from (" +
            action_storage.task_delivery_projection("meeting_memory_write_events") +
            ") where id=?", (event.lastrowid,),
        ).fetchone()
        assert row == ("failed", 1, db.execute("select current_timestamp").fetchone()[0])


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
@pytest.mark.usefixtures("_frozen_task_delivery_text_writer")
def test_wechat_allocator_uses_main_sequence_with_unrelated_temp_autoincrement(variant):
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
        db.execute("insert into tasks values(5,'reply_tasks')")
        action_storage.create_action_schema(
            db, include_reply=True, reply_variant=variant, include_email=True,
            include_followup_sync=True, include_business_sync=True,
            include_task_delivery=True,
        )
        values = {"reply_task_id": 5, "account_id": "a", "target_type": "contact",
                  "target_id": "t", "reply_text": "text", "execution_generation": "first"}
        assert action_storage.insert_task_delivery_action(
            db, "wechat_deliveries", {"id": 80, **values},
        ).lastrowid == 80
        ignored = action_storage.insert_task_delivery_action(
            db, "wechat_deliveries", {"id": 80, **values}, ignore=True,
        )
        assert ignored.rowcount == 0
        assert action_storage.delete_task_delivery_action(
            db, "wechat_deliveries", 80,
        ).rowcount == 1
        assert db.execute("select seq from main.sqlite_sequence where name='actions:wechat_deliveries'").fetchone() == (80,)
        db.execute("create temp table unrelated_temp(id integer primary key autoincrement)")
        db.execute("insert into unrelated_temp default values")
        db.execute("delete from unrelated_temp")
        assert db.execute("select seq from temp.sqlite_sequence where name='unrelated_temp'").fetchone() == (1,)
        db.commit()
        db.execute("begin")
        inserted = action_storage.insert_task_delivery_action(
            db, "wechat_deliveries", {**values, "execution_generation": "later"},
        )
        assert inserted.lastrowid == 81
        assert inserted.rowcount == 1
        assert db.in_transaction
        assert db.execute("select seq from main.sqlite_sequence where name='actions:wechat_deliveries'").fetchone() == (81,)
        assert db.execute("select seq from temp.sqlite_sequence where name='unrelated_temp'").fetchone() == (1,)
        db.rollback()
        assert db.execute("select seq from main.sqlite_sequence where name='actions:wechat_deliveries'").fetchone() == (80,)


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
@pytest.mark.usefixtures("_frozen_task_delivery_text_writer")
def test_unmatched_wechat_update_does_not_evaluate_invalid_primary_key(variant):
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
        action_storage.create_action_schema(
            db, include_reply=True, reply_variant=variant, include_email=True,
            include_followup_sync=True, include_business_sync=True,
            include_task_delivery=True,
        )
        result = action_storage.update_task_delivery_action(
            db, "wechat_deliveries", 999, {"id": None},
        )
        assert result.rowcount == 0
        assert db.execute("select count(*) from actions where record_type='wechat_deliveries'").fetchone() == (0,)


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
@pytest.mark.parametrize("job_id", (None, "absent"))
@pytest.mark.usefixtures("_frozen_task_delivery_text_writer")
def test_claim_writer_requires_explicit_non_null_job_id(variant, job_id):
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
        db.execute("insert into tasks values(1,'reply_tasks')")
        action_storage.create_action_schema(
            db, include_reply=True, reply_variant=variant, include_email=True,
            include_followup_sync=True, include_business_sync=True,
            include_task_delivery=True,
        )
        values = {"claim_token": "token"}
        if job_id != "absent":
            values["job_id"] = job_id
        with pytest.raises(ValueError, match="explicit non-NULL job_id"):
            action_storage.insert_task_delivery_action(
                db, "meeting_alignment_delivery_claims", values,
            )
        assert db.execute("select count(*) from actions where record_type='meeting_alignment_delivery_claims'").fetchone() == (0,)


@pytest.mark.usefixtures("_frozen_task_delivery_text_writer")
def test_fresh_task_delivery_rejects_unowned_reply_attempt_reference_for_all_kinds():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
        db.execute("insert into tasks values(1,'meeting_alignment_jobs'),(2,'reply_tasks')")
        action_storage.create_action_schema(
            db, include_reply=True, reply_variant="fresh_memory_fk", include_email=True,
            include_followup_sync=True, include_business_sync=True,
            include_task_delivery=True,
        )
        facts = (
            ("meeting_alignment_delivery_claims", {"job_id": 1, "claim_token": "token"}),
            ("meeting_memory_write_events", {"id": 10, "meeting_job_id": 1,
                                             "execution_generation": "first", "payload_json": "{}"}),
            ("task_memory_write_events", {"id": 11, "reply_task_id": 2,
                                          "execution_generation": "first"}),
            ("wechat_deliveries", {"id": 12, "reply_task_id": 2, "account_id": "a",
                                   "target_type": "contact", "target_id": "t",
                                   "reply_text": "text"}),
        )
        for kind, values in facts:
            action_storage.insert_task_delivery_action(db, kind, values)
        parent_id = db.execute("select id from actions where record_type='meeting_alignment_delivery_claims'").fetchone()[0]
        for kind, _ in facts:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("update actions set memory_attempt_ref=? where record_type=?",
                           (parent_id, kind))
            assert db.execute("select count(*) from actions where record_type=? and memory_attempt_ref is not null",
                              (kind,)).fetchone() == (0,)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("kind,values", (
    ("meeting_memory_write_events", {"meeting_job_id": 1, "execution_generation": "g",
                                     "payload_json": "{}"}),
    ("task_memory_write_events", {"reply_task_id": 2, "execution_generation": "g"}),
    ("wechat_deliveries", {"reply_task_id": 2, "account_id": "a", "target_type": "contact",
                           "target_id": "t", "reply_text": "text"}),
))
@pytest.mark.parametrize("explicit_transaction", (False, True))
@pytest.mark.usefixtures("_frozen_task_delivery_text_writer")
def test_auto_id_full_preserves_source_and_target_caller_transaction(
    variant, kind, values, explicit_transaction,
):
    maximum = 2**63 - 1
    with closing(sqlite3.connect(":memory:")) as source, closing(sqlite3.connect(":memory:")) as target:
        for db in (source, target):
            db.execute("create table tasks(id integer primary key,record_type text not null)")
            db.execute("create table runs(id integer primary key,record_type text not null)")
            db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
            db.execute("create table witness(id integer primary key)")
            db.execute("insert into tasks values(1,'meeting_alignment_jobs'),(2,'reply_tasks')")
        for obj in sorted(SOURCE[variant][kind]["objects"], key=lambda item: item[0] != "table"):
            if obj[3]:
                source.execute(obj[3])
        target_variant = "actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk"
        action_storage.create_action_schema(
            target, include_reply=True, reply_variant=target_variant, include_email=True,
            include_followup_sync=True, include_business_sync=True,
            include_task_delivery=True,
        )
        columns = ("id", *values)
        source.execute(
            f"insert into {kind}({','.join(columns)}) values({','.join('?' for _ in columns)})",
            (maximum, *values.values()),
        )
        source.execute(f"delete from {kind} where id=?", (maximum,))
        action_storage.insert_task_delivery_action(target, kind, {"id": maximum, **values})
        action_storage.delete_task_delivery_action(target, kind, maximum)
        for db in (source, target):
            db.commit()
            if explicit_transaction:
                db.execute("begin")
            db.execute("insert into witness values(1)")
            if explicit_transaction:
                db.execute("savepoint caller_nested")
        with pytest.raises(sqlite3.OperationalError, match="database or disk is full"):
            source.execute(
                f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
        with pytest.raises(sqlite3.OperationalError, match="database or disk is full"):
            action_storage.insert_task_delivery_action(target, kind, values)
        for db in (source, target):
            assert db.in_transaction
            assert db.execute("select id from witness").fetchall() == [(1,)]
            if explicit_transaction:
                db.execute("rollback to caller_nested")
                db.execute("release caller_nested")
            assert db.execute("select id from witness").fetchall() == [(1,)]


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("kind,values,parent", (
    ("meeting_memory_write_events", {"meeting_job_id": 1, "execution_generation": "g",
                                     "payload_json": "{}"}, "meeting_job_id"),
    ("task_memory_write_events", {"reply_task_id": 2, "execution_generation": "g"},
     "reply_task_id"),
    ("wechat_deliveries", {"reply_task_id": 2, "account_id": "a", "target_type": "contact",
                           "target_id": "t", "reply_text": "text"}, "reply_task_id"),
))
@pytest.mark.parametrize("ignore", (False, True))
@pytest.mark.parametrize("temp_shadow", (False, True))
@pytest.mark.usefixtures("_frozen_task_delivery_text_writer")
def test_wrong_parent_kind_precedes_full_auto_id_as_in_source(
    variant, kind, values, parent, ignore, temp_shadow,
):
    maximum = 2**63 - 1
    with closing(sqlite3.connect(":memory:")) as source, closing(sqlite3.connect(":memory:")) as target:
        for db in (source, target):
            db.execute("create table tasks(id integer primary key,record_type text not null)")
            db.execute("create table runs(id integer primary key,record_type text not null)")
            db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
            db.execute("create table witness(id integer primary key)")
            db.execute("insert into tasks values(1,'meeting_alignment_jobs'),(2,'reply_tasks')")
        for obj in sorted(SOURCE[variant][kind]["objects"], key=lambda item: item[0] != "table"):
            if obj[3]:
                source.execute(obj[3])
        action_storage.create_action_schema(
            target, include_reply=True,
            reply_variant="actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk",
            include_email=True, include_followup_sync=True, include_business_sync=True,
            include_task_delivery=True,
        )
        columns = ("id", *values)
        source.execute(
            f"insert into {kind}({','.join(columns)}) values({','.join('?' for _ in columns)})",
            (maximum, *values.values()),
        )
        source.execute(f"delete from {kind} where id=?", (maximum,))
        action_storage.insert_task_delivery_action(target, kind, {"id": maximum, **values})
        action_storage.delete_task_delivery_action(target, kind, maximum)
        wrong = {**values, parent: 2 if parent == "meeting_job_id" else 1}
        if temp_shadow:
            expected = "meeting_alignment_jobs" if parent == "meeting_job_id" else "reply_tasks"
            for db in (source, target):
                db.execute("create temp table tasks(id integer primary key,record_type text not null)")
                db.execute("insert into temp.tasks values(?,?)", (wrong[parent], expected))
        for db in (source, target):
            db.commit()
            db.execute("begin")
            db.execute("insert into witness values(1)")
            db.execute("savepoint caller_nested")
        with pytest.raises(sqlite3.IntegrityError) as native_error:
            source.execute(
                f"insert {'or ignore ' if ignore else ''}into {kind}({','.join(wrong)}) "
                f"values({','.join('?' for _ in wrong)})", tuple(wrong.values()),
            )
        with pytest.raises(sqlite3.IntegrityError) as target_error:
            action_storage.insert_task_delivery_action(target, kind, wrong, ignore=ignore)
        assert str(target_error.value) == str(native_error.value)
        for db, table, sequence_name in ((source, kind, kind), (target, "actions", "actions:" + kind)):
            predicate = "" if db is source else " where record_type='" + kind + "'"
            assert db.execute(f"select count(*) from {table}{predicate}").fetchone() == (0,)
            assert db.execute("select seq from main.sqlite_sequence where name=?",
                              (sequence_name,)).fetchone() == (maximum,)
        for db in (source, target):
            assert db.in_transaction
            assert db.execute("select id from witness").fetchall() == [(1,)]
            if temp_shadow:
                assert db.execute("select record_type from main.tasks where id=?",
                                  (wrong[parent],)).fetchone() == (
                                      "reply_tasks" if parent == "meeting_job_id" else "meeting_alignment_jobs",
                                  )
                assert db.execute("select record_type from temp.tasks where id=?",
                                  (wrong[parent],)).fetchone() == (
                                      "meeting_alignment_jobs" if parent == "meeting_job_id" else "reply_tasks",
                                  )
            db.execute("rollback to caller_nested")
            db.execute("release caller_nested")
            assert db.execute("select id from witness").fetchall() == [(1,)]


def test_source_capture_requires_exact_actual_or_fresh_declaration():
    import importlib.util
    assert importlib.util.find_spec("app.action_task_delivery_storage") is not None
    from app import action_task_delivery_storage as delivery

    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
        for definition in SOURCE["fresh"].values():
            for obj in sorted(definition["objects"], key=lambda item: item[0] != "table"):
                if obj[3]:
                    db.execute(obj[3])
        action_storage.create_action_schema(
            db, include_reply=True, reply_variant="fresh_memory_fk",
            include_email=True, include_followup_sync=True,
            include_business_sync=True,
        )
        for sql in LINK_TRIGGER_SQL[:2]:
            db.execute(sql)
        capture = delivery.capture_task_delivery_source(db)
        assert capture["source_variant"] == "fresh"
        db.execute("alter table wechat_deliveries add column unwanted text")
        unchanged = tuple(db.execute("select type,name,sql from sqlite_schema order by type,name"))
        with pytest.raises(ValueError, match="source declaration"):
            delivery.capture_task_delivery_source(db)
        db.execute("begin")
        with pytest.raises(ValueError, match="source declaration"):
            delivery.migrate_task_delivery_schema(db, capture)
        assert tuple(db.execute("select type,name,sql from sqlite_schema order by type,name")) == unchanged


@pytest.mark.usefixtures("_frozen_task_delivery_text_writer")
def test_task_delivery_parent_kind_and_claim_cascade():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        db.execute("create table business(task_public_id integer unique)")
        db.execute("create table business_task_signals(id integer primary key)")
        db.execute("create table email_classifications(id integer primary key)")
        db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
        db.execute("insert into tasks values(44,'meeting_alignment_jobs'),(55,'reply_tasks'),"
                   "(66,'meeting_alignment_jobs')")
        action_storage.create_action_schema(
            db, include_reply=True, reply_variant="actual_no_memory_fk",
            include_email=True, include_followup_sync=True, include_business_sync=True,
            include_task_delivery=True,
        )
        assert action_storage.insert_task_delivery_action(
            db, "meeting_alignment_delivery_claims", {"job_id": 44, "claim_token": "token"},
        ).lastrowid == 44
        structural_id = db.execute("select id from actions where record_type='meeting_alignment_delivery_claims'").fetchone()[0]
        assert action_storage.update_task_delivery_action(
            db, "meeting_alignment_delivery_claims", 44, {"job_id": 66},
        ).rowcount == 1
        assert db.execute("select id,legacy_key,td_claim_job_id,td_claim_token from actions "
                          "where record_type='meeting_alignment_delivery_claims'").fetchone() == (
                              structural_id, "66", 66, "token",
                          )
        with pytest.raises(sqlite3.IntegrityError, match="task parent must be a meeting job"):
            action_storage.insert_task_delivery_action(
                db, "meeting_memory_write_events",
                {"meeting_job_id": 55, "execution_generation": "one", "payload_json": "{}"},
            )
        db.execute("delete from tasks where id=66")
        assert db.execute("select 1 from actions where record_type='meeting_alignment_delivery_claims'").fetchone() is None


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.usefixtures("_frozen_task_delivery_text_writer")
def test_four_families_round_trip_current_facts_and_allocator(variant, monkeypatch):
    import historical_action_text_fixture
    from app import action_task_delivery_storage as delivery

    historical_action_text_fixture.install(monkeypatch)

    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("create table tasks(id integer primary key,record_type text not null)")
        db.execute("create table runs(id integer primary key,record_type text not null)")
        db.execute("create table business_legacy_links(work_update_id integer,task_id integer)")
        db.execute("insert into tasks values(4,'meeting_alignment_jobs'),(5,'reply_tasks')")
        for definition in SOURCE[variant].values():
            for obj in sorted(definition["objects"], key=lambda item: item[0] != "table"):
                if obj[3]:
                    db.execute(obj[3])
        action_storage.create_action_schema(
            db, include_reply=True,
            reply_variant="actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk",
            include_email=True, include_followup_sync=True, include_business_sync=True,
        )
        for sql in LINK_TRIGGER_SQL[:2]:
            db.execute(sql)
        db.execute("insert into actions(record_type,legacy_key,payload_json) "
                   "values('native_reply_dispatches','[\"dingtalk\",\"old\"]',"
                   "'{\"created_at\":\"2026-10-17\"}')")
        existing_action = db.execute(
            "select id,record_type,legacy_key,payload_json from actions"
        ).fetchone()
        db.execute("insert into meeting_alignment_delivery_claims values(4,'claim-a')")
        db.execute("insert into meeting_memory_write_events(id,meeting_job_id,execution_generation,payload_json,status) "
                   "values(7,4,'',?,'unrecognized')", (sqlite3.Binary(b'raw-payload'),))
        db.execute("insert into task_memory_write_events(id,reply_task_id,execution_generation,memories_json) "
                   "values(7,5,'one',?)", (sqlite3.Binary(b'raw-memories'),))
        db.execute("insert into wechat_deliveries(id,reply_task_id,account_id,target_type,target_id,reply_text,evidence_json) "
                   "values(7,5,'a','contact','t','text',?)", (sqlite3.Binary(b'raw-evidence'),))
        db.execute("update sqlite_sequence set seq=31 where name='wechat_deliveries'")
        baseline = {name: delivery._source_declaration(db, name)
                    for name in delivery.TASK_DELIVERY_KINDS}
        old_rows = {name: db.execute(f"select * from {name}").fetchall()
                    for name in delivery.TASK_DELIVERY_KINDS}
        old_seq = dict(db.execute("select name,seq from sqlite_sequence"))
        old_schema = tuple(db.execute(
            "select type,name,tbl_name,sql from sqlite_schema "
            "where sql is not null order by type,name"))
        db.commit()
        db.execute("begin")
        source = delivery.capture_task_delivery_source(db)
        state = delivery.migrate_task_delivery_schema(db, source)
        assert all(db.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone() is None
                   for name in delivery.TASK_DELIVERY_KINDS)
        assert db.execute("select count(*) from actions where record_type in (?,?,?,?)",
                          delivery.TASK_DELIVERY_KINDS).fetchone()[0] == 4
        action_storage.insert_action(db, "native_reply_dispatches", {
            "channel": "dingtalk", "delivery_key": "new", "created_at": "2026-10-17",
        })
        assert action_storage.update_action(
            db, "native_reply_dispatches", ("dingtalk", "new"),
            {"created_at": "2026-10-18"},
        ).rowcount == 1
        assert db.execute("select id,record_type,legacy_key,payload_json from actions "
                          "where record_type='native_reply_dispatches' and legacy_key='[\"dingtalk\",\"old\"]'").fetchone() == existing_action
        assert db.execute("select count(*) from actions where record_type='native_reply_dispatches'").fetchone()[0] == 2
        inserted = action_storage.insert_task_delivery_action(
            db, "wechat_deliveries",
            {"reply_task_id": 5, "account_id": "a", "target_type": "contact",
             "target_id": "t", "reply_text": "second", "execution_generation": "two"},
        )
        assert inserted.lastrowid == 32
        assert action_storage.update_task_delivery_action(
            db, "wechat_deliveries", 32, {"status": "unrecognized-later"},
            where_sql="status='ready_to_send'",
        ).rowcount == 1
        duplicate_generation = {
            "id": 33, "reply_task_id": 5, "account_id": "a", "target_type": "contact",
            "target_id": "t", "reply_text": "third", "execution_generation": "initial",
        }
        if variant == "actual":
            with pytest.raises(sqlite3.IntegrityError):
                action_storage.insert_task_delivery_action(
                    db, "wechat_deliveries", duplicate_generation)
        else:
            action_storage.insert_task_delivery_action(db, "wechat_deliveries", duplicate_generation)
            assert action_storage.delete_task_delivery_action(
                db, "wechat_deliveries", 33).rowcount == 1
        db.execute("update actions set payload_json=json_set(payload_json,'$.status','sent') "
                   "where record_type='wechat_deliveries' and legacy_key='7'")
        delivery.restore_task_delivery_schema(db, state)
        assert tuple(db.execute(
            "select type,name,tbl_name,sql from sqlite_schema "
            "where sql is not null order by type,name")) == old_schema
        assert db.execute("select id,record_type,legacy_key,payload_json from actions "
                          "where record_type='native_reply_dispatches' and legacy_key='[\"dingtalk\",\"old\"]'").fetchone() == existing_action
        assert db.execute("select count(*) from actions where record_type='native_reply_dispatches'").fetchone()[0] == 2
        assert {name: delivery._source_declaration(db, name)
                for name in delivery.TASK_DELIVERY_KINDS} == baseline
        assert db.execute("select status from wechat_deliveries where id=7").fetchone()[0] == "sent"
        assert db.execute("select status from wechat_deliveries where id=32").fetchone()[0] == "unrecognized-later"
        assert {name: db.execute(f"select * from {name}").fetchall()
                for name in delivery.TASK_DELIVERY_KINDS if name != "wechat_deliveries"} == {
                    name: old_rows[name] for name in delivery.TASK_DELIVERY_KINDS if name != "wechat_deliveries"
                }
        assert dict(db.execute("select name,seq from sqlite_sequence")) == {
            **old_seq, "wechat_deliveries": 32 if variant == "actual" else 33,
        }
        reforward = delivery.capture_task_delivery_source(db)
        delivery.migrate_task_delivery_schema(db, reforward)
        assert db.execute("select count(*) from actions where record_type='wechat_deliveries'").fetchone()[0] == 2
        assert db.execute("select seq from sqlite_sequence where name='actions:wechat_deliveries'").fetchone()[0] == (32 if variant == "actual" else 33)
        assert action_storage.delete_action(
            db, "native_reply_dispatches", ("dingtalk", "new"),
        ).rowcount == 1
        db.rollback()
