"""Workbench auxiliary schema declarations."""


from app.task_storage import (
    install_task_child_guards,
)

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
# Frozen actual source13 declarations, independently captured from 44a1a4e.
SOURCE_DECLARATIONS = {
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
