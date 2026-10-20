"""Frozen 19.6 declarations and exact byte/type inverse for both Reply FK variants."""
import hashlib
import json

import pytest

from app import action_storage as actions
from app.shared_action_definitions import SHARED_RECORD_TYPES, SPECIALIZED_RECORD_TYPES, SPECIALIZED_SOURCE_TO_TARGET
from app.shared_action_schema import shared_action_objects, migrate_shared_actions
from app.specialized_action_schema import migrate_specialized_actions, restore_specialized_actions, specialized_action_objects
from tests.test_provenance_vector_storage import db as db
from tests.test_shared_action_schema import _source19_5
from tests.test_model_action_schema import _snapshot

SOURCE_HASHES = {
    'actual_no_memory_fk': 'd7f73a0780b35b5e8ce816f6701d6d9000214d8c3821d33c720490af98b5e498',
    'fresh_memory_fk': '986267b56a49e75c52a53e44bbc0ef64df38b9863d66b8d8f9b2df94416ff1c7',
}


def test_frozen_source_and_exact_specialized_layouts():
    for variant, digest in SOURCE_HASHES.items():
        assert hashlib.sha256(json.dumps(shared_action_objects(variant),sort_keys=True,separators=(',', ':')).encode()).hexdigest() == digest
        source = actions.action_payloads(variant, selection_references=True, execution_owner=True, shared_lifecycle=True)
        target = actions.action_payloads(variant, selection_references=True, execution_owner=True, shared_lifecycle=True, specialized_claims=True)
        assert source.layouts == target.layouts
        assert {SPECIALIZED_RECORD_TYPES.code(kind) for kind in SPECIALIZED_SOURCE_TO_TARGET} == {68, 69}
        assert {SHARED_RECORD_TYPES.code(kind) for kind in SPECIALIZED_SOURCE_TO_TARGET} == {28, 21}


def test_both_variants_exact_forward_inverse_and_empty_claim_support(db):
    _source19_5(db)
    migrate_shared_actions(db)
    source = _snapshot(db)
    metadata = migrate_specialized_actions(db)
    assert metadata == {'reply_variant': actions._reply_variant_for(db)}
    assert actions._objects(db, 'actions') == specialized_action_objects(metadata['reply_variant'])
    restore_specialized_actions(db, metadata)
    assert _snapshot(db) == source
    migrate_specialized_actions(db)
    assert actions._objects(db, 'actions') == specialized_action_objects(metadata['reply_variant'])


def test_unknown_source_refuses_without_first_write(db):
    _source19_5(db)
    migrate_shared_actions(db)
    db.execute('create index unknown_claims_index on actions(email_owner_id)')
    before = _snapshot(db)
    with pytest.raises(ValueError, match='source declaration'):
        migrate_specialized_actions(db)
    assert _snapshot(db) == before


def test_specialized_payload_bytes_classes_and_existing_fk_findings_survive(db):
    from app import shared_action_lifecycle as lifecycle
    from app.shared_action_definitions import email_projection
    from tests.test_email_action_storage import _cases
    _source19_5(db)
    migrate_shared_actions(db)
    cases = _cases()
    lifecycle.persist_plan(db, 'email', cases['email_action_plans'] | {
        'authorization_snapshot_json':'{}', 'legacy_serialization_pre_v16':1})
    # Deliberately retained source FK findings, source negative/zero ROWIDs and
    # native BLOB scalar classes exercise storage inverse, not provider success.
    for rowid, kind in ((-7, 'email_reply_dispatch_claims'), (0, 'email_unsubscribe_claims')):
        values = cases[kind] | {'claimed_at':b'old-clock\x00', 'updated_at':b'old-update\xff'}
        actions.insert_email_action(db, kind, values, source_rowid=rowid)
    findings = sorted(tuple(row) for row in db.execute('pragma foreign_key_check'))
    assert findings
    before = _snapshot(db)
    metadata = migrate_specialized_actions(db)
    assert sorted(tuple(row) for row in db.execute('pragma foreign_key_check')) == findings
    restore_specialized_actions(db, metadata)
    assert _snapshot(db) == before
    migrate_specialized_actions(db)
    for kind, origin in (('email_reply_dispatch_claims','email_reply'), ('email_unsubscribe_claims','email_unsubscribe')):
        lifecycle.transition(db, origin, 'state', values={'updated_at':b'current-update\x00'}, where_sql='action_identity=?',parameters=(cases[kind]['action_identity'],))
        row = db.execute('select rowid,typeof(claimed_at),typeof(updated_at),updated_at from ('+email_projection(kind,include_rowid=True)+')').fetchone()
        assert row[1:] == ('blob','blob',b'current-update\x00')
    current = db.execute('select id,legacy_key,record_type,payload_json from actions order by id').fetchall()
    restore_specialized_actions(db, metadata)
    assert db.execute('select id,legacy_key,record_type,payload_json from actions order by id').fetchall() == current
    assert sorted(tuple(row) for row in db.execute('pragma foreign_key_check')) == findings
