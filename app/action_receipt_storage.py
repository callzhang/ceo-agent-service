"""Exact Store15 six-type actions plus three receipts -> nine-type actions.

Inverse reads current facts and native allocator metadata. No business-row
snapshot, compatibility view, dual write, provider call or policy is introduced.
"""

from contextlib import closing
from functools import lru_cache
import json
import sqlite3

from app import action_storage as storage
from app.run_storage import _set_sequence


SOURCE_RECEIPT_DECLARATIONS = {'external_action_results': {'columns': ['external_action_key',
                                         'business_object_key',
                                         'action_identity',
                                         'operation',
                                         'target_identifiers_json',
                                         'provider_result_json',
                                         'result_digest',
                                         'first_agent_run_id',
                                         'created_at'],
                             'objects': [{'type': 'index',
                                          'name': 'idx_external_action_results_business_object',
                                          'sql': 'CREATE INDEX '
                                                 'idx_external_action_results_business_object on '
                                                 'external_action_results(business_object_key, '
                                                 'external_action_key)'},
                                         {'type': 'table',
                                          'name': 'external_action_results',
                                          'sql': 'CREATE TABLE external_action_results (\n'
                                                 '                    external_action_key text '
                                                 'primary key\n'
                                                 '                        '
                                                 "check(trim(external_action_key) != ''),\n"
                                                 '                    business_object_key text not '
                                                 'null\n'
                                                 '                        '
                                                 "check(trim(business_object_key) != ''),\n"
                                                 '                    action_identity text not '
                                                 'null\n'
                                                 '                        '
                                                 "check(trim(action_identity) != ''),\n"
                                                 '                    operation text not null '
                                                 "check(trim(operation) != ''),\n"
                                                 '                    target_identifiers_json text '
                                                 'not null,\n'
                                                 '                    provider_result_json text '
                                                 'not null,\n'
                                                 '                    result_digest text not null '
                                                 "check(trim(result_digest) != ''),\n"
                                                 '                    first_agent_run_id integer '
                                                 'not null,\n'
                                                 '                    created_at text not null '
                                                 'default current_timestamp,\n'
                                                 '                    foreign '
                                                 'key(first_agent_run_id) references runs(id)\n'
                                                 '                )'},
                                         {'type': 'trigger',
                                          'name': 'trg_runs_child_external_action_results_first_agent_run_id_insert',
                                          'sql': 'CREATE TRIGGER '
                                                 'trg_runs_child_external_action_results_first_agent_run_id_insert '
                                                 'before insert on "external_action_results" when '
                                                 'new."first_agent_run_id" is not null and '
                                                 'exists(select 1 from runs where '
                                                 'id=new."first_agent_run_id" and '
                                                 "record_type<>'agent_runs') begin select "
                                                 "raise(abort,'run parent must be a logical agent "
                                                 "run'); end"},
                                         {'type': 'trigger',
                                          'name': 'trg_runs_child_external_action_results_first_agent_run_id_update',
                                          'sql': 'CREATE TRIGGER '
                                                 'trg_runs_child_external_action_results_first_agent_run_id_update '
                                                 'before update of "first_agent_run_id" on '
                                                 '"external_action_results" when '
                                                 'new."first_agent_run_id" is not null and '
                                                 'exists(select 1 from runs where '
                                                 'id=new."first_agent_run_id" and '
                                                 "record_type<>'agent_runs') begin select "
                                                 "raise(abort,'run parent must be a logical agent "
                                                 "run'); end"}]},
 'agent_effect_intents': {'columns': ['id',
                                      'agent_run_id',
                                      'authorization_id',
                                      'action_index',
                                      'receipt_operation_id',
                                      'capability',
                                      'operation',
                                      'operation_digest',
                                      'arguments_digest',
                                      'target_identifiers_json',
                                      'state',
                                      'result_digest',
                                      'exit_code',
                                      'prepared_at',
                                      'dispatched_at',
                                      'acknowledged_at',
                                      'updated_at',
                                      'external_action_key'],
                          'objects': [{'type': 'index',
                                       'name': 'idx_agent_effect_intents_operation',
                                       'sql': 'CREATE UNIQUE INDEX '
                                              'idx_agent_effect_intents_operation\n'
                                              '                    on '
                                              'agent_effect_intents(agent_run_id, '
                                              'receipt_operation_id)'},
                                      {'type': 'index',
                                       'name': 'idx_agent_effect_intents_run',
                                       'sql': 'CREATE INDEX idx_agent_effect_intents_run\n'
                                              '                    on '
                                              'agent_effect_intents(agent_run_id, action_index, '
                                              'id)'},
                                      {'type': 'table',
                                       'name': 'agent_effect_intents',
                                       'sql': 'CREATE TABLE agent_effect_intents (\n'
                                              '                    id integer primary key '
                                              'autoincrement,\n'
                                              '                    agent_run_id integer not null,\n'
                                              '                    authorization_id text not '
                                              'null,\n'
                                              '                    action_index integer not null '
                                              'check(action_index >= 0),\n'
                                              '                    receipt_operation_id text not '
                                              'null,\n'
                                              '                    capability text not null,\n'
                                              '                    operation text not null,\n'
                                              '                    operation_digest text not '
                                              'null,\n'
                                              '                    arguments_digest text not '
                                              'null,\n'
                                              '                    target_identifiers_json text '
                                              'not null,\n'
                                              '                    state text not null default '
                                              "'prepared'\n"
                                              "                        check(state in ('prepared', "
                                              "'dispatched', 'acknowledged')),\n"
                                              '                    result_digest text not null '
                                              "default '',\n"
                                              '                    exit_code integer,\n'
                                              '                    prepared_at text not null '
                                              'default current_timestamp,\n'
                                              '                    dispatched_at text not null '
                                              "default '',\n"
                                              '                    acknowledged_at text not null '
                                              "default '',\n"
                                              '                    updated_at text not null '
                                              'default current_timestamp, external_action_key text '
                                              "not null default '',\n"
                                              '                    unique(agent_run_id, '
                                              'authorization_id),\n'
                                              '                    foreign key(agent_run_id) '
                                              'references runs(id)\n'
                                              '                )'},
                                      {'type': 'trigger',
                                       'name': 'trg_runs_child_agent_effect_intents_agent_run_id_insert',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_runs_child_agent_effect_intents_agent_run_id_insert '
                                              'before insert on "agent_effect_intents" when '
                                              'new."agent_run_id" is not null and exists(select 1 '
                                              'from runs where id=new."agent_run_id" and '
                                              "record_type<>'agent_runs') begin select "
                                              "raise(abort,'run parent must be a logical agent "
                                              "run'); end"},
                                      {'type': 'trigger',
                                       'name': 'trg_runs_child_agent_effect_intents_agent_run_id_update',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_runs_child_agent_effect_intents_agent_run_id_update '
                                              'before update of "agent_run_id" on '
                                              '"agent_effect_intents" when new."agent_run_id" is '
                                              'not null and exists(select 1 from runs where '
                                              'id=new."agent_run_id" and '
                                              "record_type<>'agent_runs') begin select "
                                              "raise(abort,'run parent must be a logical agent "
                                              "run'); end"}]},
 'agent_execution_receipts': {'columns': ['id',
                                          'agent_run_id',
                                          'receipt_id',
                                          'operation_id',
                                          'cli',
                                          'command_path',
                                          'command_digest',
                                          'exit_code',
                                          'completed',
                                          'persisted',
                                          'safe_to_confirm',
                                          'created_at',
                                          'effect_counted'],
                              'objects': [{'type': 'index',
                                           'name': 'idx_agent_execution_receipts_run',
                                           'sql': 'CREATE INDEX idx_agent_execution_receipts_run\n'
                                                  '                    on '
                                                  'agent_execution_receipts(agent_run_id, id)'},
                                          {'type': 'table',
                                           'name': 'agent_execution_receipts',
                                           'sql': 'CREATE TABLE agent_execution_receipts (\n'
                                                  '                    id integer primary key '
                                                  'autoincrement,\n'
                                                  '                    agent_run_id integer not '
                                                  'null,\n'
                                                  '                    receipt_id text not null,\n'
                                                  '                    operation_id text not '
                                                  'null,\n'
                                                  '                    cli text not null,\n'
                                                  '                    command_path text not '
                                                  'null,\n'
                                                  '                    command_digest text not '
                                                  'null,\n'
                                                  '                    exit_code integer not '
                                                  'null,\n'
                                                  '                    completed integer not '
                                                  'null,\n'
                                                  '                    persisted integer not '
                                                  'null,\n'
                                                  '                    safe_to_confirm integer not '
                                                  'null,\n'
                                                  '                    created_at text not null '
                                                  'default current_timestamp, effect_counted '
                                                  'integer not null default 0,\n'
                                                  '                    unique(agent_run_id, '
                                                  'operation_id),\n'
                                                  '                    foreign key(agent_run_id) '
                                                  'references runs(id)\n'
                                                  '                )'},
                                          {'type': 'trigger',
                                           'name': 'trg_runs_child_agent_execution_receipts_agent_run_id_insert',
                                           'sql': 'CREATE TRIGGER '
                                                  'trg_runs_child_agent_execution_receipts_agent_run_id_insert '
                                                  'before insert on "agent_execution_receipts" '
                                                  'when new."agent_run_id" is not null and '
                                                  'exists(select 1 from runs where '
                                                  'id=new."agent_run_id" and '
                                                  "record_type<>'agent_runs') begin select "
                                                  "raise(abort,'run parent must be a logical agent "
                                                  "run'); end"},
                                          {'type': 'trigger',
                                           'name': 'trg_runs_child_agent_execution_receipts_agent_run_id_update',
                                           'sql': 'CREATE TRIGGER '
                                                  'trg_runs_child_agent_execution_receipts_agent_run_id_update '
                                                  'before update of "agent_run_id" on '
                                                  '"agent_execution_receipts" when '
                                                  'new."agent_run_id" is not null and '
                                                  'exists(select 1 from runs where '
                                                  'id=new."agent_run_id" and '
                                                  "record_type<>'agent_runs') begin select "
                                                  "raise(abort,'run parent must be a logical agent "
                                                  "run'); end"}]}}


# Exact empty Store15 declarations captured from committed 31d95f4e.
FRESH_RECEIPT_DECLARATIONS = {'external_action_results': {'columns': ['external_action_key',
                                         'business_object_key',
                                         'action_identity',
                                         'operation',
                                         'target_identifiers_json',
                                         'provider_result_json',
                                         'result_digest',
                                         'first_agent_run_id',
                                         'created_at'],
                             'objects': [{'type': 'index',
                                          'name': 'idx_external_action_results_business_object',
                                          'sql': 'CREATE INDEX '
                                                 'idx_external_action_results_business_object on '
                                                 'external_action_results(business_object_key, '
                                                 'external_action_key)'},
                                         {'type': 'table',
                                          'name': 'external_action_results',
                                          'sql': 'CREATE TABLE external_action_results (\n'
                                                 '                    external_action_key text '
                                                 'primary key\n'
                                                 '                        '
                                                 "check(trim(external_action_key) != ''),\n"
                                                 '                    business_object_key text not '
                                                 'null\n'
                                                 '                        '
                                                 "check(trim(business_object_key) != ''),\n"
                                                 '                    action_identity text not '
                                                 'null\n'
                                                 '                        '
                                                 "check(trim(action_identity) != ''),\n"
                                                 '                    operation text not null '
                                                 "check(trim(operation) != ''),\n"
                                                 '                    target_identifiers_json text '
                                                 'not null,\n'
                                                 '                    provider_result_json text '
                                                 'not null,\n'
                                                 '                    result_digest text not null '
                                                 "check(trim(result_digest) != ''),\n"
                                                 '                    first_agent_run_id integer '
                                                 'not null,\n'
                                                 '                    created_at text not null '
                                                 'default current_timestamp,\n'
                                                 '                    foreign '
                                                 'key(first_agent_run_id) references runs(id)\n'
                                                 '                )'},
                                         {'type': 'trigger',
                                          'name': 'trg_runs_child_external_action_results_first_agent_run_id_insert',
                                          'sql': 'CREATE TRIGGER '
                                                 'trg_runs_child_external_action_results_first_agent_run_id_insert '
                                                 'before insert on "external_action_results" when '
                                                 'new."first_agent_run_id" is not null and '
                                                 'exists(select 1 from runs where '
                                                 'id=new."first_agent_run_id" and '
                                                 "record_type<>'agent_runs') begin select "
                                                 "raise(abort,'run parent must be a logical agent "
                                                 "run'); end"},
                                         {'type': 'trigger',
                                          'name': 'trg_runs_child_external_action_results_first_agent_run_id_update',
                                          'sql': 'CREATE TRIGGER '
                                                 'trg_runs_child_external_action_results_first_agent_run_id_update '
                                                 'before update of "first_agent_run_id" on '
                                                 '"external_action_results" when '
                                                 'new."first_agent_run_id" is not null and '
                                                 'exists(select 1 from runs where '
                                                 'id=new."first_agent_run_id" and '
                                                 "record_type<>'agent_runs') begin select "
                                                 "raise(abort,'run parent must be a logical agent "
                                                 "run'); end"}]},
 'agent_effect_intents': {'columns': ['id',
                                      'agent_run_id',
                                      'authorization_id',
                                      'action_index',
                                      'receipt_operation_id',
                                      'capability',
                                      'operation',
                                      'operation_digest',
                                      'arguments_digest',
                                      'target_identifiers_json',
                                      'external_action_key',
                                      'state',
                                      'result_digest',
                                      'exit_code',
                                      'prepared_at',
                                      'dispatched_at',
                                      'acknowledged_at',
                                      'updated_at'],
                          'objects': [{'type': 'index',
                                       'name': 'idx_agent_effect_intents_operation',
                                       'sql': 'CREATE UNIQUE INDEX '
                                              'idx_agent_effect_intents_operation\n'
                                              '                    on '
                                              'agent_effect_intents(agent_run_id, '
                                              'receipt_operation_id)'},
                                      {'type': 'index',
                                       'name': 'idx_agent_effect_intents_run',
                                       'sql': 'CREATE INDEX idx_agent_effect_intents_run\n'
                                              '                    on '
                                              'agent_effect_intents(agent_run_id, action_index, '
                                              'id)'},
                                      {'type': 'table',
                                       'name': 'agent_effect_intents',
                                       'sql': 'CREATE TABLE agent_effect_intents (\n'
                                              '                    id integer primary key '
                                              'autoincrement,\n'
                                              '                    agent_run_id integer not null,\n'
                                              '                    authorization_id text not '
                                              'null,\n'
                                              '                    action_index integer not null '
                                              'check(action_index >= 0),\n'
                                              '                    receipt_operation_id text not '
                                              'null,\n'
                                              '                    capability text not null,\n'
                                              '                    operation text not null,\n'
                                              '                    operation_digest text not '
                                              'null,\n'
                                              '                    arguments_digest text not '
                                              'null,\n'
                                              '                    target_identifiers_json text '
                                              'not null,\n'
                                              '                    external_action_key text not '
                                              "null default '',\n"
                                              '                    state text not null default '
                                              "'prepared'\n"
                                              "                        check(state in ('prepared', "
                                              "'dispatched', 'acknowledged')),\n"
                                              '                    result_digest text not null '
                                              "default '',\n"
                                              '                    exit_code integer,\n'
                                              '                    prepared_at text not null '
                                              'default current_timestamp,\n'
                                              '                    dispatched_at text not null '
                                              "default '',\n"
                                              '                    acknowledged_at text not null '
                                              "default '',\n"
                                              '                    updated_at text not null '
                                              'default current_timestamp,\n'
                                              '                    unique(agent_run_id, '
                                              'authorization_id),\n'
                                              '                    foreign key(agent_run_id) '
                                              'references runs(id)\n'
                                              '                )'},
                                      {'type': 'trigger',
                                       'name': 'trg_runs_child_agent_effect_intents_agent_run_id_insert',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_runs_child_agent_effect_intents_agent_run_id_insert '
                                              'before insert on "agent_effect_intents" when '
                                              'new."agent_run_id" is not null and exists(select 1 '
                                              'from runs where id=new."agent_run_id" and '
                                              "record_type<>'agent_runs') begin select "
                                              "raise(abort,'run parent must be a logical agent "
                                              "run'); end"},
                                      {'type': 'trigger',
                                       'name': 'trg_runs_child_agent_effect_intents_agent_run_id_update',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_runs_child_agent_effect_intents_agent_run_id_update '
                                              'before update of "agent_run_id" on '
                                              '"agent_effect_intents" when new."agent_run_id" is '
                                              'not null and exists(select 1 from runs where '
                                              'id=new."agent_run_id" and '
                                              "record_type<>'agent_runs') begin select "
                                              "raise(abort,'run parent must be a logical agent "
                                              "run'); end"}]},
 'agent_execution_receipts': {'columns': ['id',
                                          'agent_run_id',
                                          'receipt_id',
                                          'operation_id',
                                          'cli',
                                          'command_path',
                                          'command_digest',
                                          'exit_code',
                                          'completed',
                                          'persisted',
                                          'safe_to_confirm',
                                          'effect_counted',
                                          'created_at'],
                              'objects': [{'type': 'index',
                                           'name': 'idx_agent_execution_receipts_run',
                                           'sql': 'CREATE INDEX idx_agent_execution_receipts_run\n'
                                                  '                    on '
                                                  'agent_execution_receipts(agent_run_id, id)'},
                                          {'type': 'table',
                                           'name': 'agent_execution_receipts',
                                           'sql': 'CREATE TABLE agent_execution_receipts (\n'
                                                  '                    id integer primary key '
                                                  'autoincrement,\n'
                                                  '                    agent_run_id integer not '
                                                  'null,\n'
                                                  '                    receipt_id text not null,\n'
                                                  '                    operation_id text not '
                                                  'null,\n'
                                                  '                    cli text not null,\n'
                                                  '                    command_path text not '
                                                  'null,\n'
                                                  '                    command_digest text not '
                                                  'null,\n'
                                                  '                    exit_code integer not '
                                                  'null,\n'
                                                  '                    completed integer not '
                                                  'null,\n'
                                                  '                    persisted integer not '
                                                  'null,\n'
                                                  '                    safe_to_confirm integer not '
                                                  'null,\n'
                                                  '                    effect_counted integer not '
                                                  'null default 0,\n'
                                                  '                    created_at text not null '
                                                  'default current_timestamp,\n'
                                                  '                    unique(agent_run_id, '
                                                  'operation_id),\n'
                                                  '                    foreign key(agent_run_id) '
                                                  'references runs(id)\n'
                                                  '                )'},
                                          {'type': 'trigger',
                                           'name': 'trg_runs_child_agent_execution_receipts_agent_run_id_insert',
                                           'sql': 'CREATE TRIGGER '
                                                  'trg_runs_child_agent_execution_receipts_agent_run_id_insert '
                                                  'before insert on "agent_execution_receipts" '
                                                  'when new."agent_run_id" is not null and '
                                                  'exists(select 1 from runs where '
                                                  'id=new."agent_run_id" and '
                                                  "record_type<>'agent_runs') begin select "
                                                  "raise(abort,'run parent must be a logical agent "
                                                  "run'); end"},
                                          {'type': 'trigger',
                                           'name': 'trg_runs_child_agent_execution_receipts_agent_run_id_update',
                                           'sql': 'CREATE TRIGGER '
                                                  'trg_runs_child_agent_execution_receipts_agent_run_id_update '
                                                  'before update of "agent_run_id" on '
                                                  '"agent_execution_receipts" when '
                                                  'new."agent_run_id" is not null and '
                                                  'exists(select 1 from runs where '
                                                  'id=new."agent_run_id" and '
                                                  "record_type<>'agent_runs') begin select "
                                                  "raise(abort,'run parent must be a logical agent "
                                                  "run'); end"}]}}


def _known_source_declarations(tables):
    return set(tables) == set(storage.RECEIPT_ACTION_TYPES) and all(
        tables[kind] in (SOURCE_RECEIPT_DECLARATIONS[kind], FRESH_RECEIPT_DECLARATIONS[kind])
        for kind in storage.RECEIPT_ACTION_TYPES
    )


def _sequence(db, name):
    rows = db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchall()
    if len(rows) > 1 or any(type(r[0]) is not int or not 0 <= r[0] < 2**63 for r in rows):
        raise ValueError(f"unsupported allocator metadata: {name}")
    return rows[0][0] if rows else None


def _source_declarations(db):
    return {kind: {"columns": [r[1] for r in db.execute(f"pragma table_info({kind})")],
                   "objects": storage._objects(db, kind)}
            for kind in storage.RECEIPT_ACTION_TYPES}


def _available_replacement_names(db, expected, owned):
    owned_names = {storage.sqlite_schema_name(obj['name']) for obj in owned}
    storage._require_available_names(db, [obj for obj in expected
                                         if storage.sqlite_schema_name(obj['name']) not in owned_names])


def capture_action_receipt_schema(db):
    """Read exact source declarations/allocator state; do not change the DB."""
    if not storage.action_schema_is_current(db, include_receipts=False):
        raise ValueError("unsupported Store15 actions declaration")
    tables = _source_declarations(db)
    if not _known_source_declarations(tables):
        raise ValueError("unsupported receipt source declaration")
    actions = storage._objects(db, 'actions')
    _available_replacement_names(db, storage._current_objects(True), actions)
    for name in storage.ACTION_SEQUENCE_NAMES.values():
        if db.execute("select 1 from sqlite_sequence where name=?", (name,)).fetchone():
            raise ValueError("receipt target allocator already exists in source")
    targets = {'actions', *storage.RECEIPT_ACTION_TYPES}
    storage._no_inbound(db, targets)
    storage._no_sql_dependencies(db, targets)
    return {'actions': actions, 'tables': tables,
            'sequences': {kind: _sequence(db, kind) for kind in storage.AUTOINCREMENT_TYPES}}


def _validate_actions(db, *, include_receipts):
    if db.execute('pragma foreign_key_check(actions)').fetchone():
        raise ValueError('invalid current action reference')
    if [r[0] for r in db.execute('pragma integrity_check(actions)')] != ['ok']:
        raise ValueError('invalid current action values')
    kinds = storage.ALL_ACTION_TYPES if include_receipts else storage.CANDIDATE_ACTION_TYPES
    for field, (parent, parent_kind) in storage._references_for(kinds).items():
        if db.execute(f"select 1 from actions a join {parent} p on p.id=a.{field} "
                      "where p.record_type<>? limit 1", (parent_kind,)).fetchone():
            raise ValueError(f'invalid current action parent type: {field}')
    observed = 0
    for kind in kinds:
        rows = db.execute(storage._projection(kind)).fetchall()
        for row in rows:
            storage._check_fields(kind, dict(zip(storage.SOURCE_COLUMNS[kind], row, strict=True)))
        observed += len(rows)
    if observed != db.execute('select count(*) from actions').fetchone()[0]:
        raise ValueError('unrepresentable current action identity')


def _source_rows(db):
    rows = {}
    for kind in storage.RECEIPT_ACTION_TYPES:
        if [r[0] for r in db.execute(f'pragma integrity_check({kind})')] != ['ok']:
            raise ValueError(f'invalid receipt source values: {kind}')
        fields = storage.SOURCE_COLUMNS[kind]
        identity = storage.IDENTITY_FIELDS[kind]
        rows[kind] = [dict(zip(fields, row, strict=True)) for row in db.execute(
            f"select {','.join(fields)} from {kind} order by {identity}"
        )]
        for values in rows[kind]:
            storage._check_fields(kind, values)
            for field, value in values.items():
                if value is None and field not in storage.NULLABLE_PAYLOAD_FIELDS.get(kind, ()):
                    raise ValueError(f'unrepresentable source null {kind}.{field}')
        if db.execute(f'pragma foreign_key_check({kind})').fetchone():
            raise ValueError(f'invalid receipt source reference: {kind}')
        for field, (parent, parent_kind) in storage._references_for((kind,)).items():
            if db.execute(f"select 1 from {kind} c join {parent} p on p.id=c.{field} "
                          "where p.record_type<>? limit 1", (parent_kind,)).fetchone():
                raise ValueError(f'invalid receipt source parent type: {kind}.{field}')
    return rows


@lru_cache(maxsize=1)
def _stored_source_action_columns():
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute(storage._action_definitions(False)[0])
        return tuple(row[1] for row in db.execute('pragma table_xinfo(actions)') if row[6] == 0)


def _stored_candidate_rows(db):
    fields = _stored_source_action_columns()
    kinds = storage.CANDIDATE_ACTION_TYPES
    return fields, [tuple(r) for r in db.execute(
        f"select {','.join(fields)} from actions "
        f"where record_type in ({','.join('?' for _ in kinds)}) order by id", kinds
    )]


def _insert_stored_candidates(db, columns, rows):
    db.executemany(f"insert into actions({','.join(columns)}) values({','.join('?' for _ in columns)})", rows)


def migrate_action_receipt_schema(db):
    storage._require_transaction(db)
    state = capture_action_receipt_schema(db)
    _validate_actions(db, include_receipts=False)
    receipts = _source_rows(db)
    columns, candidates = _stored_candidate_rows(db)
    db.execute('drop table actions')
    storage._create_action_objects(db, True)
    _insert_stored_candidates(db, columns, candidates)
    for kind, rows in receipts.items():
        for row in rows:
            values = {'record_type': kind, 'legacy_key': str(row[storage.IDENTITY_FIELDS[kind]]),
                      'payload_json': json.dumps({f: row[f] for f in storage.PAYLOAD_FIELDS[kind]},
                                                 ensure_ascii=False, separators=(',', ':'))}
            values.update({f: row[f] for f in storage.REFERENCES if f in row})
            db.execute(f"insert into actions({','.join(values)}) values({','.join('?' for _ in values)})",
                       tuple(values.values()))
    for kind in storage.AUTOINCREMENT_TYPES:
        value = state['sequences'][kind]
        name = storage.ACTION_SEQUENCE_NAMES[kind]
        if value is None:
            db.execute('delete from sqlite_sequence where name=?', (name,))
        else:
            _set_sequence(db, name, value)
    for kind in storage.RECEIPT_ACTION_TYPES:
        db.execute(f'drop table {kind}')
    return {'counts': {kind: len(rows) for kind, rows in receipts.items()}}


def restore_action_receipt_schema(db, state):
    storage._require_transaction(db)
    if (set(state) != {'actions', 'tables', 'sequences'}
            or state['actions'] != storage._current_objects(False)
            or not _known_source_declarations(state['tables'])
            or set(state['sequences']) != set(storage.AUTOINCREMENT_TYPES)
            or any(v is not None and (type(v) is not int or not 0 <= v < 2**63)
                   for v in state['sequences'].values())):
        raise ValueError('unsupported receipt inverse source metadata')
    if not storage.action_schema_is_current(db, include_receipts=True):
        raise ValueError('unsupported nine-type actions declaration')
    _available_replacement_names(db, state['actions'], storage._objects(db, 'actions'))
    storage._require_available_names(db, [obj for schema in state['tables'].values()
                                        for obj in schema['objects']])
    storage._no_inbound(db, {'actions'})
    storage._no_sql_dependencies(db, {'actions'})
    _validate_actions(db, include_receipts=True)
    sequences = {kind: _sequence(db, name) for kind, name in storage.ACTION_SEQUENCE_NAMES.items()}
    columns, candidates = _stored_candidate_rows(db)
    receipts = {kind: [tuple(row) for row in db.execute(storage._projection(kind))]
                for kind in storage.RECEIPT_ACTION_TYPES}
    db.execute('drop table actions')
    storage._create_action_objects(db, False)
    _insert_stored_candidates(db, columns, candidates)
    for kind, schema in state['tables'].items():
        db.execute(next(obj['sql'] for obj in schema['objects'] if obj['type'] == 'table'))
        fields = storage.SOURCE_COLUMNS[kind]
        db.executemany(f"insert into {kind}({','.join(fields)}) values({','.join('?' for _ in fields)})",
                       receipts[kind])
    for schema in state['tables'].values():
        for obj in schema['objects']:
            if obj['type'] != 'table':
                db.execute(obj['sql'])
    for kind, value in sequences.items():
        if value is None:
            db.execute('delete from sqlite_sequence where name=?', (kind,))
        else:
            _set_sequence(db, kind, value)
        db.execute('delete from sqlite_sequence where name=?', (storage.ACTION_SEQUENCE_NAMES[kind],))
    return {'counts': {kind: len(rows) for kind, rows in receipts.items()}}
