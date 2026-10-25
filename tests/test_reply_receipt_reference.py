import sqlite3
import pytest

from app import action_entity_storage, action_storage, shared_action_lifecycle
from app.current_schema_manifest import (
    create_current_email_migration_ledger,
    schema_objects,
)
from app.reply_receipt_reference import add_reference
from app.reply_receipt_reference_schema_release import (
    SOURCE_STORE_VERSION,
    prepare_reply_receipt_reference_release,
    target_objects,
)
from app.store import AutoReplyStore
from app.store import AgentRole
from app.task8_storage import register_task8_functions
from app.state_storage import update_state_rows


def _set_marker(db, version):
    update_state_rows(
        db, "service_state", {"value": version, "updated_at": "source-clock"},
        where_sql="key=?", parameters=("store_schema_version",),
    )


def _attempt(db, body):
    required_text = "conversation_id conversation_title trigger_message_id trigger_sender trigger_text action sensitivity_kind codex_reason draft_reply_text final_reply_text permission_action permission_reason send_status send_error reviewer_feedback corrected_reply_text created_at updated_at audit_documents_json audit_tool_events_json audit_summary codex_session_id direct_user_id direct_open_dingtalk_id oa_process_instance_id oa_task_id oa_url oa_action oa_remark oa_action_result_json trigger_create_time calendar_event_id calendar_response_status calendar_response_result_json mail_mailbox mail_message_id mail_subject mail_reply_text mail_action_result_json channel universal_execution_id universal_execution_scope_id reaction_action_result_json document_action_result_json human_decision_options_json feedback_scope skill_update_receipts_json resolved_at resolution trigger_text_provenance_json".split()
    values = action_storage._ATTEMPT_DEFAULTS | {name: "" for name in required_text}
    values.update({name: 0 for name in (
        "retry_count", "codex_transcript_start_line", "codex_transcript_end_line",
        "skill_update_requested",
    )})
    result = action_storage.insert_action(db, "reply_attempts", values | {
        "channel": "dingtalk", "conversation_id": "chat",
        "trigger_message_id": "trigger", "trigger_text": "source",
        "action": "agent_run", "oa_action_result_json": body,
    })
    return int(db.execute(
        "select cast(legacy_key as integer) from actions where record_type='reply_attempts' and legacy_key=?",
        (str(result.lastrowid),),
    ).fetchone()[0])


def _receipt(db, body):
    action = action_entity_storage.create_action(db, values={
        "operation": "approve", "status": "done",
        "business_object_key": "oa:process:7", "action_identity": "task:9",
        "external_action_key": "oa-effect-1",
    })
    return action_entity_storage.append_action_receipt(db, action["id"], values={
        "operation": "approve", "target": '{"process_instance_id":"7","task_id":"9"}',
        "provider_result_json": body, "result_digest": "digest", "agent_run_id": 1,
        "observed_at": "receipt-clock",
    })


def _decoy_action(db):
    action_entity_storage.create_action(db, values={"operation": "unrelated", "status": "done"})


def _seed_attempt(store, *, run_id, trigger, body, operation="approve", task_id="test-task"):
    attempt_id = store.record_reply_attempt(
        conversation_id="receipt-reference-chat", conversation_title="test",
        trigger_message_id=trigger, trigger_sender="test", trigger_text="test source",
        action="agent_run", sensitivity_kind="general", send_status="completed",
        oa_process_instance_id="test-process", oa_task_id=task_id,
        oa_action=operation, oa_action_result_json=body,
    )
    with store._connect() as db:
        action_storage.update_action(db, "reply_attempts", attempt_id, {"agent_run_id": run_id})
    return attempt_id


def _seed_provider_action(db, *, external_key, operation, target, body, run_id):
    action_entity_storage.create_action(db, values={
        "operation": operation, "status": "done",
        "business_object_key": "oa:test", "action_identity": external_key,
        "external_action_key": external_key,
    })
    from app.shared_action_lifecycle import record_reviewed_provider_receipt
    return record_reviewed_provider_receipt(
        db, external_action_key=external_key, operation=operation,
        target_json=target, provider_json=body, result_digest="receipt-digest",
        agent_run_id=run_id, observed_at="2026-10-25T00:00:00+00:00",
    )


def test_reply_attempt_projection_reads_exact_receipt_and_protects_it_from_retirement():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    register_task8_functions(db)
    from app.current_database_schema import create_current_database_schema
    create_current_database_schema(db)
    _decoy_action(db)
    body = '{"errcode":0,"request_id":"r-1"}'
    attempt_id = _attempt(db, "")
    physical_id = db.execute(
        "select id from actions where record_type='reply_attempts' and cast(legacy_key as integer)=?",
        (attempt_id,),
    ).fetchone()[0]
    assert physical_id != attempt_id
    action = action_entity_storage.create_action(db, values={"operation": "approve"})
    receipt_id = action_entity_storage.append_action_receipt(
        db, action["id"], values={"provider_result_json": body, "operation": "approve"},
    )
    add_reference(db, attempt_id=attempt_id, receipt_id=receipt_id, expected_body=body)

    projected = db.execute(
        f"select oa_action_result_json from ({action_storage.REPLY_ATTEMPT_COMMON_SQL}) where id=?",
        (attempt_id,),
    ).fetchone()[0]
    assert projected == body

    assert shared_action_lifecycle.retire_unreferenced_provider_receipt(
        db, action_id=action["id"], receipt_id=receipt_id, expected_provider_json=body,
    ) is False
    db.rollback()


def test_current_store_release_backfills_reference_and_inverse_restores_body(tmp_path):
    path = tmp_path / "reply-receipt.sqlite3"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        conversation_id="receipt-reference-chat", conversation_title="test",
        single_chat=True, trigger_message_id="seed-task", trigger_create_time="2026-10-25",
        trigger_sender="test", trigger_text="test source",
    )
    [task] = store.claim_reply_tasks(limit=1)
    claim = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="copy-test",
    )
    run_id = claim.run.id
    matched_body = '{"result":"matched"}'
    unmatched_body = '{"result":"local-only"}'
    ambiguous_body = '{"result":"ambiguous"}'
    matched_id = _seed_attempt(store, run_id=run_id, trigger="matched", body=matched_body)
    unmatched_id = _seed_attempt(store, run_id=run_id, trigger="unmatched", body=unmatched_body)
    ambiguous_id = _seed_attempt(store, run_id=run_id, trigger="ambiguous", body=ambiguous_body)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        create_current_email_migration_ledger(db)
        db.execute("insert into email_schema_migrations(version,applied_at) values(54,'source-clock')")
        _set_marker(db, SOURCE_STORE_VERSION)
        db.execute("drop table reply_attempt_receipt_refs")
        from app.action_reference_trigger_schema_release import RETIRED_TRIGGER_SQL
        db.execute(RETIRED_TRIGGER_SQL)
        _decoy_action(db)
        target = '{"process_instance_id":"test-process","task_id":"test-task"}'
        matched_receipt = _seed_provider_action(
            db, external_key="matched-provider", operation="approve", target=target,
            body=matched_body, run_id=run_id,
        )
        _seed_provider_action(
            db, external_key="ambiguous-provider-1", operation="approve", target=target,
            body=ambiguous_body, run_id=run_id,
        )
        _seed_provider_action(
            db, external_key="ambiguous-provider-2", operation="approve", target=target,
            body=ambiguous_body, run_id=run_id,
        )
        db.commit()

    source_sql = action_storage.reply_action_projection("reply_attempts", "fresh_memory_fk")
    with sqlite3.connect(path) as db:
        before = db.execute(source_sql).fetchall()
        original_bodies = dict(db.execute(
            "select cast(legacy_key as integer),attempt_oa_action_result_json from actions "
            "where record_type='reply_attempts'"
        ))
    release = prepare_reply_receipt_reference_release(path)
    release.publish()
    release.verify_loaded()
    with sqlite3.connect(path) as db:
        assert schema_objects(db) == target_objects()
        from app.state_definitions import state_projection
        assert db.execute(
            f"select value from ({state_projection('service_state')}) where key='store_schema_version'"
        ).fetchone()[0] == "2026-10-25.1"
        register_task8_functions(db)
        assert db.execute("select count(*) from reply_attempt_receipt_refs").fetchone()[0] == 1
        assert db.execute(
            "select ref.receipt_id from reply_attempt_receipt_refs ref join actions a "
            "on a.id=ref.attempt_id where cast(a.legacy_key as integer)=?", (matched_id,),
        ).fetchone()[0] == matched_receipt
        assert db.execute(
            "select attempt_oa_action_result_json from actions where record_type='reply_attempts' "
            "and cast(legacy_key as integer)=?", (unmatched_id,),
        ).fetchone()[0] == unmatched_body
        assert db.execute(
            "select attempt_oa_action_result_json from actions where record_type='reply_attempts' "
            "and cast(legacy_key as integer)=?", (ambiguous_id,),
        ).fetchone()[0] == ambiguous_body
        assert db.execute(action_storage.REPLY_ATTEMPT_COMMON_SQL).fetchall() == before

    release.rollback()
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        assert db.execute("select name from sqlite_schema where name='reply_attempt_receipt_refs'").fetchone() is None
        restored_bodies = dict(db.execute(
            "select cast(legacy_key as integer),attempt_oa_action_result_json from actions "
            "where record_type='reply_attempts'"
        ))
        assert restored_bodies == original_bodies
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"
        from app.current_publication_sources import publication_task_sources
        assert db.execute("select value from state where key='store_schema_version'").fetchone()[0] == SOURCE_STORE_VERSION
        with pytest.raises(ValueError, match="unsupported current publication"):
            publication_task_sources(db)
