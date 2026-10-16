"""Current Tasks20 factories and declaration recognition.

Historical task_storage/workbench_storage factories retain native source roles.
Current constructors and the paired release select these declaration helpers.
"""
from contextlib import closing
from functools import lru_cache
import sqlite3

from app.task20_definitions import (
    TASK20_TABLE_DDL, TASK20_INDEX_DDL, TASK20_TRIGGER_DDL, TASK20_NEW_KINDS,
)
from app.task_storage import RETIRED_TASK_TABLES
from app.workbench_storage import (
    SOURCE_DECLARATIONS, RETIRED_WORKBENCH_TABLES, _child_ddl,
    SEARCH_TABLE_DDL, SEARCH_INDEX_DDL, SEARCH_FTS_DDL,
)
from app.run_storage import _schema_objects
from app.configs_run_schema import _declarations as _run_declarations, _objects as _run_objects

RETIRED_TASK_NAMES = tuple(dict.fromkeys((*RETIRED_TASK_TABLES, *RETIRED_WORKBENCH_TABLES, *TASK20_NEW_KINDS)))
RETAINED_WORKBENCH_CHILDREN = ("workbench_confirmations",)

FEEDBACK_CHILD_TABLE_DDL = 'CREATE TABLE feedback_iteration_decision_items (\n                    decision_id integer not null,\n                    feedback_key text not null,\n                    round_id integer not null,\n                    primary key(decision_id, feedback_key),\n                    foreign key(decision_id) references tasks("feedback_iteration_decisions__id"),\n                    foreign key(feedback_key) references tasks("feedback_processing_items__feedback_key"),\n                    foreign key(round_id) references tasks("feedback_processing_rounds__id")\n                )'
FEEDBACK_CHILD_INDEX_DDL = ('CREATE INDEX idx_feedback_iteration_decision_items_feedback_round\n                    on feedback_iteration_decision_items(feedback_key, round_id, decision_id)',)
FEEDBACK_CHILD_TRIGGER_DDL = ("CREATE TRIGGER trg_feedback_iteration_decision_items_immutable_delete\nbefore delete on feedback_iteration_decision_items\nbegin\n    select raise(abort, 'feedback iteration decision items are append-only');\nend", "CREATE TRIGGER trg_feedback_iteration_decision_items_immutable_update\nbefore update on feedback_iteration_decision_items\nbegin\n    select raise(abort, 'feedback iteration decision items are append-only');\nend")


def create_current_task20_schema(db):
    """Create/repair the fixed current container, retaining caller commit ownership."""
    db.execute(TASK20_TABLE_DDL.replace("create table tasks", "create table if not exists tasks", 1))
    for sql in TASK20_INDEX_DDL:
        db.execute(sql.replace("create unique index ", "create unique index if not exists ", 1)
                   .replace("create index ", "create index if not exists ", 1))
    for sql in TASK20_TRIGGER_DDL:
        db.execute(sql.replace("create trigger ", "create trigger if not exists ", 1))
    db.execute("insert into main.sqlite_sequence(name,seq) select 'tasks:published',0 "
               "where not exists(select 1 from main.sqlite_sequence where name='tasks:published')")


def create_current_feedback_child_schema(db):
    """Keep the original child fields/index/append-only SQL, with three mapped FKs."""
    db.execute(FEEDBACK_CHILD_TABLE_DDL.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
    for sql in FEEDBACK_CHILD_INDEX_DDL:
        db.execute(sql.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1))
    for sql in FEEDBACK_CHILD_TRIGGER_DDL:
        db.execute(sql.replace("CREATE TRIGGER ", "CREATE TRIGGER IF NOT EXISTS ", 1))


def install_current_task_child_guards(db):
    """Preserve physical-id guards; generated public FK endpoints are separate."""
    for (table,) in db.execute(
        "select name from main.sqlite_schema where type='table' and name not in ('tasks','runs','actions')"
    ).fetchall():
        for fk in db.execute('pragma main.foreign_key_list("'+table+'")'):
            if fk[2] != "tasks" or fk[4] != "id":
                continue
            column = fk[3]
            parent_type = (
                "meeting_alignment_jobs" if (table, column) in {
                    ("meeting_memory_write_events", "meeting_job_id"),
                    ("meeting_alignment_delivery_claims", "job_id"),
                } else "workbench_turns" if table == "workbench_confirmations"
                else "reply_tasks"
            )
            parent_label = {"meeting_alignment_jobs":"meeting job", "reply_tasks":"reply task",
                            "workbench_turns":"workbench turn"}[parent_type]
            for event, suffix in (("insert", "insert"), ('update of "'+column+'"', "update")):
                db.execute(
                    f'create trigger if not exists trg_tasks_child_{table}_{column}_{suffix} before {event} on "{table}" when new."{column}" is not null and exists(select 1 from tasks where id=new."{column}" and record_type<>\'{parent_type}\') begin select raise(abort,\'task parent must be a {parent_label}\'); end'
                )


def create_current_workbench_auxiliary_schema(db):
    """Retain confirmation/search auxiliaries without the two folded child tables."""
    for table in RETAINED_WORKBENCH_CHILDREN:
        for kind, _, sql in SOURCE_DECLARATIONS[table]:
            if kind == "table":
                db.execute(_child_ddl(sql).replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
        for kind, _, sql in SOURCE_DECLARATIONS[table]:
            if kind == "index":
                db.execute(sql.replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1)
                           .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1))
    db.execute(SEARCH_TABLE_DDL.replace("create table ", "create table if not exists ", 1))
    db.execute(SEARCH_INDEX_DDL.replace("create index ", "create index if not exists ", 1))
    db.execute(SEARCH_FTS_DDL.replace("create virtual table ", "create virtual table if not exists ", 1))
    install_current_task_child_guards(db)


@lru_cache(maxsize=1)
def _current_objects():
    with closing(sqlite3.connect(":memory:")) as reference:
        create_current_task20_schema(reference)
        create_current_feedback_child_schema(reference)
        create_current_workbench_auxiliary_schema(reference)
        return {table: _schema_objects(reference,table) for table in (
            "tasks","feedback_iteration_decision_items", *RETAINED_WORKBENCH_CHILDREN,
            "codex_session_search_index","codex_session_search_fts",
        )}


def task20_schema_is_current(db, *, allow_missing_objects=False):
    """Mirror the native container's explicit missing-object repair semantics."""
    if db.execute("select 1 from main.sqlite_schema where type in ('table','view') and name in ("
                  + ",".join("?" for _ in RETIRED_TASK_NAMES)+")", RETIRED_TASK_NAMES).fetchone():
        return False
    expected = _current_objects()["tasks"]
    actual = {obj["name"]:obj for obj in _schema_objects(db,"tasks")}
    for obj in expected:
        if obj["name"] not in actual and allow_missing_objects and obj["type"] != "table":
            continue
        if actual.get(obj["name"]) != obj:
            return False
    return True


def task20_feedback_child_is_current(db, *, allow_missing_objects=False):
    """Recognize retained endpoints; missing auxiliaries keep the original repair mode."""
    actual = {obj["name"]:obj for obj in _schema_objects(db,"feedback_iteration_decision_items")}
    if not actual and allow_missing_objects:
        return True
    for obj in _current_objects()["feedback_iteration_decision_items"]:
        if obj["name"] not in actual and allow_missing_objects and obj["type"] != "table":
            continue
        if actual.get(obj["name"]) != obj:
            return False
    return True


def task20_workbench_schema_is_current(db, *, allow_missing_objects=False):
    """Keep mapped runs and retained Workbench auxiliary recognition together."""
    expected_runs = _run_declarations(mapped=True)
    actual_runs = _run_objects(db)
    if (actual_runs.get("runs") != expected_runs["runs"] or not set(actual_runs) <= set(expected_runs)
            or any(actual_runs.get(name,sql) != sql if allow_missing_objects else actual_runs.get(name) != sql
                   for name,sql in expected_runs.items())
            or not task20_schema_is_current(db,allow_missing_objects=allow_missing_objects)):
        return False
    for table, expected in _current_objects().items():
        if table in ("tasks","feedback_iteration_decision_items"):
            continue
        actual = {obj["name"]:obj for obj in _schema_objects(db,table)}
        for obj in expected:
            if obj["name"] not in actual and allow_missing_objects and obj["type"] != "table":
                continue
            if actual.get(obj["name"]) != obj:
                return False
    return True
