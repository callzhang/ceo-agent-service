"""Unselected seven-kind Email configs declaration and source projections."""

from app.action_email_definitions import _encoded_value, _encoded_value_check

CONFIG_FIELDS = {
    "email_accounts": tuple("""account_id display_name email_address imap_host imap_port
        imap_tls imap_username imap_secret_reference smtp_host smtp_port smtp_tls
        smtp_username smtp_secret_reference enabled scan_folders_json
        scan_interval_seconds created_at updated_at imap_move_mode agent_lookback_days
        scan_read_state model_lookback_days""".split()),
    "email_category_configs": tuple("""category_key display_name core_description
        include_json exclude_json threshold actions_json action_parameters_json enabled
        description_version config_version updated_at""".split()),
    "email_category_description_revisions": tuple(
        "revision_id category_key config_version config_json created_at".split()),
    "email_category_folder_bindings": tuple(
        "account_id category_key provider_folder_id provider_folder_name binding_status last_verified_at".split()),
    "email_model_mode_transitions": tuple("""request_id actor from_mode to_mode
        from_model_id target_model_id promotion_config_version status reason created_at""".split()),
    "email_model_promotion_configs": tuple("""config_version micro_f1_min category_precision_min
        category_validation_samples_min p95_latency_max_ms created_at""".split()),
    "email_training_snapshots": tuple("""snapshot_id snapshot_version description_version
        input_schema_version seed observed_at snapshot_digest manifest_json frozen created_at
        folder_label_watermark important_label_watermark""".split()),
}
CONFIG_FIELDS.update({
"managed_skills": tuple("id name display_name created_at".split()),
"managed_skill_revisions": tuple("id skill_id revision_number content sha256 parent_revision_id source created_at".split()),
"runtime_feedback_iteration_capabilities": tuple("config_id enabled created_at".split()),
"runtime_skill_bindings": tuple("config_id skill_id revision_id enabled load_order purpose".split()),
"runtime_skill_configs": tuple("id parent_id status created_at".split()),
"scheduled_task_config_versions": tuple("id snapshot_json".split()),
"scheduled_task_skill_refs": tuple("scheduled_task_id skill_source skill_name managed_skill_id managed_revision_id position".split()),
"wechat_reply_scopes": tuple("account_id target_type target_id conversation_id display_name trigger_mode enabled binding_status binding_evidence_json disabled_reason last_discovered_at updated_at".split()),
})
CONFIG_FRESH_FIELDS = {
    **CONFIG_FIELDS,
    "email_accounts": tuple("""account_id display_name email_address imap_host imap_port
        imap_tls imap_username imap_secret_reference imap_move_mode smtp_host smtp_port
        smtp_tls smtp_username smtp_secret_reference enabled scan_folders_json
        scan_interval_seconds created_at updated_at agent_lookback_days
        model_lookback_days scan_read_state""".split()),
}
PREFIX = {
    "email_accounts": "account", "email_category_configs": "category",
    "email_category_description_revisions": "revision",
    "email_category_folder_bindings": "binding",
    "email_model_mode_transitions": "transition",
    "email_model_promotion_configs": "promotion",
    "email_training_snapshots": "snapshot",
}
PREFIX.update({
"managed_skills": "skill", "managed_skill_revisions": "skill_revision",
"runtime_feedback_iteration_capabilities": "feedback_capability",
"runtime_skill_bindings": "skill_binding", "runtime_skill_configs": "runtime_config",
"scheduled_task_config_versions": "schedule_config",
"scheduled_task_skill_refs": "schedule_skill_ref", "wechat_reply_scopes": "wechat_scope",
})
ROWID_KINDS = (
    "email_accounts", "email_category_configs",
    "email_category_folder_bindings", "email_training_snapshots",
)
ROWID_KINDS += (
"managed_skills", "managed_skill_revisions", "runtime_feedback_iteration_capabilities",
"runtime_skill_bindings", "runtime_skill_configs", "scheduled_task_config_versions",
"scheduled_task_skill_refs", "wechat_reply_scopes",
)
WITHOUT_ROWID_KINDS = (
    "email_category_description_revisions", "email_model_mode_transitions",
    "email_model_promotion_configs",
)
SCALAR_KEY = {
    "email_accounts": "account_id", "email_category_configs": "category_key",
    "email_category_description_revisions": "revision_id",
    "email_model_mode_transitions": "request_id",
    "email_model_promotion_configs": "config_version",
    "email_training_snapshots": "snapshot_id",
}
SCALAR_KEY.update({
"managed_skills": "id", "managed_skill_revisions": "id",
"runtime_feedback_iteration_capabilities": "config_id", "runtime_skill_configs": "id",
"scheduled_task_config_versions": "id",
})
PHYSICAL = {
    "email_accounts": ("account_id",),
    "email_category_configs": ("category_key",),
    "email_category_description_revisions": (),
    "email_category_folder_bindings": ("account_id", "category_key"),
    "email_model_mode_transitions": ("promotion_config_version",),
    "email_model_promotion_configs": (),
    "email_training_snapshots": ("snapshot_id", "snapshot_digest", "frozen"),
}
PHYSICAL.update({
# aliases, not copied into payload_json.
"managed_skills": ("id",),
"managed_skill_revisions": ("id",),
"runtime_feedback_iteration_capabilities": ("config_id",),
"runtime_skill_bindings": (),
"runtime_skill_configs": ("id", "status"),
"scheduled_task_config_versions": ("id",),
"scheduled_task_skill_refs": (),
"wechat_reply_scopes": (),
})
DEFAULTS = {
    "email_accounts": {"imap_move_mode": "'move'", "agent_lookback_days": "30",
                       "scan_read_state": "'unread'", "model_lookback_days": "365"},
    "email_training_snapshots": {"frozen": "1", "folder_label_watermark": "0",
                                 "important_label_watermark": "0"},
}
DEFAULTS.update({
"managed_skills": {"created_at": "current_timestamp"},
"managed_skill_revisions": {"created_at": "current_timestamp"},
"runtime_feedback_iteration_capabilities": {"created_at": "current_timestamp"},
"runtime_skill_bindings": {"purpose": "''"},
"runtime_skill_configs": {"created_at": "current_timestamp"},
"wechat_reply_scopes": {"conversation_id": "''", "enabled": "1",
    "binding_status": "'unverified'", "binding_evidence_json": "'{}'",
    "disabled_reason": "''", "last_discovered_at": "''",
    "updated_at": "current_timestamp"},
})
INTEGERS = {
    "email_accounts": set("""imap_port imap_tls smtp_port smtp_tls enabled
        scan_interval_seconds agent_lookback_days model_lookback_days""".split()),
    "email_category_configs": {"enabled"},
    "email_model_promotion_configs": {"category_validation_samples_min"},
    "email_training_snapshots": {"seed", "frozen", "folder_label_watermark",
                                 "important_label_watermark"},
}
INTEGERS.update({
"managed_skills": {"id"},
"managed_skill_revisions": {"id", "skill_id", "revision_number", "parent_revision_id"},
"runtime_feedback_iteration_capabilities": {"config_id", "enabled"},
"runtime_skill_bindings": {"config_id", "skill_id", "revision_id", "enabled", "load_order"},
"runtime_skill_configs": {"id", "parent_id"},
"scheduled_task_config_versions": {"id"},
"scheduled_task_skill_refs": {"scheduled_task_id", "managed_skill_id", "managed_revision_id", "position"},
"wechat_reply_scopes": {"enabled"},
})
REALS = {
    "email_category_configs": {"threshold"},
    "email_model_promotion_configs": {"micro_f1_min", "category_precision_min",
                                       "p95_latency_max_ms"},
}
REQUIRED = {
    "email_accounts": set(CONFIG_FIELDS["email_accounts"]) - {"account_id"},
    "email_category_configs": set(CONFIG_FIELDS["email_category_configs"]) - {"category_key"},
    "email_category_description_revisions": set(CONFIG_FIELDS["email_category_description_revisions"]),
    "email_category_folder_bindings": set(CONFIG_FIELDS["email_category_folder_bindings"]),
    "email_model_mode_transitions": set(CONFIG_FIELDS["email_model_mode_transitions"]) -
                                    {"from_model_id", "target_model_id"},
    "email_model_promotion_configs": set(CONFIG_FIELDS["email_model_promotion_configs"]),
    "email_training_snapshots": set(CONFIG_FIELDS["email_training_snapshots"]) - {"snapshot_id"},
}
REQUIRED.update({
# omitted on INSERT because SQLite allocates them. Every other required source
# column remains required after source defaults are applied.
"managed_skills": {"name", "display_name", "created_at"},
"managed_skill_revisions": {"skill_id", "revision_number", "content", "sha256", "source", "created_at"},
"runtime_feedback_iteration_capabilities": {"config_id", "enabled", "created_at"},
"runtime_skill_bindings": set(CONFIG_FIELDS["runtime_skill_bindings"]),
"runtime_skill_configs": {"status", "created_at"},
"scheduled_task_config_versions": {"snapshot_json"},
"scheduled_task_skill_refs": {"scheduled_task_id", "skill_source", "skill_name", "position"},
"wechat_reply_scopes": set(CONFIG_FIELDS["wechat_reply_scopes"]),
})


NUMERIC_SCALAR_KINDS = frozenset({
    "managed_skills", "managed_skill_revisions",
    "runtime_feedback_iteration_capabilities", "runtime_skill_configs",
    "scheduled_task_config_versions",
})
AUTOINCREMENT_KINDS = frozenset({
    "managed_skills", "managed_skill_revisions", "runtime_skill_configs",
})

def config_column(kind: str, field: str) -> str:
    if (kind, field) == ("runtime_skill_configs", "status"):
        return "runtime_config_status"
    if field == SCALAR_KEY.get(kind):
        return "numeric_public_key" if kind in NUMERIC_SCALAR_KINDS else "public_key"
    return PREFIX[kind] + "_" + field


def config_payload_fields(kind: str) -> tuple[str, ...]:
    return tuple(field for field in CONFIG_FIELDS[kind]
                 if field not in PHYSICAL[kind] and field != SCALAR_KEY.get(kind))

RULES = {
    "email_accounts": (
        "account_imap_port between 1 and 65535",
        "account_imap_tls in (0,1)", "account_smtp_port between 1 and 65535",
        "account_smtp_tls in (0,1)", "account_enabled in (0,1)",
        "json_valid(account_scan_folders_json)",
        "account_scan_interval_seconds>0",
        "account_imap_move_mode in ('move','copy_as_move')",
        "account_agent_lookback_days between 1 and 365",
        "account_scan_read_state in ('unread','all')",
        "account_model_lookback_days between 1 and 3650",
    ),
    "email_category_configs": (
        "trim(public_key)!=''", "trim(category_display_name)!=''",
        "trim(category_core_description)!=''",
        "json_valid(category_include_json)",
        "json_type(category_include_json)='array'",
        "json_array_length(category_include_json)>0",
        "trim(json_extract(category_include_json,'$[0]'))!=''",
        "json_valid(category_exclude_json)",
        "json_type(category_exclude_json)='array'",
        "json_array_length(category_exclude_json)>0",
        "trim(json_extract(category_exclude_json,'$[0]'))!=''",
        "category_threshold>=0.0 and category_threshold<=1.0",
        "json_valid(category_actions_json)",
        "json_valid(category_action_parameters_json)",
        "category_enabled in (0,1)",
        "trim(category_description_version)!=''",
        "trim(category_config_version)!=''",
        "trim(category_updated_at)!=''",
    ),
    "email_category_description_revisions": (
        "trim(public_key)!=''", "trim(revision_category_key)!=''",
        "trim(revision_config_version)!=''", "json_valid(revision_config_json)",
        "trim(revision_created_at)!=''",
    ),
    "email_category_folder_bindings": (
        "trim(binding_account_id)!=''", "trim(binding_category_key)!=''",
        "binding_provider_folder_name!=''",
        "binding_binding_status in ('active','missing','ambiguous','error')",
        "trim(binding_last_verified_at)!=''",
        "binding_binding_status!='active' or binding_provider_folder_id!=''",
    ),
    "email_model_mode_transitions": (
        "trim(public_key)!=''", "trim(transition_actor)!=''",
        "transition_from_mode in ('agent_primary','model_primary')",
        "transition_to_mode in ('agent_primary','model_primary')",
        "transition_from_model_id is null or trim(transition_from_model_id)!=''",
        "transition_target_model_id is null or trim(transition_target_model_id)!=''",
        "trim(transition_promotion_config_version)!=''",
        "transition_status in ('applied','rejected','failed')",
        "trim(transition_created_at)!=''",
    ),
    "email_model_promotion_configs": (
        "trim(public_key)!=''",
        "promotion_micro_f1_min>=0 and promotion_micro_f1_min<=1",
        "promotion_category_precision_min>=0 and promotion_category_precision_min<=1",
        "typeof(promotion_category_validation_samples_min)='integer' "
        "and promotion_category_validation_samples_min>0",
        "promotion_p95_latency_max_ms>0 and "
        "(promotion_p95_latency_max_ms-promotion_p95_latency_max_ms) is 0.0",
        "trim(promotion_created_at)!=''",
    ),
    "email_training_snapshots": (
        "trim(public_key)!=''", "trim(snapshot_snapshot_version)!=''",
        "trim(snapshot_description_version)!=''",
        "trim(snapshot_input_schema_version)!=''",
        "snapshot_seed>=0", "trim(snapshot_observed_at)!=''",
        "length(snapshot_snapshot_digest)=64",
        "json_valid(snapshot_manifest_json)",
        "snapshot_frozen in (0,1)", "trim(snapshot_created_at)!=''",
        "snapshot_folder_label_watermark>=0",
        "snapshot_important_label_watermark>=0",
    ),
}
RULES.update({
"managed_skills": (),
"managed_skill_revisions": ("skill_revision_revision_number>0",),
"runtime_feedback_iteration_capabilities": ("feedback_capability_enabled in (0,1)",),
"runtime_skill_bindings": ("skill_binding_enabled in (0,1)", "skill_binding_load_order>=0"),
"runtime_skill_configs": ("runtime_config_status in ('pending_restart','active','load_failed')",),
"scheduled_task_config_versions": (),
"scheduled_task_skill_refs": ("schedule_skill_ref_position>=0",),
"wechat_reply_scopes": (),
})

# (child kind, source field, physical ref column, parent kind, delete action)
CONFIG_REFS = (
    ("managed_skill_revisions", "skill_id", "skill_revision_skill_ref", "managed_skills", "no action"),
    ("managed_skill_revisions", "parent_revision_id", "skill_revision_parent_ref", "managed_skill_revisions", "no action"),
    ("runtime_feedback_iteration_capabilities", "config_id", "feedback_capability_config_ref", "runtime_skill_configs", "no action"),
    ("runtime_skill_bindings", "config_id", "skill_binding_config_ref", "runtime_skill_configs", "no action"),
    ("runtime_skill_bindings", "skill_id", "skill_binding_skill_ref", "managed_skills", "no action"),
    ("runtime_skill_bindings", "revision_id", "skill_binding_revision_ref", "managed_skill_revisions", "no action"),
    ("runtime_skill_configs", "parent_id", "runtime_config_parent_ref", "runtime_skill_configs", "no action"),
    ("scheduled_task_skill_refs", "managed_skill_id", "schedule_skill_ref_skill_ref", "managed_skills", "no action"),
    ("scheduled_task_skill_refs", "managed_revision_id", "schedule_skill_ref_revision_ref", "managed_skill_revisions", "no action"),
)
SCHEDULE_REF = ("scheduled_task_skill_refs", "scheduled_task_id", "schedule_skill_ref_schedule_ref")

def _affinity(kind: str, field: str) -> str:
    if field in INTEGERS.get(kind, ()):
        return "integer"
    if field in REALS.get(kind, ()):
        return "real"
    return "text"


def _definitions():
    names = ",".join(repr(kind) for kind in CONFIG_FIELDS)
    wr = ",".join(repr(kind) for kind in WITHOUT_ROWID_KINDS)
    decoded_key = _encoded_value("legacy_key", "$[0]")
    columns = [
        "id integer primary key autoincrement check(id>0)",
        f"record_type text not null check(record_type in ({names}))",
        "legacy_key text not null",
        "payload_json text not null check(typeof(payload_json)='text' "
        "and json_valid(payload_json) and json_type(payload_json)='object')",
        "source_key text",
        "binding_account_id text", "binding_category_key text",
        "binding_account_ref integer", "binding_category_ref integer",
        "transition_promotion_config_version text", "transition_promotion_ref integer",
        "snapshot_snapshot_digest text", "snapshot_frozen integer",
        f"public_key text generated always as (case when record_type in ({wr}) "
        f"then {decoded_key} else source_key end) virtual",
        "numeric_public_key integer generated always as (case when record_type in "
        "('managed_skills','managed_skill_revisions','runtime_feedback_iteration_capabilities',"
        "'runtime_skill_configs','scheduled_task_config_versions') "
        "then cast(source_key as integer) end) virtual",
        "schedule_snapshot_public_id integer generated always as "
        "(case when record_type='scheduled_task_config_versions' "
        "then numeric_public_key end) virtual",
        "runtime_config_status text",
        "binding_account_kind text generated always as "
        "(case when record_type='email_category_folder_bindings' then 'email_accounts' end) virtual",
        "binding_category_kind text generated always as "
        "(case when record_type='email_category_folder_bindings' then 'email_category_configs' end) virtual",
        "transition_promotion_kind text generated always as "
        "(case when record_type='email_model_mode_transitions' "
        "then 'email_model_promotion_configs' end) virtual",
    ]
    checks = [
        "check(case when record_type in (" +
        ",".join(repr(kind) for kind in ROWID_KINDS) +
        ") then legacy_key=cast(cast(legacy_key as integer) as text) "
        "else json_valid(legacy_key) and json_type(legacy_key)='array' "
        "and json_array_length(legacy_key)=1 end)",
        "check((record_type in ('email_accounts','email_category_configs',"
        "'email_training_snapshots','managed_skills','managed_skill_revisions',"
        "'runtime_feedback_iteration_capabilities','runtime_skill_configs',"
        "'scheduled_task_config_versions')) or source_key is null)",
        "check(record_type='runtime_skill_configs' or runtime_config_status is null)",
        "check(record_type not in ('managed_skills','managed_skill_revisions',"
        "'runtime_feedback_iteration_capabilities','runtime_skill_configs',"
        "'scheduled_task_config_versions') or (source_key is not null and source_key=legacy_key))",
        "check(record_type='email_category_folder_bindings' "
        "or (binding_account_id is null and binding_category_key is null "
        "and binding_account_ref is null and binding_category_ref is null))",
        "check(record_type='email_model_mode_transitions' "
        "or (transition_promotion_config_version is null "
        "and transition_promotion_ref is null))",
        "check(record_type='email_training_snapshots' "
        "or (snapshot_snapshot_digest is null and snapshot_frozen is null))",
        "check(record_type<>'email_category_folder_bindings' or "
        "(binding_account_ref is not null and binding_category_ref is not null))",
        "check(record_type<>'email_model_mode_transitions' "
        "or transition_promotion_ref is not null)",
        "foreign key(binding_account_ref,binding_account_kind,binding_account_id) "
        "references configs(id,record_type,public_key) "
        "on update no action on delete cascade",
        "foreign key(binding_category_ref,binding_category_kind,binding_category_key) "
        "references configs(id,record_type,public_key) "
        "on update no action on delete cascade",
        "foreign key(transition_promotion_ref,transition_promotion_kind,"
        "transition_promotion_config_version) "
        "references configs(id,record_type,public_key) "
        "on update no action on delete restrict",
    ]
    for kind in CONFIG_FIELDS:
        for field in config_payload_fields(kind):
            name = config_column(kind, field)
            value = _encoded_value("payload_json", "$." + field)
            columns.append(f"{name} {_affinity(kind, field)} generated always as "
                           f"(case when record_type='{kind}' then {value} end) virtual")
        payload_fields = config_payload_fields(kind)
        paths = ",".join(repr("$." + field) for field in payload_fields)
        conditions = [
            f"json_type(payload_json,'$.{field}') is not null"
            for field in payload_fields
        ]
        conditions += [
            _encoded_value_check("payload_json", "$." + field)
            for field in payload_fields
        ]
        conditions.append(f"json_remove(payload_json,{paths})='{{}}'")
        conditions += [
            f"{config_column(kind, field)} is not null"
            for field in CONFIG_FIELDS[kind] if field in REQUIRED[kind]
        ]
        conditions += [f"({rule})" for rule in RULES[kind]]
        checks.append(f"check(record_type<>'{kind}' or "
                      + " and ".join(f"({condition})" for condition in conditions) + ")")
    for child, field, ref, parent, on_delete in CONFIG_REFS:
        source = config_column(child, field)
        # The scalar config_id of feedback capability is numeric_public_key.
        kind_col, key_col = ref + "_kind", ref + "_public_key"
        columns.extend((
            f"{ref} integer",
            f"{kind_col} text generated always as "
            f"(case when record_type='{child}' then '{parent}' end) virtual",
            f"{key_col} text generated always as "
            f"(case when record_type='{child}' then cast({source} as text) end) virtual",
        ))
        checks.append(f"check(record_type='{child}' or {ref} is null)")
        checks.append(f"check(record_type<>'{child}' or "
                      f"(({source} is null and {ref} is null) or "
                      f"({source} is not null and {ref} is not null)))")
        checks.append(f"foreign key({ref},{kind_col},{key_col}) "
                      f"references configs(id,record_type,public_key) "
                      f"on update no action on delete {on_delete}")
    child, field, ref = SCHEDULE_REF
    source = config_column(child, field)
    columns.append(f"{ref} integer references schedules(id) on update no action on delete no action")
    checks += (f"check(record_type='{child}' or {ref} is null)",
               f"check(record_type<>'{child}' or ({source} is not null and {ref}={source}))")
    table = "create table configs (" + ",".join(columns + checks) + ")"
    indexes = (
        "create unique index idx_configs_legacy on configs(record_type,legacy_key)",
        "create unique index idx_configs_public on configs(record_type,public_key)",
        "create unique index idx_configs_typed_public "
        "on configs(id,record_type,public_key)",
        "create unique index idx_configs_schedule_snapshot_public_id "
        "on configs(schedule_snapshot_public_id)",
        "create unique index idx_configs_binding_identity on "
        "configs(binding_account_id,binding_category_key) "
        "where record_type='email_category_folder_bindings'",
        "create unique index idx_configs_binding_active_provider on "
        "configs(binding_account_id,binding_provider_folder_id) "
        "where record_type='email_category_folder_bindings' "
        "and binding_binding_status='active'",
        "create unique index idx_configs_snapshot_digest on "
        "configs(snapshot_snapshot_digest) "
        "where record_type='email_training_snapshots'",
    )
    indexes += (
        "create unique index idx_configs_skill_name on configs(skill_name) where record_type='managed_skills'",
        "create unique index idx_configs_revision_identity on configs(skill_revision_skill_id,skill_revision_sha256,skill_revision_source) where record_type='managed_skill_revisions'",
        "create unique index idx_configs_revision_number on configs(skill_revision_skill_id,skill_revision_revision_number) where record_type='managed_skill_revisions'",
        "create unique index idx_configs_binding_skill on configs(skill_binding_config_id,skill_binding_skill_id) where record_type='runtime_skill_bindings'",
        "create unique index idx_configs_binding_order on configs(skill_binding_config_id,skill_binding_load_order) where record_type='runtime_skill_bindings'",
        "create unique index idx_configs_schedule_snapshot on configs(schedule_config_snapshot_json) where record_type='scheduled_task_config_versions'",
        "create unique index idx_configs_schedule_ref_position on configs(schedule_skill_ref_scheduled_task_id,schedule_skill_ref_position) where record_type='scheduled_task_skill_refs'",
        "create unique index idx_configs_wechat_scope on configs(wechat_scope_account_id,wechat_scope_target_type,wechat_scope_target_id) where record_type='wechat_reply_scopes'",
        "create index idx_configs_binding_config_order on configs(skill_binding_config_id,skill_binding_load_order) where record_type='runtime_skill_bindings'",
    )
    immutable = WITHOUT_ROWID_KINDS
    triggers = []
    for kind in immutable:
        short = PREFIX[kind]
        triggers.extend((
            f"create trigger trg_configs_{short}_duplicate before insert on configs "
            f"when new.record_type='{kind}' and exists("
            f"select 1 from configs where record_type='{kind}' "
            "and public_key is new.public_key) "
            "begin select raise(abort,'immutable model control history'); end",
            f"create trigger trg_configs_{short}_update before update on configs "
            f"when old.record_type='{kind}' "
            "begin select raise(abort,'immutable model control history'); end",
            f"create trigger trg_configs_{short}_delete before delete on configs "
            f"when old.record_type='{kind}' "
            "begin select raise(abort,'immutable model control history'); end",
        ))
    frozen_same = " and ".join(
        f"old.{config_column('email_training_snapshots', field)} is "
        f"new.{config_column('email_training_snapshots', field)}"
        for field in CONFIG_FIELDS["email_training_snapshots"] if field != "frozen"
    )
    triggers.extend((
        "create trigger trg_configs_snapshot_delete before delete on configs "
        "when old.record_type='email_training_snapshots' "
        "begin select raise(abort,'email training snapshot is immutable'); end",
        "create trigger trg_configs_snapshot_update before update on configs "
        "when old.record_type='email_training_snapshots' and not "
        f"(old.snapshot_frozen=0 and new.snapshot_frozen=1 and {frozen_same}) "
        "begin select raise(abort,'email training snapshot is immutable'); end",
    ))
    for kind, error in (
        ("managed_skill_revisions", "managed Skill revisions are immutable"),
        ("runtime_feedback_iteration_capabilities", "feedback iteration capability records are immutable"),
        ("runtime_skill_bindings", "runtime Skill bindings are immutable"),
    ):
        for event in ("update", "delete"):
            triggers.append(f"create trigger trg_configs_{PREFIX[kind]}_{event} "
                            f"before {event} on configs when old.record_type='{kind}' "
                            f"begin select raise(abort,'{error}'); end")
    triggers += (
        "create trigger trg_configs_runtime_config_delete before delete on configs "
        "when old.record_type='runtime_skill_configs' "
        "begin select raise(abort,'runtime Skill configurations are immutable'); end",
        "create trigger trg_configs_runtime_config_update before update on configs "
        "when old.record_type='runtime_skill_configs' and ("
        "new.id<>old.id or new.legacy_key<>old.legacy_key or "
        "new.runtime_config_parent_ref is not old.runtime_config_parent_ref or "
        "new.runtime_config_created_at<>old.runtime_config_created_at or "
        "old.runtime_config_status<>'pending_restart' or "
        "new.runtime_config_status not in ('active','load_failed') or "
        "(new.runtime_config_status='active' and not exists("
        "select 1 from actions a where a.record_type='runtime_skill_load_receipts' "
        "and a.runtime_config_ref=old.id and a.skill_error='' )) or "
        "(new.runtime_config_status='load_failed' and not exists("
        "select 1 from actions a where a.record_type='runtime_skill_load_receipts' "
        "and a.runtime_config_ref=old.id and a.skill_error<>'' ))) "
        "begin select raise(abort,'runtime Skill activation requires matching load receipt'); end",
    )
    blockers = (
        ("direct", "action", "processing", "email_direct_action_in_flight"),
        ("reply", "email_reply_dispatch_claims", "dispatching",
         "email_reply_dispatch_in_flight"),
        ("unsubscribe", "email_unsubscribe_claims", "dispatching",
         "email_unsubscribe_write_in_flight"),
    )
    for short, kind, status, error in blockers:
        for event in ("update", "delete"):
            identity_column = "action_account_id" if kind == "action" else "email_account_id"
            status_column = "action_status" if kind == "action" else "email_status"
            operations = (
                " and a.action_operation in ('archive','flag_important','mark_read','move','trash','label')"
                if kind == "action" else ""
            )
            triggers.append(
                f"create trigger trg_configs_{short}_account_{event} "
                f"before {event} on configs when old.record_type='email_accounts' "
                "and exists(select 1 from actions a "
                f"where a.record_type='{kind}' and a.{identity_column}=old.public_key{operations} "
                f"and a.{status_column}='{status}') "
                f"begin select raise(abort,'{error}'); end"
            )
    for kind in AUTOINCREMENT_KINDS:
        name = "configs:" + kind
        triggers.append(
            f"create trigger trg_configs_{kind}_sequence before insert on configs "
            f"when new.record_type='{kind}' begin "
            f"insert into sqlite_sequence(name,seq) select '{name}',0 "
            f"where not exists(select 1 from sqlite_sequence where name='{name}'); "
            f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) "
            f"where name='{name}'; end"
        )
    return table, indexes, tuple(triggers)


CONFIG_TABLE_DDL, CONFIG_INDEX_DDL, CONFIG_TRIGGER_DDL = _definitions()


def config_projection(kind: str, *, fresh=False, include_rowid=False) -> str:
    if kind not in CONFIG_FIELDS:
        raise ValueError("unsupported Email config kind")
    fields = CONFIG_FRESH_FIELDS[kind] if fresh else CONFIG_FIELDS[kind]
    selected = [f"{config_column(kind, field)} as {field}" for field in fields]
    if include_rowid:
        if kind not in ROWID_KINDS:
            raise ValueError("WITHOUT ROWID config has no source rowid")
        selected.insert(0, "cast(legacy_key as integer) as rowid")
    return "select " + ",".join(selected) + " from configs where record_type=" + repr(kind)


CONFIG_PROJECTIONS = {kind: config_projection(kind) for kind in CONFIG_FIELDS}
CONFIG_FRESH_PROJECTIONS = {
    kind: config_projection(kind, fresh=True) for kind in CONFIG_FIELDS
}
