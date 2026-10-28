"""Finite, unselected Business candidate declarations."""

from app.action_email_definitions import _encoded_value, _encoded_value_check

CANDIDATE_FIELDS = {'attention_item': ('id',
                    'stable_key',
                    'category',
                    'status',
                    'title',
                    'business_area',
                    'why_attention',
                    'current_state',
                    'ceo_action',
                    'anchor_id',
                    'evidence_signal_id',
                    'resolution_signal_id',
                    'resolved_at',
                    'created_at',
                    'updated_at',
                    'assessment_json'),
 'project_candidate': ('id',
                       'cluster_id',
                       'title',
                       'reason',
                       'status',
                       'confirmed_project_id',
                       'confirmation_signal_id',
                       'created_at'),
 'wechat_memory_candidate': ('id',
                             'import_run_id',
                             'account_id',
                             'statement',
                             'edited_statement',
                             'category',
                             'confidence',
                             'sensitivity',
                             'source_conversation_ids_json',
                             'source_message_ids_json',
                             'source_time_start',
                             'source_time_end',
                             'evidence_excerpt',
                             'cleanup_notes',
                             'status',
                             'reviewer',
                             'reviewed_at',
                             'memory_write_status',
                             'memory_id',
                             'created_at',
                             'updated_at',
                             'memory_write_error')}

CANDIDATE_TYPES = {'attention_item': {'id': 'integer',
                    'stable_key': 'text',
                    'category': 'text',
                    'status': 'text',
                    'title': 'text',
                    'business_area': 'text',
                    'why_attention': 'text',
                    'current_state': 'text',
                    'ceo_action': 'text',
                    'anchor_id': 'integer',
                    'evidence_signal_id': 'integer',
                    'resolution_signal_id': 'integer',
                    'resolved_at': 'text',
                    'created_at': 'text',
                    'updated_at': 'text',
                    'assessment_json': 'text'},
 'project_candidate': {'id': 'integer',
                       'cluster_id': 'integer',
                       'title': 'text',
                       'reason': 'text',
                       'status': 'text',
                       'confirmed_project_id': 'integer',
                       'confirmation_signal_id': 'integer',
                       'created_at': 'text'},
 'wechat_memory_candidate': {'id': 'integer',
                             'import_run_id': 'text',
                             'account_id': 'text',
                             'statement': 'text',
                             'edited_statement': 'text',
                             'category': 'text',
                             'confidence': 'real',
                             'sensitivity': 'text',
                             'source_conversation_ids_json': 'text',
                             'source_message_ids_json': 'text',
                             'source_time_start': 'text',
                             'source_time_end': 'text',
                             'evidence_excerpt': 'text',
                             'cleanup_notes': 'text',
                             'status': 'text',
                             'reviewer': 'text',
                             'reviewed_at': 'text',
                             'memory_write_status': 'text',
                             'memory_id': 'text',
                             'created_at': 'text',
                             'updated_at': 'text',
                             'memory_write_error': 'text'}}

CANDIDATE_NOT_NULL = {'attention_item': ('stable_key',
                    'category',
                    'status',
                    'title',
                    'business_area',
                    'why_attention',
                    'current_state',
                    'ceo_action',
                    'anchor_id',
                    'evidence_signal_id',
                    'resolved_at',
                    'created_at',
                    'updated_at',
                    'assessment_json'),
 'project_candidate': ('cluster_id', 'title', 'reason', 'status', 'created_at'),
 'wechat_memory_candidate': ('import_run_id',
                             'account_id',
                             'statement',
                             'edited_statement',
                             'category',
                             'confidence',
                             'sensitivity',
                             'source_conversation_ids_json',
                             'source_message_ids_json',
                             'source_time_start',
                             'source_time_end',
                             'evidence_excerpt',
                             'cleanup_notes',
                             'status',
                             'reviewer',
                             'reviewed_at',
                             'memory_write_status',
                             'memory_id',
                             'created_at',
                             'updated_at',
                             'memory_write_error')}

CANDIDATE_DEFAULTS = {'attention_item': {'status': "'active'",
                    'business_area': "''",
                    'resolved_at': "''",
                    'created_at': 'current_timestamp',
                    'updated_at': 'current_timestamp',
                    'assessment_json': "'{}'"},
 'project_candidate': {'status': "'proposed'", 'created_at': 'current_timestamp'},
 'wechat_memory_candidate': {'edited_statement': "''",
                             'source_conversation_ids_json': "'[]'",
                             'source_message_ids_json': "'[]'",
                             'source_time_start': "''",
                             'source_time_end': "''",
                             'evidence_excerpt': "''",
                             'cleanup_notes': "''",
                             'status': "'pending'",
                             'reviewer': "''",
                             'reviewed_at': "''",
                             'memory_write_status': "''",
                             'memory_id': "''",
                             'created_at': 'current_timestamp',
                             'updated_at': 'current_timestamp',
                             'memory_write_error': "''"}}

CANDIDATE_KINDS = tuple(CANDIDATE_FIELDS)
CANDIDATE_REFERENCES = {
    'attention_item': (('anchor_id', 'anchor_public_id', 'business'),
                       ('evidence_signal_id', 'id', 'business_task_signals'),
                       ('resolution_signal_id', 'id', 'business_task_signals')),
    'project_candidate': (('cluster_id', 'work_cluster_public_id', 'business'),
                          ('confirmed_project_id', 'project_public_id', 'business'),
                          ('confirmation_signal_id', 'id', 'business_task_signals')),
    'wechat_memory_candidate': (),
}
TRIM_WS = 'char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)'


def candidate_column(kind, field):
    return 'public_id' if field == 'id' else field if field in ('title', 'status', 'created_at', 'updated_at') else kind + '_' + field


def candidate_payload_fields(kind):
    return tuple(f for f in CANDIDATE_FIELDS[kind]
                 if f not in ('id', 'title', 'status', 'created_at', 'updated_at'))


def candidate_schema_parts(*, business_action_signals=False):
    columns, checks, indexes = [], [], []
    for kind in CANDIDATE_KINDS:
        payload = candidate_payload_fields(kind)
        for field in payload:
            value = _encoded_value('payload_json', '$.' + field)
            columns.append(f"{candidate_column(kind, field)} {CANDIDATE_TYPES[kind][field]} "
                           f"generated always as (case when record_type='{kind}' then {value} end) virtual")
        valid = [_encoded_value_check('payload_json', '$.' + field) for field in payload]
        valid.extend(f'{candidate_column(kind, field)} is not null'
                     for field in CANDIDATE_NOT_NULL[kind] if field != 'id')
        valid.append("json_remove(payload_json," + ','.join(repr('$.' + f) for f in payload) + ")='{}'")
        if kind == 'attention_item':
            valid.extend((
                f"trim(attention_item_stable_key,{TRIM_WS})<>''",
                "attention_item_category in ('fyi','watch','decision','push')",
                "status in ('active','resolved')",
                *(f"trim({field},{TRIM_WS})<>''" for field in
                  ('title', 'attention_item_why_attention', 'attention_item_current_state',
                   'attention_item_ceo_action')),
                "((status='active' and attention_item_resolution_signal_id is null and attention_item_resolved_at='')"
                " or (status='resolved' and attention_item_resolution_signal_id is not null and "
                f"trim(attention_item_resolved_at,{TRIM_WS})<>''))",
            ))
            indexes.extend((
                "create unique index idx_business_attention_stable_key on business(attention_item_stable_key) where record_type='attention_item'",
                "create index idx_business_attention_list on business(status,attention_item_category,updated_at,public_id) where record_type='attention_item'",
            ))
        elif kind == 'project_candidate':
            valid.extend((
                f"trim(title,{TRIM_WS})<>''", f"trim(project_candidate_reason,{TRIM_WS})<>''",
                "status in ('proposed','confirmed','rejected')",
                "(status='confirmed')=(project_candidate_confirmed_project_id is not null)",
                "(status='confirmed')=(project_candidate_confirmation_signal_id is not null)",
            ))
            indexes.append("create index idx_business_project_candidate_cluster on business(project_candidate_cluster_id,status,public_id) where record_type='project_candidate'")
        else:
            indexes.append("create unique index idx_business_wechat_candidate_identity on business(wechat_memory_candidate_import_run_id,wechat_memory_candidate_statement) where record_type='wechat_memory_candidate'")
        checks.append(f"check(record_type<>'{kind}' or ({' and '.join('(' + x + ')' for x in valid)}))")
        for field, endpoint, parent in CANDIDATE_REFERENCES[kind]:
            if business_action_signals and parent == 'business_task_signals':
                parent, endpoint = 'actions', 'business_signal_public_id'
            checks.append(f"foreign key({candidate_column(kind, field)}) references {parent}({endpoint}) on update no action on delete no action")
    return columns, checks, indexes


def candidate_projection(kind, with_rowid=False):
    if kind not in CANDIDATE_KINDS:
        raise ValueError('unsupported Business candidate projection')
    fields = CANDIDATE_FIELDS[kind]
    selected = (['public_id as _source_rowid'] if with_rowid else []) + [
        candidate_column(kind, field) + ' as ' + field for field in fields]
    return 'select ' + ','.join(selected) + f" from business where record_type='{kind}'"
