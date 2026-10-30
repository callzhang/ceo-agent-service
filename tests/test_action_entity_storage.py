import sqlite3

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


@pytest.mark.parametrize("missing", ["action_receipts", "action_observers", "both"])
def test_existing_action_schema_with_missing_children_is_rejected_without_repair(missing):
    db = entity_db()
    tables = ("action_receipts", "action_observers") if missing == "both" else (missing,)
    for table in tables:
        db.execute(f"drop table {table}")
    before = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_master order by type,name")]
    with pytest.raises(ValueError, match="unsupported current Actions declaration"):
        create_current_action_schema(db)
    after = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_master order by type,name")]
    assert after == before


def test_create_claim_result_uses_one_action():
    db = entity_db()
    created = create_action(db, values={
        "operation": "send", "prepared_body": "{}", "status": "pending",
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
    left = create_action(db, values={"operation": "send", "prepared_body": '{"body":"same"}', "external_action_key": "effect-a"})
    right = create_action(db, values={"operation": "send", "prepared_body": '{"body":"same"}', "external_action_key": "effect-b"})
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
    })
    assert [tuple(row) for row in db.execute("select provider_result_json from action_receipts where id in (?,?) order by id", (first, second))] == [
        ('{"status":"unknown"}',), ('{"status":"sent"}',),
    ]


def test_external_result_projection_uses_direct_canonical_binding():
    from app.action_storage import NATIVE_EXTERNAL_ACTION_RESULT_SQL as EXTERNAL_ACTION_RESULT_SQL

    db = entity_db()
    action = create_action(db, values={
        "operation": "message.send", "external_action_key": "external-key",
        "business_object_key": "business", "action_identity": "reply",
    })
    from tests.support.native_run_rows import insert_native_run
    from tests.support.native_task_seed import seed_native_task

    task_id = 1
    seed_native_task(db, task_id, "reply", "pending")
    agent_run_id = insert_native_run(db, {
        "task_id": task_id, "execution_generation": "generation", "role": "audit",
        "status": "completed",
    })
    original = append_action_receipt(db, action["id"], values={
        "operation": "message.send", "target": '{"id":"target"}',
        "provider_result_json": '{"receipt":"original"}', "result_digest": "original",
        "agent_run_id": agent_run_id,
    })
    append_action_receipt(db, action["id"], values={
        "operation": "message.send", "target": '{"id":"other"}',
        "provider_result_json": '{"receipt":"independent"}', "result_digest": "independent",
        "agent_run_id": agent_run_id,
    })
    correction = append_action_receipt(db, action["id"], values={
        "operation": "message.send", "target": '{"id":"target"}',
        "provider_result_json": '{"receipt":"corrected"}', "result_digest": "corrected",
        "agent_run_id": agent_run_id,
    })
    sent_fact = append_action_receipt(db, action["id"], values={
        "provider_receipt_id": original, "operation": "message.send",
        "target": '{"id":"target"}', "result_digest": "original",
        "agent_run_id": agent_run_id, "observed_at": "sent-clock",
        "created_at": "canonical-root-clock",
    })
    binding = append_action_receipt(db, action["id"], values={
        "provider_receipt_id": correction, "operation": "message.send",
        "target": '{"id":"target"}', "result_digest": "original",
        "agent_run_id": agent_run_id, "created_at": "canonical-root-clock",
        "observed_at": "canonical-observed-clock",
    })
    from app.action_entity_storage import record_action_result
    assert record_action_result(db, action["id"], expected={"delivery_receipt_id": None},
                                changes={"delivery_receipt_id": sent_fact}) is not None

    result = db.execute(
        f"select provider_result_json,provider_receipt_id,target_identifiers_json "
        f"from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
        ("external-key",),
    ).fetchone()
    assert tuple(result) == ('{"receipt":"corrected"}', correction, '{"id":"target"}')
    assert db.execute(
        "select send_result_json from (select r.provider_result_json as send_result_json "
        "from actions a join action_receipts s on s.id=a.action_delivery_receipt_id "
        "join action_receipts r on r.id=s.provider_receipt_id where a.id=?)", (action["id"],)
    ).fetchone()[0] == '{"receipt":"original"}'
    assert tuple(db.execute(
        "select provider_receipt_id,created_at,agent_run_id from action_receipts where id=?",
        (binding,),
    ).fetchone()) == (correction, "canonical-root-clock", agent_run_id)


def test_execution_slot_has_one_current_observation_but_provider_versions_remain_distinct():
    db = entity_db()
    # The slot is unique on (execution_run_id, action_index); the Run adoption
    # chain its foreign key points at is not what this test is about.
    db.execute("pragma foreign_keys=off")
    action = create_action(db, values={"operation": "send", "external_action_key": "effect"})
    first = append_action_receipt(db, action["id"], values={
        "status": "dispatched", "execution_run_id": 20, "action_index": 0,
    })
    second_action = create_action(
        db, values={"operation": "send", "external_action_key": "other-effect"},
    )
    with pytest.raises(sqlite3.IntegrityError):
        append_action_receipt(db, action["id"], values={
            "status": "verified", "execution_run_id": 20, "action_index": 0,
        })
    with pytest.raises(sqlite3.IntegrityError):
        append_action_receipt(db, second_action["id"], values={
            "status": "verified", "execution_run_id": 20, "action_index": 0,
        })
    assert db.execute(
        "select count(*) from action_receipts where execution_run_id=20 and action_index=0"
    ).fetchone()[0] == 1
    old_version = append_action_receipt(db, action["id"], values={
        "provider_result_json": '{"accepted":false}', "result_digest": "old",
    })
    new_version = append_action_receipt(db, action["id"], values={
        "provider_result_json": '{"accepted":true}', "result_digest": "new",
    })
    assert old_version != new_version != first


def test_null_text_null_and_empty_json_remain_distinct():
    db = entity_db()
    action = create_action(db, values={"operation": "send", "prepared_body": "null", "status": None})
    null_receipt = append_action_receipt(db, action["id"], values={"provider_result_json": None})
    text_receipt = append_action_receipt(db, action["id"], values={"provider_result_json": "null"})
    empty_receipt = append_action_receipt(db, action["id"], values={"provider_result_json": "{}"})
    assert get_action(db, action["id"])["prepared_body"] == "null"
    assert get_action(db, action["id"])["status"] is None
    values = [tuple(row) for row in db.execute(
        "select typeof(provider_result_json),provider_result_json from action_receipts where id in (?,?,?) order by id",
        (null_receipt, text_receipt, empty_receipt),
    )]
    assert values == [("null", None), ("text", "null"), ("text", "{}")]


def test_current_receipt_accepts_run_owner_and_rejects_predecessor_owner():
    db = entity_db()
    action = create_action(db, values={"operation": "send"})
    receipt_id = append_action_receipt(db, action["id"], values={"execution_run_id": None})
    assert db.execute("select execution_run_id from action_receipts where id=?", (receipt_id,)).fetchone()[0] is None
    with pytest.raises(ValueError, match="execution_id"):
        append_action_receipt(db, action["id"], values={"execution_id": None})
