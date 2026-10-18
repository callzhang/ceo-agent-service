"""Finite, opt-in sent delivery migration between twelve and fourteen action types.

Caller owns the foreign-key-disabled transaction. Inverse reads current facts.
"""

from contextlib import closing
from functools import lru_cache
import sqlite3

from app import action_storage as storage
from app.action_receipt_storage import _available_replacement_names, _sequence
from app.run_storage import _set_sequence

ACTUAL_SOURCE = {'sent_replies': {'columns': [{'cid': 0,
                               'default': None,
                               'name': 'id',
                               'notnull': 0,
                               'pk': 1,
                               'type': 'INTEGER'},
                              {'cid': 1,
                               'default': None,
                               'name': 'conversation_id',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 2,
                               'default': None,
                               'name': 'trigger_message_id',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 3,
                               'default': None,
                               'name': 'reply_text',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 4,
                               'default': 'current_timestamp',
                               'name': 'sent_at',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 5,
                               'default': "''",
                               'name': 'send_result_json',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 6,
                               'default': "''",
                               'name': 'recall_key',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 7,
                               'default': "''",
                               'name': 'recall_status',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 8,
                               'default': "''",
                               'name': 'recall_error',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 9,
                               'default': None,
                               'name': 'recalled_at',
                               'notnull': 0,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 10,
                               'default': "''",
                               'name': 'feedback_token',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 11,
                               'default': "'dingtalk'",
                               'name': 'channel',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 12,
                               'default': '0',
                               'name': 'agent_run_id',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'INTEGER'},
                              {'cid': 13,
                               'default': "''",
                               'name': 'external_action_key',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'}],
                  'foreign_keys': [],
                  'objects': [{'name': 'idx_sent_replies_external_action',
                               'sql': 'CREATE UNIQUE INDEX idx_sent_replies_external_action on '
                                      'sent_replies(external_action_key) where '
                                      "external_action_key<>''",
                               'type': 'index'},
                              {'name': 'idx_sent_replies_external_action_lookup',
                               'sql': 'CREATE INDEX idx_sent_replies_external_action_lookup on '
                                      'sent_replies(external_action_key)',
                               'type': 'index'},
                              {'name': 'idx_sent_replies_history',
                               'sql': 'CREATE INDEX idx_sent_replies_history\n'
                                      '                    on sent_replies(\n'
                                      '                        conversation_id, '
                                      'trigger_message_id, sent_at\n'
                                      '                    )\n'
                                      '                ',
                               'type': 'index'},
                              {'name': 'sent_replies',
                               'sql': 'CREATE TABLE sent_replies (\n'
                                      '                    id integer primary key autoincrement,\n'
                                      '                    conversation_id text not null,\n'
                                      '                    trigger_message_id text not null,\n'
                                      '                    reply_text text not null,\n'
                                      '                    sent_at text not null default '
                                      'current_timestamp\n'
                                      '                , send_result_json text not null default '
                                      "'', recall_key text not null default '', recall_status text "
                                      "not null default '', recall_error text not null default '', "
                                      "recalled_at text, feedback_token text not null default '', "
                                      "channel text not null default 'dingtalk', agent_run_id "
                                      'integer not null default 0, external_action_key text not '
                                      "null default '')",
                               'type': 'table'}]},
 'sent_reply_observers': {'columns': [{'cid': 0,
                                       'default': None,
                                       'name': 'sent_reply_id',
                                       'notnull': 1,
                                       'pk': 1,
                                       'type': 'INTEGER'},
                                      {'cid': 1,
                                       'default': None,
                                       'name': 'agent_run_id',
                                       'notnull': 1,
                                       'pk': 2,
                                       'type': 'INTEGER'},
                                      {'cid': 2,
                                       'default': None,
                                       'name': 'reply_task_id',
                                       'notnull': 1,
                                       'pk': 0,
                                       'type': 'INTEGER'},
                                      {'cid': 3,
                                       'default': 'current_timestamp',
                                       'name': 'created_at',
                                       'notnull': 1,
                                       'pk': 0,
                                       'type': 'TEXT'}],
                          'foreign_keys': [[0,
                                            0,
                                            'tasks',
                                            'reply_task_id',
                                            'id',
                                            'NO ACTION',
                                            'NO ACTION',
                                            'NONE'],
                                           [1,
                                            0,
                                            'runs',
                                            'agent_run_id',
                                            'id',
                                            'NO ACTION',
                                            'NO ACTION',
                                            'NONE'],
                                           [2,
                                            0,
                                            'sent_replies',
                                            'sent_reply_id',
                                            'id',
                                            'NO ACTION',
                                            'NO ACTION',
                                            'NONE']],
                          'objects': [{'name': 'sent_reply_observers',
                                       'sql': 'CREATE TABLE sent_reply_observers (\n'
                                              '                    sent_reply_id integer not '
                                              'null,\n'
                                              '                    agent_run_id integer not null,\n'
                                              '                    reply_task_id integer not '
                                              'null,\n'
                                              '                    created_at text not null '
                                              'default current_timestamp,\n'
                                              '                    primary key(sent_reply_id, '
                                              'agent_run_id),\n'
                                              '                    foreign key(sent_reply_id) '
                                              'references sent_replies(id),\n'
                                              '                    foreign key(agent_run_id) '
                                              'references runs(id),\n'
                                              '                    foreign key(reply_task_id) '
                                              'references tasks(id)\n'
                                              '                )',
                                       'type': 'table'},
                                      {'name': 'trg_runs_child_sent_reply_observers_agent_run_id_insert',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_runs_child_sent_reply_observers_agent_run_id_insert '
                                              'before insert on "sent_reply_observers" when '
                                              'new."agent_run_id" is not null and exists(select 1 '
                                              'from runs where id=new."agent_run_id" and '
                                              "record_type<>'agent_runs') begin select "
                                              "raise(abort,'run parent must be a logical agent "
                                              "run'); end",
                                       'type': 'trigger'},
                                      {'name': 'trg_runs_child_sent_reply_observers_agent_run_id_update',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_runs_child_sent_reply_observers_agent_run_id_update '
                                              'before update of "agent_run_id" on '
                                              '"sent_reply_observers" when new."agent_run_id" is '
                                              'not null and exists(select 1 from runs where '
                                              'id=new."agent_run_id" and '
                                              "record_type<>'agent_runs') begin select "
                                              "raise(abort,'run parent must be a logical agent "
                                              "run'); end",
                                       'type': 'trigger'},
                                      {'name': 'trg_tasks_child_sent_reply_observers_reply_task_id_insert',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_tasks_child_sent_reply_observers_reply_task_id_insert '
                                              'before insert on "sent_reply_observers" when '
                                              'new."reply_task_id" is not null and exists(select 1 '
                                              'from tasks where id=new."reply_task_id" and '
                                              "record_type<>'reply_tasks') begin select "
                                              "raise(abort,'task parent must be a reply task'); "
                                              'end',
                                       'type': 'trigger'},
                                      {'name': 'trg_tasks_child_sent_reply_observers_reply_task_id_update',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_tasks_child_sent_reply_observers_reply_task_id_update '
                                              'before update of "reply_task_id" on '
                                              '"sent_reply_observers" when new."reply_task_id" is '
                                              'not null and exists(select 1 from tasks where '
                                              'id=new."reply_task_id" and '
                                              "record_type<>'reply_tasks') begin select "
                                              "raise(abort,'task parent must be a reply task'); "
                                              'end',
                                       'type': 'trigger'}]}}

FRESH_SOURCE = {'sent_replies': {'columns': [{'cid': 0,
                               'default': None,
                               'name': 'id',
                               'notnull': 0,
                               'pk': 1,
                               'type': 'INTEGER'},
                              {'cid': 1,
                               'default': None,
                               'name': 'conversation_id',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 2,
                               'default': None,
                               'name': 'trigger_message_id',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 3,
                               'default': None,
                               'name': 'reply_text',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 4,
                               'default': "''",
                               'name': 'send_result_json',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 5,
                               'default': "''",
                               'name': 'recall_key',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 6,
                               'default': "''",
                               'name': 'recall_status',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 7,
                               'default': "''",
                               'name': 'recall_error',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 8,
                               'default': None,
                               'name': 'recalled_at',
                               'notnull': 0,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 9,
                               'default': "''",
                               'name': 'feedback_token',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 10,
                               'default': '0',
                               'name': 'agent_run_id',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'INTEGER'},
                              {'cid': 11,
                               'default': "''",
                               'name': 'external_action_key',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 12,
                               'default': 'current_timestamp',
                               'name': 'sent_at',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'},
                              {'cid': 13,
                               'default': "'dingtalk'",
                               'name': 'channel',
                               'notnull': 1,
                               'pk': 0,
                               'type': 'TEXT'}],
                  'foreign_keys': [],
                  'objects': [{'name': 'idx_sent_replies_external_action',
                               'sql': 'CREATE UNIQUE INDEX idx_sent_replies_external_action on '
                                      'sent_replies(external_action_key) where '
                                      "external_action_key<>''",
                               'type': 'index'},
                              {'name': 'idx_sent_replies_external_action_lookup',
                               'sql': 'CREATE INDEX idx_sent_replies_external_action_lookup on '
                                      'sent_replies(external_action_key)',
                               'type': 'index'},
                              {'name': 'idx_sent_replies_history',
                               'sql': 'CREATE INDEX idx_sent_replies_history\n'
                                      '                    on sent_replies(\n'
                                      '                        conversation_id, '
                                      'trigger_message_id, sent_at\n'
                                      '                    )\n'
                                      '                ',
                               'type': 'index'},
                              {'name': 'sent_replies',
                               'sql': 'CREATE TABLE sent_replies (\n'
                                      '                    id integer primary key autoincrement,\n'
                                      '                    conversation_id text not null,\n'
                                      '                    trigger_message_id text not null,\n'
                                      '                    reply_text text not null,\n'
                                      '                    send_result_json text not null default '
                                      "'',\n"
                                      "                    recall_key text not null default '',\n"
                                      '                    recall_status text not null default '
                                      "'',\n"
                                      "                    recall_error text not null default '',\n"
                                      '                    recalled_at text,\n'
                                      '                    feedback_token text not null default '
                                      "'',\n"
                                      '                    agent_run_id integer not null default '
                                      '0,\n'
                                      '                    external_action_key text not null '
                                      "default '',\n"
                                      '                    sent_at text not null default '
                                      'current_timestamp\n'
                                      "                , channel text not null default 'dingtalk')",
                               'type': 'table'}]},
 'sent_reply_observers': {'columns': [{'cid': 0,
                                       'default': None,
                                       'name': 'sent_reply_id',
                                       'notnull': 1,
                                       'pk': 1,
                                       'type': 'INTEGER'},
                                      {'cid': 1,
                                       'default': None,
                                       'name': 'agent_run_id',
                                       'notnull': 1,
                                       'pk': 2,
                                       'type': 'INTEGER'},
                                      {'cid': 2,
                                       'default': None,
                                       'name': 'reply_task_id',
                                       'notnull': 1,
                                       'pk': 0,
                                       'type': 'INTEGER'},
                                      {'cid': 3,
                                       'default': 'current_timestamp',
                                       'name': 'created_at',
                                       'notnull': 1,
                                       'pk': 0,
                                       'type': 'TEXT'}],
                          'foreign_keys': [[0,
                                            0,
                                            'tasks',
                                            'reply_task_id',
                                            'id',
                                            'NO ACTION',
                                            'NO ACTION',
                                            'NONE'],
                                           [1,
                                            0,
                                            'runs',
                                            'agent_run_id',
                                            'id',
                                            'NO ACTION',
                                            'NO ACTION',
                                            'NONE'],
                                           [2,
                                            0,
                                            'sent_replies',
                                            'sent_reply_id',
                                            'id',
                                            'NO ACTION',
                                            'NO ACTION',
                                            'NONE']],
                          'objects': [{'name': 'sent_reply_observers',
                                       'sql': 'CREATE TABLE sent_reply_observers (\n'
                                              '                    sent_reply_id integer not '
                                              'null,\n'
                                              '                    agent_run_id integer not null,\n'
                                              '                    reply_task_id integer not '
                                              'null,\n'
                                              '                    created_at text not null '
                                              'default current_timestamp,\n'
                                              '                    primary key(sent_reply_id, '
                                              'agent_run_id),\n'
                                              '                    foreign key(sent_reply_id) '
                                              'references sent_replies(id),\n'
                                              '                    foreign key(agent_run_id) '
                                              'references runs(id),\n'
                                              '                    foreign key(reply_task_id) '
                                              'references tasks(id)\n'
                                              '                )',
                                       'type': 'table'},
                                      {'name': 'trg_runs_child_sent_reply_observers_agent_run_id_insert',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_runs_child_sent_reply_observers_agent_run_id_insert '
                                              'before insert on "sent_reply_observers" when '
                                              'new."agent_run_id" is not null and exists(select 1 '
                                              'from runs where id=new."agent_run_id" and '
                                              "record_type<>'agent_runs') begin select "
                                              "raise(abort,'run parent must be a logical agent "
                                              "run'); end",
                                       'type': 'trigger'},
                                      {'name': 'trg_runs_child_sent_reply_observers_agent_run_id_update',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_runs_child_sent_reply_observers_agent_run_id_update '
                                              'before update of "agent_run_id" on '
                                              '"sent_reply_observers" when new."agent_run_id" is '
                                              'not null and exists(select 1 from runs where '
                                              'id=new."agent_run_id" and '
                                              "record_type<>'agent_runs') begin select "
                                              "raise(abort,'run parent must be a logical agent "
                                              "run'); end",
                                       'type': 'trigger'},
                                      {'name': 'trg_tasks_child_sent_reply_observers_reply_task_id_insert',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_tasks_child_sent_reply_observers_reply_task_id_insert '
                                              'before insert on "sent_reply_observers" when '
                                              'new."reply_task_id" is not null and exists(select 1 '
                                              'from tasks where id=new."reply_task_id" and '
                                              "record_type<>'reply_tasks') begin select "
                                              "raise(abort,'task parent must be a reply task'); "
                                              'end',
                                       'type': 'trigger'},
                                      {'name': 'trg_tasks_child_sent_reply_observers_reply_task_id_update',
                                       'sql': 'CREATE TRIGGER '
                                              'trg_tasks_child_sent_reply_observers_reply_task_id_update '
                                              'before update of "reply_task_id" on '
                                              '"sent_reply_observers" when new."reply_task_id" is '
                                              'not null and exists(select 1 from tasks where '
                                              'id=new."reply_task_id" and '
                                              "record_type<>'reply_tasks') begin select "
                                              "raise(abort,'task parent must be a reply task'); "
                                              'end',
                                       'type': 'trigger'}]}}


def _declarations(db):
    result = {}
    for kind in storage.DELIVERY_ACTION_TYPES:
        result[kind] = {
            'columns': [dict(zip(('cid', 'name', 'type', 'notnull', 'default', 'pk'), tuple(row), strict=True))
                        for row in db.execute(f'pragma table_info({kind})')],
            'foreign_keys': [list(row) for row in db.execute(f'pragma foreign_key_list({kind})')],
            'objects': storage._objects(db, kind),
        }
    return result


def _source_variant(tables):
    if tables == ACTUAL_SOURCE:
        return ACTUAL_SOURCE
    if tables == FRESH_SOURCE:
        return FRESH_SOURCE
    raise ValueError('unsupported sent delivery source declaration')


def _sequences(db):
    return {kind: _sequence(db, name) for kind, name in storage.ACTION_SEQUENCE_NAMES.items()}


def _set_sequences(db, values):
    for kind, value in values.items():
        name = storage.ACTION_SEQUENCE_NAMES[kind]
        if value is None:
            db.execute('delete from sqlite_sequence where name=?', (name,))
        else:
            _set_sequence(db, name, value)


def _sent_sequence(db, *, target):
    value = _sequence(db, storage.DELIVERY_SEQUENCE_NAME if target else 'sent_replies')
    if target:
        maximum = db.execute("select max(cast(legacy_key as integer)) from actions "
                             "where record_type='sent_replies'").fetchone()[0]
    else:
        maximum = db.execute('select max(id) from sent_replies').fetchone()[0]
    if maximum is not None and (value is None or value < maximum):
        raise ValueError('invalid sent delivery allocator metadata')
    return value


def _set_sent_sequence(db, value, *, target):
    name = storage.DELIVERY_SEQUENCE_NAME if target else 'sent_replies'
    if value is None:
        db.execute('delete from sqlite_sequence where name=?', (name,))
    else:
        _set_sequence(db, name, value)


def _validate_actions(db, *, delivery):
    if db.execute('pragma foreign_key_check(actions)').fetchone():
        raise ValueError('invalid action reference')
    if [row[0] for row in db.execute('pragma integrity_check(actions)')] != ['ok']:
        raise ValueError('invalid action values')
    kinds = storage.ALL_ACTION_TYPES_WITH_DELIVERY if delivery else storage.ALL_ACTION_TYPES_WITH_POSTFIX
    observed = 0
    for kind in kinds:
        for row in db.execute(storage._projection(kind)):
            values = dict(zip(storage.SOURCE_COLUMNS[kind], tuple(row), strict=True))
            _validate_values(kind, values)
            observed += 1
    if observed != db.execute('select count(*) from actions').fetchone()[0]:
        raise ValueError('unrepresentable action identity or reference')
    for field, (parent, parent_kind) in storage._references_for(kinds).items():
        if db.execute(f'select 1 from actions a join {parent} p on p.id=a.{field} '
                      'where p.record_type<>? limit 1', (parent_kind,)).fetchone():
            raise ValueError(f'invalid action parent type: {field}')
    if delivery:
        if db.execute("select 1 from actions o join actions s on s.id=o.sent_reply_id "
                      "where o.record_type='sent_reply_observers' and "
                      "(s.record_type<>'sent_replies' or "
                      "json_extract(o.legacy_key,'$[0]')<>cast(s.legacy_key as integer)) limit 1").fetchone():
            raise ValueError('invalid observer sent reply identity')


def _validate_values(kind, values):
    storage._check_fields(kind, values)
    for field, value in values.items():
        if value is None and field not in storage.NULLABLE_PAYLOAD_FIELDS.get(kind, ()) \
                and field not in storage.NULLABLE:
            raise ValueError(f'unrepresentable null {kind}.{field}')
        if kind in storage.ENUMS and field == storage.ENUMS[kind][0] \
                and value not in storage.ENUMS[kind][1]:
            raise ValueError(f'unrepresentable enum {kind}.{field}')


def _delivery_rows(db, *, target):
    result = {}
    for kind in storage.DELIVERY_ACTION_TYPES:
        if not target:
            if db.execute(f'pragma foreign_key_check({kind})').fetchone():
                raise ValueError(f'invalid delivery source reference: {kind}')
            if [row[0] for row in db.execute(f'pragma integrity_check({kind})')] != ['ok']:
                raise ValueError(f'invalid delivery source values: {kind}')
        fields = storage.SOURCE_COLUMNS[kind]
        table = f'({storage._projection(kind)})' if target else kind
        order = 'id' if kind == 'sent_replies' else 'sent_reply_id,agent_run_id'
        rows = [dict(zip(fields, tuple(row), strict=True)) for row in db.execute(
            f"select {','.join(fields)} from {table} order by {order}")]
        for values in rows:
            _validate_values(kind, values)
        result[kind] = rows
    if not target:
        for field, parent, expected in (('agent_run_id', 'runs', 'agent_runs'),
                                         ('reply_task_id', 'tasks', 'reply_tasks')):
            if db.execute(f'select 1 from sent_reply_observers o join {parent} p '
                          f'on p.id=o.{field} where p.record_type<>? limit 1', (expected,)).fetchone():
                raise ValueError(f'invalid observer logical parent: {field}')
    return result


@lru_cache(maxsize=1)
def _stored_columns():
    with closing(sqlite3.connect(':memory:')) as db:
        storage._create_action_objects(db, True, include_postfix=True)
        return tuple(row[1] for row in db.execute('pragma table_xinfo(actions)') if row[6] == 0)


def _twelve_rows(db):
    columns = _stored_columns()
    rows = [tuple(row) for row in db.execute(
        f"select {','.join(columns)} from actions where record_type in "
        f"({','.join('?' for _ in storage.ALL_ACTION_TYPES_WITH_POSTFIX)}) order by id",
        storage.ALL_ACTION_TYPES_WITH_POSTFIX)]
    return columns, rows


def _insert_twelve(db, columns, rows):
    db.executemany(f"insert into actions({','.join(columns)}) values"
                   f"({','.join('?' for _ in columns)})", rows)


def capture_action_delivery_schema(db):
    if not storage.action_schema_is_current(db, include_postfix=True):
        raise ValueError('unsupported twelve-type actions source')
    tables = _declarations(db)
    _source_variant(tables)
    actions = storage._objects(db, 'actions')
    _available_replacement_names(db, storage._current_objects(True, include_postfix=True,
                                                               include_delivery=True), actions)
    if _sent_sequence(db, target=True) is not None:
        raise ValueError('sent target allocator already exists')
    targets = {'actions', *storage.DELIVERY_ACTION_TYPES}
    storage._no_inbound(db, targets)
    storage._no_sql_dependencies(db, targets)
    return {'actions': actions, 'tables': tables, 'sequences': _sequences(db),
            'sent_sequence': _sent_sequence(db, target=False)}


def migrate_action_delivery_schema(db):
    storage._require_transaction(db)
    state = capture_action_delivery_schema(db)
    _validate_actions(db, delivery=False)
    delivery = _delivery_rows(db, target=False)
    columns, twelve = _twelve_rows(db)
    db.execute('drop table sent_reply_observers')
    db.execute('drop table sent_replies')
    db.execute('drop table actions')
    storage._create_action_objects(db, True, include_postfix=True, include_delivery=True)
    _insert_twelve(db, columns, twelve)
    for kind in storage.DELIVERY_ACTION_TYPES:
        for values in delivery[kind]:
            storage.insert_action(db, kind, values)
    _set_sequences(db, state['sequences'])
    _set_sent_sequence(db, state['sent_sequence'], target=True)
    _validate_actions(db, delivery=True)
    return {'counts': {kind: len(rows) for kind, rows in delivery.items()}}


def _validate_state(state):
    if type(state) is not dict or set(state) != {'actions', 'tables', 'sequences', 'sent_sequence'} \
            or state['actions'] != storage._current_objects(True, include_postfix=True) \
            or type(state['tables']) is not dict \
            or not isinstance(state['sent_sequence'], (int, type(None))) \
            or type(state['sent_sequence']) is bool \
            or (state['sent_sequence'] is not None and not 0 <= state['sent_sequence'] < 2**63) \
            or type(state['sequences']) is not dict \
            or set(state['sequences']) != set(storage.AUTOINCREMENT_TYPES) \
            or any(value is not None and (type(value) is not int or not 0 <= value < 2**63)
                   for value in state['sequences'].values()):
        raise ValueError('unsupported delivery inverse metadata')
    _source_variant(state['tables'])


def restore_action_delivery_schema(db, state):
    storage._require_transaction(db)
    _validate_state(state)
    if not storage.action_schema_is_current(db, include_delivery=True):
        raise ValueError('unsupported fourteen-type actions declaration')
    _available_replacement_names(db, state['actions'], storage._objects(db, 'actions'))
    storage._require_available_names(db, [obj for table in state['tables'].values()
                                          for obj in table['objects']])
    storage._no_inbound(db, {'actions'})
    storage._no_sql_dependencies(db, {'actions'})
    _validate_actions(db, delivery=True)
    delivery = _delivery_rows(db, target=True)
    columns, twelve = _twelve_rows(db)
    receipt_sequences = _sequences(db)
    sent_sequence = _sent_sequence(db, target=True)
    db.execute('drop table actions')
    storage._create_action_objects(db, True, include_postfix=True)
    _insert_twelve(db, columns, twelve)
    _set_sequences(db, receipt_sequences)
    for kind in storage.DELIVERY_ACTION_TYPES:
        db.execute(next(obj['sql'] for obj in state['tables'][kind]['objects']
                        if obj['type'] == 'table'))
    for kind in storage.DELIVERY_ACTION_TYPES:
        for obj in state['tables'][kind]['objects']:
            if obj['type'] != 'table':
                db.execute(obj['sql'])
    for kind in storage.DELIVERY_ACTION_TYPES:
        fields = [col['name'] for col in state['tables'][kind]['columns']]
        db.executemany(f"insert into {kind}({','.join(fields)}) values"
                       f"({','.join('?' for _ in fields)})",
                       [tuple(row[field] for field in fields) for row in delivery[kind]])
    _set_sent_sequence(db, sent_sequence, target=False)
    db.execute('delete from sqlite_sequence where name=?', (storage.DELIVERY_SEQUENCE_NAME,))
    if _declarations(db) != state['tables'] or _delivery_rows(db, target=False) != delivery:
        raise ValueError('restored delivery facts differ from current actions')
    _validate_actions(db, delivery=False)
    return {'counts': {kind: len(rows) for kind, rows in delivery.items()}}
