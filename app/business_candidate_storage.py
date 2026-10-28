"""Source-shaped Business candidate writes."""

import json

from app.action_storage import (
    _encoded_native_sqlite_value, _native_integer_primary_key, _source_bound_null,
    _raise_autoincrement_full,
)
from app.business_candidate_definitions import (
    CANDIDATE_DEFAULTS, CANDIDATE_FIELDS, CANDIDATE_KINDS, candidate_column,
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
