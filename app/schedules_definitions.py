"""Unselected one-kind schedules declaration."""
SCHEDULE_FIELDS = tuple("id migration_key name prompt cron_expression timezone runtime_id runtime_options_json working_directory enabled version created_at updated_at deleted_at required_runtime_capabilities_json command description".split())
SCHEDULE_FRESH_FIELDS = tuple("id migration_key name description prompt command cron_expression timezone runtime_id runtime_options_json required_runtime_capabilities_json working_directory enabled version created_at updated_at deleted_at".split())
SCHEDULE_DDL = """create table schedules (
 id integer primary key autoincrement,
 migration_key text, name text not null, prompt text not null,
 cron_expression text not null, timezone text not null, runtime_id text not null,
 runtime_options_json text not null default '{}',
 working_directory text not null default '',
 enabled integer not null check(enabled in (0,1)),
 version integer not null default 1 check(version>0),
 created_at text not null default current_timestamp,
 updated_at text not null default current_timestamp,
 deleted_at text,
 required_runtime_capabilities_json text not null default '[]',
 command text not null default '', description text not null default ''
)"""
SCHEDULE_INDEXES = (
    "create index idx_schedules_enabled on schedules(enabled,deleted_at,id)",
    "create unique index idx_schedules_migration_key on schedules(migration_key) where migration_key is not null",
)

def schedule_projection(*, fresh: bool = False) -> str:
    fields = SCHEDULE_FRESH_FIELDS if fresh else SCHEDULE_FIELDS
    return "select " + ",".join(fields) + " from schedules"
