"""Store19.7/Email53 -> Store19.8/Email53 through the paired coordinator."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app import action_storage as actions
from app.specialized_action_schema_release import (
    PREVIOUS_STORE_VERSIONS as SOURCE_PREVIOUS_VERSIONS,
    PREVIOUS_EMAIL_PAIRS as SOURCE_PREVIOUS_PAIRS,
    publication_action_schema_is_current as source_action_schema_is_current,
)
from app.result_schema_release import _markers, _foreign_keys
from app.task20_current_schema import task20_schema_is_current as source_tasks_are_current
from app.task20_current_schema import _current_objects
from app.task_lifecycle_current_schema import task20_schema_is_current, task_lifecycle_objects
from app.task_lifecycle_schema import migrate_task_lifecycle, restore_task_lifecycle
from app.state_storage import update_state_rows
from app.run_storage import _schema_objects

SOURCE_STORE_VERSION = "2026-10-19.7"
TARGET_STORE_VERSION = "2026-10-19.8"
TARGET_EMAIL_VERSION = EMAIL_VERSION = 53
PREVIOUS_STORE_VERSIONS = (*SOURCE_PREVIOUS_VERSIONS, SOURCE_STORE_VERSION)
PREVIOUS_EMAIL_PAIRS = (*SOURCE_PREVIOUS_PAIRS, (SOURCE_STORE_VERSION, EMAIL_VERSION))
VERSION_KEY = "store_schema_version"


def publication_action_schema_is_current(db, version):
    return source_action_schema_is_current(
        db, SOURCE_STORE_VERSION if version == TARGET_STORE_VERSION else version,
    )


def publication_tasks_are_current(db, version):
    return (task20_schema_is_current(db) if version == TARGET_STORE_VERSION
            else version in PREVIOUS_STORE_VERSIONS and source_tasks_are_current(db))


@dataclass
class TaskLifecycleSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple
    email_versions: tuple
    source_schema: dict
    source_fk: tuple
    reply_variant: str

    def _verify(self, db, *, target):
        marker, versions = _markers(db)
        version = TARGET_STORE_VERSION if target else SOURCE_STORE_VERSION
        if marker is None or marker[0] != version or (not target and marker != self.original_version):
            raise ValueError("Task lifecycle publication Store marker mismatch")
        if versions != self.email_versions or not versions or versions[-1][0] != EMAIL_VERSION:
            raise ValueError("Task lifecycle publication Email markers changed")
        if (actions._reply_variant_for(db) != self.reply_variant
                or not publication_action_schema_is_current(db, version)
                or not publication_tasks_are_current(db, version)
                or _schema_objects(db, "tasks") != (
                    task_lifecycle_objects() if target else _current_objects()["tasks"])):
            raise ValueError("Task lifecycle publication declaration mismatch")
        if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("Task lifecycle publication integrity/FK mismatch")

    def publish(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                self._verify(db, target=False)
                metadata = migrate_task_lifecycle(db)
                if metadata != self.source_schema:
                    raise ValueError("Task lifecycle source declarations changed")
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
        from app.action_email_schema_release import require_current_email_pair
        require_current_email_pair(self.database_path, store_version=TARGET_STORE_VERSION,
            email_version=EMAIL_VERSION, selection_references=True, result_references=True,
            metadata_owner=True, execution_owner=True, sent_result_references=True,
            shared_lifecycle=True, specialized_claims=True, task_lifecycle=True)
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
                    restore_task_lifecycle(db, self.source_schema)
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


def prepare_task_lifecycle_schema_release(root, path):
    from app.action_email_schema_release import require_current_email_pair
    root, path = Path(root).resolve(), Path(path).resolve()
    require_current_email_pair(path, store_version=SOURCE_STORE_VERSION, email_version=EMAIL_VERSION,
        selection_references=True, result_references=True, metadata_owner=True, execution_owner=True,
        sent_result_references=True, shared_lifecycle=True, specialized_claims=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        marker, versions = _markers(db)
        if marker is None or marker[0] != SOURCE_STORE_VERSION:
            raise ValueError("Task lifecycle publication requires exact Store19.7/Email53 source")
        release = TaskLifecycleSchemaRelease(root, path, marker, versions,
            {"tasks": _schema_objects(db, "tasks")}, _foreign_keys(db), actions._reply_variant_for(db))
        release._verify(db, target=False)
        return release
