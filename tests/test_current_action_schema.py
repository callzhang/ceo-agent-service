"""The single current Actions declaration keeps actual facts and the Memory FK."""

import sqlite3

import pytest

from app import action_storage as actions
from app.current_database_schema import create_current_database_schema
from app.current_action_schema import (
    create_current_action_schema, current_action_objects,
    current_action_payloads,
)


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    create_current_database_schema(connection)
    connection.execute("drop table action_observers")
    connection.execute("drop table action_receipts")
    connection.execute("drop table action_entity_identity_map")
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

