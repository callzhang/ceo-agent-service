"""Both frozen declarations, lossless source refusal and current-fact inverse."""
import hashlib
import json


from app import action_storage as actions
from app.shared_action_definitions import SHARED_RECORD_TYPES, SOURCE_TO_TARGET
from app.shared_action_schema import migrate_shared_actions, restore_shared_actions, shared_action_objects
from app.sent_result_schema import sent_action_objects, migrate_sent_result_references
from tests.test_provenance_vector_storage import db as db
from tests.test_model_action_schema import _snapshot
from tests.test_sent_result_references import _source

SOURCE_HASHES = {
    'actual_no_memory_fk': '25790ebc80564660e643ed3ac6ea5d157912fe1e5f8cfb169423c797df619373',
    'fresh_memory_fk': 'c9da6469aa1eccc80d6f2e74feb642c2b9470b02e5439f25651465dc6b10f6e7',
}


def _source19_5(db):
    _source(db)
    migrate_sent_result_references(db)


def test_source_declarations_remain_frozen_and_variants_have_only_source_fields():
    for variant, expected in SOURCE_HASHES.items():
        assert hashlib.sha256(json.dumps(sent_action_objects(variant),sort_keys=True,separators=(',', ':')).encode()).hexdigest() == expected
        source = actions.action_payloads(variant, selection_references=True, execution_owner=True)
        target = actions.action_payloads(variant, selection_references=True, execution_owner=True, shared_lifecycle=True)
        for kind in source.layouts:
            if kind != 'email_actions':
                assert target.layouts[kind] == source.layouts[kind]
        assert set(target.layouts['email_actions'].fields) < set(source.layouts['email_actions'].fields)
        assert all(SHARED_RECORD_TYPES.code(kind) > 62 for kind in SOURCE_TO_TARGET)
        assert actions.ACTION_RECORD_TYPES.code('candidate_execution_alias') == 62


def test_both_variants_forward_inverse_preserve_all_current_facts_and_waters(db):
    _source19_5(db)
    before = _snapshot(db)
    metadata = migrate_shared_actions(db)
    assert metadata == {'reply_variant': actions._reply_variant_for(db)}
    assert actions._objects(db, 'actions') == shared_action_objects(metadata['reply_variant'])
    assert db.execute('pragma foreign_key_check').fetchall() == []
    restore_shared_actions(db, metadata)
    assert _snapshot(db) == before


def test_current_new_reviewed_execution_slot_and_a2_a4_owners_inverse(db):
    from app import shared_action_lifecycle as lifecycle
    from app.execution_owner_storage import create_plan_execution
    _source19_5(db)
    metadata = migrate_shared_actions(db)
    execution = create_plan_execution(db, candidate_id=22, review_id=32, selection_id=None,
        status='running', lease_owner='new-owner', lease_expires_at='later')
    attempt = lifecycle.transition(db, 'reviewed', 'claim', identity=(execution, 0, 'current-new'),
        values={'created_at': 'actual-dispatch-clock', 'updated_at': 'actual-update-clock'})
    lifecycle.transition(db, 'reviewed', 'outcome', identity=attempt['id'],
        values={'status': 'confirmed_no_effect', 'result_json': '{"absence":true}', 'updated_at': 'checked-clock'})
    reclaimed = lifecycle.transition(db, 'reviewed', 'claim', identity=(execution, 0, 'current-new'),
        values={'created_at': 'unused-new-clock', 'updated_at': 're-dispatched-clock'})
    assert reclaimed['id'] == attempt['id'] and reclaimed['created_at'] == 'actual-dispatch-clock'
    public = db.execute(actions.CANDIDATE_ACTION_ATTEMPT_SQL + ' order by id').fetchall()
    results = db.execute(actions.SENT_REPLY_SQL + ' order by id').fetchall()
    ids = db.execute('select id,legacy_key from actions order by id').fetchall()
    waters = db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall()
    restore_shared_actions(db, metadata)
    assert db.execute(actions.CANDIDATE_ACTION_ATTEMPT_SQL + ' order by id').fetchall() == public
    assert db.execute(actions.SENT_REPLY_SQL + ' order by id').fetchall() == results
    assert db.execute('select id,legacy_key from actions order by id').fetchall() == ids
    assert db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall() == waters
    assert db.execute('pragma foreign_key_check').fetchall() == []
    migrate_shared_actions(db)
    assert db.execute(actions.CANDIDATE_ACTION_ATTEMPT_SQL + ' order by id').fetchall() == public
