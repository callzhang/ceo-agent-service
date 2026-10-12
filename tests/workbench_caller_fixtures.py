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
        db.close()
