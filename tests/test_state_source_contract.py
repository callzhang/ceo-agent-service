"""Native SQLite source contracts for the approved sixteen state families."""
import sqlite3

import pytest

from tests.configs_source_fixtures import capture, source_db

STATE_TABLES = tuple(capture()["groups"]["state"])


def _q(value):
    return '"' + value.replace('"', '""') + '"'


def _columns(variant, table):
    return capture()[variant]["tables"][table]["columns"]


def _row(variant, table, suffix):
    values = {}
    for column in _columns(variant, table):
        if column["pk"] or (column["notnull"] and column["dflt_value"] is None):
            assert column["type"] in ("TEXT", "INTEGER")
            values[column["name"]] = (
                f'{column["name"]}:{suffix}' if column["type"] == "TEXT" else 1
            )
    return values


def _insert(db, table, values, rowid=None):
    names = ("rowid", *values)
    return db.execute(
        f'insert into {_q(table)} ({",".join(map(_q, names))}) '
        f'values ({",".join("?" for _ in names)})',
        (rowid, *values.values()),
    ).lastrowid


@pytest.fixture(params=("actual", "fresh"))
def native(request):
    with source_db(request.param) as db:
        yield request.param, db


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_hidden_rowids_are_signed_and_reusable(native, table):
    variant, db = native
    definition = capture()[variant]["tables"][table]
    assert definition["identity"] == {
        "without_rowid": False, "autoincrement": False,
        "integer_primary_key_rowid_alias": False,
    }
    assert definition["foreign_keys"] == []
    assert definition["triggers"] == []
    assert _insert(db, table, _row(variant, table, "negative"), -7) == -7
    assert _insert(db, table, _row(variant, table, "zero"), 0) == 0
    assert _insert(db, table, _row(variant, table, "first")) == 1
    db.execute(f'delete from {_q(table)} where rowid=1')
    assert _insert(db, table, _row(variant, table, "reuse")) == 1
    assert db.execute(
        "select 1 from sqlite_sequence where name=?", (table,)
    ).fetchone() is None
    assert db.execute("pragma quick_check").fetchone() == ("ok",)


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_keys_preserve_nullability_and_uniqueness(native, table):
    variant, db = native
    columns = _columns(variant, table)
    primary = [column for column in columns if column["pk"]]
    assert primary and all(column["type"] == "TEXT" for column in primary)
    values = _row(variant, table, "original")
    _insert(db, table, values, -7)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        _insert(db, table, values, 99)
    if len(primary) == 1 and not primary[0]["notnull"]:
        for suffix in ("null-a", "null-b"):
            _insert(db, table, _row(variant, table, suffix) | {
                primary[0]["name"]: None,
            })
        assert db.execute(
            f'select count(*) from {_q(table)} where {_q(primary[0]["name"])} is null'
        ).fetchone() == (2,)
    else:
        assert all(column["notnull"] for column in primary)
        for column in primary:
            with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
                _insert(db, table, _row(variant, table, "missing") | {
                    column["name"]: None,
                })
    for value in ("Key", "key"):
        _insert(db, table, _row(variant, table, value) | {
            column["name"]: value for column in primary
        })


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_affinity_keeps_blob_and_fractional_values(native, table):
    variant, db = native
    columns = _columns(variant, table)
    assert all(column["type"] in ("TEXT", "INTEGER") for column in columns)
    numeric = {
        column["name"]: 1 if column["type"] == "TEXT" else "1"
        for column in columns
    }
    _insert(db, table, numeric, 1)
    binary = {
        column["name"]: b"1" if column["type"] == "TEXT" else "1"
        for column in columns
    }
    _insert(db, table, binary, 2)
    fractional = {
        column["name"]: "fraction:" + column["name"]
        if column["type"] == "TEXT" else 1.5
        for column in columns
    }
    _insert(db, table, fractional, 3)
    for rowid, values, text_type, integer_type in (
        (1, numeric, "text", "integer"),
        (2, binary, "blob", "integer"),
        (3, fractional, "text", "real"),
    ):
        for column in columns:
            name = column["name"]
            stored = db.execute(
                f'select {_q(name)},typeof({_q(name)}) from {_q(table)} where rowid=?',
                (rowid,),
            ).fetchone()
            expected_type = text_type if column["type"] == "TEXT" else integer_type
            expected_value = (
                ("1" if rowid == 1 else values[name]) if column["type"] == "TEXT"
                else 1.5 if rowid == 3 else 1
            )
            assert stored == (expected_value, expected_type), (table, name, rowid)


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_not_null_columns_refuse_null(native, table):
    variant, db = native
    for column in _columns(variant, table):
        if column["notnull"]:
            with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
                _insert(db, table, _row(variant, table, column["name"]) | {
                    column["name"]: None,
                })


@pytest.mark.parametrize(("table", "column", "value"), (
    ("dispatcher_claim_leases", "generation", -1),
    ("email_scan_cursors", "uidvalidity", 0),
    ("email_scan_cursors", "last_seen_uid", -1),
))
def test_native_state_declared_checks_refuse_invalid_values(native, table, column, value):
    variant, db = native
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insert(db, table, _row(variant, table, "invalid") | {column: value})


def test_native_state_distinct_integer_domains_are_not_unified(native):
    variant, db = native
    _insert(db, "email_historical_traversals",
            _row(variant, "email_historical_traversals", "historical") | {
                "uidvalidity": None, "last_seen_uid": -1,
            })
    _insert(db, "conversations", _row(variant, "conversations", "chat") | {
        "single_chat": 2,
    })
    _insert(db, "dispatcher_claim_leases",
            _row(variant, "dispatcher_claim_leases", "lease") | {"owner_pid": -1})


def test_native_state_actual_and_fresh_conversation_layouts_are_explicit(native):
    variant, db = native
    legacy = {
        "in_handoff", "handoff_message_id", "handoff_message_create_time",
        "manual_clear_message_id",
    }
    names = {column["name"] for column in _columns(variant, "conversations")}
    assert (legacy <= names) if variant == "actual" else not (legacy & names)
    state_fields = sum(len(_columns(variant, table)) for table in STATE_TABLES)
    assert state_fields == (91 if variant == "actual" else 87)
    rowid = _insert(db, "conversations", _row(variant, "conversations", "default"))
    if variant == "actual":
        assert db.execute(
            "select in_handoff,handoff_message_id,handoff_message_create_time,"
            "manual_clear_message_id from conversations where rowid=?", (rowid,),
        ).fetchone() == (0, None, None, None)


@pytest.mark.parametrize("table", STATE_TABLES)
def test_native_state_defaults_remain_literal(native, table):
    variant, db = native
    values = _row(variant, table, "defaults")
    before = db.execute("select current_timestamp").fetchone()[0]
    rowid = _insert(db, table, values)
    after = db.execute("select current_timestamp").fetchone()[0]
    for column in _columns(variant, table):
        name = column["name"]
        if name in values:
            continue
        actual = db.execute(
            f'select {_q(name)} from {_q(table)} where rowid=?', (rowid,)
        ).fetchone()[0]
        default = column["dflt_value"]
        if default is None:
            assert actual is None, (table, name)
        elif default.lower() == "current_timestamp":
            assert before <= actual <= after, (table, name)
        else:
            expected = db.execute("select " + default).fetchone()[0]
            assert actual == expected, (table, name)


def test_native_org_lookup_indexes_allow_duplicate_names_and_open_ids(native):
    variant, db = native
    for suffix in ("one", "two"):
        _insert(db, "org_user_profiles",
                _row(variant, "org_user_profiles", suffix) | {
                    "name": "Same name", "open_dingtalk_id": "same-open-id",
                })
    assert db.execute(
        "select count(*) from org_user_profiles where name='Same name' "
        "and open_dingtalk_id='same-open-id'"
    ).fetchone() == (2,)
    assert all(
        not row[2] for row in db.execute("pragma index_list(org_user_profiles)")
        if row[3] == "c"
    )
