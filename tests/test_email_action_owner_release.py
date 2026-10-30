"""Action data no longer duplicates facts owned by the Email Plan."""
import json

import pytest

from app.action_entity_storage import claim_action, create_action
from tests.test_action_entity_storage import entity_db


def test_generic_action_rejects_retired_plan_fields_and_omits_keys():
    db = entity_db()
    action = create_action(db, values={"operation": "send", "status": "pending"})
    retired = {"classification_id", "account_id", "parameters_json"}
    stored = json.loads(db.execute("select json(action_data) from actions where id=?", (action["id"],)).fetchone()[0])
    assert retired.isdisjoint(stored)
    assert retired.isdisjoint(action)
    for field in retired:
        with pytest.raises(ValueError, match="unknown"):
            create_action(db, values={field: "x"})
        with pytest.raises(ValueError, match="unknown"):
            claim_action(db, action["id"], expected={"status": "pending"}, changes={field: "x"})


def test_exact_predecessor_round_trip(tmp_path):
    import sqlite3
    from pathlib import Path
    from app.current_schema_manifest import schema_objects, schema_is_current
    from app.store_schema_release import prepare_database_schema_release
    from tests.support.store29_2_source import source_database

    path = tmp_path / 'source.sqlite3'
    source_database(path)
    with sqlite3.connect(path) as db:
        before = schema_objects(db)
    release = prepare_database_schema_release(Path(__file__).parents[1], path)
    release.publish()
    with sqlite3.connect(path) as db:
        assert schema_is_current(db)
    release.rollback()
    with sqlite3.connect(path) as db:
        assert schema_objects(db) == before


def _seed_source_action(path, *, account='account', plan_id='plan', parameters='{}'):
    import sqlite3
    from app.shared_action_lifecycle import _insert
    from tests.test_email_action_storage import _cases

    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute("insert into emails(classification_id,message_id,account_id,stable_message_identity,uidvalidity,uid,class_model_text,class_confidence,class_margin,class_probabilities_json,class_model_id,class_config_version,class_status,class_classification_source,class_agent_result_json,class_confirmed_at,class_created_at,class_updated_at,message_folder,message_rfc_message_id,message_thread_identity,message_sender,message_recipients_json,message_subject,message_normalized_text,message_preview,message_attachment_metadata_json,message_received_at,message_created_at,message_updated_at) values(11,1,'account','message',1,1,'',0,0,'{}','model','config','processed','model','{}','time','time','time','inbox','id','thread','sender','[]','','','','[]','time','time','time')")
        plan = _cases()['email_action_plans'] | {'actions_json': '["archive"]'}
        _insert(db, 'email_action_plans', plan)
        payload = {field: None for field in ('operation', 'parameters_json', 'status', 'action_plan_id', 'classification_id', 'account_id', 'external_action_key', 'next_attempt_at', 'started_at', 'created_at', 'updated_at', 'prepared_body', 'prepared_at', 'delivery_channel', 'delivery_key', 'feedback_token', 'postfix_version', 'delivery_receipt_id', 'conversation_id', 'trigger_message_id', 'recall_key', 'prepared_receipt_id', 'recall_status', 'recall_error', 'recalled_at', 'agent_run_id', 'business_object_key', 'action_identity')}
        payload.update(operation='archive', parameters_json=parameters, status='pending', action_plan_id=plan_id, classification_id=11, account_id=account)
        db.execute("insert into actions(_kind,action_data,payload_json) values(70,jsonb(?),jsonb('[]'))", (json.dumps(payload),))


def test_source_values_round_trip_and_projection(tmp_path):
    import sqlite3
    from app.email_action_owner_release import prepare_email_action_owner_release
    from app.email_store import DIRECT_EMAIL_ACTION_SQL
    from tests.support.store29_2_source import source_database

    path = tmp_path / 'facts.sqlite3'
    source_database(path)
    _seed_source_action(path)
    release = prepare_email_action_owner_release(path)
    release.publish()
    with sqlite3.connect(path) as db:
        row = db.execute('select classification_id,account_id,parameters_json from (' + DIRECT_EMAIL_ACTION_SQL + ')').fetchone()
        assert row == (11, 'account', '{}')
        assert db.execute("select json_type(action_data,'$.classification_id') from actions where _kind=70").fetchone()[0] is None
        db.row_factory = sqlite3.Row
        from app.action_entity_storage import append_action_receipt
        from app.shared_action_lifecycle import _insert, create_direct_email_action
        from tests.test_email_action_storage import _cases
        db.execute('begin')
        _insert(db, 'email_action_plans', _cases()['email_action_plans'] | {
            'action_plan_id': 'new-plan', 'action_plan_version': 2,
            'actions_json': '["move"]', 'action_parameters_json': '{"move":{"folder":"Inbox"}}',
        })
        new = create_direct_email_action(db, {'action_plan_id': 'new-plan', 'action_type': 'move',
                                             'status': 'pending', 'created_at': 'new', 'updated_at': 'new'})
        receipt = append_action_receipt(db, new['id'], values={'status': 'failed', 'error_text': 'provider failure'})
    release.rollback()
    with sqlite3.connect(path) as db:
        assert db.execute("select action_classification_id,action_account_id,action_parameters_json from actions where _kind=70").fetchone() == (11, 'account', '{}')
        assert db.execute("select action_parameters_json from actions where id=?", (new['id'],)).fetchone() == ('{"folder":"Inbox"}',)
        assert db.execute('select error_text from action_receipts where id=?', (receipt,)).fetchone() == ('provider failure',)
    again = prepare_email_action_owner_release(path)
    again.publish()
    again.finalize()


@pytest.mark.parametrize('kwargs', [{'account': 'other'}, {'plan_id': 'absent'}, {'parameters': '{"bad":1}'}])
def test_inconsistent_source_fails_before_mutation(tmp_path, kwargs):
    import sqlite3
    from app.email_action_owner_release import prepare_email_action_owner_release
    from app.current_schema_manifest import schema_objects
    from tests.support.store29_2_source import source_database

    path = tmp_path / 'bad.sqlite3'
    source_database(path)
    _seed_source_action(path, **kwargs)
    with sqlite3.connect(path) as db:
        before = (schema_objects(db), list(db.execute('select id,hex(action_data) from actions')))
    with pytest.raises(ValueError, match='Plan'):
        prepare_email_action_owner_release(path)
    with sqlite3.connect(path) as db:
        assert (schema_objects(db), list(db.execute('select id,hex(action_data) from actions'))) == before


def test_explicit_null_plan_parameters_rejected_before_write(tmp_path):
    import sqlite3
    from app.email_action_owner_release import prepare_email_action_owner_release
    from app.shared_action_lifecycle import _update
    from tests.support.store29_2_source import source_database

    path = tmp_path / 'null-parameters.sqlite3'
    source_database(path)
    _seed_source_action(path)
    with sqlite3.connect(path) as db:
        db.execute('begin')
        _update(db, 'email_action_plans', 1, {'action_parameters_json': '{"archive":null}'})
    with pytest.raises(ValueError, match='invalid Plan operation or parameters'):
        prepare_email_action_owner_release(path)


def test_interrupted_release_rolls_back_schema_and_data(tmp_path, monkeypatch):
    import sqlite3
    from app import email_action_owner_release as module
    from app.current_schema_manifest import schema_objects
    from tests.support.store29_2_source import source_database

    path = tmp_path / 'atomic.sqlite3'
    source_database(path)
    _seed_source_action(path)
    with sqlite3.connect(path) as db:
        before = (schema_objects(db), list(db.execute('select id,hex(action_data),hex(payload_json) from actions')),
                  list(db.execute('select rowid,name,seq from sqlite_sequence order by rowid')))
    release = module.prepare_email_action_owner_release(path)
    def fail(*_args):
        raise RuntimeError('interrupted publication')
    monkeypatch.setattr(module, '_write_marker', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        release.publish()
    with sqlite3.connect(path) as db:
        assert (schema_objects(db), list(db.execute('select id,hex(action_data),hex(payload_json) from actions')),
                list(db.execute('select rowid,name,seq from sqlite_sequence order by rowid'))) == before


def test_validator_preserves_non_sorted_parameter_text(tmp_path):
    import sqlite3
    from app.email_action_owner_release import prepare_email_action_owner_release
    from app.shared_action_lifecycle import _update
    from tests.support.store29_2_source import source_database
    from tools.native_copy_validation import _identities
    from tools.validate_email_action_owner_copy import fingerprint

    path = tmp_path / 'ordered-parameters.sqlite3'
    source_database(path)
    _seed_source_action(path, parameters='{"b":2,"a":1}')
    with sqlite3.connect(path) as db:
        db.execute('begin')
        _update(db, 'email_action_plans', 1, {'action_parameters_json': '{"archive":{"b":2,"a":1}}'})
    identities = _identities(path)
    before = fingerprint(path, identities)
    release = prepare_email_action_owner_release(path)
    release.publish()
    assert fingerprint(path, identities) == before
    release.rollback()
    with sqlite3.connect(path) as db:
        assert db.execute('select action_parameters_json from actions where _kind=70').fetchone()[0] == '{"b":2,"a":1}'
    assert fingerprint(path, identities) == before


@pytest.mark.parametrize('mutation', [
    "jsonb_remove(action_data,'$.account_id')",
    "jsonb_set(action_data,'$.operation','send','$.action_plan_id',null,'$.account_id','not-null')",
])
def test_missing_key_or_non_email_owned_value_rejected(tmp_path, mutation):
    import sqlite3
    from app.email_action_owner_release import prepare_email_action_owner_release
    from tests.support.store29_2_source import source_database

    path = tmp_path / 'unmappable.sqlite3'
    source_database(path)
    _seed_source_action(path)
    with sqlite3.connect(path) as db:
        db.execute('update actions set action_data=' + mutation + ' where _kind=70')
        before = db.execute('select hex(action_data) from actions where _kind=70').fetchone()
    with pytest.raises(ValueError):
        prepare_email_action_owner_release(path)
    with sqlite3.connect(path) as db:
        assert db.execute('select hex(action_data) from actions where _kind=70').fetchone() == before


def test_historical_transfer_rejects_mismatch_before_creating_action(tmp_path):
    import sqlite3
    from app.action_entity_transfer import _transfer_email
    from app.email_action_owner_release import prepare_email_action_owner_release
    from tests.support.store29_2_source import source_database

    path = tmp_path / 'historical.sqlite3'
    source_database(path)
    _seed_source_action(path)
    prepare_email_action_owner_release(path).publish()
    source = {'action_plan_id': 'plan', 'classification_id': 11, 'account_id': 'wrong',
              'parameters_json': '{}', 'action_type': 'archive', 'status': 'pending',
              'action_id': 'legacy', 'next_attempt_at': '', 'started_at': '',
              'created_at': 'time', 'updated_at': 'time'}
    with sqlite3.connect(path) as db:
        before = db.execute("select count(*) from actions where record_type='action'").fetchone()[0]
        with pytest.raises(ValueError, match='differs from its exact Plan'):
            _transfer_email(db, {'email_actions': [source], 'email_action_attempts': []})
        assert db.execute("select count(*) from actions where record_type='action'").fetchone()[0] == before
