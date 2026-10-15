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
ROWID_KINDS = (
    "email_accounts", "email_category_configs",
    "email_category_folder_bindings", "email_training_snapshots",
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
PHYSICAL = {
    "email_accounts": ("account_id",),
    "email_category_configs": ("category_key",),
    "email_category_description_revisions": (),
    "email_category_folder_bindings": ("account_id", "category_key"),
    "email_model_mode_transitions": ("promotion_config_version",),
    "email_model_promotion_configs": (),
    "email_training_snapshots": ("snapshot_id", "snapshot_digest", "frozen"),
}
DEFAULTS = {
    "email_accounts": {"imap_move_mode": "'move'", "agent_lookback_days": "30",
                       "scan_read_state": "'unread'", "model_lookback_days": "365"},
    "email_training_snapshots": {"frozen": "1", "folder_label_watermark": "0",
                                 "important_label_watermark": "0"},
}
INTEGERS = {
    "email_accounts": set("""imap_port imap_tls smtp_port smtp_tls enabled
        scan_interval_seconds agent_lookback_days model_lookback_days""".split()),
    "email_category_configs": {"enabled"},
    "email_model_promotion_configs": {"category_validation_samples_min"},
    "email_training_snapshots": {"seed", "frozen", "folder_label_watermark",
                                 "important_label_watermark"},
}
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


def config_column(kind: str, field: str) -> str:
    if field == SCALAR_KEY.get(kind):
        return "public_key"
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
        "'email_training_snapshots')) or source_key is null)",
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
            for field in REQUIRED[kind]
        ]
        conditions += [f"({rule})" for rule in RULES[kind]]
        checks.append(f"check(record_type<>'{kind}' or "
                      + " and ".join(f"({condition})" for condition in conditions) + ")")
    table = "create table configs (" + ",".join(columns + checks) + ")"
    indexes = (
        "create unique index idx_configs_legacy on configs(record_type,legacy_key)",
        "create unique index idx_configs_public on configs(record_type,public_key)",
        "create unique index idx_configs_typed_public "
        "on configs(id,record_type,public_key)",
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
    blockers = (
        ("direct", "email_actions", "processing", "email_direct_action_in_flight"),
        ("reply", "email_reply_dispatch_claims", "dispatching",
         "email_reply_dispatch_in_flight"),
        ("unsubscribe", "email_unsubscribe_claims", "dispatching",
         "email_unsubscribe_write_in_flight"),
    )
    for short, kind, status, error in blockers:
        for event in ("update", "delete"):
            triggers.append(
                f"create trigger trg_configs_{short}_account_{event} "
                f"before {event} on configs when old.record_type='email_accounts' "
                "and exists(select 1 from actions a "
                f"where a.record_type='{kind}' and a.email_account_id=old.public_key "
                f"and a.email_status='{status}') "
                f"begin select raise(abort,'{error}'); end"
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
