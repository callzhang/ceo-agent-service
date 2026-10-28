"""Paired Store 2026-10-28.3 -> 2026-10-28.4 release adding the WeChat voice transcript table."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.current_schema_manifest import current_objects, schema_objects, schema_is_current
from app.wechat.voice_transcript import DDL, OWNED_OBJECTS

SOURCE_STORE_VERSION = "2026-10-28.3"
TARGET_STORE_VERSION = "2026-10-28.4"
EMAIL_VERSION = 54
VERSION_KEY = "store_schema_version"


def source_objects():
    return tuple(obj for obj in current_objects(email_ledger=True) if obj[1] not in OWNED_OBJECTS)


def _marker(db):
    from app.state_definitions import state_projection
    return db.execute(
        f"select value,updated_at from ({state_projection('service_state')}) where key=?",
        (VERSION_KEY,),
    ).fetchone()


def _write_marker(db, version, updated_at):
    from app.state_storage import upsert_state
    upsert_state(db, "service_state", {"key": VERSION_KEY, "value": version},
                 {"value": "json_quote(excluded.value)", "updated_at": "json_quote(?)"},
                 update_parameters=(updated_at,))


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


def _email_version(db):
    return db.execute("select max(version) from email_schema_migrations").fetchone()[0]


@dataclass
class WechatVoiceTranscriptRelease:
    database_path: Path
    source_marker: tuple
    source_fk: tuple
    published: bool = False

    def _begin(self, db):
        from app.task8_storage import register_task8_functions
        register_task8_functions(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")

    def publish(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            self._begin(db)
            try:
                if (tuple(_marker(db) or ()) != self.source_marker
                        or schema_objects(db) != source_objects()
                        or _email_version(db) != EMAIL_VERSION):
                    raise ValueError("WeChat voice transcript release source changed after preparation")
                if _foreign_keys(db) != self.source_fk:
                    raise ValueError("WeChat voice transcript release source FK findings changed")
                for sql in DDL:
                    db.execute(sql)
                _write_marker(db, TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())
                if (not schema_is_current(db) or _foreign_keys(db) != self.source_fk
                        or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                    raise ValueError("WeChat voice transcript release target schema or integrity mismatch")
                db.commit()
                self.published = True
            except BaseException:
                db.rollback()
                raise

    def verify_loaded(self):
        from app.current_schema_manifest import require_current_pair
        require_current_pair(self.database_path)

    def finalize(self):
        self.verify_loaded()

    def rollback(self):
        if not self.published:
            with closing(sqlite3.connect(self.database_path.as_uri() + "?mode=ro", uri=True)) as db:
                if (tuple(_marker(db) or ()) != self.source_marker
                        or schema_objects(db) != source_objects()):
                    raise ValueError("WeChat voice transcript release did not publish and source is no longer exact")
                if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("WeChat voice transcript release unchanged-source verification failed")
            return
        # The table only caches transcripts; dropping it loses nothing that cannot be transcribed again.
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            self._begin(db)
            try:
                marker = _marker(db)
                if marker is None or marker[0] != TARGET_STORE_VERSION or not schema_is_current(db):
                    raise ValueError("WeChat voice transcript inverse requires the exact published target")
                db.execute("drop table wechat_voice_transcripts")
                _write_marker(db, self.source_marker[0], self.source_marker[1])
                if (schema_objects(db) != source_objects() or _foreign_keys(db) != self.source_fk
                        or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                    raise ValueError("WeChat voice transcript inverse schema, FK or integrity mismatch")
                db.commit()
                self.published = False
            except BaseException:
                db.rollback()
                raise


def prepare_wechat_voice_transcript_release(path: Path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        from app.task8_storage import register_task8_functions
        register_task8_functions(db)
        db.execute("begin")
        marker = _marker(db)
        if (marker is None or marker[0] != SOURCE_STORE_VERSION
                or _email_version(db) != EMAIL_VERSION
                or schema_objects(db) != source_objects()):
            raise ValueError("unsupported Store 2026-10-28.3 / Email54 source schema")
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("WeChat voice transcript release source quick_check failed")
        return WechatVoiceTranscriptRelease(path, tuple(marker), _foreign_keys(db))
