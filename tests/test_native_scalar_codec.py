"""Lossless unpublished native-source scalars; published Reply is separate."""
from contextlib import closing
import json
import math
import random
import sqlite3
import struct

import pytest

from app import action_storage as storage
from app.action_email_definitions import _encoded_value, _encoded_value_check


def test_finite_binary_real_codec_matches_native_affinity_and_bits():
    values = [0.0, -0.0, 5e-324, -5e-324, 2.2250738585072014e-308,
              1.7976931348623157e308, -2.2101102838387424e-85,
              7.294214896064974e-137, 1.0, 1.5]
    rng = random.Random(20261016)
    while len(values) < 5010:
        value = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        if math.isfinite(value):
            values.append(value)
    decoded = _encoded_value("payload", "$.v")
    check = _encoded_value_check("payload", "$.v")
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table native(t text,i integer)")
        db.execute(f"create table target(payload text,raw generated always as ({decoded}) virtual,"
                   f"t text generated always as ({decoded}) virtual,"
                   f"i integer generated always as ({decoded}) virtual,check({check}))")
        for value in values:
            payload = json.dumps({"v": storage._encoded_native_sqlite_value(value)}, allow_nan=False)
            db.execute("delete from native")
            db.execute("delete from target")
            db.execute("insert into native values(?,?)", (value, value))
            db.execute("insert into target(payload) values(?)", (payload,))
            raw = db.execute("select raw from target").fetchone()[0]
            assert struct.pack(">d", raw) == struct.pack(">d", value)
            assert db.execute("select t,typeof(t),i,typeof(i) from target").fetchone() == db.execute(
                "select t,typeof(t),i,typeof(i) from native").fetchone()
        assert db.execute("pragma quick_check").fetchone() == ("ok",)


@pytest.mark.parametrize("tag", (
    {"t": "real", "m": 1, "e": -1075},
    {"t": "real", "m": 1, "e": 972},
    {"t": "real", "m": 2**53, "e": 0},
    {"t": "real", "m": -(2**53), "e": 0},
    {"t": "real", "m": 1.5, "e": 0},
    {"t": "real", "m": "1", "e": 0},
    {"t": "real", "m": 1, "e": 0.0},
    {"t": "real", "m": 1, "e": 0, "extra": 1},
    {"t": "real", "m": 1},
    {"t": "real", "e": 0},
))
def test_malformed_finite_real_tags_are_refused(tag):
    with closing(sqlite3.connect(":memory:")) as db:
        check = _encoded_value_check("payload", "$.v")
        db.execute(f"create table target(payload text,check({check}))")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into target values(?)", (json.dumps({"v": tag}),))


