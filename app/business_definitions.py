"""Finite Business declarations; source metadata is frozen here."""

from app.action_email_definitions import _encoded_value, _encoded_value_check

BUSINESS_SOURCE_TABLES = {'task': 'business_tasks',
 'project': 'business_projects',
 'anchor': 'business_anchors',
 'project_context_revision': 'business_project_context_revisions',
 'service_bugfix_candidate': 'service_bugfix_candidates',
 'work_cluster': 'business_work_clusters',
 'todo_evidence_candidate': 'todo_evidence_candidates',
 'work_project': 'work_projects',
 'work_todo': 'work_todos'}

BUSINESS_FIELDS = {'task': ('id',
          'title',
          'description',
          'stage',
          'status',
          'commitment_status',
          'formal_basis',
          'business_relevance',
          'merged_into_task_id',
          'owner_user_id',
          'owner_name',
          'owner_evidence_json',
          'deadline_at',
          'missing_evidence_json',
          'last_activity_at',
          'created_at',
          'updated_at',
          'origin',
          'suggestion_json'),
 'project': ('id',
             'canonical_anchor_id',
             'anchor_type',
             'title',
             'registry_source',
             'created_at',
             'crm_customer_id',
             'crm_customer_name',
             'crm_customer_lookup_status',
             'crm_customer_candidates_json',
             'crm_customer_label',
             'crm_customer_evidence_json'),
 'anchor': ('id', 'anchor_type', 'anchor_ref', 'title', 'active', 'created_at'),
 'project_context_revision': ('id', 'project_id', 'context_json', 'evidence_json', 'created_at'),
 'service_bugfix_candidate': ('id', 'feedback_event_key', 'feedback_token', 'attempt_id', 'status', 'title', 'reason', 'feedback_comment', 'conversation_title', 'trigger_text', 'created_at', 'updated_at'),
 'work_cluster': ('id', 'title', 'created_at'),
 'todo_evidence_candidate': ('id', 'project_id', 'todo_id', 'source_type', 'source_ref', 'source_created_at', 'evidence_text', 'reason', 'confidence', 'status', 'work_summary_input_id', 'decision_json', 'dedupe_key', 'created_at', 'updated_at'),
 'work_project': ('id', 'title', 'category', 'tags_json', 'status', 'priority', 'risk_level', 'needs_derek_attention', 'owner_user_id', 'owner_name', 'related_people_json', 'goal', 'background', 'facts_json', 'current_state', 'blocker', 'next_step', 'next_follow_up_at', 'follow_up_mode', 'source_conversations_json', 'created_at', 'updated_at', 'last_activity_at', 'owner_evidence_json'),
 'work_todo': ('id', 'project_id', 'title', 'owner_user_id', 'owner_name', 'status', 'priority', 'deadline_at', 'next_follow_up_at', 'follow_up_question', 'blocker', 'completion_evidence_json', 'created_from_update_id', 'created_at', 'updated_at', 'completed_at', 'description', 'owner_evidence_json')}

BUSINESS_FRESH_FIELDS = {'task': ('id',
          'title',
          'description',
          'stage',
          'origin',
          'suggestion_json',
          'status',
          'commitment_status',
          'formal_basis',
          'business_relevance',
          'merged_into_task_id',
          'owner_user_id',
          'owner_name',
          'owner_evidence_json',
          'deadline_at',
          'missing_evidence_json',
          'last_activity_at',
          'created_at',
          'updated_at'),
 'project': ('id',
             'canonical_anchor_id',
             'anchor_type',
             'title',
             'registry_source',
             'crm_customer_id',
             'crm_customer_name',
             'crm_customer_lookup_status',
             'crm_customer_candidates_json',
             'crm_customer_label',
             'crm_customer_evidence_json',
             'created_at'),
 'anchor': ('id', 'anchor_type', 'anchor_ref', 'title', 'active', 'created_at'),
 'project_context_revision': ('id', 'project_id', 'context_json', 'evidence_json', 'created_at'),
 'service_bugfix_candidate': ('id', 'feedback_event_key', 'feedback_token', 'attempt_id', 'status', 'title', 'reason', 'feedback_comment', 'conversation_title', 'trigger_text', 'created_at', 'updated_at'),
 'work_cluster': ('id', 'title', 'created_at'),
 'todo_evidence_candidate': ('id', 'project_id', 'todo_id', 'source_type', 'source_ref', 'source_created_at', 'evidence_text', 'reason', 'confidence', 'status', 'work_summary_input_id', 'decision_json', 'dedupe_key', 'created_at', 'updated_at'),
 'work_project': ('id', 'title', 'category', 'tags_json', 'status', 'priority', 'risk_level', 'needs_derek_attention', 'owner_user_id', 'owner_name', 'owner_evidence_json', 'related_people_json', 'goal', 'background', 'facts_json', 'current_state', 'blocker', 'next_step', 'next_follow_up_at', 'follow_up_mode', 'source_conversations_json', 'created_at', 'updated_at', 'last_activity_at'),
 'work_todo': ('id', 'project_id', 'title', 'description', 'owner_user_id', 'owner_name', 'owner_evidence_json', 'status', 'priority', 'deadline_at', 'next_follow_up_at', 'follow_up_question', 'blocker', 'completion_evidence_json', 'created_from_update_id', 'created_at', 'updated_at', 'completed_at')}

BUSINESS_TYPES = {'task': {'id': 'integer',
          'title': 'text',
          'description': 'text',
          'stage': 'text',
          'status': 'text',
          'commitment_status': 'text',
          'formal_basis': 'text',
          'business_relevance': 'text',
          'merged_into_task_id': 'integer',
          'owner_user_id': 'text',
          'owner_name': 'text',
          'owner_evidence_json': 'text',
          'deadline_at': 'text',
          'missing_evidence_json': 'text',
          'last_activity_at': 'text',
          'created_at': 'text',
          'updated_at': 'text',
          'origin': 'text',
          'suggestion_json': 'text'},
 'project': {'id': 'integer',
             'canonical_anchor_id': 'integer',
             'anchor_type': 'text',
             'title': 'text',
             'registry_source': 'text',
             'created_at': 'text',
             'crm_customer_id': 'text',
             'crm_customer_name': 'text',
             'crm_customer_lookup_status': 'text',
             'crm_customer_candidates_json': 'text',
             'crm_customer_label': 'text',
             'crm_customer_evidence_json': 'text'},
 'anchor': {'id': 'integer',
            'anchor_type': 'text',
            'anchor_ref': 'text',
            'title': 'text',
            'active': 'integer',
            'created_at': 'text'},
 'project_context_revision': {'id': 'integer',
                              'project_id': 'integer',
                              'context_json': 'text',
                              'evidence_json': 'text',
                              'created_at': 'text'},
 'service_bugfix_candidate': {'id': 'integer', 'feedback_event_key': 'text', 'feedback_token': 'text', 'attempt_id': 'integer', 'status': 'text', 'title': 'text', 'reason': 'text', 'feedback_comment': 'text', 'conversation_title': 'text', 'trigger_text': 'text', 'created_at': 'text', 'updated_at': 'text'},
 'work_cluster': {'id': 'integer', 'title': 'text', 'created_at': 'text'},
 'todo_evidence_candidate': {'id': 'integer', 'project_id': 'integer', 'todo_id': 'integer', 'source_type': 'text', 'source_ref': 'text', 'source_created_at': 'text', 'evidence_text': 'text', 'reason': 'text', 'confidence': 'real', 'status': 'text', 'work_summary_input_id': 'integer', 'decision_json': 'text', 'dedupe_key': 'text', 'created_at': 'text', 'updated_at': 'text'},
 'work_project': {'id': 'integer', 'title': 'text', 'category': 'text', 'tags_json': 'text', 'status': 'text', 'priority': 'text', 'risk_level': 'text', 'needs_derek_attention': 'integer', 'owner_user_id': 'text', 'owner_name': 'text', 'related_people_json': 'text', 'goal': 'text', 'background': 'text', 'facts_json': 'text', 'current_state': 'text', 'blocker': 'text', 'next_step': 'text', 'next_follow_up_at': 'text', 'follow_up_mode': 'text', 'source_conversations_json': 'text', 'created_at': 'text', 'updated_at': 'text', 'last_activity_at': 'text', 'owner_evidence_json': 'text'},
 'work_todo': {'id': 'integer', 'project_id': 'integer', 'title': 'text', 'owner_user_id': 'text', 'owner_name': 'text', 'status': 'text', 'priority': 'text', 'deadline_at': 'text', 'next_follow_up_at': 'text', 'follow_up_question': 'text', 'blocker': 'text', 'completion_evidence_json': 'text', 'created_from_update_id': 'integer', 'created_at': 'text', 'updated_at': 'text', 'completed_at': 'text', 'description': 'text', 'owner_evidence_json': 'text'}}

BUSINESS_NOT_NULL = {'task': ('title',
          'description',
          'stage',
          'status',
          'commitment_status',
          'business_relevance',
          'owner_user_id',
          'owner_name',
          'owner_evidence_json',
          'deadline_at',
          'missing_evidence_json',
          'last_activity_at',
          'created_at',
          'updated_at',
          'origin',
          'suggestion_json'),
 'project': ('canonical_anchor_id',
             'anchor_type',
             'title',
             'registry_source',
             'created_at',
             'crm_customer_id',
             'crm_customer_name',
             'crm_customer_lookup_status',
             'crm_customer_candidates_json',
             'crm_customer_label',
             'crm_customer_evidence_json'),
 'anchor': ('anchor_type', 'anchor_ref', 'title', 'active', 'created_at'),
 'project_context_revision': ('project_id', 'context_json', 'evidence_json', 'created_at'),
 'service_bugfix_candidate': ('feedback_event_key', 'feedback_token', 'attempt_id', 'status', 'title', 'reason', 'feedback_comment', 'conversation_title', 'trigger_text', 'created_at', 'updated_at'),
 'work_cluster': ('title', 'created_at'),
 'todo_evidence_candidate': ('project_id', 'todo_id', 'source_type', 'source_ref', 'source_created_at', 'evidence_text', 'reason', 'confidence', 'status', 'work_summary_input_id', 'decision_json', 'dedupe_key', 'created_at', 'updated_at'),
 'work_project': ('title', 'category', 'tags_json', 'status', 'priority', 'risk_level', 'needs_derek_attention', 'owner_user_id', 'owner_name', 'related_people_json', 'goal', 'background', 'facts_json', 'current_state', 'blocker', 'next_step', 'next_follow_up_at', 'follow_up_mode', 'source_conversations_json', 'created_at', 'updated_at', 'last_activity_at', 'owner_evidence_json'),
 'work_todo': ('project_id', 'title', 'owner_user_id', 'owner_name', 'status', 'priority', 'deadline_at', 'next_follow_up_at', 'follow_up_question', 'blocker', 'completion_evidence_json', 'created_from_update_id', 'created_at', 'updated_at', 'completed_at', 'description', 'owner_evidence_json')}

BUSINESS_DEFAULTS = {'task': {'description': "''",
          'status': "'open'",
          'commitment_status': "'none'",
          'business_relevance': "'unknown'",
          'owner_user_id': "''",
          'owner_name': "''",
          'owner_evidence_json': "'{}'",
          'deadline_at': "''",
          'missing_evidence_json': "'[]'",
          'last_activity_at': 'current_timestamp',
          'created_at': 'current_timestamp',
          'updated_at': 'current_timestamp',
          'origin': "'source'",
          'suggestion_json': "'{}'"},
 'project': {'anchor_type': "'project'",
             'created_at': 'current_timestamp',
             'crm_customer_id': "''",
             'crm_customer_name': "''",
             'crm_customer_lookup_status': "'not_requested'",
             'crm_customer_candidates_json': "'[]'",
             'crm_customer_label': "''",
             'crm_customer_evidence_json': "'{}'"},
 'anchor': {'active': '1', 'created_at': 'current_timestamp'},
 'project_context_revision': {'created_at': 'current_timestamp'},
 'service_bugfix_candidate': {'feedback_token': "''", 'attempt_id': '0', 'status': "'pending'", 'conversation_title': "''", 'trigger_text': "''", 'created_at': 'current_timestamp', 'updated_at': 'current_timestamp'},
 'work_cluster': {'created_at': 'current_timestamp'},
 'todo_evidence_candidate': {'source_created_at': "''", 'evidence_text': "''", 'reason': "''", 'confidence': '0', 'status': "'candidate'", 'work_summary_input_id': '0', 'decision_json': "'{}'", 'created_at': 'current_timestamp', 'updated_at': 'current_timestamp'},
 'work_project': {'category': "'other'", 'tags_json': "'[]'", 'status': "'active'", 'priority': "'none'", 'risk_level': "'none'", 'needs_derek_attention': '0', 'owner_user_id': "''", 'owner_name': "''", 'owner_evidence_json': "'{}'", 'related_people_json': "'[]'", 'goal': "''", 'background': "''", 'facts_json': "'[]'", 'current_state': "''", 'blocker': "''", 'next_step': "''", 'next_follow_up_at': "''", 'follow_up_mode': "'none'", 'source_conversations_json': "'[]'", 'created_at': 'current_timestamp', 'updated_at': 'current_timestamp', 'last_activity_at': 'current_timestamp'},
 'work_todo': {'owner_user_id': "''", 'owner_name': "''", 'status': "'open'", 'priority': "'none'", 'deadline_at': "''", 'next_follow_up_at': "''", 'follow_up_question': "''", 'blocker': "''", 'completion_evidence_json': "'{}'", 'created_from_update_id': '0', 'created_at': 'current_timestamp', 'updated_at': 'current_timestamp', 'completed_at': "''", 'description': "''", 'owner_evidence_json': "'{}'"}}

BUSINESS_CHECKS = {'task': ('trim(title, '
          'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
          "<> ''",
          "task_stage in ('candidate', 'formal')",
          'status in (\n'
          "                        'open', 'waiting', 'done', 'cancelled', 'merged'\n"
          '                    )',
          'task_commitment_status in (\n'
          "                        'none', 'assigned_unaccepted', 'accepted', 'disputed', "
          "'completed', 'cancelled'\n"
          '                    )',
          'task_formal_basis in (\n'
          "                        'explicit_assignment', 'explicit_commitment', 'external_todo', "
          "'meeting_action_item'\n"
          '                    )',
          'task_business_relevance in (\n'
          "                        'unknown', 'not_relevant', 'relevant'\n"
          '                    )',
          "json_valid(task_owner_evidence_json) and json_type(task_owner_evidence_json) = 'object'",
          'json_valid(task_missing_evidence_json) and json_type(task_missing_evidence_json) = '
          "'array'",
          "task_origin in ('source','agent_suggestion')",
          "json_valid(task_suggestion_json) and json_type(task_suggestion_json)='object'",
          '\n'
          "                        (task_stage = 'formal' and task_formal_basis is not null)\n"
          "                        or (task_stage = 'candidate' and task_formal_basis is null)\n"
          '                    ',
          '\n'
          "                        (status = 'merged' and task_merged_into_task_id is not null)\n"
          "                        or (status <> 'merged' and task_merged_into_task_id is null)\n"
          '                    ',
          'task_merged_into_task_id is null or task_merged_into_task_id <> public_id'),
 'project': ("project_anchor_type = 'project'",
             'trim(title, '
             'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
             "<> ''",
             'trim(project_registry_source, '
             'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
             "<> ''",
             "project_crm_customer_lookup_status in ('not_requested', 'matched', 'ambiguous', "
             "'needs_confirmation', 'no_match', 'unavailable', 'conflict')",
             'json_valid(project_crm_customer_candidates_json) and '
             "json_type(project_crm_customer_candidates_json) = 'array'",
             'json_valid(project_crm_customer_evidence_json) and '
             "json_type(project_crm_customer_evidence_json) = 'object'"),
 'anchor': ('anchor_anchor_type in (\n'
            "                        'project', 'okr', 'customer', 'product', 'revenue', "
            "'financing',\n"
            "                        'cash', 'key_hire', 'personnel', 'company_priority', "
            "'matter'\n"
            '                    )',
            'trim(anchor_anchor_ref, '
            'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
            "<> ''",
            'trim(title, '
            'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) '
            "<> ''",
            'anchor_active in (0, 1)'),
 'project_context_revision': (),
 'service_bugfix_candidate': (),
 'work_cluster': ('trim(title, char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)) <> \'\'',),
 'todo_evidence_candidate': ("status in ('candidate','enqueued','accepted','rejected','error')",),
 'work_project': (),
 'work_todo': ()}

# These two declarations are copied from the current source table contracts.
# Runtime consumers continue to use their typed projections.

BUSINESS_COMMON = (
    "title", "status", "created_at", "updated_at", "source_type", "source_ref",
    "source_created_at", "completed_at",
)
BUSINESS_REFERENCES = {
    "task": ("merged_into_task_id", "merged_task_ref", "task"),
    "project": ("canonical_anchor_id", "canonical_anchor_ref", "anchor"),
    "project_context_revision": ("project_id", "context_project_ref", "project"),
}


def business_column(kind, field):
    return "public_id" if field == "id" else field if field in BUSINESS_COMMON else kind + "_" + field


def business_payload_fields(kind):
    return tuple(f for f in BUSINESS_FIELDS[kind] if f != "id" and f not in BUSINESS_COMMON)


def _definitions(*, include_candidates=False, include_business_actions=False, fields=None):
    fields = BUSINESS_FIELDS if fields is None else fields
    kinds = fields
    if include_candidates:
        from app.business_candidate_definitions import CANDIDATE_FIELDS
        kinds = {**fields, **CANDIDATE_FIELDS}
    columns = [
        "id integer primary key autoincrement check(id>0)",
        "record_type text not null check(record_type in (" + ",".join(
            repr(kind) for kind in kinds
        ) + "))",
        "legacy_key text not null check(legacy_key=cast(cast(legacy_key as integer) as text))",
        "public_id integer generated always as (cast(legacy_key as integer)) virtual",
        "payload_json text not null check(json_valid(payload_json) and json_type(payload_json)='object')",
        "title text not null default ''", "status text not null default ''",
        "created_at text not null default current_timestamp", "updated_at text not null default ''",
        "source_type text not null default ''", "source_ref text not null default ''",
        "source_created_at text not null default ''", "completed_at text not null default ''",
        "merged_task_ref integer", "canonical_anchor_ref integer", "context_project_ref integer",
    ]
    columns.extend((
        "task_public_id integer generated always as "
        "(case when record_type='task' then public_id end) virtual",
        "project_public_id integer generated always as "
        "(case when record_type='project' then public_id end) virtual",
        "anchor_public_id integer generated always as "
        "(case when record_type='anchor' then public_id end) virtual",
        "work_cluster_public_id integer generated always as "
        "(case when record_type='work_cluster' then public_id end) virtual",
        "work_project_public_id integer generated always as "
        "(case when record_type='work_project' then public_id end) virtual",
        "work_todo_public_id integer generated always as "
        "(case when record_type='work_todo' then public_id end) virtual",
    ))
    constraints = []
    for kind, source_fields in fields.items():
        payload_fields = tuple(f for f in source_fields if f != "id" and f not in BUSINESS_COMMON)
        for field in payload_fields:
            affinity = BUSINESS_TYPES[kind][field]
            required = field in BUSINESS_NOT_NULL[kind]
            inactive = "''" if required and affinity == "text" else "0" if required else "null"
            value = _encoded_value("payload_json", "$." + field)
            columns.append(f"{business_column(kind, field)} {affinity} generated always as "
                           f"(case when record_type='{kind}' then {value} else {inactive} end) virtual"
                           + (" not null" if required else ""))
        paths = ",".join(repr("$." + f) for f in payload_fields)
        valid = [f"({_encoded_value_check('payload_json', '$.' + f)})" for f in payload_fields]
        valid.append(
            f"json_remove(payload_json,{paths})='{{}}'" if paths else "payload_json='{}'"
        )
        valid.extend(f"({expr})" for expr in BUSINESS_CHECKS[kind])
        constraints.append(f"check(record_type<>'{kind}' or ({' and '.join(valid)}))")
    for kind, (field, physical, parent) in BUSINESS_REFERENCES.items():
        source = business_column(kind, field)
        discriminator = physical + "_type"
        columns.append(f"{discriminator} text generated always as "
                       f"(case when record_type='{kind}' then '{parent}' end) virtual")
        constraints.append(f"check(case when record_type='{kind}' then "
                       f"({source} is null)=({physical} is null) else {physical} is null end)")
        extra_child = ",project_anchor_type" if kind == "project" else ""
        extra_parent = ",anchor_anchor_type" if kind == "project" else ""
        constraints.append(f"foreign key({physical},{discriminator},{source}{extra_child}) "
                       f"references business(id,record_type,public_id{extra_parent}) "
                       "on update no action on delete no action")
    if include_candidates:
        from app.business_candidate_definitions import candidate_schema_parts
        candidate_columns, candidate_checks, candidate_indexes = candidate_schema_parts(
            business_action_signals=include_business_actions)
        for kind in ('attention_item', 'project_candidate', 'wechat_memory_candidate'):
            columns.append(f"{kind}_public_id integer generated always as "
                           f"(case when record_type='{kind}' then public_id end) virtual")
        columns.extend(candidate_columns)
        constraints.extend(candidate_checks)
    table = "create table business (" + ",".join(columns + constraints) + ")"
    indexes = (
        "create unique index idx_business_public_id on business(record_type,public_id)",
        "create unique index idx_business_typed_id on business(id,record_type,public_id)",
        "create unique index idx_business_typed_anchor on business(id,record_type,public_id,anchor_anchor_type)",
        "create index idx_business_task_relevance on business(task_business_relevance,updated_at,public_id) where record_type='task'",
        "create index idx_business_task_stage on business(task_stage,status,updated_at,public_id) where record_type='task'",
        "create index idx_business_task_updated on business(updated_at,public_id) where record_type='task'",
        "create unique index idx_business_project_anchor on business(project_canonical_anchor_id) where record_type='project'",
        "create unique index idx_business_anchor_identity on business(anchor_anchor_type,anchor_anchor_ref) where record_type='anchor'",
        "create unique index idx_business_anchor_public_type on business(public_id,anchor_anchor_type) where record_type='anchor'",
        "create index idx_business_context_project on business(project_context_revision_project_id,public_id desc) where record_type='project_context_revision'",
        "create unique index idx_business_bugfix_feedback_event_key on business(service_bugfix_candidate_feedback_event_key) where record_type='service_bugfix_candidate'",
        "create index idx_business_bugfix_status on business(status,created_at) where record_type='service_bugfix_candidate'",
        "create unique index idx_business_task_public_id on business(task_public_id)",
        "create unique index idx_business_project_public_id on business(project_public_id)",
        "create unique index idx_business_anchor_public_id on business(anchor_public_id)",
        "create unique index idx_business_work_cluster_public_id on business(work_cluster_public_id)",
        "create unique index idx_business_work_project_public_id on business(work_project_public_id)",
        "create unique index idx_business_work_todo_public_id on business(work_todo_public_id)",
        "create index idx_business_work_project_status_priority on business(status,work_project_priority,updated_at) where record_type='work_project'",
        "create index idx_business_work_todo_project_status on business(work_todo_project_id,status) where record_type='work_todo'",
        "create index idx_business_work_todo_follow_up on business(status,work_todo_next_follow_up_at) where record_type='work_todo'",
        "create unique index idx_business_todo_evidence_dedupe_key on business(todo_evidence_candidate_dedupe_key) where record_type='todo_evidence_candidate'",
        "create index idx_business_todo_evidence_todo on business(todo_evidence_candidate_todo_id,status,public_id) where record_type='todo_evidence_candidate'",
        "create index idx_business_todo_evidence_status on business(status,public_id) where record_type='todo_evidence_candidate'",
        "create index idx_business_todo_evidence_work_input on business(todo_evidence_candidate_work_summary_input_id) where record_type='todo_evidence_candidate'",
        "create index idx_business_todo_evidence_project on business(todo_evidence_candidate_project_id,public_id desc) where record_type='todo_evidence_candidate'",
    )
    if include_candidates:
        indexes += tuple(candidate_indexes)
        indexes += tuple(
            f"create unique index idx_business_{kind}_public_id on business({kind}_public_id)"
            for kind in ('attention_item', 'project_candidate', 'wechat_memory_candidate')
        )
    triggers = tuple(
        f"create trigger trg_business_{kind}_sequence before insert on business "
        f"when new.record_type='{kind}' begin "
        f"insert into sqlite_sequence(name,seq) select 'business:{kind}',0 where not exists "
        f"(select 1 from sqlite_sequence where name='business:{kind}'); "
        f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) "
        f"where name='business:{kind}'; end"
        for kind in kinds
    )
    return table, indexes, triggers


def _projection(kind, fields, *, index=None):
    relation = "business" + (f" indexed by {index}" if index else "")
    return ("select "
            + ",".join(business_column(kind, f) + " as " + f for f in fields)
            + f" from {relation} where record_type='{kind}'")


BUSINESS_PROJECTIONS = {k: _projection(k, fields) for k, fields in BUSINESS_FIELDS.items()}
BUSINESS_FRESH_PROJECTIONS = {k: _projection(k, fields) for k, fields in BUSINESS_FRESH_FIELDS.items()}
BUSINESS_TASK_LIST_PROJECTION = _projection(
    "task", BUSINESS_FIELDS["task"], index="idx_business_task_updated"
)
