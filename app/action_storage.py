"""Finite typed storage for candidate, review, execution and receipt facts.

Public IDs keep each source table's integer, text or composite namespace.
Historical Postfix factories retain their composite child foreign key; current
prepared Actions own those facts. Migration callers own an FK-off transaction;
inverse reads current rows, never a business snapshot.
"""

from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import sqlite3
from string import ascii_lowercase, ascii_uppercase

from app.storage_payload import ACTION_RECORD_TYPES, SOURCE_ACTION_RECORD_TYPES, EXECUTION_ACTION_RECORD_TYPES, PayloadLayout
from app.action_payload import ActionPayloads
from app.current_schema_metadata import PROJECTION_PAYLOAD_FIELDS
from app.execution_owner_definitions import (
    ALIAS as EXECUTION_ALIAS, EXECUTION_FIELDS, EXECUTION_REFERENCES,
    execution_schema_parts, execution_projection,
)

from app.action_skill_config_definitions import SKILL_RECEIPT_KINDS, skill_receipt_schema_parts
from app.action_followup_sync_definitions import (
    SYNC_DEFAULTS, SYNC_FIELDS, SYNC_TYPES_ORDER, sync_action_projection,
    sync_schema_parts,
)
from app.business_legacy_fields import CURRENT_SYNC_PROJECTIONS, CURRENT_SYNC_FIELDS

from app.action_task_delivery_definitions import (
    TASK_DELIVERY_FIELDS, TASK_DELIVERY_KINDS, TASK_DELIVERY_AUTOINCREMENT,
    TASK_DELIVERY_PARENTS, TASK_DELIVERY_DEFAULTS,
    task_delivery_projection, task_delivery_schema_parts,  # noqa: F401 -- public projection used by callers
)
from app.action_error_definitions import ERROR_PAYLOAD_FIELDS, error_schema_parts
from app.action_feedback_definitions import FEEDBACK_KINDS, feedback_schema_parts
from app.action_business_definitions import BUSINESS_ACTION_KINDS, business_action_schema_parts
from app.action_email_history_definitions import HISTORY_KINDS, history_schema_parts

from app.action_email_definitions import (
    EMAIL_ACTION_PROJECTIONS as EMAIL_ACTION_PROJECTIONS,
    EMAIL_AUTOINCREMENT_TYPES, EMAIL_COMPOSITE_KEYS, EMAIL_DEFAULTS,
    EMAIL_NUMERIC_SCOPE as _EMAIL_NUMERIC_SCOPE,
    POSTFIX_ACTION_TYPES,
    EMAIL_FIELDS as SOURCE_EMAIL_FIELDS, EMAIL_ROWID_TYPES, EMAIL_TYPES_ORDER,
    _encoded_value, _encoded_value_check, email_schema_parts,
)

from app.current_email_fields import EMAIL_FIELDS
from app.shared_action_definitions import email_projection as email_action_projection  # noqa: F401

SYNC_ACTION_PROJECTIONS = CURRENT_SYNC_PROJECTIONS

_SCHEMA_NAME_CASE = str.maketrans(ascii_uppercase, ascii_lowercase)


def sqlite_schema_name(name: str) -> str:
    """Match SQLite identifier NOCASE rules without folding Unicode or data."""
    return name.translate(_SCHEMA_NAME_CASE)


# Frozen from the 2026-10-13 source inventory, including the actual spelling of
# each declaration. This is the supported source and paired-inverse contract.
SOURCE_ACTION_DDL = """
create table review_candidates (
 id integer primary key, task_id integer not null references tasks(id),
 execution_generation text not null, consumer_run_id integer not null unique references runs(id),
 stage_index integer not null, proposal_revision integer not null,
 predecessor_review_id integer references candidate_reviews(id),
 candidate_digest text not null, candidate_json text not null,
 invalidated_at text not null default '', invalidation_reason text not null default '',
 created_at text not null default current_timestamp
);
create index idx_review_candidates_task on review_candidates(task_id,execution_generation,id);
create table candidate_reviews (
 id integer primary key, candidate_id integer not null references review_candidates(id),
 audit_run_id integer not null unique references runs(id),
 decision text not null check(decision in ('approve','return','reject')),
 candidate_digest text not null, result_json text not null,
 created_at text not null default current_timestamp
);
create index idx_candidate_reviews_candidate on candidate_reviews(candidate_id,id);
create table candidate_selections (
 id integer primary key, candidate_id integer not null unique references review_candidates(id),
 review_id integer not null references candidate_reviews(id), option_key text not null,
 branch_json text not null, status text not null default 'selected',
 created_at text not null default current_timestamp
);
create table candidate_supplements (
 id integer primary key, candidate_id integer not null references review_candidates(id),
 instruction text not null, created_at text not null default current_timestamp
);
create table candidate_executions (
 id integer primary key, candidate_id integer not null unique references review_candidates(id),
 review_id integer not null references candidate_reviews(id), selection_id integer references candidate_selections(id),
 status text not null check(status in ('running','done','skipped','failed','retry','uncertain')),
 lease_owner text not null default '', lease_expires_at text not null default '',
 result_json text not null default '{}', created_at text not null default current_timestamp,
 updated_at text not null default current_timestamp
);
create table candidate_action_attempts (
 id integer primary key, execution_id integer not null references candidate_executions(id),
 action_index integer not null, external_action_key text not null,
 status text not null check(status in ('dispatched','verified','confirmed_no_effect','uncertain','failed')),
 result_json text not null default '{}', created_at text not null default current_timestamp,
 updated_at text not null default current_timestamp,
 unique(execution_id,action_index)
);
create index idx_candidate_action_key on candidate_action_attempts(external_action_key);
"""

SOURCE_COLUMNS = {
    "review_candidates": ("id", "task_id", "execution_generation", "consumer_run_id", "stage_index", "proposal_revision", "predecessor_review_id", "candidate_digest", "candidate_json", "invalidated_at", "invalidation_reason", "created_at"),
    "candidate_reviews": ("id", "candidate_id", "audit_run_id", "decision", "candidate_digest", "result_json", "created_at"),
    "candidate_selections": ("id", "candidate_id", "review_id", "option_key", "branch_json", "status", "created_at"),
    "candidate_supplements": ("id", "candidate_id", "instruction", "created_at"),
    "candidate_executions": ("id", "candidate_id", "review_id", "selection_id", "status", "lease_owner", "lease_expires_at", "result_json", "created_at", "updated_at"),
    "candidate_action_attempts": ("id", "execution_id", "action_index", "external_action_key", "status", "result_json", "created_at", "updated_at"),
}
ACTION_TYPES = tuple(SOURCE_COLUMNS)
CANDIDATE_ACTION_TYPES = ACTION_TYPES
SOURCE_COLUMNS.update({
    "external_action_results": ("external_action_key", "business_object_key", "action_identity", "operation", "target_identifiers_json", "provider_result_json", "result_digest", "first_agent_run_id", "created_at"),
    "agent_effect_intents": ("id", "agent_run_id", "authorization_id", "action_index", "receipt_operation_id", "capability", "operation", "operation_digest", "arguments_digest", "target_identifiers_json", "state", "result_digest", "exit_code", "prepared_at", "dispatched_at", "acknowledged_at", "updated_at", "external_action_key"),
    "agent_execution_receipts": ("id", "agent_run_id", "receipt_id", "operation_id", "cli", "command_path", "command_digest", "exit_code", "completed", "persisted", "safe_to_confirm", "created_at", "effect_counted"),
})
RECEIPT_ACTION_TYPES = tuple(kind for kind in SOURCE_COLUMNS if kind not in ACTION_TYPES)
ALL_ACTION_TYPES = tuple(SOURCE_COLUMNS)
IDENTITY_FIELDS = {kind: "external_action_key" if kind == "external_action_results" else "id" for kind in ALL_ACTION_TYPES}
SOURCE_COLUMNS.update({
    "outbound_postfixes": ("channel", "delivery_key", "final_body", "feedback_token", "postfix_version", "created_at"),
    "outbound_postfix_receipts": ("channel", "delivery_key", "provider_result_json", "created_at"),
    "native_reply_dispatches": ("channel", "delivery_key", "created_at"),
})
_POSTFIX_TYPES_SQL = ",".join(repr(kind) for kind in POSTFIX_ACTION_TYPES)
ALL_ACTION_TYPES_WITH_POSTFIX = ALL_ACTION_TYPES + POSTFIX_ACTION_TYPES
POSTFIX_IDENTITY_FIELDS = ("channel", "delivery_key")
DELIVERY_ACTION_TYPES = ("sent_replies", "sent_reply_observers")
SOURCE_COLUMNS.update({
    "sent_replies": ("id", "conversation_id", "trigger_message_id", "reply_text", "sent_at", "send_result_json", "recall_key", "recall_status", "recall_error", "recalled_at", "feedback_token", "channel", "agent_run_id", "external_action_key"),
    "sent_reply_observers": ("sent_reply_id", "agent_run_id", "reply_task_id", "created_at"),
})
ALL_ACTION_TYPES_WITH_DELIVERY = ALL_ACTION_TYPES_WITH_POSTFIX + DELIVERY_ACTION_TYPES
REPLY_ACTION_TYPES = ("reply_attempts", "memory_write_events")
ALL_ACTION_TYPES_WITH_REPLY = ALL_ACTION_TYPES_WITH_DELIVERY + REPLY_ACTION_TYPES
REPLY_VARIANTS = ("actual_no_memory_fk", "fresh_memory_fk")
# This metadata is confined to the opt-in E declaration. SQLite applies the
# source affinities to generated fields over the finite type payload.
_ATTEMPT_TEXT = """conversation_id conversation_title trigger_message_id trigger_sender trigger_text action sensitivity_kind codex_reason draft_reply_text final_reply_text permission_action permission_reason send_status send_error reviewed_at reviewer_feedback corrected_reply_text created_at updated_at audit_documents_json audit_tool_events_json audit_summary codex_session_id direct_user_id direct_open_dingtalk_id oa_process_instance_id oa_task_id oa_url oa_action oa_remark oa_action_result_json trigger_create_time calendar_event_id calendar_response_status calendar_response_result_json mail_mailbox mail_message_id mail_subject mail_reply_text mail_action_result_json channel universal_execution_id universal_execution_scope_id reaction_action_result_json document_action_result_json human_decision_options_json feedback_scope skill_update_receipts_json resolved_at resolution trigger_text_provenance_json""".split()
_ATTEMPT_INTEGER = "retry_count codex_transcript_start_line codex_transcript_end_line conversation_single_chat agent_run_id skill_update_requested".split()
_ATTEMPT_HISTORICAL = {"conversation_single_chat", "trigger_create_time", "universal_execution_id", "universal_execution_scope_id"}
_ATTEMPT_NULLABLE = {"reviewed_at", "agent_run_id", "conversation_single_chat"}
_ATTEMPT_REQUIRED = {"conversation_id", "conversation_title", "trigger_message_id", "trigger_sender", "trigger_text", "action", "sensitivity_kind", "send_status"}
_ATTEMPT_DEFAULTS = {field: "" for field in _ATTEMPT_TEXT if field not in _ATTEMPT_REQUIRED | _ATTEMPT_NULLABLE | {"created_at", "updated_at", "audit_tool_events_json"}}
_ATTEMPT_DEFAULTS.update({"audit_documents_json": "[]", "human_decision_options_json": "[]", "skill_update_receipts_json": "[]", "trigger_text_provenance_json": "{}", "channel": "dingtalk", "feedback_scope": "one_time", "retry_count": 0, "codex_transcript_start_line": 0, "codex_transcript_end_line": 0, "skill_update_requested": 0, "reviewed_at": None, "agent_run_id": None, "conversation_single_chat": None, "audit_tool_events_json": "[]"})
_MEMORY_TEXT = "event_type status last_error memory_episode_id created_at updated_at".split()
_MEMORY_INTEGER = "attempt_id attempts".split()
_MEMORY_DEFAULTS = {"status": "pending", "attempts": 0, "last_error": "", "memory_episode_id": ""}
_REPLY_FIELDS = {"reply_attempts": tuple(_ATTEMPT_TEXT + _ATTEMPT_INTEGER), "memory_write_events": tuple(_MEMORY_TEXT + _MEMORY_INTEGER)}
_REPLY_TYPES = {"reply_attempts": {**dict.fromkeys(_ATTEMPT_TEXT, "text"), **dict.fromkeys(_ATTEMPT_INTEGER, "integer")}, "memory_write_events": {**dict.fromkeys(_MEMORY_TEXT, "text"), **dict.fromkeys(_MEMORY_INTEGER, "integer")}}
REPLY_SEQUENCE_NAMES = {kind: "actions:" + kind for kind in REPLY_ACTION_TYPES}
_ATTEMPT_ORDER = """id conversation_id conversation_title trigger_message_id trigger_sender trigger_text action sensitivity_kind codex_reason draft_reply_text final_reply_text permission_action permission_reason send_status send_error retry_count reviewed_at reviewer_feedback corrected_reply_text created_at updated_at audit_documents_json audit_tool_events_json audit_summary codex_session_id codex_transcript_start_line codex_transcript_end_line direct_user_id direct_open_dingtalk_id oa_process_instance_id oa_task_id oa_url oa_action oa_remark oa_action_result_json conversation_single_chat trigger_create_time calendar_event_id calendar_response_status calendar_response_result_json mail_mailbox mail_message_id mail_subject mail_reply_text mail_action_result_json channel universal_execution_id universal_execution_scope_id reaction_action_result_json document_action_result_json agent_run_id human_decision_options_json feedback_scope skill_update_requested skill_update_receipts_json resolved_at resolution trigger_text_provenance_json""".split()
_FRESH_ATTEMPT_ORDER = """id conversation_id conversation_title trigger_message_id trigger_sender trigger_text trigger_text_provenance_json action sensitivity_kind agent_run_id codex_reason draft_reply_text direct_user_id direct_open_dingtalk_id codex_session_id codex_transcript_start_line codex_transcript_end_line audit_documents_json audit_tool_events_json audit_summary human_decision_options_json oa_process_instance_id oa_task_id oa_url oa_action oa_remark oa_action_result_json calendar_event_id calendar_response_status calendar_response_result_json mail_mailbox mail_message_id mail_subject mail_reply_text mail_action_result_json reaction_action_result_json document_action_result_json final_reply_text permission_action permission_reason send_status send_error retry_count reviewed_at reviewer_feedback corrected_reply_text created_at updated_at feedback_scope skill_update_requested skill_update_receipts_json resolved_at resolution channel""".split()
_MEMORY_ORDER = "id attempt_id event_type payload_json status attempts last_error memory_episode_id created_at updated_at".split()
IDENTITY_FIELDS.update({"sent_replies": "id"})
ActionIdentity = int | str | tuple[str, str] | tuple[int, int]
AUTOINCREMENT_TYPES = ("agent_effect_intents", "agent_execution_receipts")
ACTION_SEQUENCE_NAMES = {kind: "actions:" + kind for kind in AUTOINCREMENT_TYPES}
DELIVERY_SEQUENCE_NAME = "actions:sent_replies"
# source field -> (physical table, exact parent record_type)
REFERENCES = {
    "task_id": ("tasks", "reply_tasks"),
    "consumer_run_id": ("runs", "agent_runs"),
    "audit_run_id": ("runs", "agent_runs"),
    "predecessor_review_id": ("actions", "candidate_reviews"),
    "candidate_id": ("actions", "review_candidates"),
    "review_id": ("actions", "candidate_reviews"),
    "selection_id": ("actions", "candidate_selections"),
    "execution_id": ("actions", "candidate_executions"),
    "first_agent_run_id": ("runs", "agent_runs"),
    "agent_run_id": ("runs", "agent_runs"),
}
DELIVERY_REFERENCES = {"sent_reply_id": ("actions", "sent_replies"), "reply_task_id": ("tasks", "reply_tasks")}
NULLABLE = {"predecessor_review_id", "selection_id"}
INTEGER_FIELDS = {"stage_index", "proposal_revision", "action_index", "exit_code", "completed", "persisted", "safe_to_confirm", "effect_counted"}
NULLABLE_PAYLOAD_FIELDS = {"agent_effect_intents": {"exit_code"}}
PAYLOAD_FIELDS = {
    kind: tuple(c for c in columns if c != IDENTITY_FIELDS.get(kind) and c not in REFERENCES
                and not (kind in POSTFIX_ACTION_TYPES and c in POSTFIX_IDENTITY_FIELDS))
    for kind, columns in SOURCE_COLUMNS.items()
}
ENUMS = {
    "candidate_reviews": ("decision", ("approve", "return", "reject")),
    "candidate_executions": ("status", ("running", "done", "skipped", "failed", "retry", "uncertain")),
    "candidate_action_attempts": ("status", ("dispatched", "verified", "confirmed_no_effect", "uncertain", "failed")),
    "agent_effect_intents": ("state", ("prepared", "dispatched", "acknowledged")),
}
DEFAULTS = {
    "review_candidates": {"invalidated_at": "", "invalidation_reason": ""},
    "candidate_reviews": {},
    "candidate_selections": {"status": "selected"},
    "candidate_supplements": {},
    "candidate_executions": {"lease_owner": "", "lease_expires_at": "", "result_json": "{}"},
    "candidate_action_attempts": {"result_json": "{}"},
    "external_action_results": {},
    "agent_effect_intents": {"state": "prepared", "result_digest": "", "exit_code": None, "dispatched_at": "", "acknowledged_at": "", "external_action_key": ""},
    "agent_execution_receipts": {"effect_counted": 0},
    "outbound_postfixes": {"feedback_token": ""},
    "outbound_postfix_receipts": {},
    "native_reply_dispatches": {},
}
PAYLOAD_FIELDS.update({
    "sent_replies": tuple(field for field in SOURCE_COLUMNS["sent_replies"] if field != "id"),
    "sent_reply_observers": ("created_at",),
})
NULLABLE_PAYLOAD_FIELDS["sent_replies"] = {"recalled_at"}
INTEGER_FIELDS.add("agent_run_id")
DEFAULTS.update({
    "sent_replies": {"send_result_json": "", "recall_key": "", "recall_status": "", "recall_error": "", "recalled_at": None, "feedback_token": "", "channel": "dingtalk", "agent_run_id": 0, "external_action_key": ""},
    "sent_reply_observers": {},
})


# Source-only declaration for the closed Action entity conversion. The current
# schema has its own positive declaration and does not use this factory.
_HISTORICAL_ACTION_FIELDS = (
    'operation', 'parameters_json', 'status', 'action_plan_id', 'classification_id',
    'account_id', 'external_action_key', 'next_attempt_at', 'started_at', 'created_at',
    'updated_at', 'prepared_body', 'prepared_at', 'delivery_channel', 'delivery_key',
    'feedback_token', 'postfix_version', 'delivery_receipt_id', 'conversation_id',
    'trigger_message_id', 'recall_key', 'prepared_receipt_id', 'recall_status',
    'recall_error', 'recalled_at', 'agent_run_id', 'business_object_key', 'action_identity',
)
_HISTORICAL_ACTION_INTEGERS = frozenset({'classification_id', 'agent_run_id', 'delivery_receipt_id', 'prepared_receipt_id'})


@lru_cache(maxsize=4)
def action_payloads(reply_variant, *, physical_provenance=False, selection_references=False, execution_owner=False, shared_lifecycle=False, specialized_claims=False, postfix_owner=False, action_entity=False):
    """Frozen historical payload slots for explicit release and inverse work."""
    if reply_variant not in REPLY_VARIANTS:
        raise ValueError("unsupported Actions reply variant")
    attempt_fields = tuple(field for field in _REPLY_FIELDS["reply_attempts"]
                           if reply_variant != "fresh_memory_fk" or field not in _ATTEMPT_HISTORICAL)
    memory_fields = tuple(field for field in _REPLY_FIELDS["memory_write_events"]
                          if reply_variant != "fresh_memory_fk" or field != "attempt_id")
    return _build_action_payloads(
        attempt_fields, memory_fields, physical_provenance=physical_provenance,
        selection_references=selection_references, execution_owner=execution_owner,
        shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims,
        postfix_owner=postfix_owner, action_entity=action_entity,
    )


def _build_action_payloads(attempt_fields, memory_fields, *, physical_provenance=False,
                           selection_references=False, execution_owner=False,
                           shared_lifecycle=False, specialized_claims=False,
                           postfix_owner=False, action_entity=False,
                           signal_source_references=False):
    """Compose the common payload layout from explicit Reply field tuples."""
    from app.action_payload import ActionPayloads
    from app.storage_payload import PayloadLayout
    from app.action_feedback_definitions import FEEDBACK_FIELDS
    from app.action_business_definitions import BUSINESS_ACTION_FIELDS, BUSINESS_ACTION_AUTO
    from app.action_email_history_definitions import HISTORY_FIELDS
    from app.provenance_definitions import SIGNAL_FIELDS, SIGNAL_REFERENCE_FIELDS

    fields = {kind: PAYLOAD_FIELDS[kind] for kind in ALL_ACTION_TYPES_WITH_DELIVERY}
    fields.update({"reply_attempts": tuple(attempt_fields),
                   "memory_write_events": tuple(memory_fields)})
    fields.update({kind: tuple(field for field in names if field != "id"
                               and field not in EMAIL_COMPOSITE_KEYS.get(kind, ()))
                   for kind, names in SOURCE_EMAIL_FIELDS.items()})
    fields.update({kind: tuple(field for field in names if field != "id"
                               and not (kind == "business_task_follow_up_send_attempts" and field == "draft_id"))
                   for kind, names in SYNC_FIELDS.items()})
    fields.update({kind: tuple(field for field in names
                               if field not in ("job_id" if kind == "meeting_alignment_delivery_claims" else "id",
                                                TASK_DELIVERY_PARENTS[kind][0]))
                   for kind, names in TASK_DELIVERY_FIELDS.items()})
    fields.update({
        "managed_skill_export_receipts": ("revision_id", "sha256", "path", "created_at"),
        "runtime_skill_load_receipts": ("config_id", "pid", "error", "created_at"),
        "errors": ERROR_PAYLOAD_FIELDS,
        "feedback_events": FEEDBACK_FIELDS["feedback_events"],
        "feedback_processing_transitions": FEEDBACK_FIELDS["feedback_processing_transitions"][1:],
        "feedback_iteration_decision_items": (),
    })
    fields.update({kind: tuple(field for field in names if field != "id" or kind not in BUSINESS_ACTION_AUTO)
                   for kind, names in BUSINESS_ACTION_FIELDS.items()})
    if physical_provenance:
        fields["business_task_signals"] = (
            SIGNAL_REFERENCE_FIELDS if signal_source_references else SIGNAL_FIELDS
        )
    fields.update(HISTORY_FIELDS)
    if selection_references:
        fields["candidate_selections"] = tuple(
            field for field in fields["candidate_selections"] if field != "branch_json")
    if execution_owner:
        fields['review_candidates'] += EXECUTION_FIELDS
        del fields['candidate_executions']
        fields[EXECUTION_ALIAS] = ()
    record_types = EXECUTION_ACTION_RECORD_TYPES
    if shared_lifecycle:
        from app.shared_action_definitions import SHARED_RECORD_TYPES, direct_step_layout
        fields['email_actions'] = direct_step_layout().fields
        record_types = SHARED_RECORD_TYPES
    if specialized_claims:
        from app.shared_action_definitions import SPECIALIZED_RECORD_TYPES
        record_types = SPECIALIZED_RECORD_TYPES
    if postfix_owner:
        from app.prepared_action_definitions import CHILDREN
        fields = {kind: names for kind, names in fields.items() if kind not in CHILDREN}
    if action_entity:
        from app.action_entity_definitions import ActionEntityRecordTypes
        fields["action"] = _HISTORICAL_ACTION_FIELDS
        record_types = ActionEntityRecordTypes(record_types)
    return ActionPayloads({kind: PayloadLayout(tuple(names)) for kind, names in fields.items()}, record_types)


def _kind_references(kind, *, execution_owner=False):
    if execution_owner:
        if kind in REPLY_ACTION_TYPES:
            return {}
        if kind == 'review_candidates':
            return {**_kind_references(kind), **EXECUTION_REFERENCES}
        if kind == EXECUTION_ALIAS:
            return {'candidate_id': REFERENCES['candidate_id']}
        if kind == 'candidate_action_attempts':
            return {'execution_id': ('actions', EXECUTION_ALIAS)}
    if kind == "sent_replies":
        return {}
    if kind == "sent_reply_observers":
        return {"agent_run_id": REFERENCES["agent_run_id"], **DELIVERY_REFERENCES}
    return {field: reference for field, reference in REFERENCES.items() if field in SOURCE_COLUMNS[kind]}


def _objects(db, table):
    return [dict(zip(("type", "name", "sql"), row, strict=True)) for row in db.execute(
        "select type,name,sql from sqlite_master where tbl_name=? collate nocase and sql is not null order by type,name", (table,)
    )]


@lru_cache(maxsize=1)
def _source_schema():
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(SOURCE_ACTION_DDL)
        for table, column, parent, label in (
            ("review_candidates", "task_id", "tasks", "task parent must be a reply task"),
            ("review_candidates", "consumer_run_id", "runs", "run parent must be a logical agent run"),
            ("candidate_reviews", "audit_run_id", "runs", "run parent must be a logical agent run"),
        ):
            kind = "reply_tasks" if parent == "tasks" else "agent_runs"
            for event, suffix in (("insert", "insert"), (f'update of "{column}"', "update")):
                db.execute(f'create trigger trg_{parent}_child_{table}_{column}_{suffix} before {event} on "{table}" when new."{column}" is not null and exists(select 1 from {parent} where id=new."{column}" and record_type<>\'{kind}\') begin select raise(abort,\'{label}\'); end')
        return {kind: {"columns": list(SOURCE_COLUMNS[kind]), "objects": _objects(db, kind)} for kind in ACTION_TYPES}


def _references_for(kinds, *, execution_owner=False):
    available = REFERENCES | EXECUTION_REFERENCES if execution_owner else REFERENCES
    references = {field: parent for field, parent in available.items()
                  if any(field in _kind_references(kind, execution_owner=execution_owner) for kind in kinds)}
    if execution_owner:
        references['execution_id'] = ('actions', EXECUTION_ALIAS)
    if "sent_reply_observers" in kinds:
        references.update(DELIVERY_REFERENCES)
    return references


def _payload_check(kind, references, payloads=None, *, result_references=False, execution_owner=False, sent_result_references=False):
    terms = []
    fields = payloads.layouts[kind].fields if payloads else PAYLOAD_FIELDS[kind]
    for field in fields:
        expression = "json_type(payload_json," + repr(payloads.path(kind, field) if payloads else "$." + field) + ")"
        value_type = 'integer' if field in INTEGER_FIELDS or execution_owner and field == 'execution_public_id' else 'text'
        condition = f"{expression} is '{value_type}'"
        if (execution_owner and kind == 'review_candidates' and field in EXECUTION_FIELDS
                or field in NULLABLE_PAYLOAD_FIELDS.get(kind, ())
                or result_references and kind == "candidate_action_attempts" and field == "result_json"
                or sent_result_references and kind == "sent_replies" and field == "send_result_json"):
            condition = f"({condition} or {expression} is 'null')"
        terms.append(condition)
    paths = ",".join(repr("$." + field) for field in fields)
    terms.append(payloads.shape(kind) if payloads else f"json_remove(payload_json,{paths})='{{}}'")
    for field in references:
        if field not in _kind_references(kind, execution_owner=execution_owner):
            terms.append(f"{field} is null")
        elif field in NULLABLE or execution_owner and field in EXECUTION_REFERENCES:
            terms.append(f"{field} is null or typeof({field})='integer'")
        else:
            terms.append(f"{field} is not null and typeof({field})='integer'")
    return f"check(record_type<>'{kind}' or ({' and '.join('(' + t + ')' for t in terms)}))"


def _reply_value_check(field):
    path = f"$.{field}"
    field_type = f"json_type(payload_json,'{path}')"
    tag = f"json_extract(payload_json,'{path}.t')"
    blob_value = f"json_extract(payload_json,'{path}.v')"
    obj = f"json_extract(payload_json,'{path}')"
    blob = (f"({tag}='blob' and json_type(payload_json,'{path}.v')='text' "
            f"and length({blob_value})%2=0 and unhex({blob_value}) is not null "
            f"and json_remove({obj},'$.t','$.v')='{{}}')")
    nonfinite = f"({tag} in ('inf','-inf') and json_remove({obj},'$.t')='{{}}')"
    return (f"(case when {field_type} in ('null','text','integer','real','true','false') then 1 "
            f"when {field_type}='object' then coalesce((json_type(payload_json,'{path}.t')='text' "
            f"and ({blob} or {nonfinite})),0) else 0 end)")


def _reply_payload_check(fields, *, kind=None, payloads=None):
    if payloads:
        return payloads.shape(kind) + " and " + " and ".join(payloads.check(kind, field) for field in fields)
    paths = ",".join(repr("$." + field) for field in fields)
    return f"json_remove(payload_json,{paths})='{{}}' and " + " and ".join(_reply_value_check(field) for field in fields)


def _action_definitions(include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None, include_email=False, include_followup_sync=False, include_skill_config_receipts=False, include_business_sync=False, include_task_delivery=False, include_errors=False, include_feedback=False, include_business_actions=False, include_email_history=False, inline_legacy_owner=False, physical_provenance=False, compact_payload=False, *, selection_references=False, result_references=False, execution_owner=False, sent_result_references=False, shared_lifecycle=False, specialized_claims=False, postfix_owner=False, logical_owner=False, action_entity=False, payloads=None, sync_fields=None, canonical_attempt_fields=False, retired_types=(), signal_source_references=False):
    if sent_result_references and not (compact_payload and (include_delivery or include_reply)):
        raise ValueError("Sent result references require compact delivery Actions")
    if result_references and not compact_payload:
        raise ValueError("result references require compact Actions payloads")
    if selection_references and not compact_payload:
        raise ValueError("selection references require compact Actions payloads")
    payloads = payloads if payloads is not None else action_payloads(reply_variant or "actual_no_memory_fk", physical_provenance=physical_provenance, selection_references=selection_references, execution_owner=execution_owner, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, action_entity=action_entity) if compact_payload else None
    if include_skill_config_receipts and not include_email:
        raise ValueError("Skill receipts require current Email action scaffold")
    if include_followup_sync and not include_email:
        raise ValueError("follow-up/Todo action storage requires the Email29 scaffold")
    if include_task_delivery and not include_followup_sync:
        raise ValueError("task delivery action storage requires follow-up/Todo actions")
    if include_errors and not include_task_delivery:
        raise ValueError("error action storage requires current task delivery scaffold")
    if include_feedback and not include_errors:
        raise ValueError("Feedback action storage requires current Error scaffold")
    if include_business_actions and not include_feedback:
        raise ValueError("Business actions require current Feedback scaffold")
    if include_email_history and not include_business_actions:
        raise ValueError("Email history requires current Business Actions scaffold")
    if include_email and not include_reply:
        raise ValueError("Email action storage requires the sixteen-type reply schema")
    if include_reply and reply_variant not in REPLY_VARIANTS:
        raise ValueError("reply storage requires one global source variant")
    if canonical_attempt_fields and (not include_reply or reply_variant != "fresh_memory_fk" or payloads is None):
        raise ValueError("canonical Attempt fields require current payloads and physical Memory parent")
    if not include_reply and reply_variant is not None:
        raise ValueError("reply variant requires opt-in reply storage")
    include_delivery = include_delivery or include_reply
    include_postfix = include_postfix or include_delivery
    include_receipts = include_receipts or include_postfix
    retired_types = frozenset(retired_types)
    kinds = ALL_ACTION_TYPES_WITH_REPLY if include_reply else ALL_ACTION_TYPES_WITH_DELIVERY if include_delivery else ALL_ACTION_TYPES_WITH_POSTFIX if include_postfix else ALL_ACTION_TYPES if include_receipts else CANDIDATE_ACTION_TYPES
    kinds = tuple(kind for kind in kinds if kind not in retired_types)
    if execution_owner:
        if not compact_payload:
            raise ValueError('execution owner requires compact Actions payloads')
        kinds = tuple(kind for kind in kinds if kind != 'candidate_executions')
        if EXECUTION_ALIAS not in retired_types:
            kinds += (EXECUTION_ALIAS,)
    if postfix_owner:
        from app.prepared_action_definitions import CHILDREN, prepared_schema_parts
        if not (compact_payload and include_postfix):
            raise ValueError("prepared owner requires compact Postfix Actions")
        kinds = tuple(kind for kind in kinds if kind not in CHILDREN)
        if action_entity:
            prepared_columns, prepared_checks = (), ()
        else:
            prepared_columns, prepared_checks = prepared_schema_parts()
    old_kinds = kinds
    if action_entity:
        if not compact_payload:
            raise ValueError("Action entity requires current compact Actions payloads")
        kinds = ("action", *kinds)
    payload_kinds = tuple(kind for kind in kinds if kind != "action")
    if include_email:
        kinds += tuple(kind for kind in EMAIL_TYPES_ORDER if kind not in retired_types)
        email_generated, email_checks, email_fks, email_indexes, email_triggers = email_schema_parts(
            lambda field: _encoded_value_check("payload_json", "$." + field), payloads=payloads,
            shared_lifecycle=shared_lifecycle, retired_types=retired_types,
            signal_source_references=signal_source_references)
    if include_followup_sync:
        kinds += SYNC_TYPES_ORDER
    if include_task_delivery:
        kinds += TASK_DELIVERY_KINDS
    if include_skill_config_receipts:
        kinds += SKILL_RECEIPT_KINDS
        skill_generated, skill_physical, skill_checks, skill_indexes, skill_triggers = skill_receipt_schema_parts(payloads=payloads)
    if include_errors:
        kinds += ("errors",)
    if include_feedback:
        kinds += FEEDBACK_KINDS
    if include_business_actions:
        kinds += BUSINESS_ACTION_KINDS
    if include_email_history:
        kinds += HISTORY_KINDS
        history_generated, history_checks, history_indexes = history_schema_parts(payloads=payloads)
    payload_kinds = tuple(kind for kind in kinds if kind != "action")
    references = _references_for(old_kinds, execution_owner=execution_owner)
    if include_followup_sync:
        sync_generated, sync_checks, sync_fks, sync_indexes, sync_triggers = sync_schema_parts(
            references, reply_variant, business_public_ids=include_business_sync,
            business_action_signals=include_business_actions,
            inline_legacy_owner=inline_legacy_owner, payloads=payloads, fields=sync_fields)
    if include_task_delivery:
        td_generated, td_checks, td_indexes, td_triggers = task_delivery_schema_parts(
            references, reply_variant, payloads=payloads)
    if include_errors:
        unused_references = (
            *references,
            *(("memory_attempt_ref",) if reply_variant == "fresh_memory_fk" else ()),
            "sync_draft_action_id",
            *(parent_column for _, parent_column, _ in TASK_DELIVERY_PARENTS.values()),
            *((column.split(" ", 1)[0] for column in skill_physical)
              if include_skill_config_receipts else ()),
        )
        error_generated, error_checks, error_indexes, error_triggers = error_schema_parts(
            unused_references, payloads=payloads)
    if include_feedback:
        fb_unused = (
            *references,
            *(('memory_attempt_ref',) if reply_variant == 'fresh_memory_fk' else ()),
            'sync_draft_action_id',
            *(parent_column for _, parent_column, _ in TASK_DELIVERY_PARENTS.values()),
            *((column.split(' ', 1)[0] for column in skill_physical)
              if include_skill_config_receipts else ()),
        )
        fb_generated, fb_checks, fb_indexes, fb_triggers = feedback_schema_parts(fb_unused, payloads=payloads)
    if include_business_actions:
        outer_columns = (
            "id", "record_type", "legacy_key", *references,
            *(("memory_attempt_ref",) if reply_variant == "fresh_memory_fk" else ()),
            *((column.split(" ", 1)[0] for column in skill_physical)
              if include_skill_config_receipts else ()),
            "payload_json",
        )
        ba_generated, ba_checks, ba_indexes, ba_triggers = business_action_schema_parts(
            physical_provenance=physical_provenance, outer_columns=outer_columns, payloads=payloads,
            signal_source_references=signal_source_references,
        )
    payload_fields = dict.fromkeys(c for kind in old_kinds if kind not in REPLY_ACTION_TYPES for c in (payloads.layouts[kind].fields if payloads else PAYLOAD_FIELDS[kind]) if c not in references and not (include_delivery and c == "channel"))
    generated = []
    for column in payload_fields:
        expression = (payloads.field_value(column, payload_kinds) if payloads else f"json_extract(payload_json,'$.{column}')")
        if not payloads and include_errors and column == "created_at":
            expression = ("case when record_type='errors' then "
                          + _encoded_value("payload_json", "$.created_at")
                          + (" when record_type in ('feedback_events','feedback_processing_transitions') then "
                             + _encoded_value("payload_json", "$.created_at") if include_feedback else "")
                          + f" else {expression} end")
        if include_receipts and column == 'external_action_key':
            expression = f"case when record_type='external_action_results' then legacy_key else {expression} end"
        generated.append(f"{column} {'integer' if column in INTEGER_FIELDS or execution_owner and column == 'execution_public_id' else 'text'} generated always as ({expression}) virtual")
    execution_checks = ()
    execution_indexes = ()
    execution_triggers = ()
    if execution_owner and EXECUTION_ALIAS not in retired_types:
        execution_generated, execution_checks, execution_indexes, execution_triggers = execution_schema_parts(record_types=payloads.record_types)
        generated.extend(execution_generated)
    if sent_result_references and not action_entity:
        from app.sent_result_definitions import sent_result_schema_parts
        sent_generated, sent_checks, sent_indexes, sent_triggers = sent_result_schema_parts(payloads)
        generated.extend(sent_generated)
    if shared_lifecycle:
        from app.shared_action_definitions import shared_discriminators
        generated.extend(shared_discriminators(
            specialized_claims=specialized_claims, retired_types=retired_types,
        ))
    postfix_scope = "'outbound_postfixes'" if postfix_owner and not action_entity else _POSTFIX_TYPES_SQL
    if include_postfix and not action_entity:
        for index, column in enumerate(POSTFIX_IDENTITY_FIELDS):
            sent_channel = ("when record_type='sent_replies' then " + (payloads.value("sent_replies", "channel") if payloads else "json_extract(payload_json,'$.channel')") + " ") if include_delivery and not action_entity and column == "channel" else ""
            generated.append(f"{column} text generated always as (case {sent_channel}when record_type in ({postfix_scope}) then json_extract(legacy_key,'$[{index}]') end) virtual")
        if not postfix_owner and not action_entity:
            generated.append("postfix_type text generated always as (case when record_type in ('outbound_postfix_receipts','native_reply_dispatches') then 'outbound_postfixes' end) virtual")
    reply_columns = []
    if include_reply:
        for kind, prefix in (("reply_attempts", "attempt_"), ("memory_write_events", "memory_")):
            for field in _REPLY_FIELDS[kind]:
                if kind == "reply_attempts" and reply_variant == "fresh_memory_fk" and field in _ATTEMPT_HISTORICAL and not canonical_attempt_fields:
                    continue
                if kind == "memory_write_events" and field == "attempt_id" and reply_variant == "fresh_memory_fk":
                    continue
                path = payloads.path(kind, field) if payloads else f"$.{field}"
                encoded = (f"case json_extract(payload_json,'{path}.t') "
                           f"when 'blob' then unhex(json_extract(payload_json,'{path}.v')) "
                           "when 'inf' then 9e999 when '-inf' then -9e999 end")
                value = (f"case when json_type(payload_json,'{path}')='object' "
                         f"then {encoded} else json_extract(payload_json,'{path}') end")
                if payloads:
                    value = payloads.scoped_value(kind, field)
                reply_columns.append(f"{prefix}{field} {_REPLY_TYPES[kind][field]} generated always as ({value}) virtual")
        if reply_variant == "fresh_memory_fk":
            reply_columns.append("memory_attempt_ref integer references actions(id)")
    key_check = "legacy_key=cast(cast(legacy_key as integer) as text)"
    if include_receipts:
        key_check = f"case when record_type='external_action_results' then trim(legacy_key)<>'' else {key_check} end"
    if include_postfix and not action_entity:
        composite_check = ("case when json_valid(legacy_key) then json_type(legacy_key)='array' "
                           "and json_array_length(legacy_key)=2 and json_type(legacy_key,'$[0]') is 'text' "
                           "and json_type(legacy_key,'$[1]') is 'text' else 0 end")
        key_check = f"case when record_type in ({postfix_scope}) then ({composite_check}) else ({key_check}) end"
    if include_delivery and not action_entity:
        observer_key = ("case when json_valid(legacy_key) then json_type(legacy_key)='array' "
                        "and json_array_length(legacy_key)=2 and json_type(legacy_key,'$[0]')='integer' "
                        "and json_type(legacy_key,'$[1]')='integer' else 0 end")
        key_check = f"case when record_type='sent_reply_observers' then ({observer_key}) else ({key_check}) end"
    if include_email:
        email_composites = ",".join(repr(kind) for kind in EMAIL_COMPOSITE_KEYS)
        key_check = f"case when record_type in ({email_composites}) then json_valid(legacy_key) else ({key_check}) end"
    if action_entity:
        key_check = f"record_type='action' and legacy_key is null or ({key_check})"
    if payloads:
        kind_declarations = ",".join(payloads.record_types.declarations(kinds))
    else:
        kind_declarations = "record_type text not null check(record_type in (" + ",".join(repr(k) for k in kinds) + "))"
    action_generated = ()
    if action_entity:
        action_generated = tuple(
            f"action_{field} {'integer' if field in _HISTORICAL_ACTION_INTEGERS else 'text'} generated always as "
            f"(case when _kind=70 then json_extract(action_data,'$.{field}') end) virtual"
            for field in _HISTORICAL_ACTION_FIELDS
        )
    table = (
        "create table actions (id integer primary key," + " " + kind_declarations
        + (f",legacy_key text check({key_check})," if action_entity else f",legacy_key text not null check({key_check}),")
        + ",".join(f"{column} integer references {parent}(id)" for column, (parent, _) in references.items())
        + ("," + ",".join(reply_columns) if reply_columns else "")
        + ("," + ",".join(email_generated) if include_email else "")
        + ("," + ",".join(sync_generated) if include_followup_sync else "")
        + ("," + ",".join(td_generated) if include_task_delivery else "")
        + ("," + ",".join((*skill_physical, *skill_generated)) if include_skill_config_receipts else "")
        + ("," + ",".join(error_generated) if include_errors else "")
        + ("," + ",".join(fb_generated) if include_feedback else "")
        + ("," + ",".join(ba_generated) if include_business_actions else "")
        + ("," + ",".join(history_generated) if include_email_history else "")
        + ("," + ",".join(prepared_columns) if postfix_owner and prepared_columns else "")
        + (",action_data blob" if action_entity else "")
        + (",payload_json blob not null," if payloads else ",payload_json text not null check(json_valid(payload_json) and json_type(payload_json)='object'),")
        + ",".join(generated)
        + (("," + ",".join(action_generated)) if action_entity else "")
        + "," + ",".join(_payload_check(kind, references, payloads, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references) for kind in old_kinds if kind not in REPLY_ACTION_TYPES)
        + "," + ",".join(f"check(record_type<>'{kind}' or {field} in ({','.join(repr(v) for v in options)}))"
                          for kind, (field, options) in ENUMS.items() if kind in kinds)
    )
    if include_receipts:
        table += ",check(record_type<>'agent_effect_intents' or action_index>=0)"
        if "external_action_results" not in retired_types:
            for field in ('business_object_key', 'action_identity', 'operation', 'result_digest'):
                table += f",check(record_type<>'external_action_results' or trim({field})<>'')"
    if include_postfix and not action_entity:
        table += ",check(record_type not in ('outbound_postfixes','outbound_postfix_receipts') or (channel in ('dingtalk','wechat') and trim(delivery_key)<>''))"
        table += ",check(record_type<>'outbound_postfixes' or trim(final_body)<>'')"
        if postfix_owner:
            table += "," + ",".join(prepared_checks)
        else:
            table += ",foreign key(postfix_type,channel,delivery_key) references actions(record_type,channel,delivery_key)"
    if include_delivery and not action_entity:
        table += ",check(record_type<>'sent_reply_observers' or (sent_reply_id is not null and agent_run_id is not null and reply_task_id is not null and json_extract(legacy_key,'$[1]')=agent_run_id))"
    if include_reply:
        table += ",check(record_type not in ('reply_attempts','memory_write_events') or (" + " and ".join(f"{field} is null" for field in references) + "))"
        attempt_fields = [field for field in _REPLY_FIELDS["reply_attempts"] if canonical_attempt_fields or reply_variant == "actual_no_memory_fk" or field not in _ATTEMPT_HISTORICAL]
        table += ",check(record_type<>'reply_attempts' or (" + _reply_payload_check(attempt_fields, kind="reply_attempts", payloads=payloads) + "))"
        table += ",check(record_type<>'reply_attempts' or (" + " and ".join(f"attempt_{field} is not null" for field in attempt_fields if field not in _ATTEMPT_NULLABLE) + "))"
        memory_parent = "memory_attempt_ref" if reply_variant == "fresh_memory_fk" else "memory_attempt_id"
        memory_fields = [field for field in _REPLY_FIELDS["memory_write_events"] if field != "attempt_id" or reply_variant == "actual_no_memory_fk"]
        table += ",check(record_type<>'memory_write_events' or (" + _reply_payload_check(memory_fields, kind="memory_write_events", payloads=payloads) + "))"
        table += ",check(record_type<>'memory_write_events' or (" + " and ".join([f"{memory_parent} is not null"] + [f"memory_{field} is not null" for field in _REPLY_FIELDS["memory_write_events"] if field != "attempt_id"]) + "))"
    if include_email:
        table += "," + ",".join((*email_checks, *email_fks))
        unused_refs = tuple(references) + (("memory_attempt_ref",) if reply_variant == "fresh_memory_fk" else ())
        email_scope = ",".join(repr(kind) for kind in EMAIL_TYPES_ORDER)
        table += ",check(record_type not in (" + email_scope + ") or (" + " and ".join(
            f"{field} is null" for field in unused_refs
        ) + "))"
    if include_followup_sync:
        table += "," + ",".join((*sync_checks, *sync_fks))
    if include_task_delivery:
        table += "," + ",".join(td_checks)
    if include_skill_config_receipts:
        table += "," + ",".join(skill_checks)
        unused = tuple(references) + (("memory_attempt_ref",) if reply_variant == "fresh_memory_fk" else ())
        scope = ",".join(repr(kind) for kind in SKILL_RECEIPT_KINDS)
        table += ",check(record_type not in (" + scope + ") or (" + " and ".join(f"{field} is null" for field in unused) + "))"
    if include_errors:
        table += "," + ",".join(error_checks)
    if include_feedback:
        table += "," + ",".join(fb_checks)
    if include_business_actions:
        table += "," + ",".join(ba_checks)
    if include_email_history:
        table += "," + ",".join(history_checks)
    if execution_owner and execution_checks:
        table += "," + ",".join(execution_checks)
        if EXECUTION_ALIAS not in retired_types and include_reply and reply_variant == 'fresh_memory_fk':
            table += ",check(record_type<>'candidate_execution_alias' or memory_attempt_ref is null)"
    if sent_result_references and not action_entity:
        table += "," + ",".join(sent_checks)
    if action_entity:
        table += ",check(record_type<>'action' or (legacy_key is null and typeof(action_data)='blob' and json_valid(action_data,8) and json_type(action_data)='object'))"
        table += ",check(record_type='action' or action_data is null)"
        table += ",check(record_type<>'action' or action_status is null or action_status in ('pending','processing','done','skipped','failed','dispatched','verified','confirmed_no_effect','uncertain'))"
    table += ")"
    numeric_scope = (" where record_type<>'action'" if action_entity else "")
    if include_receipts:
        numeric_scope += (" and " if numeric_scope else " where ") + "record_type<>'external_action_results'"
    if include_postfix:
        numeric_scope += f" and record_type not in ({_POSTFIX_TYPES_SQL})"
    if include_delivery:
        numeric_scope += " and record_type<>'sent_reply_observers'"
    if include_email:
        numeric_scope += " and record_type not in (" + ",".join(repr(kind) for kind in EMAIL_COMPOSITE_KEYS) + ")"
    numeric_columns = "record_type,cast(legacy_key as integer)" if include_email else "cast(legacy_key as integer),record_type"
    indexes = (
        f"create unique index idx_actions_public_id on actions({numeric_columns})" + numeric_scope,
        "create unique index idx_actions_consumer_run on actions(consumer_run_id) where record_type='review_candidates'",
        "create index idx_actions_candidates_task on actions(task_id,execution_generation,cast(legacy_key as integer)) where record_type='review_candidates'",
        "create unique index idx_actions_audit_run on actions(audit_run_id) where record_type='candidate_reviews'",
        "create index idx_actions_reviews_candidate on actions(candidate_id,cast(legacy_key as integer)) where record_type='candidate_reviews'",
        "create unique index idx_actions_selection_candidate on actions(candidate_id) where record_type='candidate_selections'",
        "create unique index idx_actions_execution_candidate on actions(candidate_id) where record_type='candidate_executions'",
        "create unique index idx_actions_attempt_sequence on actions(execution_id,action_index) where record_type='candidate_action_attempts'",
        "create index idx_actions_external_key on actions(external_action_key) where record_type='candidate_action_attempts'",
    )
    if "candidate_action_attempts" in retired_types:
        indexes = tuple(sql for sql in indexes if "idx_actions_attempt_sequence" not in sql
                        and "idx_actions_external_key" not in sql)
    if execution_owner and EXECUTION_ALIAS not in retired_types:
        indexes = tuple(sql for sql in indexes if 'idx_actions_execution_candidate ' not in sql) + execution_indexes
    if include_receipts:
        indexes += (
            "create unique index idx_actions_text_public_key on actions(legacy_key,record_type) where record_type='external_action_results'",
            "create index idx_actions_global_business on actions(business_object_key,external_action_key) where record_type='external_action_results'",
            "create unique index idx_actions_intent_authorization on actions(agent_run_id,authorization_id) where record_type='agent_effect_intents'",
            "create unique index idx_actions_intent_operation on actions(agent_run_id,receipt_operation_id) where record_type='agent_effect_intents'",
            "create index idx_actions_intent_run on actions(agent_run_id,action_index,cast(legacy_key as integer)) where record_type='agent_effect_intents'",
            "create unique index idx_actions_execution_receipt_operation on actions(agent_run_id,operation_id) where record_type='agent_execution_receipts'",
            "create index idx_actions_execution_receipt_run on actions(agent_run_id,cast(legacy_key as integer)) where record_type='agent_execution_receipts'",
        )
        if "external_action_results" in retired_types:
            indexes = tuple(sql for sql in indexes if "idx_actions_text_public_key" not in sql
                            and "idx_actions_global_business" not in sql)
    if include_postfix and not action_entity:
        indexes += ("create unique index idx_actions_postfix_identity on actions(record_type,channel,delivery_key)",)
    if include_delivery and not action_entity:
        indexes += (
            "create unique index idx_actions_observer_identity on actions(sent_reply_id,agent_run_id) where record_type='sent_reply_observers'",
            "create unique index idx_actions_sent_external_key on actions(external_action_key) where record_type='sent_replies' and external_action_key<>''",
            "create index idx_actions_sent_history on actions(conversation_id,trigger_message_id,sent_at) where record_type='sent_replies'",
        )
    if include_reply:
        memory_parent = "memory_attempt_ref" if reply_variant == "fresh_memory_fk" else "memory_attempt_id"
        indexes += (
            "create unique index idx_actions_reply_public_order on actions(record_type,cast(legacy_key as integer)) where record_type='reply_attempts'",
            "create index idx_actions_memory_public_order on actions(record_type,cast(legacy_key as integer)) where record_type='memory_write_events'",
            "create index idx_actions_reply_trigger_message on actions(attempt_trigger_message_id) where record_type='reply_attempts'",
            "create index idx_actions_reply_status on actions(record_type,attempt_send_status,attempt_created_at) where record_type='reply_attempts'",
            "create index idx_actions_reply_created on actions(record_type,attempt_created_at,cast(legacy_key as integer)) where record_type='reply_attempts'",
            "create index idx_actions_reply_oa_history on actions(attempt_oa_process_instance_id,attempt_created_at desc,cast(legacy_key as integer) desc) where record_type='reply_attempts' and attempt_oa_process_instance_id<>''",
            "create index idx_actions_reply_trigger_history on actions(attempt_conversation_id,attempt_trigger_message_id,attempt_action,cast(legacy_key as integer) desc) where record_type='reply_attempts'",
            "create index idx_actions_reply_current_trigger on actions(attempt_channel,attempt_conversation_id,attempt_trigger_message_id,attempt_updated_at desc,cast(legacy_key as integer) desc) where record_type='reply_attempts'",
            "create index idx_actions_reply_run_recovery on actions(attempt_agent_run_id,attempt_send_error,cast(legacy_key as integer)) where record_type='reply_attempts'",
            f"create unique index idx_actions_memory_unique on actions({memory_parent},memory_event_type) where record_type='memory_write_events'",
            f"create index idx_actions_memory_attempt on actions({memory_parent},cast(legacy_key as integer)) where record_type='memory_write_events'",
            f"create index idx_actions_memory_status on actions(memory_status,{'memory_updated_at' if reply_variant == 'fresh_memory_fk' else 'cast(legacy_key as integer)'}) where record_type='memory_write_events'",
        )
    if include_email:
        indexes += tuple(email_indexes)
    if include_followup_sync:
        indexes += tuple(sync_indexes)
    if include_task_delivery:
        indexes += tuple(td_indexes)
    if include_skill_config_receipts:
        indexes += skill_indexes
    if include_errors:
        indexes += error_indexes
    if include_feedback:
        indexes += fb_indexes
    if include_business_actions:
        indexes += tuple(ba_indexes)
    if include_email_history:
        indexes += history_indexes
    if sent_result_references and not action_entity:
        indexes += sent_indexes
    if action_entity:
        from app.action_entity_definitions import ACTION_INDEX_DDL
        indexes += ACTION_INDEX_DDL
    triggers = (
        "create trigger trg_actions_record_type_immutable before update of record_type on actions when new.record_type<>old.record_type begin select raise(abort,'action record type is immutable'); end",
        "create trigger trg_actions_public_reference_update before update of legacy_key on actions when new.legacy_key<>old.legacy_key and exists(select 1 from actions where predecessor_review_id=old.id or candidate_id=old.id or review_id=old.id or selection_id=old.id or execution_id=old.id) begin select raise(abort,'FOREIGN KEY constraint failed'); end",
        *tuple(
            f"create trigger trg_actions_{field}_{suffix} before {event} on actions when new.{field} is not null and exists(select 1 from {parent} where id=new.{field} and "
            + ("(record_type<>'agent_runs' or source_type is not null)"
               if logical_owner and parent == "runs" and kind == "agent_runs"
               else f"record_type<>'{kind}'")
            + ") begin select raise(abort,'action parent type mismatch'); end"
            for field, (parent, kind) in references.items()
            for event, suffix in (("insert", "insert"), (f"update of {field}", "update"))
        ),
    )
    if execution_owner and EXECUTION_ALIAS not in retired_types:
        # The retired selection_id reference is replaced by the plan's two FKs.
        triggers = tuple(sql.replace('or selection_id=old.id', 'or execution_review_id=old.id or execution_selection_id=old.id') for sql in triggers)
        triggers += execution_triggers
    if include_receipts:
        sequences = ACTION_SEQUENCE_NAMES | ({"sent_replies": DELIVERY_SEQUENCE_NAME} if include_delivery and not action_entity else {})
        for kind, name in sequences.items():
            if kind in retired_types:
                continue
            triggers += (f"create trigger trg_actions_{kind}_sequence before insert on actions when new.record_type='{kind}' begin "
                         f"insert into sqlite_sequence(name,seq) select '{name}',0 where not exists(select 1 from sqlite_sequence where name='{name}'); "
                         f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) where name='{name}'; end",)
    if include_delivery and not action_entity:
        triggers += ("create trigger trg_actions_sent_public_reference_update before update of legacy_key on actions when old.record_type='sent_replies' and new.legacy_key<>old.legacy_key and exists(select 1 from actions where sent_reply_id=old.id and record_type='sent_reply_observers') begin select raise(abort,'FOREIGN KEY constraint failed'); end",)
        for event, suffix in (("insert", "insert"), ("update of sent_reply_id,legacy_key", "update")):
            triggers += (f"create trigger trg_actions_observer_sent_key_{suffix} before {event} on actions when new.record_type='sent_reply_observers' and not exists(select 1 from actions p where p.id=new.sent_reply_id and p.record_type='sent_replies' and cast(p.legacy_key as integer)=json_extract(new.legacy_key,'$[0]')) begin select raise(abort,'observer sent reply identity mismatch'); end",)
    if include_reply:
        for kind, name in REPLY_SEQUENCE_NAMES.items():
            if kind in retired_types:
                continue
            triggers += (f"create trigger trg_actions_{kind}_sequence before insert on actions when new.record_type='{kind}' begin "
                         f"insert into sqlite_sequence(name,seq) select '{name}',0 where not exists(select 1 from sqlite_sequence where name='{name}'); "
                         f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) where name='{name}'; end",)
        marker_check = (
            "json_type(new.payload_json," + repr(payloads.path("reply_attempts", "audit_tool_events_json") if payloads else "$.audit_tool_events_json") + ") is not 'text' or "
            "case when json_valid(new.attempt_audit_tool_events_json) then ("
            "json_type(new.attempt_audit_tool_events_json) is not 'array' or "
            "exists(select 1 from json_each(new.attempt_audit_tool_events_json) e where "
            "case when e.type='object' then (json_extract(e.value,'$.tool') is not 'audit_review' or "
            "json_extract(e.value,'$.result') is not 'queued' or "
            "json_remove(e.value,'$.tool','$.result')<>'{}') else 1 end)) else 1 end"
        )
        for event, suffix in (("insert", "insert"), ("update of payload_json", "update")):
            triggers += (f"create trigger trg_actions_reply_markers_{suffix} before {event} on actions "
                         f"when new.record_type='reply_attempts' and ({marker_check}) "
                         "begin select raise(abort,'invalid reply audit markers'); end",)
        if reply_variant == "fresh_memory_fk":
            triggers += (
                "create trigger trg_actions_memory_attempt_insert before insert on actions when new.record_type='memory_write_events' and new.memory_attempt_ref is not null and not exists(select 1 from actions p where p.id=new.memory_attempt_ref and p.record_type='reply_attempts') begin select raise(abort,'memory attempt type mismatch'); end",
                "create trigger trg_actions_memory_attempt_update before update of memory_attempt_ref on actions when new.record_type='memory_write_events' and new.memory_attempt_ref is not null and not exists(select 1 from actions p where p.id=new.memory_attempt_ref and p.record_type='reply_attempts') begin select raise(abort,'memory attempt type mismatch'); end",
                "create trigger trg_actions_reply_public_reference_update before update of legacy_key on actions when old.record_type='reply_attempts' and new.legacy_key<>old.legacy_key and exists(select 1 from actions c where c.record_type='memory_write_events' and c.memory_attempt_ref=old.id) begin select raise(abort,'FOREIGN KEY constraint failed'); end",
            )
    if include_email:
        triggers += tuple(email_triggers)
        for kind in EMAIL_AUTOINCREMENT_TYPES:
            if kind in retired_types:
                continue
            name = f"actions:{kind}"
            triggers += (f"create trigger trg_actions_{kind}_sequence before insert on actions when new.record_type='{kind}' begin "
                         f"insert into sqlite_sequence(name,seq) select '{name}',0 where not exists(select 1 from sqlite_sequence where name='{name}'); "
                         f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) where name='{name}'; end",)
    if include_followup_sync:
        triggers += tuple(sync_triggers)
        for kind in SYNC_TYPES_ORDER:
            name = f"actions:{kind}"
            triggers += (f"create trigger trg_actions_sync_{kind}_sequence before insert on actions when new.record_type='{kind}' begin "
                         f"insert into sqlite_sequence(name,seq) select '{name}',0 where not exists(select 1 from sqlite_sequence where name='{name}'); "
                         f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) where name='{name}'; end",)
    if include_task_delivery:
        triggers += tuple(td_triggers)
        for kind in TASK_DELIVERY_AUTOINCREMENT:
            name = f"actions:{kind}"
            triggers += (f"create trigger trg_actions_td_{kind}_sequence before insert on actions when new.record_type='{kind}' begin "
                         f"insert into sqlite_sequence(name,seq) select '{name}',0 where not exists(select 1 from sqlite_sequence where name='{name}'); "
                         f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) where name='{name}'; end",)
    if include_skill_config_receipts:
        triggers += skill_triggers
    if include_errors:
        triggers += error_triggers
    if include_feedback:
        triggers += fb_triggers
    if include_business_actions:
        triggers += tuple(ba_triggers)
    if sent_result_references and not action_entity:
        triggers += sent_triggers
    if action_entity:
        from app.action_entity_definitions import ACTION_ENTITY_PARENT_TRIGGER_DDL
        triggers += ACTION_ENTITY_PARENT_TRIGGER_DDL
    if payloads:
        triggers = tuple((payloads.record_types if execution_owner else SOURCE_ACTION_RECORD_TYPES).trigger_sql(sql) for sql in triggers)
    return table, indexes, triggers


def _projection(kind, *, payloads=None, selection_references=False, result_references=False, execution_owner=False, sent_result_references=False):
    if kind in REPLY_ACTION_TYPES:
        raise ValueError("reply projection requires global schema variant")
    fields, joins = [], []
    for field in SOURCE_COLUMNS[kind]:
        if selection_references and kind == "candidate_selections" and field == "branch_json":
            continue
        if result_references and kind == "candidate_action_attempts" and field == "result_json":
            expression = ("case when a.status='verified' and a.result_json is null "
                          "then result_owner.provider_result_json else a.result_json end")
            joins.append("left join actions result_owner on result_owner.record_type='external_action_results' "
                         "and result_owner.legacy_key=a.external_action_key")
        elif sent_result_references and kind == "sent_replies" and field == "send_result_json":
            expression = ("case when a.send_result_json is null "
                          "then sent_owner.provider_result_json else a.send_result_json end")
            joins.append("left join actions sent_owner on sent_owner.record_type='external_action_results' "
                         "and sent_owner.legacy_key=a.external_action_key")
        elif field == IDENTITY_FIELDS.get(kind):
            expression = "a.legacy_key" if kind == "external_action_results" else "cast(a.legacy_key as integer)"
        elif kind == "sent_replies" and field == "agent_run_id":
            expression = payloads.value(kind, field, "a.payload_json") if payloads else "json_extract(a.payload_json,'$.agent_run_id')"
        elif field in _kind_references(kind, execution_owner=execution_owner) and _kind_references(kind, execution_owner=execution_owner)[field][0] == "actions":
            alias = "p_" + field
            expression = f"cast({alias}.legacy_key as integer)"
            join = "left join" if field in NULLABLE else "join"
            joins.append(f"{join} actions {alias} on {alias}.id=a.{field} and {alias}.record_type='{_kind_references(kind, execution_owner=execution_owner)[field][1]}'")
        else:
            # The existing logical reply-task and agent-run core schemas enforce
            # public ID == physical ID. Their source FK scalars remain exact.
            expression = "a." + field
        fields.append(expression + " as " + field)
    numeric_scope = " and a.record_type<>'external_action_results'" if kind != "external_action_results" else ""
    if kind in IDENTITY_FIELDS and kind != "external_action_results":
        # SQLite needs the partial index predicate explicitly, even when the
        # literal record_type already identifies an integer-keyed type.
        numeric_scope += f" and a.record_type not in ({_POSTFIX_TYPES_SQL})"
        numeric_scope += " and a.record_type<>'sent_reply_observers'"
    return f"select {','.join(fields)} from actions a {' '.join(joins)} where a.record_type='{kind}'{numeric_scope}"


_PROJECTION_PAYLOADS = ActionPayloads({kind: PayloadLayout(fields)
                                      for kind, fields in PROJECTION_PAYLOAD_FIELDS.items()})
ACTION_PROJECTIONS = {kind: _projection(kind, payloads=_PROJECTION_PAYLOADS)
                      for kind in ALL_ACTION_TYPES_WITH_DELIVERY}


def reply_action_projection(kind, reply_variant, *, canonical_attempt_fields=False,
                            receipt_references=False):
    if kind not in REPLY_ACTION_TYPES or reply_variant not in REPLY_VARIANTS:
        raise ValueError("unsupported reply projection")
    order = (_FRESH_ATTEMPT_ORDER if reply_variant == "fresh_memory_fk" and not canonical_attempt_fields else _ATTEMPT_ORDER) if kind == "reply_attempts" else _MEMORY_ORDER
    fields = []
    for field in order:
        if field == "id":
            expression = "cast(a.legacy_key as integer)"
        elif kind == "reply_attempts" and field in _ATTEMPT_HISTORICAL and reply_variant == "fresh_memory_fk" and not canonical_attempt_fields:
            continue
        elif kind == "memory_write_events" and field == "payload_json":
            expression = "'{}'"
        elif kind == "memory_write_events" and field == "attempt_id" and reply_variant == "fresh_memory_fk":
            expression = "cast(p.legacy_key as integer)"
        elif kind == "reply_attempts" and field == "oa_action_result_json" and receipt_references:
            expression = ("coalesce((select receipt.provider_result_json from "
                          "reply_attempt_receipt_refs ref join action_receipts receipt "
                          "on receipt.id=ref.receipt_id where ref.attempt_id=a.id),"
                          "a.attempt_oa_action_result_json)")
        else:
            expression = "a." + ("attempt_" if kind == "reply_attempts" else "memory_") + field
        fields.append(expression + " as " + field)
    join = "left join actions p on p.id=a.memory_attempt_ref and p.record_type='reply_attempts'" if kind == "memory_write_events" and reply_variant == "fresh_memory_fk" else ""
    return f"select {','.join(fields)} from actions a {join} where a.record_type='{kind}' and a.record_type<>'external_action_results' and a.record_type not in ({_POSTFIX_TYPES_SQL}) and a.record_type<>'sent_reply_observers'"


def current_reply_projection(kind, *, common=False):
    """Project current Reply records and resolve the exact terminal Run owner."""
    if kind not in REPLY_ACTION_TYPES:
        raise ValueError("unsupported reply projection")
    if kind == "reply_attempts":
        order = _FRESH_ATTEMPT_ORDER if common else _ATTEMPT_ORDER
        expressions = {
            "id": "cast(a.legacy_key as integer)",
            "codex_session_id": "(select a.attempt_codex_session_id where a.attempt_codex_session_id is not null union all select run.session_id where a.attempt_codex_session_id is null)",
            "codex_transcript_start_line": "(select a.attempt_codex_transcript_start_line where a.attempt_codex_transcript_start_line is not null union all select run.transcript_start where a.attempt_codex_transcript_start_line is null)",
            "codex_transcript_end_line": "(select a.attempt_codex_transcript_end_line where a.attempt_codex_transcript_end_line is not null union all select run.transcript_end where a.attempt_codex_transcript_end_line is null)",
        }
        if common:
            expressions["oa_action_result_json"] = (
                "coalesce((select receipt.provider_result_json from "
                "reply_attempt_receipt_refs ref join action_receipts receipt "
                "on receipt.id=ref.receipt_id where ref.attempt_id=a.id),"
                "a.attempt_oa_action_result_json)"
            )
        fields = [expressions.get(field, "a.attempt_" + field) + " as " + field for field in order]
        join = "left join runs run on run.id=a.attempt_agent_run_id and run.status in ('completed','failed')"
    else:
        expressions = {"id": "cast(a.legacy_key as integer)",
                       "attempt_id": "cast(p.legacy_key as integer)", "payload_json": "'{}'"}
        fields = [expressions.get(field, "a.memory_" + field) + " as " + field for field in _MEMORY_ORDER]
        join = "left join actions p on p.id=a.memory_attempt_ref and p.record_type='reply_attempts'"
    return (f"select {','.join(fields)} from actions a {join} where a.record_type='{kind}' "
            f"and a.record_type<>'external_action_results' and a.record_type not in ({_POSTFIX_TYPES_SQL}) "
            "and a.record_type<>'sent_reply_observers'")


# Both canonical E declarations contain the 54 current Attempt fields. This
# explicit projection omits only the four preserved actual-source history fields.
REPLY_ATTEMPT_COMMON_SQL = current_reply_projection("reply_attempts", common=True)

# Read candidate status from its existing index before resolving virtual
# identity fields from the full Attempt payload.
REPLY_ATTEMPT_PROBLEM_TRIGGER_KEYS_SQL = """
    select a.attempt_channel as channel,
           a.attempt_conversation_id as conversation_id,
           a.attempt_trigger_message_id as trigger_message_id
    from actions a indexed by idx_actions_reply_status
    where a.record_type='reply_attempts'
      and lower(a.attempt_send_status) in ('failed','blocked','dry_run','needs_human')
"""



REVIEW_CANDIDATE_SQL = ACTION_PROJECTIONS["review_candidates"]
CANDIDATE_REVIEW_SQL = ACTION_PROJECTIONS["candidate_reviews"]
CANDIDATE_SELECTION_SQL = _projection("candidate_selections", selection_references=True)
CANDIDATE_SUPPLEMENT_SQL = ACTION_PROJECTIONS["candidate_supplements"]
CANDIDATE_EXECUTION_SQL = execution_projection()
NATIVE_CANDIDATE_ACTION_ATTEMPT_SQL = """select r.id,a.id as action_owner_id,
       r.execution_run_id as execution_id,r.action_index,
       a.action_external_action_key as external_action_key,r.status,
       coalesce(r.outcome_json,p.provider_result_json,'{}') as result_json,
       r.agent_run_id,r.provider_receipt_id,r.target,r.created_at,r.updated_at
from action_receipts r join actions a on a.id=r.action_id
left join action_receipts p on p.id=r.provider_receipt_id and p.action_id=r.action_id
where a.record_type='action' and a.action_external_action_key is not null
  and r.execution_run_id is not null and r.action_index is not null
  and r.id=(select max(latest.id) from action_receipts latest
            where latest.action_id=r.action_id
              and latest.execution_run_id=r.execution_run_id
              and latest.action_index=r.action_index)"""


class CandidateResultReferenceError(sqlite3.IntegrityError):
    """A stored result pointer has no matching receipt owner."""


def candidate_action_attempt_row(row):
    """Return the owned result projection, refusing a broken receipt reference."""
    if row is None:
        return None
    result = dict(row)
    if result["result_json"] is None:
        raise CandidateResultReferenceError("candidate action result reference lacks its receipt owner")
    return result



CANONICAL_PROVIDER_RECEIPT_SQL = """select a.id as action_id,metadata.id as binding_id,
       body.id as receipt_id,coalesce(metadata.operation,a.action_operation) as operation,
       metadata.target,metadata.result_digest,
       metadata.agent_run_id,metadata.created_at
from actions a join action_receipts metadata on metadata.action_id=a.id
join action_receipts body on body.id=metadata.provider_receipt_id and body.action_id=a.id
where a.record_type='action' and metadata.provider_result_json is null
  and metadata.provider_receipt_id is not null and metadata.status is null
  and metadata.attempt_number is null and metadata.execution_id is null
  and metadata.action_index is null and metadata.target is not null
  and metadata.agent_run_id is not null and metadata.created_at is not null
  and metadata.id is not a.action_delivery_receipt_id
  and metadata.id is not a.action_prepared_receipt_id
  and body.provider_result_json is not null"""

EXTERNAL_ACTION_RESULT_SQL = f"""select a.action_external_action_key as external_action_key,
       a.action_business_object_key as business_object_key,
       a.action_action_identity as action_identity,canonical.operation,
       canonical.target as target_identifiers_json,r.provider_result_json,
       coalesce(r.result_digest,canonical.result_digest) as result_digest,
       canonical.agent_run_id as first_agent_run_id,
       canonical.created_at,r.id as provider_receipt_id
from actions a join action_receipts r on r.action_id=a.id
join ({CANONICAL_PROVIDER_RECEIPT_SQL}) canonical
  on canonical.action_id=a.id and canonical.receipt_id=r.id
where a.record_type='action' and a.action_external_action_key is not null
  and a.action_business_object_key is not null and a.action_action_identity is not null
  and canonical.operation is not null
  and canonical.target is not null
  and coalesce(r.result_digest,canonical.result_digest) is not null
  and canonical.agent_run_id is not null
  and r.provider_result_json is not null"""
NATIVE_EXTERNAL_ACTION_RESULT_SQL = EXTERNAL_ACTION_RESULT_SQL.replace(
    CANONICAL_PROVIDER_RECEIPT_SQL,
    CANONICAL_PROVIDER_RECEIPT_SQL.replace("metadata.execution_id", "metadata.execution_run_id"),
)
AGENT_EFFECT_INTENT_SQL = ACTION_PROJECTIONS["agent_effect_intents"]
AGENT_EXECUTION_RECEIPT_SQL = ACTION_PROJECTIONS["agent_execution_receipts"]
# Task 3 runtime projection: prepared message data and its dispatch/accepted
# provider facts are read from the concrete Action owner and its normalized
# receipts. Historical source factories remain separate for migration only.
OUTBOUND_POSTFIX_SQL = """select id,action_delivery_channel as channel,
       action_delivery_key as delivery_key,action_prepared_body as final_body,
       action_feedback_token as feedback_token,action_postfix_version as postfix_version,
       action_prepared_at as created_at
from actions where record_type='action' and action_delivery_channel is not null
  and action_delivery_key is not null"""
OUTBOUND_POSTFIX_RECEIPT_SQL = """select a.action_delivery_channel as channel,
       a.action_delivery_key as delivery_key,r.provider_result_json,
       r.observed_at as created_at
from actions a join action_receipts r on r.id=a.action_prepared_receipt_id
where a.record_type='action' and a.action_delivery_channel is not null
  and a.action_delivery_key is not null and r.action_id=a.id
  and r.provider_result_json is not null"""
NATIVE_REPLY_DISPATCH_SQL = """select a.action_delivery_channel as channel,
       a.action_delivery_key as delivery_key,r.dispatched_at as created_at
from action_receipts r join actions a on a.id=r.action_id
where a.record_type='action' and a.action_delivery_channel is not null
  and a.action_delivery_key is not null and r.operation='send_reply_to_trigger'
  and r.dispatched_at is not null"""
SENT_REPLY_SQL = """select a.id,a.action_conversation_id as conversation_id,
       a.action_trigger_message_id as trigger_message_id,
       coalesce(a.action_prepared_body,'') as reply_text,
       coalesce(body.provider_result_json,'') as send_result_json,
       fact.id as receipt_id, fact.operation as operation,
       fact.target as target, fact.result_digest as result_digest,
       body.id as provider_receipt_id, body.provider_result_id as provider_result_id,
       coalesce(a.action_recall_key,'') as recall_key,
       coalesce(a.action_recall_status,'') as recall_status,
       coalesce(a.action_recall_error,'') as recall_error,a.action_recalled_at as recalled_at,
       coalesce(a.action_feedback_token,'') as feedback_token,
       coalesce(a.action_agent_run_id,0) as agent_run_id,
       coalesce(a.action_external_action_key,'') as external_action_key,
       coalesce(a.action_delivery_channel,'dingtalk') as channel,
       fact.observed_at as sent_at
from actions a join action_receipts fact on fact.id=a.action_delivery_receipt_id
left join action_receipts body on body.id=fact.provider_receipt_id and body.action_id=a.id
where a.record_type='action'
  and a.action_conversation_id is not null and a.action_trigger_message_id is not null"""
SENT_REPLY_OBSERVER_SQL = """select o.action_id as sent_reply_id,o.agent_run_id,o.task_id as reply_task_id
from action_observers o join actions a on a.id=o.action_id
where a.record_type='action' and a.action_conversation_id is not null
  and a.action_trigger_message_id is not null"""


def create_action_schema(db: sqlite3.Connection, *, include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None, include_email=False, include_followup_sync=False, include_skill_config_receipts=False, include_business_sync=False, include_task_delivery=False, include_errors=False, include_feedback=False, include_business_actions=False, include_email_history=False, canonical_email=False, inline_legacy_owner=False, physical_provenance=False, compact_payload=False, selection_references=False, result_references=False, execution_owner=False, sent_result_references=False, shared_lifecycle=False, specialized_claims=False, postfix_owner=False, logical_owner=False) -> None:
    """Create the declared schema, or repair only its missing auxiliary objects."""
    existing = db.execute("select 1 from sqlite_master where name='actions' collate nocase").fetchone()
    if existing:
        if not action_schema_is_current(db, allow_missing_objects=True, include_receipts=include_receipts, include_postfix=include_postfix, include_delivery=include_delivery, include_reply=include_reply, reply_variant=reply_variant, include_email=include_email, include_followup_sync=include_followup_sync, include_skill_config_receipts=include_skill_config_receipts, include_business_sync=include_business_sync, include_task_delivery=include_task_delivery, include_errors=include_errors, include_feedback=include_feedback, include_business_actions=include_business_actions, include_email_history=include_email_history, canonical_email=canonical_email, inline_legacy_owner=inline_legacy_owner, physical_provenance=physical_provenance, compact_payload=compact_payload, selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner):
            raise ValueError("unsupported current actions schema declaration")
        names = {sqlite_schema_name(obj["name"]) for obj in _objects(db, "actions")}
        for obj in _current_objects(include_receipts, include_postfix, include_delivery, include_reply, reply_variant, include_email, include_followup_sync, include_skill_config_receipts, include_business_sync, include_task_delivery, include_errors, include_feedback, include_business_actions, include_email_history, inline_legacy_owner, physical_provenance, compact_payload, canonical_email, selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner):
            if sqlite_schema_name(obj["name"]) not in names:
                db.execute(obj["sql"])
    else:
        if any(db.execute("select 1 from sqlite_master where name=? collate nocase", (kind,)).fetchone() for kind in ACTION_TYPES):
            capture_action_schema(db)
        _require_available_names(db, _current_objects(include_receipts, include_postfix, include_delivery, include_reply, reply_variant, include_email, include_followup_sync, include_skill_config_receipts, include_business_sync, include_task_delivery, include_errors, include_feedback, include_business_actions, include_email_history, inline_legacy_owner, physical_provenance, compact_payload, canonical_email, selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner))
        _create_action_objects(db, include_receipts, include_postfix, include_delivery, include_reply, reply_variant, include_email, include_followup_sync, include_skill_config_receipts, include_business_sync, include_task_delivery, include_errors, include_feedback, include_business_actions, include_email_history, inline_legacy_owner, physical_provenance, compact_payload, canonical_email, selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner)


def _create_action_objects(db, include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None, include_email=False, include_followup_sync=False, include_skill_config_receipts=False, include_business_sync=False, include_task_delivery=False, include_errors=False, include_feedback=False, include_business_actions=False, include_email_history=False, inline_legacy_owner=False, physical_provenance=False, compact_payload=False, canonical_email=False, *, selection_references=False, result_references=False, execution_owner=False, sent_result_references=False, shared_lifecycle=False, specialized_claims=False, postfix_owner=False, logical_owner=False):
    if (include_receipts or include_postfix or include_delivery or include_reply) and not db.execute("select 1 from sqlite_master where type='table' and name='sqlite_sequence'").fetchone():
        # SQLite creates its native sequence metadata on the first declaration
        # of an AUTOINCREMENT table. The initializer has no rows and is removed;
        # actions retains its ordinary physical INTEGER PRIMARY KEY allocator.
        name = "_actions_sequence_initializer"
        while db.execute("select 1 from sqlite_master where name=? collate nocase", (name,)).fetchone():
            name += "_"
        db.execute(f'create table "{name}"(id integer primary key autoincrement)')
        db.execute(f'drop table "{name}"')
    table, indexes, triggers = _action_definitions(include_receipts, include_postfix, include_delivery, include_reply, reply_variant, include_email, include_followup_sync, include_skill_config_receipts, include_business_sync, include_task_delivery, include_errors, include_feedback, include_business_actions, include_email_history, inline_legacy_owner, physical_provenance, compact_payload, selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner)
    if canonical_email:
        if not include_email or not include_reply or reply_variant is None:
            raise ValueError("canonical Email requires the complete current Actions variant")
        table = table.replace("references email_classifications(id)", "references emails(classification_id)")
    for sql in (table, *indexes, *triggers):
        db.execute(sql)


@lru_cache(maxsize=16)
def _current_objects(include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None, include_email=False, include_followup_sync=False, include_skill_config_receipts=False, include_business_sync=False, include_task_delivery=False, include_errors=False, include_feedback=False, include_business_actions=False, include_email_history=False, inline_legacy_owner=False, physical_provenance=False, compact_payload=False, canonical_email=False, *, selection_references=False, result_references=False, execution_owner=False, sent_result_references=False, shared_lifecycle=False, specialized_claims=False, postfix_owner=False, logical_owner=False):
    with closing(sqlite3.connect(":memory:")) as db:
        _create_action_objects(db, include_receipts, include_postfix, include_delivery, include_reply, reply_variant, include_email, include_followup_sync, include_skill_config_receipts, include_business_sync, include_task_delivery, include_errors, include_feedback, include_business_actions, include_email_history, inline_legacy_owner, physical_provenance, compact_payload, canonical_email, selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner)
        return _objects(db, "actions")



def action_schema_is_current(db: sqlite3.Connection, *, allow_missing_objects=False, include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None, include_email=False, include_followup_sync=False, include_skill_config_receipts=False, include_business_sync=False, include_task_delivery=False, include_errors=False, include_feedback=False, include_business_actions=False, include_email_history=False, canonical_email=False, inline_legacy_owner=False, physical_provenance=False, compact_payload=False, selection_references=False, result_references=False, execution_owner=False, sent_result_references=False, shared_lifecycle=False, specialized_claims=False, postfix_owner=False, logical_owner=False, expected_objects=None) -> bool:
    kinds = ALL_ACTION_TYPES_WITH_REPLY if include_reply else ALL_ACTION_TYPES_WITH_DELIVERY if include_delivery else ALL_ACTION_TYPES_WITH_POSTFIX if include_postfix else ALL_ACTION_TYPES if include_receipts else ACTION_TYPES
    if execution_owner:
        kinds += (EXECUTION_ALIAS,)
    if include_email:
        kinds += EMAIL_TYPES_ORDER
    if include_followup_sync:
        kinds += SYNC_TYPES_ORDER
    if include_task_delivery:
        kinds += TASK_DELIVERY_KINDS
    if include_skill_config_receipts:
        kinds += SKILL_RECEIPT_KINDS
    if include_errors:
        kinds += ("errors",)
    if include_feedback:
        kinds += FEEDBACK_KINDS
    if include_business_actions:
        kinds += BUSINESS_ACTION_KINDS
    if include_email_history:
        kinds += HISTORY_KINDS
    if any(db.execute("select 1 from sqlite_master where name=? collate nocase", (kind,)).fetchone() for kind in kinds):
        return False
    actual = {sqlite_schema_name(o["name"]): o for o in _objects(db, "actions")}
    expected_objects = expected_objects if expected_objects is not None else _current_objects(include_receipts, include_postfix, include_delivery, include_reply, reply_variant, include_email, include_followup_sync, include_skill_config_receipts, include_business_sync, include_task_delivery, include_errors, include_feedback, include_business_actions, include_email_history, inline_legacy_owner, physical_provenance, compact_payload, canonical_email, selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner, logical_owner=logical_owner)
    expected = {sqlite_schema_name(o["name"]): o for o in expected_objects}
    if any(db.execute("select 1 from sqlite_master where name=? collate nocase and tbl_name<>'actions' collate nocase", (name,)).fetchone() for name in expected):
        return False
    return not (actual.keys() - expected.keys()) and all(
        actual.get(name) == obj or (allow_missing_objects and name != "actions" and name not in actual)
        for name, obj in expected.items()
    )


def _require_kind(kind):
    if kind not in SOURCE_COLUMNS and kind not in REPLY_ACTION_TYPES and kind not in EMAIL_TYPES_ORDER and kind != EXECUTION_ALIAS:
        raise ValueError("unknown action record type")


def _identity_predicate(kind):
    if kind == "sent_reply_observers":
        return ("record_type=? and sent_reply_id=(select id from actions where "
                "record_type='sent_replies' and record_type<>'external_action_results' "
                f"and record_type not in ({_POSTFIX_TYPES_SQL}) "
                "and record_type<>'sent_reply_observers' and cast(legacy_key as integer)=?) "
                "and agent_run_id=?")
    if kind in POSTFIX_ACTION_TYPES:
        return "record_type=? and channel=? and delivery_key=?"
    if kind == "external_action_results":
        return "record_type=? and legacy_key=?"
    return ("record_type=? and record_type<>'action' and record_type<>'external_action_results' "
            f"and record_type not in ({_POSTFIX_TYPES_SQL}) "
            "and record_type<>'sent_reply_observers' and cast(legacy_key as integer)=?")


def _identity_arguments(kind, public_id):
    if kind == "sent_reply_observers":
        if type(public_id) is not tuple or len(public_id) != 2 or any(type(value) is not int for value in public_id):
            raise ValueError("observer public identity must be a (sent_reply_id, agent_run_id) tuple")
        return (kind, *public_id)
    if kind in POSTFIX_ACTION_TYPES:
        if type(public_id) is not tuple or len(public_id) != 2 or any(type(value) is not str for value in public_id):
            raise ValueError("postfix public identity must be a (channel, delivery_key) tuple")
        return (kind, *public_id)
    return (kind, public_id)


def action_physical_id(db: sqlite3.Connection, record_type: str, public_id: ActionIdentity | None) -> int | None:
    _require_kind(record_type)
    if public_id is None:
        return None
    row = db.execute(f"select id from actions where {_identity_predicate(record_type)}", _identity_arguments(record_type, public_id)).fetchone()
    if row is None:
        raise sqlite3.IntegrityError(f"missing {record_type} public ID {public_id}")
    return row[0]


def _parent_id(db, field, public_id):
    if public_id is None:
        return None
    table, kind = (REFERENCES | DELIVERY_REFERENCES | EXECUTION_REFERENCES)[field]
    if table == "actions":
        return action_physical_id(db, kind, public_id)
    row = db.execute(f"select id from {table} where record_type=? and cast(legacy_key as integer)=?", (kind, public_id)).fetchone()
    if row is None:
        raise sqlite3.IntegrityError(f"missing {kind} public ID {public_id}")
    return row[0]


def _check_fields(kind, values):
    _require_kind(kind)
    if kind == EXECUTION_ALIAS:
        raise ValueError('execution alias writes belong to the plan execution transaction')
    if kind in EMAIL_TYPES_ORDER:
        if set(values) - set(EMAIL_FIELDS[kind]):
            raise ValueError(f"unknown {kind} source fields")
        return
    if kind in REPLY_ACTION_TYPES:
        allowed = set(_REPLY_FIELDS[kind]) | {"id"}
        if kind == "memory_write_events":
            allowed.add("payload_json")
        if set(values) - allowed:
            raise ValueError(f"unknown {kind} source fields")
        return
    if set(values) - set(SOURCE_COLUMNS[kind]):
        raise ValueError(f"unknown {kind} source fields")
    for field, value in values.items():
        if value is not None and type(value) is not (int if field == "id" or field in REFERENCES or field in DELIVERY_REFERENCES or field in INTEGER_FIELDS else str):
            raise ValueError(f"unrepresentable {kind}.{field} storage type")
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise ValueError(f"unrepresentable {kind}.{field} integer")


@dataclass(frozen=True)
class ActionWriteResult:
    lastrowid: ActionIdentity | None
    rowcount: int


def _autoincrement_high_water(db, kind):
    name = (f"actions:{kind}" if kind in EMAIL_AUTOINCREMENT_TYPES or kind in SYNC_TYPES_ORDER or kind in TASK_DELIVERY_AUTOINCREMENT or kind == "errors" else
            REPLY_SEQUENCE_NAMES[kind] if kind in REPLY_ACTION_TYPES else
            DELIVERY_SEQUENCE_NAME if kind == "sent_replies" else ACTION_SEQUENCE_NAMES[kind])
    rows = db.execute("select seq from main.sqlite_sequence where name=?", (name,)).fetchall()
    if len(rows) > 1 or any(type(row[0]) is not int or row[0] < 0 for row in rows):
        raise ValueError(f"invalid {kind} allocator metadata")
    maximum = (_email_max_id(db, kind) if kind in EMAIL_AUTOINCREMENT_TYPES or kind in SYNC_TYPES_ORDER or kind in TASK_DELIVERY_AUTOINCREMENT else
               db.execute("select max(cast(legacy_key as integer)) from actions where record_type=?", (kind,)).fetchone()[0])
    return max(rows[0][0] if rows else 0, maximum or 0)


def _raise_autoincrement_full():
    # Preserve SQLite's actual SQLITE_FULL exception rather than substituting
    # a constraint error or reusing an ID when the native allocator is spent.
    with closing(sqlite3.connect(":memory:")) as allocator:
        allocator.execute("create table ids(id integer primary key autoincrement)")
        allocator.execute("insert into ids values(9223372036854775807)")
        allocator.execute("insert into ids default values")


def _reply_variant_for(db):
    columns = {row[1] for row in db.execute("pragma table_xinfo(actions)")}
    if "memory_attempt_ref" in columns and "memory_attempt_id" not in columns:
        return "fresh_memory_fk"
    if "memory_attempt_id" in columns and "memory_attempt_ref" not in columns:
        return "actual_no_memory_fk"
    raise ValueError("unsupported reply actions declaration")


def _audit_markers(value):
    if isinstance(value, memoryview):
        value = bytes(value)
    if not isinstance(value, (str, bytes, bytearray)):
        return "[]"
    try:
        events = json.loads(value)
    except (ValueError, TypeError, UnicodeError):
        return "[]"
    if not isinstance(events, list):
        return "[]"
    marker = {"tool": "audit_review", "result": "queued"}
    return json.dumps([marker for item in events if item == marker], separators=(",", ":"))


def _reply_parent(db, public_id):
    row = db.execute("select id from actions where " + _identity_predicate("reply_attempts"),
                     _identity_arguments("reply_attempts", public_id)).fetchone()
    if row is None:
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")
    return row[0]


def _source_bound_null(value):
    return value is None or isinstance(value, float) and math.isnan(value)


def _reply_auto_id(values):
    return _source_bound_null(values.get("id"))


def _native_integer_primary_key(value, *, update=False):
    # A scratch SQLite INTEGER PRIMARY KEY supplies SQLite's actual assignment
    # affinity (2.0, '2.0', booleans, BLOBs), without a Python numeric parser.
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise OverflowError("Python int too large to convert to SQLite INTEGER")
        return value
    with closing(sqlite3.connect(":memory:")) as source_key:
        source_key.execute("create table ids(id integer primary key)")
        if update:
            source_key.execute("insert into ids(id) values(1)")
            source_key.execute("update ids set id=?", (value,))
            return source_key.execute("select id from ids").fetchone()[0]
        return source_key.execute("insert into ids(id) values(?) returning id", (value,)).fetchone()[0]


def _encoded_sqlite_value(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"t": "blob", "v": bytes(value).hex()}
    if isinstance(value, int):
        # sqlite3 binds the underlying PyLong, ignoring subclass conversions.
        if not -(2**63) <= int.__int__(value) < 2**63:
            raise OverflowError("Python int too large to convert to SQLite INTEGER")
        return value
    if isinstance(value, float) and math.isnan(value):
        # sqlite3 binds NaN as NULL before the source table applies NOT NULL.
        return None
    if value == float("inf"):
        return {"t": "inf"}
    if value == float("-inf"):
        return {"t": "-inf"}
    if value is None or isinstance(value, (str, float)):
        return value
    raise sqlite3.ProgrammingError("unsupported SQLite source value")


def _encoded_native_sqlite_value(value):
    """Encode the underlying scalar that sqlite3 binds for native-source kinds."""
    if isinstance(value, float):
        value = float.__float__(value)
    elif isinstance(value, str):
        value = str.__str__(value)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        view = memoryview(value)
        if not view.c_contiguous:
            raise BufferError("memoryview: underlying buffer is not C-contiguous")
        return {"t": "blob", "v": view.tobytes().hex()}
    elif value is not None and not isinstance(value, int):
        raise sqlite3.ProgrammingError("unsupported SQLite source value")
    if isinstance(value, float) and math.isfinite(value):
        numerator, denominator = value.as_integer_ratio()
        shift = max(0, abs(numerator).bit_length() - 53)
        significand = numerator >> shift
        exponent = shift - (denominator.bit_length() - 1)
        if value == 0.0:
            significand, exponent = math.copysign(0.0, value), 0
        return {"t": "real", "m": significand, "e": exponent}
    return _encoded_sqlite_value(value)


def _native_payload(values):
    return json.dumps({field: _encoded_native_sqlite_value(value)
                       for field, value in values.items()},
                      ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _action_layout(kind):
    from app.current_schema_metadata import DEFAULT_WRITER_FIELDS
    return PayloadLayout(DEFAULT_WRITER_FIELDS[kind])


def _action_path(kind, field):
    return _action_layout(kind).path(field)


def _action_payload(db, kind, values, *, layout=None):
    """Bind current JSONB cells using SQLite on the caller's connection."""
    layout = layout or _action_layout(kind)
    packed = layout.pack(dict.fromkeys(layout.fields) | values,
                         scalar_codec=_encoded_native_sqlite_value)
    return db.execute("select jsonb(?)", (packed,)).fetchone()[0]


def _reply_payload(values):
    return json.dumps({field: _encoded_sqlite_value(value) for field, value in values.items()},
                      ensure_ascii=False, allow_nan=False, separators=(",", ":"))


_EMAIL_ROWID_UNSET = object()
def _email_max_id(db, kind):
    # Match the existing numeric public-ID index's partial predicate so SQLite
    # can seek its last entry for this source family instead of scanning it.
    return db.execute(
        "select max(cast(legacy_key as integer)) from actions where record_type=? and "
        + _EMAIL_NUMERIC_SCOPE, (kind,),
    ).fetchone()[0]


def _email_lock(db, bound=()):
    # Bind first, then acquire SQLite's writer lock before reading a locator or
    # payload. The no-row UPDATE preserves the caller's transaction boundary.
    db.execute("update main.sqlite_sequence set seq=seq where 0" + " and ? is null" * len(bound), bound)


def _email_owned(db):
    return not db.in_transaction and (db.isolation_level is None or getattr(db, "autocommit", None) is True)


def _email_unit(db, name, operation):
    owned = _email_owned(db)
    if owned:
        db.execute(f"savepoint {name}")
    try:
        result = operation()
    except BaseException:
        if owned and db.in_transaction:
            db.execute(f"rollback to {name}")
            db.execute(f"release {name}")
        raise
    if owned:
        db.execute(f"release {name}")
    return result


def insert_error_action(db: sqlite3.Connection, values: dict, *, resolved_at_now=False) -> ActionWriteResult:
    """Insert one native Error in its public AUTOINCREMENT namespace."""
    if set(values) - {"id", *ERROR_PAYLOAD_FIELDS}:
        raise ValueError("unknown error source fields")

    def operation():
        _email_lock(db, tuple(values.values()))
        if _source_bound_null(values.get("id")):
            high = _autoincrement_high_water(db, "errors")
            if high == 2**63 - 1:
                _raise_autoincrement_full()
            public_id = high + 1
        else:
            public_id = _native_integer_primary_key(values["id"])
        created = values.get("created_at")
        payload = _action_payload(db, "errors", {
            field: (created if field == "created_at" else values.get(field, "")
                    if field in ("resolved_at", "resolution") else values.get(field))
            for field in ERROR_PAYLOAD_FIELDS
        })
        payload_expression = "?"
        if "created_at" not in values:
            payload_expression = "jsonb_set(" + payload_expression + "," + repr(_action_path("errors", "created_at")) + ",json(ceo_native_json(current_timestamp)))"
        if resolved_at_now:
            payload_expression = "jsonb_set(" + payload_expression + "," + repr(_action_path("errors", "resolved_at")) + ",json(ceo_native_json(current_timestamp)))"
        cursor = db.execute(
            "insert into actions(_kind,legacy_key,payload_json) "
            f"values({ACTION_RECORD_TYPES.code('errors')},?,{payload_expression}) returning cast(legacy_key as integer)",
            (str(public_id), payload),
        )
        row = cursor.fetchone()
        cursor.fetchall()
        return ActionWriteResult(row[0], cursor.rowcount)

    return _email_unit(db, "action_error_insert", operation)


def error_set_parts(values: dict | None = None, *, update_sql: dict | None = None,
                    update_parameters=()) -> tuple[str, tuple]:
    """Encode finite Error SET fields, keeping caller predicates in one UPDATE."""
    values = values or {}
    expressions = update_sql or {}
    if not values and not expressions or set(values) & set(expressions):
        raise ValueError("invalid error update fields")
    if (set(values) | set(expressions)) - {"id", *ERROR_PAYLOAD_FIELDS}:
        raise ValueError("unknown error update fields")
    if "id" in expressions:
        raise ValueError("error SQL primary-key update is unsupported")
    assignments, arguments, paths = [], [], []
    if "id" in values:
        assignments.append("legacy_key=?")
        arguments.append(str(_native_integer_primary_key(values["id"], update=True)))
    for field, value in values.items():
        if field == "id":
            continue
        paths.extend((_action_path("errors", field), json.dumps(_encoded_native_sqlite_value(value),
                                               ensure_ascii=False, allow_nan=False,
                                               separators=(",", ":"))))
    updates = ["?,json(?)" for _ in range(len(paths) // 2)]
    updates.extend(repr(_action_path("errors", field)) + ",json(ceo_native_json(" + expression + "))"
                   for field, expression in expressions.items())
    if updates:
        assignments.append("payload_json=jsonb_set(payload_json," + ",".join(updates) + ")")
        arguments.extend(paths)
        arguments.extend(update_parameters)
    return ",".join(assignments), tuple(arguments)


def _email_payload(db, kind, values):
    data = {}
    for field in EMAIL_FIELDS[kind]:
        if (field == "id" and kind in EMAIL_AUTOINCREMENT_TYPES) or field in EMAIL_COMPOSITE_KEYS.get(kind, ()):
            continue
        if field in values:
            data[field] = values[field]
        else:
            default = EMAIL_DEFAULTS[kind].get(field)
            data[field] = db.execute("select " + default).fetchone()[0] if default is not None else None
    return _action_payload(db, kind, data)


def _email_rowid_next(db, kind):
    maximum = _email_max_id(db, kind)
    if maximum == 2**63 - 1:
        with closing(sqlite3.connect(":memory:")) as allocator:
            allocator.execute("create table ids(id integer primary key)")
            allocator.executemany("insert into ids values(?)", db.execute(
                "select cast(legacy_key as integer) from actions where record_type=?", (kind,)
            ))
            return allocator.execute("insert into ids default values").lastrowid
    return (maximum or 0) + 1


def _email_auto_next(db, kind):
    high = _autoincrement_high_water(db, kind)
    if high == 2**63 - 1:
        if db.in_transaction:
            db.execute("rollback")
        _raise_autoincrement_full()
    return high + 1


def _email_identity(kind, identity):
    if kind in EMAIL_COMPOSITE_KEYS:
        if not isinstance(identity, tuple) or len(identity) != 2:
            raise ValueError("Email composite identity requires two components")
        fields = EMAIL_COMPOSITE_KEYS[kind]
        return "record_type=? and " + " and ".join(f"email_{field}=?" for field in fields), (kind, *identity)
    return "record_type=? and " + _EMAIL_NUMERIC_SCOPE + " and cast(legacy_key as integer)=?", (kind, identity)


def _insert_email_action(db, kind, values, *, source_rowid=_EMAIL_ROWID_UNSET, ignore=False,
                         ignore_plan_action_conflict=False, update_continuation_conflict=False):
    payload = _email_payload(db, kind, values)
    if kind in EMAIL_ROWID_TYPES:
        identity = _email_rowid_next(db, kind) if source_rowid is _EMAIL_ROWID_UNSET or _source_bound_null(source_rowid) else _native_integer_primary_key(source_rowid)
    elif kind in EMAIL_AUTOINCREMENT_TYPES:
        identity = _email_auto_next(db, kind) if _source_bound_null(values.get("id")) else _native_integer_primary_key(values["id"])
    else:
        identity = tuple(values.get(field) for field in EMAIL_COMPOSITE_KEYS[kind])
    key = (json.dumps([_encoded_native_sqlite_value(value) for value in identity], ensure_ascii=False,
                      allow_nan=False, separators=(",", ":")) if kind in EMAIL_COMPOSITE_KEYS else str(identity))
    returning = (",".join("email_" + field for field in EMAIL_COMPOSITE_KEYS[kind])
                 if kind in EMAIL_COMPOSITE_KEYS else "legacy_key")
    conflict = (" on conflict(email_action_plan_id,email_action_type) "
                "where record_type='email_actions' do nothing" if ignore_plan_action_conflict else "")
    if update_continuation_conflict:
        conflict = (
            " on conflict(record_type,email_public_key) do update set payload_json="
            + _action_layout(kind).assignments_sql("actions.payload_json", {
                field: "excluded.payload_json->" + repr(_action_path(kind, field))
                for field in ("effect_digest", "observation_reference", "controls_json", "updated_at")
            })
        )
    cursor = db.execute(
        f"insert {'or ignore ' if ignore else ''}into actions(_kind,legacy_key,payload_json) values(?,?,?){conflict} returning {returning}",
        (ACTION_RECORD_TYPES.code(kind), key, payload),
    )
    row = cursor.fetchone()
    cursor.fetchall()
    result_identity = (int(row[0]) if update_continuation_conflict and row else identity)
    return ActionWriteResult((tuple(row) if kind in EMAIL_COMPOSITE_KEYS else result_identity) if row else None,
                             cursor.rowcount)


def insert_email_action(db: sqlite3.Connection, kind: str, values: dict, *, source_rowid=None, ignore=False,
                        ignore_plan_action_conflict=False, update_continuation_conflict=False) -> ActionWriteResult:
    if kind not in EMAIL_ROWID_TYPES:
        raise ValueError("source ROWID is exposed only for the nine rowid families")
    if ignore_plan_action_conflict and (kind != "email_actions" or ignore):
        raise ValueError("plan/type conflict handling is only for an Email operation insert")
    if update_continuation_conflict and (kind != "email_unsubscribe_continuations" or ignore or ignore_plan_action_conflict):
        raise ValueError("continuation conflict handling is only for an Email continuation insert")
    _check_fields(kind, values)
    def operation():
        _email_lock(db, (*values.values(), source_rowid))
        return _insert_email_action(db, kind, values, source_rowid=source_rowid, ignore=ignore,
                                    ignore_plan_action_conflict=ignore_plan_action_conflict,
                                    update_continuation_conflict=update_continuation_conflict)
    return _email_unit(db, "action_email_insert", operation)


def _update_email_action(db, kind, identity, values, *, new_source_rowid=_EMAIL_ROWID_UNSET,
                         where_sql="", parameters=()):
    if not values and new_source_rowid is _EMAIL_ROWID_UNSET:
        raise ValueError("action update requires fields")
    predicate, identity_args = _email_identity(kind, identity)
    bound = (*values.values(), *identity_args, *parameters)
    if new_source_rowid is not _EMAIL_ROWID_UNSET:
        bound += (new_source_rowid,)
    _email_lock(db, bound)
    if where_sql:
        from app.shared_action_definitions import email_projection
        projection = email_projection(kind, include_rowid=kind in EMAIL_ROWID_TYPES)
        predicate += " and exists(select 1 from (" + projection + ") where " + " and ".join(
            f"{field}=?" for field in (EMAIL_COMPOSITE_KEYS[kind] if kind in EMAIL_COMPOSITE_KEYS else
                                       ("rowid",) if kind in EMAIL_ROWID_TYPES else ("id",))
        ) + f" and ({where_sql}))"
        projected_identity = identity if kind in EMAIL_COMPOSITE_KEYS else (identity,)
        identity_args += (*projected_identity, *parameters)
    existing = db.execute("select payload_json from actions where " + predicate, identity_args).fetchone()
    if existing is None:
        return db.execute("update main.sqlite_sequence set seq=seq where 0")
    assignments, args, paths = [], [], []
    if new_source_rowid is not _EMAIL_ROWID_UNSET:
        assignments.append("legacy_key=?")
        args.append(str(_native_integer_primary_key(new_source_rowid, update=True)))
    if kind in EMAIL_AUTOINCREMENT_TYPES and "id" in values:
        assignments.append("legacy_key=?")
        args.append(str(_native_integer_primary_key(values["id"], update=True)))
    if kind in EMAIL_COMPOSITE_KEYS and any(field in values for field in EMAIL_COMPOSITE_KEYS[kind]):
        current = db.execute("select " + ",".join(f"email_{field}" for field in EMAIL_COMPOSITE_KEYS[kind]) +
                             " from actions where " + _email_identity(kind, identity)[0],
                             _email_identity(kind, identity)[1]).fetchone()
        pair = tuple(values.get(field, current[index]) for index, field in enumerate(EMAIL_COMPOSITE_KEYS[kind]))
        assignments.append("legacy_key=?")
        args.append(json.dumps([_encoded_native_sqlite_value(value) for value in pair], ensure_ascii=False,
                               allow_nan=False, separators=(",", ":")))
    for field, value in values.items():
        if (field == "id" and kind in EMAIL_AUTOINCREMENT_TYPES) or field in EMAIL_COMPOSITE_KEYS.get(kind, ()):
            continue
        paths.extend((_action_path(kind, field), json.dumps(_encoded_native_sqlite_value(value), ensure_ascii=False,
                                               allow_nan=False, separators=(",", ":"))))
    if paths:
        assignments.append("payload_json=jsonb_set(payload_json," + ",".join("?,json(?)" for _ in range(len(paths) // 2)) + ")")
        args.extend(paths)
    args.extend(identity_args)
    return db.execute("update actions set " + ",".join(assignments) + " where " + predicate, args)


def update_email_action(db: sqlite3.Connection, kind: str, source_rowid, values: dict, *,
                        new_source_rowid=_EMAIL_ROWID_UNSET) -> sqlite3.Cursor:
    if kind not in EMAIL_ROWID_TYPES:
        raise ValueError("source ROWID is exposed only for the nine rowid families")
    _check_fields(kind, values)
    return _email_unit(db, "action_email_update", lambda: _update_email_action(
        db, kind, source_rowid, values, new_source_rowid=new_source_rowid))


def delete_email_action(db: sqlite3.Connection, kind: str, source_rowid) -> sqlite3.Cursor:
    if kind not in EMAIL_ROWID_TYPES:
        raise ValueError("source ROWID is exposed only for the nine rowid families")
    def operation():
        _email_lock(db, (source_rowid,))
        predicate, args = _email_identity(kind, source_rowid)
        return db.execute("delete from actions where " + predicate, args)
    return _email_unit(db, "action_email_delete", operation)


def _sync_fields(kind, values):
    if kind not in SYNC_TYPES_ORDER:
        raise ValueError(f"unsupported follow-up/Todo kind: {kind}")
    if set(values) - set(CURRENT_SYNC_FIELDS[kind]):
        raise ValueError(f"unknown {kind} source fields")


def _sync_payload(db, kind, values):
    defaults = SYNC_DEFAULTS["actual"][kind]
    missing = tuple(field for field in CURRENT_SYNC_FIELDS[kind] if field in defaults and field not in values)
    evaluated = (dict(zip(missing, db.execute("select " + ",".join(defaults[field] for field in missing)).fetchone(), strict=True))
                 if missing else {})
    return _action_payload(db, kind, {
        field: values[field] if field in values else evaluated.get(field)
        for field in CURRENT_SYNC_FIELDS[kind]
        if field != "id" and not (kind == "business_task_follow_up_send_attempts" and field == "draft_id")
    })


def _sync_parent(db, value):
    if _source_bound_null(value):
        return None
    row = db.execute(
        "select id from actions where record_type='business_task_follow_ups' "
        "and cast(legacy_key as integer)=?", (value,),
    ).fetchone()
    # A BLOB cannot identify SQLite's integer rowid. Keep a non-NULL source
    # reference non-NULL so native CHECK/IGNORE runs before the FK failure.
    return row[0] if row else b""


def insert_sync_action(db: sqlite3.Connection, kind: str, values: dict, *, ignore=False) -> ActionWriteResult:
    """Insert one source-shaped row into the disabled follow-up/Todo layout."""
    _sync_fields(kind, values)

    def operation():
        _email_lock(db, tuple(values.values()))
        payload = _sync_payload(db, kind, values)
        public_id = (_email_auto_next(db, kind) if _source_bound_null(values.get("id"))
                     else _native_integer_primary_key(values["id"]))
        columns = ["_kind", "legacy_key", "payload_json"]
        arguments = [ACTION_RECORD_TYPES.code(kind), str(public_id), payload]
        if kind == "business_task_follow_up_send_attempts":
            columns.append("sync_draft_action_id")
            arguments.append(_sync_parent(db, values.get("draft_id")))
        cursor = db.execute(
            f"insert {'or ignore ' if ignore else ''}into actions({','.join(columns)}) "
            f"values({','.join('?' for _ in arguments)}) returning cast(legacy_key as integer)", arguments,
        )
        row = cursor.fetchone()
        cursor.fetchall()
        return ActionWriteResult(row[0] if row else None, cursor.rowcount)

    return _email_unit(db, "action_sync_insert", operation)


def update_sync_action(db: sqlite3.Connection, kind: str, public_id, values: dict, *,
                       where_sql="", parameters=()) -> sqlite3.Cursor:
    """Update literal source fields, optionally guarded by a projected CAS."""
    _sync_fields(kind, values)
    if not values:
        raise ValueError("action update requires fields")

    def operation():
        _email_lock(db, (*values.values(), public_id, *parameters))
        predicate = "record_type=? and " + _EMAIL_NUMERIC_SCOPE + " and cast(legacy_key as integer)=?"
        identity_args = [kind, public_id]
        if where_sql:
            projection = sync_action_projection(kind, source_variant="actual", fields=CURRENT_SYNC_FIELDS[kind])
            predicate += (" and cast(legacy_key as integer) in "
                          f"(select id from ({projection}) where id=? and ({where_sql}))")
            identity_args.extend((public_id, *parameters))
        if db.execute("select 1 from actions where " + predicate, identity_args).fetchone() is None:
            return db.execute("update main.sqlite_sequence set seq=seq where 0")
        assignments, arguments, paths = [], [], []
        if "id" in values:
            assignments.append("legacy_key=?")
            arguments.append(str(_native_integer_primary_key(values["id"], update=True)))
        if kind == "business_task_follow_up_send_attempts" and "draft_id" in values:
            assignments.append("sync_draft_action_id=?")
            arguments.append(_sync_parent(db, values["draft_id"]))
        for field, value in values.items():
            if field == "id" or kind == "business_task_follow_up_send_attempts" and field == "draft_id":
                continue
            paths.extend((_action_path(kind, field), json.dumps(_encoded_native_sqlite_value(value), ensure_ascii=False,
                                                   allow_nan=False, separators=(",", ":"))))
        if paths:
            assignments.append("payload_json=jsonb_set(payload_json," + ",".join("?,json(?)" for _ in range(len(paths) // 2)) + ")")
            arguments.extend(paths)
        return db.execute("update actions set " + ",".join(assignments) + " where " + predicate,
                          (*arguments, *identity_args))

    return _email_unit(db, "action_sync_update", operation)



def _task_delivery_fields(kind, values):
    if kind not in TASK_DELIVERY_KINDS:
        raise ValueError(f"unsupported task delivery kind: {kind}")
    if set(values) - set(TASK_DELIVERY_FIELDS[kind]):
        raise ValueError(f"unknown {kind} source fields")


def _task_delivery_payload(db, kind, values):
    defaults = TASK_DELIVERY_DEFAULTS[kind]
    parent, _, _ = TASK_DELIVERY_PARENTS[kind]
    identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
    fields = tuple(field for field in TASK_DELIVERY_FIELDS[kind] if field not in (parent, identity))
    missing = tuple(field for field in fields if field not in values and field in defaults)
    evaluated = (dict(zip(missing, db.execute(
        "select " + ",".join(defaults[field] for field in missing)).fetchone(), strict=True))
                 if missing else {})
    return _action_payload(db, kind, {field: values[field] if field in values else evaluated.get(field)
                            for field in fields})


def upsert_meeting_alignment_delivery_claim(
    db: sqlite3.Connection, *, job_id: int, claim_token: str,
):
    """Replace a claim token at its existing public job identity.

    The caller owns the claim transaction. An untargeted UPSERT compiles an
    UPDATE branch for every unrelated Actions unique index; this is the same
    current public identity used by the meeting claim's schema constraint.
    """
    columns, args = task_delivery_insert_parts(
        db, "meeting_alignment_delivery_claims",
        {"job_id": job_id, "claim_token": claim_token},
    )
    return db.execute(
        f"insert into actions({','.join(columns)}) values(?,?,?,?) "
        "on conflict(record_type,cast(legacy_key as integer)) where "
        + _EMAIL_NUMERIC_SCOPE
        + " do update set payload_json=excluded.payload_json",
        args,
    )


def task_delivery_insert_parts(db: sqlite3.Connection, kind: str, values: dict):
    """Build one Action insert; claim writes require an explicit task job ID."""
    _task_delivery_fields(kind, values)
    parent, target, _ = TASK_DELIVERY_PARENTS[kind]
    identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
    if identity == "job_id" and _source_bound_null(values.get(identity)):
        raise ValueError("claim insert requires explicit non-NULL job_id")
    if _source_bound_null(values.get(identity)):
        high = _autoincrement_high_water(db, kind)
        if high == 2**63 - 1:
            # The source BEFORE trigger checks an existing wrong-kind parent
            # before SQLite reports AUTOINCREMENT exhaustion.
            parent_id = values.get(parent)
            expected = TASK_DELIVERY_PARENTS[kind][2]
            if db.execute(
                "select 1 from main.tasks where id=? and record_type<>? limit 1",
                (parent_id, expected),
            ).fetchone():
                db.execute(
                    f"insert into actions(_kind,legacy_key,payload_json,{target}) "
                    "values(?,?,?,?)",
                    (ACTION_RECORD_TYPES.code(kind), str(high), _action_payload(db, kind, {}), parent_id),
                )
            _raise_autoincrement_full()
        public_id = high + 1
    else:
        public_id = _native_integer_primary_key(values[identity])
    parent_id = public_id if parent == identity else values.get(parent)
    return ("_kind", "legacy_key", "payload_json", target), (
        ACTION_RECORD_TYPES.code(kind), str(public_id), _task_delivery_payload(db, kind, values), parent_id,
    )


def insert_task_delivery_action(db: sqlite3.Connection, kind: str, values: dict, *, ignore=False) -> ActionWriteResult:
    """Insert a task delivery fact while preserving its source namespace."""
    _task_delivery_fields(kind, values)

    def operation():
        _email_lock(db, tuple(values.values()))
        columns, arguments = task_delivery_insert_parts(db, kind, values)
        cursor = db.execute(
            f"insert {'or ignore ' if ignore else ''}into actions({','.join(columns)}) "
            f"values({','.join('?' for _ in arguments)}) returning cast(legacy_key as integer)",
            arguments,
        )
        row = cursor.fetchone()
        cursor.fetchall()
        return ActionWriteResult(row[0] if row else None, cursor.rowcount)

    return _email_unit(db, "action_task_delivery_insert", operation)


def task_delivery_set_parts(kind: str, values: dict, *, update_sql=None,
                            update_parameters=()):
    """Build Action SET expressions without altering a caller's SQL statement."""
    _task_delivery_fields(kind, values)
    expressions = {} if update_sql is None else update_sql
    _task_delivery_fields(kind, expressions)
    if not values and not expressions:
        raise ValueError("task delivery update requires fields")
    if set(values) & set(expressions):
        raise ValueError("duplicate task delivery update field")
    parent, target, _ = TASK_DELIVERY_PARENTS[kind]
    identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
    if identity in expressions or parent in expressions:
        raise ValueError("task delivery structural SQL update is unsupported")
    assignments, arguments, paths = [], [], []
    if identity in values:
        assignments.append("legacy_key=?")
        arguments.append(str(_native_integer_primary_key(values[identity], update=True)))
        if parent == identity:
            assignments.append(target + "=?")
            arguments.append(values[identity])
    if parent != identity and parent in values:
        assignments.append(target + "=?")
        arguments.append(values[parent])
    for field, value in values.items():
        if field in (identity, parent):
            continue
        paths.extend((_action_path(kind, field), json.dumps(_encoded_native_sqlite_value(value),
                                               ensure_ascii=False, allow_nan=False,
                                               separators=(",", ":"))))
    payload_updates = ["?,json(?)" for _ in range(len(paths) // 2)]
    payload_updates.extend(repr(_action_path(kind, field)) + ",json(ceo_native_json(" + expression + "))"
                           for field, expression in expressions.items())
    if payload_updates:
        assignments.append("payload_json=jsonb_set(payload_json," + ",".join(payload_updates) + ")")
        arguments.extend(paths)
        arguments.extend(update_parameters)
    return ",".join(assignments), tuple(arguments)





def _insert_reply_action(db, kind, values, *, ignore=False):
    from app.current_action_schema import current_action_payloads

    now = db.execute("select current_timestamp").fetchone()[0]
    if kind == "reply_attempts":
        data = _ATTEMPT_DEFAULTS | {"created_at": now, "updated_at": now} | {field: value for field, value in values.items() if field in _REPLY_FIELDS[kind]}
        data["audit_tool_events_json"] = _audit_markers(data["audit_tool_events_json"])
    else:
        data = _MEMORY_DEFAULTS | {"created_at": now, "updated_at": now} | {field: value for field, value in values.items() if field in _REPLY_FIELDS[kind]}
    attempt_id = data.pop("attempt_id", None) if kind == "memory_write_events" else None
    payload_json = _action_payload(db, kind, data, layout=current_action_payloads().layouts[kind])
    if _reply_auto_id(values):
        maximum = _autoincrement_high_water(db, kind)
        if maximum == 2**63 - 1:
            if db.in_transaction:
                db.execute("rollback")
            _raise_autoincrement_full()
        public_id = maximum + 1
    else:
        public_id = _native_integer_primary_key(values["id"])
    invalid_owner_field = next((field for field in ("codex_reason", "codex_session_id",
        "codex_transcript_start_line", "codex_transcript_end_line")
        if kind == "reply_attempts" and field in values and _source_bound_null(values[field])), None)
    duplicate = ignore and db.execute("select 1 from actions where record_type=? and cast(legacy_key as integer)=?", (kind, public_id)).fetchone()
    if ignore and (duplicate or invalid_owner_field):
        # Source INSERT OR IGNORE resolves its public-key uniqueness before FK
        # evaluation, but still advances AUTOINCREMENT on an explicit ID.
        # A legal public-key UPDATE can put this ID above the saved sequence.
        name = REPLY_SEQUENCE_NAMES[kind]
        db.execute("insert into main.sqlite_sequence(name,seq) select ?,0 where not exists(select 1 from main.sqlite_sequence where name=?)", (name, name))
        db.execute("update main.sqlite_sequence set seq=max(seq,?) where name=?", (public_id, name))
        return ActionWriteResult(None, 0)
    if invalid_owner_field:
        raise sqlite3.IntegrityError(f"NOT NULL constraint failed: reply_attempts.{invalid_owner_field}")
    if kind == "reply_attempts":
        from app.attempt_run_ownership import normalize_payload
        payload_json = normalize_payload(db, payload_json)
    parent_ref = None
    if kind == "memory_write_events":
        required = (attempt_id, *(data.get(field) for field in ("event_type", "status", "attempts",
                                                          "last_error", "memory_episode_id", "created_at", "updated_at")))
        if all(not _source_bound_null(value) for value in required):
            parent_ref = _reply_parent(db, attempt_id)
    columns = ["_kind", "legacy_key", "payload_json"]
    arguments = [ACTION_RECORD_TYPES.code(kind), str(public_id), payload_json]
    if kind == "memory_write_events":
        columns.append("memory_attempt_ref")
        arguments.append(parent_ref)
    cursor = db.execute(
        f"insert {'or ignore ' if ignore else ''}into actions({','.join(columns)}) "
        f"values({','.join('?' for _ in arguments)}) returning cast(legacy_key as integer)", arguments,
    )
    row = cursor.fetchone()
    cursor.fetchall()
    return ActionWriteResult(row[0] if row else None, cursor.rowcount)


def _update_reply_action(db, kind, public_id, values, *, where_sql="", parameters=()):
    from app.current_action_schema import current_action_payloads, current_reply_action_projection

    if not values or kind == "memory_write_events" and set(values) == {"payload_json"}:
        raise ValueError("action update requires fields")
    predicate = _identity_predicate(kind)
    identity_arguments = list(_identity_arguments(kind, public_id))
    if where_sql:
        projection = current_reply_action_projection(kind)
        predicate += f" and cast(legacy_key as integer) in (select id from ({projection}) where id=? and ({where_sql}))"
        identity_arguments.extend((public_id, *parameters))
    # Bind through a write statement: SQLite opens its deferred transaction,
    # binds every SET/WHERE input, then takes the writer lock when it executes.
    # The zero-row write keeps that lock through the scan and actual UPDATE.
    bound = (*values.values(), *identity_arguments)
    db.execute("update main.sqlite_sequence set seq=seq where 0" + " and ? is null" * len(bound), bound)
    if db.execute(f"select 1 from actions where {predicate} limit 1", identity_arguments).fetchone() is None:
        return db.execute("update main.sqlite_sequence set seq=seq where 0")
    if kind == "reply_attempts":
        for field in ("codex_reason", "codex_session_id", "codex_transcript_start_line", "codex_transcript_end_line"):
            if field in values and _source_bound_null(values[field]):
                raise sqlite3.IntegrityError(f"NOT NULL constraint failed: reply_attempts.{field}")
    assignments, arguments, paths = [], [], []
    if kind == "reply_attempts":
        from app.attempt_run_ownership import materialize_payload, normalize_payload
        existing = db.execute(f"select payload_json from actions where {predicate} limit 1", identity_arguments).fetchone()[0]
        payload = materialize_payload(db, existing)
    for field, value in values.items():
        if field == "id":
            column = "legacy_key"
            value = str(_native_integer_primary_key(value, update=True))
        elif kind == "memory_write_events" and field == "payload_json":
            continue
        elif kind == "memory_write_events" and field == "attempt_id":
            column, value = "memory_attempt_ref", _reply_parent(db, value)
        elif kind == "reply_attempts" and field == "audit_tool_events_json":
            paths.extend((current_action_payloads().path(kind, "audit_tool_events_json"), json.dumps(_audit_markers(value))))
            continue
        else:
            paths.extend((current_action_payloads().path(kind, field), json.dumps(_encoded_native_sqlite_value(value), ensure_ascii=False,
                                                   allow_nan=False, separators=(",", ":"))))
            continue
        assignments.append(column + "=?")
        arguments.append(value)
    if paths:
        if kind == "reply_attempts":
            payload = db.execute("select jsonb_set(?," + ",".join("?,json(?)" for _ in range(len(paths) // 2)) + ")", (payload, *paths)).fetchone()[0]
            assignments.append("payload_json=?")
            arguments.append(normalize_payload(db, payload))
        else:
            assignments.append("payload_json=jsonb_set(payload_json," + ",".join("?,json(?)" for _ in range(len(paths) // 2)) + ")")
            arguments.extend(paths)
    if not assignments:
        raise ValueError("action update requires fields")
    arguments.extend(identity_arguments)
    return db.execute(f"update actions set {','.join(assignments)} where {predicate}", arguments)


def insert_action(db: sqlite3.Connection, record_type: str, values: dict, *, ignore: bool = False,
                  execution_owner: bool = False, layout=None) -> ActionWriteResult:
    """Allocate automatic receipt IDs under SQLite's writer lock."""
    _check_fields(record_type, values)
    if record_type in EMAIL_TYPES_ORDER:
        if record_type in EMAIL_ROWID_TYPES:
            raise ValueError("Email ROWID family requires insert_email_action")
        def operation():
            _email_lock(db, tuple(values.values()))
            return _insert_email_action(db, record_type, values, ignore=ignore)
        return _email_unit(db, "action_email_insert", operation)
    automatic = ((record_type in AUTOINCREMENT_TYPES or record_type == "sent_replies") and values.get("id") is None
                 or record_type in REPLY_ACTION_TYPES and _reply_auto_id(values))
    locked = automatic or (record_type in REPLY_ACTION_TYPES and (ignore or record_type == "memory_write_events"))
    owned = locked and not db.in_transaction and (
        db.isolation_level is None or getattr(db, "autocommit", None) is True
    )
    if owned:
        db.execute("savepoint action_receipt_insert")
    try:
        if record_type in REPLY_ACTION_TYPES:
            bound = tuple(values.values())
            db.execute("update main.sqlite_sequence set seq=seq where 0" + " and ? is null" * len(bound), bound)
        elif locked:
            # A no-row write acquires the lock before reading the allocator.
            # Deferred connections retain their ordinary caller-owned transaction.
            db.execute("update main.sqlite_sequence set seq=seq where 0")
        result = _insert_action(db, record_type, values, ignore=ignore,
                                execution_owner=execution_owner, layout=layout)
    except BaseException:
        if owned and db.in_transaction:
            db.execute("rollback to action_receipt_insert")
            db.execute("release action_receipt_insert")
        raise
    if owned:
        db.execute("release action_receipt_insert")
    return result


def _insert_action(db: sqlite3.Connection, record_type: str, values: dict, *, ignore: bool = False,
                   execution_owner: bool = False, layout=None) -> ActionWriteResult:
    """Insert source-shaped values; return the original per-type public ID."""
    _check_fields(record_type, values)
    if record_type in REPLY_ACTION_TYPES:
        return _insert_reply_action(db, record_type, values, ignore=ignore)
    now = db.execute("select current_timestamp").fetchone()[0]
    payload = DEFAULTS[record_type] | {
        field: now for field in ("created_at", "updated_at", "prepared_at", "sent_at")
        if field in SOURCE_COLUMNS[record_type]
    }
    payload.update({field: value for field, value in values.items() if field in PAYLOAD_FIELDS[record_type]})
    fields = [field for field in (REFERENCES | DELIVERY_REFERENCES | (EXECUTION_REFERENCES if execution_owner else {})) if field in _kind_references(record_type, execution_owner=execution_owner)]
    arguments = [ACTION_RECORD_TYPES.code(record_type), _action_payload(
        db, record_type, payload, layout=layout)]
    arguments += [(action_physical_id(db, EXECUTION_ALIAS, values.get(field)) if execution_owner and field == 'execution_id' else _parent_id(db, field, values.get(field))) for field in fields]
    public_id = (tuple(values.get(field) for field in POSTFIX_IDENTITY_FIELDS)
                 if record_type in POSTFIX_ACTION_TYPES else
                 (values.get("sent_reply_id"), values.get("agent_run_id")) if record_type == "sent_reply_observers"
                 else values.get(IDENTITY_FIELDS[record_type]))
    if record_type == "external_action_results" and public_id is None:
        raise ValueError("unrepresentable null external action key")
    if public_id is None and (record_type in AUTOINCREMENT_TYPES or record_type == "sent_replies"):
        maximum = _autoincrement_high_water(db, record_type)
        if maximum == 2**63 - 1:
            if record_type == "sent_replies" and db.in_transaction:
                # Native AUTOINCREMENT SQLITE_FULL aborts the whole caller
                # transaction, including nested savepoints.
                db.execute("rollback")
            _raise_autoincrement_full()
        public_id = maximum + 1
    if public_id is None and db.execute("select 1 from actions where record_type=? and legacy_key='9223372036854775807'", (record_type,)).fetchone():
        # Preserve SQLite's own random-rowid algorithm at INTEGER's upper bound.
        with closing(sqlite3.connect(":memory:")) as allocator:
            allocator.execute("create table ids(id integer primary key)")
            allocator.executemany("insert into ids values(?)", db.execute("select cast(legacy_key as integer) from actions where record_type=?", (record_type,)))
            public_id = allocator.execute("insert into ids default values").lastrowid
    if public_id is None:
        identity = "cast(coalesce((select max(cast(legacy_key as integer))+1 from actions where record_type=?),1) as text)"
        arguments.append(record_type)
    else:
        identity = "?"
        arguments.append(json.dumps(public_id, ensure_ascii=False, separators=(",", ":")) if record_type in POSTFIX_ACTION_TYPES or record_type == "sent_reply_observers" else str(public_id))
    returning = ("channel,delivery_key" if record_type in POSTFIX_ACTION_TYPES else
                 "legacy_key" if record_type == "sent_reply_observers" else
                 "legacy_key" if record_type == "external_action_results" else "cast(legacy_key as integer)")
    columns = ("_kind", "payload_json", *fields, "legacy_key")
    sql = f"insert {'or ignore ' if ignore else ''}into actions({','.join(columns)}) values({','.join('?' for _ in range(2 + len(fields)))},{identity}) returning {returning}"
    cursor = db.execute(sql, arguments)
    result = cursor.fetchone()
    cursor.fetchall()
    identity = (tuple(result) if record_type in POSTFIX_ACTION_TYPES else
                tuple(json.loads(result[0])) if record_type == "sent_reply_observers" else result[0]) if result else None
    return ActionWriteResult(identity, cursor.rowcount)


def update_action(db: sqlite3.Connection, record_type: str, public_id: ActionIdentity, values: dict, *, where_sql: str = "", parameters=()) -> sqlite3.Cursor:
    """Update explicit source fields; an optional predicate uses the projection."""
    _check_fields(record_type, values)
    if record_type in EMAIL_TYPES_ORDER:
        if record_type in EMAIL_ROWID_TYPES:
            raise ValueError("Email ROWID family requires update_email_action")
        return _email_unit(db, "action_email_update", lambda: _update_email_action(
            db, record_type, public_id, values, where_sql=where_sql, parameters=parameters))
    if record_type in REPLY_ACTION_TYPES:
        owned = not db.in_transaction and (
            db.isolation_level is None or getattr(db, "autocommit", None) is True
        )
        if owned:
            db.execute("savepoint action_reply_update")
        try:
            result = _update_reply_action(db, record_type, public_id, values,
                                          where_sql=where_sql, parameters=parameters)
        except BaseException:
            if owned and db.in_transaction:
                db.execute("rollback to action_reply_update")
                db.execute("release action_reply_update")
            raise
        if owned:
            db.execute("release action_reply_update")
        return result
    assignments, args, paths = [], [], []
    identity_arguments = _identity_arguments(record_type, public_id)
    if record_type == "sent_reply_observers" and ("sent_reply_id" in values or "agent_run_id" in values):
        new_key = (values.get("sent_reply_id", public_id[0]), values.get("agent_run_id", public_id[1]))
        assignments.append("legacy_key=?")
        args.append(json.dumps(new_key, separators=(",", ":")))
    if record_type in POSTFIX_ACTION_TYPES and any(field in values for field in POSTFIX_IDENTITY_FIELDS):
        new_key = tuple(values.get(field, public_id[index]) for index, field in enumerate(POSTFIX_IDENTITY_FIELDS))
        assignments.append("legacy_key=?")
        args.append(json.dumps(new_key, ensure_ascii=False, separators=(",", ":")))
    for field, value in values.items():
        if record_type in POSTFIX_ACTION_TYPES and field in POSTFIX_IDENTITY_FIELDS:
            continue
        if field in _kind_references(record_type) or field == IDENTITY_FIELDS.get(record_type):
            is_identity = field == IDENTITY_FIELDS.get(record_type)
            assignments.append(("legacy_key" if is_identity else field) + "=?")
            args.append(str(value) if is_identity and value is not None else value if is_identity else _parent_id(db, field, value))
        else:
            paths.extend((_action_path(record_type, field), json.dumps(_encoded_native_sqlite_value(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"))))
    if paths:
        assignments.append("payload_json=jsonb_set(payload_json," + ",".join("?,json(?)" for _ in range(len(paths) // 2)) + ")")
        args.extend(paths)
    if not assignments:
        raise ValueError("action update requires fields")
    predicate = _identity_predicate(record_type)
    args.extend(identity_arguments)
    if where_sql:
        if record_type in POSTFIX_ACTION_TYPES:
            predicate += f" and (channel,delivery_key) in (select channel,delivery_key from ({ACTION_PROJECTIONS[record_type]}) where channel=? and delivery_key=? and ({where_sql}))"
            args.extend((*public_id, *parameters))
        elif record_type == "sent_reply_observers":
            predicate += f" and exists (select 1 from ({ACTION_PROJECTIONS[record_type]}) where sent_reply_id=? and agent_run_id=? and ({where_sql}))"
            args.extend((*public_id, *parameters))
        else:
            identity_field = IDENTITY_FIELDS[record_type]
            identity_expression = "legacy_key" if record_type == "external_action_results" else "cast(legacy_key as integer)"
            projection = SENT_REPLY_SQL if record_type == "sent_replies" else ACTION_PROJECTIONS[record_type]
            predicate += f" and {identity_expression} in (select {identity_field} from ({projection}) where {identity_field}=? and ({where_sql}))"
            args.extend((public_id, *parameters))
    return db.execute(f"update actions set {','.join(assignments)} where {predicate}", args)


def delete_action(db: sqlite3.Connection, record_type: str, public_id: ActionIdentity) -> sqlite3.Cursor:
    _require_kind(record_type)
    if record_type in EMAIL_TYPES_ORDER:
        if record_type in EMAIL_ROWID_TYPES:
            raise ValueError("Email ROWID family requires delete_email_action")
        def operation():
            predicate, args = _email_identity(record_type, public_id)
            _email_lock(db, args)
            return db.execute("delete from actions where " + predicate, args)
        return _email_unit(db, "action_email_delete", operation)
    return db.execute(f"delete from actions where {_identity_predicate(record_type)}", _identity_arguments(record_type, public_id))


def _no_inbound(db, target_tables):
    target_names = {sqlite_schema_name(table) for table in target_tables}
    for (table,) in db.execute("select name from sqlite_master where type='table'").fetchall():
        if sqlite_schema_name(table) in target_names:
            continue
        quoted = table.replace('"', '""')
        if any(sqlite_schema_name(fk[2]) in target_names for fk in db.execute(f'pragma main.foreign_key_list("{quoted}")')):
            raise ValueError(f"unsupported inbound action source reference from {table}")


def _no_sql_dependencies(db, target_tables):
    """Use SQLite's rename parser on schema-only scratch data, never live DDL.

    SQLite rewrites actual table references in views and trigger bodies during
    RENAME TABLE. Comparing the external declarations detects read/write and
    quoted/indirect references without token matching or executing any body.
    Virtual tables need only their declared column shape for this parse; no
    virtual module runs or implicitly creates duplicate shadow tables.
    """
    objects = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_master where type in ('table','view','trigger') and sql is not null and name not glob 'sqlite_*' order by case type when 'table' then 0 when 'view' then 1 else 2 end,name"
    )]
    target_names = {sqlite_schema_name(table) for table in target_tables}
    external = {(kind, sqlite_schema_name(name)): sql for kind, name, table, sql in objects if kind in ("view", "trigger") and sqlite_schema_name(table) not in target_names}
    if not external:
        return
    virtual = {sqlite_schema_name(row[1]) for row in db.execute("pragma table_list") if row[0] == "main" and row[2] == "virtual"}
    with closing(sqlite3.connect(":memory:")) as parser:
        for kind, name, _, sql in objects:
            if kind == "table" and sqlite_schema_name(name) in virtual:
                quoted = '"' + name.replace('"', '""') + '"'
                columns = [row[1] for row in db.execute(f"pragma table_xinfo({quoted})")]
                sql = f"create table {quoted} (" + ",".join('"' + c.replace('"', '""') + '"' for c in columns) + ")"
            parser.execute(sql)
        names = {sqlite_schema_name(row[1]) for row in objects}
        for table in sorted(target_tables):
            replacement = "_action_dependency_" + table
            while sqlite_schema_name(replacement) in names:
                replacement += "_"
            names.add(sqlite_schema_name(replacement))
            quoted = '"' + table.replace('"', '""') + '"'
            parser.execute(f'alter table {quoted} rename to "{replacement}"')
            for kind, name, sql in parser.execute("select type,name,sql from sqlite_master where type in ('view','trigger')"):
                key = (kind, sqlite_schema_name(name))
                if key in external and sql != external[key]:
                    raise ValueError(f"unsupported action SQL dependency: {name} references {table}")


def _require_available_names(db, objects):
    for obj in objects:
        if db.execute("select 1 from sqlite_master where name=? collate nocase", (obj["name"],)).fetchone():
            raise ValueError(f"unsupported action source declaration name collision: {obj['name']}")


def capture_action_schema(db: sqlite3.Connection) -> dict:
    """Capture exact declarations only; refuse unsupported sources read-only."""
    if db.execute("select 1 from sqlite_master where name='actions' collate nocase").fetchone():
        raise ValueError("unsupported action source: actions already exists")
    tables = {kind: {"columns": [r[1] for r in db.execute(f"pragma table_info({kind})")], "objects": _objects(db, kind)} for kind in ACTION_TYPES}
    if tables != _source_schema():
        raise ValueError("unsupported action source declaration")
    _require_available_names(db, _current_objects())
    _no_inbound(db, set(ACTION_TYPES))
    _no_sql_dependencies(db, set(ACTION_TYPES))
    return {"tables": tables}


def _require_transaction(db):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("action schema transition requires caller-owned FK-off transaction")


def _source_rows(db):
    rows = {kind: [dict(zip(SOURCE_COLUMNS[kind], row, strict=True)) for row in db.execute(f"select * from {kind} order by id")] for kind in ACTION_TYPES}
    for kind, values in rows.items():
        for row in values:
            _check_fields(kind, row)
            for field, value in row.items():
                if value is None and field not in NULLABLE:
                    raise ValueError(f"invalid source null {kind}.{field}")
            if kind in ENUMS:
                field, options = ENUMS[kind]
                if row[field] not in options:
                    raise ValueError(f"invalid source enum {kind}.{field}")
        if db.execute(f"pragma foreign_key_check({kind})").fetchone():
            raise ValueError(f"invalid source reference in {kind}")
    for kind in ACTION_TYPES:
        for field, (parent, parent_kind) in REFERENCES.items():
            if field in SOURCE_COLUMNS[kind] and parent != "actions" and db.execute(f"select 1 from {kind} s join {parent} p on p.id=s.{field} where p.record_type<>? limit 1", (parent_kind,)).fetchone():
                raise ValueError(f"invalid source parent type {kind}.{field}")
    return rows


def migrate_action_schema(db: sqlite3.Connection) -> dict:
    """Convert all six supported source tables; caller commits or rolls back."""
    _require_transaction(db)
    capture_action_schema(db)
    rows = _source_rows(db)
    mapping = {(kind, row["id"]): number for number, (kind, row) in enumerate(((kind, row) for kind in ACTION_TYPES for row in rows[kind]), 1)}
    _create_action_objects(db)
    for kind, records in rows.items():
        for row in records:
            values = {"id": mapping[kind, row["id"]], "record_type": kind, "legacy_key": str(row["id"])}
            values["payload_json"] = json.dumps({field: row[field] for field in PAYLOAD_FIELDS[kind]}, ensure_ascii=False, separators=(",", ":"))
            for field, (parent, parent_kind) in REFERENCES.items():
                if field in row:
                    values[field] = mapping[parent_kind, row[field]] if parent == "actions" and row[field] is not None else row[field]
            db.execute(f"insert into actions({','.join(values)}) values({','.join('?' for _ in values)})", tuple(values.values()))
    for kind in ACTION_TYPES:
        db.execute(f"drop table {kind}")
    return {"counts": {kind: len(records) for kind, records in rows.items()}}


def restore_action_schema(db: sqlite3.Connection, state: dict) -> dict:
    """Recreate original declarations using all current facts and public IDs."""
    _require_transaction(db)
    if state != {"tables": _source_schema()}:
        raise ValueError("unsupported action inverse source metadata")
    if not action_schema_is_current(db):
        raise ValueError("unsupported current actions schema")
    _require_available_names(db, [obj for schema in state["tables"].values() for obj in schema["objects"]])
    _no_inbound(db, {"actions"})
    _no_sql_dependencies(db, {"actions"})
    if db.execute("pragma foreign_key_check(actions)").fetchone():
        raise ValueError("invalid current actions reference")
    for field, (parent, kind) in _references_for(ACTION_TYPES).items():
        if db.execute(f"select 1 from actions a join {parent} p on p.id=a.{field} where p.record_type<>? limit 1", (kind,)).fetchone():
            raise ValueError(f"invalid current actions parent type {field}")
    check = db.execute("pragma integrity_check(actions)").fetchall()
    if [row[0] for row in check] != ["ok"]:
        raise ValueError("invalid current actions rows")
    rows = {kind: [tuple(row) for row in db.execute(ACTION_PROJECTIONS[kind])] for kind in ACTION_TYPES}
    for kind, schema in state["tables"].items():
        db.execute(next(obj["sql"] for obj in schema["objects"] if obj["type"] == "table"))
        db.executemany(f"insert into {kind}({','.join(schema['columns'])}) values({','.join('?' for _ in schema['columns'])})", rows[kind])
    for schema in state["tables"].values():
        for obj in schema["objects"]:
            if obj["type"] != "table":
                db.execute(obj["sql"])
    db.execute("drop table actions")
    return {"counts": {kind: len(records) for kind, records in rows.items()}}
