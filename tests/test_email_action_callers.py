"""Coupled target defaults: real constructors/coordinator, owned databases only."""

from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from app.action_storage import REPLY_VARIANTS, action_schema_is_current, insert_action
from app.configs_definitions import config_projection
from app.current_database_schema import FINAL_ACTION_OPTIONS
from app.email_store import EmailStore
from app.store import AutoReplyStore
from app.state_definitions import state_projection
from app.store_schema_release import prepare_database_schema_release
from tests.test_email_action_plan_direct_dao import _genuine_old_source

ROOT = Path(__file__).resolve().parents[1]
STORE_TARGET = "2026-10-14.1"
EMAIL_TARGET = 52


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
    assert any(
        action_schema_is_current(db, reply_variant=v, **FINAL_ACTION_OPTIONS)
        for v in REPLY_VARIANTS
    )
    from app.email_current_schema import current_canonical_email_parent_is_current
    from app.email_store import EMAIL_CLASSIFICATION_SQL

    assert current_canonical_email_parent_is_current(db)
    columns = {r[1] for r in db.execute("pragma table_info(emails)")}
    classification_columns = db.execute(
        f"select * from ({EMAIL_CLASSIFICATION_SQL}) limit 0"
    ).description
    assert len(classification_columns) == 30 and "action_plan_json" not in columns
    from app.action_email_storage import SOURCE_TABLES

    assert not db.execute(
        "select 1 from sqlite_schema where name in ("
        + ",".join("?" for _ in SOURCE_TABLES)
        + ")",
        SOURCE_TABLES,
    ).fetchone()


def test_real_store_only_creates_full_parent_and29_and_accepts_older_kind_with_fk(
    tmp_path,
):
    path = tmp_path / "store-only.sqlite3"
    AutoReplyStore(path)
    assert _markers(path)[0][0] == STORE_TARGET
    assert _markers(path)[1] == ()
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=on")
        _target_shape(db)
        result = insert_action(
            db,
            "sent_replies",
            dict(
                conversation_id="conversation",
                trigger_message_id="message",
                reply_text="synthetic",
                sent_at="2026-10-14T10:00:00+00:00",
            ),
        )
        assert result.rowcount == 1


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


def test_real_coordinator_selects_one_pair_and_real_subprocess_opens_both(
    predecessor, monkeypatch
):
    import app.store_schema_release as selectors
    from app.action_email_schema_release import ActionEmailSchemaRelease

    source = predecessor[3]
    old = _markers(source)

    def standalone(*args, **kwargs):
        raise AssertionError("standalone preparer ran before exact paired selection")

    monkeypatch.setattr(selectors, "prepare_store_schema_release", standalone)
    import app.email_schema_release as email_selectors

    monkeypatch.setattr(email_selectors, "prepare_email_schema_release", standalone)
    release = prepare_database_schema_release(ROOT, source)
    assert isinstance(release, ActionEmailSchemaRelease)
    release.publish()
    AutoReplyStore(source)
    EmailStore(source)
    assert _markers(source)[0][0] == STORE_TARGET
    assert _markers(source)[1][:-1] == old[1]
    assert _markers(source)[1][-1][0] == EMAIL_TARGET


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


@pytest.mark.parametrize(
    "damage",
    [
        "old_pair",
        "missing_store",
        "missing_email",
        "empty_email",
        "older_store",
        "older_email",
        "newer_store",
        "newer_email",
        "mixed_store",
        "mixed_email",
    ],
)
def test_constructor_refusal_precedes_wal_cache_and_all_mutation(
    predecessor, monkeypatch, damage
):
    import app.store as module

    source = predecessor[3]
    with closing(sqlite3.connect(source)) as db:
        if damage == "missing_store":
            db.execute("delete from service_state where key='store_schema_version'")
        elif damage == "missing_email":
            db.execute("drop table email_schema_migrations")
        elif damage == "empty_email":
            db.execute("delete from email_schema_migrations")
        elif damage in {"older_store", "newer_store", "mixed_store"}:
            db.execute(
                "update service_state set value=? where key='store_schema_version'",
                (
                    {
                        "older_store": "2026-10-13.4",
                        "newer_store": "future",
                        "mixed_store": STORE_TARGET,
                    }[damage],
                ),
            )
        elif damage in {"older_email", "newer_email", "mixed_email"}:
            db.execute(
                "update email_schema_migrations set version=?",
                ({"older_email": 48, "newer_email": 999, "mixed_email": 52}[damage],),
            )
        db.commit()
        assert db.execute("pragma journal_mode=delete").fetchone()[0] == "delete"
    before = _fingerprint(source)
    bytes_before = source.read_bytes()
    # Even an already-cached path cannot bypass finite pair source refusal.
    module._INITIALIZED_STORE_PATHS.add(source.resolve())

    def unexpected(*args, **kwargs):
        raise AssertionError("unsupported source reached a mutating initializer")

    monkeypatch.setattr(AutoReplyStore, "_ensure_wal_journal_mode", unexpected)
    monkeypatch.setattr(EmailStore, "_initialize", unexpected)
    for factory in (AutoReplyStore, EmailStore):
        with pytest.raises(
            sqlite3.IntegrityError, match="paired schema release|unsupported or mixed"
        ):
            factory(source)
        assert _fingerprint(source) == before and source.read_bytes() == bytes_before
    module._INITIALIZED_STORE_PATHS.discard(source.resolve())


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


def test_fresh_store_publishes_parent_actions_and_marker_atomically(
    tmp_path, monkeypatch
):
    statements = []
    original = AutoReplyStore._open_connection

    def observed(self):
        db = original(self)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(AutoReplyStore, "_open_connection", observed)
    AutoReplyStore(tmp_path / "trace.sqlite3")
    normalized = [" ".join(sql.casefold().split()) for sql in statements]
    action_creation = next(
        i for i, sql in enumerate(normalized) if sql.startswith("create table actions ")
    )
    parent_creation = next(
        i
        for i, sql in enumerate(normalized)
        if sql.startswith("create table main.emails")
    )
    assert parent_creation < action_creation
    assert not any(
        sql.startswith(("select ", "insert ", "update ", "delete "))
        and (" from actions " in sql or " join actions " in sql)
        for sql in normalized[:action_creation]
    )
    marker = next(
        i
        for i, sql in enumerate(normalized)
        if sql.startswith(
            "insert into state(record_type,legacy_key,payload_json) values('service_state'"
        ) and "store_schema_version" in sql
    )
    assert action_creation < marker
    assert not any(sql == "commit" for sql in normalized[:parent_creation])
    assert not any(sql == "commit" for sql in normalized[parent_creation:marker])
    assert any(sql == "commit" for sql in normalized[marker + 1 :])


@pytest.mark.parametrize("stage", ["parent", "actions", "marker", "commit"])
def test_store_fresh_suffix_fault_keeps_target_layout_and_marker_together(
    tmp_path, monkeypatch, stage
):
    import app.current_database_schema as current_schema

    path = tmp_path / "store-fault.sqlite3"
    original_open = AutoReplyStore._open_connection
    original_parent = current_schema._create_emails
    original_actions = current_schema.create_action_schema

    def fail(*args, **kwargs):
        raise RuntimeError("fresh suffix fault")

    if stage == "parent":

        def parent(db):
            original_parent(db)
            fail()

        monkeypatch.setattr(current_schema, "_create_emails", parent)
    elif stage == "actions":

        def actions(db, **kwargs):
            original_actions(db, **kwargs)
            fail()

        monkeypatch.setattr(current_schema, "create_action_schema", actions)
    else:

        class Interrupted(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if stage == "marker" and sql.startswith(
                    "insert into state(record_type,legacy_key,payload_json) values('service_state'"
                ) and any(
                    isinstance(value, str) and "store_schema_version" in value
                    for value in parameters
                ):
                    fail()
                return super().execute(sql, parameters)

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
    monkeypatch.setattr(current_schema, "_create_emails", original_parent)
    monkeypatch.setattr(current_schema, "create_action_schema", original_actions)
    monkeypatch.setattr(AutoReplyStore, "_open_connection", original_open)
    AutoReplyStore(path)
    assert _markers(path)[0][0] == STORE_TARGET
    with closing(sqlite3.connect(path)) as db:
        _target_shape(db)


@pytest.mark.parametrize("stage", ["ddl", "seeds", "marker", "commit"])
def test_email_fresh_fault_rolls_back_to_legitimate_store_first_and_retries(
    tmp_path, monkeypatch, stage
):
    import app.email_store as module

    path = tmp_path / "email-fault.sqlite3"
    AutoReplyStore(path)
    before = _fingerprint(path)
    original_factory = module.create_current_email_migration_ledger
    original_seed = EmailStore._seed_current_fresh_state
    original_open = EmailStore._open_connection

    def fail():
        raise RuntimeError("fresh Email fault")

    if stage == "ddl":

        def factory(db):
            original_factory(db)
            fail()

        monkeypatch.setattr(module, "create_current_email_migration_ledger", factory)
    elif stage == "seeds":

        def seed(self, db):
            original_seed(self, db)
            fail()

        monkeypatch.setattr(EmailStore, "_seed_current_fresh_state", seed)
    else:

        class Interrupted(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if stage == "marker" and sql.startswith(
                    "insert into email_schema_migrations"
                ):
                    fail()
                return super().execute(sql, parameters)

            def __exit__(self, *args):
                if stage == "commit" and self.in_transaction:
                    self.rollback()
                    fail()
                return super().__exit__(*args)

        def open_connection(self, *, foreign_keys=True):
            db = sqlite3.connect(self.path, factory=Interrupted)
            db.row_factory = sqlite3.Row
            db.execute("pragma foreign_keys = " + ("on" if foreign_keys else "off"))
            return db

        monkeypatch.setattr(EmailStore, "_open_connection", open_connection)
    with pytest.raises(RuntimeError, match="fresh Email fault"):
        EmailStore(path)
    assert _fingerprint(path) == before
    monkeypatch.setattr(
        module, "create_current_email_migration_ledger", original_factory
    )
    monkeypatch.setattr(EmailStore, "_seed_current_fresh_state", original_seed)
    monkeypatch.setattr(EmailStore, "_open_connection", original_open)
    EmailStore(path)
    assert _markers(path)[1][-1][0] == EMAIL_TARGET


def test_real_child_preserves_all_prepared_binding_and_blob_timestamps_on_inverse(
    predecessor, monkeypatch
):
    import json
    import app.action_email_schema_release as module
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
    original = _markers(source)
    _genuine_old_result(predecessor, _old_blob_marker_readback)
    release = prepare_database_schema_release(ROOT, source)
    run = module.subprocess.run
    captured = []

    def observed(*args, **kwargs):
        payload = json.loads(kwargs["input"])
        assert (
            payload["original_version"][0],
            module._timestamp_from_pipe(payload["original_version"][1]),
        ) == release.original_version
        assert (
            tuple((v, module._timestamp_from_pipe(t)) for v, t in payload["versions"])
            == release.versions
        )
        assert payload["source_schema"] == module._inverse_metadata(
            release.source_schema
        )
        assert tuple(tuple(row) for row in payload["source_fk"]) == release.source_fk
        assert (payload["target_store_version"], payload["target_email_version"]) == (
            STORE_TARGET,
            EMAIL_TARGET,
        )
        captured.append(payload)
        return run(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", observed)
    release.publish()
    assert len(captured) == 1
    release.rollback()
    monkeypatch.setattr(module.subprocess, "run", run)
    assert _markers(source) == original
    assert _genuine_old_result(predecessor, _old_blob_marker_readback) == [
        ["blob", b"store\x00stamp".hex().upper()],
        ["blob", b"email\x00stamp".hex().upper()],
    ]


def test_current_reopen_and_source51_migration_do_not_seed_empty_configs_or_revisions(
    predecessor, monkeypatch
):
    source = predecessor[3]
    with closing(sqlite3.connect(source)) as db, db:
        for row in db.execute(
            "select name from sqlite_schema where type='trigger' and tbl_name='email_category_description_revisions'"
        ).fetchall():
            db.execute(f'drop trigger "{row[0]}"')
        db.execute("delete from email_category_description_revisions")
        db.execute("delete from email_category_configs")
    # Restore the exact original retained triggers. Source maps are unchanged.
    from app.email_schema_history import _REQUIRED_TRIGGER_SQL

    with closing(sqlite3.connect(source)) as db:
        for name, sql in _REQUIRED_TRIGGER_SQL.items():
            if name.startswith("trg_email_category_description_revisions_"):
                db.execute(sql)
    release = prepare_database_schema_release(ROOT, source)
    release.publish()

    def unexpected(*args, **kwargs):
        raise AssertionError("reopen seeded existing empty configuration")

    monkeypatch.setattr(EmailStore, "_seed_current_fresh_state", unexpected)
    for first, second in ((AutoReplyStore, EmailStore), (EmailStore, AutoReplyStore)):
        first(source)
        second(source)
    with closing(sqlite3.connect(source)) as db:
        assert (
            db.execute("select count(*) from email_category_configs").fetchone()[0] == 0
        )
        assert (
            db.execute(
                "select count(*) from email_category_description_revisions"
            ).fetchone()[0]
            == 0
        )
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert (
            db.execute("select count(*) from email_category_configs").fetchone()[0] == 0
        )


def test_child_does_not_recapture_changed_source_metadata(predecessor):
    import subprocess

    source = predecessor[3]
    release = prepare_database_schema_release(ROOT, source)
    with closing(sqlite3.connect(source)) as db, db:
        db.execute(
            "update service_state set updated_at='changed after prepare' where key='store_schema_version'"
        )
    before = _fingerprint(source)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        release.publish()
    assert "source markers changed" in exc.value.stderr
    assert _fingerprint(source) == before


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


@pytest.mark.parametrize(
    "stage",
    [
        "before_ddl",
        "after_capture",
        "during_conversion",
        "after_first_marker",
        "before_commit",
        "crash_after_first_marker",
        "cold_store",
        "cold_email",
    ],
)
def test_real_child_fault_boundaries_and_inverse(predecessor, monkeypatch, stage):
    import app.action_email_schema_release as module
    import subprocess
    from tests.test_email_action_plan_direct_dao import _genuine_old_result

    source = predecessor[3]
    release = prepare_database_schema_release(ROOT, source)
    before = _fingerprint(source)
    run = subprocess.run
    prefix = f"""
import sqlite3,os
from pathlib import Path
from app import action_email_schema_release as m
from app import action_email_storage as storage
stage={stage!r}
def fail(*a,**k):raise RuntimeError('paired child fault')
if stage=='before_ddl':m.capture_email_action_source=fail
elif stage=='after_capture':m.migrate_email_action_schema=fail
elif stage=='during_conversion':
    original=storage._insert_email_raw
    def insert(*a,**k):original(*a,**k);fail()
    storage._insert_email_raw=insert
elif stage in ('cold_store','cold_email'):
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    cls=AutoReplyStore if stage=='cold_store' else EmailStore
    original=cls.__init__
    def initialize(self,*a,**k):original(self,*a,**k);fail()
    cls.__init__=initialize
else:
    original_connect=sqlite3.connect
    class Interrupted(sqlite3.Connection):
        def execute(self,sql,parameters=()):
            if stage in ('after_first_marker','crash_after_first_marker') and sql.startswith('insert into main.email_schema_migrations'):
                assert self.execute("select value from service_state where key='store_schema_version'").fetchone()[0]=={STORE_TARGET!r}
                if stage=='crash_after_first_marker':os._exit(43)
                fail()
            return super().execute(sql,parameters)
        def commit(self):
            if stage=='before_commit':fail()
            return super().commit()
    def connect(*a,**k):
        if a and str(a[0])=={str(source)!r}:k['factory']=Interrupted
        return original_connect(*a,**k)
    sqlite3.connect=connect
"""

    def faulted(command, **kwargs):
        assert command[1] == "-c"
        return run([*command[:2], prefix + "\n" + command[2]], **kwargs)

    monkeypatch.setattr(module.subprocess, "run", faulted)
    with pytest.raises(subprocess.CalledProcessError):
        release.publish()
    monkeypatch.setattr(module.subprocess, "run", run)
    if stage in {"cold_store", "cold_email"}:
        assert _markers(source)[0][0] == STORE_TARGET
        assert _markers(source)[1][-1][0] == EMAIL_TARGET
        release.rollback()
    assert _fingerprint(source) == before
    _genuine_old_result(predecessor, _old_blob_marker_readback)


def _old_direct_fact_readback(store, classification):
    from app.store import AutoReplyStore

    AutoReplyStore(store.path)
    ids = store.direct_action_ids_for_plan(classification.action_plan.action_plan_id)
    return dict(
        statuses=store.direct_action_statuses_for_plan(
            classification.action_plan.action_plan_id
        ),
        attempts=[store.list_action_attempts(identity) for identity in ids],
    )


def test_real_target_direct_facts_inverse_genuine_old_reopen_and_republish(predecessor):
    from tests.test_email_action_plan_direct_dao import (
        _direct_cycle,
        _genuine_old_result,
        _normalized,
    )

    source = predecessor[3]
    original = _markers(source)
    release = prepare_database_schema_release(ROOT, source)
    release.publish()
    email = EmailStore(source)
    _direct_cycle(email, predecessor[1])
    expected = _normalized(_old_direct_fact_readback(email, predecessor[1]))
    assert (
        min(attempt["id"] for attempts in expected["attempts"] for attempt in attempts)
        == 701
    )
    release.rollback()
    assert _markers(source) == original
    assert _genuine_old_result(predecessor, _old_direct_fact_readback) == expected
    next_release = prepare_database_schema_release(ROOT, source)
    next_release.publish()
    assert (
        _normalized(_old_direct_fact_readback(EmailStore(source), predecessor[1]))
        == expected
    )


@pytest.fixture
def reply_predecessor(tmp_path, request):
    from tests.test_email_action_reply_dao import reply_pair

    return reply_pair.__wrapped__(tmp_path, request)


def _old_reply_fact_readback(store, classification):
    from app.store import AutoReplyStore
    from app.email_store import email_action_identity
    from app.email_classifier_contracts import EmailAction

    AutoReplyStore(store.path)
    identity = email_action_identity(
        account_id=classification.provider_locator.account_id,
        stable_message_identity=classification.stable_message_identity,
        action_type=EmailAction.AUTO_REPLY,
        action_plan_version=classification.action_plan.action_plan_version,
    )
    return store.get_email_reply_receipt(identity)


@pytest.mark.parametrize("reply_predecessor", ["receipt"], indirect=True)
def test_real_target_receipt_backfill_inverse_retains_native_rowid_and_old_api(
    reply_predecessor,
):
    from tests.test_email_action_reply_dao import _reply_arguments, _receipt_arguments
    from tests.test_email_action_plan_direct_dao import _genuine_old_result, _normalized

    source = reply_predecessor[3]
    original = _markers(source)
    release = prepare_database_schema_release(ROOT, source)
    release.publish()
    email = EmailStore(source)
    args, _ = _reply_arguments(email, reply_predecessor[1])
    receipt = email.persist_email_reply_receipt(
        **(_receipt_arguments(args) | dict(smtp_acceptance_id="accepted-fixture"))
    )
    from app.action_storage import email_action_projection

    with email._connect() as db:
        assert (
            db.execute(
                "select rowid from ("
                + email_action_projection("email_reply_receipts", include_rowid=True)
                + ")"
            ).fetchone()[0]
            == 0
        )
    release.rollback()
    assert _markers(source) == original
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select rowid from email_reply_receipts").fetchone()[0] == 0
    assert _genuine_old_result(
        reply_predecessor, _old_reply_fact_readback
    ) == _normalized(receipt)


@pytest.mark.parametrize("reply_predecessor", ["receipt"], indirect=True)
@pytest.mark.parametrize(
    "failure", ["verification", "restart", "health", "loaded", "unsupported_inverse"]
)
def test_actual_updater_pair_lifecycle_preserves_receipt_before_genuine_old_restart(
    reply_predecessor, tmp_path, failure
):
    from tests.test_repository_updater_publication import _repo, _State
    from app.repository_updater import RepositoryUpdater, UpgradeFailed
    from tests.test_email_action_reply_dao import _reply_arguments, _receipt_arguments
    from tests.test_email_action_plan_direct_dao import _genuine_old_result, _normalized

    source = reply_predecessor[3]
    original = _markers(source)
    repo_path = tmp_path / "offline-updater"
    repo_path.mkdir()
    root, operation = _repo(repo_path)  # Entirely local temporary Git remotes.
    events = []
    receipts = []
    replacement_started = False

    def prepare(_recovery_source_path):
        events.append("prepare")
        release = prepare_database_schema_release(ROOT, source)
        publish, inverse, verify = (
            release.publish,
            release.rollback,
            release.verify_loaded,
        )

        def publication():
            events.append("publish")
            publish()  # Real child, atomic pair, both real default constructors.
            email = EmailStore(source)
            args, _ = _reply_arguments(email, reply_predecessor[1])
            receipts.append(
                _normalized(
                    email.persist_email_reply_receipt(
                        **(
                            _receipt_arguments(args)
                            | dict(smtp_acceptance_id="new-after-publication")
                        )
                    )
                )
            )
            if failure == "unsupported_inverse":
                with closing(sqlite3.connect(source)) as db:
                    db.execute(
                        "create trigger unknown_target after update on actions begin select 1;end"
                    )

        def rollback():
            events.append("inverse")
            inverse()

        def readback():
            if replacement_started and failure == "loaded":
                events.append("loaded-failure")
                raise RuntimeError("loaded failure")
            verify()

        release.publish, release.rollback, release.verify_loaded = (
            publication,
            rollback,
            readback,
        )
        return release

    def verification():
        events.append("verification")
        if failure in {"verification", "unsupported_inverse"}:
            raise RuntimeError("verification failure")

    def restart():
        nonlocal replacement_started
        if _markers(source)[0][0] == STORE_TARGET:
            events.append("new-start")
            replacement_started = True
            AutoReplyStore(source)
            EmailStore(source)
            if failure == "restart":
                raise RuntimeError("replacement start failure")
        else:
            events.append("old-start")
            assert _markers(source) == original
            assert (
                _genuine_old_result(reply_predecessor, _old_reply_fact_readback)
                == receipts[0]
            )

    health_calls = 0

    def health():
        nonlocal health_calls
        health_calls += 1
        return failure != "health" or health_calls > 1

    state = _State()
    updater = RepositoryUpdater(
        root,
        state,
        database_path=source,
        wait_for_quiet=lambda: events.append("quiet"),
        stop=lambda: events.append("stop"),
        database_publication=prepare,
        verification=verification,
        restart=restart,
        health=health,
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events[:3] == ["quiet", "stop", "prepare"]
    assert (
        events.index("publish") < events.index("verification") < events.index("inverse")
    )
    if failure == "unsupported_inverse":
        assert "old-start" not in events and "new-start" not in events
        assert _markers(source)[0][0] == STORE_TARGET
        assert any("needs_manual" in value for value in state.values.values())
    else:
        assert events.index("inverse") < events.index("old-start")
        if replacement_started:
            assert events[events.index("inverse") - 1] == "stop"
        assert _markers(source) == original
        assert any("rolled_back" in value for value in state.values.values())


def test_current_known_repair_variant_selection_keeps_original_accepted_path(
    tmp_path, monkeypatch
):
    import app.store as module

    path = tmp_path / "known-repair.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("drop index idx_actions_reply_run_recovery")
    module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    original = module._reply_variant_for
    observations = []

    def observed(db):
        assert any(
            action_schema_is_current(
                db,
                allow_missing_objects=True,
                reply_variant=v,
                **FINAL_ACTION_OPTIONS,
            )
            for v in REPLY_VARIANTS
        )
        observations.append(True)
        return original(db)

    monkeypatch.setattr(module, "_reply_variant_for", observed)
    AutoReplyStore(path)
    assert observations == [True]
    EmailStore(path)


def test_genuine_old_email_only_source_is_refused_without_creating_store(
    predecessor, tmp_path
):
    import os
    import subprocess
    import sys

    old = predecessor[4]
    path = tmp_path / "genuine-email-only.sqlite3"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path;import sys;from app.email_store import EmailStore;EmailStore(Path(sys.argv[1]))",
            str(path),
        ],
        cwd=old,
        env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(path)) as db:
        assert not db.execute(
            "select 1 from sqlite_schema where name='service_state'"
        ).fetchone()
        assert db.execute("pragma journal_mode=delete").fetchone()[0] == "delete"
    before = _fingerprint(path)
    original = path.read_bytes()
    for factory in (AutoReplyStore, EmailStore):
        with pytest.raises(sqlite3.IntegrityError, match="unsupported or mixed"):
            factory(path)
        assert _fingerprint(path) == before and path.read_bytes() == original


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
            match="malformed current Store-first config declaration",
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
                match="^malformed current provider/config declaration$",
            ):
                constructor(path)
            assert _fingerprint(path) == before and path.read_bytes() == bytes_before
            assert not path.with_name(path.name + "-wal").exists()
    finally:
        store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
