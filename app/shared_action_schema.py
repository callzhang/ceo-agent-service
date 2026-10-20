"""Source-backed shared lifecycle conversion and inverse from current owners."""
from functools import lru_cache
import json

from app import action_storage as actions
from app.action_email_definitions import email_action_projection
from app.model_action_schema import _dependencies
from app.model_result_schema import _inbound_dependencies
from app.sent_result_schema import sent_action_objects
from app.shared_action_definitions import SHARED_RECORD_TYPES, SOURCE_TO_TARGET, DIRECT_DERIVED_FIELDS, DIRECT_TERMINAL_FIELDS, email_projection
from app.storage_payload import EXECUTION_ACTION_RECORD_TYPES

_STAGING = '_shared_action_rows'
_WATER = '_shared_action_water'


@lru_cache(maxsize=4)
def shared_action_objects(reply_variant):
    from app.email_canonical_storage import ACTION_OPTIONS
    return actions._current_objects(reply_variant=reply_variant, inline_legacy_owner=True,
        physical_provenance=True, compact_payload=True, canonical_email=True,
        selection_references=True, result_references=True, execution_owner=True,
        sent_result_references=True, shared_lifecycle=True, **ACTION_OPTIONS)


def _require_source(db, *, shared):
    from app.business_candidate_storage import _collision
    if not db.in_transaction or db.execute('pragma foreign_keys').fetchone()[0]:
        raise ValueError('shared lifecycle transition requires caller-owned FK-off transaction')
    _collision(db, ('actions', _STAGING, _WATER))
    objects = actions._objects(db, 'actions')
    declaration = shared_action_objects if shared else sent_action_objects
    variant = next((variant for variant in actions.REPLY_VARIANTS if objects == declaration(variant)), None)
    if variant is None:
        raise ValueError('unsupported shared lifecycle source declaration')
    _dependencies(db)
    _inbound_dependencies(db)
    if db.execute('pragma foreign_key_check(actions)').fetchone() or db.execute('pragma quick_check').fetchone()[0] != 'ok':
        raise ValueError('shared lifecycle source integrity or foreign keys are invalid')
    return variant


def _rows(db, projection):
    cursor = db.execute(projection)
    return [dict(zip((column[0] for column in cursor.description), row, strict=True)) for row in cursor]


def _equal(left, right):
    return type(left) is type(right) and left == right


def validate_direct_ledger(db, *, shared):
    """Prove every removable value before the first source write; never repair."""
    projection = email_projection if shared else email_action_projection
    attempts = {}
    for row in _rows(db, email_action_projection('email_action_attempts') + ' order by action_id,attempt_number'):
        attempts.setdefault(row['action_id'], []).append(row)
    steps = _rows(db, projection('email_actions', include_rowid=True))
    action_ids = {row['action_id'] for row in steps}
    if set(attempts) - action_ids:
        raise ValueError('direct attempt lacks its source step')
    execution = ('started_at', *DIRECT_TERMINAL_FIELDS)
    for row in steps:
        history = attempts.get(row['action_id'], [])
        numbers = [item['attempt_number'] for item in history]
        if (any(type(number) is not int for number in numbers)
                or numbers != list(range(1, len(history) + 1))
                or not _equal(row['attempt_count'], len(history))):
            raise ValueError(f"direct source attempt ordering/count mismatch: {row['action_id']}")
        if row['status'] == 'pending':
            valid = not history and all(_equal(row[field], '') for field in execution)
        elif row['status'] == 'processing':
            valid = bool(row['started_at']) and all(_equal(row[field], '') for field in DIRECT_TERMINAL_FIELDS)
        else:
            valid = bool(history) and _equal(row['status'], history[-1]['status']) and all(
                _equal(row[field], history[-1][field]) for field in execution)
        if not valid:
            raise ValueError(f"direct source latest-attempt mirror mismatch: {row['action_id']}")
    return steps


def _rebuild(db, variant, steps, *, shared):
    from app.action_email_storage import _columns, _quote
    columns = _columns(db, 'actions')
    names = ','.join(map(_quote, columns))
    foreign_keys = sorted(tuple(row) for row in db.execute('pragma foreign_key_check'))
    db.execute(f'create temp table {_STAGING} as select {names} from main.actions')
    # CTAS loses the source primary key; each step transform must seek its physical ID.
    db.execute(f'create index temp.{_STAGING}_id on {_STAGING}(id)')
    db.execute(f'create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence')
    before_layout = actions.action_payloads(variant, selection_references=True,
        execution_owner=True, shared_lifecycle=not shared).layouts['email_actions']
    after_layout = actions.action_payloads(variant, selection_references=True,
        execution_owner=True, shared_lifecycle=shared).layouts['email_actions']
    direct = {row['rowid']: row for row in steps}
    source_codes = SHARED_RECORD_TYPES if not shared else EXECUTION_ACTION_RECORD_TYPES
    target_codes = SHARED_RECORD_TYPES if shared else EXECUTION_ACTION_RECORD_TYPES
    for physical, public, payload in db.execute(
            f'select id,cast(legacy_key as integer),json(payload_json) from temp.{_STAGING} where _kind=?',
            (source_codes.code('email_actions'),)).fetchall():
        encoded = before_layout.unpack(payload)
        if shared:
            value = {name: (encoded[name] if name != 'started_at' or direct[public]['status'] == 'processing' else '')
                     for name in after_layout.fields}
        else:
            value = {name: (actions._encoded_native_sqlite_value(direct[public][name])
                           if name in DIRECT_DERIVED_FIELDS or name == 'started_at' else encoded[name])
                     for name in after_layout.fields}
        packed = json.dumps([value[name] for name in after_layout.fields], ensure_ascii=False,
                            allow_nan=False, separators=(',', ':'))
        db.execute(f'update temp.{_STAGING} set payload_json=jsonb(?) where id=?', (packed, physical))
    # All other typed variants retain their exact original JSONB bytes.
    for source in SOURCE_TO_TARGET:
        db.execute(f'update temp.{_STAGING} set _kind=? where _kind=?',
                   (target_codes.code(source), source_codes.code(source)))
    db.execute('drop table main.actions')
    objects = shared_action_objects(variant) if shared else sent_action_objects(variant)
    db.execute(next(obj['sql'] for obj in objects if obj['type'] == 'table'))
    db.execute(f'insert into main.actions({names}) select {names} from temp.{_STAGING} order by id')
    for category in ('index', 'trigger'):
        for obj in objects:
            if obj['type'] == category:
                db.execute(obj['sql'])
    db.execute('delete from main.sqlite_sequence')
    db.execute(f'insert into main.sqlite_sequence(rowid,name,seq) select rowid,name,seq from temp.{_WATER}')
    values = names + ',' + ','.join('typeof(' + _quote(name) + ')' for name in columns)
    for left, right in (('main.actions', 'temp.' + _STAGING), ('temp.' + _STAGING, 'main.actions')):
        if db.execute(f'select {values} from {left} except select {values} from {right} limit 1').fetchone():
            raise ValueError('shared lifecycle transition changed stored values or types')
    restored = validate_direct_ledger(db, shared=shared)
    original = {row['rowid']: row for row in steps}
    if len(restored) != len(steps) or any(
        row['rowid'] not in original or any(not _equal(value, original[row['rowid']][field]) for field, value in row.items())
        for row in restored):
        raise ValueError('shared lifecycle transition changed direct public facts/types')
    if (actions._objects(db, 'actions') != objects
            or sorted(tuple(row) for row in db.execute('pragma foreign_key_check')) != foreign_keys):
        raise ValueError('shared lifecycle transition changed declaration or FK findings')
    db.execute(f'drop table temp.{_STAGING}')
    db.execute(f'drop table temp.{_WATER}')


def migrate_shared_actions(db):
    variant = _require_source(db, shared=False)
    steps = validate_direct_ledger(db, shared=False)
    _rebuild(db, variant, steps, shared=True)
    return {'reply_variant': variant}


def restore_shared_actions(db, metadata):
    variant = _require_source(db, shared=True)
    if metadata != {'reply_variant': variant}:
        raise ValueError('unsupported shared lifecycle inverse metadata')
    steps = validate_direct_ledger(db, shared=True)
    _rebuild(db, variant, steps, shared=False)
