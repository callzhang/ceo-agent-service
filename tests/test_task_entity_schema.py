from contextlib import closing
from contextvars import ContextVar
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
from app.task20_definitions import task20_projection


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


def test_remapped_reply_task_rebinds_current_and_claimed_source_input_ids():
    with closing(_source()) as db:
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata, inverse_identity={
            "version": 1,
            "owners": [{"source_kind": "reply_tasks", "source_physical_id": 201,
                        "canonical_id": 1201}],
        })
        native = db.execute(
            "select current_input_id,claimed_input_id,input_version,claimed_input_version "
            "from tasks where id=1201").fetchone()
        assert native == (205, 206, 1, 0)

        inverse = restore_task_entities(db, source_metadata, forward)
        assert tuple(db.execute(
            "select current_input_id,claimed_input_id "
            "from tasks where record_type='reply_tasks' and id=201").fetchone()) == (205, 206)
        assert inverse["inverse_identity"]["owners"] == [{
            "source_kind": "reply_tasks", "source_physical_id": 201,
            "canonical_id": 1201,
        }]


def test_dispatcher_state_task_references_forward_inverse_and_preserve_unknown_rows():
    from app.state_storage import create_state_schema, insert_state

    with closing(_source()) as db:
        db.row_factory = sqlite3.Row
        db.execute("begin immediate")
        create_state_schema(db)
        for adapter, source_id in (("reply", "201"),
                                   ("scheduled_execution", "201"),
                                   ("reply", "999")):
            insert_state(db, "dispatcher_claim_leases", {
                "adapter_name": adapter, "source_id": source_id,
                "owner": "owner", "owner_pid": 8, "generation": 3,
                "lease_expires_at": "2099-01-01 00:00:00", "terminal_at": "",
                "last_error": "", "created_at": "created", "updated_at": "updated",
            })
        before = [tuple(row) for row in db.execute(
            "select cast(legacy_key as integer),adapter_name,source_id,payload_json "
            "from state where record_type='dispatcher_claim_leases' order by adapter_name,source_id")]
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata, inverse_identity={
            "version": 1,
            "owners": [{"source_kind": "reply_tasks", "source_physical_id": 201,
                        "canonical_id": 1201}],
        })
        current = [tuple(row) for row in db.execute(
            "select cast(legacy_key as integer),adapter_name,source_id,payload_json "
            "from state where record_type='dispatcher_claim_leases' order by adapter_name,source_id")]
        assert [(row[0], row[1], row[2]) for row in current] == [
            (row[0], row[1], "1201" if row[2] == "201" else row[2]) for row in before
        ]
        assert {row[0] for row in current} == {row[0] for row in before}
        for rowid, _adapter, source_id, payload_json in current:
            current_payload = json.loads(payload_json)
            source_payload = json.loads(next(row[3] for row in before if row[0] == rowid))
            assert current_payload["source_id"] == source_id
            current_payload.pop("source_id")
            source_payload.pop("source_id")
            assert current_payload == source_payload
        assert next(row[3] for row in current if row[2] == "999") == next(
            row[3] for row in before if row[2] == "999")

        restore_task_entities(db, source_metadata, forward)
        restored = [tuple(row) for row in db.execute(
            "select cast(legacy_key as integer),adapter_name,source_id,payload_json "
            "from state where record_type='dispatcher_claim_leases' order by adapter_name,source_id")]
        assert restored == before
        from app.state_storage import update_state
        assert update_state(db, "dispatcher_claim_leases", restored[0][0],
                            {"owner": "changed-owner"}) == 1
        second_source = capture_task_entities(db)
        second_forward = migrate_task_entities(db, second_source, inverse_identity={
            "version": 1,
            "owners": [{"source_kind": "reply_tasks", "source_physical_id": 201,
                        "canonical_id": 1201}],
        })
        assert [row[0] for row in db.execute(
            "select source_id from state where record_type='dispatcher_claim_leases' "
            "and adapter_name in ('reply','scheduled_execution') order by adapter_name")] == [
                "1201", "999", "1201",
            ]
        restore_task_entities(db, second_source, second_forward)
        assert tuple(db.execute(
            "select owner,source_id from state where record_type='dispatcher_claim_leases' "
            "and adapter_name='reply' and source_id='201'").fetchone()) == (
                "changed-owner", "201",
            )


def test_dispatcher_state_unknown_reference_cannot_alias_remapped_native_task_id():
    from app.state_storage import create_state_schema, insert_state

    with closing(_source()) as db:
        db.row_factory = sqlite3.Row
        db.execute("begin immediate")
        create_state_schema(db)
        insert_state(db, "dispatcher_claim_leases", {
            "adapter_name": "reply", "source_id": "42", "owner": "stale",
            "owner_pid": 8, "generation": 3, "lease_expires_at": "",
            "terminal_at": "", "last_error": "", "created_at": "created",
            "updated_at": "updated",
        })
        metadata = capture_task_entities(db)
        with pytest.raises(ValueError, match="collides with a native owner"):
            migrate_task_entities(db, metadata, inverse_identity={
                "version": 1,
                "owners": [{"source_kind": "reply_tasks", "source_physical_id": 201,
                            "canonical_id": 42}],
            })
        db.rollback()
        assert db.execute("select record_type from tasks where id=201").fetchone()[0] == "reply_tasks"


def test_feedback_workbench_and_current_round_refs_follow_typed_handoff_two_cycles():
    with closing(_source()) as db:
        from app.task12_storage import update_task12

        db.execute("begin immediate")
        append_only = db.execute(
            "select sql from sqlite_schema where type='trigger' "
            "and name='trg_task20_decision_update'").fetchone()[0]
        db.execute("drop trigger trg_task20_decision_update")
        update_task12(db, "feedback_iteration_decisions", 227, {
            "workbench_task_id": "wb-task-a", "workbench_turn_id": "wb-turn-a",
        })
        update_task12(db, "feedback_processing_items", 228, {
            "workbench_task_id": "wb-task-a", "workbench_turn_id": "wb-turn-a",
            "current_round_id": 229,
        })
        update_task12(db, "feedback_processing_rounds", 229, {
            "scope_receipt_json": sqlite3.Binary(json.dumps({
                "decision_id": 227, "scope": "code",
                "evidence": {"associations": {"feedback-a": {
                    "workbench_task_id": "wb-task-a", "workbench_turn_id": "wb-turn-a",
                    "attempt_id": 44, "agent_run_id": 13003,
                }}, "private_note": "preserve this"},
            }, sort_keys=True).encode()),
        })
        db.execute(append_only)
        source_metadata = capture_task_entities(db)
        handoff = {"version": 1, "owners": [
            {"source_kind": "workbench_tasks", "source_physical_id": 202, "canonical_id": 1202},
            {"source_kind": "workbench_turns", "source_physical_id": 203, "canonical_id": 1203},
            {"source_kind": "feedback_iteration_decisions", "source_physical_id": 227, "canonical_id": 1227},
            {"source_kind": "feedback_processing_rounds", "source_physical_id": 229, "canonical_id": 1229},
        ]}

        forward = migrate_task_entities(db, source_metadata, inverse_identity=handoff)
        assert db.execute("select workbench_task_id,workbench_turn_id,current_round_id "
                          "from feedback_processing_items where task_id=228").fetchone() == (
            "1202", "1203", 1229)
        assert db.execute("select workbench_task_id,workbench_turn_id "
                          "from feedback_iteration_decisions where id=1227").fetchone() == (
            "1202", "1203")
        mapped_decision = db.execute(
            "select id from feedback_iteration_decisions order by id limit 1"
        ).fetchone()[0]
        native_receipt = json.loads(db.execute(
            "select scope_receipt_json from feedback_processing_rounds where id=1229"
        ).fetchone()[0])
        assert db.execute(
            "select typeof(scope_receipt_json) from feedback_processing_rounds where id=1229"
        ).fetchone()[0] == "blob"
        assert native_receipt["decision_id"] == mapped_decision
        assert native_receipt["evidence"]["associations"]["feedback-a"] == {
            "workbench_task_id": "1202", "workbench_turn_id": "1203",
            "attempt_id": 44, "agent_run_id": 13003,
        }
        assert native_receipt["evidence"]["private_note"] == "preserve this"
        native_receipt["evidence"]["associations"]["feedback-a"]["attempt_id"] = 45
        native_receipt["evidence"]["private_note"] = "current native edit"
        db.execute(
            "update feedback_processing_rounds set scope_receipt_json=? where id=1229",
            (sqlite3.Binary(json.dumps(native_receipt, sort_keys=True).encode()),),
        )
        inverse = restore_task_entities(db, source_metadata, forward)
        item_projection = task12_projection("feedback_processing_items", include_rowid=True)
        assert db.execute("select workbench_task_id,workbench_turn_id,current_round_id from ("
                          + item_projection + ") where rowid=228").fetchone() == (
            "wb-task-a", "wb-turn-a", 229)
        decision_projection = task12_projection("feedback_iteration_decisions", include_rowid=True)
        assert db.execute("select workbench_task_id,workbench_turn_id from ("
                          + decision_projection + ") where rowid=227").fetchone() == (
            "wb-task-a", "wb-turn-a")
        restored_receipt = json.loads(db.execute(
            "select scope_receipt_json from (" + task12_projection(
                "feedback_processing_rounds", include_rowid=True
            ) + ") where rowid=229"
        ).fetchone()[0])
        assert db.execute(
            "select typeof(scope_receipt_json) from (" + task12_projection(
                "feedback_processing_rounds", include_rowid=True
            ) + ") where rowid=229"
        ).fetchone()[0] == "blob"
        assert restored_receipt["decision_id"] == 227
        assert restored_receipt["evidence"]["associations"]["feedback-a"] == {
            "workbench_task_id": "wb-task-a", "workbench_turn_id": "wb-turn-a",
            "attempt_id": 45, "agent_run_id": 13003,
        }
        assert restored_receipt["evidence"]["private_note"] == "current native edit"

        second = migrate_task_entities(db, source_metadata,
            source_context=inverse["source_context"], inverse_identity=inverse["inverse_identity"])
        assert db.execute("select workbench_task_id,workbench_turn_id,current_round_id "
                          "from feedback_processing_items where task_id=228").fetchone() == (
            "1202", "1203", 1229)
        reforward_receipt = json.loads(db.execute(
            "select scope_receipt_json from feedback_processing_rounds where id=1229"
        ).fetchone()[0])
        assert reforward_receipt["evidence"]["associations"]["feedback-a"]["attempt_id"] == 45
        restore_task_entities(db, source_metadata, second)
        assert db.execute("select current_round_id from (" + item_projection + ") where rowid=228").fetchone() == (229,)
        final_receipt = json.loads(db.execute(
            "select scope_receipt_json from (" + task12_projection(
                "feedback_processing_rounds", include_rowid=True
            ) + ") where rowid=229"
        ).fetchone()[0])
        assert final_receipt["evidence"]["associations"]["feedback-a"]["attempt_id"] == 45
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_feedback_current_reference_clears_survive_inverse_despite_captured_markers():
    with closing(_source()) as db:
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        # Source item 228 has unresolved numeric-looking Workbench refs and an
        # unresolved current-round pointer. Claim resets these exact projections.
        db.execute(
            "update feedback_processing_items set workbench_task_id='', "
            "workbench_turn_id='', current_round_id=0 where task_id=228"
        )

        restore_task_entities(db, source_metadata, forward)

        item = db.execute(
            "select workbench_task_id,workbench_turn_id,current_round_id from ("
            + task12_projection("feedback_processing_items", include_rowid=True)
            + ") where rowid=228"
        ).fetchone()
        assert item == ("", "", 0)


def test_feedback_reference_helpers_preserve_null_and_noninteger_pointer_facts():
    from app.task_entity_migration import (
        restore_feedback_round_pointer, restore_feedback_workbench_ref,
        translate_feedback_round_pointer, translate_feedback_workbench_refs,
    )

    owners = {"public": {
        ("workbench_tasks", ("text", "wb-task-a")): 1202,
        ("workbench_turns", ("text", "wb-turn-a")): 1203,
        ("feedback_processing_rounds", ("integer", 229)): 1229,
    }}
    assert translate_feedback_workbench_refs(
        task_ref=None, turn_ref="wb-turn-a", task_indexes=owners, turn_indexes=owners) == (
            None, "1203")
    assert restore_feedback_workbench_ref(
        field="workbench_task_id", current_value=None, indexes=owners) is None
    assert translate_feedback_round_pointer("1229x", owners) == "1229x"
    assert restore_feedback_round_pointer("1229", owners) == "1229"
    blob = b"1202"
    integer = 1202
    assert translate_feedback_workbench_refs(
        task_ref=blob, turn_ref=integer, task_indexes=owners, turn_indexes=owners) == (
            blob, integer)
    assert restore_feedback_workbench_ref(
        field="workbench_task_id", current_value=blob, indexes=owners) == blob


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


def test_inverse_handoff_json_keeps_captured_blob_parent_out_of_uuid_file_map():
    with closing(_source()) as db:
        db.execute("update tasks set legacy_key=? where id=202", (b"\x00\xff",))
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, metadata)
        inverse = restore_task_entities(db, metadata, forward)
        portable = json.loads(json.dumps(inverse["inverse_identity"]))
        assert portable.get("workbench_attachment_files", []) == []
        assert db.execute("select legacy_key,typeof(legacy_key) from tasks where id=202").fetchone() == (
            b"\x00\xff", "blob",
        )
        migrate_task_entities(
            db, metadata, source_context=inverse["source_context"],
            inverse_identity=portable,
        )
        assert resolve_source_public_owner(
            db, source_kind="workbench_tasks", public_key=b"\x00\xff",
        ) == 202


def test_inverse_handoff_json_preserves_blob_workbench_artifact_identity():
    from app.storage_payload import TASK_RECORD_TYPES
    from app.task_lifecycle_storage import source_assignments

    with closing(_source()) as db:
        physical_id = db.execute(
            "select id from tasks where _kind=?",
            (TASK_RECORD_TYPES.code("workbench_artifacts"),),
        ).fetchone()[0]
        assignments = source_assignments(
            "workbench_artifacts", {"id": b"\x00\xff"}, {},
            payload_source="tasks.payload_json", source_alias="tasks",
        )
        db.execute(
            f"update tasks set {assignments.sql} where _kind=? and id=?",
            (*assignments.parameters, TASK_RECORD_TYPES.code("workbench_artifacts"), physical_id),
        )
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, metadata)
        inverse = restore_task_entities(db, metadata, forward)
        identity = json.loads(json.dumps(inverse["inverse_identity"]))
        owner = next(
            entry for entry in identity["workbench_artifact_identities"]
            if entry["source_physical_id"] == physical_id
        )
        assert owner["source_public_id"] == {"t": "blob", "v": "00ff"}
        migrate_task_entities(
            db, metadata, source_context=inverse["source_context"], inverse_identity=identity,
        )
        assert db.execute(
            "select public_key,typeof(public_key),public_key_type from task_run_identity_map "
            "where source_kind='workbench_artifacts'"
        ).fetchone() == (b"\x00\xff", "blob", "blob")


def test_new_native_workbench_artifact_inverse_allocates_numeric_source_legacy_key():
    with closing(_source()) as db:
        artifact_kind = TASK_RECORD_TYPES.code("workbench_artifacts")
        db.execute(
            "update tasks set legacy_key='900000' where _kind=?",
            (artifact_kind,),
        )
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        assert db.execute(
            "select legacy_key from tasks where _kind=?",
            (artifact_kind,),
        ).fetchone() == ("900000",)
        forward = migrate_task_entities(db, metadata)
        cursor = db.execute(
            "insert into workbench_artifacts(workbench_turn_id,label,path,media_type,created_at) "
            "values(203,'native','/tmp/native.bin','application/octet-stream','created')"
        )
        canonical_id = int(cursor.lastrowid)
        inverse = restore_task_entities(db, metadata, forward)
        artifact_identity = next(
            item for item in inverse["inverse_identity"]["workbench_artifact_identities"]
            if item["canonical_id"] == canonical_id
        )
        source_physical_id = artifact_identity["source_physical_id"]
        source_row = db.execute(
            "select legacy_key,typeof(legacy_key),parent_id from tasks "
            "where record_type='workbench_artifacts' and id=?",
            (source_physical_id,),
        ).fetchone()
        assert source_row[1:] == ("text", 203)
        source_legacy_rowid = int(source_row[0])
        assert source_legacy_rowid > 900000
        assert source_legacy_rowid not in {source_physical_id, canonical_id}
        assert db.execute(
            "select legacy_key from tasks where record_type='workbench_artifacts' "
            "and id!=?",
            (source_physical_id,),
        ).fetchone() == ("900000",)
        projection = task20_definitions.task20_projection("workbench_artifacts")
        public_artifact = db.execute(
            f"select id,turn_id from ({projection}) where id=?",
            (artifact_identity["source_public_id"],),
        ).fetchone()
        assert public_artifact == (artifact_identity["source_public_id"], 203)

        handoff = json.loads(json.dumps(inverse["inverse_identity"]))
        migrate_task_entities(
            db,
            capture_task_entities(db),
            source_context=inverse["source_context"],
            inverse_identity=handoff,
        )
        mapped = db.execute(
            "select public_key,typeof(public_key),target_id from task_run_identity_map "
            "where source_kind='workbench_artifacts' and physical_id=?",
            (source_physical_id,),
        ).fetchone()
        assert mapped == (artifact_identity["source_public_id"], "text", canonical_id)
        assert artifact_identity["source_public_id"] != str(source_legacy_rowid)


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
            "meeting_business_keys": [{"source_meeting_physical_id": 221,
                                       "task_business_key_row_id": 225}],
                "workbench_attachment_highwater": 234,
            "workbench_artifact_identities": [{
                "source_physical_id": 233, "canonical_id": 233,
                "source_public_id": "artifact", "is_new_native": False,
            }],
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


def test_native_input_below_kind_highwater_but_in_source_gap_gets_new_source_identity():
    with closing(_source()) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        db.execute(
            "insert into task_inputs(id,task_id,channel,conversation_id,conversation_title,single_chat,"
            "trigger_message_id,trigger_create_time,trigger_sender,trigger_text,oa_url,business_object_key,"
            "input_revision_key) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (204, 201, "dingtalk", "gap-chat", "Gap", 1, "gap-trigger", "time", "Sender",
             "gap input", "", "object", "gap-revision"),
        )
        inverse = restore_task_entities(db, source_metadata, forward)
        assigned = db.execute(
            "select id from tasks where record_type='reply_task_inputs' and "
            "json_extract(payload_json,'$[4]')='gap-trigger'"
        ).fetchone()[0]
        assert assigned > source_metadata["task_sequence"][1]
        assert assigned != 204
        handoff = {(row["source_kind"], row["canonical_id"]): row["source_physical_id"]
                   for row in inverse["inverse_identity"]["owners"]}
        assert handoff[("reply_task_inputs", 204)] == assigned


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
        auxiliary_input_physical_id = db.execute(
            "select id from tasks where record_type='reply_task_inputs' order by id limit 1"
        ).fetchone()[0]
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
        # The canonical native Task ID can reuse an old auxiliary source ID.
        # The inverse allocator must assign a globally safe source Task ID and
        # use that assigned value for Reply's source public identity.
        db.execute("update tasks set id=? where id=?",
                   (auxiliary_input_physical_id, new_task["id"]))
        new_task["id"] = auxiliary_input_physical_id
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
        assert created["id"] == 227  # Meeting identity and source Reply keys occupy 225-226
        second_key = associate_business_key(db, task_id=new_task["id"], business_key="second-business-object",
                                            created_at=now, updated_at=now)
        assert second_key["id"] == 228

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
        assert handoff_by_owner[("business_object_tasks", created["id"])] == row[0]
        assigned_reply = handoff_by_owner[("reply_tasks", new_task["id"])]
        reply_row = db.execute(
            "select id,legacy_key from tasks where record_type='reply_tasks' and id=?",
            (assigned_reply,),
        ).fetchone()
        assert tuple(reply_row) == (assigned_reply, str(assigned_reply))
        assert not tuple(db.execute("pragma foreign_key_check"))
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
        assert db.execute("select id from tasks where kind='reply' and id=?",
                          (new_task["id"],)).fetchone()[0] == new_task["id"]
        assert refoward["counts"]["reply_task_inputs"] >= 2
        assert tuple(db.execute("select current_input_id,claimed_input_id,input_version,claimed_input_version "
                                "from tasks where id=?", (new_task["id"],)).fetchone()) == (
            second["id"], first["id"], 2, 1,
        )
        assert db.execute("select 1 from task_business_keys where business_key=?",
                          ("updated-native-business-object",)).fetchone() is not None
        assert db.execute("select 1 from task_business_keys where business_key=?",
                          ("second-business-object",)).fetchone() is not None
        assert tuple(db.execute(
            "select physical_id,target_id,public_key from task_run_identity_map "
            "where source_kind='business_object_tasks'"
        ).fetchone()) == (first_identity[0], created["id"], None)
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
            "select 1 from task_business_keys where business_key='second-cycle-old-writer'"
        ).fetchone()) is not None
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
            db.execute("update tasks set parameters_json=jsonb_set(parameters_json,'$.title',jsonb(?)) "
                       "where id=221", (json.dumps(native_codec_value),))
            restore_task_entities(db, source_metadata, forward)
            assert db.execute("select title,typeof(title) from ("
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


def test_composite_task_identity_indexes_preserve_every_source_component_through_migration():
    from app.action_storage import _encoded_native_sqlite_value

    identities = (
        ("okr_review_requests", "okr_review", "conversation_id", "trigger_message_id"),
        ("weekly_okr_analysis_jobs", "okr_analysis", "week_end", "manager_user_id", "source_digest"),
    )
    with closing(_source()) as db:
        next_id = 417000
        source_blob_rows = []
        source_inserted = []
        for kind, target_kind, *identity_fields in identities:
            for selected_field in identity_fields:
                blob_value = f"{kind}-{selected_field}".encode()
                marker_text = json.dumps({"t": "blob", "v": blob_value.hex()}, separators=(",", ":"))
                shared = {field: f"shared-{kind}-{selected_field}-{field}"
                          for field in identity_fields}
                common = payload(db, kind)
                common.update(shared)
                pair_ids = (next_id, next_id + 1)
                next_id += 2
                for physical_id, value, storage_type in (
                    (pair_ids[0], sqlite3.Binary(blob_value), "blob"),
                    (pair_ids[1], marker_text, "text"),
                ):
                    values = dict(common)
                    values[selected_field] = value
                    columns, expressions, parameters = source_insert(kind, values, {})
                    db.execute(
                        'insert into tasks("_kind","id","legacy_key","parent_id",'
                        + ",".join('"' + column + '"' for column in columns) + ") values(?,?,?,? ,"
                        + ",".join(expressions) + ")",
                        (TASK_RECORD_TYPES.code(kind), physical_id, str(physical_id), None, *parameters),
                    )
                    source_inserted.append((kind, target_kind, physical_id, selected_field, storage_type))
                    if storage_type == "blob":
                        source_blob_rows.append((kind, target_kind, physical_id, values, selected_field))
                assert db.execute(
                    "select typeof(" + selected_field + ")," + selected_field + " from ("
                    + task20_definitions.task20_projection(kind) + ") where id in (?,?) order by id",
                    (str(pair_ids[0]), str(pair_ids[1])),
                ).fetchall() == [("blob", blob_value), ("text", marker_text)]

                duplicate_values = dict(common)
                duplicate_values[selected_field] = sqlite3.Binary(blob_value)
                columns, expressions, parameters = source_insert(kind, duplicate_values, {})
                with pytest.raises(sqlite3.IntegrityError):
                    db.execute(
                        'insert into tasks("_kind","id","legacy_key","parent_id",'
                        + ",".join('"' + column + '"' for column in columns) + ") values(?,?,?,? ,"
                        + ",".join(expressions) + ")",
                        (TASK_RECORD_TYPES.code(kind), next_id, str(next_id), None, *parameters),
                    )
                next_id += 1

        db.commit()
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        migrate_task_entities(db, source_metadata)

        for kind, target_kind, physical_id, selected_field, source_type in source_inserted:
            row = db.execute(
                "select json_type(parameters_json,'$." + selected_field + "'),"
                "typeof(jsonb_extract(parameters_json,'$." + selected_field + "')) "
                "from tasks where id=?", (physical_id,),
            ).fetchone()
            assert row == (("object", "blob") if source_type == "blob" else ("text", "text"))

        for kind, target_kind, physical_id, values, _selected_field in source_blob_rows:
            params = {field: _encoded_native_sqlite_value(value) for field, value in values.items()}
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "insert into tasks(kind,status,parameters_json,created_at,updated_at) "
                    "values(?, 'pending', jsonb(?), 'now', 'now')",
                    (target_kind, json.dumps(params, ensure_ascii=False, allow_nan=False, separators=(",", ":"))),
                )
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


def test_inverse_translates_raw_source_physical_ids_through_native_owner_edges():
    from app.task_entity_schema import _restore_raw_physical_edges

    kind = "workbench_attachments"
    # Source 5 belongs to native 4; source 6 belongs to native 5. A newly
    # created native row can also occupy source physical ID 5, so raw IDs
    # cannot be copied into the native-target keyed identity map.
    actual = _restore_raw_physical_edges(
        {(kind, 4): 5, (kind, 5): 6, (kind, 8): 8},
        [
            {"source_kind": kind, "physical_id": 5},
            {"source_kind": kind, "physical_id": 6},
            {"source_kind": kind, "physical_id": 8},
        ],
        {(kind, 4), (kind, 5), (kind, 8)},
    )

    assert actual == {(kind, 4): 5, (kind, 5): 6, (kind, 8): 8}


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



@pytest.mark.parametrize("batch_id", ["", "batch-a", "historical-missing"])
def test_feedback_item_batch_id_matches_source_through_forward_inverse_reforward(batch_id):
    with closing(_source()) as db:
        db.execute("pragma foreign_keys=on")
        assert db.execute("pragma foreign_keys").fetchone() == (1,)
        source_item_batch_column = task20_definitions.task20_column(
            "feedback_processing_items", "batch_id"
        ).strip('"')
        source_decision_batch_column = task20_definitions.task20_column(
            "feedback_iteration_decisions", "batch_id"
        ).strip('"')
        source_batch_column = task20_definitions.task20_column(
            "feedback_processing_batches", "batch_id"
        ).strip('"')
        source_foreign_keys = db.execute("pragma foreign_key_list(tasks)").fetchall()
        assert all(row[3] != source_item_batch_column for row in source_foreign_keys)
        assert any(
            row[3] == source_decision_batch_column
            and row[2] == "tasks"
            and row[4] == source_batch_column
            for row in source_foreign_keys
        )
        item_rowid = db.execute(
            "select rowid from (" + task12_projection("feedback_processing_items", include_rowid=True) + ")"
        ).fetchone()[0]
        assert update_task12(db, "feedback_processing_items", item_rowid, {"batch_id": batch_id}) == 1
        source_batch_id = db.execute(
            "select batch_id from (" + task20_definitions.task20_projection("feedback_processing_items") + ")"
        ).fetchone()[0]
        assert source_batch_id == batch_id
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.commit()

        db.execute("pragma foreign_keys=off")
        assert db.execute("pragma foreign_keys").fetchone() == (0,)
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        assert db.execute(
            "select batch_id from feedback_processing_items where feedback_key='feedback-a'"
        ).fetchone() == (batch_id,)
        target_batch_fks = [
            row for row in db.execute("pragma foreign_key_list(feedback_processing_items)")
            if row[2] == "feedback_processing_batches" and row[3] == "batch_id"
        ]
        assert target_batch_fks == []

        restore_task_entities(db, source_metadata, forward)
        assert db.execute(
            "select batch_id from (" + task20_definitions.task20_projection("feedback_processing_items") + ")"
        ).fetchone() == (batch_id,)
        source_metadata = capture_task_entities(db)
        migrate_task_entities(db, source_metadata)
        assert db.execute(
            "select batch_id from feedback_processing_items where feedback_key='feedback-a'"
        ).fetchone() == (batch_id,)


def test_native_feedback_claim_after_converted_reopen_clears_captured_unresolved_refs(tmp_path, monkeypatch):
    from app.store import AutoReplyStore

    source = _source()
    source.row_factory = sqlite3.Row
    update_task12(source, "feedback_processing_items", 228, {
        "status": "pending", "batch_id": "", "current_round_id": 0,
        "workbench_task_id": "202", "workbench_turn_id": "203",
    })
    update_task12(source, "feedback_processing_rounds", 229, {
        "status": "resolved", "batch_id": "old-batch",
        "resolved_at": "resolve-clock", "reopened_at": "reopen-clock",
        "reopen_reason": "confirmed reopen",
    })
    metadata = capture_task_entities(source)
    handoff = {"version": 1, "owners": [
        {"source_kind": "workbench_tasks", "source_physical_id": 202, "canonical_id": 91202},
        {"source_kind": "workbench_turns", "source_physical_id": 203, "canonical_id": 91203},
        {"source_kind": "feedback_processing_rounds", "source_physical_id": 229, "canonical_id": 91229},
    ]}
    forward = migrate_task_entities(source, metadata, inverse_identity=handoff)
    from app.task_run_entity_assembly import task_run_entity_assembly_objects
    assembled_objects = task_run_entity_assembly_objects()
    present = {row[0] for row in source.execute(
        "select name from sqlite_schema"
    )}
    for name, ddl in assembled_objects.items():
        if ddl.lstrip().lower().startswith("create table") and name not in present:
            source.execute(ddl)
            present.add(name)
    for name, ddl in assembled_objects.items():
        if not ddl.lstrip().lower().startswith("create table") and name not in present:
            source.execute(ddl)
            present.add(name)
    source.commit()
    target_path = tmp_path / "converted-feedback.sqlite3"
    target = sqlite3.connect(target_path)
    source.backup(target)
    target.close()
    source.close()

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = target_path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar("converted_feedback_snapshot", default=None)
    store._reused_connection = ContextVar("converted_feedback_reused", default=None)
    monkeypatch.setattr(store, "feedback_iteration_enabled", lambda: True)
    store.upsert_feedback_event(
        key="feedback-a", feedback_token="token-a",
    )
    assert store.list_user_feedback_items_by_keys(["feedback-a"])[0].processing_status == "pending"

    claimed = store.claim_feedback_processing_items("new-batch", ["feedback-a"])
    assert claimed[0].status == "processing"
    assert claimed[0].workbench_task_id == ""
    assert claimed[0].workbench_turn_id == ""
    with store._connect() as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        inverse = restore_task_entities(db, metadata, forward)
        restored = db.execute(
            "select workbench_task_id,workbench_turn_id,current_round_id,status "
            "from (" + task12_projection("feedback_processing_items") + ") "
            "where feedback_key='feedback-a'"
        ).fetchone()
        assert tuple(restored) == ("", "", claimed[0].current_round_id, "processing")
        rounds = db.execute(
            "select round_number,status,reopened_at,reopen_reason from ("
            + task12_projection("feedback_processing_rounds")
            + ") where feedback_key='feedback-a' order by round_number"
        ).fetchall()
        assert [tuple(row) for row in rounds] == [
            (1, "resolved", "reopen-clock", "confirmed reopen"),
            (2, "processing", "", ""),
        ]
        portable_handoff = json.loads(json.dumps(inverse["inverse_identity"]))
        source_metadata = capture_task_entities(db)
        reforward = migrate_task_entities(
            db, source_metadata, source_context=inverse["source_context"],
            inverse_identity=portable_handoff,
        )
        native_item = db.execute(
            "select workbench_task_id,workbench_turn_id,current_round_id,"
            "(select status from tasks where id=task_id) "
            "from feedback_processing_items where feedback_key='feedback-a'"
        ).fetchone()
        assert native_item[0:2] == ("", "")
        assert native_item[2] != 0
        assert native_item[3] == "processing"
        assert [tuple(row) for row in db.execute(
            "select round_number,status from feedback_processing_rounds "
            "where feedback_key='feedback-a' order by round_number"
        ).fetchall()] == [(1, "resolved"), (2, "processing")]
        restore_task_entities(db, metadata, reforward)
        source_item = db.execute(
            "select workbench_task_id,workbench_turn_id,current_round_id,status "
            "from (" + task12_projection("feedback_processing_items") + ") "
            "where feedback_key='feedback-a'"
        ).fetchone()
        assert tuple(source_item) == ("", "", native_item[2], "processing")


def test_feedback_intake_task_survives_source_inverse_and_reforward(tmp_path):
    from app.action_feedback_storage import set_feedback_action
    from app.store import AutoReplyStore, FEEDBACK_EVENT_SQL

    source = _source()
    source.row_factory = sqlite3.Row
    source.execute("pragma foreign_keys=off")
    source.execute("delete from tasks where id in (228,229)")
    source.commit()
    source.execute("begin immediate")
    metadata = capture_task_entities(source)
    forward = migrate_task_entities(source, metadata)

    from app.task_run_entity_assembly import task_run_entity_assembly_objects

    assembled_objects = task_run_entity_assembly_objects()
    present = {row[0] for row in source.execute("select name from sqlite_schema")}
    for name, ddl in assembled_objects.items():
        if ddl.lstrip().lower().startswith("create table") and name not in present:
            source.execute(ddl)
            present.add(name)
    for name, ddl in assembled_objects.items():
        if not ddl.lstrip().lower().startswith("create table") and name not in present:
            source.execute(ddl)
            present.add(name)
    source.commit()

    target_path = tmp_path / "feedback-intake-reforward.sqlite3"
    target = sqlite3.connect(target_path)
    source.backup(target)
    target.close()
    source.close()

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = target_path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar("feedback_intake_snapshot", default=None)
    store._reused_connection = ContextVar("feedback_intake_reused", default=None)
    store.upsert_feedback_event(
        key="feedback:new-native", feedback_token="token-new-native", comment="first",
    )
    with store._connect() as db:
        item = db.execute(
            "select task.id,task.status,item.feedback_key,item.attempt_id,item.agent_run_id "
            "from tasks task join feedback_processing_items item on item.task_id=task.id "
            "where item.feedback_key=?",
            ("feedback:new-native",),
        ).fetchone()
        task_id = int(item[0])
        assert tuple(item)[1:] == ("pending", "feedback:new-native", 0, 0)
        db.execute(
            "update tasks set status='processing',updated_at='current-native-time' where id=?",
            (task_id,),
        )
        db.execute(
            "update feedback_processing_items set attempt_id=41,agent_run_id=42,note='native edit',"
            "test_evidence_json=? where task_id=?",
            ('{"native":true}', task_id),
        )
        source_rowid = db.execute(
            "select cast(legacy_key as integer) from actions "
            "where record_type='feedback_events' and fb_event_key=?",
            ("feedback:new-native",),
        ).fetchone()[0]
        assert set_feedback_action(
            db, "feedback_events", source_rowid,
            {"resolved_at": sqlite3.Binary(b"2026-10-23 10:00:00")},
        ) == 1
    store.upsert_feedback_event(
        key="feedback:new-native", feedback_token="token-new-native", comment="edited",
    )
    public_item = store.list_user_feedback_items_by_keys(["feedback:new-native"])[0]
    assert public_item.processing_status == "resolved"
    with store._connect() as db:
        event = db.execute(
            f"select key,comment,typeof(resolved_at),resolved_at "
            f"from ({FEEDBACK_EVENT_SQL}) where key=?",
            ("feedback:new-native",),
        ).fetchone()
        assert tuple(event) == (
            "feedback:new-native", "edited", "blob", b"2026-10-23 10:00:00",
        )

        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        inverse = restore_task_entities(db, metadata, forward)
        restored = db.execute(
            "select feedback_key,attempt_id,agent_run_id,test_evidence_json,note,status,"
            "typeof(resolved_at),resolved_at "
            "from (" + task12_projection("feedback_processing_items") + ") "
            "where feedback_key='feedback:new-native'"
        ).fetchone()
        assert tuple(restored) == (
            "feedback:new-native", 41, 42, '{"native":true}', "native edit", "resolved",
            "blob", b"2026-10-23 10:00:00",
        )
        source_metadata = capture_task_entities(db)
        reforward = migrate_task_entities(
            db, source_metadata, source_context=inverse["source_context"],
            inverse_identity=inverse["inverse_identity"],
        )
        native = db.execute(
            "select item.feedback_key,item.attempt_id,item.agent_run_id,item.test_evidence_json,"
            "item.note,task.status,typeof(item.resolved_at),item.resolved_at "
            "from feedback_processing_items item "
            "join tasks task on task.id=item.task_id where item.feedback_key='feedback:new-native'"
        ).fetchone()
        assert tuple(native) == (
            "feedback:new-native", 41, 42, '{"native":true}', "native edit", "resolved",
            "blob", b"2026-10-23 10:00:00",
        )
        event = db.execute(
            f"select key,comment,typeof(resolved_at),resolved_at "
            f"from ({FEEDBACK_EVENT_SQL}) where key=?",
            ("feedback:new-native",),
        ).fetchone()
        assert tuple(event) == (
            "feedback:new-native", "edited", "blob", b"2026-10-23 10:00:00",
        )
        restore_task_entities(db, source_metadata, reforward)


def test_reply_retry_auth_and_defer_facts_survive_native_inverse_reforward(tmp_path):
    from app import task_entity_storage
    from app.store import AutoReplyStore
    from app.task_run_entity_assembly import task_run_entity_assembly_objects

    source = _source()
    source.row_factory = sqlite3.Row
    source.execute("pragma foreign_keys=off")
    source.execute("delete from tasks where id in (228,229)")
    source.commit()
    source.execute("begin immediate")
    second_key = payload(source, "business_object_tasks")
    second_key["business_object_key"] = "second-source-business-key"
    columns, expressions, parameters = source_insert("business_object_tasks", second_key, {})
    source.execute(
        'insert into tasks("_kind","id","legacy_key","parent_id",'
        + ",".join('"' + column + '"' for column in columns) + ") values(?,?,?, ?,"
        + ",".join(expressions) + ")",
        (TASK_RECORD_TYPES.code("business_object_tasks"), 300, "300", 201, *parameters),
    )
    metadata = capture_task_entities(source)
    forward = migrate_task_entities(source, metadata)
    present = {row[0] for row in source.execute("select name from sqlite_schema")}
    for name, ddl in task_run_entity_assembly_objects().items():
        if ddl.lstrip().lower().startswith("create table") and name not in present:
            source.execute(ddl)
            present.add(name)
    for name, ddl in task_run_entity_assembly_objects().items():
        if not ddl.lstrip().lower().startswith("create table") and name not in present:
            source.execute(ddl)
            present.add(name)
    source.commit()

    path = tmp_path / "reply-recovery-reforward.sqlite3"
    target = sqlite3.connect(path)
    source.backup(target)
    target.close()
    source.close()

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar("reply_recovery_snapshot", default=None)
    store._reused_connection = ContextVar("reply_recovery_reused", default=None)

    with store._connect() as db:
        task = task_entity_storage.get_task_with_inputs(db, 201)
        db.execute("update tasks set status='failed',attempts=2,error='turn failed' where id=201")
        run_id = db.execute(
            "insert into runs(task_id,execution_generation,role,proposal_revision,turn_attempt,"
            "operation_id,status,session_id,transcript_start,transcript_end,structured_error_json,"
            "lease_owner,lease_expires_at,started_at,completed_at,updated_at) "
            "values(?,?,'consumer',0,0,'','failed','session',0,0,'{}','','','','','') returning id",
            (201, task["execution_generation"]),
        ).fetchone()[0]

    retried = store.retry_failed_reply_task(201, int(run_id), reason="operator retry")
    assert retried.execution_generation != task["execution_generation"]
    assert retried.current_input_id == 205 and retried.claimed_input_id == 206
    with store._connect() as db:
        db.execute("update tasks set status='failed' where id=201")
    preagent = store.retry_failed_pre_agent_reply_task(201, reason="pre-agent retry")
    assert preagent.execution_generation == retried.execution_generation
    with store._connect() as db:
        db.execute("update tasks set status='processing',attempts=1,locked_at='lease' where id=201")
    store.defer_reply_task(
        201, "capacity wait", expected_execution_generation=preagent.execution_generation,
        available_at="2030-01-01 00:00:00",
    )
    with store._connect() as db:
        current = task_entity_storage.get_task(db, 201)
        parameters = dict(current["parameters"])
        parameters["recovery_code"] = "codex_provider_auth_failed"
        task_entity_storage.update_task_cas(
            db, task_id=201, expected={"kind": "reply", "status": "pending"},
            changes={"status": "failed", "error": "codex_provider_auth_failed"},
            parameters=parameters,
        )
    assert store.recover_failed_native_codex_auth_tasks(
        channel="dingtalk", reason="verified native login"
    ) == [201]

    with store._connect() as db:
        owner = task_entity_storage.get_task_with_inputs(db, 201)
        run_history = db.execute(
            "select task_id,execution_generation,role,status from runs order by id"
        ).fetchall()
        input_facts = db.execute(
            "select channel,conversation_id,conversation_title,single_chat,trigger_message_id,"
            "trigger_create_time,trigger_sender,trigger_text,trigger_message_json,oa_url,"
            "business_object_key,input_revision_key,created_at,input_provenance_json,input_compacted "
            "from task_inputs where task_id=201 order by id"
        ).fetchall()
        business_keys = [row[0] for row in db.execute(
            "select business_key from task_business_keys where task_id=201 order by id"
        ).fetchall()]
        assert "second-source-business-key" in business_keys and len(business_keys) == 2
        current_id, claimed_id = owner["current_input_id"], owner["claimed_input_id"]
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    inverse = restore_task_entities(db, metadata, forward)
    source_reply = db.execute(
        "select status,attempts,error,execution_generation,current_input_id,claimed_input_id,"
        "recovery_code from (" + task20_projection("reply_tasks") + ") where id=201"
    ).fetchone()
    assert tuple(source_reply) == (
        "pending", 0, "verified native login", owner["execution_generation"],
        5, 6, "",
    )
    source_inputs = db.execute(
        "select channel,conversation_id,conversation_title,single_chat,trigger_message_id,"
        "trigger_create_time,trigger_sender,trigger_text,trigger_message_json,oa_url,"
        "business_object_key,input_revision_key,created_at,input_provenance_json,input_compacted "
        "from (" + task20_projection("reply_task_inputs") + ") where reply_task_id=201 order by id"
    ).fetchall()
    assert [tuple(row) for row in source_inputs] == [tuple(row) for row in input_facts]
    source_keys = [row[0] for row in db.execute(
        "select business_object_key from (" + task20_projection("business_object_tasks")
        + ") where reply_task_id=201 order by business_object_key"
    ).fetchall()]
    assert source_keys == sorted(business_keys)
    source_context = json.loads(json.dumps(inverse["source_context"]))
    identity = json.loads(json.dumps(inverse["inverse_identity"]))
    source_metadata = capture_task_entities(db)
    reforward = migrate_task_entities(
        db, source_metadata, source_context=source_context, inverse_identity=identity,
    )
    native_reply = db.execute(
        "select status,attempts,error,execution_generation,current_input_id,claimed_input_id,"
        "json_extract(json(parameters_json),'$.recovery_code') from tasks where kind='reply' and id=201"
    ).fetchone()
    assert tuple(native_reply) == (
        "pending", 0, "verified native login", owner["execution_generation"],
        current_id, claimed_id, "",
    )
    assert [tuple(row) for row in db.execute(
        "select channel,conversation_id,conversation_title,single_chat,trigger_message_id,"
        "trigger_create_time,trigger_sender,trigger_text,trigger_message_json,oa_url,"
        "business_object_key,input_revision_key,created_at,input_provenance_json,input_compacted "
        "from task_inputs where task_id=201 order by id"
    ).fetchall()] == [tuple(row) for row in input_facts]
    assert [row[0] for row in db.execute(
        "select business_key from task_business_keys where task_id=201 order by id"
    ).fetchall()] == business_keys
    assert [tuple(row) for row in db.execute(
        "select task_id,execution_generation,role,status from runs order by id"
    ).fetchall()] == [tuple(row) for row in run_history]
    restore_task_entities(db, source_metadata, reforward)
    db.close()


def test_new_native_feedback_resolution_receipt_survives_inverse_reforward_cycles(
    tmp_path, monkeypatch,
):
    from app.action_feedback_storage import insert_feedback_action
    from app.feedback_processing import (
        FeedbackIterationAcceptance, FeedbackIterationDecision, ResolutionEvidence,
    )
    from app.store import AutoReplyStore
    from app.task_run_entity_assembly import task_run_entity_assembly_objects

    source = _source()
    source.row_factory = sqlite3.Row
    update_task12(source, "feedback_processing_items", 228, {
        "status": "pending", "batch_id": "", "current_round_id": 0,
        "workbench_task_id": "", "workbench_turn_id": "", "attempt_id": 0,
        "agent_run_id": 0, "commit_sha": "", "test_evidence_json": "{}",
        "restart_evidence_json": "{}", "health_evidence_json": "{}",
        "note": "", "resolved_at": "",
    })
    update_task12(source, "feedback_processing_rounds", 229, {
        "status": "resolved", "batch_id": "old-batch",
        "resolved_at": "resolve-clock", "reopened_at": "reopen-clock",
        "reopen_reason": "confirmed reopen",
    })
    metadata = capture_task_entities(source)
    handoff = {"version": 1, "owners": [
        {"source_kind": "workbench_tasks", "source_physical_id": 202, "canonical_id": 1202},
        {"source_kind": "workbench_turns", "source_physical_id": 203, "canonical_id": 1203},
        {"source_kind": "feedback_iteration_decisions", "source_physical_id": 227, "canonical_id": 1227},
        {"source_kind": "feedback_processing_rounds", "source_physical_id": 229, "canonical_id": 1229},
    ]}
    forward = migrate_task_entities(source, metadata, inverse_identity=handoff)
    objects = task_run_entity_assembly_objects()
    for name, ddl in objects.items():
        present = source.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone()
        if present is None and ddl.lstrip().lower().startswith("create table"):
            source.execute(ddl)
    for name, ddl in objects.items():
        present = source.execute("select 1 from sqlite_schema where name=?", (name,)).fetchone()
        if present is None and not ddl.lstrip().lower().startswith("create table"):
            source.execute(ddl)
    # Finish migration/assembly writes before SQLite's online backup. A backup
    # from this connection cannot progress while it owns an open write txn.
    source.commit()
    db_path = tmp_path / "native-feedback-resolution.sqlite3"
    with sqlite3.connect(db_path) as target:
        source.backup(target)
    source.close()

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = db_path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar("native_feedback_receipt_snapshot", default=None)
    store._reused_connection = ContextVar("native_feedback_receipt_reused", default=None)
    monkeypatch.setattr(store, "feedback_iteration_enabled", lambda: True)
    with store._immediate_write_transaction() as db:
        insert_feedback_action(db, "feedback_events", {
            "key": "feedback-a", "feedback_token": "token-a",
        })
    item = store.claim_feedback_processing_items("new-batch", ["feedback-a"])[0]
    store.associate_feedback_processing_turn(
        "feedback-a", expected_batch_id="new-batch",
        workbench_task_id="1202", workbench_turn_id="1203",
        attempt_id=13, agent_run_id=27,
    )
    commit_sha = "a" * 40
    test = {"pytest": {"exit_code": 0}}
    restart = {"launchd_label": "com.ceo-agent-service.main", "before_pid": 10, "after_pid": 11}
    health = {"url": "http://127.0.0.1:8765/healthz", "status_code": 200, "ok": True}
    store.patch_feedback_processing_item_evidence(
        "feedback-a", commit_sha=commit_sha, test_evidence=test,
        restart_evidence=restart, health_evidence=health,
    )
    decision = FeedbackIterationDecision(
        scope="code", root_cause="verified source defect",
        feedback_keys=["feedback-a"], source_references=["source record"],
        why_not_code="not applicable",
        acceptance=FeedbackIterationAcceptance(
            scenario="current feedback", expected_behavior="resolved with receipt",
            verification=["focused test"],
        ),
    )
    decision_record = store.record_feedback_iteration_decision(
        "new-batch", decision, workbench_task_id="1202", workbench_turn_id="1203",
    )
    receipt = ResolutionEvidence(
        commit_sha=commit_sha, test_evidence=test, restart_evidence=restart,
        health_evidence=health,
        backlog_evidence={"processing": 0, "failed": 0, "retryable": 0},
        associations={"feedback-a": {
            "workbench_task_id": "1202", "workbench_turn_id": "1203",
            "attempt_id": 13, "agent_run_id": 27,
        }},
    )
    assert store.resolve_feedback_processing_batch(
        "new-batch", receipt, commit_is_ancestor=True,
    ) is True

    with store._connect() as db:
        db.row_factory = sqlite3.Row
        native_round = db.execute(
            "select * from feedback_processing_rounds where id=?", (item.current_round_id,),
        ).fetchone()
        current = json.loads(native_round["scope_receipt_json"])
        assert current["decision_id"] == decision_record.id
        assert current["evidence"]["associations"]["feedback-a"]["workbench_task_id"] == "1202"
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        inverse = restore_task_entities(db, metadata, forward)
        source_round = db.execute(
            "select * from (" + task12_projection("feedback_processing_rounds")
            + ") where feedback_key='feedback-a' and round_number=2"
        ).fetchone()
        restored = AutoReplyStore._scope_resolution_receipt(source_round)
        assert restored is not None
        source_decision_id, scope, source_evidence = restored
        assert scope == "code"
        source_decision = db.execute(
            "select id,decision_json from (" + task12_projection(
                "feedback_iteration_decisions"
            ) + ") where id=?", (source_decision_id,),
        ).fetchone()
        assert source_decision is not None
        assert source_decision_id == source_decision["id"]
        assert source_decision_id == decision_record.id
        assert json.loads(source_decision["decision_json"])["scope"] == scope
        assert source_round["status"] == "resolved"
        assert source_evidence.associations["feedback-a"].workbench_task_id == "wb-task-a"
        assert source_evidence.associations["feedback-a"].workbench_turn_id == "wb-turn-a"
        assert source_evidence.associations["feedback-a"].attempt_id == 13
        assert source_evidence.associations["feedback-a"].agent_run_id == 27
        portable_identity = json.loads(json.dumps(inverse["inverse_identity"]))
        restored_metadata = capture_task_entities(db)
        reforward = migrate_task_entities(
            db, restored_metadata, source_context=inverse["source_context"],
            inverse_identity=portable_identity,
        )
        migrated_round = db.execute(
            "select * from feedback_processing_rounds where feedback_key='feedback-a' "
            "and round_number=2"
        ).fetchone()
        migrated_receipt = json.loads(migrated_round["scope_receipt_json"])
        assert migrated_receipt["decision_id"] == decision_record.id
        assert migrated_receipt["evidence"]["associations"]["feedback-a"]["workbench_task_id"] == "1202"
        restore_task_entities(db, restored_metadata, reforward)
        second_source_round = db.execute(
            "select * from (" + task12_projection("feedback_processing_rounds")
            + ") where feedback_key='feedback-a' and round_number=2"
        ).fetchone()
        final_receipt = AutoReplyStore._scope_resolution_receipt(second_source_round)
        assert final_receipt is not None
        assert final_receipt[0] == source_decision_id
        assert final_receipt[2] == source_evidence


def test_source_resolved_feedback_receipt_validates_then_native_reopens_and_claims(
    tmp_path, monkeypatch,
):
    """Carry a genuine source-owned resolution through conversion and current APIs."""
    from app.action_feedback_storage import insert_feedback_action, insert_feedback_decision_items
    from app.feedback_processing import (
        FeedbackIterationAcceptance, FeedbackIterationDecision, ResolutionEvidence,
    )
    from app.store import AutoReplyStore
    from app.predecessor_database_schema import create_store_2026_10_21_1_schema as create_current_database_schema
    from app.run_adoption_schema import capture_run_adoptions, migrate_run_adoptions
    from app.run_entity_schema import capture_run_entities, migrate_run_entities
    from app.task_run_entity_assembly import task_run_entity_assembly_objects

    source = _source()
    source.row_factory = sqlite3.Row
    create_current_database_schema(source)
    source.commit()
    source.execute("pragma foreign_keys=off")
    source.execute("begin immediate")
    timestamp = "2026-10-22T12:00:00+00:00"
    commit_sha = "a" * 40
    test_evidence = {"pytest": {"exit_code": 0}}
    restart_evidence = {
        "launchd_label": "com.ceo-agent-service.main", "before_pid": 10, "after_pid": 11,
    }
    health_evidence = {
        "url": "http://127.0.0.1:8765/healthz", "status_code": 200, "ok": True,
    }
    backlog_evidence = {"processing": 0, "failed": 0, "retryable": 0}
    association = {
        "workbench_task_id": "wb-task-a", "workbench_turn_id": "wb-turn-a",
        "attempt_id": 44, "agent_run_id": 13003,
    }
    source_evidence = ResolutionEvidence(
        commit_sha=commit_sha, test_evidence=test_evidence,
        restart_evidence=restart_evidence, health_evidence=health_evidence,
        backlog_evidence=backlog_evidence, associations={"feedback-a": association},
    )
    decision = FeedbackIterationDecision(
        scope="code", root_cause="verified source defect", feedback_keys=["feedback-a"],
        source_references=["source record"], why_not_code="not applicable",
        acceptance=FeedbackIterationAcceptance(
            scenario="captured source receipt", expected_behavior="accepted after conversion",
            verification=["native validator", "reopen and claim"],
        ),
    )
    decision_json = decision.model_dump_json()
    # Preserve the source's append-only decision contract while seeding this
    # actual historical row through its existing task12 writer.
    trigger_sql = source.execute(
        "select sql from sqlite_schema where type='trigger' and name='trg_task20_decision_update'"
    ).fetchone()[0]
    source.execute("drop trigger trg_task20_decision_update")
    update_task12(source, "feedback_iteration_decisions", 227, {
        "batch_id": "batch-a", "workbench_task_id": "wb-task-a",
        "workbench_turn_id": "wb-turn-a", "decision_json": decision_json,
        "created_at": timestamp,
    })
    source.execute(trigger_sql)
    round_receipt = {
        "decision_id": 227, "scope": "code", "evidence": source_evidence.model_dump(mode="json"),
    }
    update_task12(source, "feedback_processing_batches", 204, {
        "batch_id": "batch-a", "status": "resolved", "requested_count": 1,
        "created_at": timestamp, "updated_at": timestamp, "resolved_at": timestamp,
    })
    update_task12(source, "feedback_processing_items", 228, {
        "status": "resolved", "batch_id": "batch-a", "current_round_id": 229,
        "workbench_task_id": "wb-task-a", "workbench_turn_id": "wb-turn-a",
        "attempt_id": 44, "agent_run_id": 13003, "commit_sha": commit_sha,
        "test_evidence_json": json.dumps(test_evidence),
        "restart_evidence_json": json.dumps(restart_evidence),
        "health_evidence_json": json.dumps(health_evidence),
        "resolved_at": timestamp, "created_at": timestamp, "updated_at": timestamp,
    })
    update_task12(source, "feedback_processing_rounds", 229, {
        "round_number": 1, "batch_id": "batch-a", "status": "resolved",
        "workbench_task_id": "wb-task-a", "workbench_turn_id": "wb-turn-a",
        "attempt_id": 44, "agent_run_id": 13003, "commit_sha": commit_sha,
        "test_evidence_json": json.dumps(test_evidence),
        "restart_evidence_json": json.dumps(restart_evidence),
        "health_evidence_json": json.dumps(health_evidence),
        "backlog_evidence_json": json.dumps(backlog_evidence), "receipt_version": 2,
        "started_at": timestamp, "resolved_at": timestamp, "created_at": timestamp,
        "updated_at": timestamp, "scope_receipt_json": json.dumps(round_receipt),
    })
    insert_feedback_action(source, "feedback_events", {
        "key": "feedback-a", "feedback_token": "token-a", "resolved_at": timestamp,
        "created_at": timestamp, "updated_at": timestamp,
    })
    insert_feedback_action(source, "feedback_processing_transitions", {
        "feedback_key": "feedback-a", "round_id": 229, "batch_id": "batch-a",
        "from_status": "pending", "to_status": "processing", "created_at": timestamp,
    })
    insert_feedback_action(source, "feedback_processing_transitions", {
        "feedback_key": "feedback-a", "round_id": 229, "batch_id": "batch-a",
        "from_status": "processing", "to_status": "resolved",
        "workbench_task_id": "wb-task-a", "workbench_turn_id": "wb-turn-a",
        "created_at": timestamp,
    })
    insert_feedback_decision_items(source, [{
        "decision_id": 227, "feedback_key": "feedback-a", "round_id": 229,
    }])
    assert source.execute(
        "select count(*) from actions where record_type='feedback_processing_transitions'"
    ).fetchone()[0] == 2
    assert tuple(source.execute(
        "select fb_decision_id,fb_feedback_key,fb_round_id from actions "
        "where record_type='feedback_iteration_decision_items'"
    ).fetchone()) == (227, "feedback-a", 229)
    adoption_source = capture_run_adoptions(source)
    metadata = capture_task_entities(source)
    run_source = capture_run_entities(source)
    handoff = {"version": 1, "owners": [
        {"source_kind": "workbench_tasks", "source_physical_id": 202, "canonical_id": 1202},
        {"source_kind": "workbench_turns", "source_physical_id": 203, "canonical_id": 1203},
        {"source_kind": "feedback_iteration_decisions", "source_physical_id": 227, "canonical_id": 1227},
        {"source_kind": "feedback_processing_rounds", "source_physical_id": 229, "canonical_id": 1229},
    ]}
    task_forward = migrate_task_entities(source, metadata, inverse_identity=handoff)
    from tests.test_run_entity_schema import _run_handoff
    run_handoff = _run_handoff(source, run_source)
    run_forward = migrate_run_entities(
        source, run_source, task_metadata=task_forward, run_handoff=run_handoff,
    )
    migrate_run_adoptions(
        source, adoption_source, task_metadata=task_forward, run_metadata=run_forward,
    )
    assert tuple(source.execute(
        "select fb_decision_id,fb_feedback_key,fb_round_id from actions "
        "where record_type='feedback_iteration_decision_items'"
    ).fetchone()) == (1227, "feedback-a", 1229)
    assert tuple(source.execute(
        "select fb_transition_round_id,fb_transition_workbench_task_id,"
        "fb_transition_workbench_turn_id from actions "
        "where record_type='feedback_processing_transitions' and fb_transition_to_status='resolved'"
    ).fetchone()) == (1229, "1202", "1203")
    assert source.execute(
        "select fb_event_resolved_at from actions where record_type='feedback_events' "
        "and fb_event_key='feedback-a'"
    ).fetchone()[0] == timestamp
    objects = task_run_entity_assembly_objects()
    present = {row[0] for row in source.execute("select name from sqlite_schema")}
    for name, ddl in objects.items():
        if name not in present and ddl.lstrip().lower().startswith("create table"):
            source.execute(ddl)
            present.add(name)
    for name, ddl in objects.items():
        if name not in present and not ddl.lstrip().lower().startswith("create table"):
            source.execute(ddl)
            present.add(name)
    # Complete legitimate source writes before online backup; otherwise SQLite
    # backup waits forever on this connection's pending write transaction.
    source.commit()
    db_path = tmp_path / "source-resolved-feedback.sqlite3"
    with sqlite3.connect(db_path) as target:
        source.backup(target)
    source.close()

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = db_path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar("source_resolved_feedback_snapshot", default=None)
    store._reused_connection = ContextVar("source_resolved_feedback_reused", default=None)
    monkeypatch.setattr(store, "feedback_iteration_enabled", lambda: True)
    assert store.resolve_feedback_processing_batch(
        "batch-a", None, commit_is_ancestor=True,
    ) is True
    with store._connect() as db:
        receipt = db.execute(
            "select scope_receipt_json from feedback_processing_rounds where id=1229"
        ).fetchone()[0]
        assert json.loads(receipt)["decision_id"] == 1227
        assert tuple(db.execute(
            "select workbench_task_id,workbench_turn_id from feedback_iteration_decisions where id=1227"
        ).fetchone()) == ("1202", "1203")

    reopened_item = store.reopen_feedback_processing_item(
        "feedback-a", reason="reopen converted source",
    )
    assert reopened_item.status == "pending"
    assert reopened_item.current_round_id == 0
    reopened = store.claim_feedback_processing_items("next-batch", ["feedback-a"])[0]
    assert reopened.status == "processing"
    assert reopened.current_round_id != 1229
    assert reopened.workbench_task_id == reopened.workbench_turn_id == ""
    with store._connect() as db:
        assert db.execute(
            "select status,reopened_at,reopen_reason from feedback_processing_rounds where id=1229"
        ).fetchone()[0] == "resolved"
        assert [tuple(row) for row in db.execute(
            "select round_number,status from feedback_processing_rounds where feedback_key='feedback-a' "
            "order by round_number"
        ).fetchall()] == [(1, "resolved"), (2, "processing")]
