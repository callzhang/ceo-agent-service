import hashlib
import json
import sqlite3

import pytest

from app.action_email_definitions import EMAIL_FIELDS, EMAIL_CHECKS
from app.action_storage import SOURCE_COLUMNS, ENUMS
from app.prepared_action_definitions import COLUMNS as PREPARED_COLUMNS, SLOTS
from tools.action_entity_contract import (
    inspect_action_entity_source,
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
    for marker in ('create trigger trg_action_receipt_owner_insert', 'create trigger trg_action_observer_owner_insert'):
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



def test_state_contract_matches_source_enums_without_success_inference():
    values = CONTRACT['state_mappings']
    action_declared = next(check for check in EMAIL_CHECKS['email_actions'] if 'email_status in' in check)
    assert all(value in action_declared for value in values['action_status']['declared_values'])
    assert set(values['receipt_status']['declared_values']) == set(EMAIL_CHECKS['email_action_attempts'][-1].split("'")[1::2]) | set(ENUMS['candidate_action_attempts'][1])
    assert 'never infer done/verified from provider json or timestamps' in values['receipt_status']['semantic_rule'].lower()
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


def test_equal_source_public_ids_remain_distinct_action_identities():
    with sqlite3.connect(':memory:') as db:
        install_action_target(db)
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (1,70,NULL,jsonb(?),jsonb('[]'))", (json.dumps({'operation':'email.send','status':'done'}),))
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (2,70,NULL,jsonb(?),jsonb('[]'))", (json.dumps({'operation':'reviewed.publish','status':'done'}),))
        assert db.execute("select id from actions where record_type='action' order by id").fetchall() == [(1,), (2,)]
        assert 'public_id_is_identity' in CONTRACT['identity']
        assert CONTRACT['identity']['public_id_is_identity'] is False


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
        row = db.execute('select action_action_plan_id,typeof(action_action_plan_id),action_current_provider_operation,action_current_provider_target from actions where id=1').fetchone()
        assert row == ('007', 'text', None, None)


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
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) values (999,70,NULL,jsonb(?),jsonb('[]'))", (json.dumps({'status':'unknown'}),))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into action_receipts(action_id,status) values (999,'unknown')")
    broken = json.loads(json.dumps(CONTRACT))
    broken['target']['layout_selector'] = 'source_type'
    with pytest.raises(ValueError, match='source|origin|layout'):
        validate_action_entity_contract(broken)
