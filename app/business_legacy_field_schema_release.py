"""Explicit 19.8/19.9/19.10 -> 19.11 chain; load only the final constructor."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app import action_storage as actions
from app.business_legacy_fields import (
    current_action_objects, current_action_variants, current_business_schema_is_current,
)
from app.business_legacy_field_schema import migrate_legacy_business_fields, restore_legacy_business_fields
from app.business_legacy_owner_storage import _business_declaration
from app.action_reply_storage import _source_declaration
from app.logical_run_schema_release import (
    PREVIOUS_STORE_VERSIONS as PRIOR_VERSIONS, PREVIOUS_EMAIL_PAIRS as PRIOR_PAIRS,
    publication_action_schema_is_current as prior_actions,
    publication_tasks_are_current as prior_tasks,
)
from app.logical_run_current_schema import current_run11_schema_is_current as logical_runs
from app.runs11_current_schema import current_run11_schema_is_current as old_runs
from app.logical_run_schema import migrate_logical_runs, restore_logical_runs
from app.prepared_action_schema import migrate_prepared_actions, restore_prepared_actions
from app.result_schema_release import _markers, _foreign_keys
from app.state_storage import update_state_rows
from app.task_lifecycle_current_schema import task20_schema_is_current

SOURCE_STORE_VERSIONS = ("2026-10-19.8", "2026-10-19.9", "2026-10-19.10")
TARGET_STORE_VERSION = "2026-10-19.11"
TARGET_EMAIL_VERSION = EMAIL_VERSION = 53
PREVIOUS_STORE_VERSIONS = (*PRIOR_VERSIONS, SOURCE_STORE_VERSIONS[-1])
PREVIOUS_EMAIL_PAIRS = (*PRIOR_PAIRS, (SOURCE_STORE_VERSIONS[-1], EMAIL_VERSION))
VERSION_KEY = "store_schema_version"


def publication_action_schema_is_current(db, version):
    if version == TARGET_STORE_VERSION:
        from app.final_action_schema import _provenance_objects
        from app.email_current_schema import current_canonical_email_parent_is_current
        return (actions._objects(db, "actions") == current_action_objects(actions._reply_variant_for(db))
                and actions._objects(db, "provenance") == _provenance_objects()
                and not actions._objects(db, "business_source_documents")
                and current_canonical_email_parent_is_current(db, metadata_owner=True))
    return prior_actions(db, version)


def publication_tasks_are_current(db, version):
    return task20_schema_is_current(db) if version == TARGET_STORE_VERSION else prior_tasks(db, version)


def require_target_pair(path):
    from app.action_email_schema_release import require_current_email_pair
    require_current_email_pair(path, store_version=TARGET_STORE_VERSION, email_version=EMAIL_VERSION,
        selection_references=True, result_references=True, metadata_owner=True, execution_owner=True,
        sent_result_references=True, shared_lifecycle=True, specialized_claims=True,
        task_lifecycle=True, postfix_owner=True, logical_owner=True,
        expected_action_variants=current_action_variants())


@dataclass
class BusinessLegacyFieldSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple
    email_versions: tuple
    source_schema: dict
    source_fk: tuple

    def _verify(self, db, *, target):
        marker, versions = _markers(db)
        version = TARGET_STORE_VERSION if target else self.original_version[0]
        if (marker is None or marker[0] != version or (not target and marker != self.original_version)
                or versions != self.email_versions or not versions or versions[-1][0] != EMAIL_VERSION):
            raise ValueError("legacy field publication marker mismatch")
        business_ok = (current_business_schema_is_current(db) if target else
                       _source_declaration(db, "business") == _business_declaration(target=True))
        runs_ok = logical_runs(db) if version in ("2026-10-19.10", TARGET_STORE_VERSION) else old_runs(db)
        if (not business_ok or not runs_ok or actions._reply_variant_for(db) != self.source_schema["reply_variant"]
                or not publication_action_schema_is_current(db, version)
                or not publication_tasks_are_current(db, version)):
            raise ValueError("legacy field publication declaration mismatch")
        if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("legacy field publication integrity/FK mismatch")

    def publish(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                self._verify(db, target=False)
                source = self.original_version[0]
                if source == "2026-10-19.8":
                    if migrate_prepared_actions(db) != self.source_schema:
                        raise ValueError("prepared inverse declaration variant changed")
                if source in ("2026-10-19.8", "2026-10-19.9"):
                    if migrate_logical_runs(db) != {}:
                        raise ValueError("logical Run inverse declaration variant changed")
                if migrate_legacy_business_fields(db) != self.source_schema:
                    raise ValueError("legacy field inverse declaration variant changed")
                update_state_rows(db, "service_state", {
                    "value": TARGET_STORE_VERSION, "updated_at": datetime.now(timezone.utc).isoformat(),
                }, where_sql="key=?", parameters=(VERSION_KEY,))
                self._verify(db, target=True)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        from app.store_schema_release import _migrate_store
        _migrate_store(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self):
        require_target_pair(self.database_path)
        with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("begin")
            self._verify(db, target=True)

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                if _markers(db)[0] == self.original_version:
                    self._verify(db, target=False)
                else:
                    self._verify(db, target=True)
                    restore_legacy_business_fields(db, self.source_schema)
                    if self.original_version[0] in ("2026-10-19.8", "2026-10-19.9"):
                        restore_logical_runs(db, {})
                    if self.original_version[0] == "2026-10-19.8":
                        restore_prepared_actions(db, self.source_schema)
                    update_state_rows(db, "service_state", {
                        "value": self.original_version[0], "updated_at": self.original_version[1],
                    }, where_sql="key=?", parameters=(VERSION_KEY,))
                    self._verify(db, target=False)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def prepare_legacy_business_field_schema_release(root, path):
    from app.action_email_schema_release import require_current_email_pair
    root, path = Path(root).resolve(), Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        marker, versions = _markers(db)
        if marker is None or marker[0] not in SOURCE_STORE_VERSIONS:
            raise ValueError("legacy field release requires exact Store19.8/19.9/19.10 and Email53")
        release = BusinessLegacyFieldSchemaRelease(root, path, marker, versions,
            {"reply_variant": actions._reply_variant_for(db)}, _foreign_keys(db))
        release._verify(db, target=False)
    require_current_email_pair(path, store_version=marker[0], email_version=EMAIL_VERSION,
        selection_references=True, result_references=True, metadata_owner=True, execution_owner=True,
        sent_result_references=True, shared_lifecycle=True, specialized_claims=True, task_lifecycle=True,
        postfix_owner=marker[0] in ("2026-10-19.9", "2026-10-19.10"),
        logical_owner=marker[0] == "2026-10-19.10")
    return release
