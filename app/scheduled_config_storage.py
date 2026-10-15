"""One immutable configuration body shared by scheduled trigger records."""

import json
import sqlite3


def intern_scheduled_config(db: sqlite3.Connection, snapshot_json: str) -> int:
    db.execute(
        "insert or ignore into scheduled_task_config_versions(snapshot_json) values (?)",
        (snapshot_json,),
    )
    return int(db.execute(
        "select id from scheduled_task_config_versions where snapshot_json=?",
        (snapshot_json,),
    ).fetchone()[0])


def migrate_scheduled_configs(db: sqlite3.Connection) -> None:
    db.execute("""create table if not exists scheduled_task_config_versions (
        id integer primary key,
        snapshot_json text not null unique
    )""")
    # Upgrade older configurations once per distinct body. Corrupt historical
    # JSON remains corrupt and will report its original read error.
    for row in db.execute("select id, snapshot_json from scheduled_task_config_versions").fetchall():
        try:
            payload = json.loads(row[1])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        defaults = {"description": str(payload.get("name") or ""),
                    "required_runtime_capabilities": [], "command": ""}
        if all(key in payload for key in defaults):
            continue
        for key, value in defaults.items():
            payload.setdefault(key, value)
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        config_id = intern_scheduled_config(db, body)
        db.execute("update runs set snapshot_id=? where record_type='scheduled_task_runs' and snapshot_id=?",
                   (config_id, row[0]))
        db.execute("delete from scheduled_task_config_versions where id=?", (row[0],))


def publication_scheduled_source(db: sqlite3.Connection) -> str:
    """Read the exact scheduled layout for the paired publication quiet wait."""
    from app.run_storage import SCHEDULED_RUN_SQL
    from app.action_storage import action_schema_is_current, reply_schema_is_current

    row = db.execute(
        "select value from service_state where key='store_schema_version'"
    ).fetchone()
    version = row[0] if row is not None else None
    tables = {
        row[0]
        for row in db.execute("select name from sqlite_master where type='table'")
    }
    if (
        version in ("2026-10-12.1", "2026-10-13.1", "2026-10-13.2", "2026-10-13.3", "2026-10-13.4", "2026-10-13.5")
        and (reply_schema_is_current(db) if version == "2026-10-13.5" else version == "2026-10-12.1" or action_schema_is_current(db, include_receipts=version in ("2026-10-13.2", "2026-10-13.3", "2026-10-13.4"), include_postfix=version in ("2026-10-13.3", "2026-10-13.4"), include_delivery=version == "2026-10-13.4"))
        and {"tasks", "runs"} <= tables
        and not {
            "scheduled_task_runs",
            "workbench_tasks",
            "workbench_turns",
            "workbench_events",
        }.intersection(tables)
    ):
        return SCHEDULED_RUN_SQL
    if (version == "2026-10-14.1" and reply_schema_is_current(db, include_email=True)
            and {"tasks", "runs"} <= tables
            and not {"scheduled_task_runs", "workbench_tasks", "workbench_turns", "workbench_events"}.intersection(tables)):
        return SCHEDULED_RUN_SQL
    if version in ("2026-10-11.2", "2026-10-11.3") and "scheduled_task_runs" in tables:
        # The C run declaration includes its scheduled parent scalar even when
        # it holds no scheduled facts. An old marker cannot make it a source.
        run_columns = {row[1] for row in db.execute("pragma table_info(runs)")}
        if "scheduled_task_id" not in run_columns:
            return "select dispatch_status, lease_owner, lease_expires_at from scheduled_task_runs"
    raise ValueError("unknown or mixed scheduled publication source schema")
