"""Native source versus disabled nine-family action mutations."""

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time

import pytest

from app.action_followup_sync_definitions import SYNC_FIELDS, sync_action_projection
from app.action_storage import (
    create_action_schema, delete_sync_action, insert_sync_action, update_sync_action,
)
from test_followup_sync_source import NAMES, snapshot, values

EVENT_SOURCE = json.loads((Path(__file__).parents[1] /
                           "docs/superpowers/specs/2026-10-17-action-task-events-source-extension.json").read_text())


def _pair(variant):
    source = sqlite3.connect(":memory:")
    target = sqlite3.connect(":memory:")
    for db in (source, target):
        db.execute("pragma foreign_keys=on")
        for name in ("tasks", "runs", "email_classifications", "business_tasks", "business_task_signals"):
            db.execute(f"create table {name}(id integer primary key" +
                       (",record_type text)" if name in ("tasks", "runs") else ")"))
        db.execute("insert into business_tasks values(11)")
        db.execute("insert into business_task_signals values(12)")
    source_schema = snapshot(variant)
    for name in NAMES:
        source.execute(source_schema["tables"][name]["sql"])
        for index in source_schema["tables"][name]["indexes"]:
            if index["sql"]:
                source.execute(index["sql"])
    event = EVENT_SOURCE["actual"]
    source.execute(event["sql"])
    for index in event["indexes"]:
        if index["sql"]:
            source.execute(index["sql"])
    create_action_schema(target, include_reply=True,
                         reply_variant="actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk",
                         include_email=True, include_followup_sync=True, compact_payload=True)
    source.commit()
    target.commit()
    return source, target


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_task_events_insert_update_delete_and_source_constraints(variant):
    source, target = _pair(variant)
    with closing(source), closing(target):
        row = {"task_id": 11, "event_type": "status_changed", "signal_id": 12,
               "before_json": "{}", "after_json": '{"status":"open"}', "reason": "source event"}
        expected = _insert_source(source, "business_task_events", row)
        actual = insert_sync_action(target, "business_task_events", row)
        assert (actual.lastrowid, actual.rowcount) == (expected.lastrowid, expected.rowcount)
        _rows(source, target, "business_task_events", variant)
        changed = {"before_json": '{"status":"open"}', "after_json": '{"status":"done"}',
                   "reason": "updated source event"}
        source.execute("update business_task_events set before_json=?,after_json=?,reason=? where id=?",
                       (*changed.values(), expected.lastrowid))
        assert update_sync_action(target, "business_task_events", actual.lastrowid, changed).rowcount == 1
        _rows(source, target, "business_task_events", variant)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source(source, "business_task_events", row | {"event_type": "invented"})
        with pytest.raises(sqlite3.IntegrityError):
            insert_sync_action(target, "business_task_events", row | {"event_type": "invented"})
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source(source, "business_task_events", row | {"before_json": "[]"})
        with pytest.raises(sqlite3.IntegrityError):
            insert_sync_action(target, "business_task_events", row | {"before_json": "[]"})
        assert delete_sync_action(target, "business_task_events", actual.lastrowid).rowcount == \
            source.execute("delete from business_task_events where id=?", (expected.lastrowid,)).rowcount == 1


def _insert_source(db, kind, row, *, ignore=False):
    columns = ",".join(row)
    placeholders = ",".join("?" for _ in row)
    return db.execute(f"insert {'or ignore ' if ignore else ''}into {kind}({columns}) values({placeholders})",
                      tuple(row.values()))


def _rows(source, target, kind, variant):
    fields = SYNC_FIELDS[kind]
    target_query = sync_action_projection(kind, source_variant="actual" if variant == "actual" else "fresh_candidate")
    source_values = source.execute(f"select * from {kind} order by id").fetchall()
    target_values = target.execute(f"select * from ({target_query}) order by id").fetchall()
    assert len(source_values) == len(target_values)
    for source_row, target_row in zip(source_values, target_values, strict=True):
        for field, native, action in zip(fields, source_row, target_row, strict=True):
            if native != action and field in ("created_at", "updated_at") and type(native) is str and type(action) is str:
                try:
                    gap = abs(datetime.fromisoformat(native) - datetime.fromisoformat(action)).total_seconds()
                except ValueError:
                    pass
                else:
                    assert gap <= 2
                    continue
            assert native == action, (kind, field, native, action)
    for field in fields:
        assert source.execute(f"select typeof({field}) from {kind} order by id").fetchall() == target.execute(
            f"select typeof({field}) from ({target_query}) order by id").fetchall()


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_all_nine_insert_defaults_update_delete_and_independent_ids(variant):
    source, target = _pair(variant)
    with closing(source), closing(target):
        for kind in NAMES:
            row = values(kind)
            expected = _insert_source(source, kind, row)
            actual = insert_sync_action(target, kind, row)
            assert (actual.lastrowid, actual.rowcount) == (expected.lastrowid, expected.rowcount) == (1, 1)
            _rows(source, target, kind, variant)
        for kind in NAMES:
            if kind == "business_task_follow_ups":
                continue
            source.execute(f"update {kind} set id=20 where id=1")
            assert update_sync_action(target, kind, 1, {"id": 20}).rowcount == 1
            _rows(source, target, kind, variant)
        for kind in reversed(NAMES):
            old_id = 1 if kind == "business_task_follow_ups" else 20
            assert source.execute(f"delete from {kind} where id=?", (old_id,)).rowcount == 1
            assert delete_sync_action(target, kind, old_id).rowcount == 1
            _rows(source, target, kind, variant)
        for kind in NAMES:
            row = values(kind, "second")
            if kind == "business_task_follow_up_send_attempts":
                row["draft_id"] = 2
            assert _insert_source(source, kind, row).lastrowid == 2
            assert insert_sync_action(target, kind, row).lastrowid == 2
            _rows(source, target, kind, variant)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_every_source_field_written_with_native_affinity(variant):
    source, target = _pair(variant)
    with closing(source), closing(target):
        for kind in NAMES:
            row = values(kind)
            if kind == "business_task_follow_ups":
                row["status"] = "draft"
            elif kind in ("task_todo_sync_outbox", "business_task_todo_sync_outbox"):
                row["status"] = "queued"
            for column in snapshot(variant)["tables"][kind]["columns"]:
                field = column["name"]
                if field == "id" or field in row:
                    continue
                row[field] = ("7" if column["type"].upper() == "INTEGER" else
                              "2.5" if column["type"].upper() == "REAL" else
                              b"raw\x00value")
            expected = _insert_source(source, kind, row)
            actual = insert_sync_action(target, kind, row)
            assert actual.lastrowid == expected.lastrowid
            _rows(source, target, kind, variant)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_source_affinity_cas_raw_values_and_ignored_conflicts(variant):
    source, target = _pair(variant)
    with closing(source), closing(target):
        kind = "work_updates"
        row = values(kind) | {"summary": "nul\x00🙂", "changes_json": b"raw\x00json",
                              "confidence": float("inf"), "project_id": "9"}
        assert _insert_source(source, kind, row).lastrowid == insert_sync_action(target, kind, row).lastrowid
        _rows(source, target, kind, variant)
        updated = {"summary": b"binary\x00value", "confidence": "2.5"}
        source.execute("update work_updates set summary=?,confidence=? where id=1 and source_ref=?",
                       (*updated.values(), "a"))
        assert update_sync_action(target, kind, 1, updated, where_sql="source_ref=?", parameters=("a",)).rowcount == 1
        assert update_sync_action(target, kind, 1, {"summary": "wrong"}, where_sql="source_ref=?", parameters=("missing",)).rowcount == 0
        _rows(source, target, kind, variant)
        business = values("business_task_follow_ups")
        _insert_source(source, "business_task_follow_ups", business)
        insert_sync_action(target, "business_task_follow_ups", business)
        assert _insert_source(source, "business_task_follow_ups", business, ignore=True).rowcount == 0
        assert insert_sync_action(target, "business_task_follow_ups", business, ignore=True).rowcount == 0
        _rows(source, target, "business_task_follow_ups", variant)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_business_draft_reference_and_whole_transaction_full(variant):
    source, target = _pair(variant)
    with closing(source), closing(target):
        parent = values("business_task_follow_ups")
        _insert_source(source, "business_task_follow_ups", parent)
        insert_sync_action(target, "business_task_follow_ups", parent)
        child = values("business_task_follow_up_send_attempts") | {"draft_id": "1.0"}
        _insert_source(source, "business_task_follow_up_send_attempts", child)
        insert_sync_action(target, "business_task_follow_up_send_attempts", child)
        _rows(source, target, "business_task_follow_up_send_attempts", variant)
        with pytest.raises(sqlite3.IntegrityError):
            source.execute("update business_task_follow_ups set id=9 where id=1")
        with pytest.raises(sqlite3.IntegrityError):
            update_sync_action(target, "business_task_follow_ups", 1, {"id": 9})
        for db, physical in ((source, "business_task_follow_ups"), (target, "actions")):
            with pytest.raises(sqlite3.IntegrityError):
                predicate = "id=1" if db is source else "record_type='business_task_follow_ups' and legacy_key='1'"
                db.execute(f"delete from {physical} where {predicate}")
        source.commit()
        target.commit()
        for db, name in ((source, "work_updates"), (target, "actions:work_updates")):
            db.execute("begin")
            if db is source:
                _insert_source(db, "work_updates", values("work_updates"))
            else:
                insert_sync_action(db, "work_updates", values("work_updates"))
            db.execute("update sqlite_sequence set seq=? where name=?", (2**63 - 1, name))
            with pytest.raises(sqlite3.OperationalError, match="database or disk is full"):
                if db is source:
                    _insert_source(db, "work_updates", values("work_updates", "next"))
                else:
                    insert_sync_action(db, "work_updates", values("work_updates", "next"))
            assert not db.in_transaction
            assert db.execute("select count(*) from work_updates" if db is source else
                              "select count(*) from actions where record_type='work_updates'").fetchone()[0] == 0


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("candidate", (-7, 0, "9", 10.0, None))
def test_explicit_and_null_ids_match_native_sequence(variant, candidate):
    source, target = _pair(variant)
    with closing(source), closing(target):
        row = values("work_updates") | {"id": candidate}
        expected = _insert_source(source, "work_updates", row)
        actual = insert_sync_action(target, "work_updates", row)
        assert actual.lastrowid == expected.lastrowid
        assert source.execute("select seq from sqlite_sequence where name='work_updates'").fetchone() == target.execute(
            "select seq from sqlite_sequence where name='actions:work_updates'").fetchone()
        _rows(source, target, "work_updates", variant)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("candidate", (b"bad", "3.5", 1.5, 2**63))
def test_invalid_public_ids_rejected_like_native(variant, candidate):
    source, target = _pair(variant)
    with closing(source), closing(target):
        row = values("work_updates") | {"id": candidate}
        with pytest.raises((sqlite3.IntegrityError, OverflowError)):
            _insert_source(source, "work_updates", row)
        with pytest.raises((sqlite3.IntegrityError, OverflowError)):
            insert_sync_action(target, "work_updates", row)
        _rows(source, target, "work_updates", variant)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_ignore_conflict_advances_sequence_and_caller_rollback(variant):
    source, target = _pair(variant)
    with closing(source), closing(target):
        parent = values("business_task_follow_ups")
        for db in (source, target):
            db.execute("begin")
        _insert_source(source, "business_task_follow_ups", parent)
        insert_sync_action(target, "business_task_follow_ups", parent)
        attempted = parent | {"id": 8}
        assert _insert_source(source, "business_task_follow_ups", attempted, ignore=True).rowcount == 0
        assert insert_sync_action(target, "business_task_follow_ups", attempted, ignore=True).rowcount == 0
        for db, name in ((source, "business_task_follow_ups"), (target, "actions:business_task_follow_ups")):
            assert db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone()[0] == 8
        source.rollback()
        target.rollback()
        assert source.execute("select count(*) from business_task_follow_ups").fetchone()[0] == 0
        assert target.execute("select count(*) from actions where record_type='business_task_follow_ups'").fetchone()[0] == 0
        assert source.execute("select seq from sqlite_sequence where name='business_task_follow_ups'").fetchone() is None
        assert target.execute("select seq from sqlite_sequence where name='actions:business_task_follow_ups'").fetchone() is None


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_no_row_update_does_not_evaluate_invalid_assignment(variant):
    source, target = _pair(variant)
    with closing(source), closing(target):
        assert source.execute("update work_updates set id=? where id=404", (b"bad",)).rowcount == 0
        assert update_sync_action(target, "work_updates", 404, {"id": b"bad"}).rowcount == 0


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("parent_value", (0, -1, 1.0, "1", "1.0", "1x", 1.5, b"1", None))
def test_physical_draft_fk_matches_source_affinity(variant, parent_value):
    source, target = _pair(variant)
    with closing(source), closing(target):
        parent = values("business_task_follow_ups")
        _insert_source(source, "business_task_follow_ups", parent)
        insert_sync_action(target, "business_task_follow_ups", parent)
        child = values("business_task_follow_up_send_attempts") | {"draft_id": parent_value}
        try:
            expected = _insert_source(source, "business_task_follow_up_send_attempts", child)
        except sqlite3.IntegrityError:
            with pytest.raises(sqlite3.IntegrityError):
                insert_sync_action(target, "business_task_follow_up_send_attempts", child)
        else:
            actual = insert_sync_action(target, "business_task_follow_up_send_attempts", child)
            assert actual.lastrowid == expected.lastrowid
            _rows(source, target, "business_task_follow_up_send_attempts", variant)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("draft_id,state", ((None, "claimed"), (float("nan"), "claimed"),
                                             (404, "wrong"), (404, "claimed")))
def test_ignored_business_attempt_observes_native_constraints_first(variant, draft_id, state):
    source, target = _pair(variant)
    with closing(source), closing(target):
        row = values("business_task_follow_up_send_attempts") | {
            "id": 50, "draft_id": draft_id, "state": state,
        }
        if draft_id == 404 and state == "claimed":
            with pytest.raises(sqlite3.IntegrityError):
                _insert_source(source, "business_task_follow_up_send_attempts", row, ignore=True)
            with pytest.raises(sqlite3.IntegrityError):
                insert_sync_action(target, "business_task_follow_up_send_attempts", row, ignore=True)
        else:
            assert _insert_source(source, "business_task_follow_up_send_attempts", row, ignore=True).rowcount == 0
            assert insert_sync_action(target, "business_task_follow_up_send_attempts", row, ignore=True).rowcount == 0
        assert source.execute("select seq from sqlite_sequence where name='business_task_follow_up_send_attempts'").fetchone() == target.execute(
            "select seq from sqlite_sequence where name='actions:business_task_follow_up_send_attempts'").fetchone()


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_ignored_unique_conflict_precedes_missing_draft_fk(variant):
    source, target = _pair(variant)
    with closing(source), closing(target):
        parent = values("business_task_follow_ups")
        _insert_source(source, "business_task_follow_ups", parent)
        insert_sync_action(target, "business_task_follow_ups", parent)
        first = values("business_task_follow_up_send_attempts")
        _insert_source(source, "business_task_follow_up_send_attempts", first)
        insert_sync_action(target, "business_task_follow_up_send_attempts", first)
        conflict = first | {"id": 50, "draft_id": 404}
        assert _insert_source(source, "business_task_follow_up_send_attempts", conflict, ignore=True).rowcount == 0
        assert insert_sync_action(target, "business_task_follow_up_send_attempts", conflict, ignore=True).rowcount == 0
        assert source.execute("select seq from sqlite_sequence where name='business_task_follow_up_send_attempts'").fetchone() == target.execute(
            "select seq from sqlite_sequence where name='actions:business_task_follow_up_send_attempts'").fetchone()


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_omitted_timestamp_defaults_share_native_statement_time(variant):
    source, target = _pair(variant)
    with closing(source), closing(target):
        kind = "follow_up_send_attempts"
        row = values(kind)
        _insert_source(source, kind, row)
        assert source.execute(f"select created_at=updated_at from {kind}").fetchone()[0] == 1
        seen = 0

        def pause_between_separate_clock_reads(statement):
            nonlocal seen
            if statement.lower().strip() == "select current_timestamp":
                seen += 1
                if seen == 2:
                    time.sleep(1.1)

        target.set_trace_callback(pause_between_separate_clock_reads)
        try:
            insert_sync_action(target, kind, row)
        finally:
            target.set_trace_callback(None)
        assert target.execute("select sync_created_at=sync_updated_at from actions where record_type=?", (kind,)).fetchone()[0] == 1
