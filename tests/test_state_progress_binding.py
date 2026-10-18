"""Committed schema binding at startup and updater progress writes."""
from contextlib import closing
import json
import sqlite3

import pytest

from app import repository_updater as updater
from app.action_email_schema_release import (
    SOURCE_EMAIL_VERSION, SOURCE_STORE_VERSION, TARGET_EMAIL_VERSION, TARGET_STORE_VERSION,
)
from app.state_storage import upsert_state
from app.state_schema_release import capture_state_source, migrate_state_schema, restore_state_schema
from tests.test_state_upgrade_progress import source as source
from tests.test_repository_updater_publication import _repo, _State


def ledger(path, version):
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table email_schema_migrations(version,applied_at text)")
        if version is not None:
            db.execute("insert into email_schema_migrations values(?,'t')", (version,))
        db.commit()


def to_current(path, *, email=True):
    with closing(sqlite3.connect(path)) as db:
        db.execute("begin immediate")
        db.execute("insert or replace into service_state(key,value,updated_at) "
                   "values('store_schema_version',?,'t')", (SOURCE_STORE_VERSION,))
        metadata = capture_state_source(db)
        migrate_state_schema(db, metadata)
        upsert_state(db, "service_state",
                     {"key": "store_schema_version", "value": TARGET_STORE_VERSION},
                     {"value": "json_quote(?)"}, update_parameters=(TARGET_STORE_VERSION,))
        if email:
            exists = db.execute("select 1 from sqlite_schema where name='email_schema_migrations'").fetchone()
            if exists:
                db.execute("delete from email_schema_migrations")
            else:
                db.execute("create table email_schema_migrations(version,applied_at text)")
            db.execute("insert into email_schema_migrations values(?,'target')", (TARGET_EMAIL_VERSION,))
        db.commit()
        return metadata


@pytest.mark.parametrize("current,version", (
    (False, None), (False, 1), (False, SOURCE_EMAIL_VERSION),
    (True, None), (True, TARGET_EMAIL_VERSION),
))
def test_initial_binding_uses_committed_role_without_writes(source, current, version):
    if current:
        to_current(source, email=version is not None)
    elif version is not None:
        ledger(source, version)
    before = source.read_bytes()
    store = updater.ExistingSchemaUpgradeStateStore(source)
    assert store._state_schema_current is current
    assert source.read_bytes() == before
    store.set_service_state("progress", "after")
    assert store.get_service_state("progress") == "after"


@pytest.mark.parametrize("version", (0, TARGET_EMAIL_VERSION + 1, "unknown", 51.5, b"52"))
def test_unknown_ledger_refused_without_mutation(source, version):
    ledger(source, version)
    before = source.read_bytes()
    with pytest.raises(ValueError, match="committed Email schema role"):
        updater.ExistingSchemaUpgradeStateStore(source)
    assert source.read_bytes() == before


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


def test_new_instance_loads_current_persisted_operation(source):
    to_current(source)
    operation = updater.UpgradeOperation("id", "fingerprint", "old", "new")
    first = updater.ExistingSchemaUpgradeStateStore(source)
    updater.persist_operation(first, operation)
    second = updater.ExistingSchemaUpgradeStateStore(source)
    assert updater.load_persisted_operation(second, "id") == operation


def test_getter_does_not_rebind_or_retry_a_different_layout(source):
    ledger(source, SOURCE_EMAIL_VERSION)
    store = updater.ExistingSchemaUpgradeStateStore(source)
    store.set_service_state("progress", "before")
    to_current(source)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store.get_service_state("progress")
    store.bind_committed_schema()
    assert store.get_service_state("progress") == "before"


@pytest.mark.parametrize("failure", ("none", "postcommit", "inverse"))
def test_updater_persists_using_actual_committed_schema(source, tmp_path, failure):
    ledger(source, SOURCE_EMAIL_VERSION)
    local, operation = _repo(tmp_path)
    store = updater.ExistingSchemaUpgradeStateStore(source)

    class Release:
        def publish(self):
            self.metadata = to_current(source)
            if failure != "none":
                raise RuntimeError("child constructor failed after commit")

        def verify_loaded(self):
            pass

        def rollback(self):
            if failure == "inverse":
                raise RuntimeError("inverse failed")
            with closing(sqlite3.connect(source)) as db:
                db.execute("begin immediate")
                restore_state_schema(db, self.metadata)
                db.execute("update service_state set value=? where key='store_schema_version'",
                           (SOURCE_STORE_VERSION,))
                db.execute("delete from email_schema_migrations")
                db.execute("insert into email_schema_migrations values(?,'source')", (SOURCE_EMAIL_VERSION,))
                db.commit()

        def finalize(self):
            pass

    runner = updater.RepositoryUpdater(
        local, store, database_path=source, wait_for_quiet=lambda: None,
        stop=lambda: None, restart=lambda: None, health=lambda: True,
        database_publication=lambda _backup: Release(),
    )
    if failure == "none":
        assert runner.execute(operation).status == "succeeded"
    else:
        with pytest.raises(updater.UpgradeFailed):
            runner.execute(operation)
    fresh = updater.ExistingSchemaUpgradeStateStore(source)
    payload = json.loads(fresh.get_service_state(updater.UPGRADE_OPERATION_STATE_KEY))
    assert payload["status"] == {"none": "succeeded", "postcommit": "rolled_back",
                                 "inverse": "needs_manual"}[failure]
    assert fresh._state_schema_current is (failure != "postcommit")


def test_protocol_store_does_not_require_schema_binding():
    runner = updater.RepositoryUpdater.__new__(updater.RepositoryUpdater)
    runner.store = _State()
    operation = updater.UpgradeOperation("id", "fingerprint", "old", "new")
    runner._persist(operation, "preparing")
    assert json.loads(runner.store.get_service_state(updater.UPGRADE_OPERATION_STATE_KEY))["status"] == "preparing"
