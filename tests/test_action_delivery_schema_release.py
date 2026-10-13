"""Paired Store17/18 publication against frozen sent delivery sources."""
# ruff: noqa: F811

from contextlib import closing
import sqlite3

import pytest

from app import action_delivery_schema_release as release_module
from app import action_storage as storage
from app.action_delivery_storage import capture_action_delivery_schema, migrate_action_delivery_schema
from app.task_schema_release import _foreign_keys
from tests.test_action_delivery_migration import (
    action_snapshot, delivery_snapshot, delivery_source,
)
from tests.test_action_receipt_source import source_values
from tests.test_action_receipt_storage import target_db  # noqa: F401
from tests.test_sent_delivery_source import SOURCE


ORIGINAL = ("2026-10-13.3", " original marker time ")
TARGET = "2026-10-13.4"


def _source(tmp_path, db, variant, *, old_fk=False):
    delivery_source(db, variant)
    db.execute("create table service_state(key text primary key,value text,updated_at text)")
    db.execute("insert into service_state values('store_schema_version',?,?)", ORIGINAL)
    db.execute("create table unrelated_parent(id integer primary key)")
    db.execute("create table unrelated_child(id integer primary key,parent_id integer references unrelated_parent(id))")
    if old_fk:
        db.execute("insert into unrelated_child(id,parent_id) values(1,404)")
    db.commit()
    path = tmp_path / "delivery.sqlite3"
    with closing(sqlite3.connect(path)) as destination:
        db.backup(destination)
    release = release_module.prepare_action_delivery_schema_release(tmp_path, path, ORIGINAL)
    release.migrate = _publish
    return path, release


def _publish(root, path):
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        migrate_action_delivery_schema(db)
        db.execute("update service_state set value=?,updated_at='target marker time' "
                   "where key='store_schema_version'", (TARGET,))
        db.commit()


def _marker(db):
    return tuple(db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone())


def _snapshot(db, *, target):
    return {
        "twelve": action_snapshot(db),
        "delivery": delivery_snapshot(db, target),
        "physical_ids": {kind: [tuple(row) for row in db.execute(
            "select id,legacy_key from actions where record_type=? order by id", (kind,))]
            for kind in storage.ALL_ACTION_TYPES_WITH_POSTFIX},
        "allocators": [tuple(row) for row in db.execute(
            "select name,seq from sqlite_sequence where name like 'actions:%' "
            "or name='sent_replies' order by name")],
    }


@pytest.mark.parametrize("variant", tuple(SOURCE))
@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
def test_current_facts_and_watermarks_survive_inverse_repeat_and_republication(tmp_path, target_db, variant, row_factory):
    path, release = _source(tmp_path, target_db, variant)
    with closing(sqlite3.connect(path)) as db:
        original = _snapshot(db, target=False)
    release.publish()
    release.finalize()
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = row_factory
        assert _snapshot(db, target=True)["twelve"] == original["twelve"]
        assert _snapshot(db, target=True)["delivery"] == original["delivery"]
        assert _snapshot(db, target=True)["physical_ids"] == original["physical_ids"]
        storage.update_action(db, "sent_replies", 701, {"recall_status": "recalled", "recalled_at": " raw\0time "})
        new_id = storage.insert_action(db, "sent_replies", {"conversation_id": "new\0chat",
            "trigger_message_id": "new", "reply_text": " raw\0body "}).lastrowid
        assert new_id == 902
        storage.delete_action(db, "sent_replies", new_id)
        new_id = storage.insert_action(db, "sent_replies", {"conversation_id": "current",
            "trigger_message_id": "new", "reply_text": " current "}).lastrowid
        assert new_id == 903
        storage.update_action(db, "sent_reply_observers", (701, 12), {
            "sent_reply_id": 903, "agent_run_id": 1, "reply_task_id": 1})
        storage.insert_action(db, "sent_reply_observers", {
            "sent_reply_id": 701, "agent_run_id": 1, "reply_task_id": 1,
            "created_at": " current\0observer "})
        storage.insert_action(db, "agent_execution_receipts", source_values("agent_execution_receipts", token="current"))
        storage.update_action(db, "outbound_postfixes", ("dingtalk", "01"), {"final_body": " current\0body "})
        current = _snapshot(db, target=True)
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = row_factory
        assert _marker(db) == ORIGINAL
        assert _snapshot(db, target=False)["twelve"] == current["twelve"]
        assert _snapshot(db, target=False)["delivery"] == current["delivery"]
        assert _snapshot(db, target=False)["physical_ids"] == current["physical_ids"]
        assert dict(_snapshot(db, target=False)["allocators"])["sent_replies"] == 903
        assert capture_action_delivery_schema(db)["tables"] == release.source_schema["tables"]
        db.execute("update sent_replies set recall_error=? where id=701", ("after inverse\0",))
        progressed = _snapshot(db, target=False)
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _snapshot(db, target=False) == progressed
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        assert _snapshot(db, target=True)["twelve"] == progressed["twelve"]
        assert _snapshot(db, target=True)["delivery"] == progressed["delivery"]
        assert storage.action_schema_is_current(db, include_delivery=True)


@pytest.mark.parametrize("variant", tuple(SOURCE))
def test_prepare_is_read_only_and_publication_sources_are_exact(tmp_path, target_db, variant):
    path, release = _source(tmp_path, target_db, variant, old_fk=True)
    with closing(sqlite3.connect(path)) as db:
        before = tuple(db.iterdump())
        source = release_module.publication_delivery_sources(db)
        assert tuple(source) == storage.DELIVERY_ACTION_TYPES
        expected = delivery_snapshot(db)
        for kind, sql in source.items():
            cursor = db.execute(sql + " order by " + ("id" if kind == "sent_replies" else "sent_reply_id,agent_run_id"))
            assert tuple(field[0] for field in cursor.description) == storage.SOURCE_COLUMNS[kind]
            assert [tuple(row) for row in cursor] == [tuple(value for value, _ in row) for row in expected[kind]]
        prepared = release_module.prepare_action_delivery_schema_release(tmp_path, path, ORIGINAL)
        assert prepared.migrate is release_module._migrate_store
        assert prepared.source_schema == release.source_schema
        assert prepared.source_fk == (("unrelated_child", 1, "unrelated_parent", 0),)
        assert tuple(db.iterdump()) == before
    release.publish()
    release.verify_loaded()
    with closing(sqlite3.connect(path)) as db:
        assert release_module.publication_delivery_sources(db) == {
            kind: storage.ACTION_PROJECTIONS[kind] for kind in storage.DELIVERY_ACTION_TYPES}
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _foreign_keys(db) == release.source_fk


@pytest.mark.parametrize("original", [("unknown", ORIGINAL[1]), (ORIGINAL[0], "changed time")])
def test_prepare_rejects_wrong_or_changed_marker_without_mutation(tmp_path, target_db, original):
    path, _ = _source(tmp_path, target_db, "actual")
    with closing(sqlite3.connect(path)) as db:
        before = tuple(db.iterdump())
    with pytest.raises(ValueError, match="source|marker"):
        release_module.prepare_action_delivery_schema_release(tmp_path, path, original)
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("mutation", ["marker", "source-index", "target-index", "old-table"])
def test_publication_source_rejects_mixed_layout_without_mutation(tmp_path, target_db, mutation):
    path, release = _source(tmp_path, target_db, "actual")
    if mutation in ("target-index", "old-table"):
        release.publish()
    with closing(sqlite3.connect(path)) as db:
        if mutation == "marker":
            db.execute("update service_state set value=? where key='store_schema_version'", (TARGET,))
        elif mutation == "source-index":
            db.execute("create index unexpected_delivery_index on sent_replies(reply_text)")
        elif mutation == "target-index":
            db.execute("drop index idx_actions_public_id")
        else:
            db.execute("create table sent_replies(unexpected text)")
        before = tuple(db.iterdump())
        with pytest.raises(ValueError, match="source|schema"):
            release_module.publication_delivery_sources(db)
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("mutation", ["marker", "schema", "foreign-key", "integrity"])
def test_target_readback_rejects_changes(tmp_path, target_db, mutation):
    path, release = _source(tmp_path, target_db, "actual")
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        if mutation == "marker":
            db.execute("update service_state set value='unknown' where key='store_schema_version'")
        elif mutation == "schema":
            db.execute("drop index idx_actions_public_id")
        elif mutation == "foreign-key":
            db.execute("insert into unrelated_child values(2,404)")
        else:
            db.execute("create table unrelated_checked(value text)")
            db.execute("insert into unrelated_checked values(null)")
            db.execute("pragma writable_schema=on")
            db.execute("update sqlite_schema set sql='CREATE TABLE unrelated_checked(value text not null)' "
                       "where name='unrelated_checked'")
            db.execute("pragma writable_schema=off")
        db.commit()
    with pytest.raises(ValueError, match="mismatch"):
        release.verify_loaded()


def test_inverse_failure_and_changed_source_are_atomic(tmp_path, target_db, monkeypatch):
    path, release = _source(tmp_path, target_db, "actual")
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        storage.update_action(db, "sent_replies", 701, {"recall_key": " current "})
        db.commit()
        before = tuple(db.iterdump())
    restore = release_module.restore_action_delivery_schema

    def fail_after_restore(db, state):
        restore(db, state)
        raise RuntimeError("after inverse")

    monkeypatch.setattr(release_module, "restore_action_delivery_schema", fail_after_restore)
    with pytest.raises(RuntimeError, match="after inverse"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before
        assert _marker(db)[0] == TARGET
    monkeypatch.setattr(release_module, "restore_action_delivery_schema", restore)
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        db.execute("create index unexpected_delivery_index on sent_replies(reply_text)")
        db.commit()
        before = tuple(db.iterdump())
    with pytest.raises(ValueError, match="source|declaration"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("mutation", ["unknown-marker", "old-marker-on-target", "changed-source-time", "foreign-key"])
def test_inverse_rejects_changed_current_layout_before_writes(tmp_path, target_db, mutation):
    path, release = _source(tmp_path, target_db, "actual")
    release.publish()
    if mutation == "changed-source-time":
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        if mutation == "unknown-marker":
            db.execute("update service_state set value='unknown' where key='store_schema_version'")
        elif mutation == "old-marker-on-target":
            db.execute("update service_state set value=?,updated_at=? where key='store_schema_version'", ORIGINAL)
        elif mutation == "changed-source-time":
            db.execute("update service_state set updated_at='changed' where key='store_schema_version'")
        else:
            db.execute("insert into unrelated_child values(2,404)")
        db.commit()
        before = tuple(db.iterdump())
    with pytest.raises(ValueError, match="source|mismatch|declaration"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("failure", ["dependencies", "verification", "restart", "health", "loaded"])
def test_failed_publication_inverts_new_facts_before_old_start(tmp_path, target_db, failure):
    from app.repository_updater import RepositoryUpdater, UpgradeFailed
    from tests.test_repository_updater_publication import _State, _repo

    checkout, operation = _repo(tmp_path)
    path, release = _source(tmp_path, target_db, "actual")
    original_verify, original_inverse = release.verify_loaded, release.rollback
    starts, events = 0, []

    def add_fact(target):
        with closing(sqlite3.connect(path)) as db:
            if target:
                storage.update_action(db, "sent_replies", 701, {"recall_error": " postpublish\0 "})
                storage.insert_action(db, "sent_replies", {"conversation_id": "later",
                    "trigger_message_id": "later", "reply_text": " provider\0 "})
            else:
                db.execute("update sent_replies set recall_error=? where id=701", (" postpublish\0 ",))
                db.execute("insert into sent_replies(conversation_id,trigger_message_id,reply_text) "
                           "values('later','later',?)", (" provider\0 ",))
            db.commit()

    def verify():
        if starts and failure == "loaded":
            raise RuntimeError("loaded failure")
        original_verify()

    def inverse():
        events.append("inverse")
        original_inverse()

    release.verify_loaded, release.rollback = verify, inverse

    def start():
        nonlocal starts
        starts += 1
        events.append("start")
        with closing(sqlite3.connect(path)) as db:
            if starts == 1 and failure not in ("dependencies", "verification"):
                assert _marker(db)[0] == TARGET
            else:
                assert events[-2] == "inverse"
                assert _marker(db) == ORIGINAL
                expected_error = " error " if failure == "dependencies" else " postpublish\0 "
                expected_max = 701 if failure == "dependencies" else 902
                assert db.execute("select recall_error from sent_replies where id=701").fetchone() == (expected_error,)
                assert db.execute("select max(id) from sent_replies").fetchone() == (expected_max,)
        if starts == 1 and failure not in ("dependencies", "verification"):
            add_fact(True)
        if starts == 1 and failure == "restart":
            raise RuntimeError("restart failure")

    def fail_at(stage):
        if stage == failure:
            add_fact(stage != "dependencies")
            raise RuntimeError(stage)

    updater = RepositoryUpdater(
        checkout, _State(), database_path=path, stop=lambda: events.append("stop"),
        restart=start, health=lambda: failure != "health" or starts > 1,
        database_publication=lambda: release,
        dependency_sync=lambda: fail_at("dependencies"), verification=lambda: fail_at("verification"),
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events[-2:] == ["inverse", "start"]
    with closing(sqlite3.connect(path)) as db:
        assert _marker(db) == ORIGINAL
        assert db.execute("select recall_error from sent_replies where id=701").fetchone() == (" postpublish\0 ",)
