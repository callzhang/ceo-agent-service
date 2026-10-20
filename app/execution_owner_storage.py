"""Candidate-owned execution writes; the alias reserves only source identity."""

import json

from app.execution_owner_definitions import ALIAS, EXECUTION_FIELDS


def update_plan_execution(db, execution_id, values):
    from app import action_storage as actions

    if not values or set(values) - {field.removeprefix('execution_') for field in EXECUTION_FIELDS[1:]}:
        raise ValueError('unknown plan execution update')
    layout = actions.action_payloads('actual_no_memory_fk', execution_owner=True).layouts['review_candidates']
    parameters = []
    for field, value in values.items():
        parameters.extend((layout.path('execution_' + field), json.dumps(value, ensure_ascii=False)))
    return db.execute('update actions set payload_json=jsonb_set(payload_json,'
        + ','.join('?,json(?)' for _ in values) + ') '
        "where record_type='review_candidates' and id=(select candidate_id from actions "
        "where record_type='candidate_execution_alias' and cast(legacy_key as integer)=?)",
        (*parameters, execution_id))


def create_plan_execution(db, *, candidate_id, review_id, selection_id,
                          status, lease_owner, lease_expires_at):
    from app import action_storage as actions

    if not db.in_transaction:
        raise ValueError('plan execution requires caller-owned transaction')
    candidate = actions.action_physical_id(db, 'review_candidates', candidate_id)
    review = actions.action_physical_id(db, 'candidate_reviews', review_id)
    selection = actions.action_physical_id(db, 'candidate_selections', selection_id)
    # This source family was INTEGER PRIMARY KEY, never AUTOINCREMENT. The
    # existing ordinary ROWID allocator also preserves native random allocation
    # at signed-int64 exhaustion, without inventing a persistent high water.
    public_id = actions._email_rowid_next(db, ALIAS)
    db.execute('insert into actions(_kind,legacy_key,candidate_id,payload_json) values(?,?,?,jsonb(\'[]\'))',
               (actions.ACTION_RECORD_TYPES.code(ALIAS), str(public_id), candidate))
    clock = db.execute('select current_timestamp').fetchone()[0]
    layout = actions.action_payloads('actual_no_memory_fk', execution_owner=True).layouts['review_candidates']
    values = dict(zip(EXECUTION_FIELDS, (public_id, status, lease_owner, lease_expires_at,
                                       '{}', clock, clock), strict=True))
    parameters = []
    for field, value in values.items():
        parameters.extend((layout.path(field), json.dumps(value, ensure_ascii=False)))
    db.execute('update actions set execution_review_id=?,execution_selection_id=?,payload_json=jsonb_set(payload_json,'
               + ','.join('?,json(?)' for _ in values) + ") where record_type='review_candidates' and id=?",
               (review, selection, *parameters, candidate))
    return public_id
