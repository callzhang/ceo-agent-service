from app.action_email_definitions import _encoded_value, _encoded_value_check

SKILL_RECEIPT_KINDS = ("managed_skill_export_receipts", "runtime_skill_load_receipts")

def skill_receipt_schema_parts():
    def value(field):
        return _encoded_value("payload_json", "$." + field)
    generated = (
        f"skill_revision_id integer generated always as (case when record_type='managed_skill_export_receipts' then {value('revision_id')} end) virtual",
        f"skill_sha256 text generated always as (case when record_type='managed_skill_export_receipts' then {value('sha256')} end) virtual",
        f"skill_path text generated always as (case when record_type='managed_skill_export_receipts' then {value('path')} end) virtual",
        f"skill_config_id integer generated always as (case when record_type='runtime_skill_load_receipts' then {value('config_id')} end) virtual",
        f"skill_pid integer generated always as (case when record_type='runtime_skill_load_receipts' then {value('pid')} end) virtual",
        f"skill_error text generated always as (case when record_type='runtime_skill_load_receipts' then {value('error')} end) virtual",
        f"skill_created_at text generated always as ({value('created_at')}) virtual",
        "skill_revision_kind text generated always as (case when record_type='managed_skill_export_receipts' then 'managed_skill_revisions' end) virtual",
        "skill_config_kind text generated always as (case when record_type='runtime_skill_load_receipts' then 'runtime_skill_configs' end) virtual",
        "skill_revision_public_key text generated always as (cast(skill_revision_id as text)) virtual",
        "skill_config_public_key text generated always as (cast(skill_config_id as text)) virtual",
    )
    physical = ("skill_revision_ref integer", "runtime_config_ref integer")
    checks = (
        "check(record_type<>'managed_skill_export_receipts' or (skill_revision_ref is not null and skill_revision_id is not null and skill_sha256 is not null and skill_path is not null and skill_created_at is not null and json_remove(payload_json,'$.revision_id','$.sha256','$.path','$.created_at')='{}'))",
        "check(record_type<>'runtime_skill_load_receipts' or (runtime_config_ref is not null and skill_config_id is not null and skill_pid>0 and skill_error is not null and skill_created_at is not null and json_remove(payload_json,'$.config_id','$.pid','$.error','$.created_at')='{}'))",
        "check(record_type in ('managed_skill_export_receipts','runtime_skill_load_receipts') or (skill_revision_ref is null and runtime_config_ref is null))",
        "check(record_type<>'managed_skill_export_receipts' or runtime_config_ref is null)",
        "check(record_type<>'runtime_skill_load_receipts' or skill_revision_ref is null)",
        "foreign key(skill_revision_ref,skill_revision_kind,skill_revision_public_key) references configs(id,record_type,public_key)",
        "foreign key(runtime_config_ref,skill_config_kind,skill_config_public_key) references configs(id,record_type,public_key)",
    )
    checks += tuple(
        f"check(record_type<>'{kind}' or {_encoded_value_check('payload_json', '$.' + field)})"
        for kind, fields in (
            ("managed_skill_export_receipts", ("revision_id", "sha256", "path", "created_at")),
            ("runtime_skill_load_receipts", ("config_id", "pid", "error", "created_at")),
        )
        for field in fields
    )
    indexes = (
        "create index idx_actions_skill_export_revision on actions(skill_revision_ref,cast(legacy_key as integer)) where record_type='managed_skill_export_receipts'",
        "create index idx_actions_skill_load_config on actions(runtime_config_ref,cast(legacy_key as integer)) where record_type='runtime_skill_load_receipts'",
        "create index idx_actions_skill_load_pid on actions(skill_pid,cast(legacy_key as integer)) where record_type='runtime_skill_load_receipts'",
    )
    triggers = []
    for kind, message in (("managed_skill_export_receipts", "managed Skill export receipts are append-only"),
                          ("runtime_skill_load_receipts", "runtime Skill load receipts are append-only")):
        for event in ("update", "delete"):
            triggers.append(f"create trigger trg_actions_{kind}_{event} before {event} on actions "
                            f"when old.record_type='{kind}' begin select raise(abort,'{message}'); end")
        name = "actions:" + kind
        triggers.append(f"create trigger trg_actions_{kind}_sequence before insert on actions "
                        f"when new.record_type='{kind}' begin "
                        f"insert into sqlite_sequence(name,seq) select '{name}',0 "
                        f"where not exists(select 1 from sqlite_sequence where name='{name}'); "
                        f"update sqlite_sequence set seq=max(seq,cast(new.legacy_key as integer)) "
                        f"where name='{name}'; end")
    return generated, physical, checks, indexes, tuple(triggers)
