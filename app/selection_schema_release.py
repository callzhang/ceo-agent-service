"""Store 14.1 -> 19.1 selection references with a current-fact inverse.

Email52 declarations and all of its migration markers remain unchanged. The
existing deploy coordinator owns stop, backup, publication and failed-start
rollback; this release retains only source markers, variant and FK findings.
"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app import action_storage as actions
from app.model_action_schema import (
    migrate_selection_references, restore_selection_references, selection_action_objects,
)
from app.state_definitions import state_projection
from app.state_storage import update_state_rows


SOURCE_STORE_VERSION = "2026-10-14.1"
TARGET_STORE_VERSION = "2026-10-19.1"
EMAIL_VERSION = 52
VERSION_KEY = "store_schema_version"


def publication_action_schema_is_current(db, version):
    """Choose only the two explicitly declared sides of this publication."""
    from app.final_action_schema import _provenance_objects
    if version not in (SOURCE_STORE_VERSION, TARGET_STORE_VERSION):
        return False
    return (actions._objects(db, "actions") == selection_action_objects(
                actions._reply_variant_for(db), selection_references=version == TARGET_STORE_VERSION)
            and actions._objects(db, "provenance") == _provenance_objects()
            and not actions._objects(db, "business_source_documents"))


def _markers(db):
    marker = db.execute(f"select value,updated_at from ({state_projection('service_state')}) "
                        "where key=?", (VERSION_KEY,)).fetchone()
    versions = tuple(tuple(row) for row in db.execute(
        "select version,applied_at from main.email_schema_migrations order by version"))
    return tuple(marker) if marker is not None else None, versions


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


@dataclass
class SelectionSchemaRelease:
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
            raise ValueError("selection publication Store marker mismatch")
        if versions != self.email_versions or not versions or versions[-1][0] != EMAIL_VERSION:
            raise ValueError("selection publication Email markers changed")
        if (actions._reply_variant_for(db) != self.source_schema["reply_variant"]
                or not publication_action_schema_is_current(db, version)):
            raise ValueError("selection publication declaration mismatch")
        if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("selection publication integrity/FK mismatch")

    def publish(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                self._verify(db, target=False)
                metadata = migrate_selection_references(db)
                if metadata != self.source_schema:
                    raise ValueError("selection publication inverse variant changed")
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
                                   email_version=EMAIL_VERSION, selection_references=True)
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
                    restore_selection_references(db, self.source_schema)
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


def prepare_selection_schema_release(root, path):
    from app.action_email_schema_release import require_current_email_pair

    root, path = Path(root).resolve(), Path(path).resolve()
    require_current_email_pair(path, store_version=SOURCE_STORE_VERSION, email_version=EMAIL_VERSION)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        marker, versions = _markers(db)
        if marker is None or marker[0] != SOURCE_STORE_VERSION:
            raise ValueError("selection publication requires exact Store14.1/Email52 source")
        release = SelectionSchemaRelease(root, path, marker, versions,
            {"reply_variant": actions._reply_variant_for(db)}, _foreign_keys(db))
        release._verify(db, target=False)
        return release
