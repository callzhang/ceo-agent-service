"""Workbench caller fixtures use the independently captured complete source13."""

from contextlib import contextmanager

from tests.workbench_schema_fixtures import full_source_database


@contextmanager
def empty_source13(path):
    db = full_source_database(path, path.with_suffix(".unrelated-schema.sqlite3"))
    # Remove the migration torture rows before adding this caller's business fixture.
    for table in (
        "workbench_confirmations", "workbench_artifacts", "workbench_attachments",
        "workbench_events", "workbench_turns", "workbench_tasks",
        "scheduled_task_runs", "scheduled_task_config_versions", "scheduled_tasks",
        "codex_session_search_index", "runs", "tasks",
    ):
        db.execute(f"delete from {table}")
    db.commit()
    try:
        with db:
            yield db
    finally:
        # Publish each separately verified historical transition before the
        # current Store's exact source15 -> source16 migration.
        from app.action_storage import (
            ACTION_PROJECTIONS, ACTION_TYPES, RECEIPT_ACTION_TYPES, SOURCE_COLUMNS,
            migrate_action_schema,
        )
        from app.workbench_storage import migrate_workbench_schema
        db.execute('pragma foreign_keys=off')
        db.execute('begin immediate')
        migrate_workbench_schema(db)
        db.execute("update service_state set value='2026-10-12.1' where key='store_schema_version'")
        originals = {
            kind: [tuple(row) for row in db.execute(
                f"select {','.join(SOURCE_COLUMNS[kind])} from {kind} order by id"
            )]
            for kind in ACTION_TYPES
        }
        receipts = {
            kind: [tuple(row) for row in db.execute(
                f"select {','.join(SOURCE_COLUMNS[kind])} from {kind} order by {SOURCE_COLUMNS[kind][0]}"
            )]
            for kind in RECEIPT_ACTION_TYPES
        }
        sequences = [tuple(row) for row in db.execute('select name,seq from sqlite_sequence order by name')]
        migrate_action_schema(db)
        assert {
            kind: [tuple(row) for row in db.execute(f"select * from ({ACTION_PROJECTIONS[kind]}) order by id")]
            for kind in ACTION_TYPES
        } == originals
        assert {
            kind: [tuple(row) for row in db.execute(
                f"select {','.join(SOURCE_COLUMNS[kind])} from {kind} order by {SOURCE_COLUMNS[kind][0]}"
            )]
            for kind in RECEIPT_ACTION_TYPES
        } == receipts
        assert [tuple(row) for row in db.execute('select name,seq from sqlite_sequence order by name')] == sequences
        db.execute("update service_state set value='2026-10-13.1' where key='store_schema_version'")
        db.commit()
        db.close()
