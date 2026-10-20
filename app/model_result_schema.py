"""Receipt-owned verified results with an inverse from current facts.

Only byte-equal verified attempt results become references. The result slot
continues to own failed, uncertain and distinct attempt facts. Both transitions
require the caller's FK-off transaction and retain no historical row backup.
"""
from functools import lru_cache
import json

from app import action_storage as actions
from app.model_action_schema import _dependencies
from app.storage_payload import ACTION_RECORD_TYPES


_ATTEMPT = 'candidate_action_attempts'
_STAGING = '_model_result_actions'
_WATER = '_model_result_water'


@lru_cache(maxsize=4)
def result_action_objects(reply_variant, *, result_references=True):
    from app.email_canonical_storage import ACTION_OPTIONS
    return actions._current_objects(
        reply_variant=reply_variant, inline_legacy_owner=True,
        physical_provenance=True, compact_payload=True, canonical_email=True,
        selection_references=True, result_references=result_references, **ACTION_OPTIONS)


def _require_source(db, *, result_references):
    from app.business_candidate_storage import _collision
    if not db.in_transaction or db.execute('pragma foreign_keys').fetchone()[0]:
        raise ValueError('result transition requires caller-owned FK-off transaction')
    _collision(db, ('actions', _STAGING, _WATER))
    objects = actions._objects(db, 'actions')
    variant = next((variant for variant in actions.REPLY_VARIANTS
                    if objects == result_action_objects(variant, result_references=result_references)), None)
    if variant is None:
        raise ValueError('unsupported result Actions source declaration')
    _dependencies(db)
    _inbound_dependencies(db)
    return variant


def _inbound_dependencies(db):
    """Retain only the existing Business links to preserved Action endpoints."""
    from app.business_legacy_owner_storage import _business_declaration
    from app.action_email_storage import _quote
    fields = ("id", "seq", "table", "from", "to", "on_update", "on_delete", "match")
    expected = [tuple(fk[field] for field in fields) for fk in _business_declaration(target=True)["foreign_keys"]
                if fk["table"] == "actions"]
    for schema in ("main", "temp"):
        for (table,) in db.execute(f"select name from {schema}.sqlite_schema where type='table'").fetchall():
            if schema == "main" and table == "actions":
                continue
            incoming = [tuple(row) for row in db.execute(f"pragma {schema}.foreign_key_list({_quote(table)})")
                        if actions.sqlite_schema_name(row[2]) == "actions"]
            if incoming and (schema != "main" or table != "business" or incoming != expected):
                raise ValueError("unsupported inbound result Actions dependency")


def _results(db, *, result_references):
    """Plan only exact ownership changes, validating every stored reference."""
    updates = []
    for physical_id, status, body, receipt in db.execute('''select a.id,a.status,a.result_json,r.provider_result_json
        from main.actions a left join main.actions r
          on r.record_type='external_action_results' and r.legacy_key=a.external_action_key
        where a.record_type='candidate_action_attempts' order by a.id'''):
        if body is None:
            if status != 'verified' or receipt is None:
                raise ValueError('verified result reference lacks its exact receipt owner')
            if not result_references:
                updates.append((receipt, physical_id))
        elif result_references and status == 'verified' and receipt is not None and body == receipt:
            updates.append((None, physical_id))
    return updates


def _rebuild(db, variant, updates, *, result_references):
    from app.action_email_storage import _columns, _quote
    columns = _columns(db, 'actions')
    names = ','.join(map(_quote, columns))
    foreign_keys = sorted(tuple(row) for row in db.execute('pragma foreign_key_check'))
    db.execute(f'create temp table {_STAGING} as select {names} from main.actions')
    db.execute(f'create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence')
    path = actions.action_payloads(variant).path(_ATTEMPT, 'result_json')
    db.executemany(f'update temp.{_STAGING} set payload_json=jsonb_set(payload_json,?,json(?)) where id=? and _kind=?',
                   ((path, json.dumps(body, ensure_ascii=False), physical_id, ACTION_RECORD_TYPES.code(_ATTEMPT)) for body, physical_id in updates))
    db.execute('drop table main.actions')
    objects = result_action_objects(variant, result_references=result_references)
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
            raise ValueError('result transition changed stored values or types')
    if (actions._objects(db, 'actions') != objects
            or sorted(tuple(row) for row in db.execute('pragma foreign_key_check')) != foreign_keys):
        raise ValueError('result transition changed declaration or foreign keys')
    db.execute(f'drop table temp.{_STAGING}')
    db.execute(f'drop table temp.{_WATER}')


def migrate_result_references(db):
    variant = _require_source(db, result_references=False)
    updates = _results(db, result_references=True)
    _rebuild(db, variant, updates, result_references=True)
    return {'reply_variant': variant}


def restore_result_references(db, metadata):
    variant = _require_source(db, result_references=True)
    if metadata != {'reply_variant': variant}:
        raise ValueError('unsupported result inverse metadata')
    updates = _results(db, result_references=False)
    _rebuild(db, variant, updates, result_references=False)
