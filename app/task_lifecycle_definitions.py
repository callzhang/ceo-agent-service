"""Current Task scalar owners, with the frozen Task20 source ABI retained.

Each lifecycle column contains exactly one source JSONB scalar.  The source
status is the sole stored status; lifecycle_status and meeting_phase are reads.
No affinity conversion or JSON decoding/re-encoding occurs during migration.
"""
from app import task20_definitions as source
from app.action_email_definitions import _encoded_value, _encoded_value_check
from app.storage_payload import PayloadLayout, TASK_RECORD_TYPES
from app.state_definitions import STATE_TYPES

LIFECYCLE_FIELDS = (
    "status", "available_at", "attempts", "error", "created_at", "updated_at",
    "locked_at", "lease_owner", "lease_expires_at", "owner_pid", "generation",
    "adapter_name",
)


def task_lifecycle_physical_field_map():
    """Declare physical lifecycle owners and auxiliary-domain NULL coverage."""
    return tuple(
        ({"source_kind": kind, "source_physical_field": field,
          "owner_scope": "native_task", "target_table": "tasks", "target_physical_field": field}
         if kind in SOURCE_FIELDS else
         {"source_kind": kind, "source_physical_field": field,
          "owner_scope": "auxiliary_domain", "target_table": None, "target_physical_field": None,
          "retirement_reason": "auxiliary source row has no native Task lifecycle owner; source scalar is NULL"})
        for kind in source.TASK20_COLUMNS for field in LIFECYCLE_FIELDS
    )
_BASE = ("status", "error", "created_at", "updated_at")
SOURCE_FIELDS = {
    "reply_tasks": {f: f for f in (*_BASE, "available_at", "attempts", "locked_at")},
    "work_summary_inputs": {f: f for f in (*_BASE, "available_at", "attempts")},
    "meeting_alignment_jobs": {f: f for f in (*_BASE, "available_at", "attempts", "locked_at")},
    "okr_review_requests": {f: f for f in _BASE},
    "weekly_okr_analysis_jobs": {f: f for f in (*_BASE, "lease_owner", "lease_expires_at")},
    "workbench_turns": {f: f for f in ("status", "created_at", "updated_at", "lease_owner", "lease_expires_at")},
    "email_agent_classification_tasks": {
        **{f: f for f in (*_BASE, "generation", "lease_expires_at", "available_at")},
        "owner": "lease_owner", "attempt_count": "attempts",
    },
    "feedback_processing_items": {f: f for f in ("status", "created_at", "updated_at")},
    "wechat_memory_import_jobs": {f: f for f in _BASE},
}
DISPATCH_KINDS = {
    "reply": "reply_tasks",
    "scheduled_execution": "reply_tasks",
    "meeting": "meeting_alignment_jobs",
    "work_summary": "work_summary_inputs",
    "okr_review": "okr_review_requests",
}
DISPATCH_FIELDS = ("lease_owner", "lease_expires_at", "owner_pid", "generation", "adapter_name")


def _source_affinity(field):
    affinities = {column[1] for kind, fields in SOURCE_FIELDS.items()
                  for column in source.TASK20_COLUMNS[kind]
                  if fields.get(column[0]) == field}
    if field in DISPATCH_FIELDS:
        affinities.add(STATE_TYPES["owner" if field == "lease_owner" else field])
    if len(affinities) != 1:
        raise ValueError("Task lifecycle source affinities disagree")
    return affinities.pop()


COMMON_AFFINITIES = {field: _source_affinity(field) for field in LIFECYCLE_FIELDS}
STATUS_MAP = {
    "meeting_alignment_jobs": {
        "waiting": "pending", "retry": "pending", "ready_to_send": "processing",
        "sent": "done", "no_action": "skipped",
    },
    "weekly_okr_analysis_jobs": {"running": "processing", "completed": "done"},
    "workbench_turns": {"queued": "pending", "running": "processing", "completed": "done"},
    "email_agent_classification_tasks": {"running": "processing"},
    "feedback_processing_items": {"resolved": "done"},
    "wechat_memory_import_jobs": {"running": "processing", "completed": "done"},
}

TASK20_COLUMNS = {
    kind: tuple((*c[:4], SOURCE_FIELDS.get(kind, {}).get(c[0], c[4])) for c in columns)
    for kind, columns in source.TASK20_COLUMNS.items()
}
TASK20_PAYLOAD_FIELDS = {
    kind: tuple(c[0] for c in columns if c[4] == "payload")
    for kind, columns in TASK20_COLUMNS.items()
}
TASK20_LAYOUTS = {kind: PayloadLayout(fields) for kind, fields in TASK20_PAYLOAD_FIELDS.items()}
TASK20_FIELDS = source.TASK20_FIELDS
TASK20_DEFAULTS = source.TASK20_DEFAULTS
TASK20_NEW_KINDS = source.TASK20_NEW_KINDS
TASK20_PARENTS = source.TASK20_PARENTS
TASK12_AUTOINCREMENT_KINDS = source.TASK12_AUTOINCREMENT_KINDS
TASK12_PRIMARY_KEYS = source.TASK12_PRIMARY_KEYS
task20_column = source.task20_column
task20_projection = source.task20_projection
task20_projection_barrier = source.task20_projection_barrier
task20_source_projection = source.task20_source_projection


def scalar(field, alias=""):
    """Decode a common physical owner; SQL NULL means no source field/fact."""
    if field not in LIFECYCLE_FIELDS:
        raise ValueError("unknown Task lifecycle field")
    return _encoded_value((alias + "." if alias else "") + '"' + field + '"', "$")


def common_column(field, alias=""):
    if field not in LIFECYCLE_FIELDS:
        raise ValueError("unknown Task lifecycle field")
    return (alias + "." if alias else "") + '"lifecycle_' + field + '"'


def dispatch_source_projection(kind):
    """Source fields plus Task claim reads, scoped by the physical kind index."""
    if kind not in DISPATCH_KINDS.values():
        raise ValueError("not a dispatcher Task kind")
    selection, tail = task20_projection(kind).rsplit(" from tasks ", 1)
    fields = {"status": "task_status", "lease_owner": "claim_owner",
              "owner_pid": "claim_owner_pid", "generation": "claim_generation",
              "lease_expires_at": "claim_expires_at", "adapter_name": "claim_adapter"}
    return (selection + "," + ",".join(common_column(field) + " as " + alias
                                       for field, alias in fields.items())
            + " from tasks indexed by idx_task_lifecycle_status " + tail
            + " and _kind=" + str(TASK_RECORD_TYPES.code(kind)))


def eligible_adapter(alias="tasks"):
    prefix = alias + "." if alias else ""
    channel = prefix + task20_column("reply_tasks", "channel")
    return (
        "case " + prefix + "record_type "
        "when 'reply_tasks' then case when " + channel + " in ('dingtalk','wechat') "
        "then 'reply' when " + channel + "='scheduled' then 'scheduled_execution' end "
        "when 'work_summary_inputs' then 'work_summary' "
        "when 'meeting_alignment_jobs' then 'meeting' "
        "when 'okr_review_requests' then 'okr_review' end"
    )


def _source_check(kind, layout):
    columns = source.TASK20_COLUMNS[kind]
    terms = [layout.shape_sql()]
    terms.extend(_encoded_value_check("payload_json", layout.path(f)) for f in layout.fields)
    terms.extend(task20_column(kind, c[0]) + " is not null" for c in columns if c[2])
    terms.extend(source._checks(kind))
    if kind in source.TASK20_NEW_KINDS:
        terms.append("legacy_key is not null and legacy_key=cast(cast(legacy_key as integer) as text)")
    terms.append("parent_id is not null" if kind in source.TASK20_PARENTS else "parent_id is null")
    if kind != "reply_tasks":
        terms.extend(("current_input_id is null", "claimed_input_id is null"))
    else:
        terms.append("legacy_key is null or legacy_key=cast(id as text)")
    return "check(record_type<>" + repr(kind) + " or (" + " and ".join("(" + t + ")" for t in terms) + "))"


def _definitions():
    table = source.TASK20_TABLE_DDL
    # Exact source declarations are immutable; replace only explicitly mapped
    # generated expressions and each complete kind-specific payload constraint.
    for kind, columns in source.TASK20_COLUMNS.items():
        old_layout, layout = source.TASK20_LAYOUTS[kind], TASK20_LAYOUTS[kind]
        for field, affinity, _, _, storage in columns:
            if storage != "payload":
                continue
            prefix = task20_column(kind, field) + " " + affinity + " generated always as (case when record_type=" + repr(kind) + " then "
            old = prefix + _encoded_value("payload_json", old_layout.path(field)) + " end) virtual"
            value = (scalar(SOURCE_FIELDS[kind][field]) if field in SOURCE_FIELDS.get(kind, {})
                     else _encoded_value("payload_json", layout.path(field)))
            if table.count(old) != 1:
                raise ValueError("frozen Task source field declaration changed")
            table = table.replace(old, prefix + value + " end) virtual", 1)
        old = _source_check(kind, old_layout)
        if table.count(old) != 1:
            raise ValueError("frozen Task source constraint changed")
        table = table.replace(old, _source_check(kind, layout), 1)
    physical = [f'"{f}" blob' for f in LIFECYCLE_FIELDS]
    reads = []
    for field in LIFECYCLE_FIELDS:
        value = scalar(field)
        if field == "status":
            cases = []
            for kind, mapping in STATUS_MAP.items():
                cases.append("when " + repr(kind) + " then case " + value + " "
                             + " ".join("when " + repr(a) + " then " + repr(b) for a, b in mapping.items())
                             + " else " + value + " end")
            value = "case record_type " + " ".join(cases) + " else " + value + " end"
        reads.append('"lifecycle_' + field + '" ' + COMMON_AFFINITIES[field]
                     + ' generated always as (' + value + ") virtual")
    reads.append('"meeting_phase" ' + COMMON_AFFINITIES["status"]
                 + ' generated always as (case when record_type=\'meeting_alignment_jobs\' then '
                 + scalar("status") + " end) virtual")
    physical.extend(reads)
    guards = []
    for field in LIFECYCLE_FIELDS:
        allowed = {kind for kind, fields in SOURCE_FIELDS.items() if field in fields.values()}
        if field in DISPATCH_FIELDS:
            allowed.update(DISPATCH_KINDS.values())
        names = ",".join(map(repr, sorted(allowed)))
        guards.append('check("' + field + '" is null or (record_type in (' + names + ") and typeof(\""
                      + field + "\")='blob' and json_valid(\"" + field + "\",8) and "
                      + _encoded_value_check('"' + field + '"', "$") + "))")
    table = table.replace("payload_json blob not null,", "payload_json blob not null," + ",".join(physical) + ",", 1)
    table = table[:-1] + "," + ",".join(guards) + ")"
    indexes = (*source.TASK20_INDEX_DDL,
        "create index idx_task_lifecycle_dispatch_identity on tasks(lifecycle_adapter_name,legacy_key) "
        "where lifecycle_adapter_name is not null",
        "create index idx_task_lifecycle_status on tasks(_kind,lifecycle_status,id)",
        "create index idx_task_lifecycle_due on tasks(_kind,lifecycle_status,lifecycle_available_at,id)",
        "create index idx_task_lifecycle_expiry on tasks(_kind,lifecycle_status,lifecycle_lease_expires_at,id)",
    )
    return table, indexes, source.TASK20_TRIGGER_DDL


TASK20_TABLE_DDL, TASK20_INDEX_DDL, TASK20_TRIGGER_DDL = _definitions()
