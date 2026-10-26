"""Store publication accepts only the retained explicit paired transitions.

Current task/run publication and failed-start rollback invariants are covered by
 test_task_schema_release.py and test_run_schema_release.py.
"""

import sqlite3

import pytest

from app.store_schema_release import prepare_database_schema_release, prepare_store_schema_release
from tests.test_task_schema_release import target_and_source
from tests.test_task_storage import fingerprint


@pytest.mark.parametrize("source_version", ["2026-10-10.2", "2026-10-11.1", "future"])
def test_current_store_factory_rejects_unverified_sources_without_mutation(tmp_path, source_version):
    from pathlib import Path

    from app.state_storage import upsert_state
    from app.store import AutoReplyStore

    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "unverified.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute("begin")
        upsert_state(db, "service_state", {"key": "store_schema_version", "value": source_version},
                     {"value": "json_quote(excluded.value)"})
        db.commit()
        before = tuple(db.iterdump())
    with pytest.raises(ValueError, match="verified inverse"):
        prepare_store_schema_release(root, path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before


def test_current_store_factory_noops_for_already_current_marker(tmp_path):
    root, path = target_and_source(tmp_path)
    from tests.test_task_schema_release import migrate

    migrate(root, path)
    with sqlite3.connect(path) as db:
        before = fingerprint(db)
    assert prepare_store_schema_release(root, path) is None
    with sqlite3.connect(path) as db:
        assert fingerprint(db) == before


def test_database_selector_rejects_retired_store_19_12_without_mutation(tmp_path):
    from pathlib import Path

    from app.state_storage import upsert_state
    from app.store import AutoReplyStore

    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "retired-source.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute("begin")
        upsert_state(db, "service_state", {
            "key": "store_schema_version", "value": "2026-10-19.12",
        }, {"value": "json_quote(excluded.value)"})
        db.commit()
        before = tuple(db.iterdump())

    with pytest.raises(ValueError, match="unsupported current Action source schema"):
        prepare_database_schema_release(root, path)

    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before
