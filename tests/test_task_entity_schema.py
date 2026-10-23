from contextlib import closing
import json
import sqlite3
from uuid import UUID

import pytest

from app import task20_current_schema, task20_definitions
from app.task_lifecycle_current_schema import (
    create_current_task20_schema, task20_schema_is_current, task_lifecycle_objects,
)
from app.task8_storage import register_task8_functions, task8_set_parts
from app.task_entity_schema import (
    _objects, capture_task_entities, migrate_task_entities, resolve_dispatcher_claim_source_id,
    resolve_source_public_owner, restore_task_entities,
)
from app.task_run_entity_definitions import task_run_entity_objects
from tests.test_task20_definitions import payload
from app.task_lifecycle_storage import source_insert
from app.task_lifecycle_definitions import (
    LIFECYCLE_FIELDS, SOURCE_FIELDS, scalar as lifecycle_scalar,
    task_lifecycle_physical_field_map, task_physical_identity_field_map,
)
from app.storage_payload import TASK_RECORD_TYPES
from app.task12_storage import task12_projection, update_task12


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
            "feedback_processing_rounds": {"feedback_key": "feedback-a", "batch_id": "batch-a",
                                            "workbench_task_id": "wb-task-a", "workbench_turn_id": "wb-turn-a"},
            "feedback_iteration_decisions": {"batch_id": "batch-a"},
            "okr_review_items": {"request_id": 222},
        }.get(kind, {})
        values.update(changes)
        if kind == "feedback_processing_items":
            values.update(workbench_task_id="202", workbench_turn_id="203")
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


def test_native_reply_dto_preserves_source_pydantic_parsing_for_current_and_claimed_inputs():
    from app import task_entity_storage
    from app.store import (
        AutoReplyStore, REPLY_TASK_CLAIMED_SQL, REPLY_TASK_CURRENT_SQL,
    )

    with closing(_source()) as db:
        db.row_factory = sqlite3.Row
        for input_id, label in ((205, b"current"), (206, b"claimed")):
            input_parts = task8_set_parts(
                "reply_task_inputs",
                {
                    "trigger_text": sqlite3.Binary(b"typed " + label + b" text"),
                    "trigger_message_json": sqlite3.Binary(
                        b'{"typed":"' + label + b'"}',
                    ),
                },
            )
            db.execute(
                f"update tasks set {input_parts.sql} where _kind=? and id=?",
                (*input_parts.parameters, TASK_RECORD_TYPES.code("reply_task_inputs"), input_id),
            )
        db.commit()
        assert [tuple(row) for row in db.execute(
            "select typeof(trigger_text),trigger_text,typeof(trigger_message_json),"
            "trigger_message_json from ("
            + task20_definitions.task20_projection("reply_task_inputs")
            + ") where id in ('5','6') order by id"
        ).fetchall()] == [
            ("blob", b"typed current text", "blob", b'{"typed":"current"}'),
            ("blob", b"typed claimed text", "blob", b'{"typed":"claimed"}'),
        ]
        source_dtos = {}
        for claimed, projection in (
            (False, REPLY_TASK_CURRENT_SQL), (True, REPLY_TASK_CLAIMED_SQL),
        ):
            row = db.execute(
                f"select * from ({projection}) where id=201",
            ).fetchone()
            source_dtos[claimed] = AutoReplyStore._reply_task_from_row(row)
            label = "claimed" if claimed else "current"
            assert source_dtos[claimed].trigger_text == f"typed {label} text"
            assert source_dtos[claimed].trigger_message_json == f'{{"typed":"{label}"}}'

        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        migrate_task_entities(db, metadata)
        assert [tuple(row) for row in db.execute(
            "select typeof(trigger_text),trigger_text,typeof(trigger_message_json),"
            "trigger_message_json from task_inputs where id in (205,206) order by id"
        ).fetchall()] == [
            ("blob", b"typed current text", "blob", b'{"typed":"current"}'),
            ("blob", b"typed claimed text", "blob", b'{"typed":"claimed"}'),
        ]
        for claimed in (False, True):
            owner = task_entity_storage.get_task_with_inputs(db, 201)
            native = AutoReplyStore._reply_task_from_native(owner, claimed=claimed)
            source = source_dtos[claimed]
            assert type(native.trigger_text) is type(source.trigger_text)
            assert native.trigger_text == source.trigger_text
            assert type(native.trigger_message_json) is type(source.trigger_message_json)
            assert native.trigger_message_json == source.trigger_message_json
            assert native.id == source.id
            assert source.current_input_id == 5
            assert source.claimed_input_id == 6
            assert native.current_input_id == 205
            assert native.claimed_input_id == 206


def test_native_reply_dto_preserves_source_pydantic_fractional_attempts_error():
    from app import task_entity_storage
    from app.store import AutoReplyStore, REPLY_TASK_CURRENT_SQL
    from pydantic import ValidationError

    with closing(_source()) as db:
        db.row_factory = sqlite3.Row
        task_parts = task8_set_parts("reply_tasks", {"attempts": 1.25})
        db.execute(
            f"update tasks set {task_parts.sql} where _kind=? and id=201",
            (*task_parts.parameters, TASK_RECORD_TYPES.code("reply_tasks")),
        )
        db.commit()
        source_row = db.execute(
            f"select * from ({REPLY_TASK_CURRENT_SQL}) where id=201",
        ).fetchone()
        with pytest.raises(ValidationError):
            AutoReplyStore._reply_task_from_row(source_row)

        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        migrate_task_entities(db, metadata)
        owner = task_entity_storage.get_task_with_inputs(db, 201)
        with pytest.raises(ValidationError):
            AutoReplyStore._reply_task_from_native(owner)


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


def test_forward_and_public_owner_resolver_preserve_blob_typed_public_identity():
    with closing(_source()) as db:
        db.execute("update tasks set legacy_key=? where id=202", (b"\x00\xff",))
        db.commit()
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        migrate_task_entities(db, metadata)
        assert db.execute(
            "select public_key,typeof(public_key),public_key_type from task_run_identity_map "
            "where source_kind='workbench_tasks'"
        ).fetchone() == (b"\x00\xff", "blob", "blob")
        assert resolve_source_public_owner(db, source_kind="workbench_tasks",
                                          public_key=b"\x00\xff") == 202


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


def test_caller_rollback_restores_lifecycle_declaration_rows_and_allocator():
    with closing(_source()) as db:
        before_objects = task_lifecycle_objects()
        before_rows = db.execute("select * from tasks order by id").fetchall()
        before_types = db.execute(
            "select " + ",".join('typeof("' + row[1] + '")' for row in db.execute("pragma table_xinfo(tasks)") if row[6] == 0)
            + " from tasks order by id"
        ).fetchall()
        before_sequence = db.execute("select rowid,name,seq from sqlite_sequence order by rowid").fetchall()
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        result = migrate_task_entities(db, metadata)
        assert result["identity_edges"]
        db.rollback()
        assert _objects(db, "tasks") == before_objects
        assert db.execute("select * from tasks order by id").fetchall() == before_rows
        assert db.execute(
            "select " + ",".join('typeof("' + row[1] + '")' for row in db.execute("pragma table_xinfo(tasks)") if row[6] == 0)
            + " from tasks order by id"
        ).fetchall() == before_types
        assert db.execute("select rowid,name,seq from sqlite_sequence order by rowid").fetchall() == before_sequence


def _logical_snapshot(db):
    result = {}
    for kind in task20_definitions.TASK20_COLUMNS:
        projection = task20_definitions.task20_projection(kind)
        cursor = db.execute(projection)
        fields = tuple(column[0] for column in cursor.description)
        rows = cursor.fetchall()
        types = db.execute("select " + ",".join(f'typeof("{field}")' for field in fields)
                           + " from (" + projection + ")").fetchall()
        result[kind] = (fields, rows, types)
    return result


def _physical_fact_snapshot(db):
    fields = ["id", "legacy_key", "parent_id", "current_input_id", "claimed_input_id"]
    selected = []
    for field in fields:
        if field in {column[1] for column in db.execute("pragma table_xinfo(tasks)")}:
            selected.extend((f'"{field}"', f'typeof("{field}")'))
    for field in LIFECYCLE_FIELDS:
        scalar = lifecycle_scalar(field)
        selected.extend((scalar, f"typeof({scalar})"))
    return db.execute("select " + ",".join(selected) + " from tasks order by id").fetchall()


def _owned_table_names():
    return {name for name, sql in task_run_entity_objects().items()
            if sql.lstrip().lower().startswith("create table")}


def _schema_catalog(db):
    return db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema "
        "where type in ('table','index','trigger','view') order by type,name"
    ).fetchall()


def test_current_fact_inverse_restores_all_twenty_families_and_sql_types():
    with closing(_source()) as db:
        expected = _logical_snapshot(db)
        physical_expected = _physical_fact_snapshot(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        assert len(task_lifecycle_physical_field_map()) == 20 * len(LIFECYCLE_FIELDS)
        lifecycle_map = task_lifecycle_physical_field_map()
        assert all(row["owner_scope"] == "native_task" for row in lifecycle_map
                   if row["source_kind"] in SOURCE_FIELDS)
        assert all(row["owner_scope"] == "auxiliary_domain" and row["target_table"] is None
                   for row in lifecycle_map if row["source_kind"] not in SOURCE_FIELDS)
        identity_map = task_physical_identity_field_map()
        assert len(identity_map) == len(task20_definitions.TASK20_COLUMNS)
        assert {row["source_kind"] for row in identity_map
                if row["identity_semantics"] == "hidden_task12_rowid"} == {
                    "business_object_tasks", "email_historical_candidates",
                    "feedback_processing_batches", "feedback_processing_items",
                }
        restored = restore_task_entities(db, source_metadata, forward)
        assert restored["rows"] == 21
        assert restored["sparse_identity_metadata"] == {
            key: forward[key] for key in (
                "identity_edges", "raw_key_exceptions", "reference_exceptions",
            )
        }
        assert _logical_snapshot(db) == expected
        assert _physical_fact_snapshot(db) == physical_expected
        item = db.execute(task20_definitions.task20_projection("feedback_processing_items")).fetchone()
        assert item[3:5] == ("202", "203")  # unresolved numeric text despite native IDs 202 and 203
        decision = db.execute(task20_definitions.task20_projection("feedback_processing_rounds")).fetchone()
        assert decision[5:7] == ("wb-task-a", "wb-turn-a")
        for kind in set(task20_definitions.TASK20_COLUMNS) - set(SOURCE_FIELDS):
            values = db.execute("select " + ",".join(lifecycle_scalar(field) for field in LIFECYCLE_FIELDS)
                                + " from tasks where _kind=?",
                                (TASK_RECORD_TYPES.code(kind),)).fetchone()
            assert values is not None and values == (None,) * len(LIFECYCLE_FIELDS)
        assert _objects(db, "tasks") == source_metadata["source_tasks"]
        actual_sequence = db.execute("select rowid,seq from sqlite_sequence where name='tasks'").fetchone()
        assert actual_sequence == source_metadata["task_sequence"]
        assert task20_schema_is_current(db)
        for name in _owned_table_names() - {"tasks"}:
            assert db.execute("select 1 from main.sqlite_schema where type='table' and name=?",
                              (name,)).fetchone() is None
        assert db.execute("select rowid,business_object_key from ("
                          + task12_projection("business_object_tasks", include_rowid=True)
                          + ")").fetchone() == (224, "business-224")


def test_inverse_retires_owned_target_catalog_and_preserves_unrelated_schema_and_sequences():
    with closing(_source()) as db:
        db.executescript(
            "create table unrelated_data(id integer primary key autoincrement, value text);"
            "create table unrelated_audit(value text);"
            "create index idx_unrelated_data_value on unrelated_data(value);"
            "create trigger trg_unrelated_data_audit after update of value on unrelated_data "
            "begin insert into unrelated_audit values(new.value); end;"
            "insert into unrelated_data(value) values('keep');"
            "insert into unrelated_data(value) values('delete');"
            "delete from unrelated_data where value='delete';"
        )
        before_catalog = _schema_catalog(db)
        before_sequences = db.execute(
            "select rowid,name,seq from main.sqlite_sequence order by rowid"
        ).fetchall()
        before_unrelated_data = db.execute("select * from unrelated_data").fetchall()
        before_unrelated_audit = db.execute("select * from unrelated_audit").fetchall()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        restored = restore_task_entities(db, source_metadata, forward)

        assert task20_schema_is_current(db)
        assert _schema_catalog(db) == before_catalog
        assert db.execute("select rowid,name,seq from main.sqlite_sequence order by rowid").fetchall() == before_sequences
        assert db.execute("select * from unrelated_data").fetchall() == before_unrelated_data
        assert db.execute("select * from unrelated_audit").fetchall() == before_unrelated_audit
        assert db.execute("select sql from main.sqlite_schema where name='trg_unrelated_data_audit'").fetchone()[0]
        assert all(db.execute("select 1 from main.sqlite_schema where type='table' and name=?",
                              (name,)).fetchone() is None
                   for name in _owned_table_names() - {"tasks"})
        assert restored["sparse_identity_metadata"]["identity_edges"] == forward["identity_edges"]


def test_current_fact_inverse_reads_mutated_native_owners_and_reforwards_them():
    with closing(_source()) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        db.execute("update tasks set status='retry',lease_owner='new-owner',lease_expires_at='new-expiry' where id=201")
        db.execute("update tasks set parameters_json=jsonb_set(parameters_json,'$.title',jsonb('" + '"new title"' + "')) where id=221")
        db.execute("update task_inputs set trigger_text='new current input' where id=205")
        db.execute("update task_business_keys set business_key='new-business-key' where id=224")
        db.execute("update task_results set decision_json='new current result' where task_id=221")

        restore_task_entities(db, source_metadata, forward)
        assert db.execute("select lifecycle_status,lifecycle_lease_owner,lifecycle_lease_expires_at from tasks where id=201").fetchone() == (
            "retry", "new-owner", "new-expiry",
        )
        assert db.execute(task20_definitions.task20_projection("meeting_alignment_jobs")).fetchone()[2] == "new title"
        assert db.execute(task20_definitions.task20_projection("reply_task_inputs")).fetchone()[9] == "new current input"
        assert db.execute(task20_definitions.task20_projection("business_object_tasks")).fetchone()[0] == "new-business-key"
        assert db.execute(task20_definitions.task20_projection("meeting_alignment_jobs")).fetchone()[12] == "new current result"

        assert task20_schema_is_current(db)
        existing = db.execute(
            "select business_object_key from (" + task12_projection(
                "business_object_tasks", include_rowid=True) + ") where rowid=224"
        ).fetchone()
        assert existing == ("new-business-key",)
        assert update_task12(db, "business_object_tasks", 224,
                             {"business_object_key": "business-after-inverse"}) == 1
        assert db.execute(task20_definitions.task20_projection("business_object_tasks")).fetchone()[0] == "business-after-inverse"
        source_metadata = capture_task_entities(db)
        second_forward = migrate_task_entities(db, source_metadata)
        assert db.execute("select status,lease_owner,lease_expires_at from tasks where id=201").fetchone() == (
            "retry", "new-owner", "new-expiry",
        )
        assert db.execute("select trigger_text from task_inputs where id=205").fetchone() == ("new current input",)
        assert db.execute("select business_key from task_business_keys where id=224").fetchone() == (
            "business-after-inverse",
        )
        assert db.execute("select decision_json from task_results where task_id=221").fetchone() == (
            "new current result",
        )
        assert second_forward["identity_edges"] == forward["identity_edges"]


def test_native_input_new_fact_below_global_water_keeps_canonical_id_on_reforward():
    from app.task_entity_storage import append_task_input, bind_claimed_input, bind_current_input

    with closing(_source()) as db:
        # This is a valid predecessor TaskInput with a high per-kind ID below
        # the unrelated global Tasks allocator water.
        values = {
            "id": 404224, "reply_task_id": 201, "channel": "dingtalk",
            "conversation_id": "chat", "conversation_title": "Chat", "single_chat": 1,
            "trigger_message_id": "trigger-404224", "trigger_create_time": "time",
            "trigger_sender": "Sender", "trigger_text": "old input", "oa_url": "",
            "business_object_key": "object", "input_revision_key": "r1",
        }
        columns, expressions, parameters = source_insert("reply_task_inputs", values, {})
        db.execute(
            'insert into tasks("_kind","id","legacy_key","parent_id",'
            + ",".join('"' + field + '"' for field in columns) + ") values(?,?,?, ?,"
            + ",".join(expressions) + ")",
            (TASK_RECORD_TYPES.code("reply_task_inputs"), 404224, "404224", 201, *parameters),
        )
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        db.row_factory = sqlite3.Row
        appended, created = append_task_input(db, task_id=201, values={
            "channel": "dingtalk", "conversation_id": "chat",
            "conversation_title": "Chat", "single_chat": 1,
            "trigger_message_id": "trigger-404225", "trigger_create_time": "time",
            "trigger_sender": "Sender", "trigger_text": "new input",
            "oa_url": "", "business_object_key": "object",
            "input_revision_key": "r1",
        })
        assert created is True
        assert appended["id"] == 404225
        assert bind_current_input(db, task_id=201, input_id=404225,
                                  expected_input_version=1, input_version=2) is not None
        assert bind_claimed_input(db, task_id=201, input_id=404225,
                                  claimed_input_version=1) is not None

        inverse = restore_task_entities(db, source_metadata, forward)
        assert db.execute("select 1 from tasks where id=404225").fetchone() is None
        source_input = db.execute(
            "select id,legacy_key from tasks where record_type='reply_task_inputs' "
            "and cast(legacy_key as integer)=404225"
        ).fetchone()
        assert source_input is not None and source_input[0] > 416921
        pointers = db.execute("select current_input_id,claimed_input_id from tasks where id=201").fetchone()
        assert tuple(pointers) == (source_input[0], source_input[0])
        assert inverse["inverse_identity"] == {
            "version": 1,
            "owners": [{"source_kind": "reply_task_inputs",
                        "source_physical_id": source_input[0], "canonical_id": 404225}],
        }

        source_metadata = capture_task_entities(db)
        identity = json.loads(json.dumps(inverse["inverse_identity"]))
        context = json.loads(json.dumps(inverse["source_context"]))
        before_rows = db.execute("select * from tasks order by id").fetchall()
        try:
            migrate_task_entities(db, source_metadata, source_context=context)
        except ValueError as exc:
            assert "requires its inverse identity handoff" in str(exc)
        else:
            raise AssertionError("restored-source migration accepted a missing identity handoff")
        wrong = {"version": 1, "owners": []}
        try:
            migrate_task_entities(db, source_metadata, source_context=context,
                                  inverse_identity=wrong)
        except ValueError as exc:
            assert "does not match its context" in str(exc)
        else:
            raise AssertionError("restored-source migration accepted the wrong identity handoff")
        assert db.execute("select * from tasks order by id").fetchall() == before_rows
        refoward = migrate_task_entities(db, source_metadata, source_context=context,
                                         inverse_identity=identity)
        assert tuple(db.execute(
            "select id,trigger_text from task_inputs where id=404225"
        ).fetchone()) == (404225, "new input")
        assert tuple(db.execute("select physical_id,target_id from task_run_identity_map where source_kind=? "
                                "and public_key='404225' and public_key_type='integer'",
                                ("reply_task_inputs",)).fetchone()) == (source_input[0], 404225)
        second_inverse = restore_task_entities(db, source_metadata, refoward)
        assert second_inverse["inverse_identity"] == inverse["inverse_identity"]
        second_source = capture_task_entities(db)
        migrate_task_entities(db, second_source, source_context=second_inverse["source_context"],
                              inverse_identity=json.loads(json.dumps(second_inverse["inverse_identity"])))
        assert tuple(db.execute("select id,trigger_text from task_inputs where id=404225").fetchone()) == (
            404225, "new input",
        )


def test_native_business_key_collision_uses_predecessor_hidden_rowid_allocator():
    from app.task_entity_storage import (
        append_task_input, associate_business_key, bind_claimed_input,
        bind_current_input, create_task,
    )
    from app.task12_storage import insert_task12, update_task12

    with closing(_source()) as db:
        # Seed an authentic source Task at the next per-input ID.
        source_reply = db.execute("select * from ("
                                  + task20_definitions.task20_projection("meeting_alignment_jobs")
                                  + ") where id=221").fetchone()
        source_fields = [field[0] for field in task20_definitions.TASK20_COLUMNS["meeting_alignment_jobs"]]
        source_values = dict(zip(source_fields, source_reply))
        source_values.update(id=207, meeting_id="seeded-meeting-207")
        columns, expressions, parameters = source_insert("meeting_alignment_jobs", source_values, {})
        db.execute(
            'insert into tasks("_kind","id","legacy_key","parent_id",'
            + ",".join('"' + field + '"' for field in columns) + ") values(?,?,?, ?,"
            + ",".join(expressions) + ")",
            (TASK_RECORD_TYPES.code("meeting_alignment_jobs"), 207, "207", None, *parameters),
        )
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        db.row_factory = sqlite3.Row
        now = db.execute("select current_timestamp").fetchone()[0]
        new_task = create_task(db, values={"kind": "reply", "status": "pending",
                                           "available_at": now, "attempts": 0, "error": "",
                                           "execution_generation": "initial",
                                           "claimed_input_version": 0,
                                           "created_at": now, "updated_at": now}, parameters={
            "force_new_decision": 0, "manual_rerun_attempt_id": 0,
            "manual_rerun_revision_key": "", "recovery_code": "",
        })
        deleted_task = create_task(db, values={"kind": "reply", "status": "pending",
                                               "created_at": now, "updated_at": now})
        deleted_task_id = deleted_task["id"]
        db.execute("delete from tasks where id=?", (deleted_task_id,))
        first, first_created = append_task_input(db, task_id=new_task["id"], values={
            "channel": "dingtalk", "conversation_id": "new-chat", "conversation_title": "New",
            "single_chat": 1, "trigger_message_id": "new-trigger-1", "trigger_create_time": now,
            "trigger_sender": "Sender", "trigger_text": "older claimed input",
            "oa_url": "", "business_object_key": "new-object", "input_revision_key": "same-revision",
        })
        second, second_created = append_task_input(db, task_id=new_task["id"], values={
            "channel": "dingtalk", "conversation_id": "new-chat", "conversation_title": "New",
            "single_chat": 1, "trigger_message_id": "new-trigger-2", "trigger_create_time": now,
            "trigger_sender": "Sender", "trigger_text": "new current input",
            "oa_url": "", "business_object_key": "new-object", "input_revision_key": "same-revision",
        })
        assert first_created and second_created and (first["id"], second["id"]) == (207, 208)
        assert bind_claimed_input(db, task_id=new_task["id"], input_id=first["id"],
                                  claimed_input_version=1)
        assert bind_current_input(db, task_id=new_task["id"], input_id=first["id"],
                                  expected_input_version=None, input_version=1)
        assert bind_current_input(db, task_id=new_task["id"], input_id=second["id"],
                                  expected_input_version=1, input_version=2)
        created = associate_business_key(db, task_id=new_task["id"], business_key="new-business-object",
                                         created_at=now, updated_at=now)
        assert created["id"] == 225  # existing native Task id 225 belongs to another owner
        second_key = associate_business_key(db, task_id=new_task["id"], business_key="second-business-object",
                                            created_at=now, updated_at=now)
        assert second_key["id"] == 226

        inverse = restore_task_entities(db, source_metadata, forward)
        assert tuple(db.execute("select record_type from tasks where id=225").fetchone()) == (
            "email_agent_classification_tasks",
        )
        assert tuple(db.execute("select record_type from tasks where id=207").fetchone()) == (
            "meeting_alignment_jobs",
        )
        row = db.execute(
            "select id,legacy_key from tasks where record_type='business_object_tasks' and legacy_key='225'"
        ).fetchone()
        assert row is not None and row[0] > 416921 and row[1] == "225"
        first_identity = tuple(db.execute(
            "select id,legacy_key,typeof(legacy_key) from tasks "
            "where record_type='business_object_tasks' and legacy_key='225'"
        ).fetchone())
        handoff_by_owner = {(owner["source_kind"], owner["canonical_id"]): owner["source_physical_id"]
                            for owner in inverse["inverse_identity"]["owners"]}
        assert handoff_by_owner[("business_object_tasks", 225)] == row[0]
        assert db.execute("select id from tasks where record_type='reply_tasks' and legacy_key=?",
                          (str(new_task["id"]),)).fetchone()[0] == new_task["id"]
        for kind, canonical_id in (("reply_task_inputs", first["id"]),
                                   ("reply_task_inputs", second["id"]),
                                   ("business_object_tasks", second_key["id"])):
            assert handoff_by_owner[(kind, canonical_id)] > 416921
        assert update_task12(db, "business_object_tasks", 225,
                             {"business_object_key": "updated-native-business-object"}) == 1
        old_write = insert_task12(db, "business_object_tasks", {
            "business_object_key": "cold-old-business-object", "reply_task_id": 201,
        })
        assert old_write.rowcount == 1 and old_write.physical_id > 416921

        source_metadata = capture_task_entities(db)
        refoward = migrate_task_entities(db, source_metadata, source_context=inverse["source_context"],
                                        inverse_identity=inverse["inverse_identity"])
        assert refoward["counts"]["reply_task_inputs"] >= 2
        assert tuple(db.execute("select current_input_id,claimed_input_id,input_version,claimed_input_version "
                                "from tasks where id=?", (new_task["id"],)).fetchone()) == (
            second["id"], first["id"], 2, 1,
        )
        assert tuple(db.execute("select id,business_key from task_business_keys where id=225").fetchone()) == (
            225, "updated-native-business-object",
        )
        assert tuple(db.execute("select id,business_key from task_business_keys where id=226").fetchone()) == (
            226, "second-business-object",
        )
        assert tuple(db.execute(
            "select physical_id,target_id,public_key from task_run_identity_map "
            "where source_kind='business_object_tasks'"
        ).fetchone()) == (first_identity[0], 225, None)
        # A second inverse with no native fact edits must preserve the hidden
        # Task12 physical row/key identity used by old point writers.
        second_inverse = restore_task_entities(db, source_metadata, refoward)
        second_identity_row = db.execute(
            "select id,legacy_key,typeof(legacy_key) from tasks "
            "where record_type='business_object_tasks' and legacy_key='225'"
        ).fetchone()
        assert second_identity_row is not None
        second_identity = tuple(second_identity_row)
        assert second_identity == first_identity
        assert update_task12(db, "business_object_tasks", 225,
                             {"business_object_key": "second-cycle-old-writer"}) == 1
        second_source_metadata = capture_task_entities(db)
        migrate_task_entities(
            db, second_source_metadata, source_context=second_inverse["source_context"],
            inverse_identity=second_inverse["inverse_identity"],
        )
        assert tuple(db.execute(
            "select id,business_key from task_business_keys where id=225"
        ).fetchone()) == (225, "second-cycle-old-writer")
        next_task = create_task(db, values={"kind": "reply", "status": "pending",
                                            "created_at": now, "updated_at": now})
        assert next_task["id"] > deleted_task_id
        assert db.execute("select business_key from task_business_keys where id=?",
                          (old_write.physical_id,)).fetchone()[0] == "cold-old-business-object"


def test_native_workbench_session_collision_and_turn_preserve_source_uuid_and_opaque_session_ref():
    from app.task_entity_storage import create_task

    with closing(_source()) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        db.row_factory = sqlite3.Row
        # Native Workbench session IDs and Task IDs are independent namespaces.
        # Session 203 collides with the existing native workbench-turn Task 203.
        db.execute(
            "insert into workbench_tasks(title,runtime_kind,provider_session_ref) values(?,?,?)",
            ("New Workbench", "codex", "opaque:provider-session/ref"),
        )
        session_id = db.execute("select id from workbench_tasks where title='New Workbench'").fetchone()[0]
        assert session_id == 203
        now = db.execute("select current_timestamp").fetchone()[0]
        turn_task = create_task(db, values={"kind": "workbench_turn", "status": "queued",
                                            "created_at": now, "updated_at": now,
                                            "lease_owner": "", "lease_expires_at": ""})
        db.execute(
            "insert into workbench_turns(task_id,workbench_task_id,client_request_id,task_sequence,"
            "user_text,stop_requested,error_code,error_detail,resume_context,execution_run_id,"
            "runtime_quiesced_run_id,started_at,completed_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (turn_task["id"], session_id, "client-request-uuid", 1, "real user input", 0,
             "", "", "", "", "", "", ""),
        )

        inverse = restore_task_entities(db, source_metadata, forward)
        session = db.execute(
            "select id,legacy_key,payload_json from tasks where record_type='workbench_tasks' "
            "and json_extract(payload_json,'$[0]')='New Workbench'"
        ).fetchone()
        assert session is not None and session[0] > 416921
        session_public_id = db.execute(
            "select id from (" + task20_definitions.task20_projection("workbench_tasks")
            + ") where title='New Workbench'"
        ).fetchone()[0]
        UUID(session_public_id)
        session_row = db.execute(
            "select provider_session_ref from (" + task20_definitions.task20_projection("workbench_tasks")
            + ") where id=?", (session_public_id,),
        ).fetchone()
        assert tuple(session_row) == ("opaque:provider-session/ref",)
        turn = db.execute("select id,task_id,client_request_id from ("
                          + task20_definitions.task20_projection("workbench_turns")
                          + ") where client_request_id='client-request-uuid'").fetchone()
        UUID(turn[0])
        assert tuple(turn)[1:] == (session_public_id, "client-request-uuid")
        assert any(row["source_kind"] == "workbench_tasks"
                   and row["canonical_id"] == 203 for row in inverse["inverse_identity"]["owners"])

        source_metadata = capture_task_entities(db)
        migrate_task_entities(db, source_metadata, source_context=inverse["source_context"],
                              inverse_identity=inverse["inverse_identity"])
        assert db.execute("select provider_session_ref from workbench_tasks where id=203").fetchone()[0] == "opaque:provider-session/ref"
        turn = db.execute("select identity.public_key,owner.workbench_task_id,owner.client_request_id "
                          "from workbench_turns owner join task_run_identity_map identity "
                          "on identity.source_kind='workbench_turns' and identity.target_id=owner.task_id "
                          "where owner.client_request_id='client-request-uuid'").fetchone()
        assert tuple(turn)[1:] == (203, "client-request-uuid")
        UUID(turn[0])


def test_current_inverse_keeps_marker_shaped_text_parameter_as_text():
    with closing(_source()) as db:
        marker_text = '{"t":"blob","v":"ff"}'
        meeting_id_path = task20_definitions.TASK20_LAYOUTS["meeting_alignment_jobs"].path("meeting_id")
        db.execute("update tasks set payload_json=jsonb_set(payload_json,?,jsonb(?)) where id=221",
                   (meeting_id_path, json.dumps(marker_text)))
        db.commit()
        projection = task20_definitions.task20_projection("meeting_alignment_jobs")
        assert db.execute("select meeting_id,typeof(meeting_id) from (" + projection + ")").fetchone() == (
            marker_text, "text",
        )
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        db.execute("update tasks set parameters_json=jsonb_set(parameters_json,'$.meeting_id',jsonb(?)) "
                   "where id=221", (json.dumps(marker_text),))
        assert db.execute("select json_type(parameters_json,'$.meeting_id'),"
                          "json_extract(parameters_json,'$.meeting_id') from tasks where id=221").fetchone() == (
            "text", marker_text,
        )
        restore_task_entities(db, source_metadata, forward)
        restored = db.execute("select meeting_id,typeof(meeting_id) from ("
                              + task20_definitions.task20_projection("meeting_alignment_jobs")
                              + ")").fetchone()
        assert restored == (marker_text, "text")


def test_current_inverse_decodes_codec_scalars_from_the_whole_native_parameter_object():
    from app import task20_definitions as definitions

    cases = (
        ({"t": "blob", "v": "ff"}, b"\xff", "blob"),
        ({"t": "real", "m": 3, "e": 1}, "6.0", "text"),
        ({"t": "inf"}, "Inf", "text"),
        ({"t": "-inf"}, "-Inf", "text"),
    )
    for native_codec_value, expected_value, expected_type in cases:
        with closing(_source()) as db:
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            source_metadata = capture_task_entities(db)
            forward = migrate_task_entities(db, source_metadata)
            db.execute("update tasks set parameters_json=jsonb_set(parameters_json,'$.meeting_id',jsonb(?)) "
                       "where id=221", (json.dumps(native_codec_value),))
            restore_task_entities(db, source_metadata, forward)
            assert db.execute("select meeting_id,typeof(meeting_id) from ("
                              + definitions.task20_projection("meeting_alignment_jobs")
                              + ")").fetchone() == (expected_value, expected_type)


def test_meeting_identity_index_preserves_source_blob_marker_text_and_text_affinity():
    with closing(_source()) as db:
        marker_text = '{"t":"blob","v":"70726f76696465722d6964"}'
        source_rows = (
            (416922, sqlite3.Binary(b"provider-id")),
            (416923, marker_text),
            (416924, 6.0),
            (416925, float("inf")),
        )
        for physical_id, meeting_id in source_rows:
            values = payload(db, "meeting_alignment_jobs")
            values["meeting_id"] = meeting_id
            columns, expressions, parameters = source_insert("meeting_alignment_jobs", values, {})
            db.execute(
                'insert into tasks("_kind","id","legacy_key","parent_id",'
                + ",".join('"' + column + '"' for column in columns) + ") values(?,?,?,? ,"
                + ",".join(expressions) + ")",
                (TASK_RECORD_TYPES.code("meeting_alignment_jobs"), physical_id,
                 str(physical_id), None, *parameters),
            )
        assert db.execute(
            "select meeting_id,typeof(meeting_id) from ("
            + task20_definitions.task20_projection("meeting_alignment_jobs")
            + ") where id in ('416922','416923','416924','416925') order by id"
        ).fetchall() == [
            (b"provider-id", "blob"), (marker_text, "text"), ("6.0", "text"), ("Inf", "text"),
        ]

        duplicate = payload(db, "meeting_alignment_jobs")
        duplicate["meeting_id"] = sqlite3.Binary(b"provider-id")
        columns, expressions, parameters = source_insert("meeting_alignment_jobs", duplicate, {})
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                'insert into tasks("_kind","id","legacy_key","parent_id",'
                + ",".join('"' + column + '"' for column in columns) + ") values(?,?,?,? ,"
                + ",".join(expressions) + ")",
                (TASK_RECORD_TYPES.code("meeting_alignment_jobs"), 416926, "416926", None,
                 *parameters),
            )

        db.commit()
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        migrate_task_entities(db, source_metadata)
        assert db.execute(
            "select typeof(jsonb_extract(parameters_json,'$.meeting_id')),"
            "json_type(parameters_json,'$.meeting_id') from tasks where id in "
            "(416922,416923,416924,416925) order by id"
        ).fetchall() == [
            ("blob", "object"), ("text", "text"), ("text", "text"), ("text", "text"),
        ]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("insert into tasks(kind,status,parameters_json,created_at,updated_at) "
                       "values('meeting_alignment','waiting',jsonb(?),'now','now')",
                       (json.dumps({"meeting_id": {"t": "blob", "v": "70726f76696465722d6964"}}),))
        db.rollback()


def test_inverse_preserves_hidden_task12_rowid_legacy_key_for_natural_key_owner():
    with closing(_source()) as db:
        physical_id = db.execute(
            "select id from tasks where record_type='business_object_tasks'"
        ).fetchone()[0]
        db.execute("update tasks set legacy_key='7' where id=?", (physical_id,))
        db.commit()
        before = db.execute("select legacy_key,typeof(legacy_key) from tasks where id=?",
                            (physical_id,)).fetchone()
        assert before == ("7", "text")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        assert any(row["source_kind"] == "business_object_tasks"
                   and row["physical_id"] == physical_id
                   and row["value"] == "7" and row["type"] == "text"
                   for row in forward["raw_key_exceptions"])
        restore_task_entities(db, source_metadata, forward)
        assert db.execute("select legacy_key,typeof(legacy_key) from tasks where id=?",
                          (physical_id,)).fetchone() == before
        assert task20_schema_is_current(db)
        second = capture_task_entities(db)
        refoward = migrate_task_entities(db, second)
        assert any(row["source_kind"] == "business_object_tasks"
                   and row["physical_id"] == physical_id
                   and row["value"] == "7" and row["type"] == "text"
                   for row in refoward["raw_key_exceptions"])


def test_mid_inverse_failure_rolls_back_when_caller_rolls_back():
    with closing(_source()) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        db.commit()
        db.execute("begin immediate")
        before_objects = task_run_entity_objects()
        before_catalog = _schema_catalog(db)
        before_sequences = db.execute(
            "select rowid,name,seq from main.sqlite_sequence order by rowid"
        ).fetchall()
        before_tasks = db.execute("select * from tasks order by id").fetchall()
        before_owned_rows = {
            name: db.execute('select * from "' + name.replace('"', '""') + '" order by rowid').fetchall()
            for name in _owned_table_names() if db.execute(
                "select 1 from main.sqlite_schema where type='table' and name=?", (name,)
            ).fetchone()
        }
        bad = dict(forward)
        bad["raw_key_exceptions"] = [dict(row) for row in forward["raw_key_exceptions"]]
        marker = next(row for row in bad["raw_key_exceptions"]
                      if row["source_kind"] == "email_agent_classification_tasks")
        marker["value"] = "not-an-integer-source-key"
        try:
            restore_task_entities(db, source_metadata, bad)
        except sqlite3.IntegrityError:
            db.rollback()
        else:
            raise AssertionError("invalid current-fact inverse marker was accepted")
        assert db.execute("select * from tasks order by id").fetchall() == before_tasks
        assert _schema_catalog(db) == before_catalog
        assert db.execute("select rowid,name,seq from main.sqlite_sequence order by rowid").fetchall() == before_sequences
        for name, rows in before_owned_rows.items():
            assert db.execute('select * from "' + name.replace('"', '""') + '" order by rowid').fetchall() == rows
        for name, sql in before_objects.items():
            if not sql.lstrip().lower().startswith("create table"):
                continue
            actual = db.execute("select sql from sqlite_schema where name=?", (name,)).fetchone()
            assert actual is not None
            assert " ".join(actual[0].split()) == " ".join(sql.split())


def test_inverse_rejects_extra_native_index_before_mutation():
    with closing(_source()) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        db.execute("create index idx_task_inverse_unexpected on tasks(kind)")
        before_with_extra = db.execute("select * from tasks order by id").fetchall()
        try:
            restore_task_entities(db, source_metadata, forward)
        except ValueError as exc:
            assert "declaration changed" in str(exc)
        else:
            raise AssertionError("inverse accepted an extra native index")
        assert db.execute("select * from tasks order by id").fetchall() == before_with_extra
        assert db.execute("select 1 from sqlite_schema where name='idx_task_inverse_unexpected'").fetchone()
