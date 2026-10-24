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
import sqlite3

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

    def publish(self):
        from app.task_entity_schema import capture_task_entities, migrate_task_entities
        from app.run_entity_schema import capture_run_entities, migrate_run_entities
        from app.run_adoption_schema import capture_run_adoptions, migrate_run_adoptions
        path = self.database_path
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
                task_forward = migrate_task_entities(db, task_source)
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
                db.commit()
                self.task_source, self.run_source, self.adoption_source = (
                    task_source, run_source, adoption_source)
                self.task_forward, self.run_forward, self.adoption_forward = (
                    task_forward, run_forward, adoption_forward)
            except BaseException:
                db.rollback()
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
                db.commit()
            except BaseException:
                db.rollback()
                raise


def prepare_task_run_entity_release(path: Path):
    path = Path(path).resolve()
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        marker = _require_source(db)
        return TaskRunEntitySchemaRelease(
            path, marker, {}, {}, {}, _foreign_keys(db))
