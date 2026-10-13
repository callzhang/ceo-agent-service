"""Exact nine-type actions and postfix closure -> twelve-type actions.

Schema inverse retains current business facts, identity and native watermarks.
No runtime marker, provider call, compatibility view or replay is involved.
"""
from functools import lru_cache
from contextlib import closing
import sqlite3

from app import action_storage as storage
from app.action_receipt_storage import _available_replacement_names, _sequence
from app.run_storage import _set_sequence

SOURCE_POSTFIX_DECLARATIONS = {'outbound_postfixes': {'columns': ['channel',
                                    'delivery_key',
                                    'final_body',
                                    'feedback_token',
                                    'postfix_version',
                                    'created_at'],
                        'objects': [{'type': 'table',
                                     'name': 'outbound_postfixes',
                                     'sql': 'CREATE TABLE outbound_postfixes (\n'
                                            '                    channel text not null check(channel in '
                                            "('dingtalk', 'wechat')),\n"
                                            '                    delivery_key text not null '
                                            "check(trim(delivery_key) <> ''),\n"
                                            '                    final_body text not null '
                                            "check(trim(final_body) <> ''),\n"
                                            '                    feedback_token text not null default '
                                            "'',\n"
                                            '                    postfix_version text not null,\n'
                                            '                    created_at text not null default '
                                            'current_timestamp,\n'
                                            '                    primary key(channel, delivery_key)\n'
                                            '                )'}]},
 'outbound_postfix_receipts': {'columns': ['channel',
                                           'delivery_key',
                                           'provider_result_json',
                                           'created_at'],
                               'objects': [{'type': 'table',
                                            'name': 'outbound_postfix_receipts',
                                            'sql': 'CREATE TABLE outbound_postfix_receipts (\n'
                                                   '                    channel text not null '
                                                   "check(channel in ('dingtalk', 'wechat')),\n"
                                                   '                    delivery_key text not null '
                                                   "check(trim(delivery_key) <> ''),\n"
                                                   '                    provider_result_json text not '
                                                   'null,\n'
                                                   '                    created_at text not null '
                                                   'default current_timestamp,\n'
                                                   '                    primary key(channel, '
                                                   'delivery_key),\n'
                                                   '                    foreign key(channel, '
                                                   'delivery_key)\n'
                                                   '                        references '
                                                   'outbound_postfixes(channel, delivery_key)\n'
                                                   '                )'}]},
 'native_reply_dispatches': {'columns': ['channel', 'delivery_key', 'created_at'],
                             'objects': [{'type': 'table',
                                          'name': 'native_reply_dispatches',
                                          'sql': 'CREATE TABLE native_reply_dispatches (\n'
                                                 '                    channel text not null,\n'
                                                 '                    delivery_key text not null,\n'
                                                 '                    created_at text not null default '
                                                 'current_timestamp,\n'
                                                 '                    primary key(channel, '
                                                 'delivery_key),\n'
                                                 '                    foreign key(channel, '
                                                 'delivery_key)\n'
                                                 '                        references '
                                                 'outbound_postfixes(channel, delivery_key)\n'
                                                 '                )'}]}}


def _declarations(db):
    return {kind: {'columns': [row[1] for row in db.execute(f'pragma table_info({kind})')],
                   'objects': storage._objects(db, kind)}
            for kind in storage.POSTFIX_ACTION_TYPES}


def _sequences(db):
    return {kind: _sequence(db, name) for kind, name in storage.ACTION_SEQUENCE_NAMES.items()}


def _restore_sequences(db, values):
    for kind, value in values.items():
        name = storage.ACTION_SEQUENCE_NAMES[kind]
        if value is None:
            db.execute('delete from sqlite_sequence where name=?', (name,))
        else:
            _set_sequence(db, name, value)


def _validate_actions(db, include_postfix):
    if db.execute('pragma foreign_key_check(actions)').fetchone():
        raise ValueError('invalid current action reference')
    if [row[0] for row in db.execute('pragma integrity_check(actions)')] != ['ok']:
        raise ValueError('invalid current action values')
    kinds = storage.ALL_ACTION_TYPES_WITH_POSTFIX if include_postfix else storage.ALL_ACTION_TYPES
    observed = 0
    for kind in kinds:
        rows = db.execute(storage.ACTION_PROJECTIONS[kind]).fetchall()
        for row in rows:
            storage._check_fields(kind, dict(zip(storage.SOURCE_COLUMNS[kind], row, strict=True)))
        observed += len(rows)
    if observed != db.execute('select count(*) from actions').fetchone()[0]:
        raise ValueError('unrepresentable current action identity')
    for field, (parent, parent_kind) in storage._references_for(kinds).items():
        if db.execute(f'select 1 from actions a join {parent} p on p.id=a.{field} '
                      'where p.record_type<>? limit 1', (parent_kind,)).fetchone():
            raise ValueError(f'invalid current action parent type: {field}')


def capture_action_postfix_schema(db):
    if not storage.action_schema_is_current(db, include_receipts=True):
        raise ValueError('unsupported nine-type source actions')
    tables = _declarations(db)
    if tables != SOURCE_POSTFIX_DECLARATIONS:
        raise ValueError('unsupported postfix source declaration')
    actions = storage._objects(db, 'actions')
    _available_replacement_names(db, storage._current_objects(True, include_postfix=True), actions)
    targets = {'actions', *storage.POSTFIX_ACTION_TYPES}
    storage._no_inbound(db, targets)
    storage._no_sql_dependencies(db, targets)
    return {'actions': actions, 'tables': tables, 'sequences': _sequences(db)}


@lru_cache(maxsize=1)
def _stored_columns():
    with closing(sqlite3.connect(':memory:')) as db:
        storage._create_action_objects(db, True)
        return tuple(row[1] for row in db.execute('pragma table_xinfo(actions)') if row[6] == 0)


def _nine_rows(db):
    columns = _stored_columns()
    rows = db.execute(f"select {','.join(columns)} from actions where record_type in "
                      f"({','.join('?' for _ in storage.ALL_ACTION_TYPES)}) order by id",
                      storage.ALL_ACTION_TYPES).fetchall()
    return columns, [tuple(row) for row in rows]


def _insert_nine(db, columns, rows):
    db.executemany(f"insert into actions({','.join(columns)}) values({','.join('?' for _ in columns)})", rows)


def _postfix_rows(db, target=False):
    rows = {}
    for kind in storage.POSTFIX_ACTION_TYPES:
        if not target:
            if db.execute(f'pragma foreign_key_check({kind})').fetchone():
                raise ValueError(f'invalid postfix source reference: {kind}')
            if [row[0] for row in db.execute(f'pragma integrity_check({kind})')] != ['ok']:
                raise ValueError(f'invalid postfix source values: {kind}')
        fields = storage.SOURCE_COLUMNS[kind]
        source = f'({storage.ACTION_PROJECTIONS[kind]})' if target else kind
        rows[kind] = [dict(zip(fields, row, strict=True)) for row in db.execute(
            f"select {','.join(fields)} from {source} order by channel,delivery_key")]
        for values in rows[kind]:
            storage._check_fields(kind, values)
            if any(value is None for value in values.values()):
                raise ValueError(f'unrepresentable postfix null: {kind}')
    return rows


def migrate_action_postfix_schema(db):
    storage._require_transaction(db)
    state = capture_action_postfix_schema(db)
    _validate_actions(db, False)
    postfixes = _postfix_rows(db)
    columns, nine = _nine_rows(db)
    db.execute('drop table actions')
    storage._create_action_objects(db, True, include_postfix=True)
    _insert_nine(db, columns, nine)
    for kind, rows in postfixes.items():
        for values in rows:
            storage.insert_action(db, kind, values)
    _restore_sequences(db, state['sequences'])
    for kind in storage.POSTFIX_ACTION_TYPES:
        db.execute(f'drop table {kind}')
    _validate_actions(db, True)
    return {'counts': {kind: len(rows) for kind, rows in postfixes.items()}}


def restore_action_postfix_schema(db, state):
    storage._require_transaction(db)
    if (set(state) != {'actions', 'tables', 'sequences'}
            or state['actions'] != storage._current_objects(True)
            or state['tables'] != SOURCE_POSTFIX_DECLARATIONS
            or set(state['sequences']) != set(storage.AUTOINCREMENT_TYPES)
            or any(value is not None and (type(value) is not int or not 0 <= value < 2**63)
                   for value in state['sequences'].values())):
        raise ValueError('unsupported postfix inverse source metadata')
    if not storage.action_schema_is_current(db, include_postfix=True):
        raise ValueError('unsupported twelve-type actions declaration')
    _available_replacement_names(db, state['actions'], storage._objects(db, 'actions'))
    storage._require_available_names(db, [obj for source in state['tables'].values() for obj in source['objects']])
    storage._no_inbound(db, {'actions'})
    storage._no_sql_dependencies(db, {'actions'})
    _validate_actions(db, True)
    current_sequences = _sequences(db)
    postfixes = _postfix_rows(db, target=True)
    columns, nine = _nine_rows(db)
    db.execute('drop table actions')
    storage._create_action_objects(db, True)
    _insert_nine(db, columns, nine)
    _restore_sequences(db, current_sequences)
    for source in state['tables'].values():
        for obj in source['objects']:
            db.execute(obj['sql'])
    for kind, rows in postfixes.items():
        fields = storage.SOURCE_COLUMNS[kind]
        db.executemany(f"insert into {kind}({','.join(fields)}) values({','.join('?' for _ in fields)})",
                       [tuple(row[field] for field in fields) for row in rows])
    for kind in storage.POSTFIX_ACTION_TYPES:
        if db.execute(f'pragma foreign_key_check({kind})').fetchone():
            raise ValueError(f'invalid restored postfix reference: {kind}')
