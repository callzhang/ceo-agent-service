"""Frozen source layouts used by migration capture and restoration."""

from app.action_task_delivery_definitions import TASK_DELIVERY_FIELDS, TASK_DELIVERY_KINDS, TASK_DELIVERY_PARENTS
from app.action_feedback_definitions import FEEDBACK_FIELDS, FEEDBACK_KINDS

TASK_DELIVERY_FRESH_FIELDS = dict(TASK_DELIVERY_FIELDS)
TASK_DELIVERY_FRESH_FIELDS["meeting_memory_write_events"] = (
    "id", "meeting_job_id", "execution_generation", "payload_json", "status", "attempts", "available_at", "error", "memory_id", "lease_owner", "lease_expires_at", "started_at", "created_at", "updated_at",
)
FEEDBACK_FRESH_EVENTS = (*FEEDBACK_FIELDS["feedback_events"][:9], "resolved_at", *FEEDBACK_FIELDS["feedback_events"][9:12])

def historical_feedback_projection(kind, *, source_variant, with_rowid=False):
    if kind not in FEEDBACK_KINDS or source_variant not in ("actual", "fresh"):
        raise ValueError("unsupported Feedback projection")
    fields = FEEDBACK_FRESH_EVENTS if kind == "feedback_events" and source_variant == "fresh" else FEEDBACK_FIELDS[kind]
    prefix = "fb_event" if kind == "feedback_events" else "fb_transition"
    select = ["cast(a.legacy_key as integer) as _source_rowid"] if with_rowid else []
    for field in fields:
        select.append(f"cast(a.legacy_key as integer) as {field}" if kind == "feedback_processing_transitions" and field == "id" else
                      f"a.fb_{field} as {field}" if kind == "feedback_iteration_decision_items" else
                      f"a.{prefix}_{field} as {field}")
    return f"select {','.join(select)} from actions a where a.record_type='{kind}'"



def historical_task_delivery_projection(kind, *, source_variant):
    if kind not in TASK_DELIVERY_KINDS or source_variant not in ("actual", "fresh"):
        raise ValueError("unsupported task delivery projection")
    columns = (TASK_DELIVERY_FIELDS if source_variant == "actual" else TASK_DELIVERY_FRESH_FIELDS)[kind]
    parent, target, _ = TASK_DELIVERY_PARENTS[kind]
    identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
    select = ",".join((f"cast(a.legacy_key as integer) as {field}" if field == identity else
                       f"a.{target} as {field}" if field == parent else
                       f"a.td_{field} as {field}") for field in columns)
    return f"select {select} from actions a where a.record_type='{kind}'"
