"""Literal source metadata for the approved config/provider/schedule/state groups."""

from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3

import pytest

SPEC_DIR = Path(__file__).parents[1] / "docs/superpowers/specs"
CAPTURE_PATH = SPEC_DIR / "2026-10-15-database-redesign-config-state-native-source.json"
TARGETS = {"configs", "provider_state", "schedules", "state"}
LEGACY_CONVERSATION_FIELDS = {
    "in_handoff", "handoff_message_id", "handoff_message_create_time",
    "manual_clear_message_id",
}


def capture():
    assert CAPTURE_PATH.is_file(), "missing actual/published-fresh source metadata"
    return json.loads(CAPTURE_PATH.read_text())


def mapped_rows():
    with (SPEC_DIR / "2026-10-11-database-redesign-field-map.csv").open() as stream:
        return [row for row in csv.DictReader(stream) if row["target_table"] in TARGETS]


def pragma(db, kind, name):
    quoted = '"' + name.replace('"', '""') + '"'
    cursor = db.execute(f"pragma {kind}({quoted})")
    return [dict(zip((c[0] for c in cursor.description), row, strict=True)) for row in cursor]


def test_complete_source_capture_exists():
    data = capture()
    assert data["source_binding"]["producer_before"] == data["source_binding"]["producer_after"]
    assert data["source_binding"]["published_code"] == "7dadc492c1c62d5b0624f2f97dd0c89f4f358646"
    assert data["source_binding"]["source_access"] == "mode=ro; query_only; one read transaction; closed"
    assert data["source_binding"]["temporary_source_removed"] is True
    assert {target: len(names) for target, names in data["groups"].items()} == {
        "configs": 15, "provider_state": 2, "schedules": 1, "state": 16,
    }
    assert sum(len(table["columns"]) for table in data["actual"]["tables"].values()) == 238
    assert not ({"rows", "records", "values"} & set(data["actual"]))


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_all_source_fields_match_approved_map(variant):
    data = capture()
    expected = {(r["old_table"], r["old_column"]):
                (r["sql_type"], int(r["not_null"]), int(r["primary_key_position"]))
                for r in mapped_rows()}
    if variant == "fresh":
        expected = {key: value for key, value in expected.items()
                    if not (key[0] == "conversations" and key[1] in LEGACY_CONVERSATION_FIELDS)}
    tables = data[variant]["tables"]
    actual = {(name, c["name"]): (c["type"], c["notnull"], c["pk"])
              for name, table in tables.items() for c in table["columns"]}
    assert len(expected) == len(actual) == (238 if variant == "actual" else 234)
    assert expected == actual
    assert set(tables) == {name for names in data["groups"].values() for name in names}


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_literal_native_declarations_reconstruct_exactly(variant):
    tables = capture()[variant]["tables"]
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        for table in tables.values():
            db.execute(table["sql"])
        for table in tables.values():
            for index in reversed(table["indexes"]):
                if index["sql"]:
                    db.execute(index["sql"])
            for trigger in table["triggers"]:
                db.execute(trigger["sql"])
        for name, table in tables.items():
            assert db.execute("select sql from sqlite_schema where name=?", (name,)).fetchone()[0] == table["sql"]
            assert pragma(db, "table_xinfo", name) == table["columns"]
            assert pragma(db, "foreign_key_list", name) == table["foreign_keys"]
            layout = next(row for row in db.execute("pragma table_list") if row[0] == "main" and row[1] == name)
            assert bool(layout[4]) == table["identity"]["without_rowid"]
            assert pragma(db, "index_list", name) == [
                {k: index[k] for k in ("seq", "name", "unique", "origin", "partial")}
                for index in table["indexes"]
            ]
            for index in table["indexes"]:
                assert pragma(db, "index_xinfo", index["name"]) == index["columns"]
                sql_row = db.execute("select sql from sqlite_schema where name=?", (index["name"],)).fetchone()
                if sql_row is None:
                    assert table["identity"]["without_rowid"] and index["origin"] == "pk"
                    assert index["sql"] is None
                else:
                    assert sql_row[0] == index["sql"]
            assert [{"name": row[0], "sql": row[1]} for row in db.execute(
                "select name,sql from sqlite_schema where type='trigger' and tbl_name=? order by name", (name,)
            )] == table["triggers"]
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"


def test_incoming_fk_inventory_and_source_sequence_identity():
    data = capture()
    for variant in ("actual", "fresh"):
        source = data[variant]
        names = set(source["tables"])
        assert all(edge["table"] in names for edge in source["incoming_foreign_keys"])
        outgoing = {(name, fk["id"], fk["seq"]): fk for name, table in source["tables"].items()
                    for fk in table["foreign_keys"] if fk["table"] in names}
        incoming_internal = {(e["child_table"], e["id"], e["seq"]):
                             {k: v for k, v in e.items() if k != "child_table"}
                             for e in source["incoming_foreign_keys"] if e["child_table"] in names}
        assert incoming_internal == outgoing
        for name, table in source["tables"].items():
            assert table["identity"]["autoincrement"] == ("autoincrement" in table["sql"].lower())
            assert all(row["name"] == name for row in table["sequences"])
