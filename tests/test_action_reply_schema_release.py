"""Store18/19 paired publication of Attempt and Memory actions."""

from contextlib import closing
import sqlite3

import pytest

from app import action_reply_schema_release as release_module
from app import action_storage as storage
from app.action_reply_storage import capture_action_reply_schema, migrate_action_reply_schema
from app.task_schema_release import _foreign_keys
from tests.test_reply_attempt_action_migration import _database


ORIGINAL = ("2026-10-13.4", " original\x00timestamp ")
TARGET = "2026-10-13.5"


def _source(tmp_path, variant, *, prior_fk=False):
    path = tmp_path / "reply-release.sqlite3"
    with closing(_database(path, variant)) as db:
        db.execute("update service_state set updated_at=? where key='store_schema_version'", (ORIGINAL[1],))
        db.execute("create table unrelated_parent(id integer primary key)")
        db.execute("create table unrelated_child(id integer primary key,parent_id integer references unrelated_parent(id))")
        if prior_fk:
            db.commit()
            db.execute("pragma foreign_keys=off")
            db.execute("insert into unrelated_child values(1,404)")
        db.execute("insert into reply_attempts(id,conversation_id,conversation_title,trigger_message_id,"
                   "trigger_sender,trigger_text,action,sensitivity_kind,send_status) "
                   "values(7,'c','t','m','s','x','reply','ordinary','pending')")
        db.execute("insert into memory_write_events(id,attempt_id,event_type,payload_json) "
                   "values(9,7,'write','{}')")
        db.commit()
    release = release_module.prepare_action_reply_schema_release(tmp_path, path, ORIGINAL)
    release.migrate = _publish
    return path, release


def _publish(root, path):
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        migrate_action_reply_schema(db)
        db.execute("update service_state set value=?,updated_at='target time' "
                   "where key='store_schema_version'", (TARGET,))
        db.commit()


def _marker(db):
    return tuple(db.execute("select value,updated_at from service_state "
                            "where key='store_schema_version'").fetchone())


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate17"))
@pytest.mark.parametrize("row_factory", (None, sqlite3.Row))
def test_prepare_publish_current_facts_inverse_and_repeat(tmp_path, variant, row_factory):
    path, release = _source(tmp_path, variant, prior_fk=True)
    with closing(sqlite3.connect(path)) as db:
        before = tuple(db.iterdump())
        assert capture_action_reply_schema(db) == release.source_schema
    assert release.source_fk == (("unrelated_child", 1, "unrelated_parent", 0),)
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before
    release.publish()
    release.finalize()
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = row_factory
        assert _marker(db)[0] == TARGET
        assert storage.action_schema_is_current(db, include_reply=True,
                                                 reply_variant=release.source_schema["target_variant"])
        storage.update_action(db, "reply_attempts", 7, {"final_reply_text": "current\x00result"})
        storage.insert_action(db, "reply_attempts", {"id": 11, "conversation_id": "new", "conversation_title": "new",
            "trigger_message_id": "new", "trigger_sender": "s", "trigger_text": "x",
            "action": "reply", "sensitivity_kind": "ordinary", "send_status": "pending"})
        storage.insert_action(db, "memory_write_events", {"id": 12, "attempt_id": 11,
            "event_type": "receipt", "status": "completed", "memory_episode_id": "receipt\x00key"})
        storage.update_action(db, "memory_write_events", 9, {"attempt_id": 11})
        storage.delete_action(db, "reply_attempts", 7)
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _marker(db) == ORIGINAL
        assert capture_action_reply_schema(db) == release.source_schema
        assert db.execute("select id,final_reply_text from reply_attempts").fetchall() == [(11, "")]
        assert db.execute("select id,attempt_id,status,memory_episode_id from memory_write_events order by id").fetchall() == [
            (9, 11, "pending", ""), (12, 11, "completed", "receipt\x00key")]
        assert db.execute("select seq from sqlite_sequence where name='reply_attempts'").fetchone() == (11,)
        assert _foreign_keys(db) == release.source_fk
    release.rollback()
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        assert _marker(db)[0] == TARGET
        assert db.execute("select count(*) from actions where record_type='reply_attempts'").fetchone() == (1,)


@pytest.mark.parametrize("marker", [("unknown", ORIGINAL[1]), (ORIGINAL[0], "changed")])
def test_prepare_refuses_wrong_marker_without_write(tmp_path, marker):
    path, _ = _source(tmp_path, "actual")
    with closing(sqlite3.connect(path)) as db:
        before = tuple(db.iterdump())
    with pytest.raises(ValueError, match="source|marker"):
        release_module.prepare_action_reply_schema_release(tmp_path, path, marker)
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("change", ("target-marker", "unknown-marker", "wrong-source-time", "extra-index", "new-fk"))
def test_inverse_refuses_mixed_state_without_write(tmp_path, change):
    path, release = _source(tmp_path, "actual")
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        if change == "target-marker":
            db.execute("update service_state set value=?,updated_at=? where key='store_schema_version'", ORIGINAL)
        elif change == "unknown-marker":
            db.execute("update service_state set value='unknown' where key='store_schema_version'")
        elif change == "wrong-source-time":
            release.rollback()
            db.execute("update service_state set updated_at='wrong' where key='store_schema_version'")
        elif change == "extra-index":
            db.execute("create index unexpected_action on actions(id)")
        else:
            db.execute("pragma foreign_keys=off")
            db.execute("insert into unrelated_child values(2,404)")
        db.commit()
        before = tuple(db.iterdump())
    with pytest.raises(ValueError, match="source|mismatch|unsupported"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before


def test_inverse_failure_is_atomic(tmp_path, monkeypatch):
    path, release = _source(tmp_path, "actual")
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        before = tuple(db.iterdump())
    restore = release_module.restore_action_reply_schema

    def fail_after_restore(db, state):
        restore(db, state)
        raise RuntimeError("after inverse")

    monkeypatch.setattr(release_module, "restore_action_reply_schema", fail_after_restore)
    with pytest.raises(RuntimeError, match="after inverse"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before


def test_factory_routes_only_exact_future_transition(tmp_path, monkeypatch):
    from pathlib import Path

    from app import store_schema_release

    path, _ = _source(tmp_path, "actual")
    assert store_schema_release.prepare_store_schema_release(Path(__file__).parents[1], path) is None
    monkeypatch.setattr(store_schema_release, "_target_store_version", lambda root: TARGET)
    release = store_schema_release.prepare_store_schema_release(Path(__file__).parents[1], path)
    assert isinstance(release, release_module.ActionReplySchemaRelease)
    assert release.migrate is release_module._migrate_store
    with closing(sqlite3.connect(path)) as db:
        db.execute("update service_state set value='unknown' where key='store_schema_version'")
        db.commit()
    with pytest.raises(ValueError, match="verified inverse"):
        store_schema_release.prepare_store_schema_release(Path(__file__).parents[1], path)


@pytest.mark.parametrize("failure", ("dependencies", "verification", "restart", "health", "loaded"))
def test_failed_publication_inverts_current_facts_before_old_start(tmp_path, failure):
    from app.repository_updater import RepositoryUpdater, UpgradeFailed
    from tests.test_repository_updater_publication import _State, _repo

    checkout, operation = _repo(tmp_path)
    path, release = _source(tmp_path, "actual")
    original_verify, original_inverse = release.verify_loaded, release.rollback
    starts, events = 0, []

    def add_fact(target):
        with closing(sqlite3.connect(path)) as db:
            if target:
                storage.update_action(db, "reply_attempts", 7, {"final_reply_text": "postpublish\x00result"})
                storage.insert_action(db, "reply_attempts", {"id": 11, "conversation_id": "new", "conversation_title": "new",
                    "trigger_message_id": "new", "trigger_sender": "s", "trigger_text": "x",
                    "action": "reply", "sensitivity_kind": "ordinary", "send_status": "pending"})
                storage.insert_action(db, "memory_write_events", {"id": 12, "attempt_id": 11,
                    "event_type": "receipt", "status": "completed", "memory_episode_id": "current\x00receipt"})
            else:
                db.execute("update reply_attempts set final_reply_text=? where id=7", ("postpublish\x00result",))
                db.execute("insert into reply_attempts(id,conversation_id,conversation_title,trigger_message_id,"
                           "trigger_sender,trigger_text,action,sensitivity_kind,send_status) "
                           "values(11,'new','new','new','s','x','reply','ordinary','pending')")
                db.execute("insert into memory_write_events(id,attempt_id,event_type,status,memory_episode_id) "
                           "values(12,11,'receipt','completed',?)", ("current\x00receipt",))
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
                assert db.execute("select final_reply_text from reply_attempts where id=7").fetchone() == (
                    "postpublish\x00result",)
                assert db.execute("select memory_episode_id from memory_write_events where id=12").fetchone() == (
                    "current\x00receipt",)
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


def test_publish_failure_after_target_commit_restores_current_facts(tmp_path):
    path, release = _source(tmp_path, "actual")

    def fail_after_publish(root, path):
        _publish(root, path)
        with closing(sqlite3.connect(path)) as db:
            storage.update_action(db, "reply_attempts", 7, {"final_reply_text": "committed\x00result"})
            db.commit()
        raise RuntimeError("publisher failed after commit")

    release.migrate = fail_after_publish
    with pytest.raises(RuntimeError, match="publisher failed"):
        release.publish()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _marker(db) == ORIGINAL
        assert db.execute("select final_reply_text from reply_attempts where id=7").fetchone() == (
            "committed\x00result",)


@pytest.mark.parametrize("change", ("marker", "target-schema", "foreign-key", "email-version"))
def test_verify_loaded_rejects_changed_target(tmp_path, change):
    path, release = _source(tmp_path, "actual")
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        if change == "marker":
            db.execute("update service_state set value='unknown' where key='store_schema_version'")
        elif change == "target-schema":
            db.execute("drop index idx_actions_public_id")
        elif change == "email-version":
            db.execute("insert into email_schema_migrations values(52,'unexpected')")
        else:
            db.execute("pragma foreign_keys=off")
            db.execute("insert into unrelated_child values(2,404)")
        db.commit()
    with pytest.raises(ValueError, match="mismatch"):
        release.verify_loaded()


def test_inverse_checks_original_layout_before_accepting_noop(tmp_path):
    path, release = _source(tmp_path, "actual")
    with closing(sqlite3.connect(path)) as db:
        db.execute("create index unexpected_attempt on reply_attempts(id)")
        db.commit()
        before = tuple(db.iterdump())
    with pytest.raises(ValueError, match="unsupported|mismatch"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before
