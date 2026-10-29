"""Unselected source-shaped Feedback columns, constraints and projections."""

from app.action_email_definitions import _encoded_value, _encoded_value_check


FEEDBACK_FIELDS = {
    "feedback_events": ("key", "feedback_token", "rating", "rating_label", "comment",
                        "original_text", "reply_text", "source", "received_at",
                        "raw_json", "created_at", "updated_at", "resolved_at"),
    "feedback_processing_transitions": ("id", "feedback_key", "round_id", "batch_id",
                                        "from_status", "to_status", "reason",
                                        "workbench_task_id", "workbench_turn_id", "created_at"),
    "feedback_iteration_decision_items": ("decision_id", "feedback_key", "round_id"),
}
FEEDBACK_KINDS = tuple(FEEDBACK_FIELDS)
FEEDBACK_PARENTS = {
    "decision_id": ("feedback_iteration_decisions__id", "feedback_iteration_decisions"),
    "feedback_key": ("feedback_processing_items__feedback_key", "feedback_processing_items"),
    "round_id": ("feedback_processing_rounds__id", "feedback_processing_rounds"),
}


def feedback_schema_parts(unused_references, *, payloads=None):
    generated = []
    checks = []
    for kind in FEEDBACK_KINDS[:2]:
        fields = FEEDBACK_FIELDS[kind]
        payload_fields = fields if kind == "feedback_events" else fields[1:]
        for field in payload_fields:
            column = f"fb_{'event' if kind == 'feedback_events' else 'transition'}_{field}"
            affinity = "integer" if field == "round_id" else "text"
            generated.append(f"{column} {affinity} generated always as "
                             f"(case when record_type='{kind}' then "
                             + (payloads.value(kind, field) if payloads else _encoded_value("payload_json", "$." + field)) + " end) virtual")
        required = ("feedback_token", "rating", "rating_label", "comment", "original_text",
                    "reply_text", "source", "received_at", "raw_json", "created_at",
                    "updated_at", "resolved_at") if kind == "feedback_events" else payload_fields
        extra = ("fb_transition_from_status in ('', 'pending', 'processing', 'resolved')",
                 "fb_transition_to_status in ('pending', 'processing', 'resolved')") if kind == "feedback_processing_transitions" else ()
        prefix = "fb_event" if kind == "feedback_events" else "fb_transition"
        checks.append(f"check(record_type<>'{kind}' or (" + " and ".join((
            *(f"{field} is null" for field in unused_references),
            "fb_decision_id is null", "fb_feedback_key is null", "fb_round_id is null",
            *(payloads.check(kind, field) if payloads else _encoded_value_check("payload_json", "$." + field) for field in payload_fields),
            *(f"{prefix}_{field} is not null" for field in required),
            *extra,
            payloads.shape(kind) if payloads else f"json_remove(payload_json,{','.join(repr('$.' + field) for field in payload_fields)})='{{}}'",
        )) + "))")
    generated.extend((
        "fb_decision_id integer references tasks(\"feedback_iteration_decisions__id\")",
        "fb_feedback_key text references tasks(\"feedback_processing_items__feedback_key\")",
        "fb_round_id integer references tasks(\"feedback_processing_rounds__id\")",
    ))
    checks.append("check(record_type<>'feedback_iteration_decision_items' or ("
                  "fb_decision_id is not null and fb_feedback_key is not null and "
                  "fb_round_id is not null and " + (payloads.shape("feedback_iteration_decision_items") if payloads else "payload_json='{}'") + " and "
                  + " and ".join(f"{field} is null" for field in unused_references) + "))")
    checks.append("check(record_type in ('feedback_events','feedback_processing_transitions',"
                  "'feedback_iteration_decision_items') or (fb_decision_id is null and "
                  "fb_feedback_key is null and fb_round_id is null))")
    indexes = (
        "create unique index idx_actions_fb_event_key on actions(fb_event_key) where record_type='feedback_events' and fb_event_key is not null",
        "create index idx_actions_fb_event_token on actions(fb_event_feedback_token,fb_event_received_at,cast(legacy_key as integer)) where record_type='feedback_events'",
        "create index idx_actions_fb_transition_batch_round on actions(fb_transition_batch_id,fb_transition_round_id,cast(legacy_key as integer)) where record_type='feedback_processing_transitions'",
        "create index idx_actions_fb_transition_feedback on actions(fb_transition_feedback_key,cast(legacy_key as integer) desc) where record_type='feedback_processing_transitions'",
        "create index idx_actions_fb_transition_round on actions(fb_transition_round_id,cast(legacy_key as integer)) where record_type='feedback_processing_transitions'",
        "create unique index idx_actions_fb_decision_item on actions(fb_decision_id,fb_feedback_key) where record_type='feedback_iteration_decision_items'",
        "create index idx_actions_fb_decision_feedback_round on actions(fb_feedback_key,fb_round_id,fb_decision_id) where record_type='feedback_iteration_decision_items'",
    )
    triggers = [
        "create trigger trg_actions_fb_transition_sequence before insert on actions when new.record_type='feedback_processing_transitions' begin "
        "insert into sqlite_sequence(name,seq) select 'actions:feedback_processing_transitions',0 where not exists(select 1 from sqlite_sequence where name='actions:feedback_processing_transitions'); "
        "update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) where name='actions:feedback_processing_transitions'; end",
        "create trigger trg_actions_fb_decision_immutable_delete before delete on actions when old.record_type='feedback_iteration_decision_items' begin select raise(abort,'feedback iteration decision items are append-only'); end",
        "create trigger trg_actions_fb_decision_immutable_update before update on actions when old.record_type='feedback_iteration_decision_items' begin select raise(abort,'feedback iteration decision items are append-only'); end",
    ]
    for field, (endpoint, kind) in FEEDBACK_PARENTS.items():
        column = "fb_" + field
        for event, suffix in (("insert", "insert"), (f"update of {column}", "update")):
            triggers.append(
                f"create trigger trg_actions_fb_{field}_parent_{suffix} before {event} on actions "
                f"when new.record_type='feedback_iteration_decision_items' and "
                f"exists(select 1 from tasks where \"{endpoint}\"=new.{column} and record_type<>'{kind}') "
                "begin select raise(abort,'feedback parent type mismatch'); end"
            )
    return generated, checks, indexes, tuple(triggers)


def feedback_projection(kind, *, with_rowid=False):
    if kind not in FEEDBACK_KINDS:
        raise ValueError("unsupported Feedback projection")
    fields = FEEDBACK_FIELDS[kind]
    prefix = "fb_event" if kind == "feedback_events" else "fb_transition"
    select = ["cast(a.legacy_key as integer) as _source_rowid"] if with_rowid else []
    for field in fields:
        select.append(f"cast(a.legacy_key as integer) as {field}" if kind == "feedback_processing_transitions" and field == "id" else
                      f"a.fb_{field} as {field}" if kind == "feedback_iteration_decision_items" else
                      f"a.{prefix}_{field} as {field}")
    return f"select {','.join(select)} from actions a where a.record_type='{kind}'"
