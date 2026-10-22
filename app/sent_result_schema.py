"""Sent receipt ownership and an exact inverse from current Sent/receipt facts."""
from functools import lru_cache
import json

from app import action_storage as actions
from app.model_action_schema import _dependencies
from app.model_result_schema import _inbound_dependencies
from app.storage_payload import ACTION_RECORD_TYPES


_SENT = 'sent_replies'
_STAGING = '_sent_result_actions'
_WATER = '_sent_result_water'


@lru_cache(maxsize=4)
def sent_action_objects(reply_variant, *, sent_result_references=True):
    from app.email_canonical_storage import ACTION_OPTIONS
    return actions._current_objects(
        reply_variant=reply_variant, inline_legacy_owner=True,
        physical_provenance=True, compact_payload=True, canonical_email=True,
        selection_references=True, result_references=True, execution_owner=True,
        sent_result_references=sent_result_references, **ACTION_OPTIONS)


def _require_source(db, *, sent_result_references):
    from app.business_candidate_storage import _collision
    if not db.in_transaction or db.execute('pragma foreign_keys').fetchone()[0]:
        raise ValueError('Sent result transition requires caller-owned FK-off transaction')
    _collision(db, ('actions', _STAGING, _WATER))
    objects = actions._objects(db, 'actions')
    variant = next((variant for variant in actions.REPLY_VARIANTS
                    if objects == sent_action_objects(variant, sent_result_references=sent_result_references)), None)
    if variant is None:
        raise ValueError('unsupported Sent result Actions source declaration')
    _dependencies(db)
    _inbound_dependencies(db)
    if db.execute('pragma foreign_key_check(actions)').fetchone() or db.execute('pragma quick_check').fetchone()[0] != 'ok':
        raise ValueError('Sent source integrity or foreign keys are invalid')
    return variant


def _results(db, *, sent_result_references):
    """Only exact SQLite text values with a nonempty matching key share an owner."""
    updates = []
    for physical_id, key, body, receipt in db.execute('''select a.id,a.external_action_key,a.send_result_json,r.provider_result_json
        from main.actions a left join main.actions r
          on r.record_type='external_action_results' and r.legacy_key=a.external_action_key
        where a.record_type='sent_replies' order by a.id'''):
        if body is None:
            if not key or receipt is None:
                raise ValueError('Sent result reference lacks its exact receipt owner')
            if not sent_result_references:
                updates.append((receipt, physical_id))
        elif sent_result_references and key and type(body) is type(receipt) and body == receipt:
            updates.append((None, physical_id))
    return updates


def _rebuild(db, variant, updates, *, sent_result_references):
    from app.action_email_storage import _columns, _quote
    columns = _columns(db, 'actions')
    names = ','.join(map(_quote, columns))
    foreign_keys = sorted(tuple(row) for row in db.execute('pragma foreign_key_check'))
    db.execute(f'create temp table {_STAGING} as select {names} from main.actions')
    db.execute(f'create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence')
    path = actions.action_payloads(variant).path(_SENT, 'send_result_json')
    db.executemany(f'update temp.{_STAGING} set payload_json=jsonb_set(payload_json,?,json(?)) where id=? and _kind=?',
                   ((path, json.dumps(body, ensure_ascii=False), physical_id, ACTION_RECORD_TYPES.code(_SENT)) for body, physical_id in updates))
    db.execute('drop table main.actions')
    objects = sent_action_objects(variant, sent_result_references=sent_result_references)
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
            raise ValueError('Sent result transition changed stored values or types')
    if (actions._objects(db, 'actions') != objects
            or sorted(tuple(row) for row in db.execute('pragma foreign_key_check')) != foreign_keys):
        raise ValueError('Sent result transition changed declaration or foreign keys')
    db.execute(f'drop table temp.{_STAGING}')
    db.execute(f'drop table temp.{_WATER}')


def migrate_sent_result_references(db):
    variant = _require_source(db, sent_result_references=False)
    updates = _results(db, sent_result_references=True)
    _rebuild(db, variant, updates, sent_result_references=True)
    return {'reply_variant': variant}


def restore_sent_result_references(db, metadata):
    variant = _require_source(db, sent_result_references=True)
    if metadata != {'reply_variant': variant}:
        raise ValueError('unsupported Sent result inverse metadata')
    updates = _results(db, sent_result_references=False)
    _rebuild(db, variant, updates, sent_result_references=False)
