"""Unpublished native scalar encoding follows sqlite3's underlying binding."""
import math
import sqlite3

import pytest

from app.action_storage import _encoded_native_sqlite_value
from app.state_storage import insert_state, update_state, upsert_state, update_state_rows, state_json_field
from tests.test_state_storage import facts, native_insert, pair as pair, required


class RatioFloat(float):
    def as_integer_ratio(self):
        return (1, 1)


class ConvertedFloat(float):
    def __float__(self):
        return 99.0

    def __eq__(self, other):
        return True


class ChangedBytes(bytes):
    def __bytes__(self):
        return b"changed"


class ChangedBytearray(bytearray):
    def __bytes__(self):
        return b"changed"


class ChangedString(str):
    def __str__(self):
        return "changed"

    def __eq__(self, other):
        return True


class Unsupported:
    def __eq__(self, other):
        return True


VALUES = (
    RatioFloat(1.5), RatioFloat(-1.5), RatioFloat(0.0), RatioFloat(-0.0),
    RatioFloat(math.inf), RatioFloat(-math.inf), RatioFloat(math.nan),
    ConvertedFloat(2.5), ConvertedFloat(0.0),
    ChangedBytes(b"original"), ChangedBytearray(b"original"),
    ChangedString("original"), memoryview(b"original"),
    memoryview(b"abcdef")[::2], Unsupported(),
)


@pytest.mark.parametrize("value", VALUES)
def test_encoder_matches_real_sqlite_bound_scalar(value):
    with sqlite3.connect(":memory:") as db:
        try:
            bound = db.execute("select ?", (value,)).fetchone()[0]
        except (BufferError, sqlite3.ProgrammingError) as exc:
            with pytest.raises(type(exc)):
                _encoded_native_sqlite_value(value)
        else:
            assert _encoded_native_sqlite_value(value) == _encoded_native_sqlite_value(bound)


@pytest.mark.parametrize("value", VALUES)
@pytest.mark.parametrize("operation", ("insert", "point", "upsert", "bulk"))
def test_state_native_binding_and_failure_leave_prior_work(pair, value, operation):
    source, target, _variant = pair
    if operation != "insert":
        seed = {"key": "key", "value": "before", "updated_at": "t"}
        native_insert(source, "service_state", seed, -7)
        insert_state(target, "service_state", seed, source_rowid=-7)
    before = facts(source, "service_state")
    for db in (source, target):
        db.execute("create table caller_work(value)")
        db.execute("insert into caller_work values('keep')")

    def native():
        if operation == "insert":
            return source.execute(
                "insert into service_state(key,value,updated_at) values('key',?,'t')", (value,)
            ).rowcount
        if operation == "point":
            return source.execute(
                "update or replace service_state set value=? where rowid=-7", (value,)
            ).rowcount
        if operation == "upsert":
            return source.execute(
                "insert into service_state(key,value,updated_at) values('key',?,'t') "
                "on conflict(key) do update set value=excluded.value", (value,)
            ).rowcount
        return source.execute("update service_state set value=? where key='key'", (value,)).rowcount

    def candidate():
        if operation == "insert":
            return insert_state(target, "service_state", {
                "key": "key", "value": value, "updated_at": "t",
            }).rowcount
        if operation == "point":
            return update_state(target, "service_state", -7, {"value": value}, replace=True)
        if operation == "upsert":
            return upsert_state(target, "service_state", {
                "key": "key", "value": value, "updated_at": "t",
            }, {"value": state_json_field("value")})
        return update_state_rows(target, "service_state", {"value": value},
                                 where_sql="key='key'")
    try:
        count = native()
    except (BufferError, sqlite3.ProgrammingError, sqlite3.IntegrityError) as exc:
        with pytest.raises(type(exc)):
            candidate()
        assert facts(source, "service_state") == before == facts(target, "service_state", True)
    else:
        assert candidate() == count == 1
        assert facts(source, "service_state") == facts(target, "service_state", True)
    for db in (source, target):
        assert db.in_transaction
        assert db.execute("select value from caller_work").fetchone()[0] == "keep"


@pytest.mark.parametrize("operation", ("insert", "point"))
def test_subclass_cannot_change_native_generation_constraint(pair, operation):
    source, target, variant = pair
    values = required(variant, "dispatcher_claim_leases", "generation")
    if operation == "point":
        native_insert(source, "dispatcher_claim_leases", values, -7)
        insert_state(target, "dispatcher_claim_leases", values, source_rowid=-7)
    before = facts(source, "dispatcher_claim_leases")
    value = RatioFloat(-1.5)
    with pytest.raises(sqlite3.IntegrityError):
        if operation == "insert":
            native_insert(source, "dispatcher_claim_leases", values | {"generation": value}, -7)
        else:
            source.execute("update or replace dispatcher_claim_leases set generation=? where rowid=-7", (value,))
    with pytest.raises(sqlite3.IntegrityError):
        if operation == "insert":
            insert_state(target, "dispatcher_claim_leases", values | {"generation": value}, source_rowid=-7)
        else:
            update_state(target, "dispatcher_claim_leases", -7, {"generation": value}, replace=True)
    assert facts(source, "dispatcher_claim_leases") == before == facts(target, "dispatcher_claim_leases", True)
