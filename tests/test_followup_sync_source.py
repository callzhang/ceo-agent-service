"""Native SQLite source oracle for the frozen nine follow-up/Todo-sync families.

Only captured source DDL is replayed. Parent tables are narrow id-only FK stubs;
this does not prove the full historical Store constructor or any target schema.
"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest


CAPTURE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-source.json").read_text())
NAMES = tuple(CAPTURE["actual"]["tables"])
FIELD_COUNTS = (9, 31, 16, 18, 12, 15, 15, 17, 17)


def snapshot(variant):
    return CAPTURE["actual"] if variant == "actual" else CAPTURE["fresh_production_constructor"]["snapshot"]


def insert(db, table, **values):
    return db.execute(
        f"insert into {table}({','.join(values)}) values({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def values(table, suffix="a"):
    """Synthetic minimum valid fields, with distinct uniqueness keys per row."""
    base = {
        "work_updates": dict(project_id=999, source_type="synthetic", source_ref=suffix, summary="summary"),
        "follow_up_drafts": dict(project_id=999),
        "follow_up_send_attempts": dict(draft_id=999, draft_revision=1, claim_token=suffix, idempotency_uuid=suffix),
        "business_task_follow_ups": dict(business_task_id=11, source_signal_id=12, target_conversation_id="conversation", target_kind="group", question_text="question", scheduled_at="time", dedupe_key=suffix),
        "business_task_follow_up_send_attempts": dict(draft_id=1, draft_revision=1, claim_token=suffix, idempotency_uuid=suffix, state="claimed"),
        "task_todo_sync_outbox": dict(operation_key=suffix, work_todo_id=999, operation="create"),
        "business_task_todo_sync_outbox": dict(operation_key=suffix, business_task_id=11, operation="create"),
        "work_todo_dingtalk_links": dict(work_todo_id=999),
        "business_task_dingtalk_links": dict(business_task_id=11, status="creating"),
    }
    return base[table]


@pytest.fixture(params=("actual", "fresh"))
def variant(request):
    return request.param


@pytest.fixture
def db(variant):
    source = snapshot(variant)
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("pragma foreign_keys=on")
        # FK endpoints only: these stubs intentionally make no constructor claim.
        for name in ("business_tasks", "business_task_signals", "business_work_clusters",
                     "business_anchors", "business_projects", "business_project_candidates",
                     "business_attention_items", "work_projects", "work_todos"):
            connection.execute(f"create table {name}(id integer primary key)")
        connection.execute("insert into business_tasks(id) values(11)")
        connection.execute("insert into business_task_signals(id) values(12)")
        for name in NAMES:
            connection.execute(source["tables"][name]["sql"])
        for name in NAMES:
            for index in reversed(source["tables"][name]["indexes"]):
                if index["sql"]:
                    connection.execute(index["sql"])
        external = source["external_schema_references"]
        assert [(item["type"], item["name"]) for item in external] == [("table", "business_legacy_links")]
        connection.execute(external[0]["sql"])
        connection.commit()
        yield connection


def sequence(db, table):
    row = db.execute("select seq from sqlite_sequence where name=?", (table,)).fetchone()
    return None if row is None else row[0]


def test_exact_frozen_native_metadata(db, variant):
    source = snapshot(variant)
    assert len(NAMES) == 9
    assert tuple(len(source["tables"][n]["columns"]) for n in NAMES) == FIELD_COUNTS
    assert sum(FIELD_COUNTS) == 150
    assert sum(len(source["tables"][n]["indexes"]) for n in NAMES) == 24
    assert sum(len(source["tables"][n]["check_expressions"]) for n in NAMES) == 8
    assert sum(len(source["tables"][n]["foreign_keys"]) for n in NAMES) == 5
    for name in NAMES:
        definition = source["tables"][name]
        assert db.execute("select sql from sqlite_schema where type='table' and name=?", (name,)).fetchone()[0] == definition["sql"]
        assert [dict(zip(("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"), row, strict=True))
                for row in db.execute(f"pragma table_xinfo({name})")] == definition["columns"]
        assert [dict(zip(("id", "seq", "table", "from", "to", "on_update", "on_delete", "match"), row, strict=True))
                for row in db.execute(f"pragma foreign_key_list({name})")] == definition["foreign_keys"]
        assert [dict(zip(("seq", "name", "unique", "origin", "partial"), row, strict=True))
                for row in db.execute(f"pragma index_list({name})")] == [
                    {key: index[key] for key in ("seq", "name", "unique", "origin", "partial")}
                    for index in definition["indexes"]]
        for index in definition["indexes"]:
            assert db.execute("select sql from sqlite_schema where type='index' and name=?", (index["name"],)).fetchone()[0] == index["sql"]
            assert [dict(zip(("seqno", "cid", "name", "desc", "coll", "key"), row, strict=True))
                    for row in db.execute(f"pragma index_xinfo({index['name']})")] == index["xinfo"]
        assert definition["identity"]["autoincrement"]
        assert definition["identity"]["integer_primary_key_rowid_alias"]
        assert definition["triggers"] == []
        assert db.execute("select count(*) from sqlite_schema where type='trigger' and tbl_name=?", (name,)).fetchone()[0] == 0
        assert sequence(db, name) is None
    assert db.execute("select sql from sqlite_schema where name='business_legacy_links'").fetchone()[0] == source["external_schema_references"][0]["sql"]
    incoming = [dict(child_table=name, **fk) for name in (*NAMES, "business_legacy_links")
                for fk in [dict(zip(("id", "seq", "table", "from", "to", "on_update", "on_delete", "match"), row, strict=True))
                           for row in db.execute(f"pragma foreign_key_list({name})")]
                if fk["table"] in NAMES]
    assert sorted(incoming, key=lambda item: (item["child_table"], item["id"])) == source["incoming_foreign_keys"]


def test_literal_source_variants_and_omitted_defaults(db, variant):
    source = snapshot(variant)
    draft = source["tables"]["follow_up_drafts"]
    default = next(c["dflt_value"] for c in draft["columns"] if c["name"] == "updated_at")
    assert default == ("''" if variant == "actual" else "current_timestamp")
    expected_draft_order = [c["name"] for c in draft["columns"]]
    expected_link_order = [c["name"] for c in source["tables"]["work_todo_dingtalk_links"]["columns"]]
    assert expected_draft_order.index("updated_at") == (19 if variant == "actual" else 30)
    assert expected_link_order.index("retry_count") == (16 if variant == "actual" else 14)
    assert ('CREATE TABLE "task_todo_sync_outbox"' in source["tables"]["task_todo_sync_outbox"]["sql"]) == (variant == "actual")
    before = db.execute("select current_timestamp").fetchone()[0]
    draft_id = insert(db, "follow_up_drafts", **values("follow_up_drafts")).lastrowid
    after = db.execute("select current_timestamp").fetchone()[0]
    updated, revision, status = db.execute("select updated_at,revision,status from follow_up_drafts where id=?", (draft_id,)).fetchone()
    assert updated == "" if variant == "actual" else before <= updated <= after
    assert (revision, status) == (1, "draft")
    row = insert(db, "work_updates", **values("work_updates"))
    assert db.execute("select changes_json,merge_reason,confidence from work_updates where id=?", (row.lastrowid,)).fetchone() == ("{}", "", 0.0)


def test_independent_autoincrement_and_native_ids(db):
    for name in NAMES:
        assert insert(db, name, **values(name)).lastrowid == 1
        assert sequence(db, name) == 1
    for name in reversed(NAMES):
        db.execute(f"delete from {name} where id=1")
    for name in NAMES:
        data = values(name, "b")
        if name == "business_task_follow_up_send_attempts":
            data["draft_id"] = 2
        assert insert(db, name, **data).lastrowid == 2
        assert sequence(db, name) == 2
    name = "work_updates"
    for candidate in (-7, 0, "9", 10.0):
        row = insert(db, name, id=candidate, **values(name, str(candidate)))
        assert row.lastrowid == int(candidate)
        assert db.execute("select typeof(id) from work_updates where id=?", (int(candidate),)).fetchone() == ("integer",)
    for candidate in (b"bad", "3.5", 1.5, 2**63):
        with pytest.raises((sqlite3.IntegrityError, OverflowError)):
            insert(db, name, id=candidate, **values(name, str(candidate)))
    assert insert(db, name, id=None, **values(name, "null")).lastrowid == 11
    db.execute("delete from work_updates where id=11")
    db.execute("update work_updates set id=50 where id=10")
    db.execute("delete from work_updates where id=50")
    assert sequence(db, name) == 11
    assert insert(db, name, **values(name, "after-update")).lastrowid == 12


def test_sequence_absence_zero_and_transaction_rollback(db):
    assert all(sequence(db, name) is None for name in NAMES)
    db.execute("begin")
    assert insert(db, "work_updates", **values("work_updates")).lastrowid == 1
    assert sequence(db, "work_updates") == 1
    db.execute("rollback")
    assert sequence(db, "work_updates") is None
    assert insert(db, "work_updates", **values("work_updates")).lastrowid == 1
    db.commit()
    db.execute("begin")
    assert insert(db, "work_updates", **values("work_updates", "b")).lastrowid == 2
    db.execute("rollback")
    assert sequence(db, "work_updates") == 1
    assert insert(db, "work_updates", **values("work_updates", "c")).lastrowid == 2
    db.execute("update sqlite_sequence set seq=0 where name='work_updates'")
    assert sequence(db, "work_updates") == 0
    assert all(sequence(db, name) is None for name in NAMES[1:])


def test_native_allocator_exhaustion_rolls_back_whole_transaction(db):
    name = "work_updates"
    db.commit()
    db.execute("begin")
    insert(db, name, **values(name))
    db.execute("update sqlite_sequence set seq=? where name=?", (2**63 - 1, name))
    with pytest.raises(sqlite3.OperationalError, match="database or disk is full"):
        insert(db, name, **values(name, "next"))
    assert db.execute("select count(*) from work_updates").fetchone()[0] == 0
    assert sequence(db, name) is None


def test_source_checks_notnull_and_nullable_scalar_storage(db):
    for name, field, valid, invalid in (
        ("business_task_follow_ups", "target_kind", "direct", "channel"),
        ("business_task_follow_ups", "status", "approved", "other"),
        ("business_task_follow_up_send_attempts", "state", "sending", "other"),
        ("task_todo_sync_outbox", "operation", "complete", "other"),
        ("task_todo_sync_outbox", "status", "unknown", "other"),
        ("business_task_todo_sync_outbox", "operation", "complete", "other"),
        ("business_task_todo_sync_outbox", "status", "unknown", "other"),
        ("business_task_dingtalk_links", "status", "done", "other"),
    ):
        data = values(name, f"{name}-{field}") | {field: valid}
        insert(db, name, **data)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            insert(db, name, **(data | {field: invalid, "id": 1000 + len(name)}))
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            insert(db, name, **(data | {field: None, "id": 2000 + len(name)}))
    # Nullable fields remain NULL; no source trigger fills them.
    row = insert(db, "business_task_dingtalk_links", **values("business_task_dingtalk_links", "nullable"))
    assert db.execute("select dingtalk_task_id,last_dingtalk_done from business_task_dingtalk_links where id=?", (row.lastrowid,)).fetchone() == ("", None)
    for name, field in (("work_updates", "summary"), ("follow_up_drafts", "project_id"),
                        ("follow_up_send_attempts", "claim_token")):
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            insert(db, name, **(values(name, f"null-{name}") | {field: None}))


def test_distinct_source_uniqueness_scopes(db):
    # Legacy draft dedupe and attempt idempotency/revision pairs have no UNIQUE declaration.
    for suffix in ("a", "b"):
        insert(db, "follow_up_drafts", **(values("follow_up_drafts") | {"dedupe_key": "same"}))
        insert(db, "follow_up_send_attempts", **(values("follow_up_send_attempts", suffix) | {"idempotency_uuid": "same"}))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert(db, "follow_up_send_attempts", **values("follow_up_send_attempts", "a"))
    insert(db, "business_task_follow_ups", **values("business_task_follow_ups", "unique"))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert(db, "business_task_follow_ups", **values("business_task_follow_ups", "unique"))
    insert(db, "business_task_follow_up_send_attempts", **values("business_task_follow_up_send_attempts", "first"))
    insert(db, "business_task_follow_up_send_attempts", **(values("business_task_follow_up_send_attempts", "second") | {"idempotency_uuid": "first"}))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert(db, "business_task_follow_up_send_attempts", **values("business_task_follow_up_send_attempts", "first"))
    for name in ("task_todo_sync_outbox", "business_task_todo_sync_outbox"):
        insert(db, name, **values(name, "shared"))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert(db, name, **values(name, "shared"))
    insert(db, "work_todo_dingtalk_links", work_todo_id=20, status="done", dingtalk_task_id="provider")
    insert(db, "work_todo_dingtalk_links", work_todo_id=20, status="failed", dingtalk_task_id="")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert(db, "work_todo_dingtalk_links", work_todo_id=21, status="failed", dingtalk_task_id="provider")
    insert(db, "work_todo_dingtalk_links", work_todo_id=20, status="creating")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert(db, "work_todo_dingtalk_links", work_todo_id=20, status="active")
    insert(db, "business_task_dingtalk_links", business_task_id=11, status="done", dingtalk_task_id="provider")
    insert(db, "business_task_dingtalk_links", business_task_id=11, status="failed", dingtalk_task_id="provider")
    insert(db, "business_task_dingtalk_links", business_task_id=11, status="creating")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert(db, "business_task_dingtalk_links", business_task_id=11, status="active")


def test_immediate_fk_and_unconstrained_legacy_scalar_refs(db):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert(db, "business_task_follow_ups", **(values("business_task_follow_ups") | {"business_task_id": 404}))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert(db, "business_task_follow_ups", **(values("business_task_follow_ups") | {"source_signal_id": 404}))
    # No declared FK from these legacy scalar references to project/Todo/draft.
    insert(db, "follow_up_drafts", project_id=404, todo_id=405)
    insert(db, "follow_up_send_attempts", **(values("follow_up_send_attempts") | {"draft_id": 406}))
    insert(db, "task_todo_sync_outbox", **(values("task_todo_sync_outbox") | {"work_todo_id": 407}))
    insert(db, "work_todo_dingtalk_links", work_todo_id=408)
    parent = insert(db, "business_task_follow_ups", **values("business_task_follow_ups"))
    insert(db, "business_task_follow_up_send_attempts", **values("business_task_follow_up_send_attempts"))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert(db, "business_task_follow_up_send_attempts", **(values("business_task_follow_up_send_attempts", "bad") | {"draft_id": 404}))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("update business_task_follow_ups set id=404 where id=?", (parent.lastrowid,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("delete from business_task_follow_ups where id=?", (parent.lastrowid,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("update business_tasks set id=404 where id=11")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("delete from business_task_signals where id=12")
    update = insert(db, "work_updates", **values("work_updates"))
    insert(db, "business_legacy_links", task_id=11, work_update_id=update.lastrowid)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("delete from work_updates where id=?", (update.lastrowid,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("update work_updates set id=50 where id=?", (update.lastrowid,))


def test_native_rowcount_cas_and_no_owned_triggers(db):
    row = insert(db, "business_task_follow_ups", **values("business_task_follow_ups"))
    assert db.execute("update business_task_follow_ups set status='approved', revision=revision+1 where id=? and revision=1 and status='draft'", (row.lastrowid,)).rowcount == 1
    assert db.execute("update business_task_follow_ups set status='sent' where id=? and revision=1", (row.lastrowid,)).rowcount == 0
    assert db.execute("update business_task_follow_ups set revision=1 where id=?", (row.lastrowid,)).rowcount == 1
    assert db.execute("select revision,status from business_task_follow_ups where id=?", (row.lastrowid,)).fetchone() == (1, "approved")
    assert db.execute("delete from business_task_follow_ups where id=?", (row.lastrowid,)).rowcount == 1


def test_nan_maps_to_null_id_and_ignored_high_id_failures(db):
    # Python's sqlite3 binds NaN as SQL NULL; INTEGER PK then auto-allocates.
    assert insert(db, "work_updates", id=float("nan"), **values("work_updates")).lastrowid == 1
    assert sequence(db, "work_updates") == 1
    for name, failed in (
        ("work_updates", values("work_updates", "null") | {"summary": None}),
        ("business_task_follow_ups", values("business_task_follow_ups", "bad") | {"target_kind": "invalid"}),
    ):
        assert db.execute(
            f"insert or ignore into {name}({','.join(('id', *failed))}) values({','.join('?' for _ in range(len(failed) + 1))})",
            (5000, *failed.values()),
        ).rowcount == 0
        assert db.execute(f"select count(*) from {name} where id=5000").fetchone() == (0,)
        assert sequence(db, name) == 5000
    assert insert(db, "business_task_follow_ups", **values("business_task_follow_ups", "unique")).lastrowid == 5001
    assert db.execute(
        "insert or ignore into business_task_follow_ups(id,business_task_id,source_signal_id,target_conversation_id,target_kind,question_text,scheduled_at,dedupe_key) values(5000,11,12,'conversation','group','question','time','unique')"
    ).rowcount == 0
    assert sequence(db, "business_task_follow_ups") == 5001
