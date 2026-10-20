"""Exact execution-owner conversion and inverse from current plans and aliases.

Only declaration metadata leaves the transaction. Temporary staging is removed
before commit; no earlier business row is used to reconstruct current facts.
"""

from functools import lru_cache
import json

from app import action_storage as actions
from app.execution_owner_definitions import ALIAS, EXECUTION_FIELDS, EXECUTION_REFERENCES
from app.model_action_schema import _dependencies
from app.model_result_schema import _inbound_dependencies


_STAGING = '_execution_owner_actions'
_WATER = '_execution_owner_water'
_PLAN = 'review_candidates'
_SOURCE = 'candidate_executions'
_FIELDS = ('status', 'lease_owner', 'lease_expires_at', 'result_json', 'created_at', 'updated_at')


@lru_cache(maxsize=4)
def execution_action_objects(reply_variant, *, execution_owner=True):
    from app.email_canonical_storage import ACTION_OPTIONS
    return actions._current_objects(reply_variant=reply_variant, inline_legacy_owner=True,
        physical_provenance=True, compact_payload=True, canonical_email=True,
        selection_references=True, result_references=True, execution_owner=execution_owner,
        **ACTION_OPTIONS)


def _require_source(db, *, execution_owner):
    from app.business_candidate_storage import _collision
    if not db.in_transaction or db.execute('pragma foreign_keys').fetchone()[0]:
        raise ValueError('execution-owner transition requires caller-owned FK-off transaction')
    _collision(db, ('actions', _STAGING, _WATER))
    objects = actions._objects(db, 'actions')
    variant = next((variant for variant in actions.REPLY_VARIANTS
                    if objects == execution_action_objects(variant, execution_owner=execution_owner)), None)
    if variant is None:
        raise ValueError('unsupported execution-owner Actions source declaration')
    _dependencies(db)
    _inbound_dependencies(db)
    if db.execute('pragma foreign_key_check(actions)').fetchone() or db.execute('pragma quick_check').fetchone()[0] != 'ok':
        raise ValueError('execution-owner source integrity or foreign keys are invalid')
    return variant


def _execution_facts(db, *, execution_owner):
    """Validate all physical bindings, including independently repeated reviews."""
    from app.action_email_storage import _columns, _quote
    kind = ALIAS if execution_owner else _SOURCE
    allowed = {'id', '_kind', 'legacy_key', 'candidate_id', 'payload_json'}
    if not execution_owner:
        allowed.update(('review_id', 'selection_id'))
    unused = [column for column in _columns(db, 'actions') if column not in allowed]
    if db.execute('select 1 from actions where record_type=? and ('
                  + ' or '.join(_quote(column) + ' is not null' for column in unused)
                  + ') limit 1', (kind,)).fetchone():
        raise ValueError('execution has an unused physical reference')
    if execution_owner:
        expressions = ['a.id', 'a.legacy_key', 'a.candidate_id',
                       'p.execution_review_id', 'p.execution_selection_id']
        expressions += ['p.execution_' + field for field in _FIELDS]
        review, selection = 'p.execution_review_id', 'p.execution_selection_id'
    else:
        expressions = ['a.id', 'a.legacy_key', 'a.candidate_id', 'a.review_id', 'a.selection_id']
        expressions += ['a.' + field for field in _FIELDS]
        review, selection = 'a.review_id', 'a.selection_id'
    rows = db.execute('select ' + ','.join(expressions) + ',p.id,r.candidate_id,s.candidate_id,s.review_id'
        + (',p.execution_public_id' if execution_owner else '') +
        " from actions a left join actions p on p.id=a.candidate_id and p.record_type='review_candidates' "
        f"left join actions r on r.id={review} and r.record_type='candidate_reviews' "
        f"left join actions s on s.id={selection} and s.record_type='candidate_selections' "
        'where a.record_type=? order by a.id', (kind,)).fetchall()
    facts, candidates = [], set()
    for row in rows:
        physical, public, candidate, review_id, selection_id = row[:5]
        if (candidate in candidates or row[11] != candidate or row[12] != candidate
                or selection_id is not None and (row[13] != candidate or row[14] != review_id)
                or execution_owner and row[15] != int(public)):
            raise ValueError('execution alias/plan review or selection binding mismatch')
        candidates.add(candidate)
        facts.append(tuple(row[:11]))
    if execution_owner and db.execute("select count(*) from actions where record_type='review_candidates' and execution_public_id is not null").fetchone()[0] != len(facts):
        raise ValueError('plan execution has no unique matching alias')
    return facts


def _rebuild(db, variant, facts, *, execution_owner):
    from app.action_email_storage import _columns, _quote
    source_columns = _columns(db, 'actions')
    names = ','.join(map(_quote, source_columns))
    foreign_keys = sorted(tuple(row) for row in db.execute('pragma foreign_key_check'))
    db.execute(f'create temp table {_STAGING} as select {names} from main.actions')
    db.execute(f'create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence')
    plan_code = actions.ACTION_RECORD_TYPES.code(_PLAN)
    source_code, alias_code = (actions.ACTION_RECORD_TYPES.code(kind) for kind in (_SOURCE, ALIAS))
    layout = actions.action_payloads(variant, execution_owner=True).layouts[_PLAN]
    if execution_owner:
        for field in EXECUTION_REFERENCES:
            db.execute(f'alter table temp.{_STAGING} add column {field} integer')
        db.execute(f'update temp.{_STAGING} set payload_json=jsonb_insert(payload_json,'
                   + ','.join("'$[#]',null" for _ in EXECUTION_FIELDS) + ') where _kind=?', (plan_code,))
        for physical, public, candidate, review, selection, *values in facts:
            parameters = []
            for field, value in zip(EXECUTION_FIELDS, (int(public), *values), strict=True):
                parameters.extend((layout.path(field), json.dumps(value, ensure_ascii=False)))
            db.execute(f'update temp.{_STAGING} set execution_review_id=?,execution_selection_id=?,payload_json=jsonb_set(payload_json,'
                + ','.join('?,json(?)' for _ in EXECUTION_FIELDS) + ') where id=?',
                (review, selection, *parameters, candidate))
            db.execute(f"update temp.{_STAGING} set _kind=?,review_id=null,selection_id=null,payload_json=jsonb('[]') where id=?",
                       (alias_code, physical))
    else:
        db.execute(f'alter table temp.{_STAGING} add column selection_id integer')
        source_layout = actions.action_payloads(variant).layouts[_SOURCE]
        for physical, public, candidate, review, selection, *values in facts:
            packed = source_layout.pack(dict(zip(_FIELDS, values, strict=True)),
                                        scalar_codec=actions._encoded_native_sqlite_value)
            db.execute(f'update temp.{_STAGING} set _kind=?,review_id=?,selection_id=?,payload_json=jsonb(?) where id=?',
                       (source_code, review, selection, packed, physical))
        # Descending paths avoid shifting subsequent positions while removing.
        db.execute(f'update temp.{_STAGING} set execution_review_id=null,execution_selection_id=null,payload_json=jsonb_remove(payload_json,'
            + ','.join(repr(layout.path(field)) for field in reversed(EXECUTION_FIELDS)) + ') where _kind=?', (plan_code,))
    objects = execution_action_objects(variant, execution_owner=execution_owner)
    db.execute('drop table main.actions')
    db.execute(next(obj['sql'] for obj in objects if obj['type'] == 'table'))
    columns = _columns(db, 'actions')
    names = ','.join(map(_quote, columns))
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
            raise ValueError('execution-owner transition changed stored values or types')
    if (actions._objects(db, 'actions') != objects
            or sorted(tuple(row) for row in db.execute('pragma foreign_key_check')) != foreign_keys
            or _execution_facts(db, execution_owner=execution_owner) != facts):
        raise ValueError('execution-owner transition changed declaration, bindings or facts')
    db.execute(f'drop table temp.{_STAGING}')
    db.execute(f'drop table temp.{_WATER}')


def migrate_execution_owner(db):
    variant = _require_source(db, execution_owner=False)
    facts = _execution_facts(db, execution_owner=False)
    _rebuild(db, variant, facts, execution_owner=True)
    return {'reply_variant': variant}


def restore_execution_owner(db, metadata):
    variant = _require_source(db, execution_owner=True)
    if metadata != {'reply_variant': variant}:
        raise ValueError('unsupported execution-owner inverse metadata')
    facts = _execution_facts(db, execution_owner=True)
    _rebuild(db, variant, facts, execution_owner=False)
