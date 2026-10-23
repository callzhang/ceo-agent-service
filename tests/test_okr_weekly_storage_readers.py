"""OKR and weekly readers retain their public identities on typed storage."""

from datetime import UTC, datetime, timedelta

import pytest

from app.dispatcher.adapters import OkrReviewQueueAdapter
from app.store import AutoReplyStore
from app.task20_definitions import task20_projection
from app.task12_storage import task12_projection
from app.task8_storage import task8_insert_parts


def request_values(**overrides):
    return dict(
        conversation_id="chat", conversation_title="Original title",
        trigger_message_id="message", trigger_sender="Original sender",
        trigger_sender_user_id="sender-id", trigger_text="Original prompt",
        period_label="Q3", period_start="2026-07-01", period_end="2026-09-30",
        okr_source_json='{"first": true}',
    ) | overrides


def _insert_wrong_type_work_summary(db, public_id, *, status="pending", error=""):
    parts = task8_insert_parts("work_summary_inputs", {
        "legacy_key": public_id, "source_type": "test", "source_ref": "other-type",
        "payload_json": "{}", "status": status, "error": error,
    })
    return db.execute(
        f"insert into tasks ({parts.columns}) values ({parts.values})", parts.parameters,
    ).lastrowid


def test_request_failed_only_upsert_public_id_and_run_reference(tmp_path):
    store = AutoReplyStore(tmp_path / "okr.sqlite3")
    physical_id = store.create_okr_review_request(**request_values())
    with store._connect() as db:
        db.execute(
            "update tasks set legacy_key='501' where record_type='okr_review_requests' and id=?",
            (physical_id,),
        )
        _insert_wrong_type_work_summary(db, 501)
    assert store.create_okr_review_request(**request_values(
        conversation_title="Changed title", okr_source_json='{"ignored": true}'
    )) == 501
    assert store.get_okr_review_request(501).conversation_title == "Original title"
    assert store.get_okr_review_request(501).okr_source_json == '{"first": true}'

    store.mark_okr_review_request_failed(501, "temporary")
    assert store.create_okr_review_request(**request_values(
        conversation_title="Changed title", trigger_text="Changed prompt",
        okr_source_json='{"new": true}'
    )) == 501
    row = store.get_okr_review_request(501)
    assert (row.conversation_title, row.trigger_text, row.okr_source_json, row.status) == (
        "Original title", "Original prompt", '{"new": true}', "pending"
    )
    assert [item.id for item in store.claim_okr_review_requests(1)] == [501]
    store.mark_okr_review_request_done(501, codex_session_id="native-session")
    run_id = store.create_logical_run('okr_review_runs', task_public_id=501, codex_session_id="native-session", codex_transcript_start_line=3, codex_transcript_end_line=9)
    with store._connect() as db:
        db.execute("update runs set legacy_key='900' "
                   "where record_type='agent_runs' and source_type='okr_review_runs' and id=?", (run_id,))
    run = store.get_logical_run('okr_review_runs', 900)
    assert run is not None
    assert (run["id"], run["request_id"], run["codex_session_id"],
            run["codex_transcript_start_line"], run["codex_transcript_end_line"]) == (
        900, 501, "native-session", 3, 9
    )
    assert store.get_logical_run('okr_review_runs', run_id) is None
    assert not run["native_available"]
    second_run_id = store.create_logical_run('okr_review_runs', task_public_id=501, codex_session_id="second-native-session", codex_transcript_start_line=10, codex_transcript_end_line=19)
    with pytest.raises(ValueError, match="parent does not exist"):
        store.create_logical_run('okr_review_runs', 999999, codex_session_id="orphan-native-session")
    assert store.get_logical_run('okr_review_runs', second_run_id)["request_id"] == 501
    item_id = store.record_okr_review_item(
        request_id=501, objective_title="Objective", objective_weight=1,
        kr_title="KR", kr_weight=1, item_json='{"adopted": true}',
    )
    with store._connect() as db:
        assert db.execute("select cast(p.legacy_key as integer) from runs r join tasks p on p.id=r.task_id "
                          "where r.record_type='agent_runs' and r.source_type='okr_review_runs' and cast(r.legacy_key as integer)=?", (900,)).fetchone()[0] == 501
        assert db.execute(f"select status from ({task20_projection('work_summary_inputs')}) "
                          "where id=501").fetchone()[0] == "pending"
        assert db.execute(f"select request_id from ({task12_projection('okr_review_items')}) where id=?", (item_id,)).fetchone()[0] == 501
        assert db.execute("select name from sqlite_master where name='okr_review_requests'").fetchone() is None




def test_dispatcher_claim_uses_public_request_id_and_typed_status(tmp_path):
    store = AutoReplyStore(tmp_path / "dispatch.sqlite3")
    physical_id = store.create_okr_review_request(**request_values())
    with store._connect() as db:
        db.execute("update tasks set legacy_key='777' where record_type='okr_review_requests' "
                   "and id=?", (physical_id,))
    adapter = OkrReviewQueueAdapter(store, owner_alive=lambda pid: False)
    now = datetime(2026, 10, 11, tzinfo=UTC)
    envelope = adapter.claim(now, owner="worker", lease=timedelta(minutes=5))
    assert envelope is not None and envelope.source_id == "777"
    assert store.get_okr_review_request(777).status == "processing"
    successor = adapter.claim(
        now + timedelta(minutes=6), owner="successor", lease=timedelta(minutes=5)
    )
    assert successor is not None and successor.source_id == "777"
    assert successor.generation > envelope.generation
    with pytest.raises(ValueError):
        adapter.release(envelope, owner="worker", now=now + timedelta(minutes=6))
    adapter.release(successor, owner="successor", now=now + timedelta(minutes=6, seconds=1))
    assert store.get_okr_review_request(777).status == "pending"
    with store._connect() as db:
        assert db.execute(f"select id from ({task20_projection('okr_review_requests')})").fetchone()[0] == 777


def test_quality_and_attention_read_typed_okr_without_wrong_type_rows(tmp_path):
    from app.audit_web import _queue_attention_rows, _queue_status_snapshots
    from app.quality_gate import scan_hourly_quality

    store = AutoReplyStore(tmp_path / "quality.sqlite3")
    physical_id = store.create_okr_review_request(**request_values())
    with store._connect() as db:
        db.execute("update tasks set legacy_key='777' where record_type='okr_review_requests' "
                   "and id=?", (physical_id,))
        _insert_wrong_type_work_summary(
            db, 777, status="failed", error="wrong-type failure",
        )
    store.mark_okr_review_request_failed(777, "actual OKR failure")
    snapshots = _queue_status_snapshots(store)
    okr = next(row for row in snapshots if row["table"] == "okr_review_requests")
    assert (okr["failed"], okr["latest_error"]) == (1, "actual OKR failure")
    attention = _queue_attention_rows(store)
    okr_rows = [row for row in attention if row["category"] == "OKR"]
    assert len(okr_rows) == 1
    assert (okr_rows[0]["id"], okr_rows[0]["error"]) == ("777", "actual OKR failure")
    report = scan_hourly_quality(store.path, now=datetime(2026, 10, 11, tzinfo=UTC))
    assert "okr_review_requests" in report.checked_sources
    assert any(issue.source == "okr_review_requests" and issue.code == "failed"
               and issue.count == 1 for issue in report.violations)
