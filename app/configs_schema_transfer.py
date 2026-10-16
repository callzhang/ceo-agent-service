"""Unselected source-bound row transfer for the config/schedule release.

The release coordinator installs schemas and owns transactions/rollback. This
module never drops tables, changes markers, commits or persists a row snapshot.
Inverse transfer reads current target facts, including receipts written later.
"""
from contextlib import closing
from functools import lru_cache
import json
import sqlite3

from app.action_email_storage import _quote, _sequence, _set_sequence
from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_storage import _current_objects, _objects
from app.action_skill_config_storage import insert_skill_receipt, loaded_body
from app.configs_definitions import (
    AUTOINCREMENT_KINDS, CONFIG_FIELDS, CONFIG_REFS, ROWID_KINDS, SCALAR_KEY,
    config_projection,
)
from app.configs_storage import _identity, _stored, create_configs_schema
from app.schedules_storage import create_schedules_schema

RECEIPT_FIELDS = {
    'managed_skill_export_receipts': ('id', 'revision_id', 'sha256', 'path', 'created_at'),
    'runtime_skill_load_receipts': ('id', 'config_id', 'pid', 'loaded_json', 'error', 'created_at'),
}
SOURCE_TABLES = (*CONFIG_FIELDS, 'scheduled_tasks', *RECEIPT_FIELDS)
AUTO_KINDS = (*sorted(AUTOINCREMENT_KINDS), 'scheduled_tasks', *RECEIPT_FIELDS)
SOURCE_HASHES = {
    'actual': '4705ccca6a2fe6eef6be550baa99008fe8cdb7c767a50646ba2e516382575bf6',
    'fresh': '877b819ec78de375db8fdd6093cf1bb627a88769b55688cebd9e9cb7d4d23389',
    # Same native config fields after the existing Email13 migration translates
    # six account-owned blockers to typed actions. No mixed trigger set matches.
    'actual_email52': 'ae625fbb906925358c68e921c54eb047bfc574878bd2e8915b6bab0f76879bc3',
    'fresh_email52': '915d992f75a788da33181b2702b751e3948a4b48c590d43462abd07920d87756',
}
FRESH_VARIANTS = ('fresh', 'fresh_email52')


def _transaction(db, *, write=False):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError('config transfer requires caller transaction')
    if write and db.execute('pragma foreign_keys').fetchone()[0]:
        raise sqlite3.ProgrammingError('config transfer requires caller FK-off transaction')


def _no_temp(db, names):
    for name in names:
        if db.execute('select 1 from temp.sqlite_schema where name=? collate nocase',
                      (name,)).fetchone():
            raise ValueError('config transfer TEMP name collision')


def capture_config_declarations(db):
    """Capture only supported native declarations, never input/receipt bodies."""
    _transaction(db)
    _no_temp(db, SOURCE_TABLES)
    tables = {kind: _source_declaration(db, kind) for kind in SOURCE_TABLES}
    fingerprint = _fingerprint(tables)
    variant = next((key for key, expected in SOURCE_HASHES.items()
                    if fingerprint == expected), None)
    if variant is None:
        raise ValueError('unsupported config source declaration')
    return {'variant': variant, 'tables': tables,
            'rowid_kinds': list(ROWID_KINDS), 'auto_kinds': list(AUTO_KINDS)}


def _metadata(metadata):
    if (not isinstance(metadata, dict)
            or set(metadata) != {'variant', 'tables', 'rowid_kinds', 'auto_kinds'}
            or metadata['rowid_kinds'] != list(ROWID_KINDS)
            or metadata['auto_kinds'] != list(AUTO_KINDS)
            or metadata['variant'] not in SOURCE_HASHES
            or _fingerprint(metadata['tables']) != SOURCE_HASHES[metadata['variant']]):
        raise ValueError('unsupported config source declaration metadata')


@lru_cache(maxsize=1)
def _target_declarations():
    with closing(sqlite3.connect(':memory:')) as db:
        create_schedules_schema(db)
        create_configs_schema(db)
        return {kind: _source_declaration(db, kind) for kind in ('configs', 'schedules')}


def target_declaration_matches(db: sqlite3.Connection, kind: str, expected: dict) -> bool:
    """Compare target metadata; PRAGMA index_list.seq is only enumeration order."""
    actual = _source_declaration(db, kind)
    if any(actual[key] != expected[key]
           for key in ('sql', 'columns', 'foreign_keys', 'triggers')):
        return False
    actual_indexes = {item['name']: item for item in actual['indexes']}
    expected_indexes = {item['name']: item for item in expected['indexes']}
    return (set(actual_indexes) == set(expected_indexes)
            and all({key: value for key, value in item.items() if key != 'seq'}
                    == {key: value for key, value in expected_indexes[name].items()
                        if key != 'seq'}
                    for name, item in actual_indexes.items()))


def _target(db, *, empty=False):
    _no_temp(db, ('configs', 'schedules', 'actions'))
    if any(not target_declaration_matches(db, kind, definition)
           for kind, definition in _target_declarations().items()):
        raise ValueError('unsupported config target declaration')
    # During an in-place release, old receipt tables still exist until the
    # copy is verified. Check exact target objects, not final retirement state;
    # the runtime's action_schema_is_current contract remains unchanged.
    actual_actions = _objects(db, 'actions')
    if not any(actual_actions == _current_objects(
            include_reply=True, reply_variant=variant, include_email=True,
            include_followup_sync=followup, include_skill_config_receipts=True)
            for variant in ('actual_no_memory_fk', 'fresh_memory_fk')
            for followup in (False, True)):
        raise ValueError('unsupported config receipt target declaration')
    if empty:
        if (db.execute('select 1 from configs limit 1').fetchone()
                or db.execute('select 1 from schedules limit 1').fetchone()
                or db.execute('select 1 from actions where record_type in (?,?) limit 1',
                              tuple(RECEIPT_FIELDS)).fetchone()):
            raise ValueError('config transfer destination must be empty')
        for name in ('configs', 'schedules', *(f'configs:{k}' for k in AUTOINCREMENT_KINDS),
                     *(f'actions:{k}' for k in RECEIPT_FIELDS)):
            if _sequence(db, name) is not None:
                raise ValueError('config transfer destination allocator must be empty')


def _fields(metadata, kind):
    fields = tuple(c['name'] for c in metadata['tables'][kind]['columns'])
    return ('rowid', *fields) if kind in ROWID_KINDS else fields


def _native_rows(db, metadata):
    return {kind: [tuple(row) for row in db.execute(
        'select ' + ','.join(map(_quote, _fields(metadata, kind)))
        + f' from main.{_quote(kind)}')]
        for kind in SOURCE_TABLES}


def _row_signature(rows):
    # Preserve storage classes as well as values (Python considers 1 == 1.0).
    return sorted(tuple((type(value).__name__, repr(value)) for value in row)
                  for row in rows)


def _target_rows(db, metadata):
    rows = {kind: [tuple(row) for row in db.execute(config_projection(
        kind, fresh=metadata['variant'] in FRESH_VARIANTS, include_rowid=kind in ROWID_KINDS))]
        for kind in CONFIG_FIELDS}
    schedule_fields = ','.join(map(_quote, _fields(metadata, 'scheduled_tasks')))
    rows['scheduled_tasks'] = [tuple(row) for row in db.execute(
        f'select {schedule_fields} from main.schedules')]
    rows['managed_skill_export_receipts'] = [tuple(row) for row in db.execute(
        "select cast(legacy_key as integer),skill_revision_id,skill_sha256,"
        "skill_path,skill_created_at from main.actions "
        "where record_type='managed_skill_export_receipts'")]
    rows['runtime_skill_load_receipts'] = [
        (identity, config_id, pid, loaded_body(db, ref, error), error, created_at)
        for identity, config_id, pid, ref, error, created_at in db.execute(
            "select cast(legacy_key as integer),skill_config_id,skill_pid,"
            "runtime_config_ref,skill_error,skill_created_at from main.actions "
            "where record_type='runtime_skill_load_receipts'")]
    return rows


def _verify_rows(expected, actual):
    for kind in SOURCE_TABLES:
        if _row_signature(expected[kind]) != _row_signature(actual[kind]):
            raise ValueError(f'{kind} values or storage classes differ')


def _waters(db, *, target):
    return {kind: _sequence(db, (
        'schedules' if kind == 'scheduled_tasks' else
        'actions:' + kind if kind in RECEIPT_FIELDS else 'configs:' + kind
    ) if target else kind) for kind in AUTO_KINDS}


def _write_waters(db, waters, *, target):
    for kind, value in waters.items():
        name = ('schedules' if kind == 'scheduled_tasks' else
                'actions:' + kind if kind in RECEIPT_FIELDS else 'configs:' + kind)
        _set_sequence(db, name if target else kind, value)


def _target_foreign_keys(db):
    for table in ('configs', 'schedules'):
        if db.execute(f'pragma main.foreign_key_check({table})').fetchone():
            raise ValueError('copied config foreign keys differ')
    for _table, rowid, _parent, _group in db.execute('pragma main.foreign_key_check(actions)'):
        kind = db.execute('select record_type from main.actions where id=?', (rowid,)).fetchone()
        if kind and kind[0] in RECEIPT_FIELDS:
            raise ValueError('copied config receipt foreign keys differ')


def _preflight_bodies(facts):
    # Mirror target reconstruction, including the documented native BLOB gap.
    revisions = {row['id']: row for row in facts['managed_skill_revisions']}
    for receipt in facts['runtime_skill_load_receipts']:
        try:
            body = '{}' if receipt['error'] else json.dumps({
                int(binding['skill_id']): revisions[binding['revision_id']]['sha256']
                for binding in facts['runtime_skill_bindings']
                if binding['config_id'] == receipt['config_id'] and binding['enabled'] == 1
            }, sort_keys=True)
        except (KeyError, TypeError) as exc:
            raise ValueError('config receipt cannot reconstruct from source bindings') from exc
        if not isinstance(receipt['loaded_json'], str) or receipt['loaded_json'] != body:
            raise ValueError('config receipt body is not exactly reconstructable')


def copy_config_rows(source, target, metadata):
    """Copy eighteen families into installed empty target kinds; caller rolls back."""
    _transaction(source)
    _transaction(target, write=True)
    _metadata(metadata)
    if capture_config_declarations(source) != metadata:
        raise ValueError('config source declaration changed')
    _target(target, empty=True)
    rows = _native_rows(source, metadata)
    facts = {kind: [dict(zip(_fields(metadata, kind), row, strict=True)) for row in records]
             for kind, records in rows.items()}
    _preflight_bodies(facts)
    waters = _waters(source, target=False)
    physical = {}
    public = {}
    for kind in CONFIG_FIELDS:
        for row in facts[kind]:
            identity = row['rowid'] if kind in ROWID_KINDS else row[SCALAR_KEY[kind]]
            key = _identity(kind, identity)
            physical[kind, key] = len(physical) + 1
            if kind in SCALAR_KEY and row[SCALAR_KEY[kind]] is not None:
                public[kind, row[SCALAR_KEY[kind]]] = physical[kind, key]

    def parent(kind, key):
        if key is None:
            return None
        try:
            return public[kind, key]
        except KeyError as exc:
            raise ValueError('config source has a missing typed parent') from exc

    for row in facts['scheduled_tasks']:
        columns = ','.join(map(_quote, row))
        target.execute(f"insert into main.schedules({columns}) values({','.join('?' for _ in row)})",
                       tuple(row.values()))
    for kind in CONFIG_FIELDS:
        for row in facts[kind]:
            identity = row['rowid'] if kind in ROWID_KINDS else row[SCALAR_KEY[kind]]
            key = _identity(kind, identity)
            values = {field: row[field] for field in CONFIG_FIELDS[kind]}
            data = {'id': physical[kind, key], **_stored(target, kind, values, key)}
            for child, field, ref, parent_kind, _delete in CONFIG_REFS:
                if child == kind:
                    data[ref] = parent(parent_kind, row[field])
            if kind == 'email_category_folder_bindings':
                data['binding_account_ref'] = parent('email_accounts', row['account_id'])
                data['binding_category_ref'] = parent('email_category_configs', row['category_key'])
            if kind == 'email_model_mode_transitions':
                data['transition_promotion_ref'] = parent('email_model_promotion_configs', row['promotion_config_version'])
            columns = ','.join(map(_quote, data))
            target.execute(f"insert into main.configs({columns}) values({','.join('?' for _ in data)})",
                           tuple(data.values()))
    for kind in RECEIPT_FIELDS:
        for row in facts[kind]:
            insert_skill_receipt(target, kind, {key: value for key, value in row.items() if key != 'id'},
                                 source_id=row['id'])
    _write_waters(target, waters, target=True)
    _verify_rows(rows, _target_rows(target, metadata))
    _target_foreign_keys(target)
    return {kind: len(records) for kind, records in rows.items()}


def restore_config_rows(target, native, metadata):
    """Restore current target facts into installed empty native source families."""
    _transaction(target)
    _transaction(native, write=True)
    _metadata(metadata)
    _target(target)
    _target_foreign_keys(target)
    if capture_config_declarations(native) != metadata:
        raise ValueError('config inverse destination declaration changed')
    if any(native.execute(f'select 1 from main.{_quote(kind)} limit 1').fetchone()
           for kind in SOURCE_TABLES):
        raise ValueError('config inverse destination must be empty')
    if any(_sequence(native, kind) is not None for kind in AUTO_KINDS):
        raise ValueError('config inverse destination allocator must be empty')
    rows = _target_rows(target, metadata)
    waters = _waters(target, target=True)
    for kind, records in rows.items():
        fields = _fields(metadata, kind)
        native.executemany(f'insert into main.{_quote(kind)}('
                           + ','.join(map(_quote, fields)) + ') values('
                           + ','.join('?' for _ in fields) + ')', records)
    _write_waters(native, waters, target=False)
    _verify_rows(rows, _native_rows(native, metadata))
    return {kind: len(records) for kind, records in rows.items()}
