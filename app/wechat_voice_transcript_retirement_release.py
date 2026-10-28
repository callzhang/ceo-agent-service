"""Paired Store 2026-10-28.4 -> 2026-10-28.5 release retiring the WeChat voice transcript cache."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.current_schema_manifest import current_objects, schema_objects, schema_is_current

SOURCE_STORE_VERSION = "2026-10-28.4"
TARGET_STORE_VERSION = "2026-10-28.5"
EMAIL_VERSION = 54
VERSION_KEY = "store_schema_version"
TABLE = "wechat_voice_transcripts"
# The retired table, frozen exactly as Store 28.4 declared it.
TABLE_DDL = (
    "create table wechat_voice_transcripts ("
    "account_id text not null, conversation_id text not null, message_id text not null, "
    "audio_sha256 text not null check(length(audio_sha256)=64), "
    "transcript text not null check(length(trim(transcript))>0), "
    "created_at text not null default current_timestamp, "
    "primary key(account_id,conversation_id,message_id))"
)
ROW_COLUMNS = "account_id,conversation_id,message_id,audio_sha256,transcript,created_at"


def source_objects():
    """Store 28.4: the current declaration plus the table this release removes."""
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute(TABLE_DDL)
        table = tuple(db.execute(
            "select type,name,tbl_name,sql from main.sqlite_schema where name=?", (TABLE,)).fetchone())
    return tuple(sorted((*current_objects(email_ledger=True), table), key=lambda obj: (obj[0], obj[1])))


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
class WechatVoiceTranscriptRetirementRelease:
    database_path: Path
    source_marker: tuple
    source_fk: tuple
    source_rows: tuple
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
                    raise ValueError("WeChat voice transcript retirement source changed after preparation")
                if _foreign_keys(db) != self.source_fk:
                    raise ValueError("WeChat voice transcript retirement source FK findings changed")
                if tuple(tuple(r) for r in db.execute(
                        f"select {ROW_COLUMNS} from {TABLE} order by 1,2,3")) != self.source_rows:
                    raise ValueError("WeChat voice transcript retirement source rows changed after preparation")
                db.execute(f"drop table {TABLE}")
                _write_marker(db, TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())
                if (not schema_is_current(db) or _foreign_keys(db) != self.source_fk
                        or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                    raise ValueError("WeChat voice transcript retirement target schema or integrity mismatch")
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
                    raise ValueError("WeChat voice transcript retirement did not publish and source is no longer exact")
                if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                    raise ValueError("WeChat voice transcript retirement unchanged-source verification failed")
            return
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            self._begin(db)
            try:
                marker = _marker(db)
                if marker is None or marker[0] != TARGET_STORE_VERSION or not schema_is_current(db):
                    raise ValueError("WeChat voice transcript retirement inverse requires the exact published target")
                db.execute(TABLE_DDL)
                db.executemany(
                    f"insert into {TABLE}({ROW_COLUMNS}) values(?,?,?,?,?,?)", self.source_rows)
                _write_marker(db, self.source_marker[0], self.source_marker[1])
                if (schema_objects(db) != source_objects() or _foreign_keys(db) != self.source_fk
                        or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                    raise ValueError("WeChat voice transcript retirement inverse schema, FK or integrity mismatch")
                db.commit()
                self.published = False
            except BaseException:
                db.rollback()
                raise


def prepare_wechat_voice_transcript_retirement_release(path: Path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        from app.task8_storage import register_task8_functions
        register_task8_functions(db)
        db.execute("begin")
        marker = _marker(db)
        if (marker is None or marker[0] != SOURCE_STORE_VERSION
                or _email_version(db) != EMAIL_VERSION
                or schema_objects(db) != source_objects()):
            raise ValueError("unsupported Store 2026-10-28.4 / Email54 source schema")
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("WeChat voice transcript retirement source quick_check failed")
        rows = tuple(tuple(r) for r in db.execute(f"select {ROW_COLUMNS} from {TABLE} order by 1,2,3"))
        return WechatVoiceTranscriptRetirementRelease(path, tuple(marker), _foreign_keys(db), rows)
