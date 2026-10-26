"""One final Action/provenance transition and current-data inverse."""

from dataclasses import replace
import hashlib
import json
import math
import sqlite3

import pytest

from tests.test_provenance_vector_storage import db as db, binding as binding
from app import action_storage as actions
from app.email_canonical_storage import _action_objects
from app.provenance_definitions import SOURCE_FIELDS
from app.provenance_storage import signal_binding
from app.task_source_documents import source_document_key


def test_reply_inverse_finite_real_json_keeps_sqlite_value():
    from app.final_action_schema import _source_scalar_json
    values = (.68, math.nextafter(.68, 1), math.ulp(0.0),
              math.nextafter(float("inf"), 0), -math.ulp(0.0), -0.0)
    with sqlite3.connect(":memory:") as connection:
        for value in values:
            encoded = actions._encoded_native_sqlite_value(value)
            restored = connection.execute("select json_extract(?, '$')", (
                _source_scalar_json(encoded),)).fetchone()[0]
            assert restored == value
            assert float(restored).hex() == value.hex()


def version(binding, body, **changes):
    fields = {name: changes.get(name, getattr(binding, name)) for name in SOURCE_FIELDS[:-5]}
    return replace(binding, **changes, identity_key=source_document_key(**fields, evidence_text=body),
                   body_sha256=hashlib.sha256(body.encode()).hexdigest(), body_utf8_bytes=len(body.encode()))


def source(db, binding):
    from app.final_action_schema import BUSINESS_SOURCE_DOCUMENT_SCHEMA
    db.commit()
    db.execute("pragma foreign_keys=off")
    db.execute("begin")
    variant = actions._reply_variant_for(db)
    db.execute("drop table actions")
    db.execute("drop table provenance")
    for sql in BUSINESS_SOURCE_DOCUMENT_SCHEMA:
        db.execute(sql)
    objects = _action_objects(variant, target=True, inline_legacy_owner=True)
    for category in ("table", "index", "trigger"):
        for obj in objects:
            if obj["type"] == category:
                db.execute(obj["sql"])
    body = "source α"
    other = version(binding, "source β", source_time="other")
    for doc_id, b, text in ((1, binding, body), (2, other, "source β")):
        db.execute("insert into business_source_documents(id,identity_key,body,created_at) values(?,?,?,?)",
                   (doc_id, b.identity_key, text, f"document-{doc_id}"))
    from app.action_business_definitions import BUSINESS_ACTION_FIELDS
    for physical_id, public_id, doc_id, b in ((101, 10, 1, binding), (201, 20, 1, binding), (301, 30, 2, other)):
        data = {name: getattr(b, name) for name in SOURCE_FIELDS[:-5]}
        data |= dict(source_document_id=doc_id, conversation_title=f"Signal {public_id}", context_json='{"fact":1}',
                     dedupe_key=f"signal-{public_id}", created_at="signal-clock")
        assert set(data) == set(BUSINESS_ACTION_FIELDS["business_task_signals"]) - {"id"}
        db.execute("insert into actions(id,record_type,legacy_key,payload_json) values(?,'business_task_signals',?,?)",
                   (physical_id, str(public_id), actions._native_payload(data)))
    actions._email_lock(db)
    db.execute("update sqlite_sequence set seq=80 where name='actions:business_task_signals'")
    db.execute("update sqlite_sequence set seq=40 where name='business_source_documents'")
    db.commit()
    db.execute("begin")
    return {binding.identity_key: binding, other.identity_key: other}, {binding.identity_key: body, other.identity_key: "source β"}


def test_final_transition_removes_originals_and_preserves_signal_identities(db, binding):
    from app.final_action_schema import migrate_final_actions, final_action_schema_is_current
    bindings, bodies = source(db, binding)
    before = db.execute("select id,legacy_key from actions order by id").fetchall()
    metadata = migrate_final_actions(db, bindings=bindings)
    assert final_action_schema_is_current(db)
    assert db.execute("select id,legacy_key from actions order by id").fetchall() == before
    assert not db.execute("select 1 from sqlite_schema where name='business_source_documents'").fetchone()
    assert db.execute("select count(*) from provenance").fetchone()[0] == 1
    assert signal_binding(db, 10) == signal_binding(db, 20) == binding
    assert signal_binding(db, 30) == bindings[next(key for key in bindings if key != binding.identity_key)]
    assert all(text not in json.dumps(metadata) for text in bodies.values())
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_unproved_source_cannot_be_retired_and_inverse_needs_exact_body(db, binding):
    from app.final_action_schema import migrate_final_actions, restore_final_actions, final_action_schema_is_current
    bindings, _bodies = source(db, binding)
    with pytest.raises(ValueError, match="binding"):
        migrate_final_actions(db, bindings={})
    assert db.execute("select count(*) from business_source_documents").fetchone()[0] == 2
    metadata = migrate_final_actions(db, bindings=bindings)
    before = db.execute("select id,payload_json from actions order by id").fetchall()
    with pytest.raises(ValueError, match="body"):
        restore_final_actions(db, metadata, read_body=lambda item: "wrong")
    assert final_action_schema_is_current(db)
    assert db.execute("select id,payload_json from actions order by id").fetchall() == before


def test_existing_canonical_email_guards_survive_action_rebuild(db, binding):
    from app.final_action_schema import migrate_final_actions
    from app.email_canonical_definitions import EMAILS_TRIGGER_SQL
    bindings, _bodies = source(db, binding)
    for sql in EMAILS_TRIGGER_SQL:
        db.execute(sql)
    before = actions._objects(db, "emails")
    migrate_final_actions(db, bindings=bindings)
    assert actions._objects(db, "emails") == before


def test_public_constructor_selects_complete_final_declaration(db):
    from app.final_action_schema import final_action_schema_is_current
    variant = actions._reply_variant_for(db)
    db.execute("drop table actions")
    options = dict(include_reply=True, reply_variant=variant, include_email=True,
        include_skill_config_receipts=True, include_followup_sync=True, include_business_sync=True,
        include_task_delivery=True, include_errors=True, include_feedback=True,
        include_business_actions=True, include_email_history=True, inline_legacy_owner=True,
        canonical_email=True, physical_provenance=True, compact_payload=True)
    actions.create_action_schema(db, **options)
    assert actions.action_schema_is_current(db, **options)
    assert final_action_schema_is_current(db)
    before = actions._objects(db, "actions")
    index = next(obj for obj in before if obj["type"] == "index")
    db.execute('drop index "' + index["name"] + '"')
    assert not actions.action_schema_is_current(db, **options)
    actions.create_action_schema(db, **options)
    assert actions._objects(db, "actions") == before
    assert final_action_schema_is_current(db)
