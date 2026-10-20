"""Current Task lifecycle factory; historical Task20 factories stay frozen."""
from contextlib import closing
from functools import lru_cache
import sqlite3

from app.task_lifecycle_definitions import TASK20_TABLE_DDL, TASK20_INDEX_DDL, TASK20_TRIGGER_DDL
from app.task20_current_schema import (
    RETIRED_TASK_NAMES, create_current_feedback_child_schema as create_current_feedback_child_schema,
    install_current_task_child_guards as install_current_task_child_guards,
    create_current_workbench_auxiliary_schema as create_current_workbench_auxiliary_schema,
    task20_feedback_child_is_current,
)
from app.run_storage import _schema_objects
from app.runs11_current_schema import current_run11_schema_is_current


def create_current_task20_schema(db):
    db.execute(TASK20_TABLE_DDL.replace("create table tasks", "create table if not exists tasks", 1))
    for sql in (*TASK20_INDEX_DDL, *TASK20_TRIGGER_DDL):
        db.execute(sql.replace("create unique index ", "create unique index if not exists ", 1)
                   .replace("create index ", "create index if not exists ", 1)
                   .replace("create trigger ", "create trigger if not exists ", 1))
    db.execute("insert into main.sqlite_sequence(name,seq) select 'tasks:published',0 "
               "where not exists(select 1 from main.sqlite_sequence where name='tasks:published')")


@lru_cache(maxsize=1)
def task_lifecycle_objects():
    with closing(sqlite3.connect(":memory:")) as db:
        create_current_task20_schema(db)
        return _schema_objects(db, "tasks")


def task20_schema_is_current(db, *, allow_missing_objects=False):
    if db.execute("select 1 from main.sqlite_schema where type in ('table','view') and name in ("
                  + ",".join("?" for _ in RETIRED_TASK_NAMES) + ")", RETIRED_TASK_NAMES).fetchone():
        return False
    actual = {obj["name"]: obj for obj in _schema_objects(db, "tasks")}
    for obj in task_lifecycle_objects():
        if obj["name"] not in actual and allow_missing_objects and obj["type"] != "table":
            continue
        if actual.get(obj["name"]) != obj:
            return False
    return True


def task20_workbench_schema_is_current(db, *, allow_missing_objects=False, include_feedback=False):
    return (current_run11_schema_is_current(db, allow_missing_objects=allow_missing_objects)
            and task20_schema_is_current(db, allow_missing_objects=allow_missing_objects)
            and task20_feedback_child_is_current(db, allow_missing_objects=allow_missing_objects,
                                                 include_feedback=include_feedback))
