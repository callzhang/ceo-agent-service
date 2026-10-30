import pytest

from app.store import AutoReplyStore
from app.state_storage import insert_state


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (
            "failed_browser: category=operation_failed;detail=net::ERR_TIMED_OUT;operation=open_entry",
            True,
        ),
        (
            "failed_browser: category=operation_timeout;detail=provider timeout;operation=follow_redirect",
            True,
        ),
        (
            "failed_browser: category=form_response_rejected;detail=provider rejected;operation=submit_form",
            True,
        ),
        (
            "failed_browser: category=control_unavailable;detail=control absent;operation=click_confirmation",
            True,
        ),
        (
            "failed_browser: category=page_state_unknown;detail=state unavailable;operation=read_page",
            False,
        ),
        (
            "failed_browser: category=operation_timeout;detail=provider timeout;operation=unknown_operation",
            False,
        ),
        ("malformed attempt evidence", False),
    ],
)
def test_legacy_attempt_retry_classification_uses_shared_external_types(
    summary: str, expected: bool,
):
    assert AutoReplyStore._legacy_attempt_has_external_transient_failure(summary) is expected


@pytest.mark.parametrize(
    ("source", "category", "operation", "task_error", "should_recover"),
    [
        (
            "body_text_https", "form_response_rejected", "submit_form",
            "email_unsubscribe_browser_failed: category=form_response_rejected", True,
        ),
        (
            "header_https", "operation_timeout", "follow_redirect",
            "email_unsubscribe_browser_timeout", True,
        ),
        (
            "body_html_https", "page_state_unknown", "read_page",
            "unsubscribe_operation_rejected:ValueError", False,
        ),
        (
            "not_a_supported_source", "operation_timeout", "open_entry",
            "email_unsubscribe_browser_timeout", False,
        ),
    ],
)
def test_legacy_fence_recovery_is_shared_across_sources_and_external_failures(
    tmp_path, source: str, category: str, operation: str, task_error: str,
    should_recover: bool,
):
    import hashlib
    import json

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    action_identity = f"email-action:{source}:{operation}"
    payload = {
        "schema": "email_agent_action.v1",
        "action_type": "unsubscribe",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_identity": action_identity,
        "action_parameters": {
            "candidate_reference": "unsubscribe-entry:stable",
            "candidate_source": source,
        },
        "unsubscribe_entries": [{
            "reference": "unsubscribe-entry:stable",
            "source": source,
        }],
    }
    input_json = json.dumps(payload, separators=(",", ":"))
    store.enqueue_reply_task(
        conversation_id=f"email-thread:{source}",
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=action_identity,
        trigger_create_time="2026-10-15 20:43:35",
        trigger_sender="sender@example.com",
        trigger_text="Authorized unsubscribe action.",
        trigger_message_json=input_json,
        channel="email",
    )
    task = store.list_reply_tasks(limit=1)[0]
    store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=action_identity,
        trigger_sender="sender@example.com",
        trigger_text="Authorized unsubscribe action.",
        action="direct_unsubscribe",
        sensitivity_kind="none",
        audit_summary=(
            f"failed_browser: category={category};detail=provider failure;"
            f"operation={operation}"
        ),
        send_status="failed",
        channel="email",
    )
    input_digest = hashlib.sha256(input_json.encode()).hexdigest()
    with store._immediate_write_transaction() as db:
        db.execute(
            "update tasks set status='failed',error=? where id=?",
            (task_error, task.id),
        )
        insert_state(db, "service_state", {
            "key": f"email_unsubscribe_execution_pending:v1:{action_identity}",
            "value": json.dumps({
                "task_id": task.id,
                "action_identity": action_identity,
                "execution_generation": "initial",
                "input_version": task.input_version,
                "input_digest": input_digest,
                "started_at": "2026-10-15T20:43:35+00:00",
            }),
        })

    recovered = store.retry_failed_effect_free_email_unsubscribe_tasks()
    assert recovered == ([task.id] if should_recover else [])
    assert store.list_reply_tasks(limit=1)[0].status == (
        "pending" if should_recover else "failed"
    )
