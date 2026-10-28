import sqlite3

from app import action_entity_storage, action_storage, shared_action_lifecycle
from app.reply_receipt_reference import add_reference
from app.task8_storage import register_task8_functions
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


def _decoy_action(db):
    action_entity_storage.create_action(db, values={"operation": "unrelated", "status": "done"})


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


def test_current_native_receipts_preserve_fake_provider_results():
    import json
    from app.current_database_schema import create_current_database_schema

    with sqlite3.connect(":memory:") as db:
        register_task8_functions(db)
        create_current_database_schema(db)
        receipt_ids = []
        for index in range(2):
            action = action_entity_storage.create_action(
                db, values={"operation": "approve", "action_identity": f"fixture-{index}"},
            )
            receipt_ids.append(action_entity_storage.append_action_receipt(
                db, action["id"], values={
                    "operation": "approve",
                    "provider_result_json": json.dumps({"provider": "test_double", "ok": True}),
                },
            ))
        assert receipt_ids[0] != receipt_ids[1]
        for receipt_id in receipt_ids:
            body = db.execute("select provider_result_json from action_receipts where id=?", (receipt_id,)).fetchone()[0]
            assert json.loads(body) == {"provider": "test_double", "ok": True}
