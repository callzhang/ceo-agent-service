"""Failed cards link to persisted source-specific attempts, never Task IDs."""

import json

import pytest

from app.audit_web import _queue_attention_rows
from app.action_storage import insert_action
from app.run_entity_storage import create_run
from app.store import AutoReplyStore


def reply(store, key, *, generation="initial", status="failed", business_key=""):
    task = store.ensure_reply_task(
        conversation_id=key,
        conversation_title=key,
        single_chat=True,
        trigger_message_id=key,
        trigger_sender="sender",
        trigger_text="request",
        trigger_create_time="2026-10-01",
        business_object_key=business_key,
    )
    with store._connect() as db:
        db.execute(
            "update tasks set status=?,updated_at=? where id=? and kind='reply'",
            (status, "2026-10-01 00:00:00", task.id),
        )
        run = create_run(db, values=dict(
            task_id=task.id, execution_generation=generation, role="consumer",
            status="failed", proposal_revision=0, turn_attempt=0, parent_id=None,
            operation_id="", session_id="", transcript_start=0, transcript_end=0,
            structured_error_json="{}", error=None, lease_owner="", lease_expires_at="",
            started_at="2026-10-01 00:00:00", completed_at="2026-10-01 00:00:00",
            created_at="2026-10-01 00:00:00", updated_at="2026-10-01 00:00:00",
        ))
        attempt = insert_action(
            db,
            "reply_attempts",
            dict(
                agent_run_id=run["id"],
                conversation_id=key,
                conversation_title=key,
                trigger_message_id=key,
                trigger_sender="sender",
                trigger_text="request",
                action="reply",
                sensitivity_kind="normal",
                send_status="failed",
            ),
        )
        attempt_id = attempt.lastrowid
    return task, attempt_id


@pytest.mark.parametrize("oa", [False, True])
def test_failed_task_and_pending_oa_link_their_actual_attempt(tmp_path, oa):
    store = AutoReplyStore(tmp_path / "links.db")
    store.record_reply_attempt(
        conversation_id="prior",
        conversation_title="Prior",
        trigger_message_id="prior",
        trigger_sender="sender",
        trigger_text="prior",
        action="reply",
        sensitivity_kind="normal",
        send_status="sent",
    )
    key = "oa:process:node" if oa else "chat"
    task, attempt_id = reply(store, key, business_key=key if oa else "")
    reply(store, "unrelated")
    if oa:
        store.set_daily_scan_state(
            "oa_pending",
            last_success_at="2026-10-01",
            cursor_json=json.dumps({"pending_business_object_keys": [key]}),
        )
    rows = _queue_attention_rows(store)
    [row] = [
        r
        for r in rows
        if r["id"] == str(task.id)
        and r["category"] == ("OA approval" if oa else "Reply task")
    ]
    assert row.get("detail_url") == f"/attempts/{attempt_id}"
    assert str(attempt_id) != str(task.id)


def test_failed_task_does_not_link_attempt_from_previous_generation(tmp_path):
    store = AutoReplyStore(tmp_path / "links.db")
    task, _ = reply(store, "old", generation="previous")
    [row] = [r for r in _queue_attention_rows(store) if r["category"] == "Reply task"]
    assert row["id"] == str(task.id)
    assert row.get("detail_url") == ""


def test_meeting_links_latest_meeting_attempt_not_job_id(tmp_path):
    store = AutoReplyStore(tmp_path / "links.db")
    job = store.upsert_meeting_alignment_job(
        meeting_id="meeting",
        title="Meeting",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-10-01",
        eligible_at="",
        status="processing",
    )
    with store._connect() as db:
        native_job_id = db.execute(
            "select id from tasks where kind='meeting_alignment' and json_extract(parameters_json,'$.meeting_id')='meeting'"
        ).fetchone()[0]
    store.create_professional_run(native_job_id, status="ready_to_send")
    latest_id = store.create_professional_run(
        native_job_id, status="failed", error="meeting target missing"
    )
    store.update_meeting_alignment_job(
        job, status="failed", error="meeting target missing"
    )
    [row] = [r for r in _queue_attention_rows(store) if r["category"] == "Meeting"]
    assert row.get("detail_url") == f"/meeting-attempts/{latest_id}"
    assert str(latest_id) != str(job)


def test_standalone_reply_failure_links_original_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "links.db")
    attempt_id = store.record_reply_attempt(
        conversation_id="standalone",
        conversation_title="Failure",
        trigger_message_id="message",
        trigger_sender="sender",
        trigger_text="request",
        action="reply",
        sensitivity_kind="normal",
        send_status="failed",
    )
    [row] = [r for r in _queue_attention_rows(store) if r["category"] == "Reply"]
    assert row.get("detail_url") == f"/attempts/{attempt_id}"


def test_task_link_selects_latest_current_attempt_without_mutating_facts(tmp_path):
    store = AutoReplyStore(tmp_path / "links.db")
    task, _ = reply(store, "current")
    with store._connect() as db:
        run = create_run(db, values=dict(
            task_id=task.id, execution_generation="initial", role="audit", status="failed",
            proposal_revision=0, turn_attempt=0, parent_id=None, operation_id="audit",
            session_id="", transcript_start=0, transcript_end=0, structured_error_json="{}",
            error=None, lease_owner="", lease_expires_at="", started_at="2026-10-01 00:00:00",
            completed_at="2026-10-01 00:00:00", created_at="2026-10-01 00:00:00",
            updated_at="2026-10-01 00:00:00",
        ))
        run_id = run["id"]
        latest = insert_action(
            db,
            "reply_attempts",
            dict(
                agent_run_id=int(run_id),
                conversation_id="current",
                conversation_title="Current",
                trigger_message_id="current",
                trigger_sender="sender",
                trigger_text="request",
                action="reply",
                sensitivity_kind="normal",
                send_status="failed",
            ),
        )
        latest_id = latest.lastrowid
        old = create_run(db, values=dict(
            task_id=task.id, execution_generation="previous", role="audit", status="failed",
            proposal_revision=0, turn_attempt=0, parent_id=None, operation_id="audit-old",
            session_id="", transcript_start=0, transcript_end=0, structured_error_json="{}",
            error=None, lease_owner="", lease_expires_at="", started_at="2026-10-01 00:00:00",
            completed_at="2026-10-01 00:00:00", created_at="2026-10-01 00:00:00",
            updated_at="2026-10-01 00:00:00",
        ))
        old_id = old["id"]
        insert_action(
            db,
            "reply_attempts",
            dict(
                agent_run_id=int(old_id),
                conversation_id="old",
                conversation_title="Old",
                trigger_message_id="old",
                trigger_sender="sender",
                trigger_text="old",
                action="reply",
                sensitivity_kind="normal",
                send_status="failed",
            ),
        )
        before = {
            t: db.execute(f"select * from {t}").fetchall()
            for t in ("tasks", "runs", "actions")
        }
    [row] = [r for r in _queue_attention_rows(store) if r["category"] == "Reply task"]
    assert row["detail_url"] == f"/attempts/{latest_id}"
    with store._connect() as db:
        assert before == {
            t: db.execute(f"select * from {t}").fetchall() for t in before
        }
