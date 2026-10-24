"""Focused acceptance-runner contracts for portable Task/Run handoffs."""

import json
import math
import sqlite3

import pytest

from scripts.validate_native_copy_lifecycle import _portable_value, _restore_portable_value
from scripts.validate_native_copy_lifecycle import original_rows_unchanged


def test_converter_handoff_metadata_round_trips_tuples_blobs_and_exact_float_bits():
    source = {
        "declaration": (("runs", "CREATE TABLE runs (id INTEGER)"),),
        "identity_edges": [{"source_type": "meeting", "source_id": 4}],
        "sql_facts": (None, 0, False, "", b"\x00\xff", -0.0, math.inf),
    }

    encoded = json.dumps(_portable_value(source), allow_nan=False, sort_keys=True)
    restored = _restore_portable_value(json.loads(encoded))

    assert restored == source
    assert type(restored["declaration"]) is tuple
    assert type(restored["declaration"][0]) is tuple
    assert type(restored["sql_facts"][4]) is bytes
    assert math.copysign(1.0, restored["sql_facts"][5]) == -1.0
    assert restored["sql_facts"][6] == math.inf


@pytest.mark.parametrize("value", [
    {"$task5_type": "tuple", "items": [], "unexpected": True},
    {"$task5_type": "unknown", "items": []},
])
def test_portable_handoff_rejects_malformed_type_tags(value):
    with pytest.raises(ValueError, match="Task 5 metadata type tag"):
        _restore_portable_value(value)


def test_portable_handoff_rejects_non_string_metadata_keys():
    with pytest.raises(TypeError, match="string object keys"):
        _portable_value({1: "not JSON object metadata"})


def test_cross_forward_comparison_uses_declared_business_key_and_schema_marker(tmp_path):
    source_path = tmp_path / "source.sqlite3"
    target_path = tmp_path / "target.sqlite3"
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    for db in (source, target):
        db.executescript("""
            create table task_business_keys (
                id integer primary key, task_id integer not null,
                business_key text not null unique, created_at text, updated_at text
            );
            create table state (
                id integer primary key, record_type text not null,
                legacy_key text not null, payload_json text not null
            );
        """)
    source.execute("insert into task_business_keys values(4, 20, 'meeting:20', 'a', 'b')")
    target.execute("insert into task_business_keys values(900, 20, 'meeting:20', 'a', 'b')")
    source.execute("insert into state values(1, 'service_state', '120', ?)",
                   (json.dumps({"key": "store_schema_version", "value": "v1", "updated_at": "old"}),))
    target.execute("insert into state values(1, 'service_state', '120', ?)",
                   (json.dumps({"key": "store_schema_version", "value": "v1", "updated_at": "new"}),))
    source.commit()
    target.commit()
    with pytest.raises(AssertionError):
        original_rows_unchanged(source_path, target_path)
    assert original_rows_unchanged(
        source_path, target_path,
        natural_key_overrides={"task_business_keys": ["business_key"]},
        ignored_value_columns={"task_business_keys": ["id"]},
        allow_store_schema_marker_timestamp=True,
    ) == 2
    target.execute("update task_business_keys set task_id=21")
    target.commit()
    with pytest.raises(AssertionError):
        original_rows_unchanged(
            source_path, target_path,
            natural_key_overrides={"task_business_keys": ["business_key"]},
            ignored_value_columns={"task_business_keys": ["id"]},
            allow_store_schema_marker_timestamp=True,
        )
    source.close()
    target.close()
