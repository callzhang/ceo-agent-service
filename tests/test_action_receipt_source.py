"""Independent source oracle for the next actions receipt migration.

Exercise captured declarations, never target helpers, so subsequent forward and
inverse tests have a measured source contract rather than copied assumptions.
"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest


INVENTORY = Path(__file__).parents[1] / (
    "docs/superpowers/specs/2026-10-13-database-redesign-phase5b-inventory.json"
)
SOURCE = json.loads(INVENTORY.read_text())["tables"]
RECEIPT_TYPES = tuple(name for name in SOURCE if name != "actions")


@pytest.fixture
def source_db():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        # Only these two columns are read by the captured FK and type guards.
        db.execute("create table runs(id integer primary key,record_type text)")
        db.executemany("insert into runs values(?,?)", (
            (1, "agent_runs"), (2, "agent_runtime_attempts"), (3, "agent_runs"),
        ))
        for kind in RECEIPT_TYPES:
            objects = SOURCE[kind]["objects"]
            db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
            for obj in objects:
                if obj["type"] != "table" and obj["sql"]:
                    db.execute(obj["sql"])
        yield db


def source_values(kind, *, token="operation", run_id=1):
    if kind == "external_action_results":
        return dict(external_action_key=token, business_object_key="business",
                    action_identity="identity", operation="send",
                    target_identifiers_json="not parsed JSON",
                    provider_result_json=' { "原始": [2, 1] } ',
                    result_digest="digest", first_agent_run_id=run_id)
    if kind == "agent_effect_intents":
        return dict(agent_run_id=run_id, authorization_id=token, action_index=0,
                    receipt_operation_id=token, capability="message", operation="send",
                    operation_digest="digest", arguments_digest="arguments",
                    target_identifiers_json="not parsed JSON")
    return dict(agent_run_id=run_id, receipt_id="receipt", operation_id=token,
                cli="native", command_path="send", command_digest="digest",
                exit_code=0, completed=2, persisted=-1, safe_to_confirm=3)


def insert_source(db, kind, values, *, ignore=False):
    fields = tuple(values)
    return db.execute(
        f"insert {'or ignore ' if ignore else ''}into {kind}"
        f"({','.join(fields)}) values({','.join('?' for _ in fields)})",
        tuple(values.values()),
    )


def test_text_keys_keep_binary_identity_and_raw_receipt_text(source_db):
    for key in ("1", "01", "A", "a", " key "):
        insert_source(source_db, "external_action_results", source_values(
            "external_action_results", token=key,
        ))
    rows = source_db.execute(
        "select external_action_key,provider_result_json,target_identifiers_json "
        "from external_action_results order by external_action_key"
    ).fetchall()
    assert len(rows) == 5
    assert {row[0] for row in rows} == {"1", "01", "A", "a", " key "}
    assert all(row[1:] == (' { "原始": [2, 1] } ', "not parsed JSON") for row in rows)
    assert not source_db.execute("pragma foreign_key_check").fetchall()


def test_sqlite_text_primary_key_allows_null_but_rejects_blank(source_db):
    # NULL is legal in the source declaration. A migration must explicitly
    # refuse unrepresentable source keys before mutation, never invent an ID.
    for _ in range(2):
        insert_source(source_db, "external_action_results", source_values(
            "external_action_results", token=None,
        ))
    assert source_db.execute(
        "select count(*) from external_action_results where external_action_key is null"
    ).fetchone()[0] == 2
    for key in ("", "   "):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            insert_source(source_db, "external_action_results", source_values(
                "external_action_results", token=key,
            ))


@pytest.mark.parametrize("kind", RECEIPT_TYPES)
@pytest.mark.parametrize("run_id", [2, 404])
def test_receipts_require_existing_logical_runs(source_db, kind, run_id):
    with pytest.raises(sqlite3.IntegrityError):
        insert_source(source_db, kind, source_values(kind, run_id=run_id))
    insert_source(source_db, kind, source_values(kind))
    run_field = "first_agent_run_id" if kind == "external_action_results" else "agent_run_id"
    with pytest.raises(sqlite3.IntegrityError):
        source_db.execute(f"update {kind} set {run_field}=?", (run_id,))
    assert source_db.execute(f"select {run_field} from {kind}").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["agent_effect_intents", "agent_execution_receipts"])
def test_autoincrement_retains_deleted_and_ignored_insert_watermarks(source_db, kind):
    first = source_values(kind)
    first["id"] = 560
    insert_source(source_db, kind, first)
    source_db.execute(f"delete from {kind}")
    assert insert_source(source_db, kind, source_values(kind)).lastrowid == 561
    assert insert_source(source_db, kind, source_values(kind), ignore=True).rowcount == 0
    assert source_db.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone()[0] == 562
    assert insert_source(source_db, kind, source_values(kind, token="next")).lastrowid == 563


@pytest.mark.parametrize("kind", ["agent_effect_intents", "agent_execution_receipts"])
def test_autoincrement_exhaustion_does_not_reuse_deleted_maximum(source_db, kind):
    values = source_values(kind)
    values["id"] = 2**63 - 1
    insert_source(source_db, kind, values)
    source_db.execute(f"delete from {kind}")
    # Keep the previous transaction; SQLITE_FULL rolls back its transaction.
    source_db.commit()
    with pytest.raises(sqlite3.OperationalError) as error:
        insert_source(source_db, kind, source_values(kind))
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    assert source_db.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone()[0] == 2**63 - 1


def test_independent_receipt_and_intent_keys_do_not_require_other_receipts(source_db):
    insert_source(source_db, "agent_execution_receipts", source_values("agent_execution_receipts"))
    values = source_values("agent_effect_intents")
    values["external_action_key"] = "no-global-receipt"
    insert_source(source_db, "agent_effect_intents", values)
    assert source_db.execute("select count(*) from external_action_results").fetchone()[0] == 0
    assert not source_db.execute("pragma foreign_key_check").fetchall()
    flags = source_db.execute(
        "select completed,persisted,safe_to_confirm,effect_counted from agent_execution_receipts"
    ).fetchone()
    assert flags == (2, -1, 3, 0)
    defaults = source_db.execute(
        "select state,result_digest,exit_code,dispatched_at,acknowledged_at from agent_effect_intents"
    ).fetchone()
    assert defaults == ("prepared", "", None, "", "")


def test_intent_has_two_distinct_run_scoped_unique_keys(source_db):
    insert_source(source_db, "agent_effect_intents", source_values("agent_effect_intents"))
    for changed in ("authorization_id", "receipt_operation_id"):
        values = source_values("agent_effect_intents")
        values[changed] = "different"
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert_source(source_db, "agent_effect_intents", values)
    insert_source(source_db, "agent_effect_intents", source_values("agent_effect_intents", run_id=3))
    assert source_db.execute("select count(*) from agent_effect_intents").fetchone()[0] == 2
