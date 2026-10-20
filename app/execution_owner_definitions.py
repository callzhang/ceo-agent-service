"""Source-backed plan execution fields and declarative identity bindings."""

from app.storage_payload import ACTION_RECORD_TYPES


PLAN = 'review_candidates'
ALIAS = 'candidate_execution_alias'
EXECUTION_FIELDS = (
    'execution_public_id', 'execution_status', 'execution_lease_owner',
    'execution_lease_expires_at', 'execution_result_json',
    'execution_created_at', 'execution_updated_at',
)
EXECUTION_REFERENCES = {
    'execution_review_id': ('actions', 'candidate_reviews'),
    'execution_selection_id': ('actions', 'candidate_selections'),
}


def execution_schema_parts(*, record_types=ACTION_RECORD_TYPES):
    """A deferred reciprocal FK permits two writes but requires both at commit."""
    plan, alias = (record_types.code(kind) for kind in (PLAN, ALIAS))
    generated = (
        f'execution_binding_candidate_id integer generated always as (case when _kind={plan} and execution_public_id is not null then id when _kind={alias} then candidate_id end) virtual',
        f'execution_binding_public_id integer generated always as (case when _kind={plan} then execution_public_id when _kind={alias} then cast(legacy_key as integer) end) virtual',
        f'execution_peer_kind integer generated always as (case when _kind={plan} and execution_public_id is not null then {alias} when _kind={alias} then {plan} end) virtual',
    )
    absent = ' and '.join(field + ' is null' for field in (*EXECUTION_FIELDS, *EXECUTION_REFERENCES))
    present = ' and '.join(field + ' is not null' for field in (*EXECUTION_FIELDS, 'execution_review_id'))
    checks = (
        f'check(_kind<>{plan} or (({absent}) or ({present})))',
        f"check(_kind<>{plan} or execution_status in ('running','done','skipped','failed','retry','uncertain'))",
        'foreign key(execution_peer_kind,execution_binding_candidate_id,execution_binding_public_id) references actions(_kind,execution_binding_candidate_id,execution_binding_public_id) deferrable initially deferred',
        'foreign key(execution_review_id,id) references actions(id,candidate_id) deferrable initially deferred',
        'foreign key(execution_selection_id,id) references actions(id,candidate_id) deferrable initially deferred',
        'foreign key(execution_selection_id,execution_review_id) references actions(id,review_id) deferrable initially deferred',
    )
    indexes = (
        'create unique index idx_actions_execution_binding on actions(_kind,execution_binding_candidate_id,execution_binding_public_id)',
        f'create unique index idx_actions_execution_alias_candidate on actions(candidate_id) where _kind={alias}',
        'create unique index idx_actions_parent_candidate on actions(id,candidate_id)',
        'create unique index idx_actions_parent_review on actions(id,review_id)',
    )
    triggers = (
        f"create trigger trg_actions_execution_alias_immutable before update on actions when old._kind={alias} and (new.id is not old.id or new._kind is not old._kind or new.legacy_key is not old.legacy_key or new.candidate_id is not old.candidate_id or new.payload_json is not old.payload_json) begin select raise(abort,'execution alias is immutable'); end",
        f"create trigger trg_actions_execution_alias_delete before delete on actions when old._kind={alias} begin select raise(abort,'execution alias is immutable'); end",
    )
    return generated, checks, indexes, triggers


def execution_projection():
    """Return the old public execution identity and fields from its plan owner."""
    fields = ['cast(a.legacy_key as integer) as id',
              'cast(p.legacy_key as integer) as candidate_id',
              'cast(r.legacy_key as integer) as review_id',
              'cast(s.legacy_key as integer) as selection_id']
    fields.extend('p.execution_' + name + ' as ' + name for name in (
        'status', 'lease_owner', 'lease_expires_at', 'result_json', 'created_at', 'updated_at'))
    return ('select ' + ','.join(fields) + ' from actions a '
            "join actions p on p.id=a.candidate_id and p.record_type='review_candidates' "
            "join actions r on r.id=p.execution_review_id and r.record_type='candidate_reviews' "
            "left join actions s on s.id=p.execution_selection_id and s.record_type='candidate_selections' "
            "where a.record_type='candidate_execution_alias'")
