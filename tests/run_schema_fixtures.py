"""The recorded 11.1 core schema, paired with current unaffected test tables."""

import json
from pathlib import Path

from app.run_storage import RETIRED_RUN_TABLES, capture_run_schema, restore_run_schema
from app.store import AutoReplyStore, _INITIALIZED_STORE_PATHS


def previous_run_schema_store(
    path: Path, *, task_args: dict | None = None
) -> AutoReplyStore:
    """Return a complete empty database using the actual preceding core DDL."""
    store = AutoReplyStore(path)
    if task_args is not None:
        store.enqueue_reply_task(**task_args)
    inventory = json.loads(
        (
            Path(__file__).parents[1]
            / "docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json"
        ).read_text()
    )
    with store._connect() as db:
        state = capture_run_schema(db)
        children = []
        for (table,) in db.execute(
            "select name from sqlite_master where type='table'"
        ).fetchall():
            if table == "runs" or not any(
                row[2] == "runs"
                for row in db.execute(f'pragma foreign_key_list("{table}")')
            ):
                continue
            children.append(table)
            columns = [r[1] for r in db.execute(f'pragma table_info("{table}")')]
            objects = [
                dict(zip(("type", "name", "sql"), row, strict=True))
                for row in db.execute(
                    "select type,name,sql from sqlite_master where tbl_name=? and sql is not null",
                    (table,),
                )
                if not row[1].startswith("trg_runs_child_")
            ]
            for obj in objects:
                if obj["type"] == "table":
                    obj["sql"] = obj["sql"].replace(
                        "references runs(id)", "references agent_runs(id)"
                    )
            sequence = db.execute(
                "select seq from sqlite_sequence where name=?", (table,)
            ).fetchone()
            state["tables"][table] = {
                "objects": objects,
                "columns": columns,
                "sequence": sequence[0] if sequence else None,
            }
        state["children"] = children
        for table in RETIRED_RUN_TABLES:
            old = inventory["tables"][table]
            objects = [{"type": "table", "name": table, "sql": old["sql"]}]
            objects.extend(
                {"type": kind, "name": name, "sql": sql}
                for kind, name, sql in old["indexes_and_triggers"]
                if sql is not None
            )
            state["tables"][table] = {
                "objects": objects,
                "columns": [r[1] for r in old["columns"]],
                "sequence": 0,
            }
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        restore_run_schema(db, state)
        db.execute(
            "update service_state set value='2026-10-11.1' where key='store_schema_version'"
        )
    _INITIALIZED_STORE_PATHS.discard(path.resolve())
    return store
