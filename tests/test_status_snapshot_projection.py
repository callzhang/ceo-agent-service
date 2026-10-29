import sqlite3

import pytest

from app import shared_action_lifecycle
from app.audit_web import _email_action_queue_snapshot, _reply_task_queue_snapshot
from app.current_database_schema import create_current_database_schema
from app.email_canonical_storage import insert_email_classification, insert_email_message
from app.email_store import DIRECT_ACTION_MAX_ATTEMPTS
from app import task_entity_storage


@pytest.fixture
def database():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    create_current_database_schema(db)
    try:
        yield db
    finally:
        db.close()


def _insert_task8(db, kind, values):
    if kind == "reply_tasks":
        if values.get("business_object_key", "") is None:
            raise sqlite3.IntegrityError("business_object_key may not be NULL")
        status = values.get("status", "pending")
        task = task_entity_storage.create_task(db, values={
            "kind": "reply", "status": status, "available_at": "",
            "attempts": int(values.get("attempts", 0)), "error": values.get("error", ""),
            "execution_generation": f"generation-{values.get('trigger_message_id', 'reply')}",
            "created_at": values.get("created_at", "2026-10-11"),
            "updated_at": values.get("updated_at", "2026-10-11"),
        }, parameters={"force_new_decision": 0, "manual_rerun_attempt_id": 0,
                       "manual_rerun_revision_key": "", "recovery_code": ""})
        task_input, _ = task_entity_storage.append_task_input(db, task_id=task["id"], values={
            "channel": values.get("channel", "dingtalk"),
            "conversation_id": values["conversation_id"],
            "conversation_title": values["conversation_title"],
            "single_chat": values["single_chat"],
            "trigger_message_id": values["trigger_message_id"],
            "trigger_create_time": values["trigger_create_time"],
            "trigger_sender": values["trigger_sender"], "trigger_text": "",
            "business_object_key": values.get("business_object_key") or f"reply:{task['id']}",
            "input_revision_key": f"input:{task['id']}",
        })
        assert task_entity_storage.bind_current_input(
            db, task_id=task["id"], input_id=task_input["id"],
            expected_input_version=None, input_version=1,
        )
        return int(task["id"])
    task = task_entity_storage.create_task(db, values={
        "kind": "work_summary", "status": values.get("status", "pending"),
        "available_at": "", "attempts": 0, "error": "",
        "created_at": "2026-10-11", "updated_at": "2026-10-11",
    }, parameters={})
    db.execute("insert into work_summary_inputs(task_id,source_type,source_ref,payload_json) values(?,?,?,?)",
               (task["id"], "local_file", f"summary:{task['id']}", "{}"))
    return int(task["id"])


def _insert_reply_task(db, task_id, status, updated_at, error="", business_object_key=""):
    inserted = _insert_task8(db, "reply_tasks", {
        "channel": "dingtalk",
        "conversation_id": f"conversation-{task_id}",
        "conversation_title": f"Conversation {task_id}",
        "single_chat": 0,
        "trigger_message_id": f"message-{task_id}",
        "trigger_create_time": "2026-10-11",
        "trigger_sender": "sender",
        "status": status,
        "error": error,
        "updated_at": updated_at,
        "business_object_key": business_object_key,
    })
    assert inserted == task_id
    return inserted


def _email_locator(classification_id):
    return {
        "account_id": "account",
        "stable_message_identity": f"message-{classification_id}",
        "folder": "INBOX",
        "uidvalidity": 1,
        "uid": classification_id,
    }


def _insert_email_message(db, classification_id):
    insert_email_message(db, _email_locator(classification_id) | {
        "rfc_message_id": "",
        "in_reply_to": "",
        "references_json": "[]",
        "thread_identity": "",
        "sender": "Sender",
        "recipients_json": "[]",
        "subject": "Subject",
        "normalized_text": "",
        "preview": "",
        "attachment_metadata_json": "[]",
        "received_at": "",
        "created_at": "",
        "updated_at": "",
    })


def _classification_values(classification_id, plan_id, status):
    return _email_locator(classification_id) | {
        "id": classification_id,
        "status": status,
        "classification_source": "model",
        "confidence": 0.5,
        "margin": 0.1,
        "probabilities_json": "{}",
        "model_id": "model",
        "config_version": "config",
        "current_action_plan_id": plan_id,
        "sender": "Sender",
        "subject": "Subject",
        "updated_at": "2026-10-12",
    }


def _insert_email_classification(db, classification_id, plan_id, status):
    insert_email_classification(db, _classification_values(classification_id, plan_id, status))


def _insert_email_plan(db, plan_id, classification_id, version=1):
    shared_action_lifecycle.persist_email_plan(db, {
        "action_plan_id": plan_id,
        "action_plan_version": version,
        "classification_id": classification_id,
        "account_id": "account",
        "category": "work",
        "classification_source": "model",
        "confidence": 0.5,
        "model_id": "model",
        "config_version": "config",
        "actions_json": '["archive","move","trash","label","mark_read"]',
        "action_parameters_json": "{}",
        "authorization_snapshot_json": "[]",
        "created_at": "2026-10-12",
    })


def _insert_email_action_row(db, *, plan_id, classification_id, status, attempts,
                             next_attempt_at, updated_at, error,
                             action_type="archive") -> int:
    if not db.in_transaction:
        db.execute("begin")
    action = shared_action_lifecycle.create_direct_email_action(db, {
        "action_plan_id": plan_id,
        "classification_id": classification_id,
        "account_id": "account",
        "action_type": action_type,
        "parameters_json": "{}",
        "status": "pending" if status in {"done", "failed", "skipped"} else status,
        "next_attempt_at": next_attempt_at or None,
        "created_at": updated_at,
        "updated_at": updated_at,
    })
    action_id = int(action["id"])
    terminal_attempts = max(attempts, int(status in {"done", "failed", "skipped"}))
    current_status = str(action["status"])
    for attempt_number in range(1, terminal_attempts + 1):
        claimed_at = f"{updated_at}T00:00:{attempt_number:02d}+00:00"
        claimed = shared_action_lifecycle.claim_direct_email_action(
            db,
            action_id=action_id,
            expected_status=current_status,
            expected_started_at=None,
            attempt_count=attempt_number - 1,
            claimed_at=claimed_at,
        )
        assert claimed is not None
        attempt_status = status if attempt_number == terminal_attempts else "failed"
        recorded = shared_action_lifecycle.record_direct_email_action_outcome(
            db,
            action_id=action_id,
            expected_status="processing",
            expected_started_at=claimed_at,
            expected_attempt_count=attempt_number - 1,
            attempt_number=attempt_number,
            status=attempt_status,
            provider_operation=action_type,
            provider_target=f"message-{classification_id}",
            provider_result_id="" if attempt_status == "failed" else f"receipt-{action_id}",
            error=error if attempt_number == terminal_attempts else "earlier failure",
            started_at=claimed_at,
            finished_at=updated_at,
            next_attempt_at=next_attempt_at or None,
            updated_at=updated_at,
        )
        assert recorded is not None
        current_status = attempt_status
    return action_id


@pytest.mark.parametrize("snapshot", [_reply_task_queue_snapshot, _email_action_queue_snapshot])
def test_queue_summary_evaluates_current_projection_once(database, snapshot):
    queries = []
    database.set_trace_callback(queries.append)
    result = snapshot(database)
    assert result["counts"] == {}
    assert result["latest_updated_at"] == ""
    assert result["latest_error"] == ""
    assert len(queries) == 1


def test_reply_summary_preserves_current_business_identity_and_latest_error(database):
    for row in (
        (1, "failed", "object-a", "2026-10-12", "retired error"),
        (2, "done", "object-a", "2026-10-11", ""),
        (3, "FAILED", "", "2026-10-10", "older failure"),
        (4, "error", "", "2026-10-11", ""),
    ):
        task_id, status, business_key, updated_at, error = row
        _insert_reply_task(database, task_id, status, updated_at, error, business_key)
    task_entity_storage.associate_business_key(
        database, task_id=2, business_key="object-a",
        created_at="2026-10-11", updated_at="2026-10-11",
    )
    result = _reply_task_queue_snapshot(database)
    assert result["counts"] == {"done": 1, "error": 1, "failed": 1}
    assert result["failed"] == 2
    assert result["latest_updated_at"] == "2026-10-11"
    assert result["latest_error"] == ""


def test_reply_summary_latest_failed_tie_uses_id(database):
    _insert_reply_task(database, 1, "failed", "2026-10-11", "first")
    _insert_reply_task(database, 2, "failed", "2026-10-11", "second")
    assert _reply_task_queue_snapshot(database)["latest_error"] == "second"


def test_email_summary_rejects_retired_status_values(database):
    _insert_email_message(database, 1)
    with pytest.raises(sqlite3.IntegrityError, match="invalid email classification status"):
        insert_email_classification(database, _classification_values(1, "current", "pending"))
    _insert_email_classification(database, 1, "current", "pending_feedback")
    _insert_email_plan(database, "current", 1)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_email_action_row(
            database, plan_id="current", classification_id=1,
            status="FAILED", attempts=0, next_attempt_at="", updated_at="2026-10-12",
            error="uppercase status",
        )


def test_reply_summary_rejects_null_business_object_key(database):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_task8(database, "reply_tasks", {
            "channel": "dingtalk",
            "conversation_id": "conversation-null-business-key",
            "conversation_title": "Conversation",
            "single_chat": 0,
            "trigger_message_id": "message-null-business-key",
            "trigger_create_time": "2026-10-11",
            "trigger_sender": "sender",
            "status": "done",
            "error": "",
            "updated_at": "2026-10-11",
            "business_object_key": None,
        })


def test_email_summary_preserves_plan_retry_and_latest_row_semantics(database):
    _insert_email_message(database, 1)
    _insert_email_classification(database, 1, "current", "processed")
    _insert_email_plan(database, "retired", 1, 1)
    _insert_email_plan(database, "current", 1, 2)
    _insert_email_message(database, 2)
    _insert_email_classification(database, 2, "inactive", "pending_feedback")
    _insert_email_plan(database, "inactive", 2)
    action_ids = {}
    for action_key, classification_id, plan_id, status, attempts, next_at, updated, error, action_type in (
        ("old", 1, "retired", "processing", 0, "", "2026-10-12", "retired error", "archive"),
        ("retry", 1, "current", "failed", DIRECT_ACTION_MAX_ATTEMPTS - 1,
         "2026-10-11T00:00:00+00:00", "2026-10-10", "retryable", "archive"),
        ("exhausted", 1, "current", "failed", DIRECT_ACTION_MAX_ATTEMPTS,
         "2026-10-11T00:00:00+00:00", "2026-10-10", "terminal", "move"),
        ("invalid", 1, "current", "failed", 0, "invalid date", "2026-10-10", "invalid", "trash"),
        ("b", 1, "current", "done", 1, "", "2026-10-11", "first", "label"),
        ("z", 1, "current", "done", 1, "", "2026-10-11", "最新\x1f错误", "mark_read"),
        ("inactive", 2, "inactive", "pending", 0, "", "2026-10-12", "inactive", "archive"),
    ):
        action_ids[action_key] = _insert_email_action_row(
            database, plan_id=plan_id,
            classification_id=classification_id, status=status, attempts=attempts,
            next_attempt_at=next_at, updated_at=updated, error=error, action_type=action_type,
        )
    assert action_ids["z"] > action_ids["b"]
    result = _email_action_queue_snapshot(database)
    assert result["counts"] == {"done": 2, "failed": 3}
    assert result["retryable"] == 1
    assert result["failed"] == 2
    assert result["processing"] == 0
    assert result["latest_updated_at"] == "2026-10-11"
    assert result["latest_error"] == "最新\x1f错误"


def test_email_summary_uses_indexed_actions_and_classification_lookup(database):
    _insert_email_message(database, 1)
    _insert_email_classification(database, 1, "current", "processed")
    _insert_email_plan(database, "current", 1)
    _insert_email_action_row(
        database, plan_id="current", classification_id=1,
        status="done", attempts=1, next_attempt_at="", updated_at="2026-10-14", error="",
    )

    statements = []
    database.set_trace_callback(statements.append)
    snapshot = _email_action_queue_snapshot(database)
    database.set_trace_callback(None)
    plan = [
        str(row[3])
        for row in database.execute("explain query plan " + statements[0])
    ]

    assert snapshot["counts"] == {"done": 1}
    assert any("SEARCH a USING INDEX idx_actions_email_public_key (record_type=?)" in step
               for step in plan), plan
    assert any("SEARCH main.emails USING INDEX sqlite_autoindex_emails_1 (classification_id=?)" in step
               for step in plan), plan


def test_reply_summary_excludes_other_task_types(database):
    _insert_task8(database, "work_summary_inputs", {
        "source_type": "message", "source_ref": "other-type",
        "payload_json": "{}", "status": "failed", "updated_at": "future",
    })
    result = _reply_task_queue_snapshot(database)
    assert result["counts"] == {}
    assert result["failed"] == 0
    assert result["latest_error"] == ""
