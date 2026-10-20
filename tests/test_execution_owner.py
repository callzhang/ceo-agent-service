"""A plan owns execution; immutable aliases retain independent public/physical IDs."""

import sqlite3

import pytest

from app import action_storage as actions
from app.action_email_storage import _fingerprint
from app.model_result_schema import result_action_objects
from tests.test_provenance_vector_storage import db as db


SOURCE_HASHES = {
    'actual_no_memory_fk': '77ac6dff94126976212d1cd7bfda3b36531e4ee8a02839a9a73f88bee85401d4',
    'fresh_memory_fk': 'f9ab43a6874aba1cc9fc8d6c19770a4834c6374850e29630a905dcc91379c3be',
}


def test_source_declarations_frozen_and_execution_layout_is_explicit():
    for variant, digest in SOURCE_HASHES.items():
        assert _fingerprint(result_action_objects(variant)) == digest
        source = actions.action_payloads(variant, physical_provenance=True, selection_references=True)
        target = actions.action_payloads(variant, physical_provenance=True,
                                         selection_references=True, execution_owner=True)
        assert 'candidate_executions' not in target.layouts
        assert target.layouts['candidate_execution_alias'].fields == ()
        assert target.layouts['review_candidates'].fields == source.layouts['review_candidates'].fields + (
            'execution_public_id', 'execution_status', 'execution_lease_owner',
            'execution_lease_expires_at', 'execution_result_json',
            'execution_created_at', 'execution_updated_at',
        )
        for kind in set(source.layouts) - {'review_candidates', 'candidate_executions'}:
            assert target.layouts[kind] == source.layouts[kind]


def test_runtime_keeps_execution_identity_and_mutates_only_plan(tmp_path):
    from app.store import AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path / 'runtime.sqlite3')
    _, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'owner', 60)
    assert store.get_candidate_execution(candidate['id']) == execution
    with store._connect() as connection:
        assert connection.execute("select count(*) from actions where record_type='candidate_executions'").fetchone()[0] == 0
        alias = tuple(connection.execute("select id,legacy_key,candidate_id,payload_json,status,result_json,created_at,updated_at from actions where record_type='candidate_execution_alias'").fetchone())
        plan = connection.execute("select id,execution_public_id,execution_review_id,execution_status,execution_lease_owner from actions where record_type='review_candidates'").fetchone()
        assert alias[0] != plan[0]
        assert alias[1:3] == (str(execution['id']), plan[0])
        assert alias[4:] == (None,) * 4
        assert tuple(plan[1:]) == (execution['id'], actions.action_physical_id(connection, 'candidate_reviews', review['id']), 'running', 'owner')
    renewed = store.renew_candidate_execution_claim(execution['id'], 'owner', 120)
    assert renewed['lease_expires_at'] >= execution['lease_expires_at']
    attempt = store.begin_candidate_action(execution['id'], 'owner', 0, 'receipt')
    assert attempt['execution_id'] == execution['id']
    store.record_candidate_external_action(execution['id'], 'owner', 0, 'receipt', 'send', {'user_id': 'x'}, {'receipt': 'substitute'})
    finished = store.finish_candidate_execution(execution['id'], 'owner', 'done', {'done': True})
    assert finished['id'] == execution['id'] and finished['status'] == 'done'
    assert store.claim_candidate_execution(candidate['id'], review['id'], 'owner', 60) is None
    with store._connect() as connection:
        assert tuple(connection.execute("select id,legacy_key,candidate_id,payload_json,status,result_json,created_at,updated_at from actions where record_type='candidate_execution_alias'").fetchone()) == alias
        assert connection.execute('pragma foreign_key_check').fetchall() == []


def _source(db):
    from app.model_action_schema import migrate_selection_references
    from app.model_result_schema import migrate_result_references
    from tests.test_model_action_schema import _seed
    _seed(db)
    migrate_selection_references(db)
    migrate_result_references(db)


def test_forward_inverse_preserves_all_source_facts_and_independent_ids(db):
    from app.execution_owner_schema import migrate_execution_owner, restore_execution_owner
    from tests.test_model_action_schema import _snapshot
    _source(db)
    before = _snapshot(db)
    metadata = migrate_execution_owner(db)
    assert metadata == {'reply_variant': actions._reply_variant_for(db)}
    assert db.execute("select id,legacy_key,candidate_id,json(payload_json),status,result_json,created_at from actions where record_type='candidate_execution_alias'").fetchone() == (601, '61', 101, '[]', None, None, None)
    assert db.execute('select execution_public_id,execution_review_id,execution_selection_id,execution_status,execution_result_json,execution_created_at,created_at from actions where id=101').fetchone() == (61, 301, 501, 'done', ' { "kept": 1 } ', 'created', 'candidate-clock')
    assert db.execute('select execution_public_id,execution_review_id,execution_selection_id,execution_status,execution_result_json,execution_created_at from actions where id=202').fetchone() == (None,) * 6
    assert db.execute("select count(*) from actions where record_type not in ('review_candidates','candidate_execution_alias') and (execution_binding_candidate_id is not null or execution_binding_public_id is not null or execution_peer_kind is not null)").fetchone() == (0,)
    assert db.execute('select execution_binding_candidate_id,execution_binding_public_id,execution_peer_kind from actions where id=202').fetchone() == (None,) * 3
    assert db.execute(f'select id,candidate_id,review_id,selection_id from ({actions.CANDIDATE_EXECUTION_SQL})').fetchone() == (61, 11, 31, 51)
    assert db.execute('pragma foreign_key_check').fetchall() == []
    # Both variants forbid every unrelated physical column on the alias, even
    # with FK enforcement disabled for this caller-owned migration transaction.
    allowed = {'id', '_kind', 'legacy_key', 'candidate_id', 'payload_json'}
    for column in (row[1] for row in db.execute('pragma table_xinfo(actions)')
                   if row[6] == 0 and row[1] not in allowed):
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
            db.execute(f'update actions set "{column}"=999999999 where id=601')
    restore_execution_owner(db, metadata)
    assert _snapshot(db) == before


def test_new_execution_and_attempt_inverse_uses_current_plan(db):
    from app.execution_owner_schema import migrate_execution_owner, restore_execution_owner
    from app.execution_owner_storage import create_plan_execution, update_plan_execution
    from tests.test_model_action_schema import _snapshot
    _source(db)
    metadata = migrate_execution_owner(db)
    new_id = create_plan_execution(db, candidate_id=22, review_id=32, selection_id=None,
        status='running', lease_owner='new-owner', lease_expires_at='later')
    assert new_id == 62  # Old per-type ROWID max was 61; candidate public max is 22.
    attempt = actions.insert_action(db, 'candidate_action_attempts', dict(execution_id=new_id,
        action_index=0, external_action_key='later', status='uncertain', result_json=' { "later": 2 } '), execution_owner=True)
    update_plan_execution(db, new_id, dict(status='uncertain', result_json=' { "current": 1 } ', updated_at='new-clock'))
    physical = actions.action_physical_id(db, 'candidate_execution_alias', new_id)
    target = _snapshot(db)
    restore_execution_owner(db, metadata)
    assert db.execute('select id,candidate_id,review_id,selection_id,status,result_json,updated_at from actions where id=?', (physical,)).fetchone() == (physical, 202, 302, None, 'uncertain', ' { "current": 1 } ', 'new-clock')
    assert db.execute("select execution_id from actions where record_type='candidate_action_attempts' and legacy_key=?", (str(attempt.lastrowid),)).fetchone() == (physical,)
    assert not db.execute("select 1 from sqlite_sequence where name like '%candidate_execution%'").fetchone()
    migrate_execution_owner(db)
    assert _snapshot(db) == target


def test_deferred_binding_rejects_invalid_commit_and_keeps_transaction(db):
    from app.execution_owner_schema import migrate_execution_owner
    from app.execution_owner_storage import create_plan_execution
    _source(db)
    migrate_execution_owner(db)
    db.commit()
    db.execute('pragma foreign_keys=on')
    for mutation in ('alias_without_block', 'block_without_alias', 'wrong_public',
                     'wrong_candidate_review', 'wrong_candidate_selection', 'wrong_selection_review'):
        db.execute('begin immediate')
        if mutation == 'alias_without_block':
            db.execute("insert into actions(_kind,legacy_key,candidate_id,payload_json) values(?,?,202,jsonb('[]'))", (actions.ACTION_RECORD_TYPES.code('candidate_execution_alias'), '62'))
        elif mutation == 'block_without_alias':
            from app.execution_owner_definitions import EXECUTION_FIELDS
            layout = actions.action_payloads('actual_no_memory_fk', execution_owner=True).layouts['review_candidates']
            values = (62, 'running', '', '', '{}', 'created', 'updated')
            params = []
            import json
            for field, value in zip(EXECUTION_FIELDS, values, strict=True):
                params.extend((layout.path(field), json.dumps(value)))
            db.execute('update actions set execution_review_id=302,payload_json=jsonb_set(payload_json,'
                       + ','.join('?,json(?)' for _ in values) + ') where id=202', params)
        elif mutation == 'wrong_public':
            path = actions.action_payloads('actual_no_memory_fk', execution_owner=True).path('review_candidates', 'execution_public_id')
            db.execute('update actions set payload_json=jsonb_set(payload_json,?,62) where id=101', (path,))
        elif mutation == 'wrong_candidate_review':
            db.execute('update actions set execution_review_id=302 where id=101')
        elif mutation == 'wrong_candidate_selection':
            create_plan_execution(db, candidate_id=22, review_id=32, selection_id=51,
                status='running', lease_owner='new', lease_expires_at='later')
        else:
            from app.run_storage import insert_run
            from tests.test_model_action_schema import _add
            insert_run(db, 'agent_runs', dict(id=5, task_id=1, proposal_revision=5, execution_generation='generation', role='audit'))
            _add(db, 'candidate_reviews', 303, 33, dict(decision='approve', candidate_digest='digest', result_json='{}', created_at='later'), references={'candidate_id':101,'audit_run_id':5}, target=True)
            db.execute('update actions set execution_review_id=303 where id=101')
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            db.commit()
        assert db.in_transaction
        db.rollback()
        assert db.execute('pragma foreign_key_check').fetchall() == []


def test_valid_atomic_pair_commits_with_foreign_keys_on(db):
    from app.execution_owner_schema import migrate_execution_owner
    from app.execution_owner_storage import create_plan_execution
    _source(db)
    migrate_execution_owner(db)
    db.commit()
    db.execute('pragma foreign_keys=on')
    db.execute('begin immediate')
    new = create_plan_execution(db, candidate_id=22, review_id=32, selection_id=None,
        status='running', lease_owner='owner', lease_expires_at='later')
    db.commit()
    assert new == 62
    assert db.execute('pragma foreign_key_check').fetchall() == []
    for noncanonical in ('063', '+63', '63.0', '9223372036854775808'):
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
            db.execute("insert into actions(_kind,legacy_key,candidate_id,payload_json) values(62,?,101,jsonb('[]'))", (noncanonical,))
    with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
        db.execute("insert into actions(_kind,legacy_key,candidate_id,payload_json) values(62,'63',101,jsonb('[]'))")
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        db.execute('update actions set legacy_key=\'63\' where id=601')


@pytest.mark.parametrize('mutation', ['wrong_review', 'changed_schema'])
def test_bad_source_refuses_before_any_write(db, mutation):
    from app.execution_owner_schema import migrate_execution_owner
    from tests.test_model_action_schema import _snapshot
    _source(db)
    if mutation == 'wrong_review':
        db.execute('update actions set review_id=302 where id=601')
    else:
        db.execute('create index changed_execution_source on actions(status)')
    before = _snapshot(db)
    with pytest.raises(ValueError, match='binding|declaration'):
        migrate_execution_owner(db)
    assert _snapshot(db) == before


@pytest.mark.parametrize('maximum', [None, -2, 9223372036854775807])
def test_alias_allocator_keeps_ordinary_rowid_namespace(db, maximum):
    from app.execution_owner_schema import migrate_execution_owner
    from app.execution_owner_storage import create_plan_execution
    from tests.test_model_action_schema import _snapshot
    _source(db)
    if maximum is None:
        db.execute('delete from actions where id=601')
    else:
        db.execute('update actions set legacy_key=? where id=601', (str(maximum),))
    water = _snapshot(db)[2]
    migrate_execution_owner(db)
    new = create_plan_execution(db, candidate_id=22, review_id=32, selection_id=None,
        status='running', lease_owner='owner', lease_expires_at='later')
    if maximum == 9223372036854775807:
        assert 0 < new < maximum
    else:
        assert new == (1 if maximum is None else maximum + 1)
    assert _snapshot(db)[2] == water
    assert db.execute('pragma foreign_key_check').fetchall() == []


def test_multiple_reviews_remain_independent_and_source_type_codes_are_fixed(db):
    from app.execution_owner_schema import migrate_execution_owner, restore_execution_owner
    from app.run_storage import insert_run
    from app.storage_payload import ACTION_RECORD_TYPES, SOURCE_ACTION_RECORD_TYPES
    from tests.test_model_action_schema import _add, _snapshot
    _source(db)
    insert_run(db, 'agent_runs', dict(id=5, task_id=1, proposal_revision=5, execution_generation='generation', role='audit'))
    _add(db, 'candidate_reviews', 303, 33, dict(decision='return', candidate_digest='digest', result_json=' { "other": true } ', created_at='later'), references={'candidate_id':101,'audit_run_id':5}, target=True)
    before = _snapshot(db)
    metadata = migrate_execution_owner(db)
    assert ACTION_RECORD_TYPES.names[:-1] == SOURCE_ACTION_RECORD_TYPES.names
    assert ACTION_RECORD_TYPES.code('candidate_executions') == 5
    assert ACTION_RECORD_TYPES.code('candidate_execution_alias') == 62
    assert db.execute("select count(*) from actions where record_type='candidate_reviews' and candidate_id=101").fetchone() == (2,)
    assert db.execute('select execution_review_id from actions where id=101').fetchone() == (301,)
    with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
        db.execute("insert into actions(_kind,legacy_key,payload_json) values(5,'99',jsonb('[]'))")
    restore_execution_owner(db, metadata)
    assert _snapshot(db) == before


def test_fresh_alias_rejects_unrelated_physical_memory_binding(db):
    from app.execution_owner_schema import migrate_execution_owner
    from tests.test_model_action_schema import _snapshot
    if actions._reply_variant_for(db) != 'fresh_memory_fk':
        pytest.skip('Only the fresh source has this physical memory reference.')
    _source(db)
    db.execute('update actions set memory_attempt_ref=101 where id=601')
    before = _snapshot(db)
    with pytest.raises(ValueError, match='unused physical'):
        migrate_execution_owner(db)
    assert _snapshot(db) == before
    db.execute('update actions set memory_attempt_ref=null where id=601')
    migrate_execution_owner(db)
    with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
        db.execute('update actions set memory_attempt_ref=101 where id=601')


