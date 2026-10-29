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
SPECIALIZED_SOURCE_TO_TARGET = {
    'email_reply_dispatch_claims': 'effect_step_email_reply',
    'email_unsubscribe_claims': 'effect_step_email_unsubscribe',
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


class SpecializedActionTypes(SharedActionTypes):
    def code(self, name):
        return ACTION_RECORD_TYPES.code(SPECIALIZED_SOURCE_TO_TARGET.get(name, SOURCE_TO_TARGET.get(name, name)))


SPECIALIZED_RECORD_TYPES = SpecializedActionTypes()


def direct_step_layout():
    from app.action_email_definitions import EMAIL_FIELDS
    return PayloadLayout(tuple(field for field in EMAIL_FIELDS['email_actions']
                               if field not in DIRECT_DERIVED_FIELDS))


def shared_discriminators(*, specialized_claims=False, retired_types=()):
    variants = tuple(item for item in (
        ('review_candidates', 'plan_versions', 'reviewed'),
        ('email_action_plans', 'plan_versions', 'email'),
        ('email_actions', 'effect_steps', 'email'),
        ('candidate_action_attempts', 'action_attempts', 'reviewed'),
        ('email_action_attempts', 'action_attempts', 'email'),
    ) if item[0] not in retired_types)
    if specialized_claims:
        variants += (
            ('email_reply_dispatch_claims', 'effect_steps', 'email_reply'),
            ('email_unsubscribe_claims', 'effect_steps', 'email_unsubscribe'),
        )
    record_types = SPECIALIZED_RECORD_TYPES if specialized_claims else SHARED_RECORD_TYPES
    return tuple(
        name + ' text generated always as (case _kind ' + ' '.join(
            'when ' + str(record_types.code(source)) + ' then ' + repr(values[index])
            for source, *values in variants
        ) + ' end) virtual'
        for index, name in enumerate(('action_family', 'action_origin'))
    )


def email_projection(kind, *, include_rowid=False, include_physical_id=False):
    """Read current Email families from their Action rows."""
    from app.current_email_fields import EMAIL_FIELDS
    fields = [f"a.email_{field} as {field}" for field in EMAIL_FIELDS[kind]]
    if include_rowid:
        fields.insert(0, "cast(a.legacy_key as integer) as rowid")
    if include_physical_id:
        fields.insert(0, "a.id as physical_action_id")
    return "select " + ",".join(fields) + f" from actions a where a.record_type='{kind}'"
