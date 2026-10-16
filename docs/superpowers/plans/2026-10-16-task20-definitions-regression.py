"""Finite target declaration checks with synthetic in-memory rows only."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from app.action_storage import _native_payload
from app.task20_definitions import (
    TASK20_COLUMNS,
    TASK20_DEFAULTS,
    TASK20_FIELDS,
    TASK20_PAYLOAD_FIELDS,
    create_task20_schema,
    task20_column,
    task20_projection,
)

SPEC = Path(__file__).parents[1] / "docs/superpowers/specs"
MAP = json.loads((SPEC / "2026-10-16-tasks20-field-map.json").read_text())


def payload(db, kind, **changes):
    result = {}
    for field, affinity, required, default, storage in TASK20_COLUMNS[kind]:
        if storage != "payload":
            continue
        if field in TASK20_DEFAULTS[kind]:
            result[field] = db.execute("select " + default).fetchone()[0]
        else:
            result[field] = ("" if affinity == "TEXT" else 0) if required else None
    result.update(
        {
            "reply_tasks": {
                "conversation_id": "chat",
                "conversation_title": "Chat",
                "single_chat": 1,
                "trigger_message_id": "trigger",
                "trigger_create_time": "time",
                "trigger_sender": "Sender",
            },
            "reply_task_inputs": {
                "channel": "dingtalk",
                "conversation_id": "chat",
                "conversation_title": "Chat",
                "single_chat": 1,
                "trigger_message_id": "input-trigger",
                "trigger_create_time": "time",
                "trigger_sender": "Sender",
                "trigger_text": "active input",
                "business_object_key": "object",
            },
            "work_summary_inputs": {
                "source_type": "reply_attempt",
                "source_ref": "17",
                "payload_json": '{"source":"leaf"}',
            },
            "meeting_alignment_jobs": {"meeting_id": "meeting"},
            "okr_review_requests": {
                "conversation_id": "chat",
                "conversation_title": "Chat",
                "trigger_message_id": "okr-trigger",
                "trigger_sender": "Sender",
                "trigger_text": "review",
                "period_label": "quarter",
                "period_start": "start",
                "period_end": "end",
            },
            "weekly_okr_analysis_jobs": {
                "week_end": "week",
                "manager_user_id": "manager",
                "source_digest": "digest",
            },
            "workbench_tasks": {"title": "Workbench", "runtime_kind": "codex"},
            "workbench_turns": {
                "client_request_id": "turn-request",
                "task_sequence": 1,
                "user_text": "task",
                "status": "queued",
            },
            "business_object_tasks": {"business_object_key": "object"},
            "email_agent_classification_tasks": {
                "task_id": "classification",
                "channel": "email",
                "stable_message_identity": "message",
                "status": "pending",
                "input_json": "{}",
            },
            "email_historical_candidates": {
                "account_id": "account",
                "folder": "INBOX",
                "model_id": "model",
                "stable_message_identity": "message",
                "normalized_text": "digest",
                "provider_message_json": "{}",
                "uidvalidity": 1,
                "uid": 2,
                "state": "pending",
                "updated_at": "historical-stamp",
            },
            "feedback_processing_batches": {"batch_id": "batch"},
            "feedback_processing_items": {"feedback_key": "feedback"},
            "feedback_processing_rounds": {
                "feedback_key": "feedback",
                "batch_id": "batch",
                "round_number": 1,
                "status": "processing",
            },
            "feedback_iteration_decisions": {
                "batch_id": "batch",
                "workbench_task_id": "public-task",
                "workbench_turn_id": "public-turn",
                "decision_json": "{}",
            },
            "oa_notification_events": {
                "event_kind": "chat_reminder",
                "conversation_id": "chat",
                "trigger_message_id": "oa-trigger",
            },
            "okr_review_items": {
                "request_id": 17,
                "objective_title": "Objective",
                "kr_title": "KR",
            },
            "wechat_memory_import_jobs": {
                "import_run_id": "import",
                "account_id": "account",
            },
            "workbench_artifacts": {
                "id": "artifact",
                "label": "Artifact",
                "path": "synthetic/path",
                "media_type": "text/plain",
            },
            "workbench_attachments": {
                "id": "attachment",
                "client_request_id": "attachment-request",
                "filename": "fixture.txt",
                "media_type": "text/plain",
                "size_bytes": 7,
                "content_sha256": "digest",
                "storage_path": "synthetic/path",
            },
        }[kind]
    )
    result.update(changes)
    return result


def insert(
    db,
    kind,
    *,
    physical_id=None,
    legacy_key="1",
    parent_id=None,
    values=None,
    verb="insert",
):
    return db.execute(
        verb
        + " into tasks(id,record_type,legacy_key,parent_id,payload_json) values(?,?,?,?,?)",
        (
            physical_id,
            kind,
            legacy_key,
            parent_id,
            _native_payload(payload(db, kind) if values is None else values),
        ),
    )


@pytest.fixture
def db():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys=on")
        create_task20_schema(connection)
        insert(connection, "reply_tasks", physical_id=-11, legacy_key="-11")
        insert(connection, "workbench_tasks", physical_id=-12, legacy_key="public-task")
        insert(
            connection,
            "workbench_turns",
            physical_id=-13,
            legacy_key="public-turn",
            parent_id=-12,
        )
        insert(
            connection, "feedback_processing_batches", physical_id=-14, legacy_key="7"
        )
        yield connection


def test_exact_field_map_and_only_seven_writable_columns(db):
    assert {(k, c[0]) for k, v in TASK20_COLUMNS.items() for c in v} == {
        (r["record_type"], r["source_field"]) for r in MAP["field_map"]
    }
    assert {
        r["name"] for r in db.execute("pragma table_xinfo(tasks)") if r["hidden"] == 0
    } == {
        "id",
        "record_type",
        "legacy_key",
        "payload_json",
        "parent_id",
        "current_input_id",
        "claimed_input_id",
    }
    assert len(TASK20_COLUMNS) == 20
    assert not any("final_text" in fields for fields in TASK20_PAYLOAD_FIELDS.values())


@pytest.mark.parametrize("kind", tuple(TASK20_COLUMNS))
def test_all_twenty_source_shaped_projections(db, kind):
    if kind in (
        "reply_tasks",
        "workbench_tasks",
        "workbench_turns",
        "feedback_processing_batches",
    ):
        row = db.execute(task20_projection(kind)).fetchone()
    else:
        parent = {
            "reply_task_inputs": -11,
            "business_object_tasks": -11,
            "workbench_artifacts": -13,
            "workbench_attachments": -12,
            "feedback_iteration_decisions": -14,
        }.get(kind)
        insert(db, kind, parent_id=parent)
        row = db.execute(task20_projection(kind)).fetchone()
    assert tuple(row.keys()) == TASK20_FIELDS[kind]
    if kind == "work_summary_inputs":
        assert row["payload_json"] == '{"source":"leaf"}'
    if kind == "workbench_turns":
        assert row["task_id"] == "public-task"
        assert row["final_text"] == ""
    if kind == "reply_task_inputs":
        assert row["reply_task_id"] == -11
    assert not list(db.execute("pragma foreign_key_check"))


@pytest.mark.parametrize("kind", tuple(TASK20_COLUMNS))
def test_unknown_payload_field_rejected(db, kind):
    parent = {
        "reply_task_inputs": -11,
        "business_object_tasks": -11,
        "workbench_artifacts": -13,
        "workbench_attachments": -12,
        "feedback_iteration_decisions": -14,
        "workbench_turns": -12,
    }.get(kind)
    unique_changes = {
        "reply_tasks": dict(trigger_message_id="distinct-trigger"),
        "workbench_turns": dict(
            client_request_id="distinct-request", task_sequence=2, status="completed"
        ),
        "feedback_processing_batches": dict(batch_id="distinct-batch"),
    }.get(kind, {})
    values = payload(db, kind, **unique_changes)
    # The exact control insert succeeds, so another source constraint cannot
    # accidentally make this test pass when the unknown-key check is removed.
    db.execute("savepoint exact_shape")
    insert(db, kind, physical_id=-15, legacy_key="-15", parent_id=parent, values=values)
    db.execute("rollback to exact_shape")
    db.execute("release exact_shape")
    values["unknown_source_field"] = "forbidden"
    with pytest.raises(sqlite3.IntegrityError):
        insert(
            db, kind, physical_id=-15, legacy_key="-15", parent_id=parent, values=values
        )


@pytest.mark.parametrize(
    "kind,field,bad",
    (
        ("weekly_okr_analysis_jobs", "status", "unknown"),
        ("workbench_turns", "task_sequence", 0),
        ("workbench_turns", "stop_requested", 2),
        ("workbench_turns", "status", "unknown"),
        ("email_agent_classification_tasks", "task_id", " "),
        ("email_agent_classification_tasks", "channel", "other"),
        ("email_agent_classification_tasks", "generation", -1),
        ("email_agent_classification_tasks", "attempt_count", -1),
        ("email_agent_classification_tasks", "input_json", "invalid"),
        ("email_historical_candidates", "provider_message_json", "invalid"),
        ("feedback_processing_items", "status", "unknown"),
        ("feedback_processing_rounds", "round_number", "invalid"),
        ("feedback_processing_rounds", "receipt_version", 3),
        ("oa_notification_events", "event_kind", "unknown"),
        ("wechat_memory_import_jobs", "status", "unknown"),
        ("workbench_attachments", "size_bytes", -1),
    ),
)
def test_retained_source_checks(db, kind, field, bad):
    parent = {"workbench_turns": -12, "workbench_attachments": -12}.get(kind)
    changes = (
        dict(client_request_id="check-control", task_sequence=2, status="completed")
        if kind == "workbench_turns"
        else {}
    )
    values = payload(db, kind, **changes)
    db.execute("savepoint source_check_control")
    insert(db, kind, parent_id=parent, values=values)
    db.execute("rollback to source_check_control")
    db.execute("release source_check_control")
    values[field] = bad
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, kind, parent_id=parent, values=values)


@pytest.mark.parametrize(
    "kind,parent",
    (
        ("business_object_tasks", -12),
        ("workbench_artifacts", -11),
        ("workbench_attachments", -11),
        ("feedback_iteration_decisions", -11),
        ("reply_task_inputs", -12),
        ("workbench_turns", -11),
    ),
)
def test_typed_parent_insert_and_update_guards(db, kind, parent):
    correct = {
        "business_object_tasks": -11,
        "workbench_artifacts": -13,
        "workbench_attachments": -12,
        "feedback_iteration_decisions": -14,
        "reply_task_inputs": -11,
        "workbench_turns": -12,
    }[kind]
    changes = (
        dict(client_request_id="parent-control", task_sequence=2, status="completed")
        if kind == "workbench_turns"
        else {}
    )
    values = payload(db, kind, **changes)
    db.execute("savepoint typed_parent_control")
    insert(db, kind, legacy_key="2", parent_id=correct, values=values)
    db.execute("rollback to typed_parent_control")
    db.execute("release typed_parent_control")
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, kind, legacy_key="2", parent_id=parent, values=values)
    physical = insert(
        db, kind, legacy_key="2", parent_id=correct, values=values
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update tasks set parent_id=? where id=?", (parent, physical))


def test_mapping_aliases_and_independent_timestamps(db):
    insert(
        db,
        "business_object_tasks",
        legacy_key="4",
        parent_id=-11,
        values=payload(
            db,
            "business_object_tasks",
            business_object_key="first",
            created_at="before",
            updated_at="later",
        ),
    )
    insert(
        db,
        "business_object_tasks",
        legacy_key="5",
        parent_id=-11,
        values=payload(
            db,
            "business_object_tasks",
            business_object_key="second",
            created_at="another",
            updated_at="stamp",
        ),
    )
    rows = db.execute(
        task20_projection("business_object_tasks") + " order by business_object_key"
    ).fetchall()
    assert [
        (r["business_object_key"], r["reply_task_id"], r["created_at"], r["updated_at"])
        for r in rows
    ] == [("first", -11, "before", "later"), ("second", -11, "another", "stamp")]


def test_nullable_source_key_and_blob_keep_separate_rowids(db):
    for identity, key in (("4", None), ("5", None), ("6", b"key")):
        insert(
            db,
            "business_object_tasks",
            legacy_key=identity,
            parent_id=-11,
            values=payload(db, "business_object_tasks", business_object_key=key),
        )
    rows = db.execute(
        task20_projection("business_object_tasks") + " order by rowid"
    ).fetchall()
    assert [r["business_object_key"] for r in rows] == [None, None, b"key"]


def test_batch_public_key_no_action_and_decision_immutability(db):
    decision = insert(
        db, "feedback_iteration_decisions", legacy_key="4", parent_id=-14
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "update tasks set payload_json=? where id=-14",
            (
                _native_payload(
                    payload(db, "feedback_processing_batches", batch_id="renamed")
                ),
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("delete from tasks where id=-14")
    for sql in (
        "update tasks set payload_json=payload_json where id=?",
        "delete from tasks where id=?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, (decision,))


def test_three_independent_retained_feedback_fk_endpoints(db):
    q = task20_column
    db.execute(
        "create table retained_child(decision_id integer not null,feedback_key text not null,"
        "round_id integer not null,foreign key(decision_id) references tasks("
        + q("feedback_iteration_decisions", "id")
        + "),"
        "foreign key(feedback_key) references tasks("
        + q("feedback_processing_items", "feedback_key")
        + "),"
        "foreign key(round_id) references tasks("
        + q("feedback_processing_rounds", "id")
        + "))"
    )
    insert(db, "feedback_iteration_decisions", legacy_key="4", parent_id=-14)
    insert(db, "feedback_processing_items", legacy_key="5")
    insert(db, "feedback_processing_rounds", legacy_key="6")
    db.execute("insert into retained_child values(4,'feedback',6)")
    for values in ((99, "feedback", 6), (4, "missing", 6), (4, "feedback", 99)):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into retained_child values(?,?,?)", values)
    assert not list(db.execute("pragma foreign_key_check"))


@pytest.mark.parametrize(
    "kind,field,parent",
    (
        ("feedback_processing_items", "test_evidence_json", None),
        ("feedback_processing_rounds", "scope_receipt_json", None),
        ("feedback_iteration_decisions", "decision_json", -14),
        ("oa_notification_events", "trigger_message_json", None),
        ("okr_review_items", "item_json", None),
    ),
)
def test_no_new_json_policy_on_source_unchecked_fields(db, kind, field, parent):
    insert(
        db,
        kind,
        parent_id=parent,
        values=payload(db, kind, **{field: "native unchecked string"}),
    )
    assert (
        db.execute(task20_projection(kind)).fetchone()[field]
        == "native unchecked string"
    )


@pytest.mark.parametrize(
    "kind,field,value,parent",
    (
        ("business_object_tasks", "business_object_key", b"blob-key", -11),
        ("email_agent_classification_tasks", "generation", "nonnumeric", None),
        ("email_agent_classification_tasks", "attempt_count", b"count", None),
        ("email_historical_candidates", "uidvalidity", b"validity", None),
        ("feedback_processing_rounds", "attempt_id", "not-numeric", None),
        ("okr_review_items", "objective_weight", float("inf"), None),
        ("okr_review_items", "kr_weight", b"weight", None),
    ),
)
def test_source_legal_scalar_storage_classes(db, kind, field, value, parent):
    insert(db, kind, parent_id=parent, values=payload(db, kind, **{field: value}))
    result = db.execute(task20_projection(kind)).fetchone()[field]
    assert result == value
    assert type(result) is type(value)


def test_explicit_id_decision_replace_keeps_retained_public_fk(db):
    q = task20_column
    db.execute(
        "create table decision_child(id integer references tasks("
        + q("feedback_iteration_decisions", "id")
        + "))"
    )
    insert(db, "feedback_iteration_decisions", legacy_key="4", parent_id=-14)
    db.execute("insert into decision_child values(4)")
    insert(
        db,
        "feedback_iteration_decisions",
        legacy_key="4",
        parent_id=-14,
        values=payload(db, "feedback_iteration_decisions", decision_json="replacement"),
        verb="insert or replace",
    )
    assert (
        db.execute(task20_projection("feedback_iteration_decisions")).fetchone()[
            "decision_json"
        ]
        == "replacement"
    )
    assert not list(db.execute("pragma foreign_key_check"))


def test_input_pointers_use_physical_ids_public_projection_uses_legacy_keys(db):
    insert(db, "reply_task_inputs", physical_id=77, legacy_key="33", parent_id=-11)
    db.execute("update tasks set current_input_id=77,claimed_input_id=77 where id=-11")
    row = db.execute(task20_projection("reply_tasks")).fetchone()
    assert (row["current_input_id"], row["claimed_input_id"]) == (33, 33)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update tasks set current_input_id=-12 where id=-11")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("delete from tasks where id=77")


def test_published_nullable_workbench_keys_are_not_autofilled(db):
    for identity in (17, 18):
        insert(db, "workbench_tasks", physical_id=identity, legacy_key=None)
    assert [
        r[0]
        for r in db.execute(
            "select legacy_key from tasks where id in (17,18) order by id"
        )
    ] == [None, None]


def native_required_fields():
    from app.task_storage import NULLABLE

    result = []
    for item in MAP["field_map"]:
        k = item["record_type"]
        f = item["source_field"]
        if item.get(
            "source_notnull",
            f != "id"
            and not (k == "workbench_turns" and f == "final_text")
            and f not in NULLABLE.get(k, set()),
        ):
            result.append((k, f, item["target_storage"]))
    return result


@pytest.mark.parametrize("kind,field,storage", native_required_fields())
def test_every_retained_source_not_null_has_independent_success_control(
    db, kind, field, storage
):
    parent = {
        "reply_task_inputs": -11,
        "business_object_tasks": -11,
        "workbench_artifacts": -13,
        "workbench_attachments": -12,
        "feedback_iteration_decisions": -14,
        "workbench_turns": -12,
    }.get(kind)
    changes = {
        "reply_tasks": dict(trigger_message_id="distinct-trigger"),
        "workbench_turns": dict(
            client_request_id="distinct-request", task_sequence=2, status="completed"
        ),
        "feedback_processing_batches": dict(batch_id="distinct-batch"),
    }.get(kind, {})
    values = payload(db, kind, **changes)
    db.execute("savepoint native_required_control")
    insert(db, kind, physical_id=-15, legacy_key="-15", parent_id=parent, values=values)
    db.execute("rollback to native_required_control")
    db.execute("release native_required_control")
    if storage == "parent_id":
        parent = None
    else:
        assert storage.startswith("payload_json.$.")
        values[field] = None
    with pytest.raises(sqlite3.IntegrityError):
        insert(
            db, kind, physical_id=-15, legacy_key="-15", parent_id=parent, values=values
        )
