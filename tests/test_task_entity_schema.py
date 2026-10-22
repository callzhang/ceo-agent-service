from contextlib import closing
import sqlite3

from app import task20_current_schema, task20_definitions
from app.task_lifecycle_current_schema import create_current_task20_schema
from app.task8_storage import register_task8_functions
from app.task_entity_schema import (
    capture_task_entities, migrate_task_entities, resolve_dispatcher_claim_source_id,
    resolve_source_public_owner,
)
from app.task_run_entity_definitions import task_run_entity_objects
from tests.test_task20_definitions import payload
from app.task_lifecycle_storage import source_insert
from app.storage_payload import TASK_RECORD_TYPES


def _source():
    db = sqlite3.connect(":memory:")
    db.execute("pragma foreign_keys=off")
    register_task8_functions(db)
    create_current_task20_schema(db)
    fixed = {
        "reply_tasks": (201, None, "201"),
        "workbench_tasks": (202, None, "wb-task-a"),
        "workbench_turns": (203, 202, "wb-turn-a"),
        "feedback_processing_batches": (204, None, "204"),
        "reply_task_inputs": (205, 201, "5"),
    }
    sequence = 220
    for kind in task20_definitions.TASK20_COLUMNS:
        if kind in fixed:
            physical, parent, public = fixed[kind]
        else:
            physical, parent, public = sequence, None, str(sequence)
            sequence += 1
            parent = {
                "business_object_tasks": 201,
                "workbench_artifacts": 203,
                "workbench_attachments": 202,
                "feedback_iteration_decisions": 204,
            }.get(kind, parent)
        values = payload(db, kind)
        if kind == "reply_tasks":
            values["business_object_key"] = "object"
        if kind == "reply_task_inputs":
            values.update(channel="dingtalk", conversation_id="chat", conversation_title="Chat",
                          single_chat=1, trigger_message_id="trigger", trigger_create_time="time",
                          trigger_sender="Sender", trigger_text="input", oa_url="",
                          business_object_key="object")
        changes = {
            "business_object_tasks": {"business_object_key": f"business-{physical}"},
            "email_agent_classification_tasks": {"task_id": f"classification-{physical}"},
            "work_summary_inputs": {"source_ref": f"source-{physical}"},
            "feedback_processing_items": {"feedback_key": "feedback-a", "batch_id": "batch-a"},
            "feedback_processing_rounds": {"feedback_key": "feedback-a", "batch_id": "batch-a"},
            "feedback_iteration_decisions": {"batch_id": "batch-a"},
            "okr_review_items": {"request_id": 222},
        }.get(kind, {})
        values.update(changes)
        if kind == "feedback_processing_batches":
            values["batch_id"] = "batch-a"
        columns, expressions, parameters = source_insert(kind, values, {})
        columns = ["_kind", "id", "legacy_key", "parent_id", *columns]
        expressions = [str(TASK_RECORD_TYPES.code(kind)), "?", "?", "?", *expressions]
        db.execute("insert into tasks(" + ",".join('"' + c + '"' for c in columns)
                   + ") values(" + ",".join(expressions) + ")",
                   (physical, public, parent, *parameters))
    values = payload(db, "reply_task_inputs")
    values.update(channel="dingtalk", conversation_id="chat", conversation_title="Chat",
                  single_chat=1, trigger_message_id="trigger-2", trigger_create_time="time",
                  trigger_sender="Sender", trigger_text="input", oa_url="",
                  business_object_key="object")
    columns, expressions, parameters = source_insert("reply_task_inputs", values, {})
    db.execute('insert into tasks("_kind","id","legacy_key","parent_id",'
               + ",".join('"' + c + '"' for c in columns) + ") values(?,?,?, ?,"
               + ",".join(expressions) + ")",
               (TASK_RECORD_TYPES.code("reply_task_inputs"), 206, "6", 201, *parameters))
    for physical, owner, pid, generation, adapter in (
        (201, "reply-owner", 44, 7, "reply"),
        (220, "summary-owner", 45, 8, "work_summary"),
        (221, "meeting-owner", 46, 9, "meeting"),
    ):
        db.execute("update tasks set lease_owner=jsonb(?),owner_pid=jsonb(?),generation=jsonb(?),"
                   "adapter_name=jsonb(?) where id=?",
                   (f'"{owner}"', str(pid), str(generation), f'"{adapter}"', physical))
    db.execute("update tasks set current_input_id=205, claimed_input_id=206 where id=201")
    db.execute("update sqlite_sequence set seq=416921 where name='tasks'")
    db.commit()
    return db


def test_forward_migrates_all_twenty_families_to_native_task_and_domain_owners():
    with closing(_source()) as db:
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        result = migrate_task_entities(db, metadata)
        assert set(result["counts"]) == set(task20_definitions.TASK20_COLUMNS)
        assert {kind: count for kind, count in result["counts"].items() if kind != "reply_task_inputs"} == {kind: 1 for kind in task20_definitions.TASK20_COLUMNS if kind != "reply_task_inputs"}
        assert result["counts"]["reply_task_inputs"] == 2
        assert db.execute("select count(*) from tasks").fetchone()[0] == 9
        assert db.execute("select kind from tasks where id=201").fetchone()[0] == "reply"
        assert db.execute("select current_input_id,claimed_input_id from tasks where id=201").fetchone() == (205, 206)
        assert db.execute("select task_id,workbench_task_id from workbench_turns").fetchone() == (203, 202)
        assert db.execute("select target_id from task_run_identity_map where source_kind='workbench_tasks'").fetchone()[0] == 202
        assert db.execute("select status,typeof(status) from tasks where id=221").fetchone() == ("waiting", "text")
        assert db.execute("select status,typeof(status) from tasks where id=222").fetchone() == ("pending", "text")
        assert db.execute("select task_id,source_type from work_summary_inputs").fetchone() == (220, "reply_attempt")
        assert db.execute("select lease_owner,owner_pid,generation,adapter_name from tasks where id=201").fetchone() == ("reply-owner", 44, 7, "reply")
        assert db.execute("select lease_owner,owner_pid,generation,adapter_name from tasks where id=220").fetchone() == ("summary-owner", 45, 8, "work_summary")
        assert db.execute("select lease_owner,owner_pid,generation,adapter_name from tasks where id=221").fetchone() == ("meeting-owner", 46, 9, "meeting")
        assert db.execute("select lease_owner,owner_pid,generation,adapter_name from tasks where id=222").fetchone() == (None, None, None, None)
        assert db.execute("select count(*) from task_results").fetchone()[0] >= 4
        for kind in task20_definitions.TASK20_COLUMNS:
            if kind in {"reply_tasks", "meeting_alignment_jobs", "work_summary_inputs",
                        "okr_review_requests", "weekly_okr_analysis_jobs",
                        "email_agent_classification_tasks", "wechat_memory_import_jobs",
                        "workbench_turns"}:
                assert resolve_source_public_owner(db, source_kind=kind,
                    public_key={"reply_tasks": 201, "meeting_alignment_jobs": 221,
                        "work_summary_inputs": 220, "okr_review_requests": 222,
                        "weekly_okr_analysis_jobs": 223, "email_agent_classification_tasks": "classification-225",
                        "wechat_memory_import_jobs": 232,
                        "workbench_turns": "wb-turn-a"}[kind]) is not None
        assert resolve_dispatcher_claim_source_id(db, adapter_name="reply", source_id="201") == 201
        assert resolve_dispatcher_claim_source_id(db, adapter_name="meeting", source_id="999") is None
        assert resolve_source_public_owner(db, source_kind="workbench_tasks",
                                          public_key="wb-task-a") == 202
        assert resolve_source_public_owner(db, source_kind="workbench_tasks",
                                          public_key="202") is None
        assert db.execute("select seq from sqlite_sequence where name='tasks'").fetchone()[0] == 416921
        assert not list(db.execute("pragma foreign_key_check"))
        assert all(name in task_run_entity_objects() for name in (
            "tasks", "task_inputs", "task_results", "task_business_keys",
            "feedback_processing_items", "feedback_processing_rounds",
        ))


def test_source_declaration_rejection_happens_before_task_container_mutation():
    with closing(_source()) as db:
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        db.execute("create index extra_task_index on tasks(id)")
        before = db.execute("select id,_kind,legacy_key from tasks order by id").fetchall()
        try:
            migrate_task_entities(db, metadata)
        except ValueError as exc:
            assert "declaration or allocator changed" in str(exc)
        else:
            raise AssertionError("source declaration change was accepted")
        assert db.execute("select id,_kind,legacy_key from tasks order by id").fetchall() == before


def test_historical_task20_source_declaration_is_rejected_without_mutation():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=off")
        register_task8_functions(db)
        task20_current_schema.create_current_task20_schema(db)
        before = db.execute("select type,name,sql from sqlite_schema where name='tasks'").fetchall()
        db.commit()
        db.execute("begin immediate")
        try:
            capture_task_entities(db)
        except ValueError as exc:
            assert "unsupported current Task source" in str(exc)
        else:
            raise AssertionError("historical Task20 declaration was accepted as current source")
        assert db.execute("select type,name,sql from sqlite_schema where name='tasks'").fetchall() == before
