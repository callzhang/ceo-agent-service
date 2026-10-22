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
from collections import Counter
from typing import Mapping

from app.current_action_schema_release import _foreign_keys, source_objects
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


def _rebuild_action_owner_table(db):
    """Replace only the Actions declaration, preserving every surviving stored cell."""
    from app.current_action_schema import current_action_objects
    from app.action_email_storage import _quote

    if db.execute("select 1 from temp.sqlite_schema where name='_action_entity_rows'").fetchone():
        raise ValueError("Action entity transfer TEMP collision")
    source_cols = [row[1] for row in db.execute("pragma main.table_xinfo(actions)") if row[6] == 0]
    target_table = next(obj["sql"] for obj in current_action_objects() if obj["type"] == "table")
    with closing(sqlite3.connect(":memory:")) as target_db:
        from app.task8_storage import register_task8_functions
        register_task8_functions(target_db)
        target_db.execute(target_table)
        target_cols = [row[1] for row in target_db.execute("pragma table_xinfo(actions)") if row[6] == 0]
    common = [column for column in source_cols if column in target_cols]
    added = [column for column in target_cols if column not in source_cols]
    select = [_quote(column) for column in common]
    select.extend("null as " + _quote(column) for column in added)
    if "id" not in common or "payload_json" not in common:
        raise ValueError("Action entity transfer lacks the physical source identity or payload")
    sequence_rows = db.execute("select rowid,name,seq from main.sqlite_sequence").fetchall()
    db.execute("create temp table _action_entity_rows as select " + ",".join(select) + " from main.actions")
    before = db.execute("select count(*) from temp._action_entity_rows").fetchone()[0]
    db.execute("drop table main.actions")
    target_objects = current_action_objects()
    db.execute(target_table)
    names = ",".join(_quote(column) for column in (*common, *added))
    db.execute(f"insert into main.actions({names}) select {names} from temp._action_entity_rows order by id")
    if db.execute("select count(*) from main.actions").fetchone()[0] != before:
        raise ValueError("Action entity transfer changed surviving Action row count")
    if db.execute(f"select {names} from main.actions except select {names} from temp._action_entity_rows limit 1").fetchone():
        raise ValueError("Action entity transfer changed surviving stored cells")
    for obj in target_objects:
        if obj["type"] in {"index", "trigger"}:
            db.execute(obj["sql"])
    db.execute("delete from main.sqlite_sequence")
    db.executemany("insert into main.sqlite_sequence(rowid,name,seq) values(?,?,?)", sequence_rows)
    db.execute("drop table temp._action_entity_rows")


def _rebuild_inverse_action_owner_table(db):
    """Restore the frozen predecessor Actions declaration with raw cells.

    Task 5C's Store19.12 source has four generated Attempt columns whose
    values are carried in the original JSONB payload even though the older
    generic 19.11 conversion layout omits them.  The inverse therefore copies
    the stored predecessor/current intersection byte-for-byte and lets the
    exact predecessor declaration regenerate its public projections.
    """
    from app.action_email_storage import _columns, _quote
    from app.current_action_schema import source_current_action_objects

    stage = "_action_entity_inverse_rows"
    water = "_action_entity_inverse_water"
    if db.execute("select 1 from temp.sqlite_schema where name in (?,?)", (stage, water)).fetchone():
        raise ValueError("Action entity inverse TEMP collision")
    target_objects = source_current_action_objects()
    target_table = next(obj["sql"] for obj in target_objects if obj["type"] == "table")
    with closing(sqlite3.connect(":memory:")) as target_db:
        from app.task8_storage import register_task8_functions
        register_task8_functions(target_db)
        target_db.execute(target_table)
        target_cols = [row[1] for row in target_db.execute("pragma table_xinfo(actions)") if row[6] == 0]
    source_cols = _columns(db, "actions")
    common = [column for column in source_cols if column in target_cols]
    added = [column for column in target_cols if column not in source_cols]
    if "id" not in common or "payload_json" not in common:
        raise ValueError("Action entity inverse lacks source identity or raw payload")
    names = ",".join(_quote(column) for column in (*common, *added))
    source_values = [_quote(column) for column in common]
    source_values.extend("null as " + _quote(column) for column in added)
    db.execute("create temp table " + _quote(stage) + " as select "
               + ",".join(source_values) + " from main.actions")
    db.execute("create unique index temp.idx_action_entity_inverse_rows_id on "
               + _quote(stage) + "(id)")
    db.execute("create temp table " + _quote(water)
               + " as select rowid,name,seq from main.sqlite_sequence")
    db.execute("drop table main.actions")
    db.execute(target_table)
    db.execute(f"insert into main.actions({names}) select {names} from temp.{_quote(stage)} order by id")
    for obj in target_objects:
        if obj["type"] in {"index", "trigger"}:
            db.execute(obj["sql"])
    # Compare every stored cell that existed on both declarations, including
    # JSONB BLOB bytes and SQLite storage classes. Only predecessor-only
    # prepared-child columns are intentionally initialized to SQL NULL here.
    compare_names = ",".join(_quote(column) for column in common)
    typed = ",".join("typeof(" + _quote(column) + ")" for column in common)
    for left, right in (("main.actions", "temp." + _quote(stage)),
                        ("temp." + _quote(stage), "main.actions")):
        if db.execute(f"select {compare_names},{typed} from {left} except "
                      f"select {compare_names},{typed} from {right} limit 1").fetchone():
            raise ValueError("Action entity inverse changed raw predecessor-intersection cells")
    db.execute("delete from main.sqlite_sequence")
    db.execute(f"insert into main.sqlite_sequence(rowid,name,seq) "
               f"select rowid,name,seq from temp.{_quote(water)}")
    db.execute("drop table temp." + _quote(stage))
    db.execute("drop table temp." + _quote(water))


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
            from app.action_entity_transfer import capture_source_facts, transfer_source_facts

            source_facts = capture_source_facts(db)
            from app.current_action_schema import RETIRED_ACTION_TYPES

            covered = tuple(sorted(RETIRED_ACTION_TYPES))
            db.execute(
                "delete from actions where record_type in (" + ",".join("?" for _ in covered) + ")",
                covered,
            )
            _drop_action_guards(db)
            _rebuild_action_owner_table(db)
            _create_target_children(db)
            transfer_source_facts(db, source_facts)
            _create_action_guards(db, _target_expected())
            update_state_rows(db, "service_state", {
                "value": TARGET_STORE_VERSION,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, where_sql="key=?", parameters=(VERSION_KEY,))
            actual_schema = schema_objects(db)
            target_schema = _target_expected()
            actual_fk = _foreign_keys(db)
            quick_check = db.execute("pragma quick_check").fetchone()[0]
            if (actual_schema != target_schema
                    or actual_fk != tuple(tuple(x) for x in contract["source_fk_findings"])
                    or quick_check != "ok"):
                expected_names = {(kind, name) for kind, name, _, _ in target_schema}
                actual_names = {(kind, name) for kind, name, _, _ in actual_schema}
                expected_fk = tuple(tuple(x) for x in contract["source_fk_findings"])
                def fk_counts(rows):
                    return Counter((row[0], row[2], row[3]) for row in rows)
                expected_fk_counts = fk_counts(expected_fk)
                actual_fk_counts = fk_counts(actual_fk)
                raise ValueError(
                    "Action entity target declaration, FK, or integrity mismatch: "
                    f"missing={sorted(expected_names-actual_names)} extra={sorted(actual_names-expected_names)} "
                    f"fk_equal={actual_fk == tuple(tuple(x) for x in contract['source_fk_findings'])} "
                    f"fk_added={sorted((key, count - expected_fk_counts[key]) for key, count in actual_fk_counts.items() if count > expected_fk_counts[key])} "
                    f"fk_removed={sorted((key, count - actual_fk_counts[key]) for key, count in expected_fk_counts.items() if count > actual_fk_counts[key])} "
                    f"quick_check={quick_check}"
                )
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
            from app.action_entity_transfer import _capture_inverse_facts, restore_inverse_facts
            inverse_facts = _capture_inverse_facts(db)
            _drop_action_guards(db)
            db.execute("drop table action_entity_identity_map")
            db.execute("drop table action_observers")
            db.execute("drop table action_receipts")
            db.execute("delete from actions where record_type='action'")
            _rebuild_inverse_action_owner_table(db)
            restore_inverse_facts(db, inverse_facts)
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
