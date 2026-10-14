"""Disabled follow-up/Todo action declarations and source-shaped projections."""

import json
import sqlite3
from pathlib import Path

import pytest

from app.action_storage import _action_definitions, _encoded_sqlite_value, create_action_schema
from app.action_followup_sync_definitions import (
    SYNC_DEFAULTS, SYNC_FIELDS, SYNC_SOURCE_INDEXES, sync_action_projection,
)


SOURCE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-source.json").read_text())


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
def test_disabled_manifest_and_exact_field_projections(variant):
    before = _action_definitions(include_reply=True, reply_variant=variant, include_email=True)
    with sqlite3.connect(":memory:") as db:
        db.execute("create table email_classifications(id integer primary key)")
        db.execute("create table business_tasks(id integer primary key)")
        db.execute("create table business_task_signals(id integer primary key)")
        create_action_schema(db, include_reply=True, reply_variant=variant,
                             include_email=True, include_followup_sync=True)
        snapshot = SOURCE["actual"] if variant == "actual_no_memory_fk" else SOURCE["fresh_production_constructor"]["snapshot"]
        actual = snapshot["tables"]
        source_variant = "actual" if variant == "actual_no_memory_fk" else "fresh_candidate"
        assert SYNC_DEFAULTS[source_variant] == {
            kind: {c["name"]: c["dflt_value"] for c in table["columns"] if c["dflt_value"] is not None}
            for kind, table in actual.items()
        }
        if variant == "actual_no_memory_fk":
            assert {kind: tuple(c["name"] for c in table["columns"]) for kind, table in actual.items()} == SYNC_FIELDS
        columns = {r[1] for r in db.execute("pragma table_xinfo(actions)")}
        assert {"sync_" + field for fields in SYNC_FIELDS.values() for field in fields if field != "id"} - {"sync_draft_id"} <= columns
        assert "sync_draft_action_id" in columns
        for kind, table in actual.items():
            fields = tuple(c["name"] for c in table["columns"])
            assert tuple(r[0] for r in db.execute(sync_action_projection(kind, source_variant=source_variant) + " limit 0").description) == fields
    assert _action_definitions(include_reply=True, reply_variant=variant, include_email=True) == before


def _value(field, affinity):
    if field == "target_kind":
        return "group"
    if field == "status":
        return "draft"
    if field == "operation":
        return "create"
    if field == "state":
        return "claimed"
    if field == "business_task_id" or field == "source_signal_id":
        return 1
    return 1 if affinity == "INTEGER" else 0.5 if affinity == "REAL" else "source\x00text"


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
def test_all_150_source_fields_round_trip_direct_storage(variant):
    snapshot = SOURCE["actual"] if variant == "actual_no_memory_fk" else SOURCE["fresh_production_constructor"]["snapshot"]
    with sqlite3.connect(":memory:") as db:
        db.execute("pragma foreign_keys=on")
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text)")
        db.execute("create table email_classifications(id integer primary key)")
        db.execute("create table business_tasks(id integer primary key)")
        db.execute("create table business_task_signals(id integer primary key)")
        db.execute("insert into business_tasks values(1)")
        db.execute("insert into business_task_signals values(1)")
        create_action_schema(db, include_reply=True, reply_variant=variant,
                             include_email=True, include_followup_sync=True)
        parent_action = None
        for n, (kind, table) in enumerate(snapshot["tables"].items(), 1):
            values = {c["name"]: _value(c["name"], c["type"]) for c in table["columns"] if c["name"] != "id"}
            if "sync_outbox" in kind:
                values["status"] = "queued"
            elif kind.endswith("dingtalk_links"):
                values["status"] = "creating"
            if kind == "business_task_follow_up_send_attempts":
                values.pop("draft_id")
            payload = json.dumps(values, allow_nan=False)
            columns = "record_type,legacy_key,payload_json"
            args = [kind, str(n), payload]
            if kind == "business_task_follow_up_send_attempts":
                columns += ",sync_draft_action_id"
                args.append(parent_action)
            cursor = db.execute(f"insert into actions({columns}) values({','.join('?' for _ in args)})", args)
            if kind == "business_task_follow_ups":
                parent_action = cursor.lastrowid
            source_variant = "actual" if variant == "actual_no_memory_fk" else "fresh_candidate"
            row = db.execute(sync_action_projection(kind, source_variant=source_variant)).fetchone()
            names = tuple(c["name"] for c in table["columns"])
            assert dict(zip(names, row, strict=True)) == {"id": n, **values, **({"draft_id": 4} if kind == "business_task_follow_up_send_attempts" else {})}


def test_all_native_index_shapes_and_disabled_old_manifest():
    before = _action_definitions(include_reply=True, reply_variant="fresh_memory_fk", include_email=True)
    with sqlite3.connect(":memory:") as db:
        db.execute("create table email_classifications(id integer primary key)")
        db.execute("create table business_tasks(id integer primary key)")
        db.execute("create table business_task_signals(id integer primary key)")
        create_action_schema(db, include_reply=True, reply_variant="fresh_memory_fk",
                             include_email=True, include_followup_sync=True)
        for kind, source_indexes in SYNC_SOURCE_INDEXES.items():
            for ordinal, (unique, keys, predicate) in enumerate(source_indexes):
                name = f"idx_actions_sync_{kind}_{ordinal}"
                sql = db.execute("select sql from sqlite_schema where name=?", (name,)).fetchone()[0]
                assert ("create unique index" in sql.lower()) == unique
                actual_keys = tuple((r[2], bool(r[3])) for r in db.execute(f"pragma index_xinfo({name})") if r[5])
                expected_keys = tuple((None if field == "id" else "sync_draft_action_id" if kind == "business_task_follow_up_send_attempts" and field == "draft_id" else "sync_" + field, desc) for field, desc in keys)
                assert actual_keys == expected_keys
                assert (" and (" in sql) == bool(predicate)
    assert _action_definitions(include_reply=True, reply_variant="fresh_memory_fk", include_email=True) == before


def test_source_key_uniqueness_and_typed_business_draft_parent():
    with sqlite3.connect(":memory:") as db:
        db.execute("pragma foreign_keys=on")
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text)")
        db.execute("create table email_classifications(id integer primary key)")
        db.execute("create table business_tasks(id integer primary key)")
        db.execute("create table business_task_signals(id integer primary key)")
        db.execute("insert into business_tasks values(1)")
        db.execute("insert into business_task_signals values(1)")
        create_action_schema(db, include_reply=True, reply_variant="fresh_memory_fk",
                             include_email=True, include_followup_sync=True)
        draft = {c["name"]: _value(c["name"], c["type"]) for c in SOURCE["actual"]["tables"]["business_task_follow_ups"]["columns"] if c["name"] != "id"}
        parent = db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                            ("business_task_follow_ups", "7", json.dumps(draft))).lastrowid
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                       ("business_task_follow_ups", "8", json.dumps(draft)))
        attempt = {c["name"]: _value(c["name"], c["type"]) for c in SOURCE["actual"]["tables"]["business_task_follow_up_send_attempts"]["columns"] if c["name"] not in ("id", "draft_id")}
        child = db.execute("insert into actions(record_type,legacy_key,payload_json,sync_draft_action_id) values(?,?,?,?)",
                           ("business_task_follow_up_send_attempts", "2", json.dumps(attempt), parent)).lastrowid
        assert db.execute(sync_action_projection("business_task_follow_up_send_attempts") + " and a.id=?", (child,)).fetchone()[1] == 7
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("update actions set legacy_key='9' where id=?", (parent,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("delete from actions where id=?", (parent,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into actions(record_type,legacy_key,payload_json,sync_draft_action_id) values(?,?,?,?)",
                       ("business_task_follow_up_send_attempts", "3", json.dumps({**attempt, "claim_token": "other"}), child))


def test_provider_id_uniqueness_is_legacy_only_and_raw_scalar_bytes_survive():
    with sqlite3.connect(":memory:") as db:
        db.execute("pragma foreign_keys=on")
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text)")
        db.execute("create table email_classifications(id integer primary key)")
        db.execute("create table business_tasks(id integer primary key)")
        db.execute("create table business_task_signals(id integer primary key)")
        db.execute("insert into business_tasks values(1)")
        create_action_schema(db, include_reply=True, reply_variant="fresh_memory_fk",
                             include_email=True, include_followup_sync=True)

        def payload(kind, **overrides):
            values = {c["name"]: _value(c["name"], c["type"]) for c in SOURCE["actual"]["tables"][kind]["columns"] if c["name"] != "id"}
            values.update(overrides)
            return json.dumps(values)

        legacy = "work_todo_dingtalk_links"
        db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                   (legacy, "1", payload(legacy, work_todo_id=1, dingtalk_task_id="provider", status="active")))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                       (legacy, "2", payload(legacy, work_todo_id=2, dingtalk_task_id="provider", status="done")))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                       (legacy, "2", payload(legacy, work_todo_id=1, dingtalk_task_id="", status="creating")))
        business = "business_task_dingtalk_links"
        db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                   (business, "1", payload(business, business_task_id=1, dingtalk_task_id="provider", status="active")))
        db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                   (business, "2", payload(business, business_task_id=1, dingtalk_task_id="provider", status="done")))

        update = payload("work_updates", summary="raw\x00text", changes_json='{"x":  1}')
        db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)", ("work_updates", "-7", update))
        row = db.execute(sync_action_projection("work_updates") + " and a.legacy_key='-7'").fetchone()
        assert row[0] == -7 and row[4] == "raw\x00text" and row[5] == '{"x":  1}'
        encoded = json.loads(update)
        encoded["summary"] = _encoded_sqlite_value(b"\x00\xff")
        encoded["confidence"] = _encoded_sqlite_value(float("inf"))
        db.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                   ("work_updates", "0", json.dumps(encoded)))
        raw = db.execute(sync_action_projection("work_updates") + " and a.legacy_key='0'").fetchone()
        assert raw[4] == b"\x00\xff" and raw[7] == float("inf")


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
@pytest.mark.parametrize("draft_id", (None, float("nan"), 0, -7, 999))
def test_legacy_send_attempt_draft_id_matches_native_scalar_not_null(variant, draft_id):
    snapshot = SOURCE["actual"] if variant == "actual_no_memory_fk" else SOURCE["fresh_production_constructor"]["snapshot"]
    kind = "follow_up_send_attempts"
    values = {c["name"]: _value(c["name"], c["type"]) for c in snapshot["tables"][kind]["columns"] if c["name"] != "id"}
    values["draft_id"] = draft_id
    with sqlite3.connect(":memory:") as native, sqlite3.connect(":memory:") as target:
        native.execute(snapshot["tables"][kind]["sql"])
        target.execute("create table tasks(id integer primary key, record_type text)")
        target.execute("create table runs(id integer primary key, record_type text)")
        target.execute("create table email_classifications(id integer primary key)")
        target.execute("create table business_tasks(id integer primary key)")
        target.execute("create table business_task_signals(id integer primary key)")
        create_action_schema(target, include_reply=True, reply_variant=variant,
                             include_email=True, include_followup_sync=True)
        source_sql = f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})"
        payload = json.dumps({field: _encoded_sqlite_value(value) for field, value in values.items()})
        if draft_id is None or draft_id != draft_id:
            with pytest.raises(sqlite3.IntegrityError):
                native.execute(source_sql, tuple(values.values()))
            with pytest.raises(sqlite3.IntegrityError):
                target.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                               (kind, "1", payload))
        else:
            native.execute(source_sql, tuple(values.values()))
            target.execute("insert into actions(record_type,legacy_key,payload_json) values(?,?,?)",
                           (kind, "1", payload))
            assert native.execute(f"select draft_id from {kind}").fetchone() == (draft_id,)
            assert target.execute(sync_action_projection(kind)).fetchone()[1] == draft_id
