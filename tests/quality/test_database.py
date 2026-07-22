from __future__ import annotations

import sqlite3

import pytest

from app.quality.database import _prune_backups, check_database, rehearse_database
from app.quality.migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    QUALITY_TABLES,
    apply_migrations,
    schema_version,
)
from app.store import AutoReplyStore


def test_store_applies_and_records_quality_migration(tmp_path) -> None:
    db_path = tmp_path / "service.sqlite3"
    AutoReplyStore(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        version = connection.execute(
            "select max(version) from schema_migrations"
        ).fetchone()[0]

    assert QUALITY_TABLES <= tables
    assert version == CURRENT_SCHEMA_VERSION


def test_database_rehearsal_uses_a_copy(tmp_path) -> None:
    db_path = tmp_path / "service.sqlite3"
    AutoReplyStore(db_path)

    result = rehearse_database(db_path, backup_dir=tmp_path / "backups")

    assert result.ok is True
    assert result.schema_version == CURRENT_SCHEMA_VERSION
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert check_database(db_path).ok is True


def test_database_check_rejects_corruption(tmp_path) -> None:
    db_path = tmp_path / "broken.sqlite3"
    db_path.write_bytes(b"not sqlite")

    result = check_database(db_path)

    assert result.ok is False
    with pytest.raises(sqlite3.DatabaseError):
        AutoReplyStore(db_path)


def test_database_check_and_rehearsal_reject_missing_source(tmp_path) -> None:
    missing = tmp_path / "missing.sqlite3"

    assert check_database(missing).reason == "database_missing"
    assert rehearse_database(missing, backup_dir=tmp_path / "backups").reason == (
        "database_missing"
    )


def test_backup_retention_keeps_latest_seven(tmp_path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for index in range(9):
        (backup_dir / f"ceo-agent-{index:02d}.sqlite3").write_bytes(b"synthetic")

    _prune_backups(backup_dir)

    assert len(list(backup_dir.glob("ceo-agent-*.sqlite3"))) == 7


def test_rehearsal_reports_migration_failure(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "service.sqlite3"
    AutoReplyStore(db_path)

    def fail_migration(connection):
        raise RuntimeError("synthetic")

    monkeypatch.setattr("app.quality.database.apply_migrations", fail_migration)

    result = rehearse_database(db_path, backup_dir=tmp_path / "backups")

    assert result.ok is False
    assert result.reason == "RuntimeError"


def test_migration_registry_rejects_unknown_and_modified_versions() -> None:
    connection = sqlite3.connect(":memory:")
    apply_migrations(connection)
    connection.execute(
        "insert into schema_migrations(version, name, checksum) values (999, 'future', 'x')"
    )
    with pytest.raises(RuntimeError, match="newer"):
        apply_migrations(connection)
    connection.execute("delete from schema_migrations where version=999")
    connection.execute(
        "update schema_migrations set checksum='modified' where version=?",
        (MIGRATIONS[0].version,),
    )
    with pytest.raises(RuntimeError, match="checksum"):
        apply_migrations(connection)


def test_schema_version_is_zero_without_registry() -> None:
    assert schema_version(sqlite3.connect(":memory:")) == 0
