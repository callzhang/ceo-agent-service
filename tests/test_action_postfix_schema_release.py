"""Injected historical Store16/17 checks on an inverse-derived source16."""
# ruff: noqa: F811

from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from app import action_postfix_schema_release as release_module
from app import action_storage as storage
from app.action_postfix_storage import capture_action_postfix_schema, migrate_action_postfix_schema
from app.action_delivery_storage import capture_action_delivery_schema, restore_action_delivery_schema
from app.action_receipt_storage import migrate_action_receipt_schema
from app.task_schema_release import _foreign_keys
from tests.test_action_receipt_source import source_values
from tests.test_action_receipt_storage import source15_database
from tests.test_action_receipt_storage import target_db  # noqa: F401
from tests.test_action_delivery_migration import delivery_source
from tests.test_postfix_action_source import SOURCE as POSTFIX_SOURCE


SOURCE = ("2026-10-13.2", " original marker time ")
TARGET = "2026-10-13.3"


def _source(path, *, old_fk=False):
    """Declared nine-type actions plus the frozen three source tables."""
    with closing(source15_database(path, fresh=True)) as db:
        db.execute("begin immediate")
        migrate_action_receipt_schema(db)
        for definition in POSTFIX_SOURCE.values():
            for obj in definition["objects"]:
                db.execute(obj["sql"])
        for key in ("original", "destination", "delete"):
            db.execute(
                "insert into outbound_postfixes(channel,delivery_key,final_body,feedback_token,postfix_version,created_at) values(?,?,?,?,?,?)",
                ("dingtalk", key, " raw body\0 ", " token ", " source version ", " source time "),
            )
        db.execute("insert into outbound_postfix_receipts(channel,delivery_key,provider_result_json,created_at) values(?,?,?,?)",
                   ("dingtalk", "original", " not JSON\0 ", " receipt time "))
        db.execute("insert into native_reply_dispatches(channel,delivery_key,created_at) values(?,?,?)",
                   ("dingtalk", "original", " dispatch time "))
        db.execute("create table service_state(key text primary key,value text,updated_at text)")
        db.execute("insert into service_state(key,value,updated_at) values('store_schema_version',?,?)", SOURCE)
        db.execute("create table unrelated_parent(id integer primary key)")
        db.execute("create table unrelated_child(id integer primary key,parent_id integer references unrelated_parent(id))")
        if old_fk:
            db.execute("insert into unrelated_child(id,parent_id) values(1,404)")
        db.commit()


def _publish(root, path):
    """Exercise the real migration behind an explicitly injected callback."""
    with closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        migrate_action_postfix_schema(db)
        db.execute("update service_state set value=?,updated_at='target marker time' where key='store_schema_version'", (TARGET,))
        db.commit()


def _release(tmp_path, *, old_fk=False):
    path = tmp_path / "postfix.sqlite3"
    _source(path, old_fk=old_fk)
    release = release_module.prepare_action_postfix_schema_release(tmp_path, path, SOURCE)
    release.migrate = _publish
    return path, release


def _marker(db):
    return db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone()


def _snapshot(db, *, target):
    columns = [row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0]
    return {
        "physical_nine": db.execute(
            f"select {','.join(columns)} from actions where record_type in ({','.join('?' for _ in storage.ALL_ACTION_TYPES)}) order by id",
            storage.ALL_ACTION_TYPES,
        ).fetchall(),
        "public_nine": {kind: db.execute(f"select * from ({storage.ACTION_PROJECTIONS[kind]}) order by {storage.IDENTITY_FIELDS[kind]}").fetchall()
                        for kind in storage.ALL_ACTION_TYPES},
        "postfix": {kind: db.execute(
            f"select {','.join(storage.SOURCE_COLUMNS[kind])} from "
            + (f"({storage.ACTION_PROJECTIONS[kind]})" if target else kind)
            + " order by channel,delivery_key",
        ).fetchall() for kind in storage.POSTFIX_ACTION_TYPES},
        "sequence": db.execute("select name,seq from sqlite_sequence where name in (?,?) order by name",
                               tuple(storage.ACTION_SEQUENCE_NAMES.values())).fetchall(),
    }


def _new_facts(db, *, target, key="new"):
    values = {
        "outbound_postfixes": dict(channel="wechat", delivery_key=key, final_body=" new raw body\0 ",
                                  feedback_token="", postfix_version=" new version ", created_at=" new time "),
        "outbound_postfix_receipts": dict(channel="wechat", delivery_key=key, provider_result_json=" new receipt\0 ", created_at=" new receipt time "),
        "native_reply_dispatches": dict(channel="wechat", delivery_key=key, created_at=" new dispatch time "),
    }
    for kind, fields in values.items():
        if target:
            storage.insert_action(db, kind, fields)
        else:
            db.execute(f"insert into {kind}({','.join(fields)}) values({','.join('?' for _ in fields)})", tuple(fields.values()))
    for kind in storage.RECEIPT_ACTION_TYPES:
        storage.insert_action(db, kind, source_values(kind, token=key))


def test_current_facts_and_all_nine_identities_survive_inverse_repeat_and_republication(tmp_path):
    path, release = _release(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        original = _snapshot(db, target=False)
    release.publish()
    release.finalize()
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=on")
        assert _snapshot(db, target=True) == original
        _new_facts(db, target=True)
        storage.update_action(db, "outbound_postfixes", ("dingtalk", "original"), {"final_body": " current body\0 "})
        storage.update_action(db, "outbound_postfix_receipts", ("dingtalk", "original"),
                              {"delivery_key": "destination", "provider_result_json": " current receipt\0 "})
        storage.update_action(db, "native_reply_dispatches", ("dingtalk", "original"), {"delivery_key": "destination"})
        storage.update_action(db, "outbound_postfixes", ("dingtalk", "original"), {"delivery_key": "renamed"})
        storage.delete_action(db, "outbound_postfixes", ("dingtalk", "delete"))
        storage.update_action(db, "external_action_results", "01", {"provider_result_json": " current global\0 "})
        storage.delete_action(db, "external_action_results", "A")
        current = _snapshot(db, target=True)
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _marker(db) == SOURCE
        assert _snapshot(db, target=False) == current
        assert capture_action_postfix_schema(db)["actions"] == release.source_schema["actions"]
        _new_facts(db, target=False, key="after-inverse")
        progressed = _snapshot(db, target=False)
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _snapshot(db, target=False) == progressed
        assert _marker(db) == SOURCE
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        assert _snapshot(db, target=True) == progressed
        assert storage.action_schema_is_current(db, include_postfix=True)
        assert not _foreign_keys(db)
        assert dict(progressed["sequence"]) == {
            "actions:agent_effect_intents": 562, "actions:agent_execution_receipts": 117,
        }


def test_real_store17_fixture_historical_publication_and_republication(tmp_path, target_db):
    from app.action_postfix_storage import restore_action_postfix_schema
    from app.run_storage import insert_run
    from app.store import AutoReplyStore
    from app.task_storage import insert_task

    scope_path = tmp_path / "captured-source-scope.sqlite3"
    _source(scope_path)
    with closing(sqlite3.connect(scope_path)) as db:
        source_schema = capture_action_postfix_schema(db)
    path = tmp_path / "full-store.sqlite3"
    AutoReplyStore(path)
    delivery_source(target_db, "actual")
    delivery_schema = capture_action_delivery_schema(target_db)
    with closing(sqlite3.connect(path)) as db:
        insert_task(db, "reply_tasks", dict(id=1, conversation_id="chat", conversation_title="source",
                    single_chat=0, trigger_message_id="message", trigger_create_time="time", trigger_sender="sender"))
        insert_run(db, "agent_runs", dict(id=1, task_id=1, execution_generation="generation", role="consumer"))
        _new_facts(db, target=True, key="before-publication")
        for kind, watermark in (("agent_effect_intents", 560), ("agent_execution_receipts", 115)):
            storage.insert_action(db, kind, {**source_values(kind, token="deleted-high"), "id": watermark})
            storage.delete_action(db, kind, watermark)
        db.commit()
        # The current factory creates Store18. Reconstruct actual Store17 and
        # then Store16 with both paired inverses before historical publication.
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        restore_action_delivery_schema(db, delivery_schema)
        db.execute("update service_state set value=? where key='store_schema_version'", (TARGET,))
        original = _snapshot(db, target=True)
        restore_action_postfix_schema(db, source_schema)
        db.execute("update service_state set value=?,updated_at=? where key='store_schema_version'", SOURCE)
        db.commit()
        assert capture_action_postfix_schema(db) == source_schema
        assert _snapshot(db, target=False) == original
    release = release_module.prepare_action_postfix_schema_release(Path(__file__).parents[1], path, SOURCE)
    assert isinstance(release, release_module.ActionPostfixSchemaRelease)
    assert release.migrate is release_module._migrate_store
    release.migrate = _publish
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        assert _marker(db)[0] == TARGET
        assert _snapshot(db, target=True) == original
        _new_facts(db, target=True, key="after-publication")
        current = _snapshot(db, target=True)
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _marker(db) == SOURCE
        assert _snapshot(db, target=False) == current
    release.publish()
    release.finalize()
    with closing(sqlite3.connect(path)) as db:
        assert _snapshot(db, target=True) == current


def test_prepare_captures_read_only_exact_marker_schema_and_old_fk_diagnostics(tmp_path):
    path, release = _release(tmp_path, old_fk=True)
    assert release.root == tmp_path.resolve() and release.database_path == path.resolve()
    assert release.original_version == SOURCE
    assert release.source_fk == (("unrelated_child", 1, "unrelated_parent", 0),)
    with closing(sqlite3.connect(path)) as db:
        before = tuple(db.iterdump())
        assert release.source_schema == capture_action_postfix_schema(db)
    prepared = release_module.prepare_action_postfix_schema_release(tmp_path, path, SOURCE)
    assert prepared.migrate is release_module._migrate_store
    assert release_module.SOURCE_ACTION_POSTFIX_VERSION == SOURCE[0]
    assert release_module.ACTION_POSTFIX_VERSION == TARGET
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before
    release.publish()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _foreign_keys(db) == release.source_fk


@pytest.mark.parametrize("original", [("unknown", SOURCE[1]), (SOURCE[0], "changed time")])
def test_prepare_rejects_wrong_version_or_changed_marker_without_mutation(tmp_path, original):
    path = tmp_path / "source.sqlite3"
    _source(path)
    with closing(sqlite3.connect(path)) as db:
        before = tuple(db.iterdump())
    with pytest.raises(ValueError, match="source|marker"):
        release_module.prepare_action_postfix_schema_release(tmp_path, path, original)
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before


def test_publication_sources_expose_exact_three_source_shapes_at_both_versions(tmp_path):
    path, release = _release(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        sources = release_module.publication_postfix_sources(db)
        assert tuple(sources) == storage.POSTFIX_ACTION_TYPES
        expected = _snapshot(db, target=False)["postfix"]
        for kind, sql in sources.items():
            cursor = db.execute(sql + " order by channel,delivery_key")
            assert tuple(item[0] for item in cursor.description) == storage.SOURCE_COLUMNS[kind]
            assert cursor.fetchall() == expected[kind]
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        sources = release_module.publication_postfix_sources(db)
        assert sources == {kind: storage.ACTION_PROJECTIONS[kind] for kind in storage.POSTFIX_ACTION_TYPES}
        assert {kind: db.execute(sql + " order by channel,delivery_key").fetchall() for kind, sql in sources.items()} == expected


@pytest.mark.parametrize("target,mutation", [(False, "marker"), (True, "marker"), (False, "unknown"),
                                            (True, "unknown"), (False, "extra-index"), (True, "missing-index"), (True, "old-table")])
def test_publication_reader_rejects_unknown_or_mixed_layout_without_changes(tmp_path, target, mutation):
    path, release = _release(tmp_path)
    if target:
        release.publish()
    with closing(sqlite3.connect(path)) as db:
        if mutation in ("marker", "unknown"):
            version = "unknown" if mutation == "unknown" else SOURCE[0] if target else TARGET
            db.execute("update service_state set value=? where key='store_schema_version'", (version,))
        elif mutation == "extra-index":
            db.execute("create index extra_postfix_index on outbound_postfixes(created_at)")
        elif mutation == "missing-index":
            db.execute("drop index idx_actions_postfix_identity")
        else:
            db.execute("create table native_reply_dispatches(unexpected text)")
        before = tuple(db.iterdump())
        with pytest.raises(ValueError, match="source|schema|declaration"):
            release_module.publication_postfix_sources(db)
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("mutation", ["marker", "schema", "foreign-key", "integrity"])
def test_target_readback_rejects_changed_marker_schema_fk_or_integrity(tmp_path, mutation):
    path, release = _release(tmp_path)
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        if mutation == "marker":
            db.execute("update service_state set value='unknown' where key='store_schema_version'")
        elif mutation == "schema":
            db.execute("drop index idx_actions_postfix_identity")
        elif mutation == "foreign-key":
            db.execute("insert into unrelated_child(id,parent_id) values(2,404)")
        else:
            # Damage only this disposable fixture's declaration so the fresh
            # reader's quick_check detects a stored NULL violating NOT NULL.
            db.execute("create table unrelated_checked(value text)")
            db.execute("insert into unrelated_checked(value) values(null)")
            db.execute("pragma writable_schema=on")
            db.execute("update sqlite_schema set sql='CREATE TABLE unrelated_checked(value text not null)' where name='unrelated_checked'")
            db.execute("pragma writable_schema=off")
        db.commit()
    with pytest.raises(ValueError, match="mismatch"):
        release.verify_loaded()


def test_inverse_failure_rolls_back_schema_marker_and_current_facts(tmp_path, monkeypatch):
    path, release = _release(tmp_path)
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        _new_facts(db, target=True)
        db.commit()
        before = tuple(db.iterdump())
    restore = release_module.restore_action_postfix_schema

    def fail_after_restore(db, state):
        restore(db, state)
        raise RuntimeError("after inverse writes")

    monkeypatch.setattr(release_module, "restore_action_postfix_schema", fail_after_restore)
    with pytest.raises(RuntimeError, match="after inverse writes"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before
        assert _marker(db)[0] == TARGET


@pytest.mark.parametrize("mutation", ["unknown-marker", "old-marker-on-target", "changed-source-time", "changed-source-schema", "foreign-key"])
def test_inverse_rejects_unknown_or_mixed_current_layout_before_changes(tmp_path, mutation):
    path, release = _release(tmp_path)
    release.publish()
    if mutation in ("changed-source-time", "changed-source-schema"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        if mutation == "unknown-marker":
            db.execute("update service_state set value='unknown' where key='store_schema_version'")
        elif mutation == "old-marker-on-target":
            db.execute("update service_state set value=?,updated_at=? where key='store_schema_version'", SOURCE)
        elif mutation == "changed-source-time":
            db.execute("update service_state set updated_at='changed' where key='store_schema_version'")
        elif mutation == "changed-source-schema":
            db.execute("create index extra_postfix_index on outbound_postfixes(created_at)")
        else:
            db.execute("insert into unrelated_child(id,parent_id) values(2,404)")
        db.commit()
        before = tuple(db.iterdump())
    with pytest.raises(ValueError, match="source|mismatch|declaration"):
        release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("failure", ["dependencies", "verification", "restart", "health", "loaded"])
def test_failed_publication_preserves_new_facts_and_inverts_before_old_start(tmp_path, failure):
    from app.repository_updater import RepositoryUpdater, UpgradeFailed
    from tests.test_repository_updater_publication import _State, _repo

    checkout, operation = _repo(tmp_path)
    path, release = _release(tmp_path)
    original_verify, original_inverse = release.verify_loaded, release.rollback
    starts, events = 0, []

    def add_fact(target):
        with closing(sqlite3.connect(path)) as db:
            _new_facts(db, target=target, key="replacement")
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
                assert _marker(db) == SOURCE
                assert storage.action_schema_is_current(db, include_receipts=True)
                assert db.execute("select provider_result_json from outbound_postfix_receipts where channel='wechat' and delivery_key='replacement'").fetchone() == (" new receipt\0 ",)
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
        assert _marker(db) == SOURCE
        assert not _foreign_keys(db)
        assert storage.action_physical_id(db, "external_action_results", "replacement") is not None
        assert db.execute("select operation_id from (" + storage.AGENT_EXECUTION_RECEIPT_SQL + ") where operation_id='replacement'").fetchone() == ("replacement",)
        assert capture_action_postfix_schema(db)["sequences"] == {"agent_effect_intents": 561, "agent_execution_receipts": 116}
