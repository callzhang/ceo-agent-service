"""OKR and weekly readers retain their public identities on typed storage."""

from datetime import UTC, datetime, timedelta

import pytest

from app.dispatcher.adapters import OkrReviewQueueAdapter
from app.store import AutoReplyStore


def request_values(**overrides):
    return dict(
        conversation_id="chat", conversation_title="Original title",
        trigger_message_id="message", trigger_sender="Original sender",
        trigger_sender_user_id="sender-id", trigger_text="Original prompt",
        period_label="Q3", period_start="2026-07-01", period_end="2026-09-30",
        okr_source_json='{"first": true}',
    ) | overrides


def _enqueue_wrong_type_work_summary(store, ref, *, status="pending", error=""):
    task_id = store.enqueue_work_summary_input(
        source_type="test", source_ref=ref, payload_json="{}",
    )
    with store._connect() as db:
        db.execute("update tasks set status=?, error=? where id=?", (status, error, task_id))
    return task_id


def test_request_failed_only_upsert_native_id_and_run_reference(tmp_path):
    store = AutoReplyStore(tmp_path / "okr.sqlite3")
    request_id = store.create_okr_review_request(**request_values())
    wrong_type_id = _enqueue_wrong_type_work_summary(store, "other-type")
    assert wrong_type_id != request_id
    with pytest.raises(ValueError, match="not found"):
        store.get_okr_review_request(wrong_type_id)
    assert store.create_okr_review_request(**request_values(
        conversation_title="Changed title", okr_source_json='{"ignored": true}'
    )) == request_id
    assert store.get_okr_review_request(request_id).conversation_title == "Original title"
    assert store.get_okr_review_request(request_id).okr_source_json == '{"first": true}'

    store.mark_okr_review_request_failed(request_id, "temporary")
    assert store.create_okr_review_request(**request_values(
        conversation_title="Changed title", trigger_text="Changed prompt",
        okr_source_json='{"new": true}'
    )) == request_id
    row = store.get_okr_review_request(request_id)
    assert (row.conversation_title, row.trigger_text, row.okr_source_json, row.status) == (
        "Original title", "Original prompt", '{"new": true}', "pending"
    )
    assert [item.id for item in store.claim_okr_review_requests(1)] == [request_id]
    store.mark_okr_review_request_done(request_id, codex_session_id="native-session")
    run_id = store.create_professional_run(
        request_id, session_id="native-session", transcript_start=3, transcript_end=9,
    )
    run = store.get_professional_run(run_id)
    assert run is not None
    assert (run["id"], run["task_id"], run["session_id"],
            run["transcript_start"], run["transcript_end"]) == (
        run_id, request_id, "native-session", 3, 9
    )
    assert not run["native_available"]
    second_run_id = store.create_professional_run(request_id, session_id="second-native-session",
                                                  transcript_start=10, transcript_end=19)
    with pytest.raises(ValueError, match="requires a Task Agent, Meeting, or OKR Task"):
        store.create_professional_run(999999, session_id="orphan-native-session")
    assert store.get_professional_run(second_run_id)["task_id"] == request_id
    assert [item["id"] for item in store.list_okr_review_runs(request_id)] == [second_run_id, run_id]
    item_id = store.record_okr_review_item(
        request_id=request_id, objective_title="Objective", objective_weight=1,
        kr_title="KR", kr_weight=1, item_json='{"adopted": true}',
    )
    with store._connect() as db:
        assert db.execute("select task_id from runs where id=?", (run_id,)).fetchone()[0] == request_id
        assert db.execute("select status from tasks where id=?", (request_id,)).fetchone()[0] == "done"
        assert db.execute("select request_id from okr_review_items where id=?", (item_id,)).fetchone()[0] == request_id
        assert db.execute("select name from sqlite_master where name='okr_review_requests'").fetchone() is None


def test_dispatcher_claim_uses_native_request_id_and_typed_status(tmp_path):
    store = AutoReplyStore(tmp_path / "dispatch.sqlite3")
    request_id = store.create_okr_review_request(**request_values())
    adapter = OkrReviewQueueAdapter(store, owner_alive=lambda pid: False)
    now = datetime(2026, 10, 11, tzinfo=UTC)
    envelope = adapter.claim(now, owner="worker", lease=timedelta(minutes=5))
    assert envelope is not None and envelope.source_id == str(request_id)
    assert store.get_okr_review_request(request_id).status == "processing"
    successor = adapter.claim(
        now + timedelta(minutes=6), owner="successor", lease=timedelta(minutes=5)
    )
    assert successor is not None and successor.source_id == str(request_id)
    assert successor.generation > envelope.generation
    with pytest.raises(ValueError):
        adapter.release(envelope, owner="worker", now=now + timedelta(minutes=6))
    adapter.release(successor, owner="successor", now=now + timedelta(minutes=6, seconds=1))
    assert store.get_okr_review_request(request_id).status == "pending"
    with store._connect() as db:
        assert db.execute("select id from tasks where kind='okr_review'").fetchone()[0] == request_id


def test_quality_and_attention_read_typed_okr_without_wrong_type_rows(tmp_path):
    from app.audit_web import _queue_attention_rows, _queue_status_snapshots
    from app.quality_gate import scan_hourly_quality

    store = AutoReplyStore(tmp_path / "quality.sqlite3")
    request_id = store.create_okr_review_request(**request_values())
    _enqueue_wrong_type_work_summary(store, "wrong-type", status="failed", error="wrong-type failure")
    store.mark_okr_review_request_failed(request_id, "actual OKR failure")
    snapshots = _queue_status_snapshots(store)
    okr = next(row for row in snapshots if row["table"] == "okr_review_requests")
    assert (okr["failed"], okr["latest_error"]) == (1, "actual OKR failure")
    attention = _queue_attention_rows(store)
    okr_rows = [row for row in attention if row["category"] == "OKR"]
    assert len(okr_rows) == 1
    assert (okr_rows[0]["id"], okr_rows[0]["error"]) == (str(request_id), "actual OKR failure")
    report = scan_hourly_quality(store.path, now=datetime(2026, 10, 11, tzinfo=UTC))
    assert "okr_review_requests" in report.checked_sources
    assert any(issue.source == "okr_review_requests" and issue.code == "failed"
               and issue.count == 1 for issue in report.violations)
