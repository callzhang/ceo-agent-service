"""Current core declarations must retain case-sensitive SQL literal contracts."""

import sqlite3

import pytest

from app.run_storage import (
    RUN_INDEX_DDL,
    RUN_TABLE_DDL,
    RUN_TRIGGER_DDL,
    create_run_schema,
    run_schema_is_current,
)


@pytest.mark.parametrize("allow_missing", [False, True])
@pytest.mark.parametrize("object_kind", ["table", "index", "trigger"])
def test_current_manifest_rejects_changed_sql_literals(object_kind, allow_missing):
    with sqlite3.connect(":memory:") as db:
        create_run_schema(db)
        assert run_schema_is_current(db, allow_missing_objects=allow_missing)
        if object_kind == "table":
            db.execute("drop table runs")
            db.execute(RUN_TABLE_DDL.replace("'consumer'", "'CONSUMER'"))
            for statement in (*RUN_INDEX_DDL, *RUN_TRIGGER_DDL):
                db.execute(statement)
        elif object_kind == "index":
            db.execute("drop index idx_agent_runs_turn_identity")
            statement = next(
                sql for sql in RUN_INDEX_DDL if "idx_agent_runs_turn_identity" in sql
            )
            db.execute(statement.replace("'agent_runs'", "'AGENT_RUNS'"))
        else:
            db.execute("drop trigger trg_runs_record_type_immutable")
            statement = next(
                sql
                for sql in RUN_TRIGGER_DDL
                if "trg_runs_record_type_immutable" in sql
            )
            db.execute(
                statement.replace(
                    "run record type is immutable", "RUN RECORD TYPE IS IMMUTABLE"
                )
            )
        assert not run_schema_is_current(db, allow_missing_objects=allow_missing)
