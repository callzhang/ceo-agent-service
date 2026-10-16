"""JSON scalar integers must match the native sqlite3 parameter binder."""
from contextlib import closing
import json
import sqlite3

import pytest

from app import action_storage as codec
from app.state_storage import state_json_field, upsert_state
from tests.test_state_storage import facts, native_insert, pair as pair, required


class NativeInt(int):
    pass


class MaskedInt(int):
    def __int__(self):
        return 0

    def __eq__(self, other):
        return True

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return False


@pytest.mark.parametrize("integer", (int, NativeInt, MaskedInt))
@pytest.mark.parametrize("number", (-(2**63)-1, -(2**63), 0, 1, 2**63-1, 2**63))
def test_integer_codec_matches_native_binder(integer, number):
    value = integer(number)
    with closing(sqlite3.connect(":memory:")) as db:
        try:
            native = db.execute("select ?", (value,)).fetchone()[0]
        except OverflowError:
            for encode in (codec._encoded_sqlite_value, codec._encoded_native_sqlite_value):
                with pytest.raises(OverflowError):
                    encode(value)
            with pytest.raises(OverflowError):
                codec._native_payload({"owner_pid": value})
        else:
            assert native == number
            for encode in (codec._encoded_sqlite_value, codec._encoded_native_sqlite_value):
                assert json.loads(json.dumps(encode(value))) == native
            assert json.loads(codec._native_payload({"owner_pid": value})) == {"owner_pid": native}


@pytest.mark.parametrize("value", (False, True))
def test_published_boolean_json_representation_is_unchanged(value):
    assert codec._encoded_sqlite_value(value) is value
    assert codec._encoded_native_sqlite_value(value) is value
    assert json.loads(codec._native_payload({"value": value}))["value"] is value


@pytest.mark.parametrize("integer", (NativeInt, MaskedInt))
@pytest.mark.parametrize("number", (-(2**63)-1, 2**63))
def test_invalid_integer_lease_candidate_preserves_outer_work(pair, integer, number):
    source, target, variant = pair
    kind = "dispatcher_claim_leases"
    sentinel = {"key": "caller", "value": "pending"}
    native_insert(source, "service_state", sentinel)
    # The target prior write is owned by this same outer transaction.
    upsert_state(target, "service_state", sentinel, {"value": state_json_field("value")})
    values = required(variant, kind, "binding") | {"owner_pid": integer(number)}
    with pytest.raises(OverflowError):
        native_insert(source, kind, values)
    with pytest.raises(OverflowError):
        upsert_state(target, kind, values, {"owner_pid": state_json_field("owner_pid")})
    assert facts(source, kind) == facts(target, kind, True) == []
    assert facts(source, "service_state") == facts(target, "service_state", True)
    assert source.in_transaction and target.in_transaction
