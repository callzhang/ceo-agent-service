from datetime import datetime, timedelta, timezone

from app.quality_gate import add_channel_health, scan_hourly_quality, write_hourly_quality_state
from app.cli import WorkerSettings, quality_check_command
from app.store import AutoReplyStore


NOW = datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc)


def _insert_reply_task(store, *, status="pending", updated_at="2026-08-07 00:59:00"):
    with store._connect() as db:
        db.execute(
            """insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, status, updated_at
            ) values ('dingtalk', 'conversation', 'group', 0, 'message',
                '2026-08-07 00:00:00', 'sender', 'trigger', ?, ?)""",
            (status, updated_at),
        )


def test_quality_gate_fails_closed_when_a_required_source_is_missing(tmp_path):
    report = scan_hourly_quality(tmp_path / "empty.sqlite3", now=NOW)

    assert not report.ok
    assert "reply_tasks" in report.missing_sources
    assert {issue.code for issue in report.violations} == {"source_missing"}


def test_quality_gate_detects_failed_queues_and_stale_processing(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    stale = (NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _insert_reply_task(store, status="processing", updated_at=stale)
    with store._connect() as db:
        db.execute(
            """insert into meeting_alignment_jobs (meeting_id, status, updated_at)
               values ('meeting', 'failed', ?)""",
            (stale,),
        )
        db.execute(
            """insert into follow_up_drafts (project_id, status, scheduled_at)
               values (1, 'draft', ?)""",
            (stale,),
        )
        db.execute(
            """insert into work_summary_inputs (source_type, source_ref, payload_json, status, updated_at)
               values ('message', 'work', '{}', 'failed', ?)""",
            (stale,),
        )
        db.execute(
            """insert into errors (kind, detail, created_at)
               values ('producer', 'temporary failure', '2026-08-07 00:59:00')"""
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not report.ok
    assert {(issue.source, issue.code) for issue in report.violations} >= {
        ("reply_tasks", "processing_stale"),
        ("meeting_alignment_jobs", "failed"),
        ("follow_up_drafts", "scheduled_overdue"),
        ("work_summary_inputs", "failed"),
        ("errors", "recent_error"),
    }


def test_quality_gate_keeps_future_follow_up_as_attention_not_failure(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    future = (NOW + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    with store._connect() as db:
        db.execute(
            """insert into follow_up_drafts (project_id, status, scheduled_at)
               values (1, 'draft', ?)""",
            (future,),
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert [(item.source, item.code, item.count) for item in report.attention] == [
        ("follow_up_drafts", "future_scheduled", 1)
    ]


def test_quality_gate_deduplicates_historical_attempts_after_a_later_success(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        for status, updated_at in (
            ("blocked", "2026-08-07 00:00:00"),
            ("sent", "2026-08-07 00:30:00"),
        ):
            db.execute(
                """insert into reply_attempts (
                    conversation_id, conversation_title, trigger_message_id,
                    trigger_sender, trigger_text, action, sensitivity_kind,
                    final_reply_text, permission_action, send_status, updated_at
                ) values ('conversation', 'group', 'message', 'sender', 'trigger',
                    'send_reply', 'normal', 'reply', 'send', ?, ?)""",
                (status, updated_at),
            )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert not [item for item in report.violations if item.source == "reply_attempts"]


def test_quality_gate_writes_coverage_state(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    state_path = tmp_path / "hourly-quality-gate.json"

    report = scan_hourly_quality(store.path, now=NOW)
    write_hourly_quality_state(report, state_path)

    content = state_path.read_text(encoding="utf-8")
    assert '"mode": "fail_closed_queue_coverage"' in content
    assert '"reply_tasks"' in content


def test_quality_check_command_writes_state_and_returns_nonzero_for_violation(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    AutoReplyStore(db_path)
    state_path = tmp_path / "gate.json"

    assert quality_check_command(WorkerSettings(db_path=db_path), state_file=state_path) == 0
    with AutoReplyStore(db_path)._connect() as db:
        db.execute(
            """insert into work_summary_inputs (source_type, source_ref, payload_json, status)
               values ('message', 'failed', '{}', 'failed')"""
        )

    assert quality_check_command(WorkerSettings(db_path=db_path), state_file=state_path) == 2


def test_quality_gate_fails_when_a_live_channel_is_not_ready(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")

    report = add_channel_health(
        scan_hourly_quality(store.path, now=NOW),
        {"dingtalk": "ready", "wechat": "blocked"},
    )

    assert not report.ok
    assert report.checked_sources[-2:] == ("channel:dingtalk", "channel:wechat")
    assert [(item.source, item.code) for item in report.violations] == [
        ("channel:wechat", "not_ready")
    ]
