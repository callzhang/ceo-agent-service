"""Atomic paired primitive with sentinel markers; real callback tests live in callers."""

from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from app import action_email_schema_release as release_module
from app import action_email_storage as email_storage
from app import action_storage as storage
from app.email_current_schema import (
    create_current_email_migration_ledger,
    create_current_email_non_action_schema,
)
from tests.test_email_action_storage import PARENTS, CHILDREN, _cases, _source_tables


# Deliberate test markers, not allocated deployment versions.
TARGET_STORE = "unselected-email13-protocol-test"
TARGET_EMAIL = 1000051
ORIGINAL_STORE = ("2026-10-13.5", " original\x00store timestamp ")
ORIGINAL_LEDGER = ((1, " first\x00migration "), (51, " final\x00migration "))
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=("actual", "fresh_candidate"))
def source(tmp_path, request):
    path = tmp_path / "paired.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table tasks(id integer primary key,record_type text)")
        db.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
        create_current_email_non_action_schema(db)
        create_current_email_migration_ledger(db)
        db.execute("create table service_state(key text primary key,value text not null,updated_at text not null)")
        db.execute("insert into service_state values('store_schema_version',?,?)", ORIGINAL_STORE)
        db.executemany("insert into email_schema_migrations values(?,?)", ORIGINAL_LEDGER)
        db.execute("create table unrelated_parent(id integer primary key)")
        db.execute("create table unrelated_child(id integer primary key,parent_id integer references unrelated_parent(id))")
        db.execute("insert into unrelated_child values(1,404)")
        storage.create_action_schema(db, include_reply=True, reply_variant=(
            "actual_no_memory_fk" if request.param == "actual" else "fresh_memory_fk"))
        for table in _source_tables(request.param).values():
            db.execute(table["sql"])
            for index in reversed(table["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in table["triggers"]:
                db.execute(trigger["sql"])
        external = [item for item in PARENTS[request.param]["external_schema_references"]
                    if item["type"] == "trigger"]
        external += CHILDREN["actual"]["external_schema_references"]
        for trigger in external:
            db.execute(trigger["sql"])
        db.execute("""insert into email_classifications(
            id,account_id,folder,uidvalidity,uid,stable_message_identity,
            confidence,margin,probabilities_json,model_id,config_version,status,
            classification_source
        ) values(11,'account','INBOX',1,1,'message',0.5,0.1,'{}','model',
                 'config','pending_feedback','model')""")
        for kind, values in _cases().items():
            db.execute(f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                       tuple(values.values()))
        db.execute("update email_actions set attempt_count=1")
        # Ordinary nullable IDs retain their structural ROWID independently.
        db.execute("insert into email_unsubscribe_cancellations(rowid,decision_json) values(-7,'{}')")
        db.commit()
    return path


def _prepare(path):
    return release_module.prepare_action_email_schema_release(
        ROOT, path, target_store_version=TARGET_STORE, target_email_version=TARGET_EMAIL)


def _markers(db):
    return (
        db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone(),
        tuple(db.execute("select version,applied_at from email_schema_migrations order by version")),
    )


def _fingerprint(path):
    with closing(sqlite3.connect(path)) as db:
        rowids = tuple((kind, tuple(db.execute(f"select rowid,* from {kind} order by rowid")))
                       for kind in email_storage.SOURCE_TABLES
                       if kind in storage.EMAIL_ROWID_TYPES
                       and db.execute("select 1 from sqlite_schema where name=?", (kind,)).fetchone())
        return tuple(sorted(db.iterdump())), rowids


def test_prepare_is_readonly_and_captures_exact_pair_schema_aliases_and_ledger(source):
    before = _fingerprint(source)
    release = _prepare(source)
    assert release.original_version == ORIGINAL_STORE
    assert release.versions == ORIGINAL_LEDGER
    with closing(sqlite3.connect(source)) as db:
        assert release.source_schema == email_storage.capture_email_action_source(db)
    assert release.source_fk == (("unrelated_child", 1, "unrelated_parent", 0),)
    assert _fingerprint(source) == before


def test_publish_commits_both_markers_with_complete_target_then_inverse_is_repeatable(source):
    release = _prepare(source)
    before = _fingerprint(source)
    release.rollback()  # Unpublished source is already a valid inverse result.
    assert _fingerprint(source) == before
    release.apply()
    release.verify_loaded()
    release.finalize()
    with closing(sqlite3.connect(source)) as db:
        store, ledger = _markers(db)
        assert store[0] == TARGET_STORE
        assert ledger[:-1] == ORIGINAL_LEDGER
        assert ledger[-1] == (TARGET_EMAIL, store[1])
        assert storage.action_schema_is_current(db, include_reply=True,
            reply_variant=release.source_schema["action_variant"], include_email=True)
        assert not set(email_storage.SOURCE_TABLES).intersection(
            row[0] for row in db.execute("select name from sqlite_schema where type='table'"))
    release.rollback()
    assert _fingerprint(source) == before
    release.rollback()
    release.apply()
    release.finalize()


def test_inverse_uses_current_facts_receipt_backfill_native_ids_and_allocator_waters(source):
    release = _prepare(source)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        db.execute("pragma foreign_keys=on")
        storage.update_email_action(db, "email_reply_receipts", 1,
            {"provider_receipt_json": '{"smtp_acceptance_id":"current-receipt"}'})
        storage.insert_email_action(db, "email_unsubscribe_cancellations",
            {"action_identity": None, "decision_json": '{"current":true}'}, source_rowid=-19)
        storage.insert_action(db, "email_action_attempts", _cases()["email_action_attempts"] | {
            "id": 81, "attempt_number": 2, "provider_result_id": "new-attempt-receipt"})
        storage.delete_action(db, "email_action_attempts", 81)
        storage.insert_action(db, "email_unsubscribe_steps", _cases()["email_unsubscribe_steps"] | {
            "id": 93, "sequence": 2})
        storage.delete_action(db, "email_unsubscribe_steps", 93)
        storage.insert_action(db, "reply_attempts", {
            "id": 73, "conversation_id": "new", "conversation_title": "new",
            "trigger_message_id": "new", "trigger_sender": "s", "trigger_text": "x",
            "action": "reply", "sensitivity_kind": "ordinary", "send_status": "pending"})
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert _markers(db) == (ORIGINAL_STORE, ORIGINAL_LEDGER)
        assert db.execute("select provider_receipt_json from email_reply_receipts").fetchone() == (
            '{"smtp_acceptance_id":"current-receipt"}',)
        assert db.execute("select rowid,action_identity,decision_json from email_unsubscribe_cancellations where rowid=-19").fetchone() == (
            -19, None, '{"current":true}')
        assert db.execute("select seq from sqlite_sequence where name='email_action_attempts'").fetchone() == (81,)
        assert db.execute("select seq from sqlite_sequence where name='email_unsubscribe_steps'").fetchone() == (93,)
        assert db.execute("select attempt_count from email_actions").fetchone() == (1,)
        assert db.execute("select * from (" + storage.REPLY_ATTEMPT_COMMON_SQL + ") where id=73").fetchone() is not None
        assert db.execute("select seq from sqlite_sequence where name='actions:reply_attempts'").fetchone() == (73,)
    release.apply()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select seq from sqlite_sequence where name='email_action_attempts'").fetchone() == (81,)


@pytest.mark.parametrize("damage", ("store", "email", "timestamp", "ledger", "declaration"))
def test_prepare_and_publish_refuse_changed_source_without_mutation(source, damage):
    release = _prepare(source)
    with closing(sqlite3.connect(source)) as db:
        if damage == "store":
            db.execute("update service_state set value='unverified' where key='store_schema_version'")
        elif damage == "email":
            db.execute("insert into email_schema_migrations values(50,'future')")
            db.execute("delete from email_schema_migrations where version=51")
        elif damage == "timestamp":
            db.execute("update service_state set updated_at='changed' where key='store_schema_version'")
        elif damage == "ledger":
            db.execute("update email_schema_migrations set applied_at='changed' where version=1")
        else:
            db.execute("create index unknown_email_plan_index on email_action_plans(created_at)")
        db.commit()
    before = _fingerprint(source)
    with pytest.raises(ValueError):
        release.apply()
    assert _fingerprint(source) == before
    if damage in ("store", "email", "declaration"):
        with pytest.raises(ValueError):
            _prepare(source)
        assert _fingerprint(source) == before


@pytest.mark.parametrize("stage", ("before_ddl", "after_capture", "during_conversion", "after_first_marker", "before_commit"))
def test_publication_failure_rolls_back_ddl_markers_and_all_source_facts(source, monkeypatch, stage):
    release = _prepare(source)
    before = _fingerprint(source)

    def fail(*args, **kwargs):
        raise RuntimeError("injected paired publication failure")

    if stage == "before_ddl":
        monkeypatch.setattr(release_module, "capture_email_action_source", fail)
    elif stage == "after_capture":
        monkeypatch.setattr(release_module, "migrate_email_action_schema", fail)
    elif stage == "during_conversion":
        original = email_storage._insert_email_raw

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            fail()

        monkeypatch.setattr(email_storage, "_insert_email_raw", interrupted)
    else:
        connect = sqlite3.connect

        class InterruptedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if stage == "after_first_marker" and sql.lower().startswith("insert into main.email_schema_migrations"):
                    assert self.execute("select value from service_state where key='store_schema_version'").fetchone() == (TARGET_STORE,)
                    fail()
                return super().execute(sql, parameters)

            def commit(self):
                if stage == "before_commit":
                    assert _markers(self)[0][0] == TARGET_STORE
                    assert _markers(self)[1][-1][0] == TARGET_EMAIL
                    fail()
                return super().commit()

        def interrupted_connect(*args, **kwargs):
            if args and args[0] == source:
                return connect(*args, **kwargs, factory=InterruptedConnection)
            return connect(*args, **kwargs)

        monkeypatch.setattr(release_module.sqlite3, "connect", interrupted_connect)
    with pytest.raises(RuntimeError, match="injected paired"):
        release.apply()
    assert _fingerprint(source) == before


@pytest.mark.parametrize("damage", ("store", "ledger", "action_declaration", "external_trigger", "alias"))
def test_unsupported_current_inverse_preserves_entire_database(source, damage):
    release = _prepare(source)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        if damage == "store":
            db.execute("update service_state set value='unverified' where key='store_schema_version'")
        elif damage == "ledger":
            db.execute("update email_schema_migrations set applied_at='changed' where version=1")
        elif damage == "action_declaration":
            db.execute("create index unknown_email_action_index on actions(legacy_key)")
        elif damage == "external_trigger":
            db.execute("drop trigger trg_email_direct_action_blocks_account_delete")
        else:
            db.execute("insert into sqlite_sequence(name,seq) values('actions:unknown',100)")
        db.commit()
    before = _fingerprint(source)
    with pytest.raises(ValueError):
        release.rollback()
    assert _fingerprint(source) == before


def test_inverse_exception_after_ddl_restores_target_transaction(source, monkeypatch):
    release = _prepare(source)
    release.apply()
    before = _fingerprint(source)
    restore = release_module.restore_email_action_schema

    def interrupted(db, state):
        restore(db, state)
        raise RuntimeError("injected inverse failure after reconstruction")

    monkeypatch.setattr(release_module, "restore_email_action_schema", interrupted)
    with pytest.raises(RuntimeError, match="injected inverse"):
        release.rollback()
    assert _fingerprint(source) == before


def test_active_versions_constructors_select_coupled_target_and_noop_reprepare(tmp_path):
    from app.store import AutoReplyStore, STORE_SCHEMA_VERSION
    from app.email_store import EmailStore, EMAIL_SCHEMA_VERSION
    from app.store_schema_release import prepare_database_schema_release

    path = tmp_path / "active.sqlite3"
    AutoReplyStore(path)
    EmailStore(path)
    assert (STORE_SCHEMA_VERSION, EMAIL_SCHEMA_VERSION) == ("2026-10-14.1", 52)
    assert prepare_database_schema_release(ROOT, path) is None
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select 1 from sqlite_schema where name='email_action_plans'").fetchone() is None
        assert storage.action_schema_is_current(db, include_reply=True, reply_variant="fresh_memory_fk", include_email=True)


def test_business_facts_after_prepare_are_converted_instead_of_restoring_capture(source):
    release = _prepare(source)
    with closing(sqlite3.connect(source)) as db:
        db.execute("insert into email_unsubscribe_cancellations(rowid,action_identity,decision_json) values(23,'later','{}')")
        db.execute("update sqlite_sequence set seq=103 where name='email_action_attempts'")
        db.commit()
    before = _fingerprint(source)
    release.apply()
    release.rollback()
    assert _fingerprint(source) == before


@pytest.mark.parametrize("target_store,target_email", (
    ("2026-10-13.5", TARGET_EMAIL), ("", TARGET_EMAIL), (TARGET_STORE, 51),
    (TARGET_STORE, True), (TARGET_STORE, "unallocated"),
))
def test_protocol_requires_explicit_different_target_pair_without_mutation(source, target_store, target_email):
    before = _fingerprint(source)
    with pytest.raises(ValueError, match="explicit different target pair"):
        release_module.prepare_action_email_schema_release(
            ROOT, source, target_store_version=target_store, target_email_version=target_email)
    assert _fingerprint(source) == before


def test_inverse_failure_after_first_source_marker_preserves_target(source, monkeypatch):
    release = _prepare(source)
    release.apply()
    before = _fingerprint(source)
    connect = sqlite3.connect

    class InterruptedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.lower().startswith("delete from main.email_schema_migrations"):
                assert self.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone() == ORIGINAL_STORE
                raise RuntimeError("injected inverse marker failure")
            return super().execute(sql, parameters)

    def interrupted_connect(*args, **kwargs):
        if args and args[0] == source:
            return connect(*args, **kwargs, factory=InterruptedConnection)
        return connect(*args, **kwargs)

    monkeypatch.setattr(release_module.sqlite3, "connect", interrupted_connect)
    with pytest.raises(RuntimeError, match="injected inverse marker"):
        release.rollback()
    assert _fingerprint(source) == before
