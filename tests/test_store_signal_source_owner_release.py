from pathlib import Path
from contextlib import closing
import sqlite3

from app.current_schema_manifest import schema_objects
from app.signal_source_owner_schema_release import (
    SignalSourceOwnerRelease,
    source_objects,
)
from app.store_schema_release import prepare_database_schema_release
from tests.test_signal_source_owner_schema_release import source


def test_database_release_selector_uses_exact_signal_transition(tmp_path):
    path = tmp_path / "signal-source.sqlite3"
    source(path)
    root = Path(__file__).resolve().parents[1]

    with closing(sqlite3.connect(path)) as db:
        before = schema_objects(db)
    release = prepare_database_schema_release(root, path)

    assert isinstance(release, SignalSourceOwnerRelease)
    with sqlite3.connect(path) as db:
        assert schema_objects(db) == before
        assert db.execute(
            "select value from state where key='store_schema_version'"
        ).fetchone() == ("2026-10-25.2",)

    release.publish()
    release.verify_loaded()
    with sqlite3.connect(path) as db:
        assert db.execute(
            "select value from state where key='store_schema_version'"
        ).fetchone() == ("2026-10-25.3",)

    release.rollback()
    with sqlite3.connect(path) as db:
        assert schema_objects(db) == source_objects()
        assert db.execute(
            "select value from state where key='store_schema_version'"
        ).fetchone() == ("2026-10-25.2",)


def test_database_release_selector_migrates_notification_column_and_rolls_back(tmp_path):
    from app.scheduled_task_notification_schema_release import ScheduledNotificationSchemaRelease
    from app.current_publication_sources import _version
    from app.schedules_definitions import SCHEDULE_FIELDS
    path = tmp_path / "notification-source.sqlite3"
    source(path)
    root = Path(__file__).resolve().parents[1]
    with sqlite3.connect(path) as db:
        db.execute("insert into schedules(id,name,prompt,cron_expression,timezone,runtime_id,enabled) values(41,'daily','p','* * * * *','UTC','route',1)")
    source_release = prepare_database_schema_release(root, path)
    source_release.publish()
    source_release.finalize()

    with sqlite3.connect(path) as db:
        assert _version(db) == "2026-10-25.3"
    release = prepare_database_schema_release(root, path)
    assert isinstance(release, ScheduledNotificationSchemaRelease)
    release.publish()
    release.verify_loaded()
    with sqlite3.connect(path) as db:
        assert db.execute("select value from state where key='store_schema_version'").fetchone() == ("2026-10-26.1",)
        assert db.execute("select notify_on_completion from schedules where id=41").fetchone() == (1,)

    release.rollback()
    with sqlite3.connect(path) as db:
        assert db.execute("select value from state where key='store_schema_version'").fetchone() == ("2026-10-25.3",)
        assert tuple(row[1] for row in db.execute("pragma table_xinfo(schedules)") if row[6] == 0) == tuple(
            field for field in SCHEDULE_FIELDS if field != "notify_on_completion"
        )
        assert db.execute("select name from schedules where id=41").fetchone() == ("daily",)
