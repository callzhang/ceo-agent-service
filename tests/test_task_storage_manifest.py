"""Current typed schema checks must preserve SQL string-literal identity."""

import sqlite3

import pytest

from app import run_storage, task_storage


@pytest.mark.parametrize("allow_missing", [False, True])
@pytest.mark.parametrize("kind", ["table", "index", "trigger"])
def test_task_manifest_rejects_changed_literal(kind, allow_missing):
    db = sqlite3.connect(":memory:")
    if kind == "table":
        db.execute(
            task_storage.TASK_TABLE_DDL.replace("'reply_tasks'", "'REPLY_TASKS'", 1)
        )
        task_storage.create_task_schema(db)
    else:
        task_storage.create_task_schema(db)
        if kind == "index":
            name = "idx_reply_tasks_identity"
        else:
            name = "trg_tasks_parent_id_insert"
        sql = db.execute(
            "select sql from sqlite_master where name=?", (name,)
        ).fetchone()[0]
        db.execute(f"drop {kind} {name}")
        db.execute(sql.replace("'reply_tasks'", "'REPLY_TASKS'", 1))
    assert not task_storage.task_schema_is_current(
        db, allow_missing_objects=allow_missing
    )
    db.close()


@pytest.mark.parametrize("include_task_runs", [False, True])
@pytest.mark.parametrize("allow_missing", [False, True])
@pytest.mark.parametrize("kind", ["table", "index", "trigger"])
def test_run_manifest_rejects_changed_literal(kind, allow_missing, include_task_runs):
    db = sqlite3.connect(":memory:")
    if kind == "table":
        ddl = (
            run_storage.TASK_RUN_TABLE_DDL
            if include_task_runs
            else run_storage.RUN_TABLE_DDL
        )
        db.execute(ddl.replace("'agent_runs'", "'AGENT_RUNS'", 1))
        run_storage.create_run_schema(db, include_task_runs=include_task_runs)
    else:
        run_storage.create_run_schema(db, include_task_runs=include_task_runs)
        name = (
            "idx_agent_runs_turn_identity"
            if kind == "index"
            else "trg_runs_logical_parent_insert"
        )
        sql = db.execute(
            "select sql from sqlite_master where name=?", (name,)
        ).fetchone()[0]
        db.execute(f"drop {kind} {name}")
        db.execute(sql.replace("'agent_runs'", "'AGENT_RUNS'", 1))
    assert not run_storage.run_schema_is_current(
        db,
        allow_missing_objects=allow_missing,
        include_task_runs=include_task_runs,
    )
    db.close()


@pytest.mark.parametrize("include_task_runs", [False, True])
@pytest.mark.parametrize("allow_missing", [False, True])
def test_current_manifests_accept_exact_sql(include_task_runs, allow_missing):
    db = sqlite3.connect(":memory:")
    task_storage.create_task_schema(db)
    run_storage.create_run_schema(db, include_task_runs=include_task_runs)
    assert task_storage.task_schema_is_current(db, allow_missing_objects=allow_missing)
    assert run_storage.run_schema_is_current(
        db,
        allow_missing_objects=allow_missing,
        include_task_runs=include_task_runs,
    )
    db.close()


@pytest.mark.parametrize("include_task_runs", [False, True])
def test_missing_declared_objects_still_repair(include_task_runs):
    db = sqlite3.connect(":memory:")
    task_storage.create_task_schema(db)
    run_storage.create_run_schema(db, include_task_runs=include_task_runs)
    db.execute("drop index idx_reply_tasks_identity")
    db.execute("drop trigger trg_runs_logical_parent_insert")
    assert not task_storage.task_schema_is_current(db)
    assert not run_storage.run_schema_is_current(
        db, include_task_runs=include_task_runs
    )
    assert task_storage.task_schema_is_current(db, allow_missing_objects=True)
    assert run_storage.run_schema_is_current(
        db, allow_missing_objects=True, include_task_runs=include_task_runs
    )
    task_storage.create_task_schema(db)
    run_storage.create_run_schema(db, include_task_runs=include_task_runs)
    assert task_storage.task_schema_is_current(db)
    assert run_storage.run_schema_is_current(db, include_task_runs=include_task_runs)
    db.close()
