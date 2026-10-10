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
    columns = {row[1] for row in db.execute("pragma table_info(scheduled_task_runs)")}
    if "snapshot_json" in columns:
        if "snapshot_id" not in columns:
            db.execute("alter table scheduled_task_runs add column snapshot_id integer "
                       "references scheduled_task_config_versions(id)")
        db.execute("insert or ignore into scheduled_task_config_versions(snapshot_json) "
                   "select distinct snapshot_json from scheduled_task_runs")
        db.execute("update scheduled_task_runs set snapshot_id=(select id "
                   "from scheduled_task_config_versions as config "
                   "where config.snapshot_json=scheduled_task_runs.snapshot_json)")
        db.execute("alter table scheduled_task_runs drop column snapshot_json")
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
        db.execute("update scheduled_task_runs set snapshot_id=? where snapshot_id=?",
                   (config_id, row[0]))
        db.execute("delete from scheduled_task_config_versions where id=?", (row[0],))
