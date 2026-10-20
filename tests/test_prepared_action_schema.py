"""Frozen source declarations and exact business-fact inverse in both variants."""
import json
import sqlite3

import pytest

from app import action_storage as actions
from app.action_email_storage import _columns
from app.prepared_action_definitions import PARENT, CHILDREN, COLUMNS
from app.prepared_action_schema import (
    _facts, migrate_prepared_actions, restore_prepared_actions, prepared_action_objects,
)
from app.shared_action_lifecycle import transition
from app.specialized_action_schema import specialized_action_objects
from app.current_database_schema import create_current_database_schema


@pytest.fixture(params=actions.REPLY_VARIANTS)
def db(request):
    # Explicit component fixture: C1 Tasks with frozen 19.8 Actions. The archive
    # tests separately construct all three Stores using the actual source code.
    with sqlite3.connect(':memory:') as connection:
        create_current_database_schema(connection)
        connection.execute('drop table actions')
        for category in ('table', 'index', 'trigger'):
            for obj in specialized_action_objects(request.param):
                if obj['type'] == category:
                    connection.execute(obj['sql'])
        connection.commit()
        connection.execute('begin')
        yield connection


def source(db):
    assert actions._objects(db, 'actions') == specialized_action_objects(actions._reply_variant_for(db))


def retained(db):
    columns = tuple(c for c in _columns(db, "actions") if c not in COLUMNS)
    fields = ",".join(columns) + "," + ",".join(f"typeof({c})" for c in columns)
    return (
        db.execute("select " + fields + " from actions where record_type not in (?,?) order by id", CHILDREN).fetchall(),
        db.execute("select rowid,name,seq from sqlite_sequence order by rowid").fetchall(),
    )


def seed(db):
    for index, key in enumerate(("01", "1", " whitespace \t", "unicode:中", "claim-only")):
        actions.insert_action(db, PARENT, {
            "channel": "dingtalk", "delivery_key": key, "final_body": " body\x00\n" + key,
            "feedback_token": "token", "postfix_version": "version", "created_at": f"prepare-{index}",
        })
        if index in (0, 3, 4):
            actions.insert_action(db, CHILDREN[1], {
                "channel": "dingtalk", "delivery_key": key, "created_at": f"claim-{index}",
            })
        if index < 4:
            actions.insert_action(db, CHILDREN[0], {
                "channel": "dingtalk", "delivery_key": key,
                "provider_result_json": ('null', '{}', ' { "z" : 1, "a": 2 } \n', 'not-json\x00')[index],
                "created_at": f"receipt-{index}",
            })
    # Source-equivalent JSON key spelling has no public integer identity.
    db.execute("update actions set legacy_key=? where record_type=? and delivery_key='01'",
               (' [ "dingtalk" , "\\u0030\\u0031" ] ', CHILDREN[0]))
    for value in (b"bytes\x00\xff", 1.25, -0.0, None, 3, "text", float("inf"), float("-inf")):
        actions.insert_error_action(db, {"conversation_id": value, "message_id": value,
            "kind": "fixture", "detail": value if value is not None else "",
            "created_at": "error-clock"})
    db.execute("insert into sqlite_sequence(rowid,name,seq) values(912,'actions:agent_effect_intents',811)")


def test_owner_migration_current_inverse_preserves_source_facts_and_retained_bytes(db):
    source(db)
    seed(db)
    expected, original = _facts(db, owned=False), retained(db)
    source_count = db.execute("select count(*) from actions").fetchone()[0]
    metadata = migrate_prepared_actions(db)
    assert metadata == {"reply_variant": actions._reply_variant_for(db)}
    assert actions._objects(db, "actions") == prepared_action_objects(metadata["reply_variant"])
    assert retained(db) == original
    assert _facts(db, owned=True) == expected
    assert db.execute("select count(*) from actions").fetchone()[0] == source_count - 7
    assert not db.execute("select 1 from actions where record_type in (?,?)", CHILDREN).fetchone()
    # Current new writes, including a claim with no receipt and literal JSON null.
    for key in ("after", "after-claim"):
        transition(db, "prepared", "prepare", values={"channel": "wechat", "delivery_key": key,
            "final_body": "after body", "postfix_version": "2", "created_at": "after-prepare"})
        transition(db, "prepared", "claim", identity=("wechat", key), values={"created_at": "after-claim"})
    transition(db, "prepared", "outcome", identity=("wechat", "after"),
               values={"provider_result_json": "null", "created_at": "after-receipt"})
    current, stable = _facts(db, owned=True), retained(db)
    restore_prepared_actions(db, metadata)
    assert _facts(db, owned=False) == current
    assert retained(db) == stable
    assert actions._objects(db, "actions") == specialized_action_objects(metadata["reply_variant"])
    migrate_prepared_actions(db)
    assert _facts(db, owned=True) == current
    assert retained(db) == stable
    assert not db.execute("select name from sqlite_temp_schema where name like '%prepared_action%'").fetchall()


def test_target_parent_only_slots_and_receipt_presence(db):
    source(db)
    migrate_prepared_actions(db)
    transition(db, "prepared", "prepare", values={"channel": "wechat", "delivery_key": "empty",
        "final_body": "body", "postfix_version": "1"})
    assert _facts(db, owned=True)[CHILDREN[0]] == []
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update actions set postfix_receipt_provider_result_json='null' where record_type=?", (PARENT,))
    actions.insert_error_action(db, {"kind": "fixture", "detail": "value", "created_at": "clock"})
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update actions set postfix_dispatch_created_at='fake' where record_type='errors'")
    for body in ("null", "{}"):
        key = "receipt-" + body
        transition(db, "prepared", "prepare", values={"channel": "wechat", "delivery_key": key,
            "final_body": "body", "postfix_version": "1"})
        transition(db, "prepared", "outcome", identity=("wechat", key),
                   values={"provider_result_json": body, "created_at": "own-clock"})
    assert [row[2] for row in _facts(db, owned=True)[CHILDREN[0]]] == ["null", "{}"]
    # Original child codes cannot be physical owners in this selected schema.
    for kind in CHILDREN:
        with pytest.raises(sqlite3.IntegrityError):
            actions.insert_action(db, kind, {"channel": "wechat", "delivery_key": "empty",
                **({"provider_result_json": "{}"} if kind == CHILDREN[0] else {})})


def test_source_validation_and_ignore_match_before_and_after_owner_migration(db):
    source(db)
    actions.insert_action(db, PARENT, {"channel": "wechat", "delivery_key": "key",
        "final_body": "body", "postfix_version": "1"})
    db.commit()
    db.execute("pragma foreign_keys=on")
    variants = [
        ("claim", ("wechat", "key"), {}),
        ("claim", ("wechat", "key"), {"created_at": None}),
        ("claim", (None, "key"), {}),
        ("claim", ("wechat", None), {}),
        ("claim", ("wechat", "missing"), {}),
        ("claim", (" wechat ", "key"), {}),
        ("claim", ("wechat", " key "), {}),
        ("claim", ("wechat", "key"), {"created_at": b"x"}),
        *[("outcome", ("wechat", "key"), {"provider_result_json": v})
          for v in (None, b"x", 1, 1.2, -0.0, True, {}, [], "", "null", "{}", "raw\x00text")],
        ("outcome", ("wechat", "missing"), {"provider_result_json": "{}"}),
        ("outcome", ("invalid", ""), {"provider_result_json": "{}"}),
    ]

    def exercise(owned):
        results = []
        for event, identity, values in variants:
            db.execute("savepoint trial")
            try:
                if owned:
                    result = transition(db, "prepared", event, identity=identity, values=values)
                else:
                    kind = CHILDREN[event == "claim"]
                    result = actions.insert_action(db, kind,
                        {"channel": identity[0], "delivery_key": identity[1]} | values, ignore=True)
                results.append(("ok", result.lastrowid, result.rowcount))
            except (ValueError, sqlite3.IntegrityError) as exc:
                results.append((type(exc).__name__, str(exc)))
            finally:
                db.execute("rollback to trial")
                db.execute("release trial")
        return results

    expected = exercise(False)
    db.execute("pragma foreign_keys=off")
    db.execute("begin")
    migrate_prepared_actions(db)
    db.commit()
    db.execute("pragma foreign_keys=on")
    assert exercise(True) == expected


def test_invalid_declaration_and_orphan_refuse_without_mutation(db):
    source(db)
    actions.insert_action(db, CHILDREN[1], {"channel": "wechat", "delivery_key": "orphan"})
    before = retained(db)
    with pytest.raises(ValueError, match="exact parent"):
        migrate_prepared_actions(db)
    assert retained(db) == before
    db.execute("delete from actions where record_type=?", (CHILDREN[1],))
    db.execute("create index extra_prepared_index on actions(legacy_key)")
    with pytest.raises(ValueError, match="source declaration"):
        migrate_prepared_actions(db)


def test_nonminimal_parent_jsonb_framing_is_preserved(db):
    source(db)
    actions.insert_action(db, PARENT, {"channel": "wechat", "delivery_key": "binary",
        "final_body": "body", "postfix_version": "1"})
    raw = db.execute("select payload_json from actions where record_type=?", (PARENT,)).fetchone()[0]
    header = raw[0] >> 4
    offset = 1 if header < 12 else {12: 2, 13: 3, 14: 5, 15: 9}[header]
    body = raw[offset:]
    nonminimal = bytes([0xEB]) + len(body).to_bytes(4, "big") + body
    assert json.loads(db.execute("select json(?)", (nonminimal,)).fetchone()[0])
    db.execute("update actions set payload_json=? where record_type=?", (nonminimal, PARENT))
    metadata = migrate_prepared_actions(db)
    transition(db, "prepared", "claim", identity=("wechat", "binary"))
    assert db.execute("select payload_json from actions where record_type=?", (PARENT,)).fetchone()[0] == nonminimal
    restore_prepared_actions(db, metadata)
    assert db.execute("select payload_json from actions where record_type=?", (PARENT,)).fetchone()[0] == nonminimal


def test_unrelated_existing_fk_finding_and_raw_run_are_preserved(db):
    from app.run_storage import insert_run
    source(db)
    seed(db)
    insert_run(db, "agent_runs", dict(id=190042, task_id=999, execution_generation="missing-task",
        role="consumer", session_id=b"raw-session\0", transcript_start=1.25))
    findings = [tuple(row) for row in db.execute("pragma foreign_key_check")]
    assert findings == [("runs", 190042, "tasks", 1)]
    fields = ",".join(_columns(db, "runs")) + "," + ",".join(
        "typeof(" + field + ")" for field in _columns(db, "runs"))
    original = db.execute("select " + fields + " from runs").fetchall()
    water = db.execute("select rowid,name,seq from sqlite_sequence order by rowid").fetchall()
    metadata = migrate_prepared_actions(db)
    restore_prepared_actions(db, metadata)
    migrate_prepared_actions(db)
    assert db.execute("select " + fields + " from runs").fetchall() == original
    assert db.execute("select rowid,name,seq from sqlite_sequence order by rowid").fetchall() == water
    assert [tuple(row) for row in db.execute("pragma foreign_key_check")] == findings


def test_inverse_refuses_unknown_target_declaration_before_write(db):
    source(db)
    seed(db)
    metadata = migrate_prepared_actions(db)
    db.execute("create index extra_prepared_index on actions(legacy_key)")
    before = (retained(db), _facts(db, owned=True), actions._objects(db, "actions"), db.total_changes)
    with pytest.raises(ValueError, match="source declaration"):
        restore_prepared_actions(db, metadata)
    assert (retained(db), _facts(db, owned=True), actions._objects(db, "actions"), db.total_changes) == before
    assert not db.execute("select name from sqlite_temp_schema where name like '%prepared_action%'").fetchall()
