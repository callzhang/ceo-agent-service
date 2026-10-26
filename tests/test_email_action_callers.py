"""Coupled target defaults: real constructors/coordinator, owned databases only."""

from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from app.configs_definitions import config_projection
from app.current_schema_manifest import schema_is_current
from app.email_store import EMAIL_SCHEMA_VERSION, EmailStore
from app.store import STORE_SCHEMA_VERSION, AutoReplyStore, _close_idle_connections
from app.state_definitions import state_projection
from tests.test_email_action_plan_direct_dao import _genuine_old_source

ROOT = Path(__file__).resolve().parents[1]
STORE_TARGET = STORE_SCHEMA_VERSION
EMAIL_TARGET = EMAIL_SCHEMA_VERSION


@pytest.fixture
def predecessor(tmp_path, request):
    # These tests publish their own source through the real coordinator.
    return None, *_genuine_old_source(tmp_path, request)


def _markers(path):
    with closing(sqlite3.connect(path)) as db:
        current_state = db.execute(
            "select 1 from sqlite_schema where name='state' collate nocase"
        ).fetchone() is not None
        state = state_projection("service_state") if current_state else "select * from service_state"
        store = db.execute(
            f"select value,updated_at from ({state}) where key='store_schema_version'"
        ).fetchone()
        ledger = (
            tuple(
                db.execute(
                    "select version,applied_at from email_schema_migrations order by version"
                )
            )
            if db.execute(
                "select 1 from sqlite_schema where name='email_schema_migrations'"
            ).fetchone()
            else ()
        )
        return store, ledger


def _target_shape(db):
    assert schema_is_current(db)
    from app.action_email_storage import SOURCE_TABLES

    assert not db.execute(
        "select 1 from sqlite_schema where name in ("
        + ",".join("?" for _ in SOURCE_TABLES)
        + ")",
        SOURCE_TABLES,
    ).fetchone()


def test_real_store_only_creates_the_full_current_schema_without_email_ledger(
    tmp_path,
):
    path = tmp_path / "store-only.sqlite3"
    AutoReplyStore(path)
    assert _markers(path)[0][0] == STORE_TARGET
    assert _markers(path)[1] == ()
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=on")
        assert schema_is_current(db, email_ledger=False)


@pytest.mark.parametrize("email_first", [False, True])
def test_real_fresh_orders_current_reopen_and_classification_action(
    tmp_path, email_first
):
    from app.email_classifier_contracts import EmailClassificationStatus
    from tests.test_email_store import _classification

    path = tmp_path / "both.sqlite3"
    if not email_first:
        AutoReplyStore(path)
    email = EmailStore(path)
    AutoReplyStore(path)
    assert _markers(path)[0][0] == STORE_TARGET
    assert _markers(path)[1][-1][0] == EMAIL_TARGET
    c = _classification(
        status=EmailClassificationStatus.PROCESSED, classification_id=11
    )
    email.persist_scan_result(c, model_text="synthetic")
    assert (
        email.get_classification(11)["action_plan"]["action_plan_id"]
        == c.action_plan.action_plan_id
    )
    with email._connect() as db:
        _target_shape(db)
        before = (
            db.execute(
                f"select count(*) from ({config_projection('email_category_configs')})"
            ).fetchone()[0],
            db.execute(
                f"select count(*) from ({config_projection('email_model_promotion_configs')})"
            ).fetchone()[0],
            db.execute(
                f"select count(*) from ({config_projection('email_category_description_revisions')})"
            ).fetchone()[0],
        )
        assert before == (9, 1, 9)
    EmailStore(path)
    EmailStore(path, validate_rows=False)
    AutoReplyStore(path)


def _old_blob_marker_readback(store, classification):
    from app.store import AutoReplyStore

    AutoReplyStore(store.path)
    with store._connect() as db:
        return [
            tuple(
                db.execute(
                    "select typeof(updated_at),hex(updated_at) from service_state where key='store_schema_version'"
                ).fetchone()
            ),
            tuple(
                db.execute(
                    "select typeof(applied_at),hex(applied_at) from email_schema_migrations order by version desc limit 1"
                ).fetchone()
            ),
        ]


def test_genuine_old_both_constructors_accept_native_blob_marker_timestamps(
    predecessor,
):
    from tests.test_email_action_plan_direct_dao import _genuine_old_result

    source = predecessor[3]
    with closing(sqlite3.connect(source)) as db, db:
        db.execute(
            "update service_state set updated_at=? where key='store_schema_version'",
            (b"store\x00stamp",),
        )
        db.execute(
            "update email_schema_migrations set applied_at=?", (b"email\x00stamp",)
        )
    assert _genuine_old_result(predecessor, _old_blob_marker_readback) == [
        ["blob", b"store\x00stamp".hex().upper()],
        ["blob", b"email\x00stamp".hex().upper()],
    ]


def _fingerprint(path):
    from app.action_email_definitions import (
        EMAIL_FIELDS,
        EMAIL_ROWID_TYPES,
        EMAIL_AUTOINCREMENT_TYPES,
        EMAIL_COMPOSITE_KEYS,
    )
    from tests.test_email_action_schema_release import _fingerprint as primitive_facts

    facts = primitive_facts(path)
    with closing(sqlite3.connect(path)) as db:
        types = []
        for kind, fields in EMAIL_FIELDS.items():
            if not db.execute(
                "select 1 from sqlite_schema where name=?", (kind,)
            ).fetchone():
                continue
            keys = (
                ("rowid",)
                if kind in EMAIL_ROWID_TYPES
                else ("id",)
                if kind in EMAIL_AUTOINCREMENT_TYPES
                else EMAIL_COMPOSITE_KEYS[kind]
            )
            types.append(
                (
                    kind,
                    tuple(
                        db.execute(
                            "select "
                            + ",".join("typeof(" + f + ")" for f in fields)
                            + " from "
                            + kind
                            + " order by "
                            + ",".join(keys)
                        )
                    ),
                )
            )
        sequences = tuple(
            db.execute("select name,seq,typeof(seq) from sqlite_sequence order by name")
        )
        return (
            facts,
            tuple(types),
            sequences,
            db.execute("pragma journal_mode").fetchone()[0],
        )


def test_email_first_calls_real_store_once_outside_email_lock(tmp_path, monkeypatch):
    import app.email_store as email_module
    from contextlib import contextmanager

    events = []
    actual_store = AutoReplyStore
    actual_lock = email_module.schema_initialize_lock

    @contextmanager
    def lock(path):
        events.append("email-lock")
        with actual_lock(path):
            yield

    def store(path):
        events.append("store-enter")
        result = actual_store(path)
        events.append("store-complete")
        return result

    monkeypatch.setattr(email_module, "AutoReplyStore", store)
    monkeypatch.setattr(email_module, "schema_initialize_lock", lock)
    EmailStore(tmp_path / "email-first.sqlite3")
    assert events == ["store-enter", "store-complete", "email-lock"]


def _build_with_real_constructor(monkeypatch):
    # Tests copy a prebuilt template for fresh paths; these cases need the real DDL run.
    from tests.support import store_template

    monkeypatch.setattr(store_template, "_BUILDING", True)


def test_fresh_store_publishes_schema_and_marker_in_one_transaction(
    tmp_path, monkeypatch
):
    _build_with_real_constructor(monkeypatch)
    statements = []
    original = AutoReplyStore._open_connection

    def observed(self, *args, **kwargs):
        db = original(self, *args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(AutoReplyStore, "_open_connection", observed)
    AutoReplyStore(tmp_path / "trace.sqlite3")
    normalized = [" ".join(sql.casefold().split()) for sql in statements]
    creations = [i for i, sql in enumerate(normalized) if sql.startswith("create table ")]
    action_creation = next(
        i for i in creations if normalized[i].startswith("create table actions ")
    )
    parent_creation = next(
        i for i in creations if normalized[i].startswith("create table emails")
    )
    begin = next(i for i, sql in enumerate(normalized) if sql == "begin immediate")
    marker = next(
        i
        for i, sql in enumerate(normalized)
        if sql.startswith(
            "insert into state(record_type,legacy_key,payload_json) values('service_state'"
        ) and "store_schema_version" in sql
    )
    # Every table and the version marker belong to the one transaction that opens
    # before the first table and commits only after the marker.
    assert begin < min(creations) <= parent_creation < marker
    assert action_creation < marker
    assert not any(
        sql.startswith(("select ", "insert ", "update ", "delete "))
        and (" from actions " in sql or " join actions " in sql)
        for sql in normalized[:action_creation]
    )
    assert not any(sql == "commit" for sql in normalized[begin:marker])
    assert any(sql == "commit" for sql in normalized[marker + 1 :])


@pytest.mark.parametrize("stage", ["emails", "actions", "marker", "commit"])
def test_store_fresh_suffix_fault_keeps_target_layout_and_marker_together(
    tmp_path, monkeypatch, stage
):
    _build_with_real_constructor(monkeypatch)
    path = tmp_path / "store-fault.sqlite3"
    original_open = AutoReplyStore._open_connection

    def fail(*args, **kwargs):
        raise RuntimeError("fresh suffix fault")

    class Interrupted(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if stage in {"emails", "actions"} and " ".join(sql.casefold().split()).startswith(
                f"create table {stage}"
            ):
                fail()
            if stage == "marker" and sql.startswith(
                "insert into state(record_type,legacy_key,payload_json) values('service_state'"
            ) and any(
                isinstance(value, str) and "store_schema_version" in value
                for value in parameters
            ):
                fail()
            return result

        def __exit__(self, *args):
            if (
                stage == "commit"
                and self.in_transaction
                and self.execute(
                    "select 1 from sqlite_schema where name='actions'"
                ).fetchone()
            ):
                self.rollback()
                fail()
            return super().__exit__(*args)

    def open_connection(self, *, foreign_keys=True):
        db = sqlite3.connect(self.path, factory=Interrupted)
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys = " + ("on" if foreign_keys else "off"))
        return db

    monkeypatch.setattr(AutoReplyStore, "_open_connection", open_connection)
    with pytest.raises(RuntimeError, match="fresh suffix fault"):
        AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert not db.execute(
            "select 1 from sqlite_schema where name in ('actions','emails','tasks','state')"
        ).fetchone()
    monkeypatch.setattr(AutoReplyStore, "_open_connection", original_open)
    AutoReplyStore(path)
    assert _markers(path)[0][0] == STORE_TARGET
    with closing(sqlite3.connect(path)) as db:
        _target_shape(db)


def test_concurrent_real_opposite_orders_finish_without_nested_flock(tmp_path):
    import os
    import subprocess
    import sys

    path = tmp_path / "concurrent.sqlite3"
    code = """
from pathlib import Path
import sys
from app.store import AutoReplyStore
from app.email_store import EmailStore
p=Path(sys.argv[1])
if sys.argv[2]=='store':AutoReplyStore(p);EmailStore(p)
else:EmailStore(p);AutoReplyStore(p)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(path), order],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for order in ("store", "email")
    ]
    try:
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, (stdout, stderr)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=10)
    assert _markers(path)[1] and len(_markers(path)[1]) == 1
    with closing(sqlite3.connect(path)) as db:
        assert (
            db.execute(
                f"select count(*) from ({config_projection('email_category_configs')})"
            ).fetchone()[0]
            == 9
        )
        assert (
            db.execute(
                f"select count(*) from ({config_projection('email_category_description_revisions')})"
            ).fetchone()[0]
            == 9
        )


def test_store_first_rejects_preexisting_unversioned_email_configuration(tmp_path):
    from app.email_category_config import CATEGORY_CONFIG_TABLE_SQL

    path = tmp_path / "unversioned-email.sqlite3"
    AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(CATEGORY_CONFIG_TABLE_SQL)
    before = _fingerprint(path)
    for factory in (AutoReplyStore, EmailStore):
        with pytest.raises(
            sqlite3.IntegrityError,
            match="explicit paired schema release required",
        ):
            factory(path)
        assert _fingerprint(path) == before


@pytest.mark.parametrize("damage", (
    "missing_folder_index", "column_affinity", "kind_constraint",
    "mixed_old_table", "mixed_old_view",
))
def test_current_provider_malformed_target_refused_before_constructor_mutation(tmp_path, monkeypatch, damage):
    import app.store as store_module

    path = tmp_path / "malformed-provider.sqlite3"
    AutoReplyStore(path)
    EmailStore(path)
    _close_idle_connections()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("pragma journal_mode=delete").fetchone()[0] == "delete"
        if damage == "missing_folder_index":
            db.execute("drop index idx_provider_state_observation_folder")
        elif damage in {"mixed_old_table", "mixed_old_view"}:
            db.execute(
                "create table email_provider_observations(id integer)" if damage == "mixed_old_table"
                else "create view email_provider_observations as select 1 as id"
            )
        else:
            sql = db.execute("select sql from sqlite_schema where name='provider_state'").fetchone()[0]
            old, new = (
                ("stable_message_identity TEXT,", "stable_message_identity NUMERIC,")
                if damage == "column_affinity" else
                ("record_type IN ('observation','folder_generation')",
                 "record_type IN ('observation','folder_generation','other')")
            )
            assert sql.count(old) == 1
            cookie = db.execute("pragma schema_version").fetchone()[0]
            db.execute("pragma writable_schema=on")
            db.execute("update sqlite_schema set sql=? where name='provider_state'", (sql.replace(old, new),))
            db.execute(f"pragma schema_version={cookie + 1}")
            db.execute("pragma writable_schema=off")
        db.commit()
    before = _fingerprint(path)
    bytes_before = path.read_bytes()
    store_module._INITIALIZED_STORE_PATHS.add(path.resolve())

    def unexpected(*args, **kwargs):
        raise AssertionError("malformed provider target reached an initializer")

    monkeypatch.setattr(AutoReplyStore, "_ensure_wal_journal_mode", unexpected)
    monkeypatch.setattr(EmailStore, "_initialize", unexpected)
    try:
        for constructor in (AutoReplyStore, EmailStore):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="explicit paired schema release required",
            ):
                constructor(path)
            assert _fingerprint(path) == before and path.read_bytes() == bytes_before
            assert not path.with_name(path.name + "-wal").exists()
    finally:
        store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
