"""Actual Actions source field catalogs and compact physical declarations."""

import sqlite3

import pytest

from app.action_storage import _action_definitions, action_payloads
from app.storage_payload import SOURCE_ACTION_RECORD_TYPES


def declaration(**overrides):
    flags = dict(include_reply=True, reply_variant="actual_no_memory_fk", include_email=True,
                 include_followup_sync=True, include_skill_config_receipts=True, include_business_sync=True,
                 include_task_delivery=True, include_errors=True, include_feedback=True,
                 include_business_actions=True, include_email_history=True, inline_legacy_owner=True,
                 compact_payload=True)
    return _action_definitions(**(flags | overrides))


def test_complete_actual_layouts_and_explicit_reply_variants():
    actual = action_payloads("actual_no_memory_fk")
    fresh = action_payloads("fresh_memory_fk")
    assert set(actual.layouts) == set(SOURCE_ACTION_RECORD_TYPES.names)
    assert actual.layouts["feedback_iteration_decision_items"].fields == ()
    assert "conversation_single_chat" in actual.layouts["reply_attempts"].fields
    assert "conversation_single_chat" not in fresh.layouts["reply_attempts"].fields
    assert "attempt_id" in actual.layouts["memory_write_events"].fields
    assert "attempt_id" not in fresh.layouts["memory_write_events"].fields
    assert "source_document_id" in actual.layouts["business_task_signals"].fields
    physical = action_payloads("actual_no_memory_fk", physical_provenance=True)
    assert "source_document_id" not in physical.layouts["business_task_signals"].fields
    assert "provenance_id" in physical.layouts["business_task_signals"].fields


def test_actual_shared_columns_read_the_kind_specific_slots_and_exact_shape():
    payloads = action_payloads("actual_no_memory_fk")
    fields = dict(execution_generation="g", stage_index=0, proposal_revision=1,
                  candidate_digest="d", candidate_json="{}", invalidated_at="",
                  invalidation_reason="", created_at="clock")
    packed = payloads.pack("review_candidates", fields)
    with sqlite3.connect(":memory:") as db:
        table, indexes, _triggers = declaration()
        db.execute(table)
        for sql in indexes:
            db.execute(sql)
        db.execute("insert into actions(id,_kind,legacy_key,task_id,consumer_run_id,payload_json) "
                   "values(1,1,'1',1,1,jsonb(?))", (packed,))
        assert db.execute("select candidate_digest,created_at,typeof(payload_json) from actions").fetchone() == ("d", "clock", "blob")
        assert db.execute("select attempt_conversation_id,memory_event_type,error_detail from actions where id=1").fetchone() == (None, None, None)
        values = dict(status="done", lease_owner="owner", lease_expires_at="expiry", result_json='{"receipt":1}',
                      created_at="later", updated_at="updated")
        db.execute("insert into actions(id,_kind,legacy_key,candidate_id,review_id,payload_json) "
                   "values(2,5,'1',1,1,jsonb(?))", (payloads.pack("candidate_executions", values),))
        assert db.execute("select status,created_at from actions where id=2").fetchone() == ("done", "later")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("update actions set payload_json=jsonb_insert(payload_json,'$[#]',1) where id=1")
        assert db.execute("select candidate_digest from actions where id=1").fetchone() == ("d",)


@pytest.mark.parametrize("variant", ("actual_no_memory_fk", "fresh_memory_fk"))
def test_complete_physical_provenance_declaration_compiles(variant):
    with sqlite3.connect(":memory:") as db:
        table, indexes, triggers = declaration(reply_variant=variant, physical_provenance=True)
        db.execute(table)
        for sql in (*indexes, *triggers):
            db.execute(sql)
        assert db.execute("select count(*) from pragma_table_xinfo('actions') where name='payload_json' and type='BLOB'").fetchone()[0] == 1


def attempt_values():
    from app.action_storage import _ATTEMPT_DEFAULTS, _REPLY_FIELDS, _REPLY_TYPES
    fields = dict(_ATTEMPT_DEFAULTS, created_at="clock", updated_at="clock")
    for field in _REPLY_FIELDS["reply_attempts"]:
        if field not in fields:
            fields[field] = 0 if _REPLY_TYPES["reply_attempts"][field] == "integer" else "source"
    return fields


def test_reply_native_real_tag_retains_source_integer_affinity_fraction():
    values = attempt_values() | {"retry_count": 3.5}
    with sqlite3.connect(":memory:") as db:
        db.execute(declaration()[0])
        packed = action_payloads("actual_no_memory_fk").pack("reply_attempts", values)
        db.execute("insert into actions(_kind,legacy_key,payload_json) values(15,'1',jsonb(?))", (packed,))
        assert db.execute("select attempt_retry_count,typeof(attempt_retry_count) from actions").fetchone() == (3.5, "real")


def test_reply_marker_trigger_reads_the_real_slot_and_rejects_original_bodies():
    values = attempt_values()
    with sqlite3.connect(":memory:") as db:
        table, _indexes, triggers = declaration()
        db.execute(table)
        for sql in triggers:
            if "trg_actions_reply_markers_" in sql:
                db.execute(sql)
        payloads = action_payloads("actual_no_memory_fk")
        db.execute("insert into actions(_kind,legacy_key,payload_json) values(15,'1',jsonb(?))",
                   (payloads.pack("reply_attempts", values),))
        malformed = values | {"audit_tool_events_json": '[{"tool":"audit_review","result":"full original body"}]'}
        with pytest.raises(sqlite3.IntegrityError, match="invalid reply audit markers"):
            db.execute("insert into actions(_kind,legacy_key,payload_json) values(15,'2',jsonb(?))",
                       (payloads.pack("reply_attempts", malformed),))
