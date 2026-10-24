"""Reversible releases of verified email storage migrations.

Rollback applies the matched inverse of each verified transition and never
restores a pre-start snapshot over business writes made by the replacement.
"""
from __future__ import annotations

import ast
from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Callable

from app.email_historical_candidate_schema import (
    SOURCE_VERSION as HISTORICAL_CANDIDATE_SOURCE_VERSION,
    TARGET_VERSION as HISTORICAL_CANDIDATE_TARGET_VERSION,
    compact_candidate_locators,
    restore_candidate_locators,
)

LOOKUP_INDEX = "idx_email_provider_observations_lookup"


def email_historical_candidate_release_selected(
    *, store_marker: str | None, current_store_marker: str,
    email_version: int | None, target_email_version: int,
) -> bool:
    """Whether the exact current Store/Email pair needs the locator transition."""
    return (store_marker == current_store_marker
            and email_version == HISTORICAL_CANDIDATE_SOURCE_VERSION
            and target_email_version == HISTORICAL_CANDIDATE_TARGET_VERSION)


def apply_email_historical_candidate_transition(db, versions, applied_at: str) -> None:
    current = tuple(tuple(row) for row in db.execute(
        "select version,applied_at from email_schema_migrations order by version"
    ))
    if current != tuple(versions) or not current or current[-1][0] != HISTORICAL_CANDIDATE_SOURCE_VERSION:
        raise ValueError("Email 53-to-54 source ledger changed")
    compact_candidate_locators(db)
    db.execute("insert into email_schema_migrations(version,applied_at) values(?,?)",
               (HISTORICAL_CANDIDATE_TARGET_VERSION, applied_at))


def inverse_email_historical_candidate_transition(db, versions) -> None:
    expected = tuple(versions)
    current = tuple(tuple(row) for row in db.execute(
        "select version,applied_at from email_schema_migrations order by version"
    ))
    if current == expected:
        return
    if (len(current) != len(expected) + 1 or current[:-1] != expected
            or current[-1][0] != HISTORICAL_CANDIDATE_TARGET_VERSION
            or not expected or expected[-1][0] != HISTORICAL_CANDIDATE_SOURCE_VERSION):
        raise ValueError("Email rollback requires the exact53-to-54 locator transition")
    restore_candidate_locators(db)
    db.execute("delete from email_schema_migrations where version=?",
               (HISTORICAL_CANDIDATE_TARGET_VERSION,))
    if tuple(tuple(row) for row in db.execute(
        "select version,applied_at from email_schema_migrations order by version"
    )) != expected:
        raise ValueError("Email schema54 rollback ledger readback mismatch")


@dataclass
class EmailHistoricalCandidateLocatorRelease:
    root: Path
    database_path: Path
    versions: tuple[tuple[int, str], ...]

    def publish(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute("begin immediate")
            from datetime import datetime, timezone
            apply_email_historical_candidate_transition(
                db, self.versions, datetime.now(timezone.utc).isoformat())
        self.verify_loaded()

    def verify_loaded(self) -> None:
        with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
            versions = tuple(db.execute(
                "select version,applied_at from email_schema_migrations order by version"
            ))
            if (len(versions) != len(self.versions) + 1 or versions[:-1] != self.versions
                    or versions[-1][0] != HISTORICAL_CANDIDATE_TARGET_VERSION):
                raise ValueError("Email schema54 candidate-locator readback mismatch")

    def rollback(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute("begin immediate")
            inverse_email_historical_candidate_transition(db, self.versions)

    def finalize(self) -> None:
        self.verify_loaded()


def _target_schema_version(root: Path) -> int:
    module = ast.parse((root / "app" / "email_store.py").read_text())
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "EMAIL_SCHEMA_VERSION"
            for target in statement.targets
        ):
            value = ast.literal_eval(statement.value)
            if type(value) is int:
                return value
    raise ValueError("target Email schema version is not an integer constant")


def _migrate(root: Path, database_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-c",
         "from pathlib import Path; import sys; from app.email_store import EmailStore; "
         "EmailStore(Path(sys.argv[1]))", str(database_path)],
        cwd=root, env={**os.environ, "PYTHONPATH": str(root)},
        check=True, capture_output=True, timeout=300,
    )


@dataclass
class EmailSchemaRelease:
    root: Path
    database_path: Path
    versions: tuple[tuple[int, str], ...]
    index_sql: str
    migrate: Callable[[Path, Path], None] = _migrate

    def publish(self) -> None:
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db:
            version = db.execute("select max(version) from email_schema_migrations").fetchone()[0]
            index = db.execute("select sql from sqlite_master where name=?", (LOOKUP_INDEX,)).fetchone()
            if version != 48 or index is not None:
                raise ValueError("Email schema 48 provider-index readback mismatch")

    def rollback(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute("begin immediate")
            versions = tuple(db.execute(
                "select version,applied_at from email_schema_migrations order by version"
            ))
            index = db.execute("select sql from sqlite_master where name=?", (LOOKUP_INDEX,)).fetchone()
            if versions == self.versions and index == (self.index_sql,):
                return
            if not versions or versions[-1][0] != 48 or versions[:-1] != self.versions or index is not None:
                raise ValueError("Email schema rollback requires the exact 47-to-48 index transition")
            db.execute(self.index_sql)
            db.execute("delete from email_schema_migrations where version=48")
            restored = tuple(db.execute(
                "select version,applied_at from email_schema_migrations order by version"
            ))
            if restored != self.versions:
                raise ValueError("Email schema rollback version readback mismatch")

    def finalize(self) -> None:
        self.verify_loaded()


@dataclass
class EmailActionPlanSchemaRelease:
    root: Path
    database_path: Path
    versions: tuple[tuple[int, str], ...]
    legacy_full_snapshot_ids: frozenset[int] = frozenset()
    empty_snapshot_ids: frozenset[int] = frozenset()
    migrate: Callable[[Path, Path], None] = _migrate

    def publish(self) -> None:
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db:
            version = db.execute("select max(version) from email_schema_migrations").fetchone()[0]
            columns = {row[1] for row in db.execute("pragma table_info(email_classifications)")}
            if version != 49 or "action_plan_json" in columns:
                raise ValueError("Email schema49 action-plan reference readback mismatch")

    def rollback(self) -> None:
        from app.email_schema_history import HistoricalEmailStore

        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute("begin immediate")
            versions = tuple(db.execute(
                "select version,applied_at from email_schema_migrations order by version"
            ))
            columns = {row[1] for row in db.execute("pragma table_info(email_classifications)")}
            if versions == self.versions and "action_plan_json" in columns:
                return
            if (not versions or versions[-1][0] != 49 or versions[:-1] != self.versions
                    or "action_plan_json" in columns):
                raise ValueError("Email schema rollback requires the exact48-to-49 plan transition")
            db.execute("alter table email_classifications add column "
                       "action_plan_json text not null default 'null'")
            db.row_factory = sqlite3.Row
            rows = db.execute("select p.* from email_action_plans p "
                              "join email_classifications c on c.current_action_plan_id=p.action_plan_id").fetchall()
            for row in rows:
                plan = HistoricalEmailStore._stored_action_plan(row)
                exclude = ({"authorization_snapshot_format", "action_authorizations"}
                           if row["legacy_serialization_pre_v16"] == 1
                           and row["authorization_snapshot_json"] is None
                           and row["classification_id"] not in self.legacy_full_snapshot_ids else set())
                db.execute("update email_classifications set action_plan_json=? "
                           "where current_action_plan_id=?",
                           (plan.model_dump_json(exclude=exclude), plan.action_plan_id))
            for identity in self.empty_snapshot_ids:
                db.execute("update email_classifications set action_plan_json='' "
                           "where id=? and current_action_plan_id is null", (identity,))
            db.execute("delete from email_schema_migrations where version=49")
            # Use the same original-snapshot validation as the forward migration.
            validator = object.__new__(HistoricalEmailStore)
            validator._validate_durable_state(db)
            validator._validate_cached_action_plan_snapshots(db)
            db.row_factory = None
            restored = tuple(db.execute(
                "select version,applied_at from email_schema_migrations order by version"
            ))
            if restored != self.versions:
                raise ValueError("Email schema rollback version readback mismatch")

    def finalize(self) -> None:
        self.verify_loaded()


@dataclass
class EmailUnsubscribeCancellationSchemaRelease:
    root: Path
    database_path: Path
    versions: tuple[tuple[int, str], ...]
    migrate: Callable[[Path, Path], None] = _migrate

    def publish(self) -> None:
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self) -> None:
        with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
            versions = tuple(db.execute("select version,applied_at from email_schema_migrations order by version"))
            columns = tuple(row[1] for row in db.execute("pragma table_info(email_unsubscribe_cancellations)"))
            if (not versions or versions[-1][0] != 50 or versions[:-1] != self.versions
                    or columns != ("action_identity", "decision_json")):
                raise ValueError("Email schema50 cancellation relation readback mismatch")

    def rollback(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute("begin immediate")
            versions = tuple(db.execute("select version,applied_at from email_schema_migrations order by version"))
            table = db.execute("select 1 from sqlite_master where type='table' and name='email_unsubscribe_cancellations'").fetchone()
            if versions == self.versions and table is None:
                return
            if not versions or versions[-1][0] != 50 or versions[:-1] != self.versions or table is None:
                raise ValueError("Email schema rollback requires the exact49-to-50 cancellation transition")
            if db.execute("select 1 from email_unsubscribe_cancellations limit 1").fetchone():
                raise ValueError("Email schema rollback would discard principal cancellation decisions")
            shared_state = db.execute("select 1 from sqlite_master where type='table' and name='service_state'").fetchone()
            if shared_state is not None and db.execute(
                "select 1 from service_state where substr(key,1,?)=? limit 1",
                (len("email_unsubscribe_execution_pending:v1:"), "email_unsubscribe_execution_pending:v1:"),
            ).fetchone():
                raise ValueError("Email schema rollback would bypass an unknown unsubscribe execution")
            db.execute("drop table email_unsubscribe_cancellations")
            db.execute("delete from email_schema_migrations where version=50")
            if tuple(db.execute("select version,applied_at from email_schema_migrations order by version")) != self.versions:
                raise ValueError("Email cancellation rollback version readback mismatch")

    def finalize(self) -> None:
        self.verify_loaded()


@dataclass
class EmailDirectRetrySchemaRelease:
    root: Path
    database_path: Path
    versions: tuple[tuple[int, str], ...]
    migrate: Callable[[Path, Path], None] = _migrate

    def publish(self) -> None:
        self.migrate(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self) -> None:
        with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
            versions = tuple(db.execute("select version,applied_at from email_schema_migrations order by version"))
            columns = tuple(row[1] for row in db.execute("pragma table_info(email_direct_retry_requests)"))
            if (not versions or versions[-1][0] != 51 or versions[:-1] != self.versions
                    or columns != ("action_id", "attempt_number", "source_readback_digest", "account_configuration_snapshot", "requested_at")
                    or db.execute("pragma foreign_key_check(email_direct_retry_requests)").fetchone() is not None):
                raise ValueError("Email schema51 retry request readback mismatch")

    def rollback(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as db, db:
            db.execute("begin immediate")
            versions = tuple(db.execute("select version,applied_at from email_schema_migrations order by version"))
            table = db.execute("select 1 from sqlite_master where type='table' and name='email_direct_retry_requests'").fetchone()
            if versions == self.versions and table is None:
                return
            if not versions or versions[-1][0] != 51 or versions[:-1] != self.versions or table is None:
                raise ValueError("Email rollback requires the exact50-to-51 retry request transition")
            if db.execute("select 1 from email_direct_retry_requests limit 1").fetchone():
                raise ValueError("Email rollback would discard explicit retry authorization")
            db.execute("drop table email_direct_retry_requests")
            db.execute("delete from email_schema_migrations where version=51")
            if tuple(db.execute("select version,applied_at from email_schema_migrations order by version")) != self.versions:
                raise ValueError("Email retry rollback version readback mismatch")

    def finalize(self) -> None:
        self.verify_loaded()


def prepare_email_schema_release(root: Path, database_path: Path) -> EmailSchemaRelease | EmailActionPlanSchemaRelease | EmailUnsubscribeCancellationSchemaRelease | EmailDirectRetrySchemaRelease | EmailHistoricalCandidateLocatorRelease | None:
    root = root.resolve()
    database_path = database_path.resolve()
    if not database_path.exists():
        return None
    with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("select 1 from sqlite_master where name='email_schema_migrations'").fetchone():
            return None
        versions = tuple(db.execute("select version,applied_at from email_schema_migrations order by version"))
        current = versions[-1][0] if versions else 0
        target = _target_schema_version(root)
        if current == target:
            return None
        if (current, target) == (HISTORICAL_CANDIDATE_SOURCE_VERSION,
                                 HISTORICAL_CANDIDATE_TARGET_VERSION):
            from app.current_schema_manifest import STORE_VERSION, schema_is_current
            from app.state_definitions import state_projection
            if not schema_is_current(db):
                raise ValueError("Email 53-to-54 requires the exact current Store declaration")
            marker = db.execute(
                f"select value from ({state_projection('service_state')}) where key=?",
                ("store_schema_version",),
            ).fetchone()
            if marker != (STORE_VERSION,):
                raise ValueError("Email 53-to-54 requires the current Store marker")
            return EmailHistoricalCandidateLocatorRelease(root, database_path, versions)
        if (current, target) == (50, 51):
            if db.execute("select 1 from sqlite_master where name='email_direct_retry_requests'").fetchone():
                raise ValueError("Email schema50 unexpectedly contains retry request state")
            return EmailDirectRetrySchemaRelease(root, database_path, versions)
        if (current, target) == (49, 50):
            if db.execute("select 1 from sqlite_master where type='table' and name='email_unsubscribe_cancellations'").fetchone():
                raise ValueError("Email schema49 unexpectedly contains cancellation state")
            return EmailUnsubscribeCancellationSchemaRelease(root, database_path, versions)
        if (current, target) == (48, 49):
            column = next((row for row in db.execute("pragma table_info(email_classifications)")
                           if row[1] == "action_plan_json"), None)
            if column is None or (column[2].lower(), column[3], column[4]) != ("text", 1, "'null'"):
                raise ValueError("Email schema48 action-plan snapshot column mismatch")
            legacy_full_ids = frozenset(row[0] for row in db.execute(
                "select c.id from email_classifications c join email_action_plans p "
                "on p.action_plan_id=c.current_action_plan_id "
                "where p.legacy_serialization_pre_v16=1 and p.authorization_snapshot_json is null "
                "and json_type(nullif(c.action_plan_json,''),'$.authorization_snapshot_format') is not null"
            ))
            empty_ids = frozenset(row[0] for row in db.execute(
                "select id from email_classifications where action_plan_json=''"
            ))
            return EmailActionPlanSchemaRelease(root, database_path, versions,
                                               legacy_full_ids, empty_ids)
        if (current, target) != (47, 48):
            raise ValueError("Email schema release needs a verified inverse for this transition")
        index = db.execute("select sql from sqlite_master where type='index' and name=?", (LOOKUP_INDEX,)).fetchone()
        if index is None:
            raise ValueError("Email schema 47 provider lookup index is missing")
        return EmailSchemaRelease(root, database_path, versions, index[0])
