"""Finite, opt-in Actions projection for the native errors table."""

from app.action_email_definitions import EMAIL_NUMERIC_SCOPE, _encoded_value, _encoded_value_check


ERROR_FIELDS = ("id", "conversation_id", "message_id", "kind", "detail",
                "created_at", "resolved_at", "resolution")
ERROR_PAYLOAD_FIELDS = ("conversation_id", "message_id", "kind", "detail", "created_at",
                        "resolved_at", "resolution")


def error_schema_parts(unused_references, *, payloads=None):
    generated = [
        f"error_{field} text generated always as (" + (payloads.scoped_value("errors", field) if payloads else _encoded_value("payload_json", "$." + field)) + ") virtual"
        for field in ERROR_PAYLOAD_FIELDS
    ]
    paths = ",".join(repr("$." + field) for field in ERROR_PAYLOAD_FIELDS)
    checks = [
        "check(record_type<>'errors' or ("
        + (payloads.shape("errors") + " and " if payloads else f"json_remove(payload_json,{paths})='{{}}' and ")
        + " and ".join(payloads.check("errors", field) if payloads else _encoded_value_check("payload_json", "$." + field)
                       for field in ERROR_PAYLOAD_FIELDS)
        + " and " + " and ".join((
            *(f"{field} is null" for field in unused_references),
            *(f"error_{field} is not null" for field in
              ("kind", "detail", "created_at", "resolved_at", "resolution")),
        ))
        + "))",
    ]
    indexes = (
        "create index idx_actions_errors_reply_task_settlement on actions(error_conversation_id,error_message_id,error_kind) where record_type='errors'",
        "create index idx_actions_errors_unresolved on actions(error_conversation_id,error_kind,error_message_id,cast(legacy_key as integer)) where record_type='errors' and coalesce(error_resolved_at,'')=''",
    )
    triggers = (
        "create trigger trg_actions_errors_sequence before insert on actions when new.record_type='errors' begin "
        "insert into sqlite_sequence(name,seq) select 'actions:errors',0 where not exists(select 1 from sqlite_sequence where name='actions:errors'); "
        "update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) where name='actions:errors'; end",
    )
    return generated, checks, indexes, triggers


def error_projection(*, unresolved=False):
    columns = ("cast(a.legacy_key as integer) as id",) + tuple(
        "a.created_at as created_at" if field == "created_at" else
        f"a.error_{field} as {field}" for field in ERROR_FIELDS[1:]
    )
    source = "from actions a where a.record_type='errors' and " + EMAIL_NUMERIC_SCOPE
    if unresolved:
        source = (
            "from actions a indexed by idx_actions_errors_unresolved "
            "where a.record_type='errors' and coalesce(a.error_resolved_at,'')=''"
        )
    return "select " + ",".join(columns) + " " + source
