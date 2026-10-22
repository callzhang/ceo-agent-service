"""Explicit Store/Email Action entity release and current-data inverse.

The release contract contains schema fingerprints and source-version facts only.
The identity relation is created in the published database by the release code;
runtime Action readers never consult it.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
from typing import Mapping

from app.current_action_schema_release import _foreign_keys, _rebuild, source_objects
from app.current_schema_manifest import (
    STORE_VERSION as TARGET_STORE_VERSION,
    EMAIL_VERSION as TARGET_EMAIL_VERSION,
    current_objects,
    schema_objects,
    require_current_pair,
)
from app.result_schema_release import _markers
from app.state_storage import update_state_rows

SOURCE_STORE_VERSION = "2026-10-19.12"
SOURCE_EMAIL_VERSION = 53
VERSION_KEY = "store_schema_version"
_ACTION_GUARD_NAMES = frozenset({
    "trg_configs_direct_account_delete", "trg_configs_direct_account_update",
    "trg_emails_direct_action_blocks_plan_switch",
})


def _digest(objects) -> str:
    payload = json.dumps(objects, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_variant(db):
    from app.action_storage import REPLY_VARIANTS
    current = schema_objects(db)
    # Store19.12 is the reviewed fresh Memory parent declaration. The older
    # no-physical-parent variant belongs to earlier explicit release steps.
    variant = "fresh_memory_fk"
    return variant if variant in REPLY_VARIANTS and current == source_objects(variant) else None


def _check_source(db, contract=None):
    marker, versions = _markers(db)
    variant = _source_variant(db)
    if (marker is None or marker[0] != SOURCE_STORE_VERSION or not versions
            or versions[-1][0] != SOURCE_EMAIL_VERSION or variant is None):
        raise ValueError("unsupported Action entity source Store/Email schema or version")
    if contract is not None and (
        marker != tuple(contract["source_marker"])
        or versions != tuple(tuple(item) for item in contract["email_versions"])
        or variant != contract["reply_variant"]
        or _digest(schema_objects(db)) != contract["source_schema_sha256"]
    ):
        raise ValueError("Action entity source identity or schema changed after preparation")
    return marker, versions, variant


def prepare_action_entity_release(database: Path) -> dict:
    """Read-only source preflight. The returned contract contains no row bodies."""
    path = Path(database).resolve()
    if not path.exists():
        raise ValueError("Action entity source database does not exist")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        marker, versions, variant = _check_source(db)
        findings = _foreign_keys(db)
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("Action entity source integrity check failed")
        return {
            "database": str(path),
            "source_marker": marker,
            "source_version": marker[0],
            "email_versions": versions,
            "reply_variant": variant,
            "source_schema_sha256": _digest(schema_objects(db)),
            "source_fk_findings": findings,
            "target_version": TARGET_STORE_VERSION,
            "target_email_version": TARGET_EMAIL_VERSION,
        }


def _create_target_children(db):
    from app.current_action_schema import _action_entity_objects
    for obj in sorted(_action_entity_objects(), key=lambda item: item["type"] != "table"):
        db.execute(obj["sql"])


def _source_expected(variant):
    return source_objects(variant)


def _target_expected():
    return current_objects(email_ledger=True)


def _drop_action_guards(db):
    for name in _ACTION_GUARD_NAMES:
        db.execute(f'drop trigger if exists "{name}"')


def _create_action_guards(db, objects):
    for kind, name, _table, sql in objects:
        if kind == "trigger" and name in _ACTION_GUARD_NAMES:
            db.execute(sql)


def publish_action_entity_release(database: Path, contract: Mapping[str, object]) -> None:
    """Atomically publish the current declaration for the prepared source."""
    path = Path(database).resolve()
    if Path(str(contract.get("database"))).resolve() != path:
        raise ValueError("Action entity release contract belongs to another database")
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        from app.task8_storage import register_task8_functions
        register_task8_functions(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        try:
            marker, _, variant = _check_source(db, contract)
            if _foreign_keys(db) != tuple(tuple(x) for x in contract["source_fk_findings"]):
                raise ValueError("Action entity source FK findings changed after preparation")
            _drop_action_guards(db)
            _rebuild(db, variant, inverse=False)
            _create_target_children(db)
            _create_action_guards(db, _target_expected())
            update_state_rows(db, "service_state", {
                "value": TARGET_STORE_VERSION,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, where_sql="key=?", parameters=(VERSION_KEY,))
            if (schema_objects(db) != _target_expected()
                    or _foreign_keys(db) != tuple(tuple(x) for x in contract["source_fk_findings"])
                    or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                raise ValueError("Action entity target declaration, FK, or integrity mismatch")
            db.commit()
        except BaseException:
            db.rollback()
            raise


def invert_action_entity_release(database: Path, contract: Mapping[str, object]) -> None:
    """Rebuild from current rows into the exact predecessor declaration."""
    path = Path(database).resolve()
    if Path(str(contract.get("database"))).resolve() != path:
        raise ValueError("Action entity release contract belongs to another database")
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        from app.task8_storage import register_task8_functions
        register_task8_functions(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        try:
            if (schema_objects(db) != _target_expected()
                    or _markers(db)[0] is None
                    or _markers(db)[0][0] != TARGET_STORE_VERSION):
                raise ValueError("Action entity inverse requires the exact published target")
            if _foreign_keys(db) != tuple(tuple(x) for x in contract["source_fk_findings"]):
                raise ValueError("Action entity target FK findings changed before inverse")
            _drop_action_guards(db)
            db.execute("drop table action_entity_identity_map")
            db.execute("drop table action_observers")
            db.execute("drop table action_receipts")
            _rebuild(db, contract["reply_variant"], inverse=True)
            _create_action_guards(db, _source_expected(contract["reply_variant"]))
            update_state_rows(db, "service_state", {
                "value": contract["source_marker"][0],
                "updated_at": contract["source_marker"][1],
            }, where_sql="key=?", parameters=(VERSION_KEY,))
            if (schema_objects(db) != _source_expected(contract["reply_variant"])
                    or _foreign_keys(db) != tuple(tuple(x) for x in contract["source_fk_findings"])
                    or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                raise ValueError("Action entity inverse declaration, FK, or integrity mismatch")
            db.commit()
        except BaseException:
            db.rollback()
            raise


@dataclass
class ActionEntitySchemaRelease:
    root: Path
    database_path: Path
    contract: Mapping[str, object]

    def publish(self):
        publish_action_entity_release(self.database_path, self.contract)
        from app.store_schema_release import _migrate_store
        _migrate_store(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self):
        require_current_pair(self.database_path)
        with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
            if schema_objects(db) != _target_expected():
                raise ValueError("Action entity current schema readback mismatch")

    def rollback(self):
        invert_action_entity_release(self.database_path, self.contract)

    def finalize(self):
        self.verify_loaded()
