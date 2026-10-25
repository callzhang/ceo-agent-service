"""Committed schema binding at startup and updater progress writes."""
from contextlib import closing
import json
import sqlite3


from app import repository_updater as updater
from tests.test_state_upgrade_progress import source as source
from tests.test_repository_updater_publication import _State


def ledger(path, version):
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table email_schema_migrations(version,applied_at text)")
        if version is not None:
            db.execute("insert into email_schema_migrations values(?,'t')", (version,))
        db.commit()


def test_email54_release_target_binds_as_current_for_post_publish_progress(tmp_path):
    from app.current_database_schema import create_current_database_schema
    from app.current_schema_manifest import STORE_VERSION, create_current_email_migration_ledger
    from app.state_storage import insert_state as insert_current_state

    path = tmp_path / "email54.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        create_current_database_schema(db)
        create_current_email_migration_ledger(db)
        db.execute("begin")
        insert_current_state(db, "service_state", {
            "key": "store_schema_version", "value": STORE_VERSION,
        })
        db.execute("insert into email_schema_migrations values(54,'target')")
        db.commit()

    store = updater.ExistingSchemaUpgradeStateStore(path)
    assert store._state_schema_current is True
    store.set_service_state("progress", "restarting")
    assert store.get_service_state("progress") == "restarting"


def test_missing_path_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite3"
    store = updater.ExistingSchemaUpgradeStateStore(path)
    assert not store._state_schema_current
    assert not path.exists()


def test_empty_database_stays_source_without_creating_tables(tmp_path):
    path = tmp_path / "empty.sqlite3"
    sqlite3.connect(path).close()
    before = path.read_bytes()
    store = updater.ExistingSchemaUpgradeStateStore(path)
    assert not store._state_schema_current
    assert path.read_bytes() == before


def test_protocol_store_does_not_require_schema_binding():
    runner = updater.RepositoryUpdater.__new__(updater.RepositoryUpdater)
    runner.store = _State()
    operation = updater.UpgradeOperation("id", "fingerprint", "old", "new")
    runner._persist(operation, "preparing")
    assert json.loads(runner.store.get_service_state(updater.UPGRADE_OPERATION_STATE_KEY))["status"] == "preparing"
