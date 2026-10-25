"""Source-shaped writes and reversible Business candidate conversion."""

import json

from app.action_storage import (
    _encoded_native_sqlite_value, _native_integer_primary_key, _source_bound_null,
    _raise_autoincrement_full, sqlite_schema_name,
)
from app.business_definitions import _definitions, BUSINESS_FIELDS
from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_email_storage import _external_dependencies, _quote, _sequence, _set_sequence
from app.business_schema_helpers import (
    _assert_transaction, _snapshot, _restore_rows, _insert_rows,
    business_schema_parent_is_current, _canonical_create,
)
from app.result_schema_helpers import _foreign_keys
from app.action_followup_sync_storage import LINK_TRIGGER_SQL
from app.business_candidate_definitions import (
    CANDIDATE_DEFAULTS, CANDIDATE_FIELDS, CANDIDATE_FRESH_FIELDS, CANDIDATE_KINDS,
    CANDIDATE_SOURCE_TABLES, SOURCE_PROFILES, candidate_column,
    candidate_payload_fields, candidate_projection,
)
from app.business_storage import BusinessWriteResult, _lock, _next_id


def _validate(kind, values):
    if kind not in CANDIDATE_KINDS or set(values) - set(CANDIDATE_FIELDS[kind]):
        raise ValueError('unsupported Business candidate source fields')
    for value in values.values():
        _encoded_native_sqlite_value(value)


def _complete(db, kind, supplied):
    fields = CANDIDATE_FIELDS[kind][1:]
    expressions = ['?' if field in supplied else CANDIDATE_DEFAULTS[kind].get(field, 'null')
                   for field in fields]
    row = db.execute('select ' + ','.join(expressions),
                     tuple(supplied[field] for field in fields if field in supplied)).fetchone()
    return dict(zip(fields, row, strict=True))


def _stored(kind, values):
    payload = json.dumps({field: _encoded_native_sqlite_value(values[field])
                          for field in candidate_payload_fields(kind)},
                         ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    return {'payload_json': payload,
            'title': values.get('title', ''), 'status': values.get('status', ''),
            'created_at': values.get('created_at', ''), 'updated_at': values.get('updated_at', '')}


def insert_business_candidate(db, kind, values, *, ignore=False):
    _validate(kind, values)
    _lock(db)
    physical_water = db.execute("select seq from sqlite_sequence where name='business'").fetchone()
    if physical_water and physical_water[0] == 2**63 - 1:
        db.rollback()
        _raise_autoincrement_full()
    public_id = (_next_id(db, kind) if _source_bound_null(values.get('id'))
                 else _native_integer_primary_key(values['id']))
    data = {'record_type': kind, 'legacy_key': str(public_id),
            **_stored(kind, _complete(db, kind, values))}
    cursor = db.execute('insert ' + ('or ignore ' if ignore else '') + 'into business('
                        + ','.join(data) + ') values(' + ','.join('?' for _ in data)
                        + ') returning public_id', tuple(data.values()))
    row = cursor.fetchone()
    cursor.fetchall()
    return BusinessWriteResult(row[0] if row else None, cursor.rowcount)


def update_business_candidate(db, kind, public_id, values, *, ignore=False,
                              expected=None, expected_in=None, stale_seconds=None):
    _validate(kind, values)
    _validate(kind, expected or {})
    _validate(kind, {field: None for field in (expected_in or {})})
    _lock(db)
    cursor = db.execute('select * from (' + candidate_projection(kind) + ') where id=?', (public_id,))
    row = cursor.fetchone()
    if row is None:
        return 0
    current = dict(zip((item[0] for item in cursor.description), row, strict=True))
    physical_id = db.execute('select id from business where record_type=? and public_id=?',
                             (kind, public_id)).fetchone()[0]
    current.update(values)
    new_id = _native_integer_primary_key(current['id'], update=True)
    data = {'legacy_key': str(new_id), **_stored(kind, current)}
    guards = []
    guard_values = []
    for field, value in (expected or {}).items():
        guards.append(candidate_column(kind, field) + '=?')
        guard_values.append(value)
    for field, values_in in (expected_in or {}).items():
        if not values_in:
            raise ValueError('empty Business candidate expected values')
        guards.append(candidate_column(kind, field) + ' in (' +
                      ','.join('?' for _ in values_in) + ')')
        guard_values.extend(values_in)
    if stale_seconds is not None:
        if kind != 'wechat_memory_candidate' or type(stale_seconds) is not int:
            raise ValueError('unsupported Business candidate stale guard')
        guards.append("datetime(updated_at) <= datetime('now', ?)")
        guard_values.append(f'-{stale_seconds} seconds')
    result = db.execute('update ' + ('or ignore ' if ignore else '') + 'business set '
                        + ','.join(field + '=?' for field in data) + ' where id=?'
                        + ''.join(' and ' + guard for guard in guards),
                        (*data.values(), physical_id, *guard_values))
    return result.rowcount


def delete_business_candidate(db, kind, public_id):
    _validate(kind, {})
    _lock(db)
    return db.execute('delete from business where record_type=? and public_id=?',
                      (kind, public_id)).rowcount


CHILD_FKS = {
    'business_attention_events': {'attention_item_id': 'attention_item'},
    'business_attention_tasks': {'attention_item_id': 'attention_item'},
    'business_attention_proposal_tasks': {'attention_item_id': 'attention_item'},
    'business_legacy_links': {'attention_item_id': 'attention_item',
                              'project_candidate_id': 'project_candidate'},
}
CHILD_ROWIDS = {'business_attention_tasks', 'business_attention_proposal_tasks'}
SOURCE_NAMES = (*CANDIDATE_SOURCE_TABLES.values(), *CHILD_FKS)


def _declarations(db):
    return {name: _source_declaration(db, name) for name in SOURCE_NAMES}


def _candidate_fk_findings(db):
    findings = [list(row) for row in _foreign_keys(db)]
    moved = set(SOURCE_NAMES) | {'business'}
    if any(table in moved or parent in CANDIDATE_SOURCE_TABLES.values()
           for table, _rowid, parent, _id in findings):
        raise ValueError('broken Business candidate source FK')
    return findings


def _variant(declarations):
    for variant, profile in SOURCE_PROFILES.items():
        if all(_fingerprint(declarations[name]) == profile[name]['hash']
               for name in SOURCE_NAMES):
            return variant
    raise ValueError('unsupported Business candidate source declarations')


def _validate_metadata(metadata):
    variant = metadata.get('variant')
    if variant not in SOURCE_PROFILES or set(metadata.get('declarations', {})) != set(SOURCE_NAMES):
        raise ValueError('unsupported Business candidate metadata')
    if any(_fingerprint(metadata['declarations'][name]) != SOURCE_PROFILES[variant][name]['hash']
           for name in SOURCE_NAMES):
        raise ValueError('unsupported Business candidate metadata declarations')
    if metadata.get('external_trigger') != LINK_TRIGGER_SQL[2].replace(
            'create trigger', 'CREATE TRIGGER', 1):
        raise ValueError('unsupported Business candidate metadata guard')


def _external_trigger(db):
    row = db.execute("select sql from main.sqlite_schema where type='trigger' and "
                     "name='trg_sync_legacy_update_public_id' and tbl_name='actions'").fetchone()
    if row is None or row[0] != LINK_TRIGGER_SQL[2].replace('create trigger', 'CREATE TRIGGER', 1):
        raise ValueError('unsupported Business candidate Actions guard')
    return row[0]


def _collision(db, names):
    names = tuple(names)
    params = ','.join('?' for _ in names)
    if db.execute('select 1 from temp.sqlite_schema where name collate nocase in ('
                  + params + ') limit 1', names).fetchone():
        raise ValueError('Business candidate TEMP collision')


def _created_name(sql):
    words = sql.split()
    return words[3 if words[1].lower() == 'unique' else 2]


def capture_business_candidate_source(db):
    _collision(db, ('business', *SOURCE_NAMES))
    if not business_schema_parent_is_current(db):
        raise ValueError('unsupported base Business declaration')
    declarations = _declarations(db)
    variant = _variant(declarations)
    external = _external_trigger(db)
    findings = _candidate_fk_findings(db)
    _target_table, target_indexes, target_triggers = _definitions(include_candidates=True)
    new_names = {sqlite_schema_name(_created_name(sql))
                 for sql in (*target_indexes, *target_triggers)}
    _collision(db, new_names)
    existing = {sqlite_schema_name(row[0]) for row in db.execute(
        'select name from main.sqlite_schema')}
    old_business = {sqlite_schema_name(row[0]) for row in db.execute(
        "select name from main.sqlite_schema where tbl_name='business'")}
    if new_names & (existing - old_business):
        raise ValueError('Business candidate target name collision')
    _external_dependencies(
        db, targets=('business', *SOURCE_NAMES),
        allowed_trigger_names=('trg_sync_legacy_update_public_id',),
    )
    source_count = sum(db.execute('select count(*) from main.' + _quote(name)).fetchone()[0]
                       for name in CANDIDATE_SOURCE_TABLES.values())
    source_max_physical = db.execute('select max(id) from main.business').fetchone()[0] or 0
    source_physical_water = _sequence(db, 'business')
    return {
        'variant': variant,
        'declarations': declarations,
        'external_trigger': external,
        'source_waters': {name: _sequence(db, name) for name in SOURCE_NAMES},
        'business_waters': {kind: _sequence(db, 'business:' + kind) for kind in BUSINESS_FIELDS},
        'business_physical_water': source_physical_water,
        'installed_business_physical_water': (
            max(source_physical_water or 0, source_max_physical + source_count)
            if source_physical_water is not None or source_count else None),
        'fk_findings': findings,
    }


def _business_raw(db):
    return _snapshot(db, 'business')


def _translated_child_sql(name, sql, *, inverse=False):
    for field, kind in CHILD_FKS[name].items():
        source = f'foreign key({field}) references {CANDIDATE_SOURCE_TABLES[kind]}(id)'
        target = f'foreign key({field}) references business({kind}_public_id)'
        old, new = (target, source) if inverse else (source, target)
        if sql.count(old) != 1:
            raise ValueError('unsupported Business candidate child FK')
        sql = sql.replace(old, new, 1)
    return sql


def _create_auxiliary(db, declaration):
    for index in reversed(declaration['indexes']):
        if index['sql'] is not None:
            db.execute(index['sql'])
    for trigger in declaration['triggers']:
        db.execute(trigger['sql'])


def _create_children(db, metadata, *, target):
    for name in CHILD_FKS:
        sql = metadata['declarations'][name]['sql']
        db.execute(_translated_child_sql(name, sql) if target else sql)
    for name in CHILD_FKS:
        _create_auxiliary(db, metadata['declarations'][name])


def _drop_children(db):
    for name in CHILD_FKS:
        db.execute('drop table main.' + _quote(name))


def install_fresh_business_candidate_children(db):
    """Finish the empty fresh bootstrap after its existing follow-up conversion."""
    _assert_transaction(db)
    if any(db.execute('select 1 from main.' + _quote(name) + ' limit 1').fetchone()
           for name in CHILD_FKS):
        raise ValueError('fresh Business candidate child is not empty')
    _drop_children(db)
    for name in CHILD_FKS:
        db.execute(_translated_child_sql(name, SOURCE_PROFILES['fresh'][name]['sql']))
    for name in CHILD_FKS:
        for sql in SOURCE_PROFILES['fresh'][name]['indexes']:
            db.execute(sql)
        for sql in SOURCE_PROFILES['fresh'][name]['triggers']:
            db.execute(sql)


def _drop_external(db):
    db.execute('drop trigger main.trg_sync_legacy_update_public_id')


def _restore_external(db, sql):
    db.execute(sql)


def _insert_candidate_fact(db, kind, values, physical_id):
    data = {'id': physical_id, 'record_type': kind, 'legacy_key': str(values['id']),
            **_stored(kind, values)}
    db.execute('insert into main.business(' + ','.join(data) + ') values('
               + ','.join('?' for _ in data) + ')', tuple(data.values()))


def _candidate_facts(db):
    return {kind: _snapshot(db, table) for kind, table in CANDIDATE_SOURCE_TABLES.items()}


def _projected_facts(db, kind, variant):
    fields = (CANDIDATE_FIELDS if variant == 'actual' else CANDIDATE_FRESH_FIELDS)[kind]
    selected = ','.join(field for field in fields)
    return tuple(tuple(row) for row in db.execute(
        'select ' + selected + ' from (' + candidate_projection(kind, variant)
        + ') order by id'))


def _projected_snapshot(db, kind, variant):
    fields = (CANDIDATE_FIELDS if variant == 'actual' else CANDIDATE_FRESH_FIELDS)[kind]
    projected = candidate_projection(kind, variant)
    values = _projected_facts(db, kind, variant)
    classes = tuple(tuple(row) for row in db.execute(
        'select ' + ','.join('typeof(' + field + ')' for field in fields)
        + ' from (' + projected + ') order by id'))
    return {'columns': fields, 'rows': values, 'types': classes, 'hidden_rowid': False}


def migrate_business_candidate_schema(db, metadata):
    _assert_transaction(db)
    _validate_metadata(metadata)
    current = capture_business_candidate_source(db)
    if current != metadata:
        raise ValueError('Business candidate source metadata changed')
    candidates = _candidate_facts(db)
    children = {name: _snapshot(db, name, hidden_rowid=name in CHILD_ROWIDS)
                for name in CHILD_FKS}
    business = _business_raw(db)
    max_physical = max((row[0] for row in business['rows']), default=0)
    _drop_external(db)
    _drop_children(db)
    for table in CANDIDATE_SOURCE_TABLES.values():
        db.execute('drop table main.' + _quote(table))
    db.execute('drop table main.business')
    table, indexes, triggers = _definitions(include_candidates=True)
    for sql in (table, *indexes, *triggers):
        db.execute(sql)
    _insert_rows(db, 'business', business)
    for kind, snap in candidates.items():
        for row in snap['rows']:
            max_physical += 1
            _insert_candidate_fact(db, kind, dict(zip(snap['columns'], row, strict=True)),
                                   max_physical)
    _create_children(db, metadata, target=True)
    for name, snap in children.items():
        _restore_rows(db, name, snap)
    _restore_external(db, metadata['external_trigger'])
    _set_sequence(db, 'business', metadata['installed_business_physical_water'])
    for kind, water in metadata['business_waters'].items():
        _set_sequence(db, 'business:' + kind, water)
    for kind, table_name in CANDIDATE_SOURCE_TABLES.items():
        _set_sequence(db, 'business:' + kind, metadata['source_waters'][table_name])
    for name in CHILD_FKS:
        _set_sequence(db, name, metadata['source_waters'][name])
    for kind, snap in candidates.items():
        fields = (CANDIDATE_FIELDS if metadata['variant'] == 'actual' else
                  CANDIDATE_FRESH_FIELDS)[kind]
        indexes = tuple(snap['columns'].index(field) for field in fields)
        expected = {**snap,
                    'columns': fields,
                    'rows': tuple(tuple(row[index] for index in indexes) for row in snap['rows']),
                    'types': tuple(tuple(row[index] for index in indexes) for row in snap['types'])}
        if _projected_snapshot(db, kind, metadata['variant']) != expected:
            raise ValueError('Business candidate projection differs from source')
    for name, snap in children.items():
        if _snapshot(db, name, hidden_rowid=name in CHILD_ROWIDS) != snap:
            raise ValueError('Business candidate child differs from source')
    if [list(row) for row in _foreign_keys(db)] != metadata['fk_findings']:
        raise ValueError('Business candidate FK findings changed')


def restore_business_candidate_schema(db, metadata):
    _assert_transaction(db)
    _validate_metadata(metadata)
    _collision(db, ('business', *SOURCE_NAMES))
    variant = metadata['variant']
    if variant not in SOURCE_PROFILES:
        raise ValueError('unsupported Business candidate inverse variant')
    source_names = {sqlite_schema_name(name) for name in CANDIDATE_SOURCE_TABLES.values()}
    for table_name in CANDIDATE_SOURCE_TABLES.values():
        profile = SOURCE_PROFILES[variant][table_name]
        source_names.update(sqlite_schema_name(_created_name(sql)) for sql in
                            (*profile['indexes'], *profile['triggers']))
    _collision(db, source_names)
    names = {sqlite_schema_name(row[0]) for row in db.execute(
        'select name from main.sqlite_schema')}
    if source_names & names:
        raise ValueError('Business candidate source name collision on inverse')
    table, indexes, triggers = _definitions(include_candidates=True)
    actual = _source_declaration(db, 'business')
    if (actual['sql'] != _canonical_create(table) or
            {item['sql'] for item in actual['indexes'] if item['sql']} !=
            {_canonical_create(sql) for sql in indexes} or
            {item['sql'] for item in actual['triggers']} !=
            {_canonical_create(sql) for sql in triggers}):
        raise ValueError('unsupported Business candidate target declaration')
    for name in CHILD_FKS:
        child = _source_declaration(db, name)
        source = metadata['declarations'][name]
        if (child['sql'] != _translated_child_sql(name, source['sql']) or
                {item['sql'] for item in child['indexes'] if item['sql']} !=
                {item['sql'] for item in source['indexes'] if item['sql']} or
                {item['sql'] for item in child['triggers']} !=
                {item['sql'] for item in source['triggers']}):
            raise ValueError('unsupported Business candidate child target')
    if (_external_trigger(db) != metadata['external_trigger'] or
            [list(row) for row in _foreign_keys(db)] != metadata['fk_findings']):
        raise ValueError('broken Business candidate target graph')
    _external_dependencies(
        db, targets=('business', *CHILD_FKS),
        allowed_trigger_names=('trg_sync_legacy_update_public_id',),
    )
    business = _business_raw(db)
    columns = business['columns']
    type_position = columns.index('record_type')
    old = {**business, 'rows': tuple(row for row in business['rows']
                                     if row[type_position] in BUSINESS_FIELDS),
           'types': tuple(types for row, types in zip(business['rows'], business['types'], strict=True)
                          if row[type_position] in BUSINESS_FIELDS)}
    candidate_snapshots = {kind: _projected_snapshot(db, kind, variant)
                           for kind in CANDIDATE_KINDS}
    candidate_waters = {kind: _sequence(db, 'business:' + kind)
                        for kind in CANDIDATE_KINDS}
    business_waters = {kind: _sequence(db, 'business:' + kind)
                       for kind in BUSINESS_FIELDS}
    physical_water = _sequence(db, 'business')
    children = {name: _snapshot(db, name, hidden_rowid=name in CHILD_ROWIDS)
                for name in CHILD_FKS}
    child_waters = {name: _sequence(db, name) for name in CHILD_FKS}
    _drop_external(db)
    _drop_children(db)
    db.execute('drop table main.business')
    base_table, base_indexes, base_triggers = _definitions()
    for sql in (base_table, *base_indexes, *base_triggers):
        db.execute(sql)
    _insert_rows(db, 'business', old)
    for kind, table_name in CANDIDATE_SOURCE_TABLES.items():
        profile = SOURCE_PROFILES[variant][table_name]
        db.execute(profile['sql'])
        fields = (CANDIDATE_FIELDS if variant == 'actual' else CANDIDATE_FRESH_FIELDS)[kind]
        sql = ('insert into main.' + _quote(table_name) + '('
               + ','.join(_quote(field) for field in fields) + ') values('
               + ','.join('?' for _ in fields) + ')')
        db.executemany(sql, candidate_snapshots[kind]['rows'])
        for auxiliary in (*profile['indexes'], *profile['triggers']):
            db.execute(auxiliary)
    _create_children(db, metadata, target=False)
    for name, snap in children.items():
        _restore_rows(db, name, snap)
    _restore_external(db, metadata['external_trigger'])
    if physical_water == metadata['installed_business_physical_water']:
        physical_water = metadata['business_physical_water']
    _set_sequence(db, 'business', physical_water)
    for kind, water in business_waters.items():
        _set_sequence(db, 'business:' + kind, water)
    for kind, table_name in CANDIDATE_SOURCE_TABLES.items():
        _set_sequence(db, table_name, candidate_waters[kind])
        _set_sequence(db, 'business:' + kind, None)
    for name, water in child_waters.items():
        _set_sequence(db, name, water)
    if _snapshot(db, 'business') != old:
        raise ValueError('Business old-kind facts differ after inverse')
    for kind, table_name in CANDIDATE_SOURCE_TABLES.items():
        if _snapshot(db, table_name) != candidate_snapshots[kind]:
            raise ValueError('Business candidate current facts differ after inverse')
    if any(_snapshot(db, name, hidden_rowid=name in CHILD_ROWIDS) != snap
           for name, snap in children.items()):
        raise ValueError('Business candidate child facts differ after inverse')
    if not business_schema_parent_is_current(db) or any(
            _fingerprint(_source_declaration(db, name)) != SOURCE_PROFILES[variant][name]['hash']
            for name in SOURCE_NAMES):
        raise ValueError('Business candidate source declaration differs after inverse')
    if [list(row) for row in _foreign_keys(db)] != metadata['fk_findings']:
        raise ValueError('Business candidate inverse FK findings changed')
    if db.execute('pragma quick_check').fetchone()[0] != 'ok':
        raise ValueError('Business candidate inverse quick_check failed')
