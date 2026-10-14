"""Finite typed storage for candidate, review, execution and receipt facts.

Public IDs keep each source table's integer, text or composite namespace.
Postfix children retain their original composite foreign key. Migration callers
own an FK-off transaction; inverse reads current rows, never a business snapshot.
"""

from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import sqlite3
from string import ascii_lowercase, ascii_uppercase


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
POSTFIX_ACTION_TYPES = tuple(kind for kind in SOURCE_COLUMNS if kind not in ALL_ACTION_TYPES)
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


def _kind_references(kind):
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


def _references_for(kinds):
    references = {field: parent for field, parent in REFERENCES.items()
                  if any(field in _kind_references(kind) for kind in kinds)}
    if "sent_reply_observers" in kinds:
        references.update(DELIVERY_REFERENCES)
    return references


def _payload_check(kind, references):
    terms = []
    for field in PAYLOAD_FIELDS[kind]:
        expression = f"json_type(payload_json,'$.{field}')"
        value_type = 'integer' if field in INTEGER_FIELDS else 'text'
        condition = f"{expression} is '{value_type}'"
        if field in NULLABLE_PAYLOAD_FIELDS.get(kind, ()):
            condition = f"({condition} or {expression} is 'null')"
        terms.append(condition)
    paths = ",".join(repr("$." + field) for field in PAYLOAD_FIELDS[kind])
    terms.append(f"json_remove(payload_json,{paths})='{{}}'")
    for field in references:
        if field not in _kind_references(kind):
            terms.append(f"{field} is null")
        elif field in NULLABLE:
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


def _reply_payload_check(fields):
    paths = ",".join(repr("$." + field) for field in fields)
    return f"json_remove(payload_json,{paths})='{{}}' and " + " and ".join(_reply_value_check(field) for field in fields)


def _action_definitions(include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None):
    if include_reply and reply_variant not in REPLY_VARIANTS:
        raise ValueError("reply storage requires one global source variant")
    if not include_reply and reply_variant is not None:
        raise ValueError("reply variant requires opt-in reply storage")
    include_delivery = include_delivery or include_reply
    include_postfix = include_postfix or include_delivery
    include_receipts = include_receipts or include_postfix
    kinds = ALL_ACTION_TYPES_WITH_REPLY if include_reply else ALL_ACTION_TYPES_WITH_DELIVERY if include_delivery else ALL_ACTION_TYPES_WITH_POSTFIX if include_postfix else ALL_ACTION_TYPES if include_receipts else CANDIDATE_ACTION_TYPES
    references = _references_for(kinds)
    payload_fields = dict.fromkeys(c for kind in kinds if kind not in REPLY_ACTION_TYPES for c in PAYLOAD_FIELDS[kind] if c not in references and not (include_delivery and c == "channel"))
    generated = []
    for column in payload_fields:
        expression = f"json_extract(payload_json,'$.{column}')"
        if include_receipts and column == 'external_action_key':
            expression = f"case when record_type='external_action_results' then legacy_key else {expression} end"
        generated.append(f"{column} {'integer' if column in INTEGER_FIELDS else 'text'} generated always as ({expression}) virtual")
    postfix_scope = _POSTFIX_TYPES_SQL
    if include_postfix:
        for index, column in enumerate(POSTFIX_IDENTITY_FIELDS):
            sent_channel = "when record_type='sent_replies' then json_extract(payload_json,'$.channel') " if include_delivery and column == "channel" else ""
            generated.append(f"{column} text generated always as (case {sent_channel}when record_type in ({postfix_scope}) then json_extract(legacy_key,'$[{index}]') end) virtual")
        generated.append("postfix_type text generated always as (case when record_type in ('outbound_postfix_receipts','native_reply_dispatches') then 'outbound_postfixes' end) virtual")
    reply_columns = []
    if include_reply:
        for kind, prefix in (("reply_attempts", "attempt_"), ("memory_write_events", "memory_")):
            for field in _REPLY_FIELDS[kind]:
                if kind == "reply_attempts" and reply_variant == "fresh_memory_fk" and field in _ATTEMPT_HISTORICAL:
                    continue
                if kind == "memory_write_events" and field == "attempt_id" and reply_variant == "fresh_memory_fk":
                    continue
                path = f"$.{field}"
                encoded = (f"case json_extract(payload_json,'{path}.t') "
                           f"when 'blob' then unhex(json_extract(payload_json,'{path}.v')) "
                           "when 'inf' then 9e999 when '-inf' then -9e999 end")
                value = (f"case when json_type(payload_json,'{path}')='object' "
                         f"then {encoded} else json_extract(payload_json,'{path}') end")
                reply_columns.append(f"{prefix}{field} {_REPLY_TYPES[kind][field]} generated always as ({value}) virtual")
        if reply_variant == "fresh_memory_fk":
            reply_columns.append("memory_attempt_ref integer references actions(id)")
    key_check = "legacy_key=cast(cast(legacy_key as integer) as text)"
    if include_receipts:
        key_check = f"case when record_type='external_action_results' then trim(legacy_key)<>'' else {key_check} end"
    if include_postfix:
        composite_check = ("case when json_valid(legacy_key) then json_type(legacy_key)='array' "
                           "and json_array_length(legacy_key)=2 and json_type(legacy_key,'$[0]') is 'text' "
                           "and json_type(legacy_key,'$[1]') is 'text' else 0 end")
        key_check = f"case when record_type in ({postfix_scope}) then ({composite_check}) else ({key_check}) end"
    if include_delivery:
        observer_key = ("case when json_valid(legacy_key) then json_type(legacy_key)='array' "
                        "and json_array_length(legacy_key)=2 and json_type(legacy_key,'$[0]')='integer' "
                        "and json_type(legacy_key,'$[1]')='integer' else 0 end")
        key_check = f"case when record_type='sent_reply_observers' then ({observer_key}) else ({key_check}) end"
    table = (
        "create table actions (id integer primary key, record_type text not null check(record_type in ("
        + ",".join(repr(k) for k in kinds)
        + f")),legacy_key text not null check({key_check}),"
        + ",".join(f"{column} integer references {parent}(id)" for column, (parent, _) in references.items())
        + ("," + ",".join(reply_columns) if reply_columns else "")
        + ",payload_json text not null check(json_valid(payload_json) and json_type(payload_json)='object'),"
        + ",".join(generated)
        + "," + ",".join(_payload_check(kind, references) for kind in kinds if kind not in REPLY_ACTION_TYPES)
        + "," + ",".join(f"check(record_type<>'{kind}' or {field} in ({','.join(repr(v) for v in options)}))"
                          for kind, (field, options) in ENUMS.items() if kind in kinds)
    )
    if include_receipts:
        table += ",check(record_type<>'agent_effect_intents' or action_index>=0)"
        for field in ('business_object_key', 'action_identity', 'operation', 'result_digest'):
            table += f",check(record_type<>'external_action_results' or trim({field})<>'')"
    if include_postfix:
        table += ",check(record_type not in ('outbound_postfixes','outbound_postfix_receipts') or (channel in ('dingtalk','wechat') and trim(delivery_key)<>''))"
        table += ",check(record_type<>'outbound_postfixes' or trim(final_body)<>'')"
        table += ",foreign key(postfix_type,channel,delivery_key) references actions(record_type,channel,delivery_key)"
    if include_delivery:
        table += ",check(record_type<>'sent_reply_observers' or (sent_reply_id is not null and agent_run_id is not null and reply_task_id is not null and json_extract(legacy_key,'$[1]')=agent_run_id))"
    if include_reply:
        table += ",check(record_type not in ('reply_attempts','memory_write_events') or (" + " and ".join(f"{field} is null" for field in references) + "))"
        attempt_fields = [field for field in _REPLY_FIELDS["reply_attempts"] if reply_variant == "actual_no_memory_fk" or field not in _ATTEMPT_HISTORICAL]
        table += ",check(record_type<>'reply_attempts' or (" + _reply_payload_check(attempt_fields) + "))"
        table += ",check(record_type<>'reply_attempts' or (" + " and ".join(f"attempt_{field} is not null" for field in attempt_fields if field not in _ATTEMPT_NULLABLE) + "))"
        memory_parent = "memory_attempt_ref" if reply_variant == "fresh_memory_fk" else "memory_attempt_id"
        memory_fields = [field for field in _REPLY_FIELDS["memory_write_events"] if field != "attempt_id" or reply_variant == "actual_no_memory_fk"]
        table += ",check(record_type<>'memory_write_events' or (" + _reply_payload_check(memory_fields) + "))"
        table += ",check(record_type<>'memory_write_events' or (" + " and ".join([f"{memory_parent} is not null"] + [f"memory_{field} is not null" for field in _REPLY_FIELDS["memory_write_events"] if field != "attempt_id"]) + "))"
    table += ")"
    numeric_scope = " where record_type<>'external_action_results'" if include_receipts else ""
    if include_postfix:
        numeric_scope += f" and record_type not in ({postfix_scope})"
    if include_delivery:
        numeric_scope += " and record_type<>'sent_reply_observers'"
    indexes = (
        "create unique index idx_actions_public_id on actions(cast(legacy_key as integer),record_type)" + numeric_scope,
        "create unique index idx_actions_consumer_run on actions(consumer_run_id) where record_type='review_candidates'",
        "create index idx_actions_candidates_task on actions(task_id,execution_generation,cast(legacy_key as integer)) where record_type='review_candidates'",
        "create unique index idx_actions_audit_run on actions(audit_run_id) where record_type='candidate_reviews'",
        "create index idx_actions_reviews_candidate on actions(candidate_id,cast(legacy_key as integer)) where record_type='candidate_reviews'",
        "create unique index idx_actions_selection_candidate on actions(candidate_id) where record_type='candidate_selections'",
        "create unique index idx_actions_execution_candidate on actions(candidate_id) where record_type='candidate_executions'",
        "create unique index idx_actions_attempt_sequence on actions(execution_id,action_index) where record_type='candidate_action_attempts'",
        "create index idx_actions_external_key on actions(external_action_key) where record_type='candidate_action_attempts'",
    )
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
    if include_postfix:
        indexes += ("create unique index idx_actions_postfix_identity on actions(record_type,channel,delivery_key)",)
    if include_delivery:
        indexes += (
            "create unique index idx_actions_observer_identity on actions(sent_reply_id,agent_run_id) where record_type='sent_reply_observers'",
            "create unique index idx_actions_sent_external_key on actions(external_action_key) where record_type='sent_replies' and external_action_key<>''",
            "create index idx_actions_sent_history on actions(conversation_id,trigger_message_id,sent_at) where record_type='sent_replies'",
        )
    if include_reply:
        memory_parent = "memory_attempt_ref" if reply_variant == "fresh_memory_fk" else "memory_attempt_id"
        indexes += (
            "create index idx_actions_reply_public_order on actions(record_type,cast(legacy_key as integer)) where record_type='reply_attempts'",
            "create index idx_actions_memory_public_order on actions(record_type,cast(legacy_key as integer)) where record_type='memory_write_events'",
            "create index idx_actions_reply_trigger_message on actions(attempt_trigger_message_id) where record_type='reply_attempts'",
            "create index idx_actions_reply_status on actions(attempt_send_status,attempt_created_at) where record_type='reply_attempts'",
            "create index idx_actions_reply_created on actions(attempt_created_at,cast(legacy_key as integer)) where record_type='reply_attempts'",
            "create index idx_actions_reply_oa_history on actions(attempt_oa_process_instance_id,attempt_created_at desc,cast(legacy_key as integer) desc) where record_type='reply_attempts' and attempt_oa_process_instance_id<>''",
            "create index idx_actions_reply_trigger_history on actions(attempt_conversation_id,attempt_trigger_message_id,attempt_action,cast(legacy_key as integer) desc) where record_type='reply_attempts'",
            "create index idx_actions_reply_current_trigger on actions(attempt_channel,attempt_conversation_id,attempt_trigger_message_id,attempt_updated_at desc,cast(legacy_key as integer) desc) where record_type='reply_attempts'",
            "create index idx_actions_reply_run_recovery on actions(attempt_agent_run_id,attempt_send_error,cast(legacy_key as integer)) where record_type='reply_attempts'",
            f"create unique index idx_actions_memory_unique on actions({memory_parent},memory_event_type) where record_type='memory_write_events'",
            f"create index idx_actions_memory_attempt on actions({memory_parent},cast(legacy_key as integer)) where record_type='memory_write_events'",
            f"create index idx_actions_memory_status on actions(memory_status,{'memory_updated_at' if reply_variant == 'fresh_memory_fk' else 'cast(legacy_key as integer)'}) where record_type='memory_write_events'",
        )
    triggers = (
        "create trigger trg_actions_record_type_immutable before update of record_type on actions when new.record_type<>old.record_type begin select raise(abort,'action record type is immutable'); end",
        "create trigger trg_actions_public_reference_update before update of legacy_key on actions when new.legacy_key<>old.legacy_key and exists(select 1 from actions where predecessor_review_id=old.id or candidate_id=old.id or review_id=old.id or selection_id=old.id or execution_id=old.id) begin select raise(abort,'FOREIGN KEY constraint failed'); end",
        *tuple(
            f"create trigger trg_actions_{field}_{suffix} before {event} on actions when new.{field} is not null and exists(select 1 from {parent} where id=new.{field} and record_type<>'{kind}') begin select raise(abort,'action parent type mismatch'); end"
            for field, (parent, kind) in references.items()
            for event, suffix in (("insert", "insert"), (f"update of {field}", "update"))
        ),
    )
    if include_receipts:
        sequences = ACTION_SEQUENCE_NAMES | ({"sent_replies": DELIVERY_SEQUENCE_NAME} if include_delivery else {})
        for kind, name in sequences.items():
            triggers += (f"create trigger trg_actions_{kind}_sequence before insert on actions when new.record_type='{kind}' begin "
                         f"insert into sqlite_sequence(name,seq) select '{name}',0 where not exists(select 1 from sqlite_sequence where name='{name}'); "
                         f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) where name='{name}'; end",)
    if include_delivery:
        triggers += ("create trigger trg_actions_sent_public_reference_update before update of legacy_key on actions when old.record_type='sent_replies' and new.legacy_key<>old.legacy_key and exists(select 1 from actions where sent_reply_id=old.id and record_type='sent_reply_observers') begin select raise(abort,'FOREIGN KEY constraint failed'); end",)
        for event, suffix in (("insert", "insert"), ("update of sent_reply_id,legacy_key", "update")):
            triggers += (f"create trigger trg_actions_observer_sent_key_{suffix} before {event} on actions when new.record_type='sent_reply_observers' and not exists(select 1 from actions p where p.id=new.sent_reply_id and p.record_type='sent_replies' and cast(p.legacy_key as integer)=json_extract(new.legacy_key,'$[0]')) begin select raise(abort,'observer sent reply identity mismatch'); end",)
    if include_reply:
        for kind, name in REPLY_SEQUENCE_NAMES.items():
            triggers += (f"create trigger trg_actions_{kind}_sequence before insert on actions when new.record_type='{kind}' begin "
                         f"insert into sqlite_sequence(name,seq) select '{name}',0 where not exists(select 1 from sqlite_sequence where name='{name}'); "
                         f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) where name='{name}'; end",)
        marker_check = (
            "json_type(new.payload_json,'$.audit_tool_events_json') is not 'text' or "
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
    return table, indexes, triggers


ACTION_TABLE_DDL, ACTION_INDEX_DDL, ACTION_TRIGGER_DDL = _action_definitions()
RECEIPT_ACTION_TABLE_DDL, RECEIPT_ACTION_INDEX_DDL, RECEIPT_ACTION_TRIGGER_DDL = _action_definitions(True)
POSTFIX_ACTION_TABLE_DDL, POSTFIX_ACTION_INDEX_DDL, POSTFIX_ACTION_TRIGGER_DDL = _action_definitions(True, True)


def _projection(kind):
    if kind in REPLY_ACTION_TYPES:
        raise ValueError("reply projection requires global schema variant")
    fields, joins = [], []
    for field in SOURCE_COLUMNS[kind]:
        if field == IDENTITY_FIELDS.get(kind):
            expression = "a.legacy_key" if kind == "external_action_results" else "cast(a.legacy_key as integer)"
        elif kind == "sent_replies" and field == "agent_run_id":
            expression = "json_extract(a.payload_json,'$.agent_run_id')"
        elif field in _kind_references(kind) and _kind_references(kind)[field][0] == "actions":
            alias = "p_" + field
            expression = f"cast({alias}.legacy_key as integer)"
            join = "left join" if field in NULLABLE else "join"
            joins.append(f"{join} actions {alias} on {alias}.id=a.{field} and {alias}.record_type='{_kind_references(kind)[field][1]}'")
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


ACTION_PROJECTIONS = {kind: _projection(kind) for kind in ALL_ACTION_TYPES_WITH_DELIVERY}


def reply_action_projection(kind, reply_variant):
    if kind not in REPLY_ACTION_TYPES or reply_variant not in REPLY_VARIANTS:
        raise ValueError("unsupported reply projection")
    order = (_FRESH_ATTEMPT_ORDER if reply_variant == "fresh_memory_fk" else _ATTEMPT_ORDER) if kind == "reply_attempts" else _MEMORY_ORDER
    fields = []
    for field in order:
        if field == "id":
            expression = "cast(a.legacy_key as integer)"
        elif kind == "reply_attempts" and field in _ATTEMPT_HISTORICAL and reply_variant == "fresh_memory_fk":
            continue
        elif kind == "memory_write_events" and field == "payload_json":
            expression = "'{}'"
        elif kind == "memory_write_events" and field == "attempt_id" and reply_variant == "fresh_memory_fk":
            expression = "cast(p.legacy_key as integer)"
        else:
            expression = "a." + ("attempt_" if kind == "reply_attempts" else "memory_") + field
        fields.append(expression + " as " + field)
    join = "left join actions p on p.id=a.memory_attempt_ref and p.record_type='reply_attempts'" if kind == "memory_write_events" and reply_variant == "fresh_memory_fk" else ""
    return f"select {','.join(fields)} from actions a {join} where a.record_type='{kind}' and a.record_type<>'external_action_results' and a.record_type not in ({_POSTFIX_TYPES_SQL}) and a.record_type<>'sent_reply_observers'"


# Both canonical E declarations contain the 54 current Attempt fields. This
# explicit projection omits only the four preserved actual-source history fields.
REPLY_ATTEMPT_COMMON_SQL = reply_action_projection("reply_attempts", "fresh_memory_fk")


def validated_reply_variant(db: sqlite3.Connection, *, allow_missing_objects=False) -> str:
    """Select one complete canonical E declaration, including its FK and indexes."""
    matches = [
        variant for variant in REPLY_VARIANTS
        if action_schema_is_current(
            db, allow_missing_objects=allow_missing_objects,
            include_reply=True, reply_variant=variant,
        )
    ]
    if len(matches) != 1:
        raise sqlite3.IntegrityError("unsupported or mixed Attempt/Memory action schema")
    return matches[0]


def reply_schema_is_current(db: sqlite3.Connection) -> bool:
    """Recognize either complete Store19 declaration, without altering the DB."""
    return any(action_schema_is_current(db, include_reply=True, reply_variant=variant)
               for variant in REPLY_VARIANTS)
REVIEW_CANDIDATE_SQL = ACTION_PROJECTIONS["review_candidates"]
CANDIDATE_REVIEW_SQL = ACTION_PROJECTIONS["candidate_reviews"]
CANDIDATE_SELECTION_SQL = ACTION_PROJECTIONS["candidate_selections"]
CANDIDATE_SUPPLEMENT_SQL = ACTION_PROJECTIONS["candidate_supplements"]
CANDIDATE_EXECUTION_SQL = ACTION_PROJECTIONS["candidate_executions"]
CANDIDATE_ACTION_ATTEMPT_SQL = ACTION_PROJECTIONS["candidate_action_attempts"]
EXTERNAL_ACTION_RESULT_SQL = ACTION_PROJECTIONS["external_action_results"]
AGENT_EFFECT_INTENT_SQL = ACTION_PROJECTIONS["agent_effect_intents"]
AGENT_EXECUTION_RECEIPT_SQL = ACTION_PROJECTIONS["agent_execution_receipts"]
OUTBOUND_POSTFIX_SQL = ACTION_PROJECTIONS["outbound_postfixes"]
OUTBOUND_POSTFIX_RECEIPT_SQL = ACTION_PROJECTIONS["outbound_postfix_receipts"]
NATIVE_REPLY_DISPATCH_SQL = ACTION_PROJECTIONS["native_reply_dispatches"]
SENT_REPLY_SQL = ACTION_PROJECTIONS["sent_replies"]
SENT_REPLY_OBSERVER_SQL = ACTION_PROJECTIONS["sent_reply_observers"]


def create_action_schema(db: sqlite3.Connection, *, include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None) -> None:
    """Create the declared schema, or repair only its missing auxiliary objects."""
    existing = db.execute("select 1 from sqlite_master where name='actions' collate nocase").fetchone()
    if existing:
        if not action_schema_is_current(db, allow_missing_objects=True, include_receipts=include_receipts, include_postfix=include_postfix, include_delivery=include_delivery, include_reply=include_reply, reply_variant=reply_variant):
            raise ValueError("unsupported current actions schema declaration")
        names = {sqlite_schema_name(obj["name"]) for obj in _objects(db, "actions")}
        for obj in _current_objects(include_receipts, include_postfix, include_delivery, include_reply, reply_variant):
            if sqlite_schema_name(obj["name"]) not in names:
                db.execute(obj["sql"])
    else:
        if any(db.execute("select 1 from sqlite_master where name=? collate nocase", (kind,)).fetchone() for kind in ACTION_TYPES):
            capture_action_schema(db)
        _require_available_names(db, _current_objects(include_receipts, include_postfix, include_delivery, include_reply, reply_variant))
        _create_action_objects(db, include_receipts, include_postfix, include_delivery, include_reply, reply_variant)


def _create_action_objects(db, include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None):
    if (include_receipts or include_postfix or include_delivery or include_reply) and not db.execute("select 1 from sqlite_master where type='table' and name='sqlite_sequence'").fetchone():
        # SQLite creates its native sequence metadata on the first declaration
        # of an AUTOINCREMENT table. The initializer has no rows and is removed;
        # actions retains its ordinary physical INTEGER PRIMARY KEY allocator.
        name = "_actions_sequence_initializer"
        while db.execute("select 1 from sqlite_master where name=? collate nocase", (name,)).fetchone():
            name += "_"
        db.execute(f'create table "{name}"(id integer primary key autoincrement)')
        db.execute(f'drop table "{name}"')
    table, indexes, triggers = _action_definitions(include_receipts, include_postfix, include_delivery, include_reply, reply_variant)
    for sql in (table, *indexes, *triggers):
        db.execute(sql)


@lru_cache(maxsize=12)
def _current_objects(include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None):
    with closing(sqlite3.connect(":memory:")) as db:
        _create_action_objects(db, include_receipts, include_postfix, include_delivery, include_reply, reply_variant)
        return _objects(db, "actions")


def action_schema_is_current(db: sqlite3.Connection, *, allow_missing_objects=False, include_receipts=False, include_postfix=False, include_delivery=False, include_reply=False, reply_variant=None) -> bool:
    kinds = ALL_ACTION_TYPES_WITH_REPLY if include_reply else ALL_ACTION_TYPES_WITH_DELIVERY if include_delivery else ALL_ACTION_TYPES_WITH_POSTFIX if include_postfix else ALL_ACTION_TYPES if include_receipts else ACTION_TYPES
    if any(db.execute("select 1 from sqlite_master where name=? collate nocase", (kind,)).fetchone() for kind in kinds):
        return False
    actual = {sqlite_schema_name(o["name"]): o for o in _objects(db, "actions")}
    expected = {sqlite_schema_name(o["name"]): o for o in _current_objects(include_receipts, include_postfix, include_delivery, include_reply, reply_variant)}
    if any(db.execute("select 1 from sqlite_master where name=? collate nocase and tbl_name<>'actions' collate nocase", (name,)).fetchone() for name in expected):
        return False
    return not (actual.keys() - expected.keys()) and all(
        actual.get(name) == obj or (allow_missing_objects and name != "actions" and name not in actual)
        for name, obj in expected.items()
    )


def _require_kind(kind):
    if kind not in SOURCE_COLUMNS and kind not in REPLY_ACTION_TYPES:
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
    return ("record_type=? and record_type<>'external_action_results' "
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
    table, kind = (REFERENCES | DELIVERY_REFERENCES)[field]
    if table == "actions":
        return action_physical_id(db, kind, public_id)
    row = db.execute(f"select id from {table} where record_type=? and cast(legacy_key as integer)=?", (kind, public_id)).fetchone()
    if row is None:
        raise sqlite3.IntegrityError(f"missing {kind} public ID {public_id}")
    return row[0]


def _check_fields(kind, values):
    _require_kind(kind)
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
    name = REPLY_SEQUENCE_NAMES[kind] if kind in REPLY_ACTION_TYPES else DELIVERY_SEQUENCE_NAME if kind == "sent_replies" else ACTION_SEQUENCE_NAMES[kind]
    rows = db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchall()
    if len(rows) > 1 or any(type(row[0]) is not int or row[0] < 0 for row in rows):
        raise ValueError(f"invalid {kind} allocator metadata")
    maximum = db.execute("select max(cast(legacy_key as integer)) from actions where record_type=?", (kind,)).fetchone()[0]
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
    if isinstance(value, float) and math.isnan(value):
        # sqlite3 binds NaN as NULL before the source table applies NOT NULL.
        return None
    if value == float("inf"):
        return {"t": "inf"}
    if value == float("-inf"):
        return {"t": "-inf"}
    if type(value) is int and not -(2**63) <= value < 2**63:
        raise OverflowError("Python int too large to convert to SQLite INTEGER")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise sqlite3.ProgrammingError("unsupported SQLite source value")


def _reply_payload(values):
    return json.dumps({field: _encoded_sqlite_value(value) for field, value in values.items()},
                      ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _insert_reply_action(db, kind, values, *, ignore=False):
    variant = _reply_variant_for(db)
    if kind == "reply_attempts" and variant == "fresh_memory_fk" and set(values) & _ATTEMPT_HISTORICAL:
        raise ValueError("historical attempt fields absent from fresh variant")
    now = db.execute("select current_timestamp").fetchone()[0]
    if kind == "reply_attempts":
        data = _ATTEMPT_DEFAULTS | {"created_at": now, "updated_at": now} | {field: value for field, value in values.items() if field in _REPLY_FIELDS[kind]}
        data["audit_tool_events_json"] = _audit_markers(data["audit_tool_events_json"])
        if variant == "fresh_memory_fk":
            data = {field: value for field, value in data.items() if field not in _ATTEMPT_HISTORICAL}
    else:
        data = _MEMORY_DEFAULTS | {"created_at": now, "updated_at": now} | {field: value for field, value in values.items() if field in _REPLY_FIELDS[kind]}
    attempt_id = data.pop("attempt_id", None) if kind == "memory_write_events" and variant == "fresh_memory_fk" else None
    payload_json = _reply_payload(data)
    if _reply_auto_id(values):
        maximum = _autoincrement_high_water(db, kind)
        if maximum == 2**63 - 1:
            if db.in_transaction:
                db.execute("rollback")
            _raise_autoincrement_full()
        public_id = maximum + 1
    else:
        public_id = _native_integer_primary_key(values["id"])
    if ignore and db.execute("select 1 from actions where record_type=? and cast(legacy_key as integer)=?", (kind, public_id)).fetchone():
        # Source INSERT OR IGNORE resolves its public-key uniqueness before FK
        # evaluation, but still advances AUTOINCREMENT on an explicit ID.
        # A legal public-key UPDATE can put this ID above the saved sequence.
        name = REPLY_SEQUENCE_NAMES[kind]
        db.execute("insert into sqlite_sequence(name,seq) select ?,0 where not exists(select 1 from sqlite_sequence where name=?)", (name, name))
        db.execute("update sqlite_sequence set seq=max(seq,?) where name=?", (public_id, name))
        return ActionWriteResult(None, 0)
    parent_ref = None
    if kind == "memory_write_events" and variant == "fresh_memory_fk":
        required = (attempt_id, *(data.get(field) for field in ("event_type", "status", "attempts",
                                                          "last_error", "memory_episode_id", "created_at", "updated_at")))
        if all(not _source_bound_null(value) for value in required):
            parent_ref = _reply_parent(db, attempt_id)
    columns = ["record_type", "legacy_key", "payload_json"]
    arguments = [kind, str(public_id), payload_json]
    if kind == "memory_write_events" and variant == "fresh_memory_fk":
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
    variant = _reply_variant_for(db)
    if kind == "reply_attempts" and variant == "fresh_memory_fk" and set(values) & _ATTEMPT_HISTORICAL:
        raise ValueError("historical attempt fields absent from fresh variant")
    if not values or kind == "memory_write_events" and set(values) == {"payload_json"}:
        raise ValueError("action update requires fields")
    predicate = _identity_predicate(kind)
    identity_arguments = list(_identity_arguments(kind, public_id))
    if where_sql:
        projection = reply_action_projection(kind, variant)
        predicate += f" and cast(legacy_key as integer) in (select id from ({projection}) where id=? and ({where_sql}))"
        identity_arguments.extend((public_id, *parameters))
    # Bind through a write statement: SQLite opens its deferred transaction,
    # binds every SET/WHERE input, then takes the writer lock when it executes.
    # The zero-row write keeps that lock through the scan and actual UPDATE.
    bound = (*values.values(), *identity_arguments)
    db.execute("update sqlite_sequence set seq=seq where 0" + " and ? is null" * len(bound), bound)
    if db.execute(f"select 1 from actions where {predicate} limit 1", identity_arguments).fetchone() is None:
        return db.execute("update actions set legacy_key=legacy_key where 0")
    assignments, arguments, paths = [], [], []
    for field, value in values.items():
        if field == "id":
            column = "legacy_key"
            value = str(_native_integer_primary_key(value, update=True))
        elif kind == "memory_write_events" and field == "payload_json":
            continue
        elif kind == "memory_write_events" and field == "attempt_id" and variant == "fresh_memory_fk":
            column, value = "memory_attempt_ref", _reply_parent(db, value)
        elif kind == "reply_attempts" and field == "audit_tool_events_json":
            paths.extend(("$.audit_tool_events_json", json.dumps(_audit_markers(value))))
            continue
        else:
            paths.extend(("$." + field, json.dumps(_encoded_sqlite_value(value), ensure_ascii=False,
                                                   allow_nan=False, separators=(",", ":"))))
            continue
        assignments.append(column + "=?")
        arguments.append(value)
    if paths:
        assignments.append("payload_json=json_set(payload_json," + ",".join("?,json(?)" for _ in range(len(paths) // 2)) + ")")
        arguments.extend(paths)
    if not assignments:
        raise ValueError("action update requires fields")
    arguments.extend(identity_arguments)
    return db.execute(f"update actions set {','.join(assignments)} where {predicate}", arguments)


def insert_action(db: sqlite3.Connection, record_type: str, values: dict, *, ignore: bool = False) -> ActionWriteResult:
    """Allocate automatic receipt IDs under SQLite's writer lock."""
    _check_fields(record_type, values)
    automatic = ((record_type in AUTOINCREMENT_TYPES or record_type == "sent_replies") and values.get("id") is None
                 or record_type in REPLY_ACTION_TYPES and _reply_auto_id(values))
    locked = automatic or (record_type in REPLY_ACTION_TYPES and (ignore or record_type == "memory_write_events"
                             and _reply_variant_for(db) == "fresh_memory_fk"))
    owned = locked and not db.in_transaction and (
        db.isolation_level is None or getattr(db, "autocommit", None) is True
    )
    if owned:
        db.execute("savepoint action_receipt_insert")
    try:
        if record_type in REPLY_ACTION_TYPES:
            bound = tuple(values.values())
            db.execute("update sqlite_sequence set seq=seq where 0" + " and ? is null" * len(bound), bound)
        elif locked:
            # A no-row write acquires the lock before reading the allocator.
            # Deferred connections retain their ordinary caller-owned transaction.
            db.execute("update sqlite_sequence set seq=seq where 0")
        result = _insert_action(db, record_type, values, ignore=ignore)
    except BaseException:
        if owned and db.in_transaction:
            db.execute("rollback to action_receipt_insert")
            db.execute("release action_receipt_insert")
        raise
    if owned:
        db.execute("release action_receipt_insert")
    return result


def _insert_action(db: sqlite3.Connection, record_type: str, values: dict, *, ignore: bool = False) -> ActionWriteResult:
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
    fields = [field for field in (REFERENCES | DELIVERY_REFERENCES) if field in _kind_references(record_type)]
    arguments = [record_type, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))]
    arguments += [_parent_id(db, field, values.get(field)) for field in fields]
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
    columns = ("record_type", "payload_json", *fields, "legacy_key")
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
            paths.extend(("$." + field, value))
    if paths:
        assignments.append("payload_json=json_set(payload_json," + ",".join("?" for _ in paths) + ")")
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
            predicate += f" and {identity_expression} in (select {identity_field} from ({ACTION_PROJECTIONS[record_type]}) where {identity_field}=? and ({where_sql}))"
            args.extend((public_id, *parameters))
    return db.execute(f"update actions set {','.join(assignments)} where {predicate}", args)


def delete_action(db: sqlite3.Connection, record_type: str, public_id: ActionIdentity) -> sqlite3.Cursor:
    _require_kind(record_type)
    return db.execute(f"delete from actions where {_identity_predicate(record_type)}", _identity_arguments(record_type, public_id))


def _no_inbound(db, target_tables):
    target_names = {sqlite_schema_name(table) for table in target_tables}
    for (table,) in db.execute("select name from sqlite_master where type='table'").fetchall():
        if sqlite_schema_name(table) in target_names:
            continue
        quoted = table.replace('"', '""')
        if any(sqlite_schema_name(fk[2]) in target_names for fk in db.execute(f'pragma foreign_key_list("{quoted}")')):
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
