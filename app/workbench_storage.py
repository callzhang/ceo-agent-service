"""Exact source13 -> current scheduled/Workbench storage and paired inverse.

The caller owns the private FK-off transaction. Historical typed rows keep
both physical and public identities; Workbench TEXT keys retain SQL values.
"""

from contextlib import closing
from functools import lru_cache
import sqlite3

from app.task_storage import (
    TEXT_TASK_TYPES,
    WORKBENCH_TASK_COLUMNS,
    WORKBENCH_TASK_SQL,
    WORKBENCH_TURN_SQL,
    _capture_table,
    _declared_task_objects,
    _rebuild,
    create_task_schema,
    insert_task,
    install_task_child_guards,
    task_schema_is_current,
    validate_reply_input_references,
    validate_meeting_run_references,
)
from app.run_storage import (
    SCHEDULED_RUN_COLUMNS,
    WORKBENCH_EVENT_COLUMNS,
    SCHEDULED_RUN_SQL,
    WORKBENCH_EVENT_SQL,
    _schema_objects,
    _declared_run_objects,
    _drop_explicit_objects,
    _set_sequence,
    create_run_schema,
    insert_run,
    run_schema_is_current,
)

SOURCE_VERSION = "2026-10-11.3"
WORKBENCH_VERSION = "2026-10-12.1"
RETIRED_WORKBENCH_TABLES = (
    "scheduled_task_runs",
    "workbench_tasks",
    "workbench_turns",
    "workbench_events",
)
WORKBENCH_CHILDREN = (
    "workbench_attachments",
    "workbench_artifacts",
    "workbench_confirmations",
)
SEARCH_RETIRED_COLUMNS = (
    "summary_text",
    "fts_text",
    "embedding_json",
    "embedding_model",
    "embedding_updated_at",
)
SEARCH_COLUMNS = (
    "id",
    "session_id",
    "source_type",
    "source_id",
    "title",
    "created_at",
    "updated_at",
)
WORKBENCH_PROJECTIONS = {
    "workbench_tasks": WORKBENCH_TASK_SQL,
    "workbench_turns": WORKBENCH_TURN_SQL,
    "scheduled_task_runs": SCHEDULED_RUN_SQL,
    "workbench_events": WORKBENCH_EVENT_SQL,
}
WORKBENCH_COLUMNS = dict(
    WORKBENCH_TASK_COLUMNS,
    scheduled_task_runs=SCHEDULED_RUN_COLUMNS,
    workbench_events=WORKBENCH_EVENT_COLUMNS,
)
# Frozen actual source13 declarations, independently captured from 44a1a4e.
SOURCE_DECLARATIONS = {
    "scheduled_task_runs": (
        (
            "index",
            "idx_scheduled_task_runs_claim",
            "CREATE INDEX idx_scheduled_task_runs_claim\n"
            "                    on scheduled_task_runs(dispatch_status, scheduled_for, id)",
        ),
        (
            "index",
            "idx_scheduled_task_runs_dispatch",
            "CREATE INDEX idx_scheduled_task_runs_dispatch\n"
            "                    on scheduled_task_runs(\n"
            "                        dispatch_status, lease_expires_at, scheduled_for, id\n"
            "                    )",
        ),
        (
            "index",
            "idx_scheduled_task_runs_latest",
            "CREATE INDEX idx_scheduled_task_runs_latest\n"
            "                    on scheduled_task_runs(scheduled_task_id, id desc)",
        ),
        (
            "index",
            "idx_scheduled_task_runs_reply_execution",
            "CREATE INDEX idx_scheduled_task_runs_reply_execution\n"
            "                    on scheduled_task_runs(\n"
            "                        cast(execution_id as integer), scheduled_task_id, id\n"
            "                    )\n"
            "                    where execution_kind='reply_task'",
        ),
        (
            "index",
            "idx_scheduled_task_runs_scheduled_instant",
            "CREATE UNIQUE INDEX idx_scheduled_task_runs_scheduled_instant\n"
            "                    on scheduled_task_runs(scheduled_task_id, scheduled_for)\n"
            "                    where trigger_kind = 'scheduled'",
        ),
        (
            "table",
            "scheduled_task_runs",
            "CREATE TABLE scheduled_task_runs (\n"
            "                    id integer primary key autoincrement,\n"
            "                    event_id text not null unique,\n"
            "                    scheduled_task_id integer not null,\n"
            "                    trigger_kind text not null check(trigger_kind in (\n"
            "                        'scheduled', 'manual'\n"
            "                    )),\n"
            "                    scheduled_for text not null,\n"
            "                    dispatch_status text not null default 'pending' check(\n"
            "                        dispatch_status in (\n"
            "                            'pending', 'dispatched', 'skipped', 'failed'\n"
            "                        )\n"
            "                    ),\n"
            "                    skip_or_error_reason text not null default '',\n"
            "                    execution_kind text not null default '',\n"
            "                    execution_id text not null default '',\n"
            "                    lease_owner text not null default '',\n"
            "                    lease_expires_at text,\n"
            "                    created_at text not null default current_timestamp,\n"
            "                    dispatched_at text, first_scheduled_for text not null default "
            "'', occurrence_count integer not null default 1, result_summary text not null "
            "default '', snapshot_id integer references scheduled_task_config_versions(id),\n"
            "                    foreign key(scheduled_task_id) references "
            "scheduled_tasks(id)\n"
            "                )",
        ),
    ),
    "workbench_tasks": (
        (
            "index",
            "idx_workbench_tasks_updated_id",
            "CREATE INDEX idx_workbench_tasks_updated_id on workbench_tasks(updated_at, id)",
        ),
        (
            "table",
            "workbench_tasks",
            "CREATE TABLE workbench_tasks (\n"
            "                    id text primary key,\n"
            "                    title text not null,\n"
            "                    runtime_kind text not null,\n"
            "                    provider_session_ref text not null default '',\n"
            "                    archived_at text not null default '',\n"
            "                    created_at text not null default current_timestamp,\n"
            "                    updated_at text not null default current_timestamp\n"
            "                )",
        ),
    ),
    "workbench_turns": (
        (
            "index",
            "idx_workbench_one_active_turn",
            "CREATE UNIQUE INDEX idx_workbench_one_active_turn\n"
            "                    on workbench_turns(task_id)\n"
            "                    where status in ('queued', 'running', 'waiting_confirmation')",
        ),
        (
            "index",
            "idx_workbench_turns_queue",
            "CREATE INDEX idx_workbench_turns_queue\n"
            "                    on workbench_turns(status, created_at, id)",
        ),
        (
            "index",
            "idx_workbench_turns_recovery",
            "CREATE INDEX idx_workbench_turns_recovery\n"
            "                    on workbench_turns(status, lease_expires_at)",
        ),
        (
            "index",
            "idx_workbench_turns_task_created_id",
            "CREATE INDEX idx_workbench_turns_task_created_id on workbench_turns(task_id, "
            "created_at, id)",
        ),
        (
            "index",
            "idx_workbench_turns_task_sequence",
            "CREATE UNIQUE INDEX idx_workbench_turns_task_sequence on workbench_turns(task_id, "
            "task_sequence desc)",
        ),
        (
            "table",
            "workbench_turns",
            "CREATE TABLE workbench_turns (\n"
            "                    id text primary key,\n"
            "                    task_id text not null,\n"
            "                    client_request_id text not null unique,\n"
            "                    task_sequence integer not null check(task_sequence > 0),\n"
            "                    user_text text not null,\n"
            "                    status text not null check(status in (\n"
            "                        'queued', 'running', 'waiting_confirmation',\n"
            "                        'completed', 'stopped', 'failed'\n"
            "                    )),\n"
            "                    stop_requested integer not null default 0\n"
            "                        check(stop_requested in (0, 1)),\n"
            "                    final_text text not null default '',\n"
            "                    error_code text not null default '',\n"
            "                    error_detail text not null default '',\n"
            "                    resume_context text not null default '',\n"
            "                    lease_owner text not null default '',\n"
            "                    lease_expires_at text not null default '',\n"
            "                    execution_run_id text not null default '',\n"
            "                    runtime_quiesced_run_id text not null default '',\n"
            "                    started_at text not null default '',\n"
            "                    completed_at text not null default '',\n"
            "                    created_at text not null default current_timestamp,\n"
            "                    updated_at text not null default current_timestamp,\n"
            "                    foreign key(task_id) references workbench_tasks(id)\n"
            "                )",
        ),
    ),
    "workbench_events": (
        (
            "index",
            "idx_workbench_events_event_type",
            "CREATE INDEX idx_workbench_events_event_type on workbench_events(event_type)",
        ),
        (
            "index",
            "idx_workbench_events_id_turn_id",
            "CREATE INDEX idx_workbench_events_id_turn_id on workbench_events(id, turn_id)",
        ),
        (
            "index",
            "idx_workbench_events_turn_id_id",
            "CREATE INDEX idx_workbench_events_turn_id_id on workbench_events(turn_id, id)",
        ),
        (
            "table",
            "workbench_events",
            "CREATE TABLE workbench_events (\n"
            "                    id integer primary key autoincrement,\n"
            "                    turn_id text not null,\n"
            "                    sequence integer not null,\n"
            "                    event_type text not null,\n"
            "                    payload_json text not null,\n"
            "                    created_at text not null default current_timestamp,\n"
            "                    unique(turn_id, sequence),\n"
            "                    foreign key(turn_id) references workbench_turns(id)\n"
            "                )",
        ),
    ),
    "workbench_attachments": (
        (
            "index",
            "idx_workbench_attachments_task_created_id",
            "CREATE INDEX idx_workbench_attachments_task_created_id on "
            "workbench_attachments(task_id, created_at, id)",
        ),
        (
            "index",
            "idx_workbench_attachments_task_request",
            "CREATE UNIQUE INDEX idx_workbench_attachments_task_request on "
            "workbench_attachments(task_id, client_request_id)",
        ),
        (
            "table",
            "workbench_attachments",
            "CREATE TABLE workbench_attachments (\n"
            "                    id text primary key,\n"
            "                    task_id text not null,\n"
            "                    client_request_id text not null,\n"
            "                    filename text not null,\n"
            "                    media_type text not null,\n"
            "                    size_bytes integer not null check(size_bytes >= 0),\n"
            "                    content_sha256 text not null,\n"
            "                    storage_path text not null,\n"
            "                    created_at text not null default current_timestamp,\n"
            "                    foreign key(task_id) references workbench_tasks(id)\n"
            "                )",
        ),
    ),
    "workbench_artifacts": (
        (
            "index",
            "idx_workbench_artifacts_created_id_turn",
            "CREATE INDEX idx_workbench_artifacts_created_id_turn on "
            "workbench_artifacts(created_at, id, turn_id)",
        ),
        (
            "index",
            "idx_workbench_artifacts_turn_created_id",
            "CREATE INDEX idx_workbench_artifacts_turn_created_id on "
            "workbench_artifacts(turn_id, created_at, id)",
        ),
        (
            "table",
            "workbench_artifacts",
            "CREATE TABLE workbench_artifacts (\n"
            "                    id text primary key,\n"
            "                    turn_id text not null,\n"
            "                    label text not null,\n"
            "                    path text not null,\n"
            "                    media_type text not null,\n"
            "                    created_at text not null default current_timestamp,\n"
            "                    foreign key(turn_id) references workbench_turns(id)\n"
            "                )",
        ),
    ),
    "workbench_confirmations": (
        (
            "index",
            "idx_workbench_confirmations_created_id_turn",
            "CREATE INDEX idx_workbench_confirmations_created_id_turn on "
            "workbench_confirmations(created_at, id, turn_id)",
        ),
        (
            "index",
            "idx_workbench_confirmations_legacy_execution_lease_recovery",
            "CREATE INDEX idx_workbench_confirmations_legacy_execution_lease_recovery\n"
            "                on workbench_confirmations(id)\n"
            "                where status='confirmed' and result_json=''\n"
            "                  and execution_owner<>'' and execution_lease_expires_at=''\n"
            "                ",
        ),
        (
            "index",
            "idx_workbench_confirmations_legacy_execution_owner_recovery",
            "CREATE INDEX idx_workbench_confirmations_legacy_execution_owner_recovery\n"
            "                on workbench_confirmations(id)\n"
            "                where status='confirmed' and result_json=''\n"
            "                  and execution_owner=''\n"
            "                ",
        ),
        (
            "index",
            "idx_workbench_confirmations_legacy_proposer_recovery",
            "CREATE INDEX idx_workbench_confirmations_legacy_proposer_recovery\n"
            "                on workbench_confirmations(id)\n"
            "                where status='pending' and proposer_run_id=''\n"
            "                ",
        ),
        (
            "index",
            "idx_workbench_confirmations_proposer_recovery",
            "CREATE INDEX idx_workbench_confirmations_proposer_recovery\n"
            "                on workbench_confirmations(proposer_lease_expires_at, id)\n"
            "                where status='pending' and proposer_run_id<>''\n"
            "                  and proposer_quiesced_at=''\n"
            "                ",
        ),
        (
            "index",
            "idx_workbench_confirmations_ready_intents",
            "CREATE INDEX idx_workbench_confirmations_ready_intents\n"
            "                on workbench_confirmations(decision_requested_at, id)\n"
            "                where status='pending' and decision_requested<>''\n"
            "                  and proposer_run_id<>'' and proposer_quiesced_at<>''\n"
            "                ",
        ),
        (
            "index",
            "idx_workbench_confirmations_recovery",
            "CREATE INDEX idx_workbench_confirmations_recovery\n"
            "                on workbench_confirmations(execution_lease_expires_at, id)\n"
            "                where status='confirmed' and result_json=''\n"
            "                  and execution_owner<>'' and execution_lease_expires_at<>''\n"
            "                ",
        ),
        (
            "index",
            "idx_workbench_confirmations_turn_created_id",
            "CREATE INDEX idx_workbench_confirmations_turn_created_id on "
            "workbench_confirmations(turn_id, created_at, id)",
        ),
        (
            "index",
            "idx_workbench_confirmations_turn_status",
            "CREATE INDEX idx_workbench_confirmations_turn_status on "
            "workbench_confirmations(turn_id, status, result_json, created_at, id)",
        ),
        (
            "table",
            "workbench_confirmations",
            "CREATE TABLE workbench_confirmations (\n"
            "                    id text primary key,\n"
            "                    turn_id text not null,\n"
            "                    action_kind text not null,\n"
            "                    target text not null,\n"
            "                    summary text not null,\n"
            "                    risk text not null,\n"
            "                    canonical_capability text not null default '',\n"
            "                    canonical_operation text not null default '',\n"
            "                    canonical_targets_json text not null default '[]',\n"
            "                    canonical_operation_digest text not null default '',\n"
            "                    canonical_arguments_digest text not null default '',\n"
            "                    arguments_json text not null,\n"
            "                    status text not null check(status in (\n"
            "                        'pending', 'confirmed', 'cancelled', 'executed', "
            "'failed'\n"
            "                    )),\n"
            "                    result_json text not null default '',\n"
            "                    created_at text not null default current_timestamp,\n"
            "                    decided_at text not null default '',\n"
            "                    execution_owner text not null default '',\n"
            "                    execution_lease_expires_at text not null default '',\n"
            "                    execution_started_at text not null default '',\n"
            "                    authorization_consumed_at text not null default '',\n"
            "                    proposer_run_id text not null default '',\n"
            "                    proposer_owner text not null default '',\n"
            "                    proposer_lease_expires_at text not null default '',\n"
            "                    proposer_quiesced_at text not null default '',\n"
            "                    decision_requested text not null default ''\n"
            "                        check(decision_requested in ('', 'confirm', "
            "'cancel')),\n"
            "                    decision_requested_at text not null default '',\n"
            "                    foreign key(turn_id) references workbench_turns(id)\n"
            "                )",
        ),
    ),
    "codex_session_search_index": (
        (
            "index",
            "idx_codex_session_search_source",
            "CREATE INDEX idx_codex_session_search_source\n"
            "                    on codex_session_search_index(source_type, source_id)",
        ),
        (
            "table",
            "codex_session_search_index",
            "CREATE TABLE codex_session_search_index (\n"
            "                    id integer primary key autoincrement,\n"
            "                    session_id text not null unique,\n"
            "                    source_type text not null default '',\n"
            "                    source_id text not null default '',\n"
            "                    title text not null default '',\n"
            "                    summary_text text not null default '',\n"
            "                    fts_text text not null default '',\n"
            "                    embedding_json text not null default '',\n"
            "                    embedding_model text not null default '',\n"
            "                    embedding_updated_at text not null default '',\n"
            "                    created_at text not null default current_timestamp,\n"
            "                    updated_at text not null default current_timestamp\n"
            "                )",
        ),
    ),
    "codex_session_search_fts": (
        (
            "table",
            "codex_session_search_fts",
            "CREATE VIRTUAL TABLE codex_session_search_fts\n"
            "                    using fts5(\n"
            "                        title,\n"
            "                        summary_text,\n"
            "                        fts_text,\n"
            "                        content='codex_session_search_index',\n"
            "                        content_rowid='id'\n"
            "                    )",
        ),
    ),
}

SEARCH_TABLE_DDL = """create table codex_session_search_index (
    id integer primary key autoincrement,
    session_id text not null unique,
    source_type text not null default '',
    source_id text not null default '',
    title text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
)"""
SEARCH_INDEX_DDL = "create index idx_codex_session_search_source on codex_session_search_index(source_type,source_id)"
SEARCH_FTS_DDL = """create virtual table codex_session_search_fts using fts5(
    title, content='codex_session_search_index', content_rowid='id')"""


def _child_ddl(sql):
    return (
        sql.replace("task_id text not null", "task_id integer not null")
        .replace("turn_id text not null", "turn_id integer not null")
        .replace("references workbench_tasks(id)", "references tasks(id)")
        .replace("references workbench_turns(id)", "references tasks(id)")
    )


def create_workbench_auxiliary_schema(db):
    """Retained children and metadata cache, using their original constraints."""
    for table in WORKBENCH_CHILDREN:
        for kind, _, sql in SOURCE_DECLARATIONS[table]:
            if kind == "table":
                db.execute(
                    _child_ddl(sql).replace(
                        "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1
                    )
                )
        for kind, _, sql in SOURCE_DECLARATIONS[table]:
            if kind == "index":
                sql = sql.replace(
                    "CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1
                )
                sql = sql.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
                db.execute(sql)
    db.execute(
        SEARCH_TABLE_DDL.replace("create table ", "create table if not exists ", 1)
    )
    db.execute(
        SEARCH_INDEX_DDL.replace("create index ", "create index if not exists ", 1)
    )
    db.execute(
        SEARCH_FTS_DDL.replace(
            "create virtual table ", "create virtual table if not exists ", 1
        )
    )
    install_task_child_guards(db)


def _validate_source(db):
    marker = db.execute(
        "select value from service_state where key='store_schema_version'"
    ).fetchone()
    if marker is None or marker[0] != SOURCE_VERSION:
        raise ValueError("unsupported Workbench source version: expected 2026-10-11.3")
    if not task_schema_is_current(db) or not run_schema_is_current(
        db, include_task_runs=True
    ):
        raise ValueError("unsupported source13 typed task/run declarations")
    for table, expected in (
        ("tasks", _declared_task_objects()),
        ("runs", _declared_run_objects(include_task_runs=True)),
    ):
        actual = {o["name"]: o["sql"].strip() for o in _schema_objects(db, table)}
        if actual != {name: sql.strip() for name, sql in expected.items()}:
            raise ValueError(f"unsupported source13 object inventory for {table}")
    for table, expected in SOURCE_DECLARATIONS.items():
        actual = tuple(
            (o["type"], o["name"], o["sql"].strip()) for o in _schema_objects(db, table)
        )
        if actual != tuple((kind, name, sql.strip()) for kind, name, sql in expected):
            raise ValueError(f"unsupported source13 declaration for {table}")
    for table, columns in (
        ("workbench_turns", ("final_text",)),
        ("codex_session_search_index", SEARCH_RETIRED_COLUMNS),
    ):
        for column in columns:
            if db.execute(
                f"select 1 from {table} where {column} is null or {column}<>'' limit 1"
            ).fetchone():
                raise ValueError(f"{table}.{column} is not an empty retired cache")
    validate_reply_input_references(db)
    validate_meeting_run_references(db)


def capture_workbench_schema(db):
    _validate_source(db)
    sequences = dict(db.execute("select name,seq from sqlite_sequence"))
    tables = ("tasks", "runs", *SOURCE_DECLARATIONS)
    states = {table: _capture_table(db, table, sequences) for table in tables}
    task_high = max(
        sequences.get("tasks", 0),
        db.execute("select coalesce(max(id),0) from tasks").fetchone()[0],
    )
    run_high = max(
        max(
            sequences.get(table, 0),
            db.execute(f"select coalesce(max(id),0) from {table}").fetchone()[0],
        )
        for table in ("runs", "scheduled_task_runs", "workbench_events")
    )
    search_rows = [
        tuple(row)
        for row in db.execute(
            "select " + ",".join(SEARCH_COLUMNS) + " from codex_session_search_index"
        )
    ]
    fts_shadows = {
        name: dict(
            columns=[r[1] for r in db.execute(f'pragma table_info("{name}")')],
            rows=[tuple(row) for row in db.execute(f'select * from "{name}"')],
        )
        for (name,) in db.execute(
            "select name from pragma_table_list where schema='main' and type='shadow' and name like 'codex_session_search_fts_%' order by name"
        ).fetchall()
    }
    text_rowids = {}
    next_task = task_high
    for table in TEXT_TASK_TYPES:
        text_rowids[table] = {}
        for (rowid,) in db.execute(f"select rowid from {table} order by rowid"):
            next_task += 1
            text_rowids[table][next_task] = rowid
    return dict(
        tables=states,
        text_rowids=text_rowids,
        search_rows=search_rows,
        fts_shadows=fts_shadows,
        allocator=dict(
            source_task_high=task_high,
            source_run_high=run_high,
            migration_task_high=task_high
            + sum(
                db.execute(f"select count(*) from {table}").fetchone()[0]
                for table in TEXT_TASK_TYPES
            ),
            migration_run_high=run_high
            + sum(
                db.execute(f"select count(*) from {table}").fetchone()[0]
                for table in ("scheduled_task_runs", "workbench_events")
            ),
        ),
    )


def _copy_rows(db, table, columns, values):
    db.executemany(
        f'insert into "{table}" ({",".join(columns)}) values ({",".join("?" for _ in columns)})',
        values,
    )


def migrate_workbench_schema(db):
    state = capture_workbench_schema(db)
    for table in RETIRED_WORKBENCH_TABLES:
        _drop_explicit_objects(db, state["tables"][table]["objects"])
    existing_tasks = db.execute("select * from tasks").fetchall()
    existing_runs = db.execute("select * from runs").fetchall()
    db.execute("drop table tasks")
    create_task_schema(db, include_workbench=True)
    _copy_rows(db, "tasks", state["tables"]["tasks"]["columns"], existing_tasks)
    mapping = {}
    next_task = state["allocator"]["source_task_high"]
    counts = {}
    for table in TEXT_TASK_TYPES:
        columns = state["tables"][table]["columns"]
        counts[table] = 0
        for row in db.execute(f"select * from {table} order by rowid").fetchall():
            source = dict(zip(columns, row, strict=True))
            public = source.pop("id")
            next_task += 1
            if table == "workbench_turns":
                source["parent_id"] = mapping["workbench_tasks", source.pop("task_id")]
                source.pop("final_text")
            insert_task(db, table, dict(id=next_task, legacy_key=public, **source))
            if public is not None:
                mapping[table, public] = next_task
            counts[table] += 1
    db.execute("drop table runs")
    create_run_schema(db, include_task_runs=True, include_workbench=True)
    _copy_rows(db, "runs", state["tables"]["runs"]["columns"], existing_runs)
    next_run = state["allocator"]["source_run_high"]
    for table in ("scheduled_task_runs", "workbench_events"):
        columns = state["tables"][table]["columns"]
        counts[table] = 0
        for row in db.execute(f"select * from {table} order by id").fetchall():
            source = dict(zip(columns, row, strict=True))
            public = source.pop("id")
            next_run += 1
            if table == "workbench_events":
                source["task_id"] = mapping["workbench_turns", source.pop("turn_id")]
            insert_run(db, table, dict(id=next_run, legacy_key=public, **source))
            counts[table] += 1
    for table in WORKBENCH_CHILDREN:
        schema = state["tables"][table]
        fk = schema["foreign_keys"][0]
        values = []
        columns = ["rowid", *schema["columns"]]
        for row in db.execute(f"select rowid,* from {table} order by rowid").fetchall():
            source = dict(zip(columns, row, strict=True))
            source[fk[3]] = mapping[fk[2], source[fk[3]]]
            values.append(tuple(source[c] for c in columns))
        target_schema = {
            **schema,
            "columns": columns,
            "objects": [
                dict(obj, sql=_child_ddl(obj["sql"])) if obj["type"] == "table" else obj
                for obj in schema["objects"]
            ],
        }
        _rebuild(db, table, target_schema, values)
    for table in RETIRED_WORKBENCH_TABLES:
        db.execute(f"drop table {table}")
    search_rows = db.execute(
        "select " + ",".join(SEARCH_COLUMNS) + " from codex_session_search_index"
    ).fetchall()
    search_sequence = state["tables"]["codex_session_search_index"]["sequence"]
    db.execute("drop table codex_session_search_fts")
    db.execute("drop table codex_session_search_index")
    create_workbench_auxiliary_schema(db)
    _copy_rows(db, "codex_session_search_index", SEARCH_COLUMNS, search_rows)
    if search_sequence is not None:
        _set_sequence(db, "codex_session_search_index", search_sequence)
    db.execute(
        "insert into codex_session_search_fts(codex_session_search_fts) values('rebuild')"
    )
    _set_sequence(db, "tasks", next_task)
    _set_sequence(db, "runs", next_run)
    validate_reply_input_references(db)
    validate_meeting_run_references(db)
    return counts


def restore_workbench_schema(db, state):
    if not workbench_schema_is_current(db):
        raise ValueError("Workbench inverse requires the exact current declarations")
    sequences = dict(db.execute("select name,seq from sqlite_sequence"))
    text_physical_ids = {
        table: [
            row[0]
            for row in db.execute(
                "select id from tasks where record_type=? order by id", (table,)
            )
        ]
        for table in TEXT_TASK_TYPES
    }
    saved = {
        table: db.execute(
            sql
            + (
                " order by t.id"
                if table in TEXT_TASK_TYPES
                else " order by r.id"
                if table == "workbench_events"
                else " order by runs.id"
            )
        ).fetchall()
        for table, sql in WORKBENCH_PROJECTIONS.items()
    }
    for table in ("tasks", "runs"):
        columns = state["tables"][table]["columns"]
        saved[table] = db.execute(
            "select "
            + ",".join(columns)
            + f" from {table} where record_type not in (?,?,?,?)",
            RETIRED_WORKBENCH_TABLES,
        ).fetchall()
    mapping = dict(
        db.execute(
            "select id,legacy_key from tasks where record_type in ('workbench_tasks','workbench_turns')"
        )
    )
    child_rowids = {}
    for table in WORKBENCH_CHILDREN:
        schema = state["tables"][table]
        fk = schema["foreign_keys"][0]
        saved[table] = []
        child_rowids[table] = []
        for row in db.execute(f"select rowid,* from {table} order by rowid").fetchall():
            child_rowids[table].append(row[0])
            source = dict(zip(schema["columns"], row[1:], strict=True))
            source[fk[3]] = mapping[source[fk[3]]]
            saved[table].append(tuple(source[c] for c in schema["columns"]))
    current_search_rows = [
        tuple(row)
        for row in db.execute(
            "select " + ",".join(SEARCH_COLUMNS) + " from codex_session_search_index"
        )
    ]
    search_columns = state["tables"]["codex_session_search_index"]["columns"]
    saved["codex_session_search_index"] = db.execute(
        "select "
        + ",".join("''" if c in SEARCH_RETIRED_COLUMNS else c for c in search_columns)
        + " from codex_session_search_index"
    ).fetchall()
    for table in ("tasks", "runs"):
        _drop_explicit_objects(db, _schema_objects(db, table))
    db.execute("drop table codex_session_search_fts")
    # Drop typed child guards before returning the parent's original TEXT keys.
    for table in (*WORKBENCH_CHILDREN, "codex_session_search_index", "tasks", "runs"):
        db.execute(f"drop table {table}")
    counts = {}
    for table in (
        *RETIRED_WORKBENCH_TABLES,
        *WORKBENCH_CHILDREN,
        "tasks",
        "runs",
        "codex_session_search_index",
    ):
        schema = state["tables"][table]
        db.execute(next(o["sql"] for o in schema["objects"] if o["type"] == "table"))
        projection_columns = WORKBENCH_COLUMNS.get(table, schema["columns"])
        values = [
            tuple(
                dict(zip(projection_columns, row, strict=True))[c]
                for c in schema["columns"]
            )
            for row in saved[table]
        ]
        if table in TEXT_TASK_TYPES:
            source_rowids = state["text_rowids"][table]
            restored, created = [], []
            for physical, row in zip(text_physical_ids[table], values, strict=True):
                if physical in source_rowids:
                    restored.append((source_rowids[physical], *row))
                else:
                    created.append(row)
            _copy_rows(db, table, ["rowid", *schema["columns"]], restored)
            # These source tables never used AUTOINCREMENT. Let SQLite assign
            # legal rowids to new facts, including when the source used INT64_MAX.
            _copy_rows(db, table, schema["columns"], created)
        elif table in WORKBENCH_CHILDREN:
            _copy_rows(
                db,
                table,
                ["rowid", *schema["columns"]],
                [
                    (identity, *row)
                    for identity, row in zip(child_rowids[table], values, strict=True)
                ],
            )
        else:
            _copy_rows(db, table, schema["columns"], values)
        for obj in schema["objects"]:
            if obj["type"] != "table":
                db.execute(obj["sql"])
        if table in (
            "tasks",
            "runs",
            "scheduled_task_runs",
            "workbench_events",
            "codex_session_search_index",
        ):
            maximum = db.execute(f"select coalesce(max(id),0) from {table}").fetchone()[
                0
            ]
            sequence = schema["sequence"]
            if table == "codex_session_search_index":
                reserve = sequences.get(table, 0)
            else:
                allocator = "tasks" if table == "tasks" else "runs"
                threshold = (
                    "migration_task_high" if table == "tasks" else "migration_run_high"
                )
                reserve = (
                    sequences.get(allocator, 0)
                    if sequences.get(allocator, 0) > state["allocator"][threshold]
                    else 0
                )
            if sequence is not None or maximum or reserve:
                _set_sequence(db, table, max(sequence or 0, maximum, reserve))
        counts[table] = len(values)
    db.execute(
        next(
            o["sql"]
            for o in state["tables"]["codex_session_search_fts"]["objects"]
            if o["type"] == "table"
        )
    )
    _restore_search_index(db, state, current_search_rows)
    return counts


def _restore_search_index(db, state, current_rows):
    # Index state is not implied by external-content rows: source upserts could
    # leave an existing metadata row unindexed. Keep the exact original bytes.
    for table, shadow in state["fts_shadows"].items():
        db.execute(f'delete from "{table}"')
        _copy_rows(db, table, shadow["columns"], shadow["rows"])
    original = {row[0]: row for row in state["search_rows"]}
    current = {row[0]: row for row in current_rows}
    changed = {
        key
        for key in original.keys() | current.keys()
        if original.get(key) != current.get(key)
    }
    if not changed:
        return
    indexed = {
        row[0]
        for row in state["fts_shadows"]["codex_session_search_fts_docsize"]["rows"]
    }
    # Delete the actual original tokens, including stale indexed terms whose
    # external cache columns had already been cleared. Vocabulary is local to
    # this private inverse connection and is not a second content store.
    db.execute(
        "create virtual table temp.workbench_inverse_fts_vocab using fts5vocab(main,codex_session_search_fts,'instance')"
    )
    for identity in sorted(changed):
        if identity in indexed:
            terms = db.execute(
                "select term,col from temp.workbench_inverse_fts_vocab where doc=? order by col,offset",
                (identity,),
            ).fetchall()
            values = [
                " ".join(term for term, col in terms if col == name)
                for name in ("title", "summary_text", "fts_text")
            ]
            db.execute(
                "insert into codex_session_search_fts(codex_session_search_fts,rowid,title,summary_text,fts_text) values('delete',?,?,?,?)",
                (identity, *values),
            )
        if identity in current:
            db.execute(
                "insert into codex_session_search_fts(rowid,title) values(?,?)",
                (identity, current[identity][4]),
            )
    db.execute("drop table temp.workbench_inverse_fts_vocab")


@lru_cache(maxsize=1)
def _current_auxiliary_objects():
    with closing(sqlite3.connect(":memory:")) as db:
        create_task_schema(db, include_workbench=True)
        create_workbench_auxiliary_schema(db)
        return {
            table: _schema_objects(db, table)
            for table in (
                *WORKBENCH_CHILDREN,
                "codex_session_search_index",
                "codex_session_search_fts",
            )
        }


def workbench_schema_is_current(db, *, allow_missing_objects=False):
    if not task_schema_is_current(
        db, include_workbench=True, allow_missing_objects=allow_missing_objects
    ) or not run_schema_is_current(
        db,
        include_task_runs=True,
        include_workbench=True,
        allow_missing_objects=allow_missing_objects,
    ):
        return False
    for table, expected in _current_auxiliary_objects().items():
        actual = {o["name"]: o for o in _schema_objects(db, table)}
        for obj in expected:
            if (
                obj["name"] not in actual
                and allow_missing_objects
                and obj["type"] != "table"
            ):
                continue
            if actual.get(obj["name"]) != obj:
                return False
    return True
