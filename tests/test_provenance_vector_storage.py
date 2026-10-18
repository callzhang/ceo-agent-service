"""Body-free Signal writes against the final complete Actions declaration."""

from dataclasses import replace
import hashlib
import sqlite3

import pytest

from app.action_storage import action_payloads
from app.email_canonical_storage import _action_objects
from app.provenance_definitions import SOURCE_FIELDS, SIGNAL_FACT_FIELDS
from app.provenance_storage import create_provenance_schema, insert_signal_version, signal_binding, signals_for_source
from app.business_source_reader import SourceVersionBinding
from app.task_source_documents import source_document_key


@pytest.fixture(params=("actual_no_memory_fk", "fresh_memory_fk"))
def db(request):
    with sqlite3.connect(":memory:") as connection:
        from app.configs_definitions import CONFIG_TABLE_DDL
        from app.configs_definitions import CONFIG_INDEX_DDL
        from app.task20_definitions import TASK20_TABLE_DDL, TASK20_INDEX_DDL
        from app.runs11_current_schema import current_run11_objects
        from app.business_definitions import _definitions
        from app.email_canonical_definitions import EMAILS_TABLE_SQL, EMAILS_INDEX_SQL
        from app.schedules_definitions import SCHEDULE_DDL
        connection.execute(SCHEDULE_DDL)
        for table, indexes in ((CONFIG_TABLE_DDL, CONFIG_INDEX_DDL),
                               (TASK20_TABLE_DDL, TASK20_INDEX_DDL),
                               (EMAILS_TABLE_SQL, EMAILS_INDEX_SQL)):
            connection.execute(table)
            for sql in indexes:
                connection.execute(sql)
        for sql in current_run11_objects().values():
            connection.execute(sql)
        business_table, business_indexes, _ = _definitions(include_candidates=True, include_business_actions=True)
        connection.execute(business_table)
        for sql in business_indexes:
            connection.execute(sql)
        create_provenance_schema(connection)
        objects = _action_objects(request.param, target=True, inline_legacy_owner=True,
                                  physical_provenance=True, compact_payload=True)
        for category in ("table", "index", "trigger"):
            for obj in objects:
                if obj["type"] == category:
                    connection.execute(obj["sql"])
        connection.execute("begin")
        yield connection


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
    return action_payloads("actual_no_memory_fk", physical_provenance=True).layouts["business_task_signals"].unpack(values)


def test_final_vector_inline_promotion_preserves_facts_and_public_ids(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="a", conversation_title="first", created_at="before")
    assert signal_binding(db, first) == binding
    inline = raw(db, first)
    assert inline["provenance_id"] is None
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
        assert all(payload[name] is None for name in SOURCE_FIELDS)
    assert {name: raw(db, first)[name] for name in SIGNAL_FACT_FIELDS} == {name: inline[name] for name in SIGNAL_FACT_FIELDS}
    assert db.execute("select distinct typeof(payload_json),record_type from actions").fetchall() == [("blob", "business_task_signals")]
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_final_vector_failed_second_owner_restores_inline_and_transaction(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="duplicate")
    before = db.execute("select payload_json from actions").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        insert_signal_version(db, binding, dedupe_key="duplicate")
    assert db.in_transaction
    db.commit()
    assert db.execute("select count(*) from provenance").fetchone()[0] == 0
    assert db.execute("select payload_json from actions").fetchone()[0] == before
    assert signal_binding(db, first) == binding


def test_final_vector_promotion_does_not_allow_other_field_changes(db, binding):
    first = insert_signal_version(db, binding, dedupe_key="one")
    insert_signal_version(db, binding, dedupe_key="two")
    layout = action_payloads("actual_no_memory_fk", physical_provenance=True).layouts["business_task_signals"]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("update actions set payload_json=jsonb_set(payload_json,?,?) where business_signal_public_id=?",
                   (layout.path("conversation_title"), "changed", first))
    assert signal_binding(db, first) == binding
