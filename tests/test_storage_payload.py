"""Final physical payload ABI; real layouts and native SQLite values."""

import json
import math
import sqlite3

import pytest

from app.action_storage import _encoded_native_sqlite_value
from app.storage_payload import ACTION_RECORD_TYPES, SOURCE_ACTION_RECORD_TYPES, TASK_RECORD_TYPES, PayloadLayout
from app.task20_definitions import TASK20_COLUMNS, TASK20_PAYLOAD_FIELDS


def test_real_type_codes_are_stable_and_include_empty_supported_families():
    assert len(SOURCE_ACTION_RECORD_TYPES.names) == 61
    assert ACTION_RECORD_TYPES.names[:-1] == SOURCE_ACTION_RECORD_TYPES.names
    assert ACTION_RECORD_TYPES.name(62) == "candidate_execution_alias"
    assert ACTION_RECORD_TYPES.code("review_candidates") == 1
    assert ACTION_RECORD_TYPES.code("business_task_signals") == 58
    assert ACTION_RECORD_TYPES.name(61) == "email_historical_operations"
    assert TASK_RECORD_TYPES.names == tuple(TASK20_COLUMNS)
    assert TASK_RECORD_TYPES.code("reply_tasks") == 1
    assert TASK_RECORD_TYPES.name(20) == "workbench_attachments"
    with pytest.raises(KeyError):
        ACTION_RECORD_TYPES.code("unknown_action")
    for invalid in (0, 63, True, "1"):
        with pytest.raises(ValueError):
            ACTION_RECORD_TYPES.name(invalid)


def test_layout_uses_declared_order_and_does_not_infer_dictionary_order():
    layout = PayloadLayout(TASK20_PAYLOAD_FIELDS["business_object_tasks"])
    values = dict.fromkeys(reversed(layout.fields), None)
    values[layout.fields[0]] = "first"
    packed = layout.pack(values, scalar_codec=_encoded_native_sqlite_value)
    assert json.loads(packed)[0] == "first"
    assert layout.unpack(packed) == {name: values[name] for name in layout.fields}
    assert layout.path(layout.fields[0]) == "$[0]"
    with pytest.raises(KeyError):
        layout.path("not_a_source_field")
    with pytest.raises(ValueError):
        layout.pack({**values, "new_field": 1}, scalar_codec=_encoded_native_sqlite_value)
    with pytest.raises(ValueError):
        layout.pack({}, scalar_codec=_encoded_native_sqlite_value)


def test_actual_scalar_codec_keeps_blob_real_integer_negative_zero_and_infinities():
    from app.action_email_definitions import _encoded_value

    layout = PayloadLayout(("integer", "real", "negative_zero", "blob", "positive_inf", "negative_inf", "text", "null"))
    source = dict(zip(layout.fields, (0, 0.0, -0.0, b"\x00\xff", math.inf, -math.inf, '{"nested":"文本"}', None), strict=True))
    packed = layout.pack(source, scalar_codec=_encoded_native_sqlite_value)
    expressions = [_encoded_value("payload_json", layout.path(name)) for name in layout.fields]
    with sqlite3.connect(":memory:") as db:
        db.execute("create table sample(payload_json blob not null check(" + layout.shape_sql() + "))")
        db.execute("insert into sample values(jsonb(?))", (packed,))
        row = db.execute("select " + ",".join(expressions) + " from sample").fetchone()
        storage_types = db.execute("select " + ",".join("typeof(" + expression + ")" for expression in expressions) + " from sample").fetchone()
        assert storage_types == ("integer", "real", "real", "blob", "real", "real", "text", "null")
        assert row == tuple(source.values())
        assert math.copysign(1.0, row[2]) == -1.0
        values_json = db.execute("select json(payload_json) from sample").fetchone()[0]
        assert layout.unpack(values_json) == dict(zip(layout.fields, json.loads(packed), strict=True))


@pytest.mark.parametrize("bad", ("{}", "[]", "[1]", "[1,2,3]"))
def test_final_shape_rejects_objects_or_wrong_arity(bad):
    layout = PayloadLayout(("source_type", "source_ref"))
    with sqlite3.connect(":memory:") as db:
        db.execute("create table sample(payload_json blob not null check(" + layout.shape_sql() + "))")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into sample values(jsonb(?))", (bad,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into sample values(?)", ("[1,2]",))


def test_final_shape_rejects_invalid_blob_without_null_check_escape():
    layout = PayloadLayout(("source_ref",))
    with sqlite3.connect(":memory:") as db:
        db.execute("create table sample(payload_json blob not null check(" + layout.shape_sql() + "))")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into sample values(?)", (b"not jsonb",))
        db.execute("insert into sample values(jsonb(?))", ("[null]",))
    for malformed in ("{}", "[]", "[1,2]"):
        with pytest.raises(ValueError):
            layout.unpack(malformed)


def test_jsonb_slot_update_preserves_other_values_and_native_encoded_types():
    layout = PayloadLayout(("count", "bytes", "clock"))
    packed = layout.pack(dict(count=2, bytes=b"\x00\xff", clock="before"), scalar_codec=_encoded_native_sqlite_value)
    with sqlite3.connect(":memory:") as db:
        db.execute("create table sample(payload_json blob not null check(" + layout.shape_sql() + "))")
        db.execute("insert into sample values(jsonb(?))", (packed,))
        db.execute("update sample set payload_json=" + layout.assignments_sql(
            "payload_json", {"count": "json_quote(7)", "clock": "json_quote('after')"}
        ))
        row = db.execute("select json(payload_json),typeof(payload_json) from sample").fetchone()
    assert row[1] == "blob"
    assert layout.unpack(row[0]) == {"count": 7, "bytes": {"t": "blob", "v": "00ff"}, "clock": "after"}
    with pytest.raises(KeyError):
        layout.assignments_sql("payload_json", {"new_field": "1"})


def test_type_tag_has_one_writable_integer_and_generated_semantic_name():
    with sqlite3.connect(":memory:") as db:
        db.execute("create table sample(" + ",".join(TASK_RECORD_TYPES.declarations()) + ")")
        db.execute("insert into sample(_kind) values(?)", (TASK_RECORD_TYPES.code("reply_tasks"),))
        assert db.execute("select _kind,record_type,typeof(_kind) from sample").fetchone() == (1, "reply_tasks", "integer")
        assert [r[1] for r in db.execute("pragma table_xinfo(sample)") if r[6] == 0] == ["_kind"]
        with pytest.raises(sqlite3.OperationalError, match="generated column"):
            db.execute("insert into sample(record_type) values('reply_tasks')")
        for unknown in (0, 21, "invented", None):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("insert into sample(_kind) values(?)", (unknown,))
