import sqlite3
from pathlib import Path

import pytest

from app.current_action_schema import create_current_action_schema
from app.current_database_schema import create_current_database_schema
from app.action_entity_storage import (
    append_action_receipt,
    add_action_observer,
    claim_action,
    create_action,
    get_action,
    record_action_result,
)


def entity_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    create_current_database_schema(db)
    db.execute("pragma foreign_keys=on")
    return db


def test_blank_schema_matches_frozen_action_declarations():
    candidate = sqlite3.connect(":memory:")
    create_current_database_schema(candidate)
    target = sqlite3.connect(":memory:")
    target.executescript(Path("docs/action-entity-target.sql").read_text())
    query = ("select type,name,sql from sqlite_master where sql is not null "
             "and tbl_name in ('actions','action_receipts','action_observers') order by type,name")
    assert [tuple(row) for row in candidate.execute(query)] == [tuple(row) for row in target.execute(query)]


@pytest.mark.parametrize("missing", ["action_receipts", "action_observers", "both"])
def test_existing_action_schema_with_missing_children_is_rejected_without_repair(missing):
    db = entity_db()
    tables = ("action_receipts", "action_observers") if missing == "both" else (missing,)
    for table in tables:
        db.execute(f"drop table {table}")
    before = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_master order by type,name")]
    with pytest.raises(ValueError, match="unsupported current Action receipt/observer declaration"):
        create_current_action_schema(db)
    after = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_master order by type,name")]
    assert after == before


def test_create_claim_result_uses_one_action():
    db = entity_db()
    created = create_action(db, values={
        "operation": "send", "parameters_json": "{}", "status": "pending",
        "external_action_key": "effect-1", "created_at": "created",
        "updated_at": "updated",
    })
    claimed = claim_action(db, created["id"], expected={"status": "pending"},
                           changes={"status": "processing", "started_at": "claimed"})
    recorded = record_action_result(db, created["id"],
                                    expected={"status": "processing"},
                                    changes={"status": "done", "updated_at": "finished"})
    assert claimed["id"] == recorded["id"] == created["id"]
    assert recorded["status"] == "done"
    assert get_action(db, created["id"]) == recorded


def test_stale_claim_does_not_overwrite():
    db = entity_db()
    action = create_action(db, values={"operation": "send", "status": "pending"})
    first = claim_action(db, action["id"], expected={"status": "pending"},
                         changes={"status": "processing", "started_at": "first"})
    stale = claim_action(db, action["id"], expected={"status": "pending"},
                         changes={"status": "processing", "started_at": "second"})
    assert first["started_at"] == "first"
    assert stale is None
    assert get_action(db, action["id"])["started_at"] == "first"


def test_one_effect_multiple_observers():
    db = entity_db()
    action = create_action(db, values={"operation": "send", "external_action_key": "same"})
    add_action_observer(db, action["id"], agent_run_id=11, task_id=21, created_at="a")
    add_action_observer(db, action["id"], agent_run_id=12, task_id=22, created_at="b")
    receipt_id = append_action_receipt(db, action["id"], values={
        "provider_result_json": '{"message_id":"m1"}', "status": "verified",
    })
    assert receipt_id > 0
    assert db.execute("select count(*) from actions where record_type='action'").fetchone()[0] == 1
    assert db.execute("select count(*) from action_observers where action_id=?", (action["id"],)).fetchone()[0] == 2
    assert db.execute("select count(*) from action_receipts where action_id=?", (action["id"],)).fetchone()[0] == 1


def test_equal_body_different_effects_stay_distinct():
    db = entity_db()
    left = create_action(db, values={"operation": "send", "parameters_json": '{"body":"same"}', "external_action_key": "effect-a"})
    right = create_action(db, values={"operation": "send", "parameters_json": '{"body":"same"}', "external_action_key": "effect-b"})
    assert left["id"] != right["id"]
    assert left["external_action_key"] != right["external_action_key"]


def test_distinct_historical_receipts_survive():
    db = entity_db()
    action = create_action(db, values={"operation": "send", "external_action_key": "effect"})
    first = append_action_receipt(db, action["id"], values={
        "provider_result_json": '{"status":"unknown"}', "result_digest": "old",
    })
    second = append_action_receipt(db, action["id"], values={
        "provider_result_json": '{"status":"sent"}', "result_digest": "new",
        "supersedes_provider_receipt_id": first,
    })
    assert [tuple(row) for row in db.execute("select provider_result_json from action_receipts where id in (?,?) order by id", (first, second))] == [
        ('{"status":"unknown"}',), ('{"status":"sent"}',),
    ]


def test_null_text_null_and_empty_json_remain_distinct():
    db = entity_db()
    action = create_action(db, values={"operation": "send", "parameters_json": "null", "status": None})
    null_receipt = append_action_receipt(db, action["id"], values={"provider_result_json": None})
    text_receipt = append_action_receipt(db, action["id"], values={"provider_result_json": "null"})
    empty_receipt = append_action_receipt(db, action["id"], values={"provider_result_json": "{}"})
    assert get_action(db, action["id"])["parameters_json"] == "null"
    assert get_action(db, action["id"])["status"] is None
    values = [tuple(row) for row in db.execute(
        "select typeof(provider_result_json),provider_result_json from action_receipts where id in (?,?,?) order by id",
        (null_receipt, text_receipt, empty_receipt),
    )]
    assert values == [("null", None), ("text", "null"), ("text", "{}")]
