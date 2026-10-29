"""Frozen source direct-email projection for historical contract tests."""

from app.shared_action_definitions import SHARED_RECORD_TYPES, DIRECT_TERMINAL_FIELDS

def email_projection(kind, *, include_rowid=False, source_variant='actual', include_physical_id=False):
    """The old public tuple, with direct terminal facts read from one attempt."""
    from app.action_email_definitions import EMAIL_FIELDS, email_action_projection
    if kind != 'email_actions':
        return email_action_projection(kind, include_rowid=include_rowid, source_variant=source_variant, include_physical_id=include_physical_id)
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
    if include_physical_id:
        fields.insert(0, 'a.id as physical_action_id')
    return ('select ' + ','.join(fields) + ' from actions a left join actions latest on latest.id=('
            "select t.id from actions t where t.record_type='email_action_attempts' and t._kind=" + str(SHARED_RECORD_TYPES.code('email_action_attempts'))
            + ' and t.email_action_id=a.email_action_id order by t.email_attempt_number desc limit 1) '
            "where a.record_type='email_actions'")
