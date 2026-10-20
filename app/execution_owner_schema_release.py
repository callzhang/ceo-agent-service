"""Store19.3/Email53 -> Store19.4/Email53 with current-fact rollback.

The existing deploy coordinator owns quiet wait, backup, publication and restart.
Only the exact source marker, declaration variant and FK findings are retained.
"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app import action_storage as actions
from app.execution_owner_schema import (
    migrate_execution_owner, restore_execution_owner, execution_action_objects,
)
from app.result_schema_release import _markers, _foreign_keys
from app.state_storage import update_state_rows


SOURCE_STORE_VERSION = "2026-10-19.3"
TARGET_STORE_VERSION = "2026-10-19.4"
PREVIOUS_STORE_VERSIONS = ("2026-10-14.1", "2026-10-19.1", "2026-10-19.2", SOURCE_STORE_VERSION)
TARGET_EMAIL_VERSION = EMAIL_VERSION = 53
PREVIOUS_EMAIL_PAIRS = (("2026-10-14.1", 52), ("2026-10-19.1", 52),
                        ("2026-10-19.2", 52), (SOURCE_STORE_VERSION, 53))
VERSION_KEY = "store_schema_version"


def publication_action_schema_is_current(db, version):
    if version in PREVIOUS_STORE_VERSIONS:
        from app.email_metadata_schema_release import publication_action_schema_is_current as previous
        return previous(db, version)
    from app.final_action_schema import _provenance_objects
    from app.email_current_schema import current_canonical_email_parent_is_current
    return (version == TARGET_STORE_VERSION
            and actions._objects(db, 'actions') == execution_action_objects(actions._reply_variant_for(db))
            and actions._objects(db, 'provenance') == _provenance_objects()
            and not actions._objects(db, 'business_source_documents')
            and current_canonical_email_parent_is_current(db, metadata_owner=True))


@dataclass
class ExecutionOwnerSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple
    email_versions: tuple
    source_schema: dict
    source_fk: tuple

    def _verify(self, db, *, target):
        marker, versions = _markers(db)
        version = TARGET_STORE_VERSION if target else SOURCE_STORE_VERSION
        if marker is None or marker[0] != version or (not target and marker != self.original_version):
            raise ValueError("execution-owner publication Store marker mismatch")
        if versions != self.email_versions or not versions or versions[-1][0] != EMAIL_VERSION:
            raise ValueError("execution-owner publication Email markers changed")
        if (actions._reply_variant_for(db) != self.source_schema["reply_variant"]
                or not publication_action_schema_is_current(db, version)):
            raise ValueError("execution-owner publication declaration mismatch")
        if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("execution-owner publication integrity/FK mismatch")

    def publish(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                self._verify(db, target=False)
                metadata = migrate_execution_owner(db)
                if metadata != self.source_schema:
                    raise ValueError("execution-owner publication inverse variant changed")
                update_state_rows(db, "service_state", {
                    "value": TARGET_STORE_VERSION, "updated_at": datetime.now(timezone.utc).isoformat(),
                }, where_sql="key=?", parameters=(VERSION_KEY,))
                self._verify(db, target=True)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        # Use the existing target-root cold opener after the atomic conversion.
        from app.store_schema_release import _migrate_store
        _migrate_store(self.root, self.database_path)
        self.verify_loaded()

    def verify_loaded(self):
        from app.action_email_schema_release import require_current_email_pair

        require_current_email_pair(self.database_path, store_version=TARGET_STORE_VERSION,
                                   email_version=EMAIL_VERSION, selection_references=True, result_references=True, metadata_owner=True, execution_owner=True)
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
                    restore_execution_owner(db, self.source_schema)
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


def prepare_execution_owner_schema_release(root, path):
    from app.action_email_schema_release import require_current_email_pair

    root, path = Path(root).resolve(), Path(path).resolve()
    require_current_email_pair(path, store_version=SOURCE_STORE_VERSION, email_version=EMAIL_VERSION, selection_references=True, result_references=True, metadata_owner=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        marker, versions = _markers(db)
        if marker is None or marker[0] != SOURCE_STORE_VERSION:
            raise ValueError("execution-owner publication requires exact Store19.3/Email53 source")
        release = ExecutionOwnerSchemaRelease(root, path, marker, versions,
            {"reply_variant": actions._reply_variant_for(db)}, _foreign_keys(db))
        release._verify(db, target=False)
        return release
