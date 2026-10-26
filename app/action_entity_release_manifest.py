"""Pinned declarations for the explicit historical Action Entity release.

These manifests model the Store 19.12 -> 21.1 intermediate release only.
They are intentionally independent of current runtime schema factories and
are not an automatic startup/deploy upgrade path.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sqlite3

SOURCE_STORE_VERSION = "2026-10-19.12"
SOURCE_EMAIL_VERSION = 53
TARGET_STORE_VERSION = "2026-10-21.1"
TARGET_EMAIL_VERSION = 53
SOURCE_VARIANT = "fresh_memory_fk"

_FIXTURES = Path(__file__).with_name("schema_fixtures")
_SOURCE_DDL = _FIXTURES / "action_entity_source_2026_10_19_12_email53.sql"
_TARGET_DDL = _FIXTURES / "action_entity_target_2026_10_21_1_email53.sql"


def _read_manifest(path: Path) -> tuple[tuple[str, str, str, str], ...]:
    if not path.is_file():
        raise RuntimeError(f"pinned Action Entity release declaration is missing: {path}")
    from app.task8_storage import register_task8_functions

    with sqlite3.connect(":memory:") as db:
        register_task8_functions(db)
        db.executescript(path.read_text())
        return tuple(tuple(row) for row in db.execute(
            "select type,name,tbl_name,sql from main.sqlite_schema "
            "where name not glob 'sqlite_*' order by type,name"
        ))


@lru_cache(maxsize=1)
def source_objects(variant: str = SOURCE_VARIANT) -> tuple[tuple[str, str, str, str], ...]:
    if variant != SOURCE_VARIANT:
        raise ValueError(f"unsupported historical Action Entity source variant: {variant}")
    return _read_manifest(_SOURCE_DDL)


@lru_cache(maxsize=1)
def target_objects() -> tuple[tuple[str, str, str, str], ...]:
    return _read_manifest(_TARGET_DDL)
