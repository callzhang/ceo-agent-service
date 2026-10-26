"""Reversible Store25.3 -> Store26.1 schedule notification preference release."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from app.current_schema_manifest import EMAIL_VERSION, current_objects, schema_objects
from app.scheduled_task_notification_schema_source import (
    PREVIOUS_SCHEDULES_DDL,
    previous_current_objects,
)
from app.schedules_definitions import SCHEDULE_DDL, SCHEDULE_INDEXES
from app.state_definitions import state_projection
from app.state_storage import update_state_rows
from app.task8_storage import register_task8_functions

SOURCE_STORE_VERSION = "2026-10-25.3"
TARGET_STORE_VERSION = "2026-10-26.1"
VERSION_KEY = "store_schema_version"


def _marker(db):
    return db.execute(
        f"select value,updated_at from ({state_projection('service_state')}) where key=?",
        (VERSION_KEY,),
    ).fetchone()


def _write_marker(db, version, updated_at):
    update_state_rows(
        db, "service_state", {"value": version, "updated_at": updated_at},
        where_sql="key=?", parameters=(VERSION_KEY,),
    )



def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


def _rebuild(db, *, target):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("scheduled notification release requires FK-off transaction")
    sequence = db.execute(
        "select seq from sqlite_sequence where name='schedules'"
    ).fetchone()
    old_fields = tuple(row[1] for row in db.execute("pragma table_xinfo(schedules)") if row[6] == 0)
    if target and "notify_on_completion" in old_fields:
        raise ValueError("scheduled notification source already has target field")
    if not target and "notify_on_completion" not in old_fields:
        raise ValueError("scheduled notification target field is missing")
    stage = "_scheduled_notification_rows"
    if db.execute("select 1 from temp.sqlite_schema where name=?", (stage,)).fetchone():
        raise ValueError("scheduled notification transfer TEMP collision")
    db.execute(f"create temp table {stage} as select * from main.schedules")
    db.execute("drop table main.schedules")
    ddl = SCHEDULE_DDL if target else PREVIOUS_SCHEDULES_DDL
    db.execute(ddl)
    for sql in SCHEDULE_INDEXES:
        db.execute(sql)
    fields = tuple(row[1] for row in db.execute("pragma table_xinfo(schedules)") if row[6] == 0)
    if target:
        expected_old_order = tuple(field for field in fields if field != "notify_on_completion")
        if expected_old_order != old_fields:
            raise ValueError("scheduled notification release changed unrelated schedule fields")
    elif tuple(field for field in old_fields if field != "notify_on_completion") != fields:
        raise ValueError("scheduled notification inverse changed unrelated schedule fields")
    projection = ",".join(
        "1" if target and field == "notify_on_completion"
        else '"' + field + '"'
        for field in fields
    )
    db.execute(f"insert into main.schedules({','.join(fields)}) select {projection} from temp.{stage} order by id")
    db.execute(f"drop table temp.{stage}")
    if sequence is not None:
        db.execute("update sqlite_sequence set seq=max(seq,?) where name='schedules'", (sequence[0],))


class ScheduledNotificationSchemaRelease:
    def __init__(self, path: Path, *, source_marker, source_fk):
        self.path = path
        self.source_marker = tuple(source_marker)
        self.source_fk = source_fk
        self.published = False

    def _verify(self, db, *, target):
        expected = current_objects(email_ledger=True) if target else previous_current_objects(email_ledger=True)
        marker = _marker(db)
        wanted_version = TARGET_STORE_VERSION if target else self.source_marker[0]
        if (marker is None or marker[0] != wanted_version
                or schema_objects(db) != expected
                or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION
                or _foreign_keys(db) != self.source_fk
                or db.execute("pragma quick_check").fetchone()[0] != "ok"):
            raise ValueError("scheduled notification release schema or integrity mismatch")

    def _convert(self, *, inverse):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            register_task8_functions(db)
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                self._verify(db, target=inverse)
                _rebuild(db, target=not inverse)
                _write_marker(db, *(self.source_marker if inverse else (
                    TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat()
                )))
                self._verify(db, target=not inverse)
                db.commit()
                self.published = not inverse
            except BaseException:
                db.rollback()
                raise

    def publish(self):
        self._convert(inverse=False)

    def verify_loaded(self):
        self._verify_open(target=True)

    def _verify_open(self, *, target):
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            register_task8_functions(db)
            db.execute("begin")
            self._verify(db, target=target)

    def rollback(self):
        if self.published:
            self._convert(inverse=True)
        else:
            self._verify_open(target=False)

    def finalize(self):
        self.verify_loaded()


def prepare_scheduled_notification_release(path: Path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        register_task8_functions(db)
        db.execute("begin")
        marker = _marker(db)
        if (marker is None or marker[0] != SOURCE_STORE_VERSION
                or schema_objects(db) != previous_current_objects(email_ledger=True)
                or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION):
            raise ValueError("unsupported Store25.3 / Email54 schedule source schema")
        return ScheduledNotificationSchemaRelease(
            path, source_marker=marker, source_fk=_foreign_keys(db)
        )
