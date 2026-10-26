"""Actual source-shaped Action DAOs against the final full declaration."""

import sqlite3

import pytest

from tests.test_provenance_vector_storage import db as db
from app.action_storage import (
    insert_error_action, error_set_parts, action_payloads,
)
from app.task8_storage import register_task8_functions, task8_insert_parts
from app.run_storage import insert_run


def seed_task(db):
    register_task8_functions(db)
    parts = task8_insert_parts("reply_tasks", dict(legacy_key="1", conversation_id="chat",
        conversation_title="title", single_chat=0, trigger_message_id="message", trigger_create_time="time", trigger_sender="author"))
    db.execute(f"insert into tasks({parts.columns}) values({parts.values})", parts.parameters)
    insert_run(db, "agent_runs", dict(id=1, task_id=1, execution_generation="generation", role="consumer"))


def payload(db, kind):
    variant = "fresh_memory_fk" if db.execute("select 1 from pragma_table_xinfo('actions') where name='memory_attempt_ref'").fetchone() else "actual_no_memory_fk"
    encoded = db.execute("select json(payload_json) from actions where record_type=?", (kind,)).fetchone()[0]
    return action_payloads(variant, physical_provenance=True).layouts[kind].unpack(encoded)


def test_error_actual_insert_and_sql_set_remain_blob_and_native_scalar(db):
    register_task8_functions(db)
    row = insert_error_action(db, dict(kind="worker", detail=b"\x00\xff"))
    assert row.lastrowid == 1
    sql, args = error_set_parts({"resolution": "recovered"}, update_sql={"resolved_at": "current_timestamp"})
    assert db.execute("update actions set " + sql + " where record_type='errors'", args).rowcount == 1
    assert db.execute("select error_detail,error_resolution,typeof(payload_json) from actions").fetchone() == (b"\x00\xff", "recovered", "blob")
    assert payload(db, "errors")["resolved_at"]


def test_direct_generated_type_write_remains_forbidden(db):
    with pytest.raises(sqlite3.OperationalError, match="generated"):
        db.execute("insert into actions(record_type,legacy_key,payload_json) values('errors','1','{}')")


def test_feedback_dao_upsert_resolve_and_reopen_final_slots(db):
    from app.action_feedback_storage import upsert_feedback_event, resolve_feedback_events, reopen_feedback_event
    first = upsert_feedback_event(db, {"key": "event", "feedback_token": "token", "comment": "before"})
    assert upsert_feedback_event(db, {"key": "event", "feedback_token": "token", "comment": "after"}) == first
    assert resolve_feedback_events(db, ["event"], "resolved") == 1
    assert reopen_feedback_event(db, "event") == 1
    assert db.execute("select fb_event_comment,fb_event_resolved_at,typeof(payload_json) from actions").fetchone() == ("after", "", "blob")


def test_history_dao_retains_typed_probability_and_terminal_result(db):
    from app.action_email_history_storage import insert_email_history, update_email_operation
    from app.action_email_history_definitions import HISTORY_FIELDS
    kind = "email_historical_operations"
    fields = dict.fromkeys(HISTORY_FIELDS[kind], "value") | {"classification_id": 1,
        "threshold": .25, "probability": .875, "important": 1, "status": "processing", "action_ids_json": "[]"}
    assert insert_email_history(db, kind, fields) == 1
    assert update_email_operation(db, model_id="value", stable_message_identity="value", updated_at="later", outcome="sent") == 1
    assert db.execute("select email_operation_status,email_operation_action_outcome,email_operation_probability,typeof(payload_json) from actions").fetchone() == ("terminal", "sent", .875, "blob")


def test_skill_load_receipt_retains_parent_and_reconstructable_result(db):
    from app.configs_storage import insert_config
    from app.action_skill_config_storage import insert_skill_receipt, load_receipt_projection
    insert_config(db, "runtime_skill_configs", dict(id=1, parent_id=None, status="pending_restart", created_at="clock"))
    assert insert_skill_receipt(db, "runtime_skill_load_receipts", dict(config_id=1, pid=123, loaded_json="{}")) == 1
    physical = db.execute("select id from actions").fetchone()[0]
    result = load_receipt_projection(db, physical)
    assert (result.id, result.config_id, result.pid, result.loaded_json) == (1, 1, 123, "{}")
    assert db.execute("select typeof(payload_json) from actions").fetchone() == ("blob",)


def test_business_evidence_dao_upsert_and_date_copy_keep_reference_ids(db):
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
    assert db.execute("select ba_task_anchor_links_reason,typeof(payload_json) from actions where record_type='business_task_anchor_links'").fetchone() == ("updated", "blob")
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_compact_action_type_change_preserves_existing_immutability(db):
    from app.action_storage import _reply_variant_for
    from app.storage_payload import ACTION_RECORD_TYPES

    register_task8_functions(db)
    payloads = action_payloads(_reply_variant_for(db), physical_provenance=True)
    row = insert_error_action(db, dict(kind="worker", detail="failed"))
    physical_id = row.lastrowid
    feedback_values = dict.fromkeys(payloads.layouts["feedback_events"].fields, "")
    with pytest.raises(sqlite3.IntegrityError, match="action record type is immutable"):
        db.execute("update actions set _kind=?,payload_json=jsonb(?) where id=?",
                   (ACTION_RECORD_TYPES.code("feedback_events"),
                    payloads.pack("feedback_events", feedback_values), physical_id))
