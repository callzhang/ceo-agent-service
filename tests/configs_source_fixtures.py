"""Owned SQLite fixtures from the frozen actual/published-fresh source DDL."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json"
CONFIGS = (
    "email_accounts", "email_category_configs",
    "email_category_description_revisions", "email_category_folder_bindings",
    "email_model_mode_transitions", "email_model_promotion_configs",
    "email_training_snapshots", "managed_skill_revisions", "managed_skills",
    "runtime_feedback_iteration_capabilities", "runtime_skill_bindings",
    "runtime_skill_configs", "scheduled_task_config_versions",
    "scheduled_task_skill_refs", "wechat_reply_scopes",
)


def capture():
    return json.loads(CAPTURE.read_text())


@contextmanager
def source_db(variant):
    assert variant in ("actual", "fresh")
    tables = capture()[variant]["tables"]
    db = sqlite3.connect(":memory:")
    try:
        db.execute("pragma foreign_keys=on")
        for spec in tables.values():
            db.execute(spec["sql"])
        for spec in tables.values():
            for index in reversed(spec["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in spec["triggers"]:
                db.execute(trigger["sql"])
        yield db
    finally:
        db.close()


def insert(db, table, values, *, ignore=False):
    assert table in CONFIGS
    columns = tuple(values)
    return db.execute(
        f"insert {'or ignore ' if ignore else ''}into {table} "
        f"({','.join(columns)}) values ({','.join('?' for _ in columns)})",
        tuple(values.values()),
    )
