"""One writer for reviewed, Email and prepared-message claims and effect results.

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
from app.shared_action_definitions import SPECIALIZED_RECORD_TYPES, SOURCE_TO_TARGET, SPECIALIZED_SOURCE_TO_TARGET, email_projection

PLAN_KINDS = {'reviewed': 'review_candidates', 'email': 'email_action_plans'}
STEP_KINDS = {'email_reply': 'email_reply_dispatch_claims', 'email_unsubscribe': 'email_unsubscribe_claims'}
RESULT_KINDS = {'email_reply': 'email_reply_receipts', 'email_unsubscribe': 'email_unsubscribe_receipts'}


def _layout(kind):
    return storage.action_payloads('actual_no_memory_fk', selection_references=True,
        execution_owner=True, shared_lifecycle=True, specialized_claims=True).layouts[kind]


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
        (SPECIALIZED_RECORD_TYPES.code(kind), str(public), packed, *physical.values()))
    row = cursor.fetchone()
    cursor.fetchall()
    return storage.ActionWriteResult(row[0] if row else None, cursor.rowcount)


def _predicate(kind, public_id, where_sql, parameters):
    if where_sql:
        # Resolve the exact source predicate to existing physical IDs in the
        # mutation itself; the outer statement seeks only these Actions rows.
        predicate = 'id in (select physical_action_id from (' + email_projection(kind,
            include_rowid=True, include_physical_id=True) + ') where ' + where_sql + ')'
        arguments = tuple(parameters)
    else:
        predicate = '_kind=?'
        arguments = (SPECIALIZED_RECORD_TYPES.code(kind),)
    if public_id is not None:
        predicate += ' and cast(legacy_key as integer)=?'
        arguments += (public_id,)
    return predicate, arguments


def _update(db, kind, public_id, values, *, where_sql='', parameters=()):
    if kind not in {*SOURCE_TO_TARGET, *SPECIALIZED_SOURCE_TO_TARGET, *RESULT_KINDS.values()}:
        raise ValueError('unknown shared action variant')
    storage._check_fields(kind, values)
    layout = _layout(kind)
    if not values or set(values) - set(layout.fields):
        raise ValueError('unknown shared action update fields')
    paths = []
    for field, value in values.items():
        paths.extend((layout.path(field), json.dumps(storage._encoded_native_sqlite_value(value),
            ensure_ascii=False, allow_nan=False, separators=(',', ':'))))
    predicate, arguments = _predicate(kind, public_id, where_sql, parameters)
    return db.execute('update actions set payload_json=jsonb_set(payload_json,'
        + ','.join('?,json(?)' for _ in values) + ') where ' + predicate,
        (*paths, *arguments))


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
        if origin == 'reviewed':
            from app.store import NATIVE_REVIEW_CANDIDATE_SQL
            projection = NATIVE_REVIEW_CANDIDATE_SQL
        else:
            projection = email_projection(kind, include_rowid=True)
        identity = 'candidate_id' if origin == 'reviewed' else 'rowid'
        if not db.execute(f'select 1 from ({projection}) where {identity}=? and ({where_sql})',
                          (public_id, *parameters)).fetchone():
            return storage.ActionWriteResult(None, 0)
    return _update(db, kind, public_id, values)


def create_direct_email_action(db, values):
    """Create one direct-email Action; config remains on its immutable plan."""
    from app.email_classifier_contracts import DIRECT_ACTIONS
    from app.action_entity_storage import create_action, get_action

    if not db.in_transaction:
        raise ValueError('direct email Action creation requires caller-owned transaction')
    if values['action_type'] not in {action.value for action in DIRECT_ACTIONS}:
        raise ValueError('only executable direct email operations have Action owners')
    fields = {
        'operation': values['action_type'],
        'parameters_json': values['parameters_json'],
        'status': values['status'],
        'action_plan_id': values['action_plan_id'],
        'classification_id': values['classification_id'],
        'account_id': values['account_id'],
        'external_action_key': None,
        'next_attempt_at': values.get('next_attempt_at'),
        'started_at': values.get('started_at'),
        'created_at': values['created_at'],
        'updated_at': values['updated_at'],
        'action_identity': None,
    }
    old = db.execute(
        "select id from actions where record_type='action' and action_action_plan_id=? and action_operation=?",
        (values['action_plan_id'], values['action_type']),
    ).fetchone()
    if old is not None:
        current = get_action(db, int(old['id']))
        if any(current[key] != value for key, value in fields.items()
               if key not in {'status', 'next_attempt_at', 'started_at', 'updated_at'}):
            raise ValueError('direct email Action identity conflicts with existing owner')
        return current
    return create_action(db, values=fields)


def claim_direct_email_action(db, *, action_id, expected_status, expected_started_at,
                              attempt_count, claimed_at):
    """Claim one direct email Action without routing through a source origin."""
    from app.action_entity_storage import claim_action

    owner = db.execute(
        "select id from actions where record_type='action' and id=?",
        (action_id,),
    ).fetchone()
    if owner is None:
        return None
    current_attempt = db.execute(
        'select coalesce(max(attempt_number),0) from action_receipts where action_id=? and attempt_number is not null',
        (owner['id'],),
    ).fetchone()[0]
    if current_attempt != attempt_count:
        return None
    action = claim_action(db, int(owner['id']),
        expected={'status': expected_status, 'started_at': expected_started_at},
        changes={'status': 'processing', 'started_at': claimed_at, 'updated_at': claimed_at})
    return action


def schedule_direct_email_action(db, *, action_id, expected_status, next_attempt_at, updated_at):
    """Update retry timing on the direct Action owner under the caller lock."""
    from app.action_entity_storage import claim_action

    owner = db.execute(
        "select id from actions where record_type='action' and id=?",
        (action_id,),
    ).fetchone()
    if owner is None:
        return None
    return claim_action(db, int(owner['id']), expected={'status': expected_status},
        changes={'next_attempt_at': next_attempt_at, 'updated_at': updated_at})


def record_direct_email_action_outcome(db, *, action_id, expected_status, expected_started_at,
                                       expected_attempt_count, attempt_number, status,
                                       provider_operation, provider_target, provider_result_id,
                                       error, started_at, finished_at, next_attempt_at, updated_at):
    """Append an attempt receipt and update its Action in one caller transaction."""
    from app.action_entity_storage import append_action_receipt, record_action_result

    if attempt_number != expected_attempt_count + 1:
        from app.email_store import EmailActionAttemptConflict
        raise EmailActionAttemptConflict(
            f'attempt number must be {expected_attempt_count + 1} for {action_id}'
        )

    owner = db.execute(
        "select id from actions where record_type='action' and id=?",
        (action_id,),
    ).fetchone()
    if owner is None:
        return None
    owner_id = int(owner['id'])
    current_attempt = db.execute(
        'select coalesce(max(attempt_number),0) from action_receipts where action_id=? and attempt_number is not null',
        (owner_id,),
    ).fetchone()[0]
    if current_attempt != expected_attempt_count:
        return None
    updated = record_action_result(db, owner_id,
        expected={'status': expected_status, 'started_at': expected_started_at},
        changes={'status': status, 'started_at': None if status != 'processing' else started_at,
                 'next_attempt_at': next_attempt_at, 'updated_at': updated_at})
    if updated is None:
        return None
    return append_action_receipt(db, owner_id, values={
        'attempt_number': attempt_number, 'status': status,
        'operation': provider_operation, 'target': provider_target,
        'provider_result_id': provider_result_id or None,
        'error_text': error or None, 'started_at': started_at,
        'finished_at': finished_at, 'created_at': finished_at, 'updated_at': updated_at,
    })


def record_sent_action(db, *, operation=None, target_json=None, provider_json, result_digest,
                       external_action_key, business_object_key, action_identity,
                       channel, conversation_id, trigger_message_id, reply_text,
                       feedback_token, recall_key='', agent_run_id, task_id,
                       observed_at=None):
    """Record one completed message effect on its common Action owner."""
    from app.action_entity_storage import (
        add_action_observer, append_action_receipt, create_action,
        get_action, record_action_result,
    )

    if not db.in_transaction:
        raise ValueError('sent Action write requires caller-owned transaction')
    if not conversation_id or not trigger_message_id or not reply_text:
        raise ValueError('sent Action projection is incomplete')
    if external_action_key:
        match = db.execute(
            "select id from actions where record_type='action' and action_external_action_key=?",
            (external_action_key,),
        ).fetchone()
    else:
        match = None
    now = observed_at or db.execute('select current_timestamp').fetchone()[0]
    if match is None:
        action = create_action(db, values={
            'operation': operation, 'external_action_key': external_action_key or None,
            'agent_run_id': agent_run_id, 'business_object_key': business_object_key,
            'action_identity': action_identity, 'delivery_channel': channel or 'dingtalk',
            'conversation_id': conversation_id, 'trigger_message_id': trigger_message_id,
            'prepared_body': reply_text, 'prepared_at': None,
            'feedback_token': feedback_token or '', 'created_at': None, 'updated_at': None,
            'recall_key': recall_key or '',
        })
    else:
        action = get_action(db, int(match['id']))
        if action is not None and action['prepared_body'] not in (None, reply_text):
            raise ValueError('conflicting message delivery projection')
        if action is None or (
            action['external_action_key'] != (external_action_key or None)
            or (action['operation'] is not None and operation is not None
                and action['operation'] != operation)
        ):
            raise ValueError('conflicting sent Action identity')
        # The old Sent writer kept the first row for a known delivery key and
        # still recorded each observing run. Preserve those first facts when a
        # later observer carries a different local projection.
        changes = {}
        for field, value in (
            ('conversation_id', conversation_id),
            ('trigger_message_id', trigger_message_id),
            ('prepared_body', reply_text),
            ('delivery_channel', channel or 'dingtalk'),
            ('feedback_token', feedback_token or ''),
            ('recall_key', recall_key or ''),
        ):
            if action[field] in (None, '') and value not in (None, ''):
                changes[field] = value
        if action['operation'] is None and operation is not None:
            changes['operation'] = operation
        if action['agent_run_id'] is None and agent_run_id is not None:
            changes['agent_run_id'] = agent_run_id
        if action['business_object_key'] is None and business_object_key is not None:
            changes['business_object_key'] = business_object_key
        if action['action_identity'] is None and action_identity is not None:
            changes['action_identity'] = action_identity
        if changes and record_action_result(db, action['id'], expected={'status': action['status']},
                                            changes=changes) is None:
            raise ValueError('sent Action changed during result write')
        action = get_action(db, action['id'])

    prior = db.execute(
        'select id,operation,target,provider_result_json,result_digest,agent_run_id,observed_at from action_receipts '
        'where action_id=? and provider_result_json is not null and operation is ? and target is ? '
        'order by id limit 1',
        (action['id'], operation, target_json),
    ).fetchone()
    if prior is None and provider_json is not None:
        receipt_id = append_action_receipt(db, action['id'], values={
            'operation': operation, 'target': target_json,
            'provider_result_json': provider_json, 'result_digest': result_digest,
            'agent_run_id': agent_run_id, 'observed_at': now,
            'created_at': None, 'updated_at': None,
        })
    elif prior is not None:
        if (prior['operation'] is not None and operation is not None
                and prior['operation'] != operation
                or prior['target'] is not None and target_json is not None
                and prior['target'] != target_json):
            raise ValueError('conflicting sent Action provider target')
        if external_action_key and any(value is None for value in (
            prior['operation'], prior['target'], prior['result_digest'],
            prior['agent_run_id'], action['business_object_key'],
            action['action_identity'],
        )):
            _bind_provider_receipt_metadata(
                db, action_id=action['id'], provider_receipt_id=int(prior['id']),
                operation=operation, target_json=target_json,
                result_digest=prior['result_digest'] or result_digest,
                agent_run_id=prior['agent_run_id'] or agent_run_id,
                observed_at=prior['observed_at'], created_at=now,
            )
        receipt_id = prior['id']
    else:
        receipt_id = append_action_receipt(db, action['id'], values={
            'operation': operation, 'target': target_json,
            'agent_run_id': agent_run_id, 'observed_at': now,
            'created_at': None, 'updated_at': None,
        })
    action = get_action(db, action['id'])
    sent_fact_id = action['delivery_receipt_id']
    if sent_fact_id is None:
        sent_fact_id = append_action_receipt(db, action['id'], values={
            'provider_receipt_id': receipt_id if provider_json is not None else None,
            'operation': operation, 'target': target_json,
            'agent_run_id': agent_run_id, 'observed_at': now,
            'created_at': None, 'updated_at': None,
        })
        if record_action_result(db, action['id'],
                expected={'delivery_receipt_id': None},
                changes={'delivery_receipt_id': sent_fact_id}) is None:
            raise ValueError('Sent receipt reference changed during write')
    if external_action_key and provider_json is not None:
        _bind_provider_receipt_metadata(
            db, action_id=action['id'], provider_receipt_id=int(receipt_id),
            operation=operation, target_json=target_json,
            result_digest=result_digest, agent_run_id=agent_run_id,
            observed_at=now, created_at=now,
        )
    if agent_run_id is not None and task_id is not None:
        add_action_observer(db, action['id'], agent_run_id=agent_run_id,
                            task_id=task_id, created_at=now)
    return {'action': action, 'receipt_id': receipt_id}


def _bind_provider_receipt_metadata(db, *, action_id, provider_receipt_id,
                                    operation, target_json, result_digest,
                                    agent_run_id, observed_at, created_at,
                                    execution_owner_column='execution_run_id'):
    """Record canonical source metadata as a reference to an immutable body."""
    if any(value is None for value in (operation, target_json, result_digest, agent_run_id)):
        return None
    from app.action_entity_storage import get_action, update_sent_receipt
    action = get_action(db, action_id)
    if action is None:
        raise ValueError('canonical provider Action is missing')
    if execution_owner_column != 'execution_run_id':
        raise ValueError('invalid provider receipt execution owner column')
    metadata = db.execute(
        'select id,provider_receipt_id,operation,target,result_digest,agent_run_id,created_at,observed_at '
        'from action_receipts where action_id=? and provider_result_json is null '
        'and provider_receipt_id is not null and status is null and attempt_number is null '
        f'and {execution_owner_column} is null and action_index is null '
        'and target is not null and agent_run_id is not null and created_at is not null '
        'and id not in (select coalesce(action_delivery_receipt_id,-1) from actions where id=?) '
        'and id not in (select coalesce(action_prepared_receipt_id,-1) from actions where id=?)',
        (action_id, action_id, action_id),
    ).fetchall()
    if len(metadata) > 1:
        raise ValueError('ambiguous canonical provider metadata receipts')
    existing = metadata[0] if metadata else None
    if existing is not None:
        if ((existing['operation'] is not None and existing['operation'] != operation)
                or existing['target'] != target_json):
            raise ValueError('canonical provider metadata identity changed')
        # An ordinary writer may observe an older receipt after correction.
        # Binding creation never moves an established canonical pointer.
        changes = {}
        if existing['operation'] is None:
            changes['operation'] = operation
        if changes and update_sent_receipt(
            db, action_id=action_id, receipt_id=int(existing['id']),
            expected={'operation': existing['operation']},
            changes=changes, execution_owner_column=execution_owner_column,
        ) is None:
            raise ValueError('canonical provider metadata changed during binding')
        return int(existing['id'])
    from app.action_entity_storage import append_action_receipt
    return append_action_receipt(db, action_id, values={
        'provider_receipt_id': provider_receipt_id, 'operation': operation,
        'target': target_json, 'result_digest': result_digest,
        'agent_run_id': agent_run_id, 'observed_at': observed_at,
        'created_at': created_at, 'updated_at': created_at,
    })


def advance_canonical_provider_reference(db, *, action_id, provider_receipt_id,
                                         execution_owner_column='execution_run_id'):
    """Move the stable canonical binding for one explicit corrected result."""
    from app.action_entity_storage import update_sent_receipt

    if execution_owner_column != 'execution_run_id':
        raise ValueError('invalid provider receipt execution owner column')
    metadata = db.execute(
        'select id,provider_receipt_id from action_receipts where action_id=? '
        'and provider_result_json is null and provider_receipt_id is not null '
        f'and status is null and attempt_number is null and {execution_owner_column} is null '
        'and action_index is null and target is not null and agent_run_id is not null '
        'and created_at is not null '
        'and id not in (select coalesce(action_delivery_receipt_id,-1) from actions where id=?) '
        'and id not in (select coalesce(action_prepared_receipt_id,-1) from actions where id=?)',
        (action_id, action_id, action_id),
    ).fetchall()
    if len(metadata) != 1:
        raise ValueError('canonical provider metadata binding is missing or ambiguous')
    binding = metadata[0]
    if update_sent_receipt(
        db, action_id=action_id, receipt_id=int(binding['id']),
        expected={'provider_receipt_id': binding['provider_receipt_id']},
        changes={'provider_receipt_id': provider_receipt_id},
        execution_owner_column=execution_owner_column,
    ) is None:
        raise ValueError('canonical provider reference changed during correction')
    return int(binding['id'])


def retire_unreferenced_provider_receipt(db, *, action_id, receipt_id,
                                         expected_provider_json):
    """Retire one replaced verification wrapper when no source fact uses it."""
    if not db.in_transaction:
        raise ValueError('provider receipt retirement requires caller-owned transaction')
    row = db.execute(
        'select provider_result_json from action_receipts where id=? and action_id=?',
        (receipt_id, action_id),
    ).fetchone()
    if row is None or row['provider_result_json'] != expected_provider_json:
        raise ValueError('replaced provider receipt changed before retirement')
    referenced = db.execute(
        'select exists(select 1 from action_receipts where provider_receipt_id=?) '
        'or exists(select 1 from actions where record_type=\'action\' and '
        '(action_delivery_receipt_id=? or action_prepared_receipt_id=?))',
        (receipt_id, receipt_id, receipt_id),
    ).fetchone()[0]
    if referenced:
        return False
    cursor = db.execute(
        'delete from action_receipts where id=? and action_id=? and provider_result_json=?',
        (receipt_id, action_id, expected_provider_json),
    )
    return cursor.rowcount == 1


def advance_sent_provider_reference(db, *, external_action_key, provider_receipt_id):
    """Explicit delivery repair adopts a provider body into the Sent fact."""
    from app.action_entity_storage import update_sent_receipt

    action = _reviewed_action_by_key(db, external_action_key)
    if action is None or action['delivery_receipt_id'] is None:
        return False
    fact = db.execute(
        'select provider_receipt_id from action_receipts where id=? and action_id=?',
        (action['delivery_receipt_id'], action['id']),
    ).fetchone()
    if fact is None:
        raise ValueError('Sent receipt reference is missing during explicit repair')
    if update_sent_receipt(
        db, action['id'], receipt_id=action['delivery_receipt_id'],
        expected={'provider_receipt_id': fact['provider_receipt_id']},
        changes={'provider_receipt_id': provider_receipt_id},
        execution_owner_column='execution_run_id',
    ) is None:
        raise ValueError('Sent provider reference changed during explicit repair')
    return True


def reviewed_action_steps(db, plan, *, selection=None):
    """Read the adopted reviewed plan without routing through a source kind."""
    from app.model_action_schema import selected_branch
    data = json.loads(plan['candidate_json'])
    proposal = selected_branch(db, selection['id']).get('plan') if selection else data.get('proposal')
    return proposal.get('actions', []) if isinstance(proposal, dict) else []


def _prepared_message_action_id(db, channel, delivery_key):
    row = db.execute(
        "select id from actions where record_type='action' and action_delivery_channel=? and action_delivery_key=?",
        (channel, delivery_key),
    ).fetchone()
    return None if row is None else int(row['id'])


def prepare_outbound_action(db, *, channel, delivery_key, final_body, feedback_token,
                            postfix_version):
    """Persist the immutable prepared message on its common Action owner."""
    if not db.in_transaction:
        raise ValueError('prepared Action write requires caller-owned transaction')
    from app.action_entity_storage import (
        create_action, get_action,
    )
    from app.action_entity_definitions import ACTION_FIELDS
    action_id = _prepared_message_action_id(db, channel, delivery_key)
    if action_id is not None:
        return get_action(db, action_id)
    action = {name: None for name in ACTION_FIELDS}
    action.update({
        'delivery_channel': channel,
        'delivery_key': delivery_key,
        'prepared_body': final_body,
        'feedback_token': feedback_token,
        'postfix_version': postfix_version,
        'prepared_at': db.execute('select current_timestamp').fetchone()[0],
    })
    return create_action(db, values=action)


def claim_outbound_action(db, *, channel, delivery_key, operation):
    """Commit the possible-effect boundary once for one prepared Action."""
    if not db.in_transaction:
        raise ValueError('prepared Action claim requires caller-owned transaction')
    from app.action_entity_storage import append_action_receipt

    action_id = _prepared_message_action_id(db, channel, delivery_key)
    if action_id is None:
        raise sqlite3.IntegrityError('missing prepared Action')
    if not operation:
        raise ValueError('actual provider operation is required')
    dispatched = db.execute(
        'select operation,target from action_receipts where action_id=? and dispatched_at is not null',
        (action_id,),
    ).fetchone()
    if dispatched is not None:
        if dispatched['operation'] != operation or dispatched['target'] != delivery_key:
            raise ValueError('prepared Action dispatch identity conflicts')
        return storage.ActionWriteResult(None, 0)
    clock = db.execute('select current_timestamp').fetchone()[0]
    receipt_id = append_action_receipt(db, action_id, values={
        'status': None, 'operation': operation, 'target': delivery_key,
        'dispatched_at': clock, 'created_at': clock, 'updated_at': clock,
    })
    return storage.ActionWriteResult(receipt_id, 1)


def record_outbound_action_receipt(db, *, channel, delivery_key, provider_result_json, operation):
    """Append and return the first provider receipt for one prepared Action."""
    if not db.in_transaction:
        raise ValueError('prepared Action receipt requires caller-owned transaction')
    from app.action_entity_storage import append_action_receipt, get_action, record_action_result

    action_id = _prepared_message_action_id(db, channel, delivery_key)
    if action_id is None:
        raise sqlite3.IntegrityError('missing prepared Action')
    if not operation:
        raise ValueError('actual provider operation is required')
    action = get_action(db, action_id)
    existing = (db.execute(
        'select id,provider_result_json,operation,target from action_receipts where action_id=? and id=?',
        (action_id, action['prepared_receipt_id']),
    ).fetchone() if action['prepared_receipt_id'] is not None else None)
    if existing is not None:
        if (existing['provider_result_json'] != provider_result_json
                or existing['operation'] != operation
                or existing['target'] != delivery_key):
            raise ValueError('prepared Action already has a different provider receipt')
        return storage.ActionWriteResult(existing['id'], 0)
    clock = db.execute('select current_timestamp').fetchone()[0]
    receipt_id = append_action_receipt(db, action_id, values={
        'status': None, 'operation': operation, 'target': delivery_key,
        'provider_result_json': provider_result_json, 'observed_at': clock,
        'created_at': clock, 'updated_at': clock,
    })
    if record_action_result(db, action_id,
            expected={'prepared_receipt_id': None},
            changes={'prepared_receipt_id': receipt_id}) is None:
        raise ValueError('prepared Action receipt reference changed during write')
    return storage.ActionWriteResult(receipt_id, 1)


def _reviewed_action_by_key(db, external_action_key):
    from app.action_entity_storage import get_action

    row = db.execute(
        "select id from actions where record_type='action' and action_external_action_key=?",
        (external_action_key,),
    ).fetchone()
    return None if row is None else get_action(db, int(row['id']))


def _reviewed_attempt(db, action_id, execution_id, action_index, *, owner_column='execution_run_id'):
    from app.action_storage import NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL, candidate_action_attempt_row

    if owner_column != 'execution_run_id':
        raise ValueError('invalid reviewed execution owner column')
    projection = NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL
    return candidate_action_attempt_row(_fetch(db,
        'select * from (' + projection + ') '
        'where action_owner_id=? and execution_id=? and action_index=? order by id desc limit 1',
        (action_id, execution_id, action_index)))


def _reviewed_action_receipt(db, action_id, execution_id, action_index, status, *,
                             result_json, provider_receipt_id, agent_run_id,
                             target_json, created_at, updated_at,
                             owner_column='execution_run_id'):
    from app.action_entity_storage import append_action_receipt, update_action_observation

    if owner_column != 'execution_run_id':
        raise ValueError('invalid reviewed execution owner column')
    current = db.execute(
        'select id,status,outcome_json,provider_receipt_id,agent_run_id,target,observed_at,updated_at '
        f'from action_receipts where action_id=? and {owner_column}=? and action_index=?',
        (action_id, execution_id, action_index),
    ).fetchone()
    if current is not None:
        if current['target'] != target_json:
            raise ValueError('reviewed Action target conflicts with its execution binding')
        changes = {'status': status, 'outcome_json': result_json,
                   'agent_run_id': agent_run_id, 'observed_at': updated_at,
                   'updated_at': updated_at}
        if provider_receipt_id is not None:
            changes['provider_receipt_id'] = provider_receipt_id
        receipt_id = update_action_observation(db, action_id,
            **{owner_column: execution_id}, action_index=action_index,
            expected={key: current[key] for key in (
                'status', 'outcome_json', 'provider_receipt_id', 'agent_run_id',
                'observed_at', 'updated_at')}, changes=changes)
        if receipt_id is None:
            raise ValueError('reviewed Action observation compare-and-set failed')
    else:
        receipt_id = append_action_receipt(db, action_id, values={
        'status': status, owner_column: execution_id, 'action_index': action_index,
        'target': target_json,
        'outcome_json': result_json, 'provider_receipt_id': provider_receipt_id,
        'agent_run_id': agent_run_id, 'created_at': created_at, 'updated_at': updated_at,
        })
    from app.action_entity_storage import get_action, record_action_result
    latest = db.execute(
        f'select status from action_receipts where action_id=? and {owner_column} is not null '
        f'and action_index is not null order by updated_at desc,{owner_column} desc,action_index desc,id desc limit 1',
        (action_id,),
    ).fetchone()
    action = get_action(db, action_id)
    if latest is not None and action is not None and action['status'] != latest['status']:
        record_action_result(db, action_id, expected={'status': action['status']},
                             changes={'status': latest['status']})
    return receipt_id


def begin_reviewed_action(db, *, execution_id, action_index, external_action_key,
                          action, candidate, preparation_owner=None,
                          created_at, updated_at, owner_column='execution_run_id'):
    """Claim one reviewed effect and append its per-execution observation."""
    from app.action_entity_storage import claim_action, create_action, get_action
    from app.action_storage import NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL

    if not db.in_transaction:
        raise ValueError('reviewed Action claim requires caller-owned transaction')
    existing = _reviewed_action_by_key(db, external_action_key)
    prepared_id = None
    if preparation_owner is not None:
        if (set(preparation_owner) != {'channel', 'delivery_key'}
                or not preparation_owner['channel'] or not preparation_owner['delivery_key']):
            raise ValueError('invalid prepared Action owner reference')
        prepared_id = _prepared_message_action_id(
            db, preparation_owner['channel'], preparation_owner['delivery_key'],
        )
        if prepared_id is None:
            raise ValueError('prepared Action owner is missing')
        # A later approved revision may prepare a new delivery key for an
        # already completed canonical effect. Keep that unused preparation
        # separate; the established external identity remains the owner.
        if existing is not None and int(existing['id']) != prepared_id:
            prepared_id = None
        if existing is None:
            existing = get_action(db, prepared_id)
            expected_identity = {
                'operation': action['operation'],
                'external_action_key': external_action_key,
                'business_object_key': candidate['business_object_key'],
                'action_identity': action['action_identity'],
            }
            if existing is None or any(
                existing[field] not in (None, value)
                for field, value in expected_identity.items()
            ):
                raise ValueError('prepared Action conflicts with reviewed identity')
    target_json = json.dumps(action.get('target', {}), ensure_ascii=False,
                             sort_keys=True, separators=(',', ':'), allow_nan=False)
    if existing is None:
        existing = create_action(db, values={
            'operation': action['operation'],
            'status': None, 'external_action_key': external_action_key,
            'business_object_key': candidate['business_object_key'],
            'action_identity': action['action_identity'],
        })
    elif preparation_owner is None and (
        existing['business_object_key'] != candidate['business_object_key']
        or existing['action_identity'] != action['action_identity']
        or existing['operation'] != action['operation']
    ):
        raise ValueError('external action key belongs to another action')
    elif preparation_owner is not None and any(
        existing[field] not in (None, value)
        for field, value in {
            'business_object_key': candidate['business_object_key'],
            'action_identity': action['action_identity'],
            'operation': action['operation'],
        }.items()
    ):
        raise ValueError('prepared Action conflicts with reviewed identity')
    current = _reviewed_attempt(db, existing['id'], execution_id, action_index,
                                owner_column=owner_column)
    if current is not None:
        if current['external_action_key'] != external_action_key:
            raise ValueError('action key conflict')
        if current['status'] in ('dispatched', 'confirmed_no_effect'):
            next_status = 'uncertain' if current['status'] == 'dispatched' else 'dispatched'
            parent = get_action(db, existing['id'])
            _reviewed_action_receipt(db, existing['id'], execution_id, action_index,
                next_status, result_json=current['result_json'], provider_receipt_id=None,
                agent_run_id=candidate['consumer_run_id'], target_json=target_json, created_at=created_at,
                updated_at=updated_at, owner_column=owner_column)
            current = _reviewed_attempt(db, existing['id'], execution_id, action_index,
                                        owner_column=owner_column)
        return current

    attempt_projection = NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL
    prior = _fetch(db, 'select * from (' + attempt_projection
        + ') where external_action_key=? order by id desc limit 1', (external_action_key,))
    from app.action_storage import NATIVE_EXTERNAL_ACTION_RESULT_SQL
    external_result_projection = NATIVE_EXTERNAL_ACTION_RESULT_SQL
    provider = db.execute(
        f'select provider_receipt_id,provider_result_json from ({external_result_projection}) '
        'where external_action_key=?', (external_action_key,),
    ).fetchone()
    if provider is not None:
        verification = json.loads(provider['provider_result_json']).get('verification', {})
        status = 'uncertain' if verification.get('state') in ('ambiguous', 'failed') else 'verified'
        _reviewed_action_receipt(db, existing['id'], execution_id, action_index, status,
            result_json=None, provider_receipt_id=provider['provider_receipt_id'],
            agent_run_id=candidate['consumer_run_id'], target_json=target_json, created_at=created_at,
            updated_at=updated_at, owner_column=owner_column)
        return _reviewed_attempt(db, existing['id'], execution_id, action_index,
                                 owner_column=owner_column)
    if prior is not None and prior['status'] != 'confirmed_no_effect':
        raise ValueError('action_key_requires_reconciliation')
    parent = get_action(db, existing['id'])
    changes = {'status': 'dispatched'}
    if preparation_owner is not None:
        changes.update({
            'operation': action['operation'],
            'external_action_key': external_action_key,
            'business_object_key': candidate['business_object_key'],
            'action_identity': action['action_identity'],
        })
    if claim_action(db, existing['id'], expected={'status': parent['status']},
                    changes=changes) is None:
        raise ValueError('action claim changed')
    _reviewed_action_receipt(db, existing['id'], execution_id, action_index,
        'dispatched', result_json='{}', provider_receipt_id=None,
        agent_run_id=candidate['consumer_run_id'], target_json=target_json, created_at=created_at,
        updated_at=updated_at, owner_column=owner_column)
    return _reviewed_attempt(db, existing['id'], execution_id, action_index,
                             owner_column=owner_column)


def record_reviewed_action_outcome(db, *, attempt, status, result_json,
                                   provider_receipt_id, agent_run_id, updated_at,
                                   owner_column='execution_run_id'):
    """Update the current execution observation and common Action phase."""
    if not db.in_transaction:
        raise ValueError('reviewed Action result requires caller-owned transaction')
    action = _reviewed_action_by_key(db, attempt['external_action_key'])
    if action is None:
        raise ValueError('reviewed Action owner is missing')
    if status == 'verified' and provider_receipt_id is None:
        raise ValueError('verified action lacks successful provider receipt')
    _reviewed_action_receipt(db, action['id'], attempt['execution_id'],
        attempt['action_index'], status, result_json=result_json,
        provider_receipt_id=provider_receipt_id, agent_run_id=agent_run_id,
        target_json=attempt['target'], owner_column=owner_column,
        created_at=updated_at, updated_at=updated_at)


def record_reviewed_provider_receipt(db, *, external_action_key, operation,
                                     target_json, provider_json, result_digest,
                                     agent_run_id, observed_at, status='verified',
                                     execution_owner_column='execution_run_id'):
    """Append one canonical provider receipt version to the common Action."""
    from app.action_entity_storage import append_action_receipt, get_action

    if not db.in_transaction:
        raise ValueError('provider receipt requires caller-owned transaction')
    action = _reviewed_action_by_key(db, external_action_key)
    if action is None:
        raise ValueError('provider receipt has no Action owner')
    receipt_id = append_action_receipt(db, action['id'], values={
        'status': status, 'operation': operation, 'target': target_json,
        'provider_result_json': provider_json, 'result_digest': result_digest,
        'agent_run_id': agent_run_id, 'observed_at': observed_at,
        'created_at': observed_at, 'updated_at': observed_at,
    })
    current = get_action(db, action['id'])
    if current is not None and external_action_key:
        _bind_provider_receipt_metadata(
            db, action_id=action['id'], provider_receipt_id=receipt_id,
            operation=operation, target_json=target_json, result_digest=result_digest,
            agent_run_id=agent_run_id, observed_at=observed_at, created_at=observed_at,
            execution_owner_column=execution_owner_column,
        )
    return receipt_id


def adopt_verified_sent_action(db, *, external_action_key, business_object_key,
                               action_identity, operation, target_json,
                               provider_json, result_digest, agent_run_id,
                               observed_at):
    """Attach already-verified Sent evidence to its common Action owner."""
    from app.action_entity_storage import create_action, record_action_result

    if not db.in_transaction:
        raise ValueError('verified Sent adoption requires caller-owned transaction')
    action = _reviewed_action_by_key(db, external_action_key)
    if action is None:
        action = create_action(db, values={
            'operation': operation,
            'external_action_key': external_action_key,
            'business_object_key': business_object_key,
            'action_identity': action_identity,
            'created_at': observed_at, 'updated_at': observed_at,
        })
    elif ((action['business_object_key'] is not None and action['business_object_key'] != business_object_key)
          or (action['action_identity'] is not None and action['action_identity'] != action_identity)
          or (action['operation'] is not None and action['operation'] != operation)):
        raise ValueError('verified Sent identity conflicts with its Action owner')

    from app.action_storage import NATIVE_EXTERNAL_ACTION_RESULT_SQL
    existing = db.execute(
        f'select provider_receipt_id,operation,target_identifiers_json from ({NATIVE_EXTERNAL_ACTION_RESULT_SQL}) '
        'where external_action_key=?', (external_action_key,),
    ).fetchone()
    if existing is not None:
        if (existing['operation'] != operation
                or existing['target_identifiers_json'] != target_json):
            raise ValueError('verified Sent receipt conflicts with its canonical Action identity')
    else:
        body = db.execute(
            'select id,operation,target,result_digest,agent_run_id,observed_at '
            'from action_receipts where action_id=? and provider_result_json=? '
            'order by id limit 1', (action['id'], provider_json),
        ).fetchone()
        if body is None:
            raise ValueError('verified Sent evidence has no immutable provider body owner')
        if ((body['operation'] is not None and body['operation'] != operation)
                or (body['target'] is not None and body['target'] != target_json)):
            raise ValueError('verified Sent body conflicts with its canonical Action identity')
        _bind_provider_receipt_metadata(
            db, action_id=action['id'], provider_receipt_id=int(body['id']),
            operation=operation, target_json=target_json,
            result_digest=result_digest,
            agent_run_id=body['agent_run_id'] or agent_run_id,
            observed_at=body['observed_at'] or observed_at,
            created_at=db.execute('select current_timestamp').fetchone()[0],
        )
    changes = {}
    for field, value in (('operation', operation), ('business_object_key', business_object_key),
                         ('action_identity', action_identity)):
        if action[field] is not None and action[field] != value:
            raise ValueError('verified Sent identity conflicts with its Action owner')
        if action[field] is None:
            changes[field] = value
    if changes and record_action_result(
        db, action['id'], expected={field: action[field] for field in changes},
        changes=changes,
    ) is None:
        raise ValueError('verified Sent Action metadata changed during adoption')
    return action['id']


def transition(db, origin, event, *, identity=None, values=None, expected=None, receipt=None,
               where_sql='', parameters=()):
    """Apply one existing claim/outcome/schedule transition under its caller lock.

    Email identity is its public action_id. The finite source variants here
    retain plan, step and unsubscribe workflow storage; concrete reviewed,
    direct-email and prepared-message effects use named Action owner methods.
    """
    if not db.in_transaction:
        raise ValueError('shared action transition requires caller-owned transaction')
    values, expected = values or {}, expected or {}
    if origin in STEP_KINDS:
        # Specialized callers retain proof validation; allocation, CAS, state
        # and receipt writes are owned here, under the same transaction as the
        # caller's unique effect journal, controls or ordered observations.
        if event == 'claim':
            return _insert(db, STEP_KINDS[origin], values)
        if event == 'outcome':
            return _insert(db, RESULT_KINDS[origin], values)
        if event in ('state', 'amend_outcome'):
            kind = STEP_KINDS[origin] if event == 'state' else RESULT_KINDS[origin]
            return _update(db, kind, identity, values, where_sql=where_sql, parameters=parameters)
        if event == 'retire':
            predicate, arguments = _predicate(STEP_KINDS[origin], identity, where_sql, parameters)
            return db.execute('delete from actions where ' + predicate, arguments)
        raise ValueError('unknown specialized action transition')
    raise ValueError('unknown shared action transition origin')
