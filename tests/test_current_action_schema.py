"""The single current Actions declaration keeps actual facts and the Memory FK."""

import sqlite3

import pytest

from app import action_storage as actions
from app.current_database_schema import create_current_database_schema
from app.current_action_schema import (
    create_current_action_schema, current_action_objects,
    current_action_payloads, current_reply_action_projection,
)


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    create_current_database_schema(connection)
    connection.execute("drop table actions")
    create_current_action_schema(connection)
    connection.execute("pragma foreign_keys=on")
    yield connection
    connection.close()


def test_current_declaration_combines_actual_attempt_fields_and_physical_memory_parent(db):
    objects = current_action_objects()
    assert actions._objects(db, "actions") == objects
    columns = {row[1]: row for row in db.execute("pragma table_xinfo(actions)")}
    for field in ("conversation_single_chat", "trigger_create_time", "universal_execution_id", "universal_execution_scope_id"):
        assert "attempt_" + field in columns
        assert field in current_action_payloads().layouts["reply_attempts"].fields
    assert "memory_attempt_ref" in columns
    assert "memory_attempt_id" not in columns
    assert "attempt_id" not in current_action_payloads().layouts["memory_write_events"].fields
    assert any(row[3] == "memory_attempt_ref" and row[2] == "actions"
               for row in db.execute("pragma foreign_key_list(actions)"))


def test_current_reply_writes_preserve_typed_fields_and_parent_public_id(db, monkeypatch):
    monkeypatch.setattr(actions, "_reply_variant_for", lambda _db: pytest.fail("runtime variant probe"))
    attempt = actions.insert_action(db, "reply_attempts", {
        "id": 735, "conversation_id": "chat", "conversation_title": "title",
        "trigger_message_id": "message", "trigger_sender": "sender",
        "trigger_text": "input", "action": "reply", "sensitivity_kind": "none",
        "send_status": "pending", "conversation_single_chat": 1,
        "trigger_create_time": "2026-10-21 09:00:00",
        "universal_execution_id": "735", "universal_execution_scope_id": "735",
    })
    assert attempt.lastrowid == 735
    event = actions.insert_action(db, "memory_write_events", {
        "attempt_id": 735, "event_type": "write", "status": "pending",
    })
    assert event.lastrowid == 1
    physical_parent = db.execute("select id from actions where record_type='reply_attempts'").fetchone()[0]
    assert db.execute("select memory_attempt_ref from actions where record_type='memory_write_events'").fetchone() == (physical_parent,)
    projection = current_reply_action_projection("reply_attempts")
    assert db.execute(f"select conversation_single_chat,trigger_create_time,universal_execution_id,universal_execution_scope_id from ({projection})").fetchone() == (
        1, "2026-10-21 09:00:00", "735", "735")
    assert db.execute(f"select typeof(conversation_single_chat),typeof(universal_execution_id) from ({projection})").fetchone() == ("integer", "text")
    actions.update_action(db, "reply_attempts", 735, {
        "conversation_single_chat": 0, "universal_execution_id": "736", "retry_count": 2.5,
    })
    assert db.execute(f"select conversation_single_chat,universal_execution_id from ({projection})").fetchone() == (0, "736")
    assert db.execute(f"select retry_count,typeof(retry_count) from ({projection})").fetchone() == (2.5, "real")
    memory_projection = current_reply_action_projection("memory_write_events")
    assert db.execute(f"select attempt_id from ({memory_projection})").fetchone() == (735,)
    with pytest.raises(sqlite3.IntegrityError):
        actions.insert_action(db, "memory_write_events", {"attempt_id": 999, "event_type": "missing"})


def test_historical_action_factories_still_have_distinct_original_shapes():
    actual = actions.action_payloads("actual_no_memory_fk", physical_provenance=True)
    fresh = actions.action_payloads("fresh_memory_fk", physical_provenance=True)
    assert "conversation_single_chat" in actual.layouts["reply_attempts"].fields
    assert "conversation_single_chat" not in fresh.layouts["reply_attempts"].fields
    assert "attempt_id" in actual.layouts["memory_write_events"].fields
    assert "attempt_id" not in fresh.layouts["memory_write_events"].fields


def test_current_payload_layout_does_not_select_a_historical_factory(monkeypatch):
    current_action_payloads.cache_clear()
    monkeypatch.setattr(actions, "action_payloads", lambda *_args, **_kwargs: pytest.fail("historical payload factory"))
    payloads = current_action_payloads()
    assert "conversation_single_chat" in payloads.layouts["reply_attempts"].fields
    assert "attempt_id" not in payloads.layouts["memory_write_events"].fields
