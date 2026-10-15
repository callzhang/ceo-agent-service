"""Unselected caller-transactional Email configs storage."""

import sqlite3

from app.configs_definitions import CONFIG_INDEX_DDL, CONFIG_TABLE_DDL, CONFIG_TRIGGER_DDL


def create_configs_schema(db: sqlite3.Connection) -> None:
    """Install declarations only; caller owns transaction and schema selection."""
    for sql in (CONFIG_TABLE_DDL, *CONFIG_INDEX_DDL, *CONFIG_TRIGGER_DDL):
        db.execute(sql)
