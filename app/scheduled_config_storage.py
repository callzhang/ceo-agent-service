"""One immutable configuration body shared by scheduled trigger records."""

import json
import sqlite3

from app.configs_definitions import config_projection
from app.configs_storage import insert_config


def intern_scheduled_config(db: sqlite3.Connection, snapshot_json: str) -> int:
    insert_config(db, "scheduled_task_config_versions", {"snapshot_json": snapshot_json}, ignore=True)
    return int(db.execute(
        f"select id from ({config_projection('scheduled_task_config_versions')}) where snapshot_json=?",
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
        db.execute(
            "insert or ignore into scheduled_task_config_versions(snapshot_json) values (?)",
            (body,),
        )
        config_id = int(db.execute(
            "select id from scheduled_task_config_versions where snapshot_json=?", (body,)
        ).fetchone()[0])
        db.execute("update runs set snapshot_id=? where record_type='scheduled_task_runs' and snapshot_id=?",
                   (config_id, row[0]))
        db.execute("delete from scheduled_task_config_versions where id=?", (row[0],))
