"""One owner/writer for typed reviewed and deterministic-email action facts.

Callers retain their business authorization, provider verification and existing
transaction boundary. This module owns plan/step allocation, dispatch changes,
and attempt outcomes. Reviewed dispatch reuses its original current slot; email
outcomes append the original numbered ledger. There is no synthetic Agent run.
"""
from contextlib import closing
import json
import sqlite3

from app import action_storage as storage
from app.action_email_definitions import EMAIL_FIELDS, EMAIL_DEFAULTS, EMAIL_NUMERIC_SCOPE
from app.shared_action_definitions import SHARED_RECORD_TYPES, SOURCE_TO_TARGET, email_projection

PLAN_KINDS = {'reviewed': 'review_candidates', 'email': 'email_action_plans'}
ATTEMPT_KINDS = {'reviewed': 'candidate_action_attempts', 'email': 'email_action_attempts'}


def _layout(kind):
    return storage.action_payloads('actual_no_memory_fk', selection_references=True,
        execution_owner=True, shared_lifecycle=True).layouts[kind]


def _fetch(db, sql, parameters=()):
    cursor = db.execute(sql, parameters)
    row = cursor.fetchone()
    return dict(zip((column[0] for column in cursor.description), row, strict=True)) if row is not None else None


def _allocate(db, kind, supplied=None):
    """Preserve each old namespace and its ordinary/AUTOINCREMENT semantics."""
    if supplied is not None:
        return storage._native_integer_primary_key(supplied)
    maximum = db.execute('select max(cast(legacy_key as integer)) from actions where record_type=? and ' + EMAIL_NUMERIC_SCOPE, (kind,)).fetchone()[0]
    if kind == 'email_action_attempts':
        waters = db.execute('select seq from sqlite_sequence where name=?', ('actions:' + kind,)).fetchall()
        if len(waters) > 1 or any(type(row[0]) is not int or row[0] < 0 for row in waters):
            raise ValueError('invalid email_action_attempts allocator metadata')
        maximum = max(maximum or 0, waters[0][0] if waters else 0)
        if maximum == 2**63 - 1:
            if db.in_transaction:
                db.execute('rollback')
            storage._raise_autoincrement_full()
    elif maximum == 2**63 - 1:
        with closing(sqlite3.connect(':memory:')) as allocator:
            allocator.execute('create table ids(id integer primary key)')
            allocator.executemany('insert into ids values(?)', db.execute(
                'select cast(legacy_key as integer) from actions where record_type=?', (kind,)))
            return allocator.execute('insert into ids default values').lastrowid
    return (maximum or 0) + 1


def _insert(db, kind, values, *, ignore=False, plan_step_conflict=False):
    storage._check_fields(kind, values)
    storage._email_lock(db, tuple(values.values()))
    layout = _layout(kind)
    if kind in EMAIL_FIELDS:
        defaults = {field: db.execute('select ' + expression).fetchone()[0]
                    for field, expression in EMAIL_DEFAULTS[kind].items() if field in layout.fields}
        references = {}
    else:
        clock = db.execute('select current_timestamp').fetchone()[0]
        defaults = {**storage.DEFAULTS[kind], **{field: clock for field in ('created_at', 'updated_at') if field in layout.fields}}
        references = storage._kind_references(kind, execution_owner=True)
    payload = dict.fromkeys(layout.fields) | defaults | {field: value for field, value in values.items() if field in layout.fields}
    public = _allocate(db, kind, values.get('id'))
    physical = {
        field: (storage.action_physical_id(db, storage.EXECUTION_ALIAS, values.get(field))
                if kind == 'candidate_action_attempts' and field == 'execution_id'
                else storage._parent_id(db, field, values.get(field)))
        for field in references
    }
    columns = ('_kind', 'legacy_key', 'payload_json', *physical)
    packed = db.execute('select jsonb(?)', (layout.pack(payload, scalar_codec=storage._encoded_native_sqlite_value),)).fetchone()[0]
    conflict = (" on conflict(email_action_plan_id,email_action_type) where record_type='email_actions' do nothing"
                if plan_step_conflict else '')
    cursor = db.execute('insert ' + ('or ignore ' if ignore else '') + 'into actions('
        + ','.join(columns) + ') values(' + ','.join('?' for _ in columns) + ')' + conflict
        + ' returning cast(legacy_key as integer)',
        (SHARED_RECORD_TYPES.code(kind), str(public), packed, *physical.values()))
    row = cursor.fetchone()
    cursor.fetchall()
    return storage.ActionWriteResult(row[0] if row else None, cursor.rowcount)


def _update(db, kind, public_id, values):
    if kind not in SOURCE_TO_TARGET:
        raise ValueError('unknown shared action variant')
    storage._check_fields(kind, values)
    layout = _layout(kind)
    if not values or set(values) - set(layout.fields):
        raise ValueError('unknown shared action update fields')
    paths = []
    for field, value in values.items():
        paths.extend((layout.path(field), json.dumps(storage._encoded_native_sqlite_value(value),
            ensure_ascii=False, allow_nan=False, separators=(',', ':'))))
    return db.execute('update actions set payload_json=jsonb_set(payload_json,'
        + ','.join('?,json(?)' for _ in values) + ') where _kind=? and cast(legacy_key as integer)=?',
        (*paths, SHARED_RECORD_TYPES.code(kind), public_id))


def persist_plan(db, origin, values, *, ignore=False):
    kind = PLAN_KINDS[origin]
    if origin == 'email':
        from app.email_store import EmailActionPlanConflict
        existing = _fetch(db, 'select * from (' + email_projection(kind) + ') where action_plan_id=?', (values['action_plan_id'],))
        immutable = tuple(field for field in EMAIL_FIELDS[kind] if field not in ('action_plan_id', 'legacy_serialization_pre_v16'))
        if existing is not None:
            if any(existing[field] != values[field] for field in immutable):
                raise EmailActionPlanConflict(f"ActionPlan ID {values['action_plan_id']} has different immutable fields")
            return storage.ActionWriteResult(None, 0)
        versions = db.execute('select action_plan_version from (' + email_projection(kind) + ') where classification_id=?', (values['classification_id'],)).fetchall()
        if any(row[0] == values['action_plan_version'] for row in versions):
            raise EmailActionPlanConflict(f"ActionPlan version {values['action_plan_version']} already exists for classification {values['classification_id']}")
        wanted = max((row[0] for row in versions), default=0) + 1
        if values['action_plan_version'] != wanted:
            raise EmailActionPlanConflict(f"ActionPlan version must be {wanted} for classification {values['classification_id']}")
    return _insert(db, kind, values, ignore=ignore)


def update_plan(db, origin, public_id, values, *, where_sql='', parameters=()):
    kind = PLAN_KINDS[origin]
    if where_sql:
        projection = storage.REVIEW_CANDIDATE_SQL if origin == 'reviewed' else email_projection(kind, include_rowid=True)
        identity = 'id' if origin == 'reviewed' else 'rowid'
        if not db.execute(f'select 1 from ({projection}) where {identity}=? and ({where_sql})',
                          (public_id, *parameters)).fetchone():
            return storage.ActionWriteResult(None, 0)
    return _update(db, kind, public_id, values)


def ensure_effect_step(db, origin, values):
    if origin != 'email':
        raise ValueError('reviewed effect steps are read from the bound immutable plan')
    return _insert(db, 'email_actions', values, plan_step_conflict=True)


def effect_steps(db, origin, plan, *, selection=None):
    """Read exact adopted steps; a reviewed action body is never copied."""
    if origin == 'email':
        cursor = db.execute('select * from (' + email_projection('email_actions') + ') where action_plan_id=?', (plan,))
        return [dict(zip((column[0] for column in cursor.description), row, strict=True)) for row in cursor]
    if origin != 'reviewed':
        raise ValueError('unknown action origin')
    from app.model_action_schema import selected_branch
    data = json.loads(plan['candidate_json'])
    proposal = selected_branch(db, selection['id']).get('plan') if selection else data.get('proposal')
    return proposal.get('actions', []) if isinstance(proposal, dict) else []


def _email_step(db, action_id):
    return _fetch(db, 'select * from (' + email_projection('email_actions', include_rowid=True) + ') where action_id=?', (action_id,))


def _matches(row, expected):
    return row is not None and all(value is None or row[field] == value for field, value in expected.items())


def transition(db, origin, event, *, identity=None, values=None, expected=None, receipt=None):
    """Apply one existing claim/outcome/schedule transition under its caller lock.

    Email identity is its public action_id. Reviewed claim identity is the exact
    (execution public ID, action_index, external_action_key); reviewed outcome
    identity is its current attempt's public ID. The finite variants determine
    whether an outcome mutates a slot or appends a numbered attempt.
    """
    if not db.in_transaction:
        raise ValueError('shared action transition requires caller-owned transaction')
    values, expected = values or {}, expected or {}
    if origin == 'reviewed':
        if event == 'outcome':
            return _update(db, ATTEMPT_KINDS[origin], identity, values)
        if event != 'claim':
            raise ValueError('unknown reviewed action transition')
        execution_id, index, external_key = identity
        old = storage.candidate_action_attempt_row(_fetch(db,
            'select * from (' + storage.CANDIDATE_ACTION_ATTEMPT_SQL
            + ') where execution_id=? and action_index=?', (execution_id, index)))
        if old is not None:
            if old['external_action_key'] != external_key:
                raise ValueError('action key conflict')
            changes = {}
            if receipt is not None and old['status'] != receipt['status']:
                result = old['result_json']
                if receipt['status'] == 'verified' and result == receipt['provider_result_json']:
                    result = None
                changes = {'status': receipt['status'], 'result_json': result}
            elif old['status'] in ('dispatched', 'confirmed_no_effect'):
                changes = {'status': 'uncertain' if old['status'] == 'dispatched' else 'dispatched'}
            if changes:
                _update(db, ATTEMPT_KINDS[origin], old['id'], changes | {'updated_at': values['updated_at']})
            public = old['id']
        else:
            result = None if receipt is not None and receipt['status'] == 'verified' and receipt['provider_result_json'] == '{}' else '{}'
            public = _insert(db, ATTEMPT_KINDS[origin], {
                'execution_id': execution_id, 'action_index': index, 'external_action_key': external_key,
                'status': receipt['status'] if receipt is not None else 'dispatched',
                'result_json': result, 'created_at': values['created_at'],
            }).lastrowid
        return storage.candidate_action_attempt_row(_fetch(db, 'select * from ('
            + storage.CANDIDATE_ACTION_ATTEMPT_SQL + ') where id=?', (public,)))
    if origin != 'email':
        raise ValueError('unknown action origin')
    row = _email_step(db, identity)
    if row is None and event == 'outcome':
        from app.email_store import EmailActionAttemptConflict
        raise EmailActionAttemptConflict(f'unknown direct email action {identity}')
    if not _matches(row, expected):
        return storage.ActionWriteResult(None, 0)
    if event == 'claim':
        return _update(db, 'email_actions', row['rowid'], {
            'status': 'processing', 'started_at': values['started_at'],
            'next_attempt_at': '', 'updated_at': values['updated_at'],
        })
    if event == 'schedule':
        return _update(db, 'email_actions', row['rowid'], values)
    if event != 'outcome':
        raise ValueError('unknown email action transition')
    from app.email_store import EmailActionAttemptConflict
    attempt_number = values['attempt_number']
    if attempt_number <= row['attempt_count']:
        raise EmailActionAttemptConflict(f'attempt {attempt_number} already exists for {identity}')
    if attempt_number != row['attempt_count'] + 1:
        raise EmailActionAttemptConflict(f'attempt number must be {row["attempt_count"] + 1} for {identity}')
    attempt = _insert(db, ATTEMPT_KINDS[origin], {
        field: (identity if field == 'action_id' else values[field])
        for field in EMAIL_FIELDS['email_action_attempts'] if field != 'id'
    })
    if expected:
        after_append = {**expected, 'attempt_count': attempt_number}
        if not _matches(_email_step(db, identity), after_append):
            raise EmailActionAttemptConflict(f'direct action claim changed for {identity}')
    changes = {'status': values['status'], 'started_at': '', 'updated_at': values['updated_at']}
    if 'next_attempt_at' in values:
        changes['next_attempt_at'] = values['next_attempt_at']
    _update(db, 'email_actions', row['rowid'], changes)
    return attempt
