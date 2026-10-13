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
        # Apply the separately verified source13 -> source14 transition before
        # the current Store's explicit source14 -> actions publication.
        from app.workbench_storage import migrate_workbench_schema
        db.execute('pragma foreign_keys=off')
        db.execute('begin immediate')
        migrate_workbench_schema(db)
        db.execute("update service_state set value='2026-10-12.1' where key='store_schema_version'")
        db.commit()
        db.close()
