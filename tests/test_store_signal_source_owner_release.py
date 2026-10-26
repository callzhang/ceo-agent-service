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
