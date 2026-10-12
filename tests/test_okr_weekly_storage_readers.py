"""OKR and weekly readers retain their public identities on typed storage."""

from datetime import UTC, datetime, timedelta

import pytest

from app.dispatcher.adapters import OkrReviewQueueAdapter
from app.store import AutoReplyStore, WeeklyOkrAnalysisJobClaimOutcome
from app.task_storage import OKR_REQUEST_SQL, WEEKLY_JOB_SQL, insert_task


def request_values(**overrides):
    return dict(
        conversation_id="chat", conversation_title="Original title",
        trigger_message_id="message", trigger_sender="Original sender",
        trigger_sender_user_id="sender-id", trigger_text="Original prompt",
        period_label="Q3", period_start="2026-07-01", period_end="2026-09-30",
        okr_source_json='{"first": true}',
    ) | overrides


def test_request_failed_only_upsert_public_id_and_run_reference(tmp_path):
    store = AutoReplyStore(tmp_path / "okr.sqlite3")
    physical_id = store.create_okr_review_request(**request_values())
    with store._connect() as db:
        db.execute(
            "update tasks set legacy_key='501' where record_type='okr_review_requests' and id=?",
            (physical_id,),
        )
        insert_task(db, "work_summary_inputs", {
            "legacy_key": 501, "source_type": "test", "source_ref": "other-type",
            "payload_json": "{}",
        })
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
    run_id = store.record_okr_review_run(
        request_id=501, codex_session_id="native-session",
        codex_transcript_start_line=3, codex_transcript_end_line=9,
        envelope_json="{}", audit_tool_events_json="[]", audit_summary="",
    )
    with store._connect() as db:
        db.execute("update runs set legacy_key='900' "
                   "where record_type='okr_review_runs' and id=?", (run_id,))
    run = store.get_okr_review_run(900)
    assert run is not None
    assert (run["id"], run["request_id"], run["codex_session_id"],
            run["codex_transcript_start_line"], run["codex_transcript_end_line"]) == (
        900, 501, "native-session", 3, 9
    )
    assert store.get_okr_review_run(run_id) is None
    assert not run["native_available"]
    second_run_id = store.record_okr_review_run(
        request_id=501, codex_session_id="second-native-session",
        codex_transcript_start_line=10, codex_transcript_end_line=19,
        envelope_json="{}", audit_tool_events_json="[]", audit_summary="",
    )
    missing_parent_run_id = store.record_okr_review_run(
        request_id=999999, codex_session_id="orphan-native-session",
        codex_transcript_start_line=1, codex_transcript_end_line=2,
        envelope_json="{}", audit_tool_events_json="[]", audit_summary="",
    )
    assert store.get_okr_review_run(second_run_id)["request_id"] == 501
    assert store.get_okr_review_run(missing_parent_run_id)["request_id"] == 999999
    item_id = store.record_okr_review_item(
        request_id=501, objective_title="Objective", objective_weight=1,
        kr_title="KR", kr_weight=1, item_json='{"adopted": true}',
    )
    with store._connect() as db:
        assert db.execute("select request_id from runs where record_type='okr_review_runs' "
                          "and cast(legacy_key as integer)=?", (900,)).fetchone()[0] == 501
        assert db.execute("select status from tasks where record_type='work_summary_inputs' "
                          "and cast(legacy_key as integer)=501").fetchone()[0] == "pending"
        assert db.execute("select request_id from okr_review_items where id=?", (item_id,)).fetchone()[0] == 501
        assert db.execute("select name from sqlite_master where name='okr_review_requests'").fetchone() is None


def test_weekly_public_job_id_lease_owner_and_runtime_parent(tmp_path):
    store = AutoReplyStore(tmp_path / "weekly.sqlite3")
    values = dict(week_end="2026-10-11", manager_user_id="manager", source_digest="a" * 64)
    first = store.begin_weekly_okr_analysis_job(
        **values, owner="first", lease_seconds=60, now="2026-10-11 01:00:00"
    )
    assert first.outcome == WeeklyOkrAnalysisJobClaimOutcome.CLAIMED
    with store._connect() as db:
        db.execute("update tasks set legacy_key='574' "
                   "where record_type='weekly_okr_analysis_jobs' and id=?", (first.job_id,))
        insert_task(db, "work_summary_inputs", {
            "legacy_key": 574, "source_type": "test", "source_ref": "other-type",
            "payload_json": "{}",
        })
        assert AutoReplyStore._runtime_operation_parent_exists(
            db, "weekly_okr", f"{values['week_end']}:manager:{values['source_digest']}:574:first"
        )
        assert not AutoReplyStore._runtime_operation_parent_exists(
            db, "weekly_okr", f"{values['week_end']}:manager:{values['source_digest']}:{first.job_id}:first"
        )
    assert store.begin_weekly_okr_analysis_job(
        **values, owner="second", lease_seconds=60, now="2026-10-11 01:00:30"
    ).outcome == WeeklyOkrAnalysisJobClaimOutcome.IN_PROGRESS
    reclaimed = store.begin_weekly_okr_analysis_job(
        **values, owner="second", lease_seconds=60, now="2026-10-11 01:01:01"
    )
    assert (reclaimed.job_id, reclaimed.reclaimed_stale) == (574, True)
    with pytest.raises(ValueError, match="lease ownership lost"):
        store.finish_weekly_okr_analysis_job(
            574, status="completed", owner="first", now="2026-10-11 01:01:02"
        )
    store.finish_weekly_okr_analysis_job(
        574, status="completed", owner="second", now="2026-10-11 01:01:02"
    )
    assert store.begin_weekly_okr_analysis_job(
        **values, owner="third", now="2026-10-11 01:01:03"
    ).outcome == WeeklyOkrAnalysisJobClaimOutcome.CACHE_HIT
    miss = store.reclaim_weekly_okr_analysis_job_cache_miss(
        574, **values, owner="third", now="2026-10-11 01:01:04"
    )
    assert (miss.job_id, miss.outcome) == (574, WeeklyOkrAnalysisJobClaimOutcome.CLAIMED)
    with store._connect() as db:
        row = db.execute(f"select id,status,lease_owner from ({WEEKLY_JOB_SQL}) where id=574").fetchone()
        assert tuple(row) == (574, "running", "third")
        assert db.execute("select status from tasks where record_type='work_summary_inputs' "
                          "and cast(legacy_key as integer)=574").fetchone()[0] == "pending"
    later = store.begin_weekly_okr_analysis_job(
        week_end="2026-10-18", manager_user_id="manager", source_digest="b" * 64,
        owner="later", now="2026-10-11 01:30:00",
    )
    store.finish_weekly_okr_analysis_job(
        later.job_id, status="completed", owner="later", now="2026-10-11 01:30:01",
    )
    assert store.complete_superseded_stale_weekly_okr_analysis_jobs(
        now="2026-10-11 02:00:00"
    ) == 1
    with store._connect() as db:
        row = db.execute(f"select status,error,lease_owner,lease_expires_at,finished_at "
                         f"from ({WEEKLY_JOB_SQL}) where id=574").fetchone()
        assert tuple(row) == (
            "completed", "superseded_by_later_completed_week", "", "", "2026-10-11 02:00:00"
        )
    technical = store.begin_weekly_okr_analysis_job(
        week_end="2026-10-04", manager_user_id="manager", source_digest="c" * 64,
        owner="technical", now="2026-10-11 02:01:00",
    )
    business = store.begin_weekly_okr_analysis_job(
        week_end="2026-10-04", manager_user_id="manager", source_digest="d" * 64,
        owner="business", now="2026-10-11 02:01:00",
    )
    store.finish_weekly_okr_analysis_job(
        technical.job_id, status="failed", error="runtime_route_unavailable",
        owner="technical", now="2026-10-11 02:01:01",
    )
    store.finish_weekly_okr_analysis_job(
        business.job_id, status="failed", error="analysis_rejected",
        owner="business", now="2026-10-11 02:01:01",
    )
    assert store.complete_superseded_failed_weekly_okr_analysis_jobs(
        now="2026-10-11 02:02:00"
    ) == 1
    with store._connect() as db:
        rows = db.execute(f"select id,status,error,finished_at from ({WEEKLY_JOB_SQL}) "
                          "where id in (?, ?) order by id", (technical.job_id, business.job_id)).fetchall()
        by_id = {row["id"]: row for row in rows}
        assert tuple(by_id[technical.job_id])[1:] == (
            "completed", "superseded_by_later_completed_week", "2026-10-11 02:02:00"
        )
        assert tuple(by_id[business.job_id])[1:3] == ("failed", "analysis_rejected")


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
        assert db.execute(f"select id from ({OKR_REQUEST_SQL})").fetchone()[0] == 777


def test_quality_and_attention_read_typed_okr_without_wrong_type_rows(tmp_path):
    from app.audit_web import _queue_attention_rows, _queue_status_snapshots
    from app.quality_gate import scan_hourly_quality

    store = AutoReplyStore(tmp_path / "quality.sqlite3")
    physical_id = store.create_okr_review_request(**request_values())
    with store._connect() as db:
        db.execute("update tasks set legacy_key='777' where record_type='okr_review_requests' "
                   "and id=?", (physical_id,))
        insert_task(db, "work_summary_inputs", {
            "legacy_key": 777, "source_type": "test", "source_ref": "wrong-type",
            "payload_json": "{}", "status": "failed", "error": "wrong-type failure",
        })
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
