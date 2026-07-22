from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

QUALITY_TABLES = {
    "schema_migrations",
    "service_component_health",
    "quality_runs",
    "quality_snapshots",
    "quality_incidents",
}


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


MIGRATIONS = (
    Migration(
        version=1,
        name="quality_runtime_tables",
        sql="""
            create table if not exists service_component_health (
                component text primary key,
                status text not null,
                last_success_at text not null default '',
                last_failure_at text not null default '',
                last_error_kind text not null default '',
                consecutive_failures integer not null default 0,
                updated_at text not null default current_timestamp
            );
            create table if not exists quality_runs (
                id integer primary key autoincrement,
                suite text not null,
                mode text not null,
                commit_sha text not null default '',
                status text not null,
                total integer not null,
                passed integer not null,
                failed integer not null,
                score real not null,
                created_at text not null default current_timestamp
            );
            create index if not exists idx_quality_runs_created
                on quality_runs(created_at, id);
            create table if not exists quality_snapshots (
                id integer primary key autoincrement,
                commit_sha text not null default '',
                pid integer not null,
                schema_version integer not null,
                ready integer not null,
                slo_status text not null,
                backlog_json text not null default '{}',
                components_json text not null default '[]',
                created_at text not null default current_timestamp
            );
            create table if not exists quality_incidents (
                id integer primary key autoincrement,
                incident_key text not null unique,
                status text not null default 'open',
                severity text not null,
                owner text not null default '',
                due_at text not null default '',
                summary_code text not null,
                acknowledged_at text not null default '',
                resolved_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );
        """,
    ),
)
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version


def apply_migrations(connection: sqlite3.Connection) -> int:
    connection.execute(
        """
        create table if not exists schema_migrations (
            version integer primary key,
            name text not null,
            checksum text not null,
            applied_at text not null default current_timestamp
        )
        """
    )
    applied = {
        int(row[0]): (str(row[1]), str(row[2]))
        for row in connection.execute(
            "select version, name, checksum from schema_migrations"
        )
    }
    known_versions = {migration.version for migration in MIGRATIONS}
    unknown = set(applied) - known_versions
    if unknown:
        raise RuntimeError(f"database schema is newer than this service: {max(unknown)}")
    for migration in MIGRATIONS:
        previous = applied.get(migration.version)
        if previous is not None:
            if previous != (migration.name, migration.checksum):
                raise RuntimeError(f"migration checksum mismatch: {migration.version}")
            continue
        connection.executescript(migration.sql)
        connection.execute(
            "insert into schema_migrations(version, name, checksum) values (?, ?, ?)",
            (migration.version, migration.name, migration.checksum),
        )
    return CURRENT_SCHEMA_VERSION


def schema_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute("select max(version) from schema_migrations").fetchone()
    except sqlite3.DatabaseError:
        return 0
    return int(row[0] or 0) if row else 0
