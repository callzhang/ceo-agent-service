"""Actual source-shaped Action DAOs against the final full declaration."""

import sqlite3

import pytest

from tests import test_provenance_vector_storage as current_signal_storage
from app.current_action_schema import current_action_payloads
from app.action_storage import (
    insert_error_action, error_set_parts, insert_action, update_action,
    insert_sync_action, update_sync_action,
    insert_task_delivery_action, task_delivery_set_parts,
)
from app.run_entity_storage import create_run
from app.task_entity_storage import create_task


db = current_signal_storage.db
current_signal_db = db


def seed_task(db):
    task = create_task(db, values={
        "kind": "reply", "status": "pending", "execution_generation": "generation",
        "created_at": "clock", "updated_at": "clock",
    })
    run = create_run(db, values={
        "task_id": task["id"], "execution_generation": "generation",
        "role": "consumer", "status": "completed",
    })
    return task, run


def payload(db, kind):
    encoded = db.execute("select json(payload_json) from actions where record_type=?", (kind,)).fetchone()[0]
    return current_action_payloads().layouts[kind].unpack(encoded)


def test_error_actual_insert_and_sql_set_remain_blob_and_native_scalar(db):
    row = insert_error_action(db, dict(kind="worker", detail=b"\x00\xff"))
    assert row.lastrowid == 1
    sql, args = error_set_parts({"resolution": "recovered"}, update_sql={"resolved_at": "current_timestamp"})
    assert db.execute("update actions set " + sql + " where record_type='errors'", args).rowcount == 1
    assert tuple(db.execute("select error_detail,error_resolution,typeof(payload_json) from actions").fetchone()) == (b"\x00\xff", "recovered", "blob")
    assert payload(db, "errors")["resolved_at"]


def test_reply_actual_dao_insert_update_and_parent_reference(db):
    seed_task(db)
    values = dict(conversation_id="chat", conversation_title="title", trigger_message_id="message", trigger_sender="author", trigger_text="input", action="reply", sensitivity_kind="none", send_status="pending", draft_reply_text="draft")
    row = insert_action(db, "reply_attempts", values)
    assert row.lastrowid == 1
    update_action(db, "reply_attempts", 1, {"draft_reply_text": "revision", "retry_count": 3.5})
    event = insert_action(db, "memory_write_events", {"attempt_id": 1, "event_type": "reply"})
    assert event.lastrowid == 1
    assert tuple(db.execute("select attempt_draft_reply_text,attempt_retry_count from actions where record_type='reply_attempts'").fetchone()) == ("revision", 3.5)
    assert [tuple(row) for row in db.execute("select distinct typeof(payload_json) from actions")] == [("blob",)]


def test_sync_dao_preserves_defaults_and_guarded_update_in_jsonb(db):
    current_fields = current_action_payloads().layouts["work_updates"].fields
    assert "confidence" not in current_fields
    assert "merge_reason" not in current_fields
    row = insert_sync_action(db, "work_updates", dict(project_id=999, source_type="synthetic", source_ref="source", summary="before"))
    assert row.lastrowid == 1
    original = payload(db, "work_updates")
    assert "confidence" not in original
    assert "merge_reason" not in original
    assert original["summary"] == "before"
    assert original["changes_json"] == "{}"
    assert original["created_at"]
    assert update_sync_action(db, "work_updates", 1, {"summary": "after"}, where_sql="summary=?", parameters=("before",)).rowcount == 1
    assert update_sync_action(db, "work_updates", 1, {"summary": "wrong"}, where_sql="summary=?", parameters=("before",)).rowcount == 0
    updated = payload(db, "work_updates")
    assert updated["summary"] == "after"
    assert updated["changes_json"] == "{}"
    assert updated["created_at"] == original["created_at"]
    assert tuple(db.execute("select typeof(payload_json) from actions").fetchone()) == ("blob",)


def test_delivery_dao_and_sql_expression_parts_keep_slots(db):
    task, run = seed_task(db)
    row = insert_task_delivery_action(db, "task_memory_write_events", dict(reply_task_id=task["id"], execution_generation="generation", consumer_run_id=run["id"]))
    assert row.lastrowid == 1
    sql, args = task_delivery_set_parts("task_memory_write_events", {"status": "completed"}, update_sql={"attempts": "td_attempts+1"})
    db.execute("update actions set " + sql + " where record_type='task_memory_write_events'", args)
    assert tuple(db.execute("select td_attempts,td_status,typeof(payload_json) from actions").fetchone()) == (1, "completed", "blob")


def test_direct_generated_type_write_remains_forbidden(db):
    with pytest.raises(sqlite3.OperationalError, match="generated"):
        db.execute("insert into actions(record_type,legacy_key,payload_json) values('errors','1','{}')")


def test_feedback_dao_upsert_resolve_and_reopen_final_slots(db):
    from app.action_feedback_storage import upsert_feedback_event, resolve_feedback_events, reopen_feedback_event
    first = upsert_feedback_event(db, {"key": "event", "feedback_token": "token", "comment": "before"})
    assert upsert_feedback_event(db, {"key": "event", "feedback_token": "token", "comment": "after"}) == first
    assert resolve_feedback_events(db, ["event"], "resolved") == 1
    assert reopen_feedback_event(db, "event") == 1
    assert tuple(db.execute("select fb_event_comment,fb_event_resolved_at,typeof(payload_json) from actions").fetchone()) == ("after", "", "blob")


def test_history_dao_retains_typed_probability_and_terminal_result(db):
    from app.action_email_history_storage import insert_email_history, update_email_operation
    from app.action_email_history_definitions import HISTORY_FIELDS
    kind = "email_historical_operations"
    fields = dict.fromkeys(HISTORY_FIELDS[kind], "value") | {"classification_id": 1,
        "threshold": .25, "probability": .875, "important": 1, "status": "processing", "action_ids_json": "[]"}
    assert insert_email_history(db, kind, fields) == 1
    assert update_email_operation(db, model_id="value", stable_message_identity="value", updated_at="later", outcome="sent") == 1
    assert tuple(db.execute("select email_operation_status,email_operation_action_outcome,email_operation_probability,typeof(payload_json) from actions").fetchone()) == ("terminal", "sent", .875, "blob")


def test_skill_load_receipt_retains_parent_and_reconstructable_result(db):
    from app.configs_storage import insert_config
    from app.action_skill_config_storage import insert_skill_receipt, load_receipt_projection
    insert_config(db, "runtime_skill_configs", dict(id=1, parent_id=None, status="pending_restart", created_at="clock"))
    assert insert_skill_receipt(db, "runtime_skill_load_receipts", dict(config_id=1, pid=123, loaded_json="{}")) == 1
    physical = db.execute("select id from actions").fetchone()[0]
    result = load_receipt_projection(db, physical)
    assert (result.id, result.config_id, result.pid, result.loaded_json) == (1, 1, 123, "{}")
    assert tuple(db.execute("select typeof(payload_json) from actions").fetchone()) == ("blob",)


def test_business_evidence_dao_upsert_and_date_copy_keep_reference_ids(current_signal_db):
    db = current_signal_db
    from tests.test_provenance_vector_storage import binding as binding_fixture
    from app.provenance_storage import insert_signal_version
    from app.business_storage import insert_business
    from app.action_business_storage import (
        insert_business_action, update_business_action,
        upsert_business_action_anchor_link, copy_business_action_date_evidence,
    )
    binding = binding_fixture.__wrapped__()
    signal = insert_signal_version(db, binding, dedupe_key="signal")
    insert_business(db, "anchor", dict(id=1, anchor_type="project", anchor_ref="anchor:1", title="Anchor"))
    for task_id in (2, 3):
        insert_business(db, "task", dict(id=task_id, title=f"Task {task_id}", stage="candidate"))
    link = upsert_business_action_anchor_link(db, task_id=2, anchor_id=1, status="proposed", active=1, evidence_signal_id=signal, reason="before")
    assert upsert_business_action_anchor_link(db, task_id=2, anchor_id=1, status="proposed", active=1, evidence_signal_id=signal, reason="after") == link
    assert update_business_action(db, "business_task_anchor_links", link, {"reason": "updated"}) == 1
    evidence = insert_business_action(db, "business_task_date_evidence", dict(task_id=2, date_type="next_check_at", raw_phrase="Friday", source_signal_id=signal, actor_kind="human"))
    assert evidence.rowcount == 1
    assert copy_business_action_date_evidence(db, source_task_id=2, target_task_id=3) == 1
    assert copy_business_action_date_evidence(db, source_task_id=2, target_task_id=3) == 0
    assert tuple(db.execute("select ba_task_anchor_links_reason,typeof(payload_json) from actions where record_type='business_task_anchor_links'").fetchone()) == ("updated", "blob")
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_compact_action_type_change_preserves_existing_immutability(db):
    from app.storage_payload import ACTION_RECORD_TYPES

    payloads = current_action_payloads()
    row = insert_error_action(db, dict(kind="worker", detail="failed"))
    physical_id = row.lastrowid
    feedback_values = dict.fromkeys(payloads.layouts["feedback_events"].fields, "")
    with pytest.raises(sqlite3.IntegrityError, match="action record type is immutable"):
        db.execute("update actions set _kind=?,payload_json=jsonb(?) where id=?",
                   (ACTION_RECORD_TYPES.code("feedback_events"),
                    payloads.pack("feedback_events", feedback_values), physical_id))
