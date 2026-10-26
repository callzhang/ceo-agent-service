"""Body-free Signal writes against the final complete Actions declaration."""

from dataclasses import replace
import hashlib
import sqlite3

import pytest

from app.action_storage import action_payloads
from app.current_action_schema import current_action_payloads
from app.provenance_definitions import SIGNAL_FIELDS, SIGNAL_FACT_FIELDS
from app.provenance_storage import insert_signal_version, signal_binding, signals_for_source
from app.business_source_reader import SourceVersionBinding
from app.store import AutoReplyStore
from app.task_source_documents import source_document_key


@pytest.fixture
def db(tmp_path):
    store = AutoReplyStore(tmp_path / "provenance-vector.sqlite3")
    with store._connect() as connection:
        connection.execute("begin")
        yield connection
        connection.rollback()


@pytest.fixture
def binding():
    source = dict(source_type="ai_minutes", source_ref="source-1#todos-sha256=" + "a" * 64,
                  source_time="clock", conversation_id="chat", author_user_id="author",
                  author_name="Author", author_kind="human")
    body = "source α"
    return SourceVersionBinding(**source, identity_key=source_document_key(**source, evidence_text=body),
                                body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                                body_utf8_bytes=len(body.encode()),
                                provider_locator_json='{"kind":"ai_minutes","minutes_id":"source-1","components":["meeting","todos"]}')


def raw(db, public_id):
    values = db.execute("select json(payload_json) from actions where business_signal_public_id=?", (public_id,)).fetchone()[0]
    return current_action_payloads().layouts["business_task_signals"].unpack(values)


def test_current_vector_reuses_parent_without_editing_first_signal(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="a", conversation_title="first", created_at="before")
    assert signal_binding(db, first) == binding
    original = db.execute(
        "select payload_json from actions where business_signal_public_id=?", (first,)
    ).fetchone()[0]
    second = insert_signal_version(db, replace(binding, provider_locator_json=None), dedupe_key="b", created_at="second")
    third = insert_signal_version(db, binding, dedupe_key="c", created_at="third")
    assert (first, second, third) == (1, 2, 3)
    assert db.execute("select count(*) from provenance").fetchone()[0] == 1
    assert set(signals_for_source(db, source_type=binding.source_type, source_ref=binding.source_ref,
                                  source_time=binding.source_time)) == {1, 2, 3}
    assert [signal_binding(db, item) for item in (first, second, third)] == [binding] * 3
    for item in (first, second, third):
        payload = raw(db, item)
        assert payload["provenance_id"] == 1
    assert db.execute(
        "select payload_json from actions where business_signal_public_id=?", (first,)
    ).fetchone()[0] == original
    assert {name: raw(db, first)[name] for name in SIGNAL_FACT_FIELDS} == {
        "conversation_title": "first", "context_json": "{}",
        "dedupe_key": "a", "created_at": "before",
    }
    assert [tuple(row) for row in db.execute(
        "select distinct typeof(payload_json),record_type from actions"
    )] == [("blob", "business_task_signals")]
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_current_vector_failed_second_owner_restores_parent_and_transaction(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="duplicate")
    before = db.execute("select payload_json from actions").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        insert_signal_version(db, binding, dedupe_key="duplicate")
    assert db.in_transaction
    db.commit()
    assert db.execute("select count(*) from provenance").fetchone()[0] == 1
    assert db.execute("select payload_json from actions").fetchone()[0] == before
    assert signal_binding(db, first) == binding


def test_current_vector_does_not_allow_field_changes(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="one")
    layout = current_action_payloads().layouts["business_task_signals"]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("update actions set payload_json=jsonb_set(payload_json,?,?) where business_signal_public_id=?",
                   (layout.path("conversation_title"), "changed", first))
    assert signal_binding(db, first) == binding


def test_historical_signal_payload_factories_keep_old_seventeen_slots():
    for variant in ("actual_no_memory_fk", "fresh_memory_fk"):
        assert action_payloads(
            variant, physical_provenance=True,
        ).layouts["business_task_signals"].fields == SIGNAL_FIELDS
