"""Actual/genuine-fresh Tasks12 SQLite contracts, not a target implementation.

Only synthetic memory rows are written. Public DAO flows are tested separately
against complete candidate fixtures; this file freezes native storage behavior.
"""
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from app.task_storage import create_task_schema, insert_task

SPEC = Path(__file__).parents[1] / "docs/superpowers/specs"
ACTUAL = json.loads((SPEC / "2026-10-16-tasks12-native-source.json").read_text())
FRESH = json.loads((SPEC / "2026-10-16-tasks12-fresh-source.json").read_text())
NAMES = tuple(ACTUAL["tables"])
AUTO = ("feedback_iteration_decisions", "feedback_processing_rounds",
        "oa_notification_events", "okr_review_items", "wechat_memory_import_jobs")
TEXT_PK = ("business_object_tasks", "email_agent_classification_tasks",
           "feedback_processing_batches", "feedback_processing_items",
           "workbench_artifacts", "workbench_attachments")
PARENTS = {"business_object_tasks": ("reply_task_id", -11, -12),
           "workbench_artifacts": ("turn_id", -13, -11),
           "workbench_attachments": ("task_id", -12, -11)}


def values(name, suffix="a"):
    return {
        "business_object_tasks": dict(business_object_key="object-" + suffix, reply_task_id=-11),
        "email_agent_classification_tasks": dict(task_id="task-" + suffix, channel="email",
            stable_message_identity="message-" + suffix, status="pending", input_json="{}"),
        "email_historical_candidates": dict(account_id="account", folder="INBOX", model_id="model",
            stable_message_identity="message-" + suffix, normalized_text="digest", provider_message_json="{}",
            uidvalidity=1, uid=2, state="pending", updated_at="source-stamp"),
        "feedback_iteration_decisions": dict(batch_id="seed", workbench_task_id="public-task",
            workbench_turn_id="public-turn", decision_json="{}"),
        "feedback_processing_batches": dict(batch_id="batch-" + suffix),
        "feedback_processing_items": dict(feedback_key="feedback-" + suffix),
        "feedback_processing_rounds": dict(feedback_key="feedback-" + suffix,
            round_number=1, batch_id="batch-" + suffix, status="processing"),
        "oa_notification_events": dict(event_kind="chat_reminder", conversation_id="chat",
            trigger_message_id="trigger-" + suffix),
        "okr_review_items": dict(request_id=17, objective_title="objective", kr_title="KR"),
        "wechat_memory_import_jobs": dict(import_run_id="import-" + suffix, account_id="account"),
        "workbench_artifacts": dict(id="artifact-" + suffix, turn_id=-13, label="artifact",
            path="synthetic/path", media_type="text/plain"),
        "workbench_attachments": dict(id="attachment-" + suffix, task_id=-12,
            client_request_id="request-" + suffix, filename="fixture.txt", media_type="text/plain",
            size_bytes=7, content_sha256="synthetic-digest", storage_path="synthetic/path"),
    }[name]


def insert(db, name, supplied=None, *, suffix="a", verb="insert", **changes):
    fields = values(name, suffix) if supplied is None else supplied
    fields = fields | changes
    return db.execute(f"{verb} into {name}({','.join(fields)}) "
                      f"values({','.join('?' for _ in fields)})", tuple(fields.values()))


def pragma(db, kind, name):
    return [dict(row) for row in db.execute(f'pragma main.{kind}("{name}")')]


@pytest.fixture(params=("actual", "fresh"))
def source(request):
    return ACTUAL if request.param == "actual" else FRESH


@pytest.fixture
def db(source):
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys=on")
        # This is the published eight-kind source factory, not the new target.
        create_task_schema(connection, include_workbench=True)
        task_sql = connection.execute("select sql from sqlite_schema where name='tasks'").fetchone()[0]
        assert hashlib.sha256(task_sql.encode()).hexdigest() == ACTUAL["existing_tasks_endpoint_metadata"]["table_ddl_sha256"]
        for metadata in source["tables"].values():
            connection.execute(next(o["sql"] for o in metadata["objects"] if o["type"] == "table"))
        for metadata in source["tables"].values():
            objects = {o["name"]: o for o in metadata["objects"]}
            for index in reversed(metadata["index_list"]):
                if objects[index["name"]]["sql"]:
                    connection.execute(objects[index["name"]]["sql"])
            for item in metadata["objects"]:
                if item["type"] == "trigger":
                    connection.execute(item["sql"])
        child = ACTUAL["retained_coupled_schema_metadata"]["feedback_iteration_decision_items"]
        for item in child["objects"]:
            if item["type"] == "table":
                connection.execute(item["sql"])
        insert_task(connection, "reply_tasks", dict(id=-11, conversation_id="chat",
            conversation_title="synthetic", single_chat=0, trigger_message_id="message",
            trigger_create_time="source-stamp", trigger_sender="synthetic"))
        insert_task(connection, "workbench_tasks", dict(id=-12, legacy_key="public-task",
            title="synthetic", runtime_kind="codex"))
        insert_task(connection, "workbench_turns", dict(id=-13, legacy_key="public-turn", parent_id=-12,
            client_request_id="request", task_sequence=1, user_text="synthetic", status="queued"))
        insert(connection, "feedback_processing_batches", batch_id="seed")
        connection.commit()
        yield connection


def test_profiles_and_approved_fields(source):
    assert FRESH["source_commit"] == ACTUAL["production_checkout_head"]
    assert FRESH["owned_temporary_removed"]
    assert set(FRESH["actual_differences"]) == {"feedback_processing_items", "feedback_processing_rounds"}
    with (SPEC / "2026-10-11-database-redesign-field-map.csv").open() as stream:
        mapped = {(r["old_table"], r["old_column"]): (r["sql_type"], int(r["not_null"]), int(r["primary_key_position"]))
                  for r in csv.DictReader(stream) if r["old_table"] in NAMES}
    for key in (("workbench_artifacts", "turn_id"), ("workbench_attachments", "task_id")):
        assert mapped[key] == ("TEXT", 1, 0)
        mapped[key] = ("INTEGER", 1, 0)
    native = {(name, c["name"]): (c["type"], c["notnull"], c["pk"])
              for name, metadata in source["tables"].items() for c in metadata["table_xinfo"]}
    assert len(native) == 134
    assert native == mapped


def test_exact_native_metadata_and_endpoints(db, source):
    for name, metadata in source["tables"].items():
        assert pragma(db, "table_xinfo", name) == metadata["table_xinfo"]
        assert pragma(db, "foreign_key_list", name) == metadata["foreign_key_list"]
        assert pragma(db, "index_list", name) == [{k: v for k, v in index.items() if k != "xinfo"}
                                                 for index in metadata["index_list"]]
        for index in metadata["index_list"]:
            assert pragma(db, "index_xinfo", index["name"]) == index["xinfo"]
        assert [dict(row) for row in db.execute("select type,name,tbl_name,sql from sqlite_schema "
            "where tbl_name=? order by type,name", (name,))] == metadata["objects"]
    assert len(pragma(db, "foreign_key_list", "feedback_iteration_decision_items")) == 3
    assert db.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("name", NAMES)
def test_minimal_rows_preserve_defaults_affinity_and_statement_clocks(db, source, name):
    before = db.execute("select current_timestamp").fetchone()[0]
    cursor = insert(db, name)
    row = db.execute(f"select * from {name} where rowid=?", (cursor.lastrowid,)).fetchone()
    after = db.execute("select current_timestamp").fetchone()[0]
    assert row is not None
    for field in source["tables"][name]["table_xinfo"]:
        if field["dflt_value"] == "current_timestamp" and field["name"] not in values(name):
            assert before <= row[field["name"]] <= after
    if name == "email_agent_classification_tasks":
        assert tuple(row[k] for k in ("owner", "generation", "attempt_count", "result_json", "available_at")) == ("", 0, 0, "null", "")
    if name == "feedback_processing_items":
        assert tuple(row[k] for k in ("batch_id", "status", "current_round_id", "attempt_id", "agent_run_id")) == ("", "pending", 0, 0, 0)
    if name == "feedback_processing_rounds":
        assert tuple(row[k] for k in ("receipt_version", "scope_receipt_json", "reopened_at")) == (1, "{}", "")
    if name == "okr_review_items":
        assert tuple(db.execute("select objective_weight,typeof(objective_weight),kr_weight,typeof(kr_weight) from okr_review_items").fetchone()) == (0.0, "real", 0.0, "real")


@pytest.mark.parametrize("name,field", [(n, c["name"]) for n, m in ACTUAL["tables"].items()
                                       for c in m["table_xinfo"] if c["notnull"]])
def test_native_not_null_is_not_weakened(db, name, field):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, name, **{field: None})


@pytest.mark.parametrize("name", TEXT_PK)
def test_nullable_text_pk_keeps_distinct_null_rows_and_binary_identity(db, name):
    key = next(c["name"] for c in ACTUAL["tables"][name]["table_xinfo"] if c["pk"])
    first = insert(db, name, suffix="first", **{key: None}).lastrowid
    second = insert(db, name, suffix="second", **{key: None}).lastrowid
    assert first != second
    assert db.execute(f"select count(*) from {name} where {key} is null").fetchone()[0] == 2
    insert(db, name, suffix="upper", **{key: "Case"})
    insert(db, name, suffix="lower", **{key: "case"})
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, name, suffix="duplicate", **{key: "Case"})


@pytest.mark.parametrize("name", NAMES)
def test_signed_source_rowids_are_legal_and_preserved(db, name):
    ident = insert(db, name, **({"id": -101} if name in AUTO else {"rowid": -101})).lastrowid
    assert ident == -101
    assert db.execute(f"select rowid from {name} where rowid=-101").fetchone()[0] == -101


@pytest.mark.parametrize("name", AUTO)
def test_autoincrement_high_water_preserves_native_full_transaction_scope(db, name):
    insert(db, name, id=9223372036854775807)
    db.commit()
    db.execute("begin immediate")
    insert(db, "feedback_processing_batches", batch_id="must-rollback")
    with pytest.raises(sqlite3.OperationalError, match="full"):
        insert(db, name, suffix="overflow")
    survives = name == "feedback_processing_rounds"
    assert db.in_transaction is survives
    assert bool(db.execute("select 1 from feedback_processing_batches where batch_id='must-rollback'").fetchone()) is survives
    assert db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone()[0] == 9223372036854775807


def test_five_allocator_streams_are_independent(db):
    assert [insert(db, name).lastrowid for name in AUTO] == [1] * 5


@pytest.mark.parametrize("name", ("oa_notification_events", "feedback_processing_rounds"))
def test_ignored_natural_key_conflict_consumes_source_allocator(db, name):
    assert insert(db, name).lastrowid == 1
    assert insert(db, name, verb="insert or ignore").rowcount == 0
    assert db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchone()[0] == 2
    assert insert(db, name, suffix="different").lastrowid == 3


@pytest.mark.parametrize("value,accepted", ((1, True), (1.0, True), ("1", True), ("1.0", True),
    ("1e0", True), (0, False), (-1, False), (1.25, False), ("x", False), (b"1", False),
    (None, False), (float("inf"), False), (float("nan"), False)))
def test_round_integer_affinity_and_native_trigger_order(db, value, accepted):
    if accepted:
        insert(db, "feedback_processing_rounds", round_number=value)
        assert tuple(db.execute("select round_number,typeof(round_number) from feedback_processing_rounds").fetchone()) == (1, "integer")
    else:
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "feedback_processing_rounds", round_number=value)


@pytest.mark.parametrize("name,field", (("feedback_iteration_decisions", "decision_json"),
    ("feedback_processing_items", "test_evidence_json"), ("feedback_processing_rounds", "scope_receipt_json"),
    ("oa_notification_events", "trigger_message_json"), ("okr_review_items", "item_json")))
def test_source_does_not_impose_new_json_policy(db, name, field):
    insert(db, name, **{field: "not json"})
    assert db.execute(f"select {field} from {name} order by rowid desc limit 1").fetchone()[0] == "not json"


@pytest.mark.parametrize("name,field", (("email_agent_classification_tasks", "input_json"),
    ("email_agent_classification_tasks", "result_json"), ("email_historical_candidates", "provider_message_json")))
def test_only_existing_json_valid_checks_are_enforced(db, name, field):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, name, **{field: "not json"})
    insert(db, name, **{field: b"{}"})
    assert db.execute(f"select typeof({field}) from {name}").fetchone()[0] == "blob"


@pytest.mark.parametrize("name", PARENTS)
def test_existing_kind_guards_and_no_action_fk_are_preserved(db, name):
    field, valid, wrong = PARENTS[name]
    with pytest.raises(sqlite3.IntegrityError, match="parent must"):
        insert(db, name, **{field: wrong})
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        insert(db, name, **{field: -999})
    inserted = insert(db, name).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="parent must"):
        db.execute(f"update {name} set {field}=? where rowid=?", (wrong, inserted))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("delete from tasks where id=?", (valid,))
    assert db.execute(f"select {field} from {name} where rowid=?", (inserted,)).fetchone()[0] == valid


def test_decision_append_only_and_three_independent_child_endpoints(db):
    decision = insert(db, "feedback_iteration_decisions").lastrowid
    insert(db, "feedback_processing_items", feedback_key="item")
    round_id = insert(db, "feedback_processing_rounds", feedback_key="item").lastrowid
    for endpoint, invalid in (("decision_id", -99), ("feedback_key", "missing"), ("round_id", -99)):
        fields = dict(decision_id=decision, feedback_key="item", round_id=round_id) | {endpoint: invalid}
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            insert(db, "feedback_iteration_decision_items", supplied=fields)
    insert(db, "feedback_iteration_decision_items", supplied=dict(decision_id=decision, feedback_key="item", round_id=round_id))
    for sql in ("update feedback_iteration_decisions set decision_json='changed'",
                "delete from feedback_iteration_decisions"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(sql)
    assert db.execute("pragma foreign_key_check").fetchall() == []



@pytest.mark.parametrize("name,field,value", (
    ("email_agent_classification_tasks", "task_id", " "),
    ("email_agent_classification_tasks", "stable_message_identity", " "),
    ("email_agent_classification_tasks", "channel", "dingtalk"),
    ("email_agent_classification_tasks", "status", "processing"),
    ("email_agent_classification_tasks", "generation", -1),
    ("email_agent_classification_tasks", "attempt_count", -1),
    ("email_historical_candidates", "state", "running"),
    ("feedback_processing_batches", "status", "done"),
    ("feedback_processing_items", "status", "done"),
    ("feedback_processing_rounds", "status", "pending"),
    ("feedback_processing_rounds", "receipt_version", 3),
    ("oa_notification_events", "event_kind", "other"),
    ("oa_notification_events", "status", "done"),
    ("wechat_memory_import_jobs", "status", "pending"),
    ("workbench_attachments", "size_bytes", -1),
))
def test_existing_checks_reject_invalid_values(db, name, field, value):
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, name, **{field: value})


@pytest.mark.parametrize("name,field,value,storage", (
    ("email_agent_classification_tasks", "task_id", "\t", "text"),
    ("email_agent_classification_tasks", "generation", 0.5, "real"),
    ("email_agent_classification_tasks", "generation", "not a number", "text"),
    ("email_agent_classification_tasks", "attempt_count", b"-1", "blob"),
    ("workbench_attachments", "size_bytes", b"-1", "blob"),
    ("okr_review_items", "objective_weight", "not a number", "text"),
    ("okr_review_items", "objective_weight", float("inf"), "real"),
    ("okr_review_items", "kr_weight", float("-inf"), "real"),
))
def test_affinity_is_not_an_invented_strict_type_policy(db, name, field, value, storage):
    cursor = insert(db, name, **{field: value})
    stored = db.execute(f"select {field},typeof({field}) from {name} where rowid=?", (cursor.lastrowid,)).fetchone()
    assert tuple(stored) == (value, storage)


@pytest.mark.parametrize("name", TEXT_PK)
def test_replace_keeps_native_hidden_rowid_and_conflict_semantics(db, name):
    first = insert(db, name).lastrowid
    second = insert(db, name, verb="insert or replace").lastrowid
    assert second > first
    assert db.execute(f"select 1 from {name} where rowid=?", (first,)).fetchone() is None
    assert db.execute(f"select 1 from {name} where rowid=?", (second,)).fetchone() is not None
