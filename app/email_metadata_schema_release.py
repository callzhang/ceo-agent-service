"""Paired Store19.2/Email52 -> Store19.3/Email53 publication of metadata ownership."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app import action_storage as actions
from app.email_metadata_schema import capture_email_metadata_source, migrate_email_metadata, restore_email_metadata
from app.result_schema_release import _markers, _foreign_keys
from app.state_storage import update_state_rows

SOURCE_STORE_VERSION = "2026-10-19.2"
TARGET_STORE_VERSION = "2026-10-19.3"
SOURCE_EMAIL_VERSION = 52
TARGET_EMAIL_VERSION = 53
PREVIOUS_STORE_VERSIONS = ("2026-10-14.1", "2026-10-19.1", SOURCE_STORE_VERSION)
VERSION_KEY = "store_schema_version"


def publication_action_schema_is_current(db, version):
    from app.result_schema_release import publication_action_schema_is_current as previous
    from app.email_current_schema import current_canonical_email_parent_is_current

    if version in PREVIOUS_STORE_VERSIONS:
        return previous(db, version)
    return (version == TARGET_STORE_VERSION
            and previous(db, SOURCE_STORE_VERSION)
            and current_canonical_email_parent_is_current(db, metadata_owner=True))


@dataclass
class EmailMetadataSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple
    email_versions: tuple
    source_schema: dict
    source_fk: tuple

    def _verify(self, db, *, target):
        from app.email_current_schema import current_canonical_email_parent_is_current

        marker, versions = _markers(db)
        version = TARGET_STORE_VERSION if target else SOURCE_STORE_VERSION
        if marker is None or marker[0] != version or (not target and marker != self.original_version):
            raise ValueError("Email metadata Store marker mismatch")
        if target:
            valid_versions = len(versions) == len(self.email_versions) + 1 and versions[:-1] == self.email_versions and versions[-1][0] == TARGET_EMAIL_VERSION
        else:
            valid_versions = versions == self.email_versions and versions[-1][0] == SOURCE_EMAIL_VERSION
        if not valid_versions:
            raise ValueError("Email metadata Email marker mismatch")
        if (actions._reply_variant_for(db) != self.source_schema["reply_variant"]
                or not publication_action_schema_is_current(db, version)
                or not current_canonical_email_parent_is_current(db, metadata_owner=target)):
            raise ValueError("Email metadata publication declaration mismatch")
        if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("Email metadata publication integrity/FK mismatch")

    def publish(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                self._verify(db, target=False)
                if migrate_email_metadata(db) != self.source_schema:
                    raise ValueError("Email metadata inverse variant changed")
                now = datetime.now(timezone.utc).isoformat()
                update_state_rows(db, "service_state", {"value": TARGET_STORE_VERSION, "updated_at": now},
                                  where_sql="key=?", parameters=(VERSION_KEY,))
                db.execute("insert into email_schema_migrations(version,applied_at) values(?,?)", (TARGET_EMAIL_VERSION, now))
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
            email_version=TARGET_EMAIL_VERSION, selection_references=True, result_references=True, metadata_owner=True)
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
                    restore_email_metadata(db, self.source_schema)
                    update_state_rows(db, "service_state", {
                        "value": self.original_version[0], "updated_at": self.original_version[1],
                    }, where_sql="key=?", parameters=(VERSION_KEY,))
                    db.execute("delete from email_schema_migrations where version=?", (TARGET_EMAIL_VERSION,))
                    self._verify(db, target=False)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def finalize(self):
        self.verify_loaded()


def prepare_email_metadata_schema_release(root, path):
    from app.action_email_schema_release import require_current_email_pair

    root, path = Path(root).resolve(), Path(path).resolve()
    require_current_email_pair(path, store_version=SOURCE_STORE_VERSION, email_version=SOURCE_EMAIL_VERSION,
                               selection_references=True, result_references=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("begin")
        marker, versions = _markers(db)
        if marker is None or marker[0] != SOURCE_STORE_VERSION:
            raise ValueError("Email metadata publication requires exact Store19.2/Email52 source")
        release = EmailMetadataSchemaRelease(root, path, marker, versions,
                                             capture_email_metadata_source(db), _foreign_keys(db))
        release._verify(db, target=False)
        return release
