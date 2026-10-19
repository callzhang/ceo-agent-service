"""Finite State declarations from the approved actual/fresh native inventory.

Only retained native fields are stored. Runtime does not load source artifacts.
Schema/caller selection and native-table retirement belong to the release.
"""
from app.action_email_definitions import _encoded_value, _encoded_value_check

STATE_FIELDS = {'channel_login_reservations': ('channel', 'reservation_owner', 'reserved_at'),
 'codex_session_locks': ('conversation_id', 'owner', 'locked_at'),
 'conversation_runtime_sessions': ('conversation_id',
                                   'route_name',
                                   'session_id',
                                   'contract_hash',
                                   'updated_at'),
 'conversations': ('conversation_id',
                   'title',
                   'single_chat',
                   'codex_session_id',
                   'codex_session_contract_hash'),
 'daily_scan_state': ('scanner_name', 'last_success_at', 'cursor_json', 'last_error', 'updated_at'),
 'dispatcher_claim_leases': ('adapter_name',
                             'source_id',
                             'owner',
                             'owner_pid',
                             'generation',
                             'lease_expires_at',
                             'terminal_at',
                             'last_error',
                             'created_at',
                             'updated_at'),
 'email_historical_traversals': ('account_id',
                                 'folder',
                                 'model_id',
                                 'uidvalidity',
                                 'last_seen_uid',
                                 'updated_at'),
 'email_scan_cursors': ('account_id',
                        'folder',
                        'uidvalidity',
                        'last_seen_uid',
                        'last_success_at',
                        'last_error'),
 'org_cache_metadata': ('key', 'value_json', 'updated_at'),
 'org_user_profiles': ('user_id',
                       'name',
                       'open_dingtalk_id',
                       'manager_user_id',
                       'department_ids_json',
                       'fetched_at',
                       'title',
                       'manager_name',
                       'department_names_json',
                       'org_labels_json',
                       'has_subordinate'),
 'runtime_route_pauses': ('route_name', 'failure_code', 'retry_at', 'opened_at', 'updated_at'),
 'seen_messages': ('message_id', 'conversation_id', 'seen_at'),
 'service_state': ('key', 'value', 'updated_at'),
 'setup_wizard_steps': ('step_id',
                        'status',
                        'summary',
                        'manual_confirmed_at',
                        'manual_confirmed_by',
                        'updated_at'),
 'wechat_read_state': ('account_id',
                       'account_dir',
                       'db_dir',
                       'app_version',
                       'self_user_id',
                       'capability_status',
                       'capability_reason',
                       'watermark_sent_at',
                       'watermark_message_id',
                       'last_scan_at',
                       'updated_at')}

STATE_TYPES = {'account_dir': 'TEXT',
 'account_id': 'TEXT',
 'adapter_name': 'TEXT',
 'app_version': 'TEXT',
 'capability_reason': 'TEXT',
 'capability_status': 'TEXT',
 'channel': 'TEXT',
 'codex_session_contract_hash': 'TEXT',
 'codex_session_id': 'TEXT',
 'contract_hash': 'TEXT',
 'conversation_id': 'TEXT',
 'created_at': 'TEXT',
 'cursor_json': 'TEXT',
 'db_dir': 'TEXT',
 'department_ids_json': 'TEXT',
 'department_names_json': 'TEXT',
 'failure_code': 'TEXT',
 'fetched_at': 'TEXT',
 'folder': 'TEXT',
 'generation': 'INTEGER',
 'has_subordinate': 'INTEGER',
 'key': 'TEXT',
 'last_error': 'TEXT',
 'last_scan_at': 'TEXT',
 'last_seen_uid': 'INTEGER',
 'last_success_at': 'TEXT',
 'lease_expires_at': 'TEXT',
 'locked_at': 'TEXT',
 'manager_name': 'TEXT',
 'manager_user_id': 'TEXT',
 'manual_confirmed_at': 'TEXT',
 'manual_confirmed_by': 'TEXT',
 'message_id': 'TEXT',
 'model_id': 'TEXT',
 'name': 'TEXT',
 'open_dingtalk_id': 'TEXT',
 'opened_at': 'TEXT',
 'org_labels_json': 'TEXT',
 'owner': 'TEXT',
 'owner_pid': 'INTEGER',
 'reservation_owner': 'TEXT',
 'reserved_at': 'TEXT',
 'retry_at': 'TEXT',
 'route_name': 'TEXT',
 'scanner_name': 'TEXT',
 'seen_at': 'TEXT',
 'self_user_id': 'TEXT',
 'session_id': 'TEXT',
 'single_chat': 'INTEGER',
 'source_id': 'TEXT',
 'status': 'TEXT',
 'step_id': 'TEXT',
 'summary': 'TEXT',
 'terminal_at': 'TEXT',
 'title': 'TEXT',
 'uidvalidity': 'INTEGER',
 'updated_at': 'TEXT',
 'user_id': 'TEXT',
 'value': 'TEXT',
 'value_json': 'TEXT',
 'watermark_message_id': 'TEXT',
 'watermark_sent_at': 'TEXT'}

STATE_NOT_NULL = {'channel_login_reservations': ('reservation_owner', 'reserved_at'),
 'codex_session_locks': ('owner', 'locked_at'),
 'conversation_runtime_sessions': ('conversation_id',
                                   'route_name',
                                   'session_id',
                                   'contract_hash',
                                   'updated_at'),
 'conversations': ('title', 'single_chat', 'codex_session_contract_hash'),
 'daily_scan_state': ('last_success_at', 'cursor_json', 'last_error', 'updated_at'),
 'dispatcher_claim_leases': ('adapter_name',
                             'source_id',
                             'owner',
                             'owner_pid',
                             'generation',
                             'lease_expires_at',
                             'terminal_at',
                             'last_error',
                             'created_at',
                             'updated_at'),
 'email_historical_traversals': ('account_id', 'folder', 'model_id', 'last_seen_uid', 'updated_at'),
 'email_scan_cursors': ('account_id',
                        'folder',
                        'uidvalidity',
                        'last_seen_uid',
                        'last_success_at',
                        'last_error'),
 'org_cache_metadata': ('value_json', 'updated_at'),
 'org_user_profiles': ('name',
                       'department_ids_json',
                       'fetched_at',
                       'title',
                       'manager_name',
                       'department_names_json',
                       'org_labels_json'),
 'runtime_route_pauses': ('failure_code', 'retry_at', 'opened_at', 'updated_at'),
 'seen_messages': ('conversation_id', 'seen_at'),
 'service_state': ('value', 'updated_at'),
 'setup_wizard_steps': ('status',
                        'summary',
                        'manual_confirmed_at',
                        'manual_confirmed_by',
                        'updated_at'),
 'wechat_read_state': ('account_dir',
                       'db_dir',
                       'app_version',
                       'self_user_id',
                       'capability_status',
                       'capability_reason',
                       'watermark_sent_at',
                       'watermark_message_id',
                       'last_scan_at',
                       'updated_at')}

STATE_PRIMARY_KEYS = {'channel_login_reservations': ('channel',),
 'codex_session_locks': ('conversation_id',),
 'conversation_runtime_sessions': ('conversation_id', 'route_name'),
 'conversations': ('conversation_id',),
 'daily_scan_state': ('scanner_name',),
 'dispatcher_claim_leases': ('adapter_name', 'source_id'),
 'email_historical_traversals': ('account_id', 'folder', 'model_id'),
 'email_scan_cursors': ('account_id', 'folder'),
 'org_cache_metadata': ('key',),
 'org_user_profiles': ('user_id',),
 'runtime_route_pauses': ('route_name',),
 'seen_messages': ('message_id',),
 'service_state': ('key',),
 'setup_wizard_steps': ('step_id',),
 'wechat_read_state': ('account_id',)}

STATE_DEFAULTS = {'channel_login_reservations': {},
 'codex_session_locks': {'locked_at': 'current_timestamp'},
 'conversation_runtime_sessions': {'contract_hash': "''", 'updated_at': 'current_timestamp'},
 'conversations': {'codex_session_contract_hash': "''"},
 'daily_scan_state': {'last_success_at': "''",
                      'cursor_json': "'{}'",
                      'last_error': "''",
                      'updated_at': 'current_timestamp'},
 'dispatcher_claim_leases': {'owner': "''",
                             'owner_pid': '0',
                             'generation': '0',
                             'lease_expires_at': "''",
                             'terminal_at': "''",
                             'last_error': "''",
                             'created_at': 'current_timestamp',
                             'updated_at': 'current_timestamp'},
 'email_historical_traversals': {'last_seen_uid': '0'},
 'email_scan_cursors': {'last_success_at': "''", 'last_error': "''"},
 'org_cache_metadata': {'updated_at': 'current_timestamp'},
 'org_user_profiles': {'name': "''",
                       'fetched_at': 'current_timestamp',
                       'title': "''",
                       'manager_name': "''",
                       'department_names_json': "'[]'",
                       'org_labels_json': "'[]'"},
 'runtime_route_pauses': {'opened_at': 'current_timestamp', 'updated_at': 'current_timestamp'},
 'seen_messages': {'seen_at': 'current_timestamp'},
 'service_state': {'updated_at': 'current_timestamp'},
 'setup_wizard_steps': {'summary': "''",
                        'manual_confirmed_at': "''",
                        'manual_confirmed_by': "''",
                        'updated_at': 'current_timestamp'},
 'wechat_read_state': {'self_user_id': "''",
                       'capability_status': "'blocked'",
                       'capability_reason': "''",
                       'watermark_sent_at': "''",
                       'watermark_message_id': "''",
                       'last_scan_at': "''",
                       'updated_at': 'current_timestamp'}}


STATE_CHECKS = {
    "dispatcher_claim_leases": ("generation>=0",),
    "email_scan_cursors": ("uidvalidity>0", "last_seen_uid>=0"),
}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _definitions():
    kinds = tuple(STATE_FIELDS)
    parts = [
        "id integer primary key",
        "record_type text not null",
        "legacy_key text not null check(legacy_key=cast(cast(legacy_key as integer) as text))",
        "payload_json text not null check(json_valid(payload_json) and json_type(payload_json)='object')",
    ]
    for field, affinity in STATE_TYPES.items():
        scope = ",".join(repr(kind) for kind in kinds if field in STATE_FIELDS[kind])
        expression = _encoded_value("payload_json", "$." + field)
        parts.append(_quote(field) + " " + affinity + " generated always as "
                     "(case when record_type in (" + scope + ") then " + expression + " end) virtual")
    parts.append("check(record_type in (" + ",".join(map(repr, kinds)) + "))")
    for kind, fields in STATE_FIELDS.items():
        terms = ["json_type(payload_json,'$." + field + "') is not null" for field in fields]
        terms.extend(_encoded_value_check("payload_json", "$." + field) for field in fields)
        terms.extend(_quote(field) + " is not null" for field in STATE_NOT_NULL[kind])
        paths = ",".join(repr("$." + field) for field in fields)
        terms.append("json_remove(payload_json," + paths + ")='{}'")
        terms.extend(STATE_CHECKS.get(kind, ()))
        parts.append("check(record_type<>" + repr(kind) + " or (" +
                     " and ".join("(" + term + ")" for term in terms) + "))")
    indexes = [
        "create unique index idx_state_source_rowid on state(record_type,cast(legacy_key as integer))",
    ]
    for kind, fields in STATE_PRIMARY_KEYS.items():
        indexes.append("create unique index " + _quote("idx_state_" + kind + "_pk") +
                       " on state(" + ",".join(map(_quote, fields)) +
                       ") where record_type=" + repr(kind))
    indexes.extend((
        "create index idx_state_dispatcher_terminal on state(record_type,terminal_at,cast(legacy_key as integer)) "
        "where record_type='dispatcher_claim_leases' and terminal_at<>''",
        "create index idx_state_dispatcher_expiry on state(adapter_name,lease_expires_at,source_id) "
        "where record_type='dispatcher_claim_leases'",
        "create index idx_state_org_name on state(name,cast(legacy_key as integer)) where record_type='org_user_profiles'",
        "create index idx_state_org_open_id on state(open_dingtalk_id,cast(legacy_key as integer)) where record_type='org_user_profiles'",
    ))
    return "create table state(" + ",".join(parts) + ")", tuple(indexes)


STATE_TABLE_DDL, STATE_INDEX_DDL = _definitions()


def state_projection(kind, *, include_rowid=False):
    if kind not in STATE_FIELDS:
        raise ValueError("unsupported State kind")
    fields = ["cast(legacy_key as integer) as rowid"] if include_rowid else []
    fields.extend(map(_quote, STATE_FIELDS[kind]))
    return "select " + ",".join(fields) + " from state where record_type=" + repr(kind)
