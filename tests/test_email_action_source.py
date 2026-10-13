"""Native SQLite oracle for the frozen nine-table email action source variants.

These tests execute captured source SQL only. They make no claim about the future
migration, Store constructors, provider effects, or runtime policy.
"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest


CAPTURE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                      "2026-10-13-database-redesign-email-actions-source.json").read_text())
VARIANTS = ("actual", "fresh_candidate")
TABLES = (
    "email_action_plans", "email_actions", "email_action_attempts",
    "email_unsubscribe_cancellations", "email_unsubscribe_claims",
    "email_unsubscribe_continuations", "email_unsubscribe_effects",
    "email_unsubscribe_receipts", "email_unsubscribe_steps",
)


@pytest.fixture(params=VARIANTS)
def variant(request):
    return request.param


@pytest.fixture
def db(variant):
    source = CAPTURE[variant]["tables"]
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("pragma foreign_keys=on")
        # The captured FKs require only this classification key for these tests.
        connection.execute("create table email_classifications(id integer primary key)")
        connection.execute("insert into email_classifications(id) values(11)")
        for name in TABLES:
            connection.execute(source[name]["sql"])
        for name in TABLES:
            # PRAGMA index_list lists explicit indexes in reverse creation order.
            for index in reversed(source[name]["indexes"]):
                if index["sql"] is not None:
                    connection.execute(index["sql"])
            for trigger in source[name]["triggers"]:
                connection.execute(trigger["sql"])
        yield connection


def _insert(db, table, values, *, ignore=False):
    return db.execute(
        f"insert {'or ignore ' if ignore else ''}into {table}"
        f"({','.join(values)}) values({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def _plan(db, **changes):
    return _insert(db, "email_action_plans", {
        "action_plan_id": "plan", "action_plan_version": 1,
        "classification_id": 11, "account_id": "account", "category": "category",
        "classification_source": "model", "confidence": 0.5,
        "model_id": "model", "config_version": "config", "actions_json": "[]",
        "action_parameters_json": "{}", "created_at": "time", **changes,
    })


def _action(db, **changes):
    return _insert(db, "email_actions", {
        "action_id": "action", "action_plan_id": "plan", "classification_id": 11,
        "account_id": "account", "action_type": "archive", "parameters_json": "{}",
        "config_version": "config", "status": "pending", "created_at": "time",
        "updated_at": "time", **changes,
    })


def _claim(db, **changes):
    return _insert(db, "email_unsubscribe_claims", {
        "action_identity": "claim", "effect_digest": "digest", "action_plan_id": "plan",
        "action_plan_version": 1, "classification_id": 11, "account_id": "account",
        "stable_message_identity": "message", "thread_identity": "thread",
        "entry_reference": "entry", "operations_json": "[]", "owner_id": "owner",
        "owner_generation": 1, "lease_token": "lease", "account_updated_at": "time",
        "status": "dispatching", "claimed_at": "time", "updated_at": "time",
        **changes,
    })


def _attempt(db, *, ignore=False, **changes):
    return _insert(db, "email_action_attempts", {
        "action_id": "action", "attempt_number": 1, "status": "done",
        "provider_operation": "operation", "provider_target": "target",
        "provider_result_id": "receipt", "error": "", "started_at": "start",
        "finished_at": "finish", **changes,
    }, ignore=ignore)


def _step(db, *, ignore=False, **changes):
    return _insert(db, "email_unsubscribe_steps", {
        "action_identity": "claim", "effect_digest": "digest", "sequence": 1,
        "operation": "operation", "state": "state", "reference": "reference",
        "created_at": "time", **changes,
    }, ignore=ignore)


def _sequence(db, table):
    return db.execute("select seq from sqlite_sequence where name=?", (table,)).fetchone()[0]


def test_exact_nine_source_declarations_and_metadata(db, variant):
    source = CAPTURE[variant]["tables"]
    assert set(source) == set(TABLES)
    assert sum(len(table["columns"]) for table in source.values()) == 103
    for name in TABLES:
        definition = source[name]
        assert db.execute("select sql from sqlite_schema where type='table' and name=?",
                          (name,)).fetchone()[0] == definition["sql"]
        assert [dict(zip(("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"),
                         row, strict=True)) for row in db.execute(f"pragma table_xinfo({name})")] == definition["columns"]
        assert [dict(zip(("id", "seq", "table", "from", "to", "on_update", "on_delete", "match"),
                         row, strict=True)) for row in db.execute(f"pragma foreign_key_list({name})")] == definition["foreign_keys"]
        indexes = [dict(zip(("seq", "name", "unique", "origin", "partial"), row, strict=True))
                   for row in db.execute(f"pragma index_list({name})")]
        assert indexes == [
            {key: index[key] for key in ("seq", "name", "unique", "origin", "partial")}
            for index in definition["indexes"]
        ]
        for index in definition["indexes"]:
            assert db.execute("select sql from sqlite_schema where type='index' and name=?",
                              (index["name"],)).fetchone()[0] == index["sql"]
            assert [dict(zip(("seqno", "cid", "name", "desc", "coll", "key"), row, strict=True))
                    for row in db.execute(f"pragma index_xinfo({index['name']})")] == index["columns"]
        assert [dict(zip(("type", "name", "tbl_name", "sql"), row, strict=True)) for row in db.execute(
            "select type,name,tbl_name,sql from sqlite_schema where type='trigger' and tbl_name=? order by name",
            (name,),
        )] == sorted(definition["triggers"], key=lambda trigger: trigger["name"])
    assert not db.execute("pragma foreign_key_check").fetchall()


def test_claim_physical_order_and_status_lookup_index_are_variant_specific(db, variant):
    _plan(db)
    _claim(db, audit_agent_run_id=37)
    names = [column["name"] for column in CAPTURE[variant]["tables"]["email_unsubscribe_claims"]["columns"]]
    row = db.execute("select * from email_unsubscribe_claims").fetchone()
    assert dict(zip(names, row, strict=True))["audit_agent_run_id"] == 37
    assert names.index("audit_agent_run_id") == (18 if variant == "actual" else 16)
    index = next(index for index in CAPTURE[variant]["tables"]["email_unsubscribe_claims"]["indexes"]
                 if index["name"] == "idx_email_unsubscribe_claims_status")
    assert [part["name"] for part in index["columns"] if part["key"]] == [
        "status", "updated_at", "action_identity",
    ]
    assert index["columns"][1]["cid"] == names.index("updated_at")


@pytest.mark.parametrize("table", ("email_action_attempts", "email_unsubscribe_steps"))
def test_native_autoincrement_deleted_and_ignored_highwater(db, table):
    _plan(db)
    _action(db)
    _claim(db)
    insert = _attempt if table == "email_action_attempts" else _step
    invalid = {"action_id": None} if table == "email_action_attempts" else {"action_identity": None}
    assert insert(db, id=700).lastrowid == 700
    db.execute(f"delete from {table} where id=700")
    assert insert(db).lastrowid == 701
    assert insert(db, id=900, ignore=True, **invalid).rowcount == 0
    assert _sequence(db, table) == 900
    extra = {"attempt_number": 2} if table == "email_action_attempts" else {"sequence": 2}
    assert insert(db, **extra).lastrowid == 901
    assert _sequence(db, table) == 901


@pytest.mark.parametrize("table", ("email_action_attempts", "email_unsubscribe_steps"))
def test_ignored_max_integer_exhausts_native_allocator(db, table):
    _plan(db)
    _action(db)
    _claim(db)
    insert = _attempt if table == "email_action_attempts" else _step
    invalid = {"action_id": None} if table == "email_action_attempts" else {"action_identity": None}
    assert insert(db, id=1).lastrowid == 1
    assert insert(db, id=2**63 - 1, ignore=True, **invalid).rowcount == 0
    assert _sequence(db, table) == 2**63 - 1
    db.commit()
    extra = {"attempt_number": 2} if table == "email_action_attempts" else {"sequence": 2}
    with pytest.raises(sqlite3.DatabaseError) as exc:
        insert(db, **extra)
    assert exc.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    assert db.execute(f"select id from {table}").fetchall() == [(1,)]


def test_text_primary_keys_allow_multiple_nulls_where_source_check_allows(db):
    _plan(db)
    for _ in range(2):
        _insert(db, "email_unsubscribe_cancellations", {
            "action_identity": None, "decision_json": "{}",
        })
    assert db.execute("select count(*) from email_unsubscribe_cancellations where action_identity is null").fetchone() == (2,)
    for version in (2, 3):
        _plan(db, action_plan_id=None, action_plan_version=version)
    assert db.execute("select count(*) from email_action_plans where action_plan_id is null").fetchone() == (2,)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _insert(db, "email_unsubscribe_cancellations", {
            "action_identity": "  ", "decision_json": "{}",
        })


def test_representative_enums_required_values_and_raw_text(db):
    _plan(db, action_plan_id="plan\0raw", category=" raw\0category ",
          classification_source="agent", actions_json='{"raw":"\\u0000"}')
    _action(db, action_plan_id="plan\0raw", action_type="trash", status="processing",
            provider_target=" raw\0target ", error=" raw\0error ")
    assert db.execute("select category,actions_json from email_action_plans").fetchone() == (
        " raw\0category ", '{"raw":"\\u0000"}',
    )
    assert db.execute("select provider_target,error from email_actions").fetchone() == (
        " raw\0target ", " raw\0error ",
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _action(db, action_id="bad", action_plan_id="plan\0raw", action_type="send")
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        _action(db, action_id="bad", action_plan_id="plan\0raw", status=None)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _plan(db, action_plan_id="bad", action_plan_version=2, classification_source="unknown")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _plan(db, action_plan_id="bad", action_plan_version=2, actions_json="not json")


def test_claim_phase_and_attempt_count_trigger(db):
    _plan(db)
    _action(db)
    _claim(db)
    assert db.execute("select phase,audit_agent_run_id from email_unsubscribe_claims").fetchone() == (
        "prepared", None,
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _claim(db, action_identity="other", phase="unknown")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _claim(db, action_identity="other", audit_agent_run_id=0)
    _attempt(db)
    with pytest.raises(sqlite3.IntegrityError, match="email_action_attempt_count_drift"):
        db.execute("update email_actions set status='done' where action_id='action'")
    db.execute("update email_actions set attempt_count=1,status='done' where action_id='action'")
    assert db.execute("select attempt_count,status from email_actions").fetchone() == (1, "done")
    assert not db.execute("pragma foreign_key_check").fetchall()


def test_declared_unique_membership_and_parent_keys(db):
    _plan(db)
    _action(db)
    _claim(db)
    _attempt(db)
    _step(db)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _attempt(db, id=7)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _step(db, id=7)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _attempt(db, action_id="missing", attempt_number=2)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _step(db, action_identity="missing", sequence=2)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("delete from email_action_plans where action_plan_id='plan'")
