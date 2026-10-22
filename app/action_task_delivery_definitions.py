"""Finite opt-in Actions declarations for four task delivery families."""

from app.action_email_definitions import _encoded_value, _encoded_value_check

TASK_DELIVERY_FIELDS = {
    "meeting_alignment_delivery_claims": ("job_id", "claim_token"),
    "meeting_memory_write_events": ("id", "meeting_job_id", "payload_json", "status", "attempts", "available_at", "error", "memory_id", "created_at", "updated_at", "execution_generation", "lease_owner", "lease_expires_at", "started_at"),
    "task_memory_write_events": ("id", "reply_task_id", "execution_generation", "consumer_run_id", "memories_json", "written_memory_ids_json", "status", "attempts", "available_at", "error", "skip_reason", "lease_owner", "lease_expires_at", "started_at", "created_at", "updated_at"),
    "wechat_deliveries": ("id", "reply_task_id", "account_id", "target_type", "target_id", "conversation_id", "reply_text", "execution_generation", "status", "action_started_at", "pre_action_failure", "evidence_json", "error", "created_at", "updated_at"),
}
TASK_DELIVERY_FRESH_FIELDS = dict(TASK_DELIVERY_FIELDS)
TASK_DELIVERY_FRESH_FIELDS["meeting_memory_write_events"] = (
    "id", "meeting_job_id", "execution_generation", "payload_json", "status", "attempts", "available_at", "error", "memory_id", "lease_owner", "lease_expires_at", "started_at", "created_at", "updated_at",
)
TASK_DELIVERY_KINDS = tuple(TASK_DELIVERY_FIELDS)
TASK_DELIVERY_AUTOINCREMENT = TASK_DELIVERY_KINDS[1:]
TASK_DELIVERY_PARENTS = {
    "meeting_alignment_delivery_claims": ("job_id", "td_claim_job_id", "meeting_alignment_jobs"),
    "meeting_memory_write_events": ("meeting_job_id", "td_meeting_job_id", "meeting_alignment_jobs"),
    "task_memory_write_events": ("reply_task_id", "td_reply_task_id", "reply_tasks"),
    "wechat_deliveries": ("reply_task_id", "td_reply_task_id", "reply_tasks"),
}
TASK_DELIVERY_TYPES = {
    "claim_token": "text", "payload_json": "text", "status": "text", "attempts": "integer",
    "available_at": "text", "error": "text", "memory_id": "text", "created_at": "text",
    "updated_at": "text", "execution_generation": "text", "lease_owner": "text",
    "lease_expires_at": "text", "started_at": "text", "consumer_run_id": "integer",
    "memories_json": "text", "written_memory_ids_json": "text", "skip_reason": "text",
    "account_id": "text", "target_type": "text", "target_id": "text",
    "conversation_id": "text", "reply_text": "text", "action_started_at": "text",
    "pre_action_failure": "integer", "evidence_json": "text",
}
TASK_DELIVERY_DEFAULTS = {
    "meeting_alignment_delivery_claims": {},
    "meeting_memory_write_events": {"status": "'pending'", "attempts": "0", "available_at": "''", "error": "''", "memory_id": "''", "created_at": "current_timestamp", "updated_at": "current_timestamp", "execution_generation": "''", "lease_owner": "''", "lease_expires_at": "''", "started_at": "''"},
    "task_memory_write_events": {"memories_json": "'[]'", "written_memory_ids_json": "'[]'", "status": "'pending'", "attempts": "0", "available_at": "''", "error": "''", "skip_reason": "''", "lease_owner": "''", "lease_expires_at": "''", "started_at": "''", "created_at": "current_timestamp", "updated_at": "current_timestamp"},
    "wechat_deliveries": {"conversation_id": "''", "execution_generation": "'initial'", "status": "'ready_to_send'", "action_started_at": "''", "pre_action_failure": "0", "evidence_json": "'{}'", "error": "''", "created_at": "current_timestamp", "updated_at": "current_timestamp"},
}
TASK_DELIVERY_DEFAULTS_FRESH = {
    **TASK_DELIVERY_DEFAULTS,
    "meeting_memory_write_events": {key: value for key, value in TASK_DELIVERY_DEFAULTS["meeting_memory_write_events"].items() if key != "execution_generation"},
}


def task_delivery_schema_parts(existing_references, reply_variant, *, payloads=None):
    """Compose target columns and source constraints into the one Actions table."""
    unused_references = tuple(existing_references) + (
        ("memory_attempt_ref",) if reply_variant == "fresh_memory_fk" else ()
    )
    fields = tuple(TASK_DELIVERY_TYPES)
    generated = [
        f"td_{field} {TASK_DELIVERY_TYPES[field]} generated always as "
        f"(case when record_type in ({','.join(repr(kind) for kind, columns in TASK_DELIVERY_FIELDS.items() if field in columns)}) "
        + "then " + (payloads.field_value(field, TASK_DELIVERY_KINDS) if payloads else _encoded_value("payload_json", "$." + field)) + " end) virtual"
        for field in fields
    ]
    generated.extend((
        "td_claim_job_id integer references tasks(id) on delete cascade",
        "td_meeting_job_id integer references tasks(id)",
        "td_reply_task_id integer references tasks(id)",
    ))
    checks = []
    for kind, columns in TASK_DELIVERY_FIELDS.items():
        parent, parent_column, _ = TASK_DELIVERY_PARENTS[kind]
        identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
        payload_fields = [field for field in columns if field not in (parent, identity)]
        checks.append(
            f"check(record_type<>'{kind}' or ("
            + " and ".join((
                f"{parent_column} is not null",
                *(("td_claim_job_id=cast(legacy_key as integer)",) if kind == "meeting_alignment_delivery_claims" else ()),
                *(f"{field} is null" for field in ("td_claim_job_id", "td_meeting_job_id", "td_reply_task_id") if field != parent_column),
                *(f"{field} is null" for field in unused_references),
                *(payloads.check(kind, field) if payloads else _encoded_value_check("payload_json", "$." + field) for field in payload_fields),
                *(f"td_{field} is not null" for field in payload_fields if field != "consumer_run_id"),
                payloads.shape(kind) if payloads else f"json_remove(payload_json,{','.join(repr('$.' + field) for field in payload_fields)})='{{}}'",
            )) + "))"
        )
    scope = ",".join(repr(k) for k in TASK_DELIVERY_KINDS)
    checks.append("check(record_type in (" + scope + ") or (td_claim_job_id is null and td_meeting_job_id is null and td_reply_task_id is null))")
    indexes = [
        "create unique index idx_actions_td_meeting_memory_unique on actions(td_meeting_job_id) where record_type='meeting_memory_write_events'",
        "create unique index idx_actions_td_task_memory_unique on actions(td_reply_task_id,td_execution_generation) where record_type='task_memory_write_events'",
        "create index idx_actions_td_meeting_due on actions(td_status,td_available_at,cast(legacy_key as integer)) where record_type='meeting_memory_write_events'",
        "create index idx_actions_td_meeting_lease on actions(td_status,td_lease_expires_at,cast(legacy_key as integer)) where record_type='meeting_memory_write_events'",
        "create index idx_actions_td_task_due on actions(td_status,td_available_at,cast(legacy_key as integer)) where record_type='task_memory_write_events'",
        "create index idx_actions_td_task_lease on actions(td_status,td_lease_expires_at,cast(legacy_key as integer)) where record_type='task_memory_write_events'",
        "create index idx_actions_td_wechat_status on actions(td_status,cast(legacy_key as integer)) where record_type='wechat_deliveries'",
        "create index idx_actions_td_wechat_task_generation on actions(td_reply_task_id,td_execution_generation,cast(legacy_key as integer)) where record_type='wechat_deliveries'",
    ]
    if reply_variant == "actual_no_memory_fk":
        indexes.append("create unique index idx_actions_td_wechat_actual_unique on actions(td_reply_task_id,td_execution_generation) where record_type='wechat_deliveries'")
    triggers = []
    for kind, (parent, target, expected) in TASK_DELIVERY_PARENTS.items():
        message = ("task parent must be a meeting job" if expected == "meeting_alignment_jobs"
                   else "task parent must be a reply task")
        for event, suffix in (("insert", "insert"), (f"update of {target}", "update")):
            triggers.append(
                f"create trigger trg_actions_td_{kind}_parent_{suffix} before {event} on actions "
                f"when new.record_type='{kind}' and exists(select 1 from tasks where id=new.{target} and record_type<>'{expected}') "
                f"begin select raise(abort,'{message}'); end"
            )
    return generated, checks, indexes, triggers


def task_delivery_projection(kind, *, source_variant="actual"):
    if kind not in TASK_DELIVERY_KINDS or source_variant not in ("actual", "fresh"):
        raise ValueError("unsupported task delivery projection")
    columns = (TASK_DELIVERY_FIELDS if source_variant == "actual" else TASK_DELIVERY_FRESH_FIELDS)[kind]
    parent, target, _ = TASK_DELIVERY_PARENTS[kind]
    identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
    select = ",".join((f"cast(a.legacy_key as integer) as {field}" if field == identity else
                       f"a.{target} as {field}" if field == parent else
                       f"a.td_{field} as {field}") for field in columns)
    return f"select {select} from actions a where a.record_type='{kind}'"
