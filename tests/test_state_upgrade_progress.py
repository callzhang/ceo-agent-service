"""Explicit-schema upgrade progress without application constructors."""
from contextlib import closing
import sqlite3

import pytest

from app import repository_updater as updater
from app.state_schema_current import SOURCE_TABLES
from tests.configs_source_fixtures import capture


@pytest.fixture(params=("actual", "fresh"))
def source(tmp_path, request):
    path = tmp_path / "progress.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        for name in SOURCE_TABLES:
            spec = capture()[request.param]["tables"][name]
            db.execute(spec["sql"])
            for index in reversed(spec["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
        db.commit()
    return path


def test_progress_open_missing_database_does_not_create(tmp_path):
    path = tmp_path / "does-not-exist.sqlite3"
    store = updater.ExistingSchemaUpgradeStateStore(path)
    for current in (False, True):
        if current:
            store.bind_schema_current()
        with pytest.raises(sqlite3.OperationalError):
            store.set_service_state("upgrade", "missing")
        assert not path.exists()
