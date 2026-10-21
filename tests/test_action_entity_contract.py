import hashlib
import json
import sqlite3

import pytest

from app.action_email_definitions import EMAIL_FIELDS, EMAIL_CHECKS
from app.action_storage import SOURCE_COLUMNS, ENUMS
from app.prepared_action_definitions import COLUMNS as PREPARED_COLUMNS, SLOTS
from app.current_action_schema import current_action_payloads
from app.shared_action_definitions import SHARED_RECORD_TYPES, email_projection
from tools.action_entity_contract import (
    inspect_action_entity_source,
    source_identity,
    validate_action_entity_contract,
)


CONTRACT = json.loads(open('docs/action-entity-field-map.json').read())
DDL = open('docs/action-entity-target.sql').read()


def install_action_target(db):
    db.create_function('unhex', 1, lambda value: bytes.fromhex(value) if value is not None else None, deterministic=True)
    db.create_function('pow', 2, lambda x, y: x ** y, deterministic=True)
    db.execute('create table business(id integer primary key, task_public_id text unique, attention_item_public_id text unique, project_public_id text unique, anchor_public_id text unique, work_cluster_public_id text unique)')
    for parent in ('configs', 'emails', 'provenance', 'runs', 'tasks'):
        db.execute(f'create table {parent}(id integer primary key)')

    # Execute the actual rebuilt table and its indexes, then Action-owned child
    # relations/triggers. Legacy triggers are parsed and validated by the full
    # DDL validator but are intentionally not fired by this minimal fixture.
    table_start = DDL.index('CREATE TABLE actions')
    table_end = DDL.index(');', table_start) + 2
    db.execute(DDL[table_start:table_end])
    for marker in ('create table action_receipts', 'create table action_observers'):
        start = DDL.lower().index(marker)
        end = DDL.lower().index(');', start) + 2
        db.execute(DDL[start:end])
    for line in DDL.splitlines():
        sql = line.strip()
        if sql.lower().startswith(('create index ', 'create unique index ')):
            db.execute(sql)
    for marker in ('create trigger trg_action_receipt_owner_insert', 'create trigger trg_action_observer_owner_insert', 'create trigger trg_action_receipt_reference_owner_insert'):
        start = DDL.lower().index(marker)
        end = DDL.lower().index('end;', start) + 4
        db.executescript(DDL[start:end])


def test_all_source_fields_have_one_owner_and_match_declarations():
    validate_action_entity_contract(CONTRACT)
    expected = {
        'email_actions': EMAIL_FIELDS['email_actions'],
        'email_action_attempts': EMAIL_FIELDS['email_action_attempts'],
        **{name: SOURCE_COLUMNS[name] for name in (
            'candidate_action_attempts', 'external_action_results', 'outbound_postfixes',
            'sent_replies', 'sent_reply_observers',
        )},
        'outbound_postfixes': (*SOURCE_COLUMNS['outbound_postfixes'], *PREPARED_COLUMNS),
    }
    assert tuple(column for fields in SLOTS.values() for column in fields.values()) == PREPARED_COLUMNS
    actual = {table['table']: [field['name'] for field in table['fields']]
              for table in CONTRACT['sources']}
    assert actual == {table: list(fields) for table, fields in expected.items()}
    assert all(len(names) == len(set(names)) for names in actual.values())
    email_parent = set(current_action_payloads().layouts['email_actions'].fields)
    projected = set(EMAIL_FIELDS['email_actions']) - email_parent
    assert projected == {'attempt_count', 'error', 'finished_at', 'provider_operation', 'provider_result_id', 'provider_target'}
    mapped = next(item for item in CONTRACT['sources'] if item['table'] == 'email_actions')
    assert {field['name'] for field in mapped['fields'] if field.get('storage') == 'stored'} == email_parent
    assert {field['name'] for field in mapped['fields'] if field.get('storage') == 'derived_projection'} == projected
    action_names = {column['name'] for column in CONTRACT['target']['business_columns']}
    assert not {'finished_at', 'current_provider_operation', 'current_provider_target', 'current_provider_result_id', 'current_error_text'} & action_names


def test_email_derived_action_fields_keep_current_projection_semantics():
    names = {f'email_{field}' for kind in ('email_actions', 'email_action_attempts') for field in EMAIL_FIELDS[kind]}
    names.update({'id', '_kind', 'record_type', 'legacy_key'})
    with sqlite3.connect(':memory:') as db:
        db.execute('create table actions(' + ','.join(f'"{name}"' + (' integer' if name in {'id','_kind','email_attempt_number'} else ' text') for name in sorted(names)) + ')')
        projection = email_projection('email_actions')
        action_kind = SHARED_RECORD_TYPES.code('email_actions')
        attempt_kind = SHARED_RECORD_TYPES.code('email_action_attempts')
        for status, parent_started, expected_started, expected_terminal in (
            ('pending', '', '', ''),
            ('processing', 'claim-time', 'claim-time', ''),
            ('done', 'claim-time', 'attempt-start', ('finished', 'provider-op', 'target', 'receipt-id', 'provider-error')),
        ):
            db.execute('delete from actions')
            parent = {'id':1, '_kind':action_kind, 'record_type':'email_actions', 'legacy_key':'a', 'email_action_id':'a', 'email_status':status, 'email_started_at':parent_started}
            receipt = {'id':2, '_kind':attempt_kind, 'record_type':'email_action_attempts', 'legacy_key':'1', 'email_action_id':'a', 'email_attempt_number':1, 'email_started_at':'attempt-start', 'email_finished_at':'finished', 'email_provider_operation':'provider-op', 'email_provider_target':'target', 'email_provider_result_id':'receipt-id', 'email_error':'provider-error'}
            for row in (parent, receipt):
                keys = list(row)
                db.execute(f"insert into actions ({','.join(keys)}) values ({','.join('?' for _ in keys)})", [row[key] for key in keys])
            result = db.execute(f"select attempt_count,started_at,finished_at,provider_operation,provider_target,provider_result_id,error from ({projection})").fetchone()
            terminal = ('', '', '', '', '') if status in ('pending', 'processing') else expected_terminal
            assert result == (1, expected_started, *terminal)



def test_state_contract_matches_source_enums_without_success_inference():
    values = CONTRACT['state_mappings']
    action_declared = next(check for check in EMAIL_CHECKS['email_actions'] if 'email_status in' in check)
    email_values = set(action_declared.split("'")[1::2])
    assert set(values['action_status']['declared_values']) == email_values | set(ENUMS['candidate_action_attempts'][1])
    assert values['action_status']['target'] == 'Action.status'
    assert values['action_status']['current_observation_order'] == ['updated_at', 'execution_id', 'action_index', 'id']
    assert values['action_status']['semantic_rule'] == 'Use the last explicit source lifecycle observation in the frozen order; candidate verified means the source writer accepted an explicit verification receipt. No provider JSON/timestamp inference. Preserve every source observation unchanged.'
    assert set(values['receipt_status']['declared_values']) == set(EMAIL_CHECKS['email_action_attempts'][-1].split("'")[1::2]) | set(ENUMS['candidate_action_attempts'][1])
    assert 'never establish verified/done' in values['receipt_status']['semantic_rule'].lower()
    assert values['receipt_status']['mapping'] == 'identity; retain exact state on its owning attempt or execution observation'


def test_no_unsourced_business_column_or_duplicate_owner():
    for column in CONTRACT['target']['business_columns']:
        assert column['sources']
    broken = json.loads(json.dumps(CONTRACT))
    broken['sources'][0]['fields'][0]['owner'] = ''
    with pytest.raises(ValueError, match='owner'):
        validate_action_entity_contract(broken)
    broken = json.loads(json.dumps(CONTRACT))
    broken['target']['business_columns'][0]['sources'] = ['made_up.business_field']
    with pytest.raises(ValueError, match='unknown source'):
        validate_action_entity_contract(broken)
    broken = json.loads(json.dumps(CONTRACT))
    broken['target']['business_columns'][1]['name'] = broken['target']['business_columns'][0]['name']
    with pytest.raises(ValueError, match='duplicate target'):
        validate_action_entity_contract(broken)
    broken = json.loads(json.dumps(CONTRACT))
    broken['sources'][0]['fields'].append(dict(broken['sources'][0]['fields'][0]))
    with pytest.raises(ValueError, match='duplicate field'):
        validate_action_entity_contract(broken)
    for disposition in ('retain', 'reference', 'retire'):
        for key in ('inverse_rule', 'consumers'):
            broken = json.loads(json.dumps(CONTRACT))
            field = next(field for source in broken['sources'] for field in source['fields'] if field['disposition'] == disposition)
            field.pop(key)
            with pytest.raises(ValueError, match=key):
                validate_action_entity_contract(broken)


def test_equal_source_public_ids_remain_distinct_action_identities():
    assert source_identity('email_actions', '7') != source_identity('candidate_action_attempts', '7')
    assert source_identity('sent_replies', 7) != source_identity('sent_replies', '7')
    with sqlite3.connect(':memory:') as db:
        install_action_target(db)
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (1,70,NULL,jsonb(?),jsonb('[]'))", (json.dumps({'operation':'email.send','status':'done'}),))
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (2,70,NULL,jsonb(?),jsonb('[]'))", (json.dumps({'operation':'reviewed.publish','status':'done'}),))
        assert db.execute("select id from actions where record_type='action' order by id").fetchall() == [(1,), (2,)]
        assert CONTRACT['identity']['public_id_is_identity'] is False
        source_refs = [('email_actions', '7'), ('candidate_action_attempts', '7')]
        allocated = {source_identity(table, source_id): physical_id for physical_id, (table, source_id) in enumerate(source_refs, 1)}
        assert allocated[source_identity('email_actions', '7')] == 1
        assert allocated[source_identity('candidate_action_attempts', '7')] == 2


def test_multiple_observers_reference_one_effect():
    with sqlite3.connect(':memory:') as db:
        db.execute('pragma foreign_keys=on')
        install_action_target(db)
        db.execute('pragma foreign_keys=off')
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (1,70,NULL,jsonb(?),jsonb('[]'))", (json.dumps({'operation':'message.send','status':'done'}),))
        db.commit()
        db.execute('pragma foreign_keys=on')
        db.execute("insert into action_observers values(1,10,20,'t')")
        db.execute("insert into action_observers values(1,11,21,'t')")
        assert db.execute("select count(*) from actions where record_type='action'").fetchone()[0] == 1
        assert db.execute('select count(*) from action_observers where action_id=1').fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into action_observers values(1,10,22,'t')")



def test_duplicate_execution_keys_share_one_action_without_losing_local_outcomes():
    with sqlite3.connect(':memory:') as db:
        install_action_target(db)
        db.execute('pragma foreign_keys=off')
        body = json.dumps({'external_action_key':'stable-key'})
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (1,70,NULL,jsonb(?),jsonb('[]'))", (body,))
        db.execute("insert into action_receipts(action_id,operation,target,provider_result_json,status,created_at) values (1,?,?,?,?,?)", ('send','["to"]','{"accepted":true}','verified','provider-time'))
        db.execute("insert into action_receipts(action_id,status,execution_id,action_index,outcome_json,created_at,updated_at) values (1,'verified',10,0,NULL,'first-created','first-updated')")
        db.execute("insert into action_receipts(action_id,status,execution_id,action_index,outcome_json,created_at,updated_at) values (1,'verified',11,0,'2','second-created','second-updated')")
        assert db.execute("select count(*) from actions where record_type='action'").fetchone()[0] == 1
        assert db.execute("select count(*) from action_receipts where provider_result_json is not null").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (2,70,NULL,jsonb(?),jsonb('[]'))", (body,))
        assert db.execute("select execution_id,action_index,status,outcome_json,created_at,updated_at from action_receipts where execution_id is not null order by execution_id").fetchall() == [
            (10,0,'verified',None,'first-created','first-updated'),
            (11,0,'verified','2','second-created','second-updated'),
        ]


def test_null_reference_stays_bound_to_original_provider_receipt_after_correction():
    with sqlite3.connect(':memory:') as db:
        db.execute('pragma foreign_keys=on')
        install_action_target(db)
        db.execute('pragma foreign_keys=off')
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (1,70,NULL,jsonb(?),jsonb('[]'))", (json.dumps({'external_action_key':'stable-key','status':'verified'}),))
        db.commit()
        db.execute('pragma foreign_keys=on')
        db.execute("insert into action_receipts(id,action_id,provider_result_json,result_digest,created_at) values (1,1,'{\"accepted\":true}','d1','v1')")
        db.execute("insert into action_receipts(id,action_id,status,execution_id,action_index,outcome_json,provider_receipt_id,updated_at) values (2,1,'verified',10,0,NULL,1,'t1')")
        db.execute("insert into action_receipts(id,action_id,provider_result_json,result_digest,created_at) values (3,1,'{\"accepted\":true,\"corrected\":true}','d2','v2')")
        assert db.execute('select provider_receipt_id,outcome_json from action_receipts where id=2').fetchone() == (1, None)
        assert db.execute('select r.provider_result_json from action_receipts o join action_receipts r on r.id=o.provider_receipt_id where o.id=2').fetchone()[0] == '{"accepted":true}'
        assert db.execute('select provider_result_json from action_receipts where id=3').fetchone()[0] == '{"accepted":true,"corrected":true}'
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into action_receipts(action_id,status,execution_id,action_index,provider_receipt_id) values (1,'verified',11,0,999)")


def test_missing_parent_and_distinct_receipt_facts_are_preserved():
    with sqlite3.connect(':memory:') as db:
        db.execute('pragma foreign_keys=on')
        install_action_target(db)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into action_receipts(action_id) values (404)")
        db.rollback()
        db.execute('pragma foreign_keys=off')
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (1,70,NULL,jsonb(?),jsonb('[]'))", (json.dumps({'status':'failed'}),))
        db.commit()
        db.execute('pragma foreign_keys=on')
        db.execute("insert into action_receipts(action_id,status,provider_result_json,observed_at) values (1,'failed',NULL,'t1')")
        db.execute("insert into action_receipts(action_id,status,provider_result_json,observed_at) values (1,'uncertain','null','t2')")
        db.execute("insert into action_receipts(action_id,status,provider_result_json,observed_at) values (1,'uncertain','{}','t3')")
        rows = db.execute('select status,provider_result_json,operation,target,dispatched_at from action_receipts order by id').fetchall()
        assert rows == [('failed', None, None, None, None), ('uncertain', 'null', None, None, None), ('uncertain', '{}', None, None, None)]
        action = db.execute('select action_status,action_operation,action_action_plan_id,typeof(action_action_plan_id) from actions where id=1').fetchone()
        assert action == ('failed', None, None, 'null')
        assert CONTRACT['receipts']['preserve_distinct_values'] is True
        assert CONTRACT['receipts']['missing_and_empty_are_distinct'] is True



def test_action_plan_identifier_keeps_text_affinity_and_unknown_provider_facts_absent():
    with sqlite3.connect(':memory:') as db:
        install_action_target(db)
        db.execute('pragma foreign_keys=off')
        body = json.dumps({'action_plan_id': '007'})
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (1,70,NULL,jsonb(?),jsonb('[]'))", (body,))
        row = db.execute('select action_action_plan_id,typeof(action_action_plan_id) from actions where id=1').fetchone()
        columns = {entry[1] for entry in db.execute('pragma table_xinfo(actions)')}
        assert row == ('007', 'text')
        assert not {'action_finished_at', 'action_current_provider_operation', 'action_current_provider_target', 'action_current_provider_result_id', 'action_current_error_text'} & columns


def test_inspection_is_read_only_and_counts_exact_existing_keys(tmp_path):
    source = tmp_path / 'source.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('create table actions(record_type text, legacy_key text, sent_reply_id integer, agent_run_id integer, postfix_dispatch_created_at text, postfix_receipt_created_at text, send_result_json text, external_action_key text, status text, result_json text, execution_id integer, operation text, target_identifiers_json text, provider_result_json text)')
        db.executemany('insert into actions(record_type,legacy_key,sent_reply_id,agent_run_id) values(?,?,?,?)', [
            ('email_actions', '7', None, None),
            ('sent_replies', '7', None, None),
            ('sent_reply_observers', '[2,10]', 2, 10),
            ('sent_reply_observers', '[2,11]', 2, 11),
        ])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report = inspect_action_entity_source(source)
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert before == after == report['database_sha256']
    assert report['source_rows'] == 4
    assert report['equal_public_id_groups'] == 1
    assert report['observer_groups'] == {
        'rows': 2, 'sent_replies': 1, 'unique_sent_reply_run_pairs': 2,
    }


def test_target_ddl_has_one_action_layout_and_runs():
    with sqlite3.connect(':memory:') as db:
        install_action_target(db)
        columns = {row[1] for row in db.execute('pragma table_xinfo(actions)')}
        assert {'action_operation', 'action_parameters_json', 'action_external_action_key'} <= columns
        assert 'action_outcome_json' not in columns
        receipt_columns = {row[1] for row in db.execute('pragma table_info(action_receipts)')}
        assert 'outcome_json' in receipt_columns
        assert 'action_source_type' not in columns
        db.execute('pragma foreign_keys=off')
        allowed = set(CONTRACT['state_mappings']['action_status']['declared_values'])
        for action_id, status in enumerate(sorted(allowed), 1):
            db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (?,70,NULL,jsonb(?),jsonb('[]'))", (action_id, json.dumps({'status': status})))
        assert {row[0] for row in db.execute("select action_status from actions where record_type='action'")} == allowed
        db.execute("insert into action_receipts(action_id,status,execution_id,action_index,updated_at) values (1,'failed',100,0,'2026-01-01T00:00:00Z')")
        db.execute("insert into action_receipts(action_id,status,execution_id,action_index,updated_at) values (1,'verified',101,0,'2026-01-02T00:00:00Z')")
        observations = db.execute("select status,execution_id,action_index,updated_at from action_receipts where action_id=1 order by updated_at,execution_id,action_index,id").fetchall()
        assert observations == [('failed',100,0,'2026-01-01T00:00:00Z'), ('verified',101,0,'2026-01-02T00:00:00Z')]
        assert observations[-1][0] == 'verified'
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (999,70,NULL,jsonb(?),jsonb('[]'))", (json.dumps({'status':'unknown'}),))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into action_receipts(action_id,status) values (999,'unknown')")
    broken = json.loads(json.dumps(CONTRACT))
    broken['target']['layout_selector'] = 'source_type'
    with pytest.raises(ValueError, match='source|origin|layout'):
        validate_action_entity_contract(broken)
