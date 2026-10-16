"""Lossless unpublished native-source scalars; published Reply is separate."""
from contextlib import closing
import json
import math
import random
import sqlite3
import struct

from tests.test_email_action_schema_release import source as source, _prepare
from app.provider_state_storage import update_important_signals
import pytest

from app import action_storage as storage
from app.action_email_definitions import _encoded_value, _encoded_value_check
from app.configs_definitions import CONFIG_PROJECTIONS
from app.configs_storage import create_configs_schema, insert_config, update_config
from app.schedules_storage import create_schedules_schema
from tests.configs_source_fixtures import source_db, insert


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("field,value", (
    ("enabled", -2.2101102838387424e-85),
    ("display_name", 7.294214896064974e-137),
))
def test_configs_native_real_insert_and_update_match_literal_source(variant, field, value):
    values = dict(account_id="synthetic", target_type="direct", target_id="target",
                  display_name="test", trigger_mode="all", enabled=1)
    values[field] = value
    with source_db(variant) as source, closing(sqlite3.connect(":memory:")) as target:
        insert(source, "wechat_reply_scopes", values)
        storage.create_action_schema(target, include_reply=True,
            reply_variant="fresh_memory_fk", include_email=True,
            include_followup_sync=True, include_skill_config_receipts=True)
        create_schedules_schema(target)
        create_configs_schema(target)
        target.execute("begin")
        rowid = insert_config(target, "wechat_reply_scopes", values).lastrowid
        for update, next_value in ((False, value), (True, -value)):
            if update:
                source.execute(f"update wechat_reply_scopes set {field}=?", (next_value,))
                assert update_config(target, "wechat_reply_scopes", rowid, {field: next_value}) == 1
            expected = source.execute(f"select {field},typeof({field}) from wechat_reply_scopes").fetchone()
            actual = target.execute(f"select {field},typeof({field}) from (" +
                CONFIG_PROJECTIONS["wechat_reply_scopes"] + ")").fetchone()
            assert actual == expected


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


def test_native_reals_survive_paired_forward_current_writes_and_inverse(source):
    first = 2.2101102838387424e-85
    second = -first
    with closing(sqlite3.connect(source)) as db:
        db.execute("update email_action_plans set confidence=?", (first,))
        db.execute("insert into wechat_reply_scopes(account_id,target_type,target_id,"
                   "display_name,trigger_mode,enabled) values('synthetic','direct','target','test','all',?)",
                   (first,))
        db.execute("insert into email_provider_observations(account_id,stable_message_identity,"
                   "state,provider_folder_id,important,important_signals_json,observed_at) "
                   "values('synthetic','message','available','inbox',1,?,'time')", (first,))
        source_provider_signal = db.execute("select important_signals_json from email_provider_observations "
                                          "where account_id='synthetic'").fetchone()[0]
        original_schema = db.execute("select type,name,tbl_name,sql from sqlite_schema "
                                     "where sql is not null order by type,name").fetchall()
        db.commit()
    release = _prepare(source)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select email_confidence,typeof(email_confidence) from actions "
                          "where record_type='email_action_plans'").fetchone() == (first, "real")
        assert db.execute("select important_signals_json,typeof(important_signals_json) from provider_state "
                          "where account_id='synthetic'").fetchone() == (source_provider_signal, "text")
        assert db.execute("select enabled,typeof(enabled) from (" +
            CONFIG_PROJECTIONS["wechat_reply_scopes"] + ")").fetchone() == (first, "real")
        db.execute("begin immediate")
        identity = db.execute("select cast(legacy_key as integer) from actions "
                              "where record_type='email_action_plans'").fetchone()[0]
        storage.update_email_action(db, "email_action_plans", identity, {"confidence": first * 3})
        key = db.execute("select cast(legacy_key as integer) from configs "
                         "where record_type='wechat_reply_scopes'").fetchone()[0]
        assert update_config(db, "wechat_reply_scopes", key, {"enabled": second}) == 1
        assert update_important_signals(db, "synthetic", "message", 1, second)
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select confidence,typeof(confidence) from email_action_plans").fetchone() == (first * 3, "real")
        assert db.execute("select enabled,typeof(enabled) from wechat_reply_scopes").fetchone() == (second, "real")
        expected_provider_signal = db.execute("select cast(? as text)", (second,)).fetchone()[0]
        assert db.execute("select important_signals_json,typeof(important_signals_json),important "
                          "from email_provider_observations where account_id='synthetic'").fetchone() == (
                              expected_provider_signal, "text", 1)
        assert db.execute("select type,name,tbl_name,sql from sqlite_schema "
                          "where sql is not null order by type,name").fetchall() == original_schema
        assert db.execute("pragma quick_check").fetchone() == ("ok",)
    release.apply()
    release.rollback()
