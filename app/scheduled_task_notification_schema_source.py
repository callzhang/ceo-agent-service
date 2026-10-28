"""Exact immediately previous current schema before the schedule notification field."""
from functools import lru_cache
import sqlite3

from app.model_retirement_schema_source import store26_objects
from app.schedules_definitions import SCHEDULE_INDEXES

PREVIOUS_SCHEDULES_DDL = """create table schedules (
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


@lru_cache(maxsize=2)
def previous_current_objects(*, email_ledger: bool = False) -> tuple:
    """Build the exact prior declaration, changing only the schedules table."""
    current = store26_objects(email_ledger=email_ledger)
    with sqlite3.connect(":memory:") as db:
        db.execute(PREVIOUS_SCHEDULES_DDL)
        for sql in SCHEDULE_INDEXES:
            db.execute(sql)
        previous_schedule = tuple(tuple(row) for row in db.execute(
            "select type,name,tbl_name,sql from sqlite_schema "
            "where name not glob 'sqlite_*' order by type,name"
        ))
    replacement_names = {item[1] for item in previous_schedule}
    combined = [item for item in current if item[1] not in replacement_names]
    combined.extend(previous_schedule)
    return tuple(sorted(combined, key=lambda item: (item[0], item[1])))
