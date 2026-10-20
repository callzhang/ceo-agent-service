"""Finite plan, effect-step and attempt variants of the existing source facts.

The stored discriminator is the new typed variant. ``record_type`` is only the
old read-only name projection used by existing parent guards and public SQL.
No business state, counter or timestamp is introduced by the origin mapping.
"""
from app.storage_payload import ACTION_RECORD_TYPES, EXECUTION_ACTION_RECORD_TYPES, PayloadLayout, RecordTypes

SOURCE_TO_TARGET = {
    'review_candidates': 'plan_version_reviewed',
    'email_action_plans': 'plan_version_email',
    'email_actions': 'effect_step_email',
    'candidate_action_attempts': 'action_attempt_reviewed',
    'email_action_attempts': 'action_attempt_email',
}
DIRECT_TERMINAL_FIELDS = (
    'finished_at', 'provider_operation', 'provider_target', 'provider_result_id', 'error',
)
DIRECT_DERIVED_FIELDS = ('attempt_count', *DIRECT_TERMINAL_FIELDS)


class SharedActionTypes:
    names = EXECUTION_ACTION_RECORD_TYPES.names

    def code(self, name):
        return ACTION_RECORD_TYPES.code(SOURCE_TO_TARGET.get(name, name))

    def declarations(self, kinds=None):
        # RecordTypes deliberately projects the passed source names. Only the
        # physical codes change, retaining exact cross-table guard semantics.
        return RecordTypes.declarations(self, kinds)

    def trigger_sql(self, sql):
        return RecordTypes.trigger_sql(self, sql)


SHARED_RECORD_TYPES = SharedActionTypes()


def direct_step_layout():
    from app.action_email_definitions import EMAIL_FIELDS
    return PayloadLayout(tuple(field for field in EMAIL_FIELDS['email_actions']
                               if field not in DIRECT_DERIVED_FIELDS))


def shared_discriminators():
    variants = (
        ('review_candidates', 'plan_versions', 'reviewed'),
        ('email_action_plans', 'plan_versions', 'email'),
        ('email_actions', 'effect_steps', 'email'),
        ('candidate_action_attempts', 'action_attempts', 'reviewed'),
        ('email_action_attempts', 'action_attempts', 'email'),
    )
    return tuple(
        name + ' text generated always as (case _kind ' + ' '.join(
            'when ' + str(SHARED_RECORD_TYPES.code(source)) + ' then ' + repr(values[index])
            for source, *values in variants
        ) + ' end) virtual'
        for index, name in enumerate(('action_family', 'action_origin'))
    )


def email_projection(kind, *, include_rowid=False, source_variant='actual'):
    """The old public tuple, with direct terminal facts read from one attempt."""
    from app.action_email_definitions import EMAIL_FIELDS, email_action_projection
    if kind != 'email_actions':
        return email_action_projection(kind, include_rowid=include_rowid, source_variant=source_variant)
    fields = []
    for field in EMAIL_FIELDS[kind]:
        if field == 'attempt_count':
            expression = ("(select count(*) from actions tally where tally.record_type='email_action_attempts' "
                          "and tally.email_action_id=a.email_action_id)")
        elif field in DIRECT_TERMINAL_FIELDS:
            expression = ("case when a.email_status in ('pending','processing') then '' "
                          'else coalesce(latest.email_' + field + ",'') end")
        elif field == 'started_at':
            expression = ("case when a.email_status='processing' then a.email_started_at "
                          "when a.email_status='pending' then '' else coalesce(latest.email_started_at,'') end")
        else:
            expression = 'a.email_' + field
        fields.append(expression + ' as ' + field)
    if include_rowid:
        fields.insert(0, 'cast(a.legacy_key as integer) as rowid')
    return ('select ' + ','.join(fields) + ' from actions a left join actions latest on latest.id=('
            "select t.id from actions t where t.record_type='email_action_attempts' and t._kind=" + str(SHARED_RECORD_TYPES.code('email_action_attempts'))
            + ' and t.email_action_id=a.email_action_id order by t.email_attempt_number desc limit 1) '
            "where a.record_type='email_actions'")
