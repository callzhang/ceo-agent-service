"""Independent native SQLite oracle for the captured Phase 5E source variants."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest


INVENTORY = Path(__file__).parents[1] / (
    "docs/superpowers/specs/2026-10-13-database-redesign-phase5e-reply-source.json"
)
CAPTURE = json.loads(INVENTORY.read_text())
VARIANTS = ("actual", "fresh_candidate17")
REQUIRED_ATTEMPT = {
    "conversation_id": "conversation",
    "conversation_title": "title",
    "trigger_message_id": "trigger",
    "trigger_sender": "sender",
    "trigger_text": "input",
    "action": "reply",
    "sensitivity_kind": "ordinary",
    "send_status": "pending",
}
LEGACY = {
    "conversation_single_chat": 1,
    "trigger_create_time": "historical time",
    "universal_execution_id": "historical execution",
    "universal_execution_scope_id": "historical scope",
}


@pytest.fixture(params=VARIANTS)
def source(request):
    return request.param


@pytest.fixture
def db(source):
    tables = CAPTURE[source]["tables"]
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("pragma foreign_keys=on")
        for name in ("reply_attempts", "memory_write_events"):
            connection.execute(tables[name]["sql"])
        for definition in tables.values():
            for index in definition["indexes"]:
                if index["sql"] is not None:
                    connection.execute(index["sql"])
        yield connection


def _insert(db, table, values, *, ignore=False):
    return db.execute(
        f"insert {'or ignore ' if ignore else ''}into {table}"
        f"({','.join(values)}) values({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def _attempt(db, *, ignore=False, **changes):
    return _insert(db, "reply_attempts", REQUIRED_ATTEMPT | changes, ignore=ignore)


def _event(db, attempt_id, event_type="memory_write", *, ignore=False, **changes):
    return _insert(
        db, "memory_write_events",
        {"attempt_id": attempt_id, "event_type": event_type, "payload_json": "{}", **changes},
        ignore=ignore,
    )


def _sequence(db, table):
    return db.execute("select seq from sqlite_sequence where name=?", (table,)).fetchone()[0]


def _row(db, table, row_id, *columns):
    return db.execute(
        f"select {','.join(columns)} from {table} where id=?", (row_id,)
    ).fetchone()


def test_exact_source_declarations_and_metadata(db, source):
    tables = CAPTURE[source]["tables"]
    assert set(tables) == {"reply_attempts", "memory_write_events"}
    for name, definition in tables.items():
        assert db.execute(
            "select sql from sqlite_schema where type='table' and name=?", (name,)
        ).fetchone()[0] == definition["sql"]
        assert [dict(zip(("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"),
                         row, strict=True))
                for row in db.execute(f"pragma table_xinfo({name})")] == definition["columns"]
        assert [dict(zip(("id", "seq", "table", "from", "to", "on_update", "on_delete", "match"),
                         row, strict=True))
                for row in db.execute(f"pragma foreign_key_list({name})")] == definition["foreign_keys"]
        assert {row[0] for row in db.execute(
            "select sql from sqlite_schema where type='index' and tbl_name=? and sql is not null",
            (name,),
        )} == {index["sql"] for index in definition["indexes"] if index["sql"] is not None}
    assert not db.execute("pragma foreign_key_check").fetchall()


def test_attempt_required_fields_and_original_defaults(db, source):
    before = db.execute("select current_timestamp").fetchone()[0]
    attempt_id = _attempt(db).lastrowid
    columns = {column["name"]: column for column in CAPTURE[source]["tables"]["reply_attempts"]["columns"]}
    values = dict(zip(
        columns, _row(db, "reply_attempts", attempt_id, *columns), strict=True
    ))
    assert {name: values.pop(name) for name in REQUIRED_ATTEMPT} == REQUIRED_ATTEMPT
    assert values.pop("id") == attempt_id
    after = db.execute("select current_timestamp").fetchone()[0]
    created_at = values.pop("created_at")
    updated_at = values.pop("updated_at")
    assert created_at == updated_at
    assert before <= created_at <= after
    assert values.pop("reviewed_at") is None
    assert values.pop("agent_run_id") is None
    if source == "actual":
        assert values.pop("conversation_single_chat") is None
        for name in set(LEGACY) - {"conversation_single_chat"}:
            assert values.pop(name) == ""
    assert values == {
        name: {"'[]'": "[]", "'{}'": "{}", "'one_time'": "one_time",
               "'dingtalk'": "dingtalk", "0": 0, "''": ""}[column["dflt_value"]]
        for name, column in columns.items() if name in values
    }


@pytest.mark.parametrize("name", REQUIRED_ATTEMPT)
def test_required_attempt_fields_reject_null(db, name):
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        _attempt(db, **{name: None})


def test_attempt_nullable_review_and_run_fields_accept_null(db):
    attempt_id = _attempt(db, reviewed_at=None, agent_run_id=None).lastrowid
    assert _row(db, "reply_attempts", attempt_id, "reviewed_at", "agent_run_id") == (None, None)


@pytest.mark.parametrize("run_id", (0, 404, None))
def test_attempt_run_id_is_an_unconstrained_scalar(db, run_id):
    attempt_id = _attempt(db, agent_run_id=run_id).lastrowid
    db.execute("update reply_attempts set agent_run_id=? where id=?", (run_id, attempt_id))
    assert _row(db, "reply_attempts", attempt_id, "agent_run_id") == (run_id,)
    assert not db.execute("pragma foreign_key_check").fetchall()


@pytest.mark.parametrize("table", ("reply_attempts", "memory_write_events"))
def test_autoincrement_retains_deleted_and_ignored_highwater(db, table):
    if table == "reply_attempts":
        def insert(**values):
            return _attempt(db, **values)

        duplicate = {"id": 900, "conversation_id": None}
    else:
        parent = _attempt(db).lastrowid

        def insert(**values):
            return _event(db, parent, **values)

        duplicate = {"id": 900, "event_type": None}
    assert insert(id=700).lastrowid == 700
    db.execute(f"delete from {table} where id=700")
    assert insert().lastrowid == 701
    assert insert(ignore=True, **duplicate).rowcount == 0
    assert _sequence(db, table) == 900
    assert insert(**({"event_type": "next"} if table == "memory_write_events" else {})).lastrowid == 901
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        insert(id=1000, **{k: v for k, v in duplicate.items() if k != "id"})
    assert _sequence(db, table) == 901


@pytest.mark.parametrize("table", ("reply_attempts", "memory_write_events"))
def test_ignored_max_integer_exhausts_autoincrement(db, table):
    if table == "reply_attempts":
        def insert(**values):
            return _attempt(db, **values)

        invalid = {"conversation_id": None}
    else:
        parent = _attempt(db).lastrowid

        def insert(**values):
            return _event(db, parent, **values)

        invalid = {"event_type": None}
    assert insert(id=1).lastrowid == 1
    assert insert(id=2**63 - 1, ignore=True, **invalid).rowcount == 0
    assert _sequence(db, table) == 2**63 - 1
    db.commit()
    with pytest.raises(sqlite3.DatabaseError) as exc:
        insert()
    assert exc.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    assert _sequence(db, table) == 2**63 - 1
    assert db.execute(f"select id from {table}").fetchall() == [(1,)]


def test_explicit_attempt_and_event_ids_preserve_allocator(db):
    parent = _attempt(db, id=41).lastrowid
    assert parent == 41
    assert _attempt(db, id=5).lastrowid == 5
    assert _attempt(db).lastrowid == 42
    assert _event(db, parent, id=31).lastrowid == 31
    assert _event(db, parent, "second", id=4).lastrowid == 4
    assert _event(db, parent, "third").lastrowid == 32


def test_memory_unique_membership_and_multiple_event_types(db):
    parent = _attempt(db).lastrowid
    other = _attempt(db).lastrowid
    _event(db, parent)
    _event(db, parent, "memory_write_2")
    _event(db, other)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _event(db, parent)
    assert _event(db, parent, ignore=True).rowcount == 0
    assert db.execute(
        "select attempt_id,event_type from memory_write_events order by id"
    ).fetchall() == [
        (parent, "memory_write"), (parent, "memory_write_2"), (other, "memory_write")
    ]


def test_memory_membership_update_conflict_and_reuse(db):
    first, second = _attempt(db).lastrowid, _attempt(db).lastrowid
    one = _event(db, first).lastrowid
    two = _event(db, second).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        db.execute("update memory_write_events set attempt_id=? where id=?", (first, two))
    assert _row(db, "memory_write_events", two, "attempt_id") == (second,)
    db.execute("delete from memory_write_events where id=?", (one,))
    db.execute("update memory_write_events set attempt_id=? where id=?", (first, two))
    assert _row(db, "memory_write_events", two, "attempt_id") == (first,)


def test_actual_source_allows_orphan_memory_and_parent_key_changes(db, source):
    if source != "actual":
        pytest.skip("actual source contract")
    parent = _attempt(db).lastrowid
    event = _event(db, parent).lastrowid
    _event(db, 404, "orphan")
    db.execute("update reply_attempts set id=? where id=?", (parent + 10, parent))
    assert _row(db, "memory_write_events", event, "attempt_id") == (parent,)
    db.execute("delete from reply_attempts where id=?", (parent + 10,))
    assert db.execute("select count(*) from memory_write_events").fetchone() == (2,)
    assert not db.execute("pragma foreign_key_check").fetchall()


def test_fresh_source_forbids_orphan_and_referenced_parent_changes(db, source):
    if source != "fresh_candidate17":
        pytest.skip("fresh source contract")
    parent = _attempt(db).lastrowid
    event = _event(db, parent).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _event(db, 404)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("delete from reply_attempts where id=?", (parent,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("update reply_attempts set id=? where id=?", (parent + 10, parent))
    assert _row(db, "memory_write_events", event, "attempt_id") == (parent,)
    assert _row(db, "reply_attempts", parent, "id") == (parent,)


def test_memory_child_rebind_and_unreferenced_parent_change(db):
    first, second, unused = (_attempt(db).lastrowid for _ in range(3))
    event = _event(db, first).lastrowid
    db.execute("update memory_write_events set attempt_id=? where id=?", (second, event))
    db.execute("update reply_attempts set id=? where id=?", (unused + 10, unused))
    db.execute("delete from reply_attempts where id=?", (first,))
    assert _row(db, "memory_write_events", event, "attempt_id") == (second,)
    assert not db.execute("pragma foreign_key_check").fetchall()


def test_raw_text_json_and_blob_storage_classes_are_preserved(db):
    raw = "not JSON \0 { trailing"
    payload = b"\x00\xffnot json"
    attempt = _attempt(
        db, trigger_text="message\0body", audit_summary="summary\0text",
        audit_tool_events_json=raw, audit_documents_json=payload,
        oa_action_result_json=raw, mail_action_result_json=payload,
        trigger_text_provenance_json=raw,
    ).lastrowid
    assert _row(db, "reply_attempts", attempt, "trigger_text", "audit_summary",
                "audit_tool_events_json", "audit_documents_json",
                "oa_action_result_json", "mail_action_result_json",
                "trigger_text_provenance_json") == (
        "message\0body", "summary\0text", raw, payload, raw, payload, raw,
    )
    event = _event(db, attempt, payload_json=payload).lastrowid
    assert _row(db, "memory_write_events", event, "payload_json") == (payload,)
    db.execute("update memory_write_events set payload_json=? where id=?", (raw, event))
    assert _row(db, "memory_write_events", event, "payload_json") == (raw,)


def test_actual_legacy_fields_accept_populated_values_and_fresh_lacks_them(db, source):
    fields = {column["name"] for column in CAPTURE[source]["tables"]["reply_attempts"]["columns"]}
    if source == "actual":
        assert set(LEGACY) <= fields
        attempt = _attempt(db, **LEGACY).lastrowid
        assert _row(db, "reply_attempts", attempt, *LEGACY) == tuple(LEGACY.values())
    else:
        assert not set(LEGACY) & fields
    assert len(fields) == (58 if source == "actual" else 54)


def test_memory_defaults_and_required_columns(db):
    parent = _attempt(db).lastrowid
    event = _event(db, parent).lastrowid
    now = db.execute("select current_timestamp").fetchone()[0]
    assert _row(db, "memory_write_events", event, "status", "attempts", "last_error",
                "memory_episode_id", "created_at", "updated_at") == (
        "pending", 0, "", "", now, now
    )
    for field in ("attempt_id", "event_type", "payload_json"):
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            _insert(db, "memory_write_events", {
                "attempt_id": parent, "event_type": "different", "payload_json": "{}",
                field: None,
            })


def test_existing_attempt_correction_and_memory_status_are_mutable(db):
    parent = _attempt(db).lastrowid
    event = _event(db, parent).lastrowid
    db.execute(
        "update reply_attempts set reviewed_at=?, reviewer_feedback=?, "
        "corrected_reply_text=?, final_reply_text=?, send_status=?, resolution=?, "
        "audit_tool_events_json=? where id=?",
        ("reviewed", "feedback", "corrected", "final", "sent", "resolved", "marker\0", parent),
    )
    db.execute(
        "update memory_write_events set status=?, attempts=?, last_error=?, "
        "memory_episode_id=?, updated_at=? where id=?",
        ("written", 3, "", "episode", "later", event),
    )
    assert _row(db, "reply_attempts", parent, "reviewed_at", "reviewer_feedback",
                "corrected_reply_text", "final_reply_text", "send_status", "resolution",
                "audit_tool_events_json") == (
        "reviewed", "feedback", "corrected", "final", "sent", "resolved", "marker\0"
    )
    assert _row(db, "memory_write_events", event, "status", "attempts", "last_error",
                "memory_episode_id", "updated_at") == ("written", 3, "", "episode", "later")
