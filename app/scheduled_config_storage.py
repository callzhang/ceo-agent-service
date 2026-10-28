"""One immutable configuration body shared by scheduled trigger records."""

import sqlite3

from app.configs_definitions import config_projection
from app.configs_storage import insert_config


def intern_scheduled_config(db: sqlite3.Connection, snapshot_json: str) -> int:
    insert_config(db, "scheduled_task_config_versions", {"snapshot_json": snapshot_json}, ignore=True)
    return int(db.execute(
        f"select id from ({config_projection('scheduled_task_config_versions')}) where snapshot_json=?",
        (snapshot_json,),
    ).fetchone()[0])
