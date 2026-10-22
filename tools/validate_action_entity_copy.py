#!/usr/bin/env python3
"""Validate an Action Entity migration on owned SQLite copies only."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class CapacityMeasure:
    population_sha256: str
    vacuumed: bool
    table_pages: int
    index_pages: int
    freelist_pages: int
    file_bytes: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _owned(path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(Path("/tmp").resolve()):
        raise ValueError(f"owned workspace must be under /tmp: {path}")
    return path


def backup_readonly(source: Path, destination: Path) -> str:
    """Use SQLite's backup API from a mode=ro connection; destination is owned."""
    source = Path(source).resolve()
    destination = _owned(Path(destination))
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    uri = source.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as src, sqlite3.connect(destination) as dst:
        src.execute("PRAGMA query_only=ON")
        src.backup(dst)
    return _sha256(destination)


def check_readonly_backup_calls(calls: list[tuple[str, bool]]) -> None:
    """Assert the source connection is read-only and no source write handle exists."""
    if not calls or calls[0] != ("source", True):
        raise AssertionError("source database must be opened with mode=ro")
    if any(name == "source" and not readonly for name, readonly in calls):
        raise AssertionError("production/source connection opened for write")


def check_live_provider_rejected(provider: object) -> None:
    if not isinstance(provider, StrictSubstituteProvider):
        raise TypeError("acceptance harness only accepts StrictSubstituteProvider")


def check_new_facts_survive_inverse(before: Mapping[str, str], after: Mapping[str, str]) -> None:
    if not before or any(not value for value in before.values()):
        raise AssertionError("new Action/receipt digests are required before inverse")
    if dict(before) != dict(after):
        raise AssertionError("new Action facts changed across inverse and re-forward")


def assert_capacity_pair(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    for field in ("population_sha256", "vacuumed", "page_size"):
        if before.get(field) != after.get(field):
            raise AssertionError(f"capacity cohorts differ in {field} packing/population")
    if before["vacuumed"] is not True:
        raise AssertionError("both capacity databases must use VACUUM packing")
    if not isinstance(before["page_size"], int) or before["page_size"] <= 0:
        raise AssertionError("capacity database page_size must be a positive SQLite page size")


class StrictSubstituteProvider:
    """A deliberately local provider double; it never opens a network client."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send_message(self, conversation_id: str, text: str, **_: Any) -> dict[str, Any]:
        receipt = {"conversation_id": conversation_id, "text_sha256": hashlib.sha256(text.encode()).hexdigest()}
        self.sent.append(receipt)
        return {"success": True, "result": {"openMessageId": "copy-substitute-receipt"}}

    def verify_message_send_result(self, result: Mapping[str, Any]) -> dict[str, str]:
        if result.get("result", {}).get("openMessageId") != "copy-substitute-receipt":
            raise AssertionError("substitute receipt mismatch")
        return {"state": "sent", "message_id": "copy-substitute-receipt"}


def _population_digest(db_path: Path) -> str:
    """Hash ordered semantic facts without returning private text to the report."""
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        h = hashlib.sha256()
        tables = [row[0] for row in db.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
        )]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            cols = [r[1] for r in db.execute(f"pragma table_xinfo({quoted})") if r[6] == 0]
            if not cols:
                continue
            projection = ",".join('"' + c.replace('"', '""') + '"' for c in cols)
            for row in db.execute(f"select {projection} from {quoted}"):
                h.update(table.encode())
                for value in row:
                    if isinstance(value, bytes):
                        h.update(b"B" + len(value).to_bytes(8, "big") + value)
                    elif value is None:
                        h.update(b"N")
                    else:
                        encoded = (type(value).__name__ + ":" + str(value)).encode()
                        h.update(b"V" + len(encoded).to_bytes(8, "big") + encoded)
        return h.hexdigest()


def _hash_value(digest: Any, value: Any) -> None:
    if value is None:
        digest.update(b"N")
    elif isinstance(value, bytes):
        digest.update(b"B" + len(value).to_bytes(8, "big") + value)
    else:
        encoded = (type(value).__name__ + ":" + str(value)).encode()
        digest.update(b"V" + len(encoded).to_bytes(8, "big") + encoded)


def _action_semantic_digest(db: sqlite3.Connection) -> dict[str, Any]:
    """Hash predecessor Actions by typed source key, normalizing covered IDs only."""
    stored_columns = [row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0]
    required = {"id", "_kind", "legacy_key", "sent_reply_id"}
    if not required <= set(stored_columns):
        raise AssertionError("source Actions declaration lacks typed identity or Sent reference")
    projection = ",".join('"' + name.replace('"', '""') + '"' for name in stored_columns)
    records: dict[tuple[Any, Any], dict[str, Any]] = {}
    sent_reference_digest = hashlib.sha256()
    cursor = db.execute(f"select record_type,{projection} from actions")
    names = [item[0] for item in cursor.description]
    for values in cursor:
        row = dict(zip(names, values, strict=True))
        identity = (row["record_type"], row["legacy_key"])
        if not all(identity) or identity in records:
            raise AssertionError("Action typed source identities must be nonempty and unique")
        records[identity] = row
    sent_by_id = {row["id"]: identity for identity, row in records.items()
                  if identity[0] == "sent_replies"}
    digest = hashlib.sha256()
    for identity in sorted(records, key=lambda item: (str(item[0]), str(item[1]))):
        row = records[identity]
        digest.update(b"action-identity")
        _hash_value(digest, identity[0])
        _hash_value(digest, identity[1])
        for column in stored_columns:
            if column == "id":
                continue
            digest.update(column.encode())
            if column == "sent_reply_id":
                target = row[column]
                referenced_identity = None if target is None else sent_by_id.get(target)
                if target is not None and referenced_identity is None:
                    raise AssertionError("Sent reference does not resolve to a typed Sent source identity")
                _hash_value(digest, referenced_identity)
                if target is not None:
                    sent_reference_digest.update(repr((identity, referenced_identity)).encode())
                continue
            _hash_value(digest, row[column])
    return {"sha256": digest.hexdigest(), "action_rows": len(records),
            "typed_identities": len(records),
            "sent_reference_rows": sum(row["sent_reply_id"] is not None for row in records.values()),
            "sent_reference_identity_sha256": sent_reference_digest.hexdigest(),
            "physical_id_by_identity": {identity: row["id"] for identity, row in records.items()},
            "record_type_by_identity": {identity: identity[0] for identity in records}}


def _database_rows_digest(db_path: Path) -> tuple[str, int, dict[str, int], dict[str, Any]]:
    """Hash stored rows; Actions are keyed by typed source identity, not remapped rowid."""
    digest, total = hashlib.sha256(), 0
    counts: dict[str, int] = {}
    with sqlite3.connect(db_path) as db:
        tables = [row[0] for row in db.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
        )]
        without_rowid = {row[1] for row in db.execute("pragma table_list") if row[4]}
        action_semantics = _action_semantic_digest(db) if "actions" in tables else {}
        for table in tables:
            if table == "actions":
                digest.update(b"actions")
                digest.update(action_semantics["sha256"].encode())
                counts[table] = action_semantics["action_rows"]
                total += counts[table]
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            if table in without_rowid:
                pk = [row[1] for row in sorted(db.execute(f"pragma table_info({quoted})"), key=lambda r: r[5]) if row[5]]
                order = ",".join('"' + name.replace('"', '""') + '"' for name in pk)
                query = f"select * from {quoted} order by {order}"
            else:
                query = f"select rowid,* from {quoted} order by rowid"
            count = 0
            for row in db.execute(query):
                digest.update(table.encode())
                for value in row:
                    _hash_value(digest, value)
                count += 1
            counts[table] = count
            total += count
        sequence = db.execute("select name,seq from sqlite_sequence order by name").fetchall()
        for row in sequence:
            digest.update(b"sqlite_sequence")
            digest.update(repr(tuple(row)).encode())
        total += len(sequence)
    return digest.hexdigest(), total, counts, action_semantics


def _compare_source_databases(expected: Path, actual: Path) -> dict[str, Any]:
    """Compare every source fact, retaining physical identity outside covered types."""
    from app.current_action_schema import RETIRED_ACTION_TYPES

    expected_digest, expected_rows, expected_counts, expected_actions = _database_rows_digest(expected)
    actual_digest, actual_rows, actual_counts, actual_actions = _database_rows_digest(actual)
    if set(expected_actions.get("physical_id_by_identity", {})) != set(actual_actions.get("physical_id_by_identity", {})):
        raise AssertionError("full old-schema inverse typed Action identity set differs")
    remaps: dict[str, int] = {}
    for identity, before_id in expected_actions.get("physical_id_by_identity", {}).items():
        after_id = actual_actions["physical_id_by_identity"][identity]
        if before_id != after_id:
            record_type = expected_actions["record_type_by_identity"][identity]
            remaps[record_type] = remaps.get(record_type, 0) + 1
    out_of_scope_remaps = sorted(set(remaps) - RETIRED_ACTION_TYPES)
    if out_of_scope_remaps:
        raise AssertionError("out-of-scope physical Action IDs changed: " + ", ".join(out_of_scope_remaps))
    if expected_actions.get("sent_reference_identity_sha256") != actual_actions.get("sent_reference_identity_sha256"):
        raise AssertionError("Sent references no longer resolve to the same typed source identities")
    with sqlite3.connect(expected) as left, sqlite3.connect(actual) as right:
        expected_schema = left.execute(
            "select type,name,tbl_name,sql from sqlite_master where type in ('table','index','trigger','view') order by type,name"
        ).fetchall()
        actual_schema = right.execute(
            "select type,name,tbl_name,sql from sqlite_master where type in ('table','index','trigger','view') order by type,name"
        ).fetchall()
        expected_fk = sorted(tuple(row) for row in left.execute("pragma foreign_key_check"))
        actual_fk = sorted(tuple(row) for row in right.execute("pragma foreign_key_check"))
        expected_sequences = left.execute("select name,seq from sqlite_sequence order by name").fetchall()
        actual_sequences = right.execute("select name,seq from sqlite_sequence order by name").fetchall()
    if expected_counts != actual_counts:
        changed = sorted(name for name in set(expected_counts) | set(actual_counts)
                         if expected_counts.get(name) != actual_counts.get(name))
        raise AssertionError("old-schema inverse row counts differ: " + ", ".join(changed))
    if expected_digest != actual_digest or expected_rows != actual_rows:
        raise AssertionError("full old-schema inverse semantic population differs from the same-cohort baseline")
    if expected_sequences != actual_sequences:
        raise AssertionError("old-schema inverse sqlite_sequence allocation state differs")
    if expected_schema != actual_schema:
        raise AssertionError("old-schema inverse DDL/index/trigger declarations differ")
    if expected_fk != actual_fk:
        raise AssertionError("old-schema inverse FK findings differ from the baseline")
    return {"population_sha256": expected_digest, "verified_population_sha256": actual_digest,
            "stored_rows": expected_rows, "table_rows": expected_counts,
            "fk_findings": len(expected_fk), "exact_match": True,
            "action_typed_identity_count": expected_actions["typed_identities"],
            "physical_action_id_remaps_by_type": remaps,
            "out_of_scope_physical_action_id_remaps": 0,
            "sent_reference_rows": expected_actions["sent_reference_rows"],
            "sent_reply_reference_identity_sha256": expected_actions["sent_reference_identity_sha256"],
            "sequences_exact": True, "schema_exact": True}


def _schema_inventory(source: Path, current: Path) -> dict[str, Any]:
    from app.current_action_schema import RETIRED_ACTION_TYPES

    def one(path: Path) -> dict[str, Any]:
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            action_columns = db.execute("pragma table_xinfo(actions)").fetchall()
            indexes = db.execute(
                "select name,tbl_name,sql from sqlite_master where type='index' and name not glob 'sqlite_*' order by name"
            ).fetchall()
            type_counts = dict(db.execute("select record_type,count(*) from actions group by record_type order by record_type"))
            receipts = db.execute("select count(*) from action_receipts").fetchone()[0] if db.execute(
                "select 1 from sqlite_master where type='table' and name='action_receipts'").fetchone() else 0
            observers = db.execute("select count(*) from action_observers").fetchone()[0] if db.execute(
                "select 1 from sqlite_master where type='table' and name='action_observers'").fetchone() else 0
            identity_maps = db.execute("select count(*) from action_entity_identity_map").fetchone()[0] if db.execute(
                "select 1 from sqlite_master where type='table' and name='action_entity_identity_map'").fetchone() else 0
            return {"action_columns": len(action_columns),
                    "stored_action_columns": sum(row[6] == 0 for row in action_columns),
                    "generated_action_columns": sum(row[6] != 0 for row in action_columns),
                    "index_count": len(indexes), "indexes": [dict(row) for row in indexes],
                    "action_type_count": len(type_counts), "action_type_rows": type_counts,
                    "action_receipts": receipts, "action_observers": observers,
                    "identity_map_rows": identity_maps}
    with sqlite3.connect(source) as db:
        source_counts = dict(db.execute("select record_type,count(*) from actions group by record_type order by record_type"))
    source_inventory, current_inventory = one(source), one(current)
    retired = {kind: source_counts.get(kind, 0) for kind in sorted(RETIRED_ACTION_TYPES)}
    return {"source": source_inventory, "current": current_inventory,
            "retired_source_action_types": retired,
            "retired_source_action_type_count": sum(bool(count) for count in retired.values()),
            "retired_source_action_row_count": sum(retired.values()),
            "action_index_delta_count": current_inventory["index_count"] - source_inventory["index_count"]}


def _measure(db_path: Path, *, vacuum: bool) -> dict[str, Any]:
    with sqlite3.connect(db_path) as db:
        if vacuum:
            db.execute("VACUUM")
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        pages = db.execute("pragma page_count").fetchone()[0]
        freelist = db.execute("pragma freelist_count").fetchone()[0]
        kinds = {name: kind for kind, name in db.execute(
            "select type,name from sqlite_master where type in ('table','index')"
        )}
        page_bytes: dict[str, int] = {}
        page_counts: dict[str, dict[str, int]] = {}
        page_bytes_by_type: dict[str, dict[str, int]] = {}
        for name, page_type, count, size in db.execute(
            "select name,pagetype,count(*),sum(pgsize) from dbstat group by name,pagetype"
        ):
            page_bytes[name] = page_bytes.get(name, 0) + size
            page_counts.setdefault(name, {})[page_type] = count
            page_bytes_by_type.setdefault(name, {})[page_type] = size
        table_pages = sum(sum(page_counts.get(name, {}).values()) for name in kinds if kinds[name] == "table")
        index_pages = sum(sum(page_counts.get(name, {}).values()) for name in kinds if kinds[name] == "index")
        table_bytes = {name: page_bytes.get(name, 0) for name, kind in kinds.items() if kind == "table"}
        index_bytes = {name: page_bytes.get(name, 0) for name, kind in kinds.items() if kind == "index"}
        table_page_types = {name: page_counts.get(name, {}) for name, kind in kinds.items() if kind == "table"}
        index_page_types = {name: page_counts.get(name, {}) for name, kind in kinds.items() if kind == "index"}
        table_page_bytes_by_type = {name: page_bytes_by_type.get(name, {}) for name, kind in kinds.items() if kind == "table"}
        index_page_bytes_by_type = {name: page_bytes_by_type.get(name, {}) for name, kind in kinds.items() if kind == "index"}
        technical = {name: table_bytes[name] for name in (
            "actions", "action_receipts", "action_observers", "action_entity_identity_map"
        ) if name in table_bytes}
        index_definitions = {name: sql for kind, name, sql in db.execute(
            "select type,name,sql from sqlite_master where type='index' order by name"
        )}
    return {"vacuumed": vacuum,
            "page_size": page_size,
            "table_pages": table_pages, "index_pages": index_pages,
            "freelist_pages": freelist, "page_count": pages,
            "table_bytes": table_bytes, "technical_table_bytes": technical,
            "table_page_types": table_page_types,
            "table_page_bytes_by_type": table_page_bytes_by_type,
            "index_bytes": index_bytes, "index_definitions": index_definitions,
            "index_page_types": index_page_types,
            "index_page_bytes_by_type": index_page_bytes_by_type,
            "file_bytes": db_path.stat().st_size}


def _module_path(root: Path, module: str) -> str:
    code = f"import {module};print({module}.__file__)"
    return subprocess.check_output([sys.executable, "-c", code], cwd=root, text=True).strip()


def _old_public_write(db_path: Path, old_root: Path, key: str, body: str) -> str:
    script = db_path.parent / ("old-write-" + hashlib.sha256(key.encode()).hexdigest()[:10] + ".py")
    script.write_text(
        "from pathlib import Path\n"
        "from app.store import AutoReplyStore\n"
        f"store=AutoReplyStore(Path({str(db_path)!r}))\n"
        f"item=store.prepare_outbound_postfix('dingtalk',{key!r},{body!r},'copy acceptance input')\n"
        "print(item.final_body)\n"
    )
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(old_root)
    run = subprocess.run([sys.executable, str(script)], cwd=old_root, env=env,
                         capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError("old-code public writer failed: " + run.stderr[-1000:])
    return run.stdout.strip()


def validate_copy(source: Path, workspace: Path, old_root: Path, new_root: Path) -> dict[str, Any]:
    """Run the reversible migration/capacity checks using only uniquely owned copies."""
    source, workspace = Path(source).resolve(), _owned(Path(workspace))
    old_root, new_root = Path(old_root).resolve(), Path(new_root).resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError(f"workspace must be a new empty directory: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    source_before = _sha256(source)
    old_copy = workspace / "cohort-old.sqlite3"
    backup_readonly(source, old_copy)
    with sqlite3.connect(old_copy) as db:
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise AssertionError("read-only SQLite backup failed quick_check")

    # All current-code imports and schema edits are confined to an owned copy.
    sys.path.insert(0, str(new_root))
    from app.store_schema_release import prepare_database_schema_release

    candidate = workspace / "cohort-current.sqlite3"
    with sqlite3.connect(old_copy) as src, sqlite3.connect(candidate) as dst:
        src.backup(dst)
    # Publish the candidate through the paired Store/Email release, which also
    # performs the exact current Store constructor readback.
    release = prepare_database_schema_release(new_root, candidate)
    if release is None:
        raise AssertionError("paired current schema release was not selected for Store19.12 source")
    release.publish()
    contract = release.contract
    target_marker = contract.get("target_version")

    # Create the new fact through the real current public writer after schema
    # selection, then verify its public readback before inverse.
    unique_key = "task6-copy-" + source_before[:16]
    body = "Action Entity copy validation " + source_before[:16]
    from app.store import AutoReplyStore
    store = AutoReplyStore(candidate)
    prepared_action = store.prepare_outbound_postfix("dingtalk", unique_key, body, "copy acceptance input")
    readback = store.get_outbound_postfix("dingtalk", unique_key)
    if readback is None or readback.final_body != prepared_action.final_body:
        raise AssertionError("current public Action writer/readback did not preserve the prepared body")
    expected_action_digest = hashlib.sha256(readback.final_body.encode()).hexdigest()
    current_modules = {module: _module_path(new_root, module) for module in ("app.store", "app.email_store")}
    if any(not Path(path).resolve().is_relative_to(new_root) for path in current_modules.values()):
        raise AssertionError("current public writer imported a module outside the selected new root")

    # Inverse operates only on the owned current copy; exact source contract is
    # captured by the real release contract and the unique write remains data.
    release.rollback()
    old_modules = {module: _module_path(old_root, module) for module in ("app.store", "app.email_store")}
    if any(not Path(path).resolve().is_relative_to(old_root) for path in old_modules.values()):
        raise AssertionError("old cold-write process imported a module outside the pinned old root")
    cold_key = unique_key + "-cold"
    cold_body = _old_public_write(candidate, old_root, cold_key, "old runtime " + source_before[:16])

    # Capacity baseline is the exact old-schema cohort after its cold write.
    # Clone it, then re-forward only the second copy.
    before_capacity = workspace / "before-inverse.sqlite3"
    with sqlite3.connect(candidate) as src, sqlite3.connect(before_capacity) as dst:
        src.backup(dst)
    reforward_release = prepare_database_schema_release(new_root, candidate)
    if reforward_release is None:
        raise AssertionError("paired re-forward release was not selected after inverse")
    reforward_release.publish()
    reforward = reforward_release.contract
    from app.action_entity_transfer import _capture_inverse_facts
    with sqlite3.connect(candidate) as db:
        current_facts = _capture_inverse_facts(db)["facts"]["outbound_postfixes"]
    carried_action = next((row for row in current_facts if row["delivery_key"] == unique_key), None)
    if carried_action is None or hashlib.sha256(carried_action["final_body"].encode()).hexdigest() != expected_action_digest:
        raise AssertionError("pre-migration public Action fact was lost during inverse/reforward")
    carried_cold = next((row for row in current_facts if row["delivery_key"] == cold_key), None)
    if carried_cold is None or carried_cold["final_body"] != cold_body:
        raise AssertionError("old cold-write fact was lost during re-forward")
    check_new_facts_survive_inverse(
        {"new_action": expected_action_digest,
         "old_cold_write": hashlib.sha256(cold_body.encode()).hexdigest()},
        {"new_action": hashlib.sha256(carried_action["final_body"].encode()).hexdigest(),
         "old_cold_write": hashlib.sha256(carried_cold["final_body"].encode()).hexdigest()},
    )
    source_after = _sha256(source)
    if source_after != source_before:
        raise AssertionError("source backup bytes changed during copy validation")

    # Independently inverse a second current target copy and compare every
    # source-shaped row with the old-schema cohort. This proves population
    # equality before measuring storage.
    inverse_check = workspace / "capacity-inverse.sqlite3"
    with sqlite3.connect(candidate) as src, sqlite3.connect(inverse_check) as dst:
        src.backup(dst)
    inverse_contract = {**reforward, "database": str(inverse_check.resolve())}
    from app.action_entity_schema_release import invert_action_entity_release
    invert_action_entity_release(inverse_check, inverse_contract)
    cohort_proof = _compare_source_databases(before_capacity, inverse_check)

    # Both capacity cohorts are independently VACUUM-packed from the exact
    # source-shaped population proved above.
    after_capacity = workspace / "after-reforward.sqlite3"
    with sqlite3.connect(candidate) as src, sqlite3.connect(after_capacity) as dst:
        src.backup(dst)
    baseline_measure = _measure(before_capacity, vacuum=True)
    current_measure = _measure(after_capacity, vacuum=True)
    baseline_measure["population_sha256"] = cohort_proof["population_sha256"]
    current_measure["population_sha256"] = cohort_proof["verified_population_sha256"]
    assert_capacity_pair(baseline_measure, current_measure)
    removed_indexes = sorted(set(baseline_measure["index_definitions"]) - set(current_measure["index_definitions"]))
    added_indexes = sorted(set(current_measure["index_definitions"]) - set(baseline_measure["index_definitions"]))
    index_delta = {"removed": [{"name": name, "sql": baseline_measure["index_definitions"][name],
                                "bytes": baseline_measure["index_bytes"].get(name, 0)} for name in removed_indexes],
                   "added": [{"name": name, "sql": current_measure["index_definitions"][name],
                              "bytes": current_measure["index_bytes"].get(name, 0)} for name in added_indexes]}
    capacity = {"baseline": baseline_measure, "current": current_measure,
                "delta_bytes": current_measure["file_bytes"] - baseline_measure["file_bytes"],
                "delta_table_pages": current_measure["table_pages"] - baseline_measure["table_pages"],
                "delta_index_pages": current_measure["index_pages"] - baseline_measure["index_pages"],
                "index_changes": index_delta,
                "same_population": cohort_proof}
    inventory = _schema_inventory(before_capacity, after_capacity)
    report = {
        "source_sha256": source_before,
        "source_unchanged": source_after == source_before,
        "workspace": str(workspace),
        "current_store_version": target_marker,
        "old_modules": old_modules,
        "current_modules": current_modules,
        "action_digest": expected_action_digest,
        "cold_write_body_sha256": hashlib.sha256(carried_cold["final_body"].encode()).hexdigest(),
        "new_facts_survived_inverse_and_reforward": True,
        "schema_inventory": inventory,
        "capacity": capacity,
        "native": {"status": "not_run", "reason": "native lifecycle requires a separately prepared approved business case"},
        "ui": {"status": "not_run", "reason": "UI validation is partitioned to an owned server/copy acceptance"},
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--new-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = validate_copy(args.source, args.workspace, args.old_root, args.new_root)
    except Exception as exc:
        print(json.dumps({"status": "blocked_or_failed", "error_type": type(exc).__name__,
                          "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
