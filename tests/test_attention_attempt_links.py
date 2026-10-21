"""Failed cards link to persisted source-specific attempts, never Task IDs."""

import json

import pytest

from app.audit_web import _queue_attention_rows
from app.action_storage import insert_action
from app.run_storage import insert_run
from app.store import AutoReplyStore
from app.task8_storage import task8_set_parts


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
        parts = task8_set_parts(
            "reply_tasks", {"status": status, "updated_at": "2026-10-01 00:00:00"}
        )
        db.execute(
            "update tasks set " + parts.sql + " where id=?",
            (*parts.parameters, task.id),
        )
        run = insert_run(
            db,
            "agent_runs",
            dict(
                task_id=task.id,
                execution_generation=generation,
                role="consumer",
                status="failed",
            ),
        )
        attempt = insert_action(
            db,
            "reply_attempts",
            dict(
                agent_run_id=run.lastrowid,
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
        attempt_id = db.execute(
            "select legacy_key from actions where id=?", (attempt.lastrowid,)
        ).fetchone()[0]
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
    store.begin_meeting_alignment_run(job)
    with store._connect() as db:
        latest = insert_run(
            db, "meeting_alignment_runs", dict(task_id=job, status="failed")
        )
        latest_id = db.execute(
            "select legacy_key from runs where id=?", (latest.lastrowid,)
        ).fetchone()[0]
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
        run = insert_run(
            db,
            "agent_runs",
            dict(
                task_id=task.id,
                execution_generation="initial",
                role="audit",
                status="failed",
            ),
        )
        run_id = db.execute(
            "select legacy_key from runs where id=?", (run.lastrowid,)
        ).fetchone()[0]
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
        latest_id = db.execute(
            "select legacy_key from actions where id=?", (latest.lastrowid,)
        ).fetchone()[0]
        old = insert_run(
            db,
            "agent_runs",
            dict(
                task_id=task.id,
                execution_generation="previous",
                role="audit",
                status="failed",
            ),
        )
        old_id = db.execute(
            "select legacy_key from runs where id=?", (old.lastrowid,)
        ).fetchone()[0]
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
