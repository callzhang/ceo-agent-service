"""The real paired release publishes result references and rolls back current facts."""
import json
import sqlite3
from pathlib import Path

import pytest

from app.action_storage import _objects, insert_action
from app.email_store import EmailStore
from app.state_definitions import state_projection
from app.state_storage import update_state_rows
from app.store import AutoReplyStore
from app.store_schema_release import prepare_database_schema_release
from tests.test_reviewed_candidate_store import _reviewed

ROOT = Path(__file__).resolve().parents[1]


def _markers(db):
    return (tuple(db.execute(f"select value,updated_at from ({state_projection('service_state')}) where key='store_schema_version'").fetchone()),
            tuple(tuple(r) for r in db.execute('select version,applied_at from email_schema_migrations order by version')))


def _source(tmp_path):
    from app.model_result_schema import restore_result_references
    path = tmp_path/'release.sqlite3'
    store = AutoReplyStore(path)
    EmailStore(path)
    task, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'worker', 60)
    store.begin_candidate_action(execution['id'], 'worker', 0, 'receipt')
    store.record_candidate_external_action(execution['id'], 'worker', 0, 'receipt', 'send',
                                          {'user_id':'x'}, {'receipt':'substitute'})
    store.finish_candidate_execution(execution['id'], 'worker', 'done', {})
    with sqlite3.connect(path, isolation_level=None) as db:
        db.execute('pragma foreign_keys=off')
        db.execute('begin immediate')
        restore_result_references(db, {'reply_variant':'fresh_memory_fk'})
        update_state_rows(db, 'service_state', {'value':'2026-10-19.1', 'updated_at':b'original-clock'},
                          where_sql="key='store_schema_version'")
        db.commit()
    return path, task, execution, review


def test_registered_release_and_current_fact_rollback(tmp_path):
    path, task, execution, prior_review = _source(tmp_path)
    with sqlite3.connect(path) as db:
        markers, source_objects = _markers(db), _objects(db,'actions')
    release = prepare_database_schema_release(ROOT,path)
    assert type(release).__name__ == 'ResultSchemaRelease'
    release.publish()
    release.verify_loaded()
    store = AutoReplyStore(path)
    EmailStore(path)
    from app.store import AgentRole
    from app.model_action_schema import selected_branch
    from tests.test_model_action_schema import _snapshot
    prior = store.get_review_candidate(execution['candidate_id'])
    later_run = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=prior_review['audit_run_id'], operation_id='', owner='later').run
    later_body = {**json.loads(prior['candidate_json']), 'outcome':'needs_human', 'proposal':None,
        'decision_options':[{'key':'later-option','plan':{'actions':[{'action_identity':'later',
        'operation':'send','target':{'user_id':'later-target'}}]}}]}
    store.complete_agent_run(later_run.id,later_body,owner='later')
    later_candidate = store.persist_review_candidate(task,later_run,later_body)
    audit = store.claim_agent_run(task.id,task.execution_generation,role=AgentRole.AUDIT,
        proposal_revision=1,turn_attempt=0,parent_agent_run_id=later_run.id,operation_id='later-audit',owner='audit').run
    verdict = {'outcome':'approve','candidate_digest':later_candidate['candidate_digest']}
    store.complete_agent_run(audit.id,verdict,owner='audit')
    later_review = store.record_candidate_review(later_candidate['id'],audit.id,verdict)
    selection = store.select_candidate_option(later_candidate['id'],later_review['id'],'later-option')
    with store._immediate_write_transaction() as db:
        later = insert_action(db,'external_action_results',dict(external_action_key='later',
            business_object_key='object',action_identity='later',operation='send',target_identifiers_json='{}',
            provider_result_json=' {"new":true} ',result_digest='digest',first_agent_run_id=store.get_candidate_external_action('receipt')['first_agent_run_id']))
        assert later.lastrowid == 'later'
        attempt = insert_action(db,'candidate_action_attempts',dict(execution_id=execution['id'],
            action_index=1,external_action_key='later',status='verified',result_json=None))
        db.execute("insert into sqlite_sequence(name,seq) values('actions:extra-water',9000)")
        water = db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall()
        target_rows = _snapshot(db)
        assert selected_branch(db,selection['id']) == later_body['decision_options'][0]
    assert store.list_candidate_action_attempts(execution['id'])[1]['result_json'] == ' {"new":true} '
    release.finalize()
    release.rollback()
    with sqlite3.connect(path) as db:
        assert _markers(db) == markers
        assert _objects(db,'actions') == source_objects
        assert selected_branch(db,selection['id']) == later_body['decision_options'][0]
        assert db.execute("select result_json from actions where record_type='candidate_action_attempts' and legacy_key=?",(str(attempt.lastrowid),)).fetchone() == (' {"new":true} ',)
        assert db.execute('select rowid,name,seq from sqlite_sequence order by rowid').fetchall() == [tuple(r) for r in water]
        assert db.execute('pragma quick_check').fetchone() == ('ok',)
    release.rollback()
    release.publish()
    release.finalize()
    with sqlite3.connect(path) as db:
        assert _snapshot(db) == target_rows


@pytest.mark.parametrize('constructor',[AutoReplyStore,EmailStore])
def test_source_constructor_refuses_without_writes(tmp_path,constructor):
    path,*_ = _source(tmp_path)
    before = path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError,match='unsupported or mixed'):
        constructor(path)
    assert path.read_bytes() == before


def test_fresh_target_is_noop_and_mixed_marker_refuses(tmp_path):
    path = tmp_path/'fresh.sqlite3'
    AutoReplyStore(path)
    EmailStore(path)
    assert prepare_database_schema_release(ROOT,path) is None
    with sqlite3.connect(path) as db:
        db.execute('begin immediate')
        update_state_rows(db,'service_state',{'value':'2026-10-19.1'},where_sql="key='store_schema_version'")
    with pytest.raises((ValueError,sqlite3.IntegrityError)):
        prepare_database_schema_release(ROOT,path)


