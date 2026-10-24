"""Paired Store 2026-10-21.1 to native Task/Run publication.

This explicit deploy-window converter captures Task, Run, and adopted Action
metadata before mutating any owner. Runtime construction never dispatches on
these predecessor declarations.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import tempfile

from app.predecessor_database_schema import PREDECESSOR_STORE_VERSION

TARGET_STORE_VERSION = "2026-10-23.1"
EMAIL_VERSION = 53
VERSION_KEY = "store_schema_version"
SOURCE_SCHEMA_SHA256 = "920b7eedb6138439ab94d4fbf109e60258ce8842c212d3a2ee3eec56fb4ca290"


def _digest(objects):
    return hashlib.sha256(repr(objects).encode("utf-8")).hexdigest()


def _source_marker(db):
    from app.state_definitions import state_projection
    return db.execute(
        f"select value,updated_at from ({state_projection('service_state')}) where key=?",
        (VERSION_KEY,),
    ).fetchone()


def _require_source(db):
    from app.current_schema_manifest import schema_objects
    from app.action_entity_schema_release import _markers
    marker = _source_marker(db)
    email_marker, email_versions = _markers(db)
    if (marker is None or marker[0] != PREDECESSOR_STORE_VERSION
            or email_marker is None or not email_versions
            or email_versions[-1][0] != EMAIL_VERSION
            or _digest(schema_objects(db)) != SOURCE_SCHEMA_SHA256):
        raise ValueError("unsupported Store 2026-10-21.1 / Email53 source schema")
    return marker


def _run_handoff(db, source_metadata):
    from app.run_entity_schema import run_handoff_context, run_handoff_digest
    owners = ("runs", "runtime_attempts", "run_failures", "schedule_occurrences",
              "workbench_events", "codex_session_search_index",
              "email_classifier_runtime_samples", "setup_wizard_events")
    source_streams = {
        "runs": "runs", "runtime_attempts": "runs:agent_runtime_attempts",
        "run_failures": "runs:agent_run_state_events",
        "schedule_occurrences": "runs:scheduled_task_runs",
        "workbench_events": "runs:workbench_events",
        "codex_session_search_index": "runs:codex_session_search_index",
        "email_classifier_runtime_samples": "runs:email_classifier_runtime_samples",
        "setup_wizard_events": "runs:setup_wizard_events",
    }
    source_water = {name: seq for _, name, seq in source_metadata["sequence_rows"]}
    highwaters = {owner: source_water.get(source_streams[owner]) for owner in owners}
    edges = []
    context = run_handoff_context(source_metadata)
    return {
        "version": 3, "source_context": context, "identity_edges": edges,
        "runtime_attempt_parent_edges": [],
        "runtime_attempt_orphan_workload_edges": [],
        "owner_highwaters": highwaters,
        "digest": run_handoff_digest(context, edges, [], [], highwaters),
    }


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


def _version_write(db, value, updated_at):
    from app.state_storage import upsert_state
    upsert_state(db, "service_state", {"key": VERSION_KEY, "value": value},
                 {"value": "json_quote(excluded.value)",
                  "updated_at": "json_quote(?)"},
                 update_parameters=(updated_at,))


def _portable(value):
    """Encode the SQLite capture/identity metadata as strict JSON."""
    if type(value) is tuple:
        return {"$task_run_type": "tuple", "items": [_portable(item) for item in value]}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$task_run_type": "bytes", "hex": bytes(value).hex()}
    if type(value) is float:
        return {"$task_run_type": "float", "hex": value.hex()}
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise TypeError("Task/Run recovery metadata requires string keys")
        return {key: _portable(item) for key, item in value.items()}
    if type(value) is list:
        return [_portable(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise TypeError(f"unsupported Task/Run recovery metadata value: {type(value).__name__}")


def _restore_portable(value):
    if type(value) is list:
        return [_restore_portable(item) for item in value]
    if type(value) is dict:
        tag = value.get("$task_run_type")
        if tag == "tuple" and set(value) == {"$task_run_type", "items"}:
            return tuple(_restore_portable(item) for item in value["items"])
        if tag == "bytes" and set(value) == {"$task_run_type", "hex"}:
            return bytes.fromhex(value["hex"])
        if tag == "float" and set(value) == {"$task_run_type", "hex"}:
            return float.fromhex(value["hex"])
        if tag is not None:
            raise ValueError("unknown or malformed Task/Run recovery metadata tag")
        return {key: _restore_portable(item) for key, item in value.items()}
    if value is None or type(value) in (str, int, bool):
        return value
    raise TypeError(f"unsupported Task/Run recovery JSON value: {type(value).__name__}")


def _remove_handoff(path: Path) -> None:
    path.unlink(missing_ok=True)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_source_identity_handoff(path: Path, inverse_identity: dict,
                                   source_context: dict) -> None:
    """Retain the sparse Task identities needed by the next source forward."""
    path = Path(path).resolve()
    payload = {
        "format": "ceo-task-run-source-identity-v1",
        "inverse_identity": inverse_identity,
        "source_context": source_context,
    }
    encoded = json.dumps(_portable(payload), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class TaskRunEntitySchemaRelease:
    database_path: Path
    original_version: tuple[str, str]
    task_source: dict
    run_source: dict
    adoption_source: dict
    source_fk: tuple[tuple, ...]
    task_forward: dict | None = None
    run_forward: dict | None = None
    adoption_forward: dict | None = None
    recovery_handoff_path: Path | None = None
    source_inverse_identity: dict | None = None
    source_context: dict | None = None

    def _handoff_payload(self) -> dict:
        if (self.task_forward is None or self.run_forward is None
                or self.adoption_forward is None):
            raise ValueError("Task/Run inverse handoff requires completed forward metadata")
        return {
            "format": "ceo-task-run-inverse-v1",
            "original_version": self.original_version,
            "source_fk": self.source_fk,
            "task_source": self.task_source,
            "run_source": self.run_source,
            "adoption_source": self.adoption_source,
            "task_forward": self.task_forward,
            "run_forward": self.run_forward,
            "adoption_forward": self.adoption_forward,
        }

    def write_inverse_handoff(self, path: Path) -> Path:
        """Durably export the exact forward metadata for a later inverse tool."""
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(_portable(self._handoff_payload()), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path

    @classmethod
    def from_inverse_handoff(cls, database_path: Path, handoff_path: Path):
        payload = _restore_portable(json.loads(Path(handoff_path).read_text(encoding="utf-8")))
        if type(payload) is not dict or payload.get("format") != "ceo-task-run-inverse-v1":
            raise ValueError("unsupported Task/Run inverse handoff format")
        required = {"format", "original_version", "source_fk", "task_source", "run_source",
                    "adoption_source", "task_forward", "run_forward", "adoption_forward"}
        if set(payload) != required:
            raise ValueError("Task/Run inverse handoff fields differ from the supported format")
        return cls(Path(database_path).resolve(), payload["original_version"],
                   payload["task_source"], payload["run_source"], payload["adoption_source"],
                   payload["source_fk"], payload["task_forward"], payload["run_forward"],
                   payload["adoption_forward"], Path(handoff_path).resolve())

    def publish(self):
        from app.task_entity_schema import capture_task_entities, migrate_task_entities
        from app.run_entity_schema import capture_run_entities, migrate_run_entities
        from app.run_adoption_schema import capture_run_adoptions, migrate_run_adoptions
        path = self.database_path
        handoff_written = False
        previous_metadata = (self.task_source, self.run_source, self.adoption_source,
                             self.task_forward, self.run_forward, self.adoption_forward,
                             self.source_inverse_identity, self.source_context)
        with closing(sqlite3.connect(path)) as db:
            db.row_factory = sqlite3.Row
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                marker = _require_source(db)
                if tuple(marker) != tuple(self.original_version):
                    raise ValueError("paired Task/Run source marker changed after preparation")
                # Capture all three live sources before the first source table
                # is replaced, in the same transaction as the conversion.
                adoption_source = capture_run_adoptions(db)
                task_source = capture_task_entities(db)
                run_source = capture_run_entities(db)
                task_forward = migrate_task_entities(
                    db, task_source, source_context=self.source_context,
                    inverse_identity=self.source_inverse_identity,
                )
                run_forward = migrate_run_entities(
                    db, run_source, task_metadata=task_forward,
                    run_handoff=_run_handoff(db, run_source),
                )
                adoption_forward = migrate_run_adoptions(
                    db, adoption_source, task_metadata=task_forward,
                    run_metadata=run_forward,
                )
                from app.current_schema_manifest import schema_is_current
                if not schema_is_current(db):
                    raise ValueError("paired Task/Run target schema differs from current manifest")
                target_marker_time = db.execute("select current_timestamp").fetchone()[0]
                _version_write(db, TARGET_STORE_VERSION, target_marker_time)
                if db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("paired Task/Run publication integrity check failed")
                self.task_source, self.run_source, self.adoption_source = (
                    task_source, run_source, adoption_source)
                self.source_inverse_identity = None
                self.source_context = None
                self.task_forward, self.run_forward, self.adoption_forward = (
                    task_forward, run_forward, adoption_forward)
                if self.recovery_handoff_path is not None:
                    self.write_inverse_handoff(self.recovery_handoff_path)
                    handoff_written = True
                db.commit()
            except BaseException:
                db.rollback()
                if handoff_written and self.recovery_handoff_path is not None:
                    _remove_handoff(self.recovery_handoff_path)
                (self.task_source, self.run_source, self.adoption_source,
                 self.task_forward, self.run_forward, self.adoption_forward,
                 self.source_inverse_identity, self.source_context) = previous_metadata
                raise
        self.verify_loaded()

    def verify_loaded(self):
        from app.current_schema_manifest import require_current_pair
        require_current_pair(self.database_path)

    def finalize(self):
        self.verify_loaded()

    def rollback(self):
        from app.task_entity_schema import restore_task_entities
        from app.run_entity_schema import restore_run_entities
        from app.run_adoption_inverse import restore_run_adoptions
        if self.task_forward is None or self.run_forward is None or self.adoption_forward is None:
            # The repository updater invokes rollback even when preparation or a
            # transactionally rolled-back publish failed. Accept only the exact
            # unchanged frozen source; any other state still requires inverse metadata.
            with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as source_db:
                source_db.execute("begin")
                marker = _require_source(source_db)
                if tuple(marker) != tuple(self.original_version):
                    raise ValueError("paired Task/Run inverse lacks forward capture metadata")
                if (_foreign_keys(source_db) != self.source_fk
                        or source_db.execute("pragma quick_check").fetchone()[0] != "ok"):
                    raise ValueError("paired Task/Run unchanged source integrity/FK mismatch")
            return
        with closing(sqlite3.connect(self.database_path)) as db:
            db.row_factory = sqlite3.Row
            from app.task8_storage import register_task8_functions
            register_task8_functions(db)
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                marker = _source_marker(db)
                if marker is None or marker[0] != TARGET_STORE_VERSION:
                    raise ValueError("paired Task/Run inverse source version mismatch")
                from app.current_schema_manifest import schema_is_current
                from app.action_entity_schema_release import _markers
                email_marker, email_versions = _markers(db)
                if (not schema_is_current(db) or email_marker is None or not email_versions
                        or email_versions[-1][0] != EMAIL_VERSION):
                    raise ValueError("paired Task/Run inverse target schema mismatch")
                task_inverse = restore_task_entities(db, self.task_source, self.task_forward)
                run_inverse = restore_run_entities(
                    db, self.run_source, self.run_forward,
                    task_metadata=self.task_forward,
                    task_inverse_metadata=task_inverse,
                )
                restore_run_adoptions(
                    db, self.adoption_source, self.adoption_forward,
                    task_metadata=self.task_forward,
                    task_inverse_metadata=task_inverse,
                    run_inverse_metadata=run_inverse,
                )
                _version_write(db, self.original_version[0], self.original_version[1])
                _require_source(db)
                if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("paired Task/Run inverse integrity/FK mismatch")
                if self.recovery_handoff_path is not None:
                    _write_source_identity_handoff(
                        self.recovery_handoff_path, task_inverse["inverse_identity"],
                        task_inverse["source_context"],
                    )
                db.commit()
            except BaseException:
                db.rollback()
                if self.recovery_handoff_path is not None:
                    self.write_inverse_handoff(self.recovery_handoff_path)
                raise


def prepare_task_run_entity_release(path: Path, *, recovery_source_path: Path | None = None):
    path = Path(path).resolve()
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        marker = _require_source(db)
        handoff_path = path.with_name(path.name + ".task-run-inverse.json")
        source_inverse_identity = None
        source_context = None
        if handoff_path.is_file():
            payload = _restore_portable(json.loads(handoff_path.read_text(encoding="utf-8")))
            if (type(payload) is not dict
                    or payload.get("format") != "ceo-task-run-source-identity-v1"
                    or set(payload) != {"format", "inverse_identity", "source_context"}):
                raise ValueError("paired source identity continuation has an unsupported format")
            source_inverse_identity = payload["inverse_identity"]
            source_context = payload["source_context"]
        return TaskRunEntitySchemaRelease(
            path, marker, {}, {}, {}, _foreign_keys(db),
            recovery_handoff_path=handoff_path,
            source_inverse_identity=source_inverse_identity,
            source_context=source_context)
