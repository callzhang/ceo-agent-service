from tests.task_input_fixtures import bind_claimed_input
import sqlite3

import pytest

from app import action_storage, task8_storage
from app.task_entity_storage import (
    append_task_input, associate_business_key,
    bind_current_input, create_task, get_task, get_task_with_inputs,
    list_task_business_keys, update_task_cas,
)


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    task8_storage.register_task8_functions(db)
    from app.task_run_entity_assembly import task_run_entity_assembly_objects
    objects = task_run_entity_assembly_objects()
    for sql in objects.values():
        if sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    for sql in objects.values():
        if not sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    return db


def _params(kind):
    return {
        "reply": {},
        "work_summary": {},
        "meeting_alignment": {"meeting_id": "m-1"},
        "okr_review": {key: value for key, value in {
            "conversation_id": "c", "conversation_title": "title", "period_end": "e",
            "period_label": "p", "period_start": "s", "trigger_message_id": "m",
            "trigger_sender": "u", "trigger_text": "text",
        }.items()},
        "okr_analysis": {"manager_user_id": "u", "source_digest": "d", "week_end": "e"},
        "workbench_turn": {},
        "email_classification": {},
        "feedback_processing": {},
        "wechat_import": {"account_id": "a", "import_run_id": "r"},
    }[kind]


@pytest.mark.parametrize("kind", [
    "reply", "work_summary", "meeting_alignment", "okr_review", "okr_analysis",
    "workbench_turn", "email_classification", "feedback_processing", "wechat_import",
])
def test_create_task_for_all_nine_kinds_keeps_absent_facts_null(kind):
    db = _db()
    status = "ready_to_send" if kind == "meeting_alignment" else "pending"
    task = create_task(db, values={
        "kind": kind, "status": status, "created_at": "created", "updated_at": "updated",
    }, parameters=_params(kind))
    assert task["kind"] == kind
    assert task["status"] == status
    assert task["attempts"] is None
    assert task["owner_pid"] is None
    assert task["lease_owner"] is None
    assert task["current_input_id"] is None
    assert task["claimed_input_id"] is None
    assert task["parameters"] == _params(kind)


def test_task_native_codec_preserves_sqlite_scalar_values_and_rejects_unknown_fields():
    db = _db()
    task = create_task(db, values={
        "kind": "reply", "status": "pending", "attempts": 1.0, "created_at": "c", "updated_at": "u",
    }, parameters={
        "force_new_decision": "true", "manual_rerun_attempt_id": 1.25,
        "manual_rerun_revision_key": memoryview(b"rev"), "recovery_code": None,
    })
    assert task["attempts"] == 1 and type(task["attempts"]) is int
    assert task["parameters"] == {
        "force_new_decision": "true", "manual_rerun_attempt_id": 1.25,
        "manual_rerun_revision_key": b"rev", "recovery_code": None,
    }
    encoded = db.execute("select json_extract(parameters_json,'$.force_new_decision'),json_extract(parameters_json,'$.manual_rerun_attempt_id') from tasks").fetchone()
    assert encoded[0] == "true"
    assert encoded[1] == '{"t":"real","m":5,"e":-2}'
    bool_task = create_task(db, values={
        "kind": "reply", "status": "pending", "created_at": "c", "updated_at": "u",
    }, parameters={"force_new_decision": True})
    assert bool_task["parameters"]["force_new_decision"] == 1
    with pytest.raises(ValueError, match="unknown Task fields"):
        create_task(db, values={"kind": "reply", "status": "pending", "result_json": "{}"})
    with pytest.raises(sqlite3.IntegrityError, match="invalid task parameter contract"):
        create_task(db, values={"kind": "reply", "status": "pending", "created_at": "c", "updated_at": "u"},
                    parameters={"status": "pending"})


def test_task_cas_checks_only_supplied_native_expectations_and_preserves_nulls():
    db = _db()
    task = create_task(db, values={
        "kind": "reply", "status": "pending", "created_at": "c", "updated_at": "u",
    })
    assert update_task_cas(db, task_id=task["id"], expected={"status": "running"}, changes={"error": "stale"}) is None
    updated = update_task_cas(db, task_id=task["id"], expected={"status": "pending", "attempts": None}, changes={"status": "failed", "error": "x"})
    assert updated["status"] == "failed"
    assert updated["attempts"] is None
    with pytest.raises(ValueError, match="unknown Task expectation fields"):
        update_task_cas(db, task_id=task["id"], expected={"result": None}, changes={"error": "x"})


def _input(trigger, revision="same", **extra):
    return {
        "channel": "dingtalk", "conversation_id": "c", "conversation_title": "title",
        "single_chat": 0, "trigger_message_id": trigger, "trigger_create_time": "t",
        "trigger_sender": "u", "trigger_text": trigger, "trigger_message_json": '{"raw":true}',
        "oa_url": "", "business_object_key": "b", "input_revision_key": revision,
        "input_provenance_json": '{"source":"native"}', "input_compacted": 0, **extra,
    }


def test_full_input_key_allows_same_task_revision_with_distinct_triggers_and_is_immutable():
    db = _db()
    task = create_task(db, values={"kind": "reply", "status": "pending", "created_at": "c", "updated_at": "u"})
    first, inserted = append_task_input(db, task_id=task["id"], values=_input("m1"))
    same, inserted_again = append_task_input(db, task_id=task["id"], values=_input("m1", trigger_text="changed"))
    second, inserted_second = append_task_input(db, task_id=task["id"], values=_input("m2"))
    assert inserted and not inserted_again and inserted_second
    assert same["id"] == first["id"] and same["trigger_text"] == "m1"
    assert second["id"] != first["id"]
    other = create_task(db, values={"kind": "reply", "status": "pending", "created_at": "c", "updated_at": "u"})
    with pytest.raises(sqlite3.IntegrityError, match="belongs to another Task"):
        append_task_input(db, task_id=other["id"], values=_input("m1"))


def test_current_and_claimed_bindings_keep_old_execution_input_during_advance():
    db = _db()
    task = create_task(db, values={"kind": "reply", "status": "pending", "input_version": 1, "created_at": "c", "updated_at": "u"})
    old, _ = append_task_input(db, task_id=task["id"], values=_input("m1"))
    bound = bind_current_input(db, task_id=task["id"], input_id=old["id"], expected_input_version=1, input_version=2)
    assert bound["current_input_id"] == old["id"] and bound["input_version"] == 2
    claimed = bind_claimed_input(db, task_id=task["id"], input_id=old["id"], claimed_input_version=2)
    new, _ = append_task_input(db, task_id=task["id"], values=_input("m2"))
    assert bind_current_input(db, task_id=task["id"], input_id=new["id"], expected_input_version=1, input_version=3) is None
    updated = bind_current_input(db, task_id=task["id"], input_id=new["id"], expected_input_version=2, input_version=3)
    assert updated["current_input_id"] == new["id"]
    assert updated["claimed_input_id"] == old["id"]
    assert get_task_with_inputs(db, task["id"])["claimed_input"]["trigger_message_id"] == "m1"
    assert claimed["claimed_input_version"] == 2


def test_business_keys_support_multiple_per_task_but_global_key_collision_fails():
    db = _db()
    first = create_task(db, values={"kind": "reply", "status": "pending", "created_at": "c", "updated_at": "u"})
    second = create_task(db, values={"kind": "reply", "status": "pending", "created_at": "c", "updated_at": "u"})
    associate_business_key(db, task_id=first["id"], business_key="one")
    associate_business_key(db, task_id=first["id"], business_key="two")
    assert [row["business_key"] for row in list_task_business_keys(db, first["id"])] == ["one", "two"]
    with pytest.raises(sqlite3.IntegrityError):
        associate_business_key(db, task_id=second["id"], business_key="one")


def test_caller_transaction_rollback_removes_task_input_and_key_together():
    db = _db()
    db.execute("begin")
    task = create_task(db, values={"kind": "reply", "status": "pending", "created_at": "c", "updated_at": "u"})
    item, _ = append_task_input(db, task_id=task["id"], values=_input("m"))
    associate_business_key(db, task_id=task["id"], business_key="key")
    db.rollback()
    assert get_task(db, task["id"]) is None
    assert db.execute("select count(*) from task_inputs where id=?", (item["id"],)).fetchone()[0] == 0
    assert db.execute("select count(*) from task_business_keys where business_key='key'").fetchone()[0] == 0


def test_native_api_does_not_call_old_storage_factories(monkeypatch):
    db = _db()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("old Task factory called")
    monkeypatch.setattr(action_storage, "_action_definitions", forbidden)
    monkeypatch.setattr(task8_storage, "task8_insert_parts", forbidden)
    monkeypatch.setattr(task8_storage, "task8_set_parts", forbidden)
    task = create_task(db, values={"kind": "meeting_alignment", "status": "ready_to_send", "created_at": "c", "updated_at": "u"}, parameters={"meeting_id": "m"})
    assert get_task(db, task["id"])["status"] == "ready_to_send"
    assert task["parameters"] == {"meeting_id": "m"}
