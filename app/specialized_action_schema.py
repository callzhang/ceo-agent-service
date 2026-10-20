"""Exact specialized step conversion; inverse reads current claims and proofs."""
from functools import lru_cache

from app import action_storage as actions
from app.action_email_storage import _columns, _quote
from app.model_action_schema import _dependencies
from app.model_result_schema import _inbound_dependencies
from app.shared_action_definitions import SHARED_RECORD_TYPES, SPECIALIZED_RECORD_TYPES, SPECIALIZED_SOURCE_TO_TARGET
from app.shared_action_schema import shared_action_objects

_STAGING = '_specialized_action_rows'
_WATER = '_specialized_action_water'


@lru_cache(maxsize=4)
def specialized_action_objects(reply_variant):
    from app.email_canonical_storage import ACTION_OPTIONS
    return actions._current_objects(reply_variant=reply_variant, inline_legacy_owner=True,
        physical_provenance=True, compact_payload=True, canonical_email=True,
        selection_references=True, result_references=True, execution_owner=True,
        sent_result_references=True, shared_lifecycle=True, specialized_claims=True, **ACTION_OPTIONS)


def _require_source(db, *, specialized):
    from app.business_candidate_storage import _collision
    if not db.in_transaction or db.execute('pragma foreign_keys').fetchone()[0]:
        raise ValueError('specialized lifecycle transition requires caller-owned FK-off transaction')
    _collision(db, ('actions', _STAGING, _WATER))
    objects = actions._objects(db, 'actions')
    declaration = specialized_action_objects if specialized else shared_action_objects
    variant = next((variant for variant in actions.REPLY_VARIANTS if objects == declaration(variant)), None)
    if variant is None:
        raise ValueError('unsupported specialized lifecycle source declaration')
    _dependencies(db)
    _inbound_dependencies(db)
    if db.execute('pragma quick_check').fetchone()[0] != 'ok':
        raise ValueError('specialized lifecycle source integrity is invalid')
    return variant


def _rebuild(db, variant, *, specialized):
    columns = _columns(db, 'actions')
    names = ','.join(map(_quote, columns))
    findings = sorted(tuple(row) for row in db.execute('pragma foreign_key_check'))
    db.execute(f'create temp table {_STAGING} as select {names} from main.actions')
    db.execute(f'create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence')
    source_types = SHARED_RECORD_TYPES if specialized else SPECIALIZED_RECORD_TYPES
    target_types = SPECIALIZED_RECORD_TYPES if specialized else SHARED_RECORD_TYPES
    # One bounded pass over the staged rows. Every payload byte and physical/
    # public identity is retained; only the two exact source codes change.
    pairs = [(source_types.code(kind), target_types.code(kind)) for kind in SPECIALIZED_SOURCE_TO_TARGET]
    db.execute(f'update temp.{_STAGING} set _kind=case _kind '
        + ' '.join('when ? then ?' for _ in pairs) + ' else _kind end where _kind in ('
        + ','.join('?' for _ in pairs) + ')',
        (*[value for pair in pairs for value in pair], *[pair[0] for pair in pairs]))
    objects = specialized_action_objects(variant) if specialized else shared_action_objects(variant)
    db.execute('drop table main.actions')
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
            raise ValueError('specialized lifecycle transition changed stored values or types')
    if (actions._objects(db, 'actions') != objects
            or sorted(tuple(row) for row in db.execute('pragma foreign_key_check')) != findings):
        raise ValueError('specialized lifecycle transition changed declaration or FK findings')
    db.execute(f'drop table temp.{_STAGING}')
    db.execute(f'drop table temp.{_WATER}')


def migrate_specialized_actions(db):
    variant = _require_source(db, specialized=False)
    _rebuild(db, variant, specialized=True)
    return {'reply_variant': variant}


def restore_specialized_actions(db, metadata):
    variant = _require_source(db, specialized=True)
    if metadata != {'reply_variant': variant}:
        raise ValueError('unsupported specialized lifecycle inverse metadata')
    _rebuild(db, variant, specialized=False)
