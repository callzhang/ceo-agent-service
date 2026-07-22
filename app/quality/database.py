from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.quality.migrations import apply_migrations, schema_version


@dataclass(frozen=True)
class DatabaseCheckResult:
    ok: bool
    schema_version: int = 0
    quick_check: str = ""
    foreign_key_violations: int = 0
    reason: str = ""
    backup_path: Path | None = None


def _inspect(connection: sqlite3.Connection) -> DatabaseCheckResult:
    quick_row = connection.execute("pragma quick_check").fetchone()
    quick = str(quick_row[0]) if quick_row else "missing"
    violations = len(connection.execute("pragma foreign_key_check").fetchall())
    return DatabaseCheckResult(
        ok=quick == "ok" and violations == 0,
        schema_version=schema_version(connection),
        quick_check=quick,
        foreign_key_violations=violations,
        reason="" if quick == "ok" and violations == 0 else "integrity_check_failed",
    )


def check_database(path: Path) -> DatabaseCheckResult:
    if not path.exists():
        return DatabaseCheckResult(ok=False, reason="database_missing")
    try:
        with sqlite3.connect(path) as connection:
            connection.execute("pragma foreign_keys=on")
            return _inspect(connection)
    except sqlite3.DatabaseError:
        return DatabaseCheckResult(ok=False, reason="database_unreadable")


def _prune_backups(backup_dir: Path, *, keep: int = 7) -> None:
    backups = sorted(backup_dir.glob("ceo-agent-*.sqlite3"), reverse=True)
    for stale in backups[keep:]:
        stale.unlink()


def rehearse_database(path: Path, *, backup_dir: Path) -> DatabaseCheckResult:
    source_check = check_database(path)
    if not source_check.ok:
        return source_check
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"ceo-agent-{stamp}.sqlite3"
    try:
        with sqlite3.connect(path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        with sqlite3.connect(backup_path) as rehearsal:
            rehearsal.execute("pragma foreign_keys=on")
            apply_migrations(rehearsal)
            result = _inspect(rehearsal)
        _prune_backups(backup_dir)
        return DatabaseCheckResult(
            ok=result.ok,
            schema_version=result.schema_version,
            quick_check=result.quick_check,
            foreign_key_violations=result.foreign_key_violations,
            reason=result.reason,
            backup_path=backup_path,
        )
    except (OSError, sqlite3.DatabaseError, RuntimeError) as exc:
        return DatabaseCheckResult(ok=False, reason=type(exc).__name__)
