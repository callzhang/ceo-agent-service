import json
from datetime import datetime, timedelta, timezone

from app.quality_gate import (
    add_channel_health,
    required_live_channels,
    scan_hourly_quality,
    write_hourly_quality_state,
)
from app.cli import WorkerSettings, quality_check_command
from app.store import AutoReplyStore


NOW = datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc)


def _structured_needs_human_result(
    *, risk="high", confidence=0.2, rule_coverage=1.0,
    information_completeness=1.0, options=None,
):
    return {
        "outcome": "needs_human",
        "summary": "decision required",
        "proposal_revision": 0,
        "feedback": None,
        "external_result": None,
        "decision_options": options or [
            {"key": "approve", "label": "Approve", "instruction": "approve", "consequence": "run"},
            {"key": "reject", "label": "Reject", "instruction": "reject", "consequence": "stop"},
        ],
        "error": {"code": "", "message": "", "retryable": False, "authorization_required": False},
        "risk": risk,
        "confidence": confidence,
        "rule_coverage": rule_coverage,
        "information_completeness": information_completeness,
    }


def _insert_needs_human_projection(store, *, result, status="needs_human"):
    store.enqueue_reply_task(
        conversation_id="conversation", conversation_title="group", single_chat=False,
        trigger_message_id="message", trigger_create_time="2026-08-07 00:00:00",
        trigger_sender="sender", trigger_text="trigger", execution_generation="generation",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="conversation", conversation_title="group",
        trigger_message_id="message", trigger_sender="sender", trigger_text="trigger",
        action="agent_run", sensitivity_kind="general", send_status=status,
    )
    with store._connect() as db:
        task_id = db.execute(
            "select id from reply_tasks where conversation_id='conversation' and trigger_message_id='message'"
        ).fetchone()[0]
        db.execute("update reply_tasks set status='done' where id=?", (task_id,))
        run = db.execute(
            """insert into agent_runs (
                reply_task_id, execution_generation, role, status, final_result_json
            ) values (?, 'generation', 'audit', 'completed', ?)""",
            (task_id, json.dumps(result)),
        )
        db.execute("update reply_attempts set agent_run_id=? where id=?", (run.lastrowid, attempt_id))
    return attempt_id


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


def test_quality_gate_runtime_attempt_invariants_use_persisted_rows(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute("pragma foreign_keys=off")
        db.execute(
            """insert into agent_runtime_attempts
            (agent_run_id, workload_kind, workload_key, attempt_number, route_name,
             runtime_kind, credential_mode, model, status, first_effect_started_at)
            values (999, 'agent_run', '999', 1, 'codex_oauth', 'codex_cli',
                    'local_oauth', 'gpt-5.5', 'completed', '2026-08-07 00:00:00')"""
        )
        db.execute(
            """insert into agent_runtime_attempts
            (agent_run_id, workload_kind, workload_key, attempt_number, route_name, runtime_kind,
             credential_mode, model, status)
            values (999, 'agent_run', '999', 2, 'codex_api', 'codex_cli', 'service_api',
                    'gpt-5.5', 'running')"""
        )
    codes = {issue.code for issue in scan_hourly_quality(store.path, now=NOW).violations}
    assert {
        "runtime_attempt_without_parent",
        "completed_runtime_attempt_without_final_run",
    } <= codes
    assert "unsafe_runtime_failover" not in codes
    assert "unknown_effect_with_fallback_attempt" not in codes


def test_quality_gate_covers_every_runtime_attempt_invariant_with_persisted_rows(tmp_path):
    seeders = {
        "runtime_attempt_without_parent": [
            "pragma foreign_keys=off",
            """insert into agent_runtime_attempts
               (agent_run_id, workload_kind, workload_key, attempt_number, route_name,
                runtime_kind, credential_mode, model, status)
               values (999, 'agent_run', '999', 1, 'codex_oauth', 'codex_cli',
                       'local_oauth', 'gpt-5.5', 'failed')""",
        ],
        "multiple_active_runtime_attempts": [
            """insert into agent_runtime_attempts
               (workload_kind, workload_key, attempt_number, route_name, runtime_kind,
                credential_mode, model, status, lease_owner, lease_expires_at)
               values ('task', 'active-pair', 1, 'codex_oauth', 'codex_cli',
                       'local_oauth', 'gpt-5.5', 'running', 'worker-1',
                       '2026-08-07 01:00:00')""",
            """insert into agent_runtime_attempts
               (workload_kind, workload_key, attempt_number, route_name, runtime_kind,
                credential_mode, model, status, lease_owner, lease_expires_at)
               values ('task', 'active-pair', 2, 'codex_api', 'codex_cli',
                       'service_api', 'gpt-5.5', 'starting', 'worker-2',
                       '2026-08-07 01:00:00')""",
        ],
        "completed_runtime_attempt_without_final_run": [
            "pragma foreign_keys=off",
            """insert into agent_runtime_attempts
               (agent_run_id, workload_kind, workload_key, attempt_number,
                route_name, runtime_kind, credential_mode, model, status)
               values (998, 'agent_run', '998', 1, 'codex_oauth', 'codex_cli',
                       'local_oauth', 'gpt-5.5', 'completed')""",
        ],
        "runtime_secret_leak": [
            """insert into agent_runtime_attempts
               (workload_kind, workload_key, attempt_number, route_name, runtime_kind,
                credential_mode, model, status)
               values ('task', 'secret-row', 1,
                       'sk-proj-abcdefghijklmnopqrstuvwxyz', 'codex_cli',
                       'service_api', 'gpt-5.5', 'failed')""",
        ],
    }
    for code, statements in seeders.items():
        store = AutoReplyStore(tmp_path / f"{code}.sqlite3")
        with store._connect() as db:
            for statement in statements:
                db.execute(statement)
        codes = {issue.code for issue in scan_hourly_quality(store.path, now=NOW).violations}
        assert code in codes


def test_quality_gate_treats_route_pause_as_warning_with_healthy_alternative(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into runtime_route_pauses
               (route_name, failure_code, retry_at, opened_at, updated_at)
               values ('codex_oauth', 'codex_login_required',
                       '2026-08-07 02:00:00', '2026-08-07 00:00:00',
                       '2026-08-07 00:00:00')"""
        )
        db.execute(
            """insert into agent_runtime_attempts
               (workload_kind, workload_key, attempt_number, route_name, runtime_kind,
                credential_mode, model, status, finished_at)
               values ('task', 'healthy-alternative', 1, 'codex_api', 'codex_cli',
                       'service_api', 'gpt-5.5', 'completed',
                       '2026-08-07 00:01:00')"""
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert ("runtime_route_pauses", "route_paused_with_healthy_alternative", 1) in {
        (issue.source, issue.code, issue.count) for issue in report.attention
    }


def test_required_live_channels_skips_unused_lark_but_includes_referenced_lark(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    _insert_reply_task(store)

    assert required_live_channels(store.path) == frozenset({"codex", "dingtalk"})

    with store._connect() as db:
        db.execute(
            """update reply_tasks set trigger_message_json=?
               where conversation_id='conversation'""",
            ('{"link":"https://example.larksuite.com/docx/demo"}',),
        )

    assert required_live_channels(store.path) == frozenset(
        {"codex", "dingtalk", "lark"}
    )


def test_quality_gate_detects_failed_queues_and_stale_processing(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    stale = (NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _insert_reply_task(store, status="processing", updated_at=stale)
    with store._connect() as db:
        db.execute(
            """insert into meeting_alignment_jobs
               (meeting_id, status, error, updated_at)
               values ('meeting', 'failed', '', ?)""",
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
            """insert into errors (
                   conversation_id, message_id, kind, detail, created_at
               ) values (
                   'conversation', 'message', 'producer', 'temporary failure',
                   '2026-08-07 00:59:00'
               )"""
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


def test_quality_gate_accepts_explicit_terminal_failures(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, status, error
            ) values ('dingtalk', 'conversation', 'group', 0, 'message',
                '2026-08-07 00:00:00', 'sender', 'trigger', 'failed',
                'image_dependency_unavailable')"""
        )
        db.execute(
            """insert into meeting_alignment_jobs
               (meeting_id, status, error)
               values ('meeting', 'failed', '{\"kind\":\"meeting_agent\",\"message\":\"runtime_execution_failed\"}')"""
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not [
        issue for issue in report.violations
        if issue.code == "failed"
        and issue.source in {"reply_tasks", "meeting_alignment_jobs"}
    ]


def test_quality_gate_excludes_recent_error_recovered_by_later_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into errors (
                conversation_id, message_id, kind, detail, created_at
            ) values ('conversation', 'message', 'consumer', 'temporary failure',
                '2026-08-07 00:30:00')"""
        )
        db.execute(
            """insert into reply_attempts (
                conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind,
                final_reply_text, permission_action, send_status, updated_at
            ) values ('conversation', 'group', 'message', 'sender', 'trigger',
                'agent_run', 'normal', '', 'none', 'completed',
                '2026-08-07 00:45:00')"""
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not [
        issue
        for issue in report.violations
        if issue.source == "errors" and issue.code == "recent_error"
    ]


def test_quality_gate_excludes_explicitly_resolved_global_error(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.record_error(None, None, "task_agent", "temporary provider failure")
    error_id = store.list_errors(limit=1)[0].id

    assert store.resolve_errors(
        [error_id],
        resolution="Codex channel is healthy after readback.",
    ) == 1

    report = scan_hourly_quality(store.path, now=NOW)

    assert not [
        issue
        for issue in report.violations
        if issue.source == "errors" and issue.code == "recent_error"
    ]


def test_quality_gate_excludes_open_global_service_error(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.record_error(None, None, "read_robot_direct_messages", "temporary DWS failure")
    with store._connect() as db:
        db.execute(
            "update errors set created_at=? where kind='read_robot_direct_messages'",
            ((NOW - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),),
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not [
        issue
        for issue in report.violations
        if issue.source == "errors" and issue.code == "recent_error"
    ]


def test_quality_gate_reports_active_codex_capacity_pause_as_attention(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.open_codex_capacity_pause(
        retry_at=(NOW + timedelta(minutes=30)).isoformat(), now=NOW
    )
    store.record_error(
        None,
        None,
        "codex_capacity_pause",
        "Codex workspace credits are exhausted; work is paused until the next capacity check",
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert [(issue.source, issue.code, issue.count) for issue in report.attention] == [
        ("codex_capacity", "paused", 1)
    ]


def test_quality_gate_marks_overdue_pending_reply_as_attention_during_capacity_pause(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    stale = (NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _insert_reply_task(store, updated_at=stale)
    store.open_codex_capacity_pause(
        retry_at=(NOW + timedelta(minutes=30)).isoformat(), now=NOW
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert ("reply_tasks", "capacity_paused", 1) in {
        (issue.source, issue.code, issue.count) for issue in report.attention
    }


def test_quality_gate_reports_deferred_minutes_pagination_as_attention(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.set_daily_scan_state(
        "ai_minutes",
        last_success_at=NOW.isoformat(),
        cursor_json='{"pagination_deferred": true}',
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert ("daily_scan_state", "pagination_deferred", 1) in {
        (issue.source, issue.code, issue.count) for issue in report.attention
    }


def test_quality_gate_keeps_scheduled_capability_skip_out_of_scheduler_health(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.record_error(
        "scheduled-task:7",
        "scheduled:7:2026-09-08T12:00:00+00:00",
        "scheduled_task_runtime_unavailable",
        "configured runtime is unavailable; no fallback was used",
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not any(issue.code == "recent_error" for issue in report.violations)
    assert any(
        issue.source == "scheduled_task_runs"
        and issue.code == "scheduled_task_execution_unavailable"
        and issue.count == 1
        for issue in report.attention
    )


def test_quality_gate_only_downgrades_exact_scheduled_capability_errors(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    capability_kinds = (
        "scheduled_task_runtime_unavailable",
        "scheduled_task_managed_skill_unavailable",
        "scheduled_task_operation_skill_unavailable",
        "scheduled_task_execution_unavailable",
    )
    for index, kind in enumerate(capability_kinds):
        store.record_error(
            "scheduled-task:7",
            f"scheduled:7:{index}",
            kind,
            "configured capability is unavailable",
        )
    store.record_error(
        "scheduled-task:7",
        "scheduled:7:database",
        "scheduled_task_database_error",
        "database disk write failed",
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert any(
        issue.code == "scheduled_task_execution_unavailable" and issue.count == 4
        for issue in report.attention
    )
    assert any(
        issue.code == "recent_error" and issue.count == 1
        for issue in report.violations
    )


def test_quality_gate_reports_scheduler_health_observation_that_stopped(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.set_service_health_component(
        "agent-cron-scheduler",
        state="healthy",
        status="running",
        latest_tick_at=(NOW - timedelta(minutes=6)).isoformat(),
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert any(
        issue.source == "service_state"
        and issue.code == "scheduler_tick_stale"
        and issue.count == 1
        for issue in report.violations
    )


def test_quality_gate_accepts_recent_scheduler_health_observation(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.set_service_health_component(
        "agent-cron-scheduler",
        state="healthy",
        status="running",
        latest_tick_at=(NOW - timedelta(minutes=4, seconds=59)).isoformat(),
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not any(
        issue.code == "scheduler_tick_stale" for issue in report.violations
    )

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


def test_quality_gate_keeps_terminal_quarantine_and_discard_out_of_failures(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into meeting_alignment_jobs (meeting_id, status, error)
               values ('meeting', 'quarantined', 'delivery could not be verified')"""
        )
        db.execute(
            """insert into okr_review_requests (
                conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, period_label, period_start,
                period_end, status, error
            ) values (
                'conversation', 'group', 'message', 'sender', 'trigger', '2026 Q3',
                '2026-07-01', '2026-09-30', 'discarded', 'not assigned to principal'
            )"""
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert not [
        item
        for item in report.violations
        if item.source in {"meeting_alignment_jobs", "okr_review_requests"}
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


def test_quality_gate_ignores_dry_run_when_done_task_has_no_unknown_effect(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, status,
                updated_at
            ) values ('dingtalk', 'conversation', 'group', 0, 'message',
                '2026-08-07 00:00:00', 'sender', 'trigger', 'done',
                '2026-08-07 00:59:00')"""
        )
        db.execute(
            """insert into reply_attempts (
                conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind,
                send_status, updated_at
            ) values ('conversation', 'group', 'message', 'sender', 'trigger',
                'agent_run', 'general', 'dry_run', '2026-08-07 00:59:00')"""
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert not [item for item in report.violations if item.code == "recent_dry_run"]


def test_quality_gate_reports_only_current_needs_human_attempts_as_attention(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        for status, message, updated_at in (
            ("needs_human", "current", "2026-08-07 00:30:00"),
            ("needs_human", "recovered", "2026-08-07 00:00:00"),
            ("completed", "recovered", "2026-08-07 00:30:00"),
        ):
            db.execute(
                """insert into reply_attempts (
                    channel, conversation_id, conversation_title, trigger_message_id,
                    trigger_sender, trigger_text, action, sensitivity_kind,
                    codex_reason, send_status, updated_at
                ) values ('dingtalk', 'conversation', 'group', ?, 'sender', 'trigger',
                    'agent_run', 'normal', 'specific action required', ?, ?)""",
                (message, status, updated_at),
            )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not any(item.code == "needs_human" for item in report.attention)
    assert any(item.code == "invalid_needs_human_result" for item in report.violations)


def test_quality_gate_excludes_reviewed_needs_human_attempt(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into reply_attempts (
                channel, conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind,
                codex_reason, send_status, reviewed_at, updated_at
            ) values ('dingtalk', 'conversation', 'group', 'message', 'sender',
                'trigger', 'agent_run', 'normal', 'specific action required',
                'needs_human', '2026-08-07 00:45:00', '2026-08-07 00:30:00')"""
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not [
        item
        for item in report.attention
        if item.source == "reply_attempts" and item.code == "needs_human"
    ]
    assert store.count_current_unresolved_problem_attempts() == 0


def test_quality_gate_reports_needs_human_projection_when_queue_task_is_done(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    _insert_needs_human_projection(store, result=_structured_needs_human_result())

    report = scan_hourly_quality(store.path, now=NOW)

    assert ("reply_attempts", "needs_human", 1) in {
        (item.source, item.code, item.count) for item in report.attention
    }
    # The quality gate intentionally keeps a done-task needs_human projection
    # actionable, while the queue helper excludes a trigger owned by a done
    # current business object from its unresolved-work count.
    assert store.count_current_unresolved_problem_attempts() == 0


def test_quality_gate_accepts_service_generated_confirmation_options(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    attempt_id = _insert_needs_human_projection(
        store,
        result={"outcome": "needs_human"},
    )
    options = _structured_needs_human_result()["decision_options"]
    store.update_reply_attempt(
        attempt_id,
        send_error="confirmation_required",
        human_decision_options_json=json.dumps(options),
    )
    with store._connect() as db:
        db.execute(
            "update agent_runs set final_result_json='' where id=("
            "select agent_run_id from reply_attempts where id=?"
            ")",
            (attempt_id,),
        )
    report = scan_hourly_quality(store.path, now=NOW)

    assert ("reply_attempts", "needs_human", 1) in {
        (item.source, item.code, item.count) for item in report.attention
    }
    assert not any(
        item.code == "invalid_needs_human_result" for item in report.violations
    )


def test_quality_gate_accepts_service_generated_uncertain_unsubscribe_options(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    attempt_id = _insert_needs_human_projection(
        store,
        result={"outcome": "proposal"},
    )
    options = _structured_needs_human_result()["decision_options"]
    store.update_reply_attempt(
        attempt_id,
        send_error="email_unsubscribe_effect_uncertain",
        human_decision_options_json=json.dumps(options),
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert ("reply_attempts", "needs_human", 1) in {
        (item.source, item.code, item.count) for item in report.attention
    }
    assert not any(
        item.code == "invalid_needs_human_result" for item in report.violations
    )


def test_quality_gate_requires_structured_high_risk_low_confidence_options(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    _insert_needs_human_projection(store, result=_structured_needs_human_result())

    report = scan_hourly_quality(store.path, now=NOW)

    assert ("reply_attempts", "needs_human", 1) in {
        (item.source, item.code, item.count) for item in report.attention
    }


def test_quality_gate_hydrates_legacy_missing_coverage_fields(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    result = _structured_needs_human_result()
    result.pop("rule_coverage")
    result.pop("information_completeness")
    _insert_needs_human_projection(store, result=result)

    report = scan_hourly_quality(store.path, now=NOW)

    assert ("reply_attempts", "needs_human", 1) in {
        (item.source, item.code, item.count) for item in report.attention
    }
    assert not any(item.code == "invalid_needs_human_result" for item in report.violations)


def test_quality_gate_does_not_report_low_risk_or_confident_needs_human_projection(tmp_path):
    for name, result in {
        "low-risk": _structured_needs_human_result(risk="low"),
        "confident": _structured_needs_human_result(confidence=0.5),
    }.items():
        store = AutoReplyStore(tmp_path / f"{name}.sqlite3")
        _insert_needs_human_projection(store, result=result)
        report = scan_hourly_quality(store.path, now=NOW)
        assert not any(item.code == "needs_human" for item in report.attention)


def test_quality_gate_low_risk_low_rule_coverage_still_requires_human(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    _insert_needs_human_projection(
        store,
        result=_structured_needs_human_result(risk="low", rule_coverage=0.4),
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert ("reply_attempts", "needs_human", 1) in {
        (item.source, item.code, item.count) for item in report.attention
    }


def test_quality_gate_rejects_needs_human_projection_with_inconsistent_outcome(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    result = _structured_needs_human_result()
    result.pop("outcome")
    _insert_needs_human_projection(store, result=result)

    report = scan_hourly_quality(store.path, now=NOW)

    assert not any(item.code == "needs_human" for item in report.attention)
    assert any(item.code == "invalid_needs_human_result" for item in report.violations)


def test_quality_gate_rejects_autonomous_outcome_even_when_information_is_incomplete(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    result = _structured_needs_human_result(information_completeness=0.4)
    result["outcome"] = "autonomous"
    _insert_needs_human_projection(store, result=result)

    report = scan_hourly_quality(store.path, now=NOW)

    assert not any(item.code == "needs_human" for item in report.attention)
    assert any(item.code == "invalid_needs_human_result" for item in report.violations)


def test_quality_gate_reports_low_rule_coverage_but_incomplete_information_is_ask_back(tmp_path):
    complete = AutoReplyStore(tmp_path / "complete.sqlite3")
    _insert_needs_human_projection(
        complete,
        result=_structured_needs_human_result(rule_coverage=0.4),
    )
    assert any(
        item.code == "needs_human"
        for item in scan_hourly_quality(complete.path, now=NOW).attention
    )

    incomplete = AutoReplyStore(tmp_path / "incomplete.sqlite3")
    _insert_needs_human_projection(
        incomplete,
        result=_structured_needs_human_result(
            rule_coverage=0.1, information_completeness=0.4
        ),
    )
    assert not any(
        item.code == "needs_human"
        for item in scan_hourly_quality(incomplete.path, now=NOW).attention
    )


def test_quality_gate_marks_missing_structured_needs_human_result_invalid(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    _insert_needs_human_projection(store, result={"outcome": "needs_human"})

    report = scan_hourly_quality(store.path, now=NOW)

    assert not any(item.code == "needs_human" for item in report.attention)
    assert any(item.code == "invalid_needs_human_result" for item in report.violations)


def test_quality_gate_does_not_count_failed_projection_as_needs_human(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    _insert_needs_human_projection(
        store,
        result=_structured_needs_human_result(confidence=0.1),
        status="failed",
    )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not any(item.code == "needs_human" for item in report.attention)


def test_quality_gate_excludes_needs_human_from_historical_business_object_task(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        historical_task = db.execute(
            """insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, business_object_key, status
            ) values ('dingtalk', 'work-notice', '工作通知', 0, 'old-message',
                '2026-08-07 00:00:00', 'OA审批', '旧审批通知', 'oa:process:task',
                'done')"""
        )
        current_task = db.execute(
            """insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, business_object_key, status
            ) values ('dingtalk', 'oa-pending-scan', '审批待办', 0, 'current-message',
                '2026-08-07 00:30:00', 'OA审批', '当前审批任务', 'oa:process:task',
                'done')"""
        )
        db.execute(
            """insert into business_object_tasks (business_object_key, reply_task_id)
            values ('oa:process:task', ?)""",
            (current_task.lastrowid,),
        )
        db.execute(
            """insert into reply_attempts (
                channel, conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind,
                codex_reason, send_status, updated_at
            ) values ('dingtalk', 'work-notice', '工作通知', 'old-message',
                'OA审批', '旧审批通知', 'agent_run', 'normal',
                'specific action required', 'needs_human', '2026-08-07 00:30:00')"""
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not [
        item
        for item in report.attention
        if item.source == "reply_attempts" and item.code == "needs_human"
    ]
    assert store.count_current_unresolved_problem_attempts() == 0
    with store._connect() as db:
        historical_attempt = db.execute(
            "select send_status from reply_attempts where trigger_message_id='old-message'"
        ).fetchone()
        assert historical_attempt["send_status"] == "needs_human"
        assert historical_task.lastrowid != current_task.lastrowid


def test_quality_gate_deduplicates_failed_delivery_after_sent_reply_receipt(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into reply_attempts (
                channel, conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind,
                final_reply_text, permission_action, send_status, updated_at
            ) values ('dingtalk', 'conversation', 'group', 'message', 'sender',
                'trigger', 'send_reply', 'normal', 'reply', 'send', 'failed', ?)""",
            ("2026-08-07 00:30:00",),
        )
    store.record_sent_reply("conversation", "message", "reply")

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert not [item for item in report.violations if item.source == "reply_attempts"]


def test_quality_gate_does_not_treat_agent_run_block_as_delivery_failure(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into reply_attempts (
                channel, conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind,
                final_reply_text, permission_action, send_status, updated_at
            ) values ('dingtalk', 'conversation', 'group', 'message', 'sender',
                'trigger', 'agent_run', 'normal', '', '', 'blocked', ?)""",
            ("2026-08-07 00:30:00",),
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert not [item for item in report.violations if item.source == "reply_attempts"]


def test_quality_gate_deduplicates_oa_failure_after_later_success(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        for status, updated_at in (
            ("blocked", "2026-08-07 00:00:00"),
            ("completed", "2026-08-07 00:30:00"),
        ):
            db.execute(
                """insert into reply_attempts (
                    channel, conversation_id, conversation_title, trigger_message_id,
                    trigger_sender, trigger_text, action, sensitivity_kind,
                    oa_process_instance_id, send_status, updated_at
                ) values ('dingtalk', 'conversation', 'group', 'message', 'sender',
                    'trigger', 'oa_approval', 'normal', 'approval-instance', ?, ?)""",
                (status, updated_at),
            )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert not [item for item in report.violations if item.source == "reply_attempts"]


def test_quality_gate_deduplicates_oa_failure_after_agent_receipt_success(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into reply_attempts (
                channel, conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind,
                oa_process_instance_id, send_status, updated_at
            ) values ('dingtalk', 'conversation', 'group', 'message', 'sender',
                'trigger', 'oa_approval', 'normal', 'approval-instance', 'blocked',
                '2026-08-07 01:00:00')"""
        )
        db.execute(
            """insert into reply_attempts (
                channel, conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind,
                oa_process_instance_id, oa_action_result_json, send_status, updated_at
            ) values ('dingtalk', 'conversation', 'group', 'message', 'sender',
                'trigger', 'agent_run', 'normal', 'approval-instance',
                '{"success":true,"taskStatus":"COMPLETED"}',
                'completed', '2026-08-07 00:30:00')"""
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert not [item for item in report.violations if item.source == "reply_attempts"]


def test_quality_gate_counts_only_latest_oa_failure_per_approval_instance(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        for updated_at in ("2026-08-07 00:00:00", "2026-08-07 00:30:00"):
            db.execute(
                """insert into reply_attempts (
                    channel, conversation_id, conversation_title, trigger_message_id,
                    trigger_sender, trigger_text, action, sensitivity_kind,
                    oa_process_instance_id, send_status, updated_at
                ) values ('dingtalk', 'conversation', 'group', 'message', 'sender',
                    'trigger', 'oa_approval', 'normal', 'approval-instance', 'failed', ?)""",
                (updated_at,),
            )

    report = scan_hourly_quality(store.path, now=NOW)

    issue = next(item for item in report.violations if item.source == "reply_attempts")
    assert issue.count == 1


def test_quality_gate_treats_user_rejected_wechat_delivery_as_terminal(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        task_id = db.execute(
            """insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, status
            ) values ('wechat', 'conversation', 'chat', 1, 'message',
                '2026-08-07 00:00:00', 'sender', 'trigger', 'done')"""
        ).lastrowid
    delivery_id = store.create_wechat_delivery(
        reply_task_id=task_id,
        account_id="account",
        target_type="direct",
        target_id="target",
        conversation_id="conversation",
        reply_text="reply",
    )
    store.set_wechat_delivery_status(delivery_id, "failed", error="user_rejected")

    report = scan_hourly_quality(store.path, now=NOW)

    assert report.ok
    assert not [item for item in report.violations if item.source == "wechat_deliveries"]


def test_quality_gate_deduplicates_failed_todo_link_after_later_active_link(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        cursor = db.execute(
            """insert into work_projects (title, status)
               values ('project', 'active')"""
        )
        project_id = cursor.lastrowid
        cursor = db.execute(
            """insert into work_todos (
                project_id, title, owner_user_id, status, deadline_at
            ) values (?, 'follow up', 'owner', 'open', '2026-08-08 12:00:00')""",
            (project_id,),
        )
        todo_id = cursor.lastrowid
        db.execute(
            """insert into work_todo_dingtalk_links (work_todo_id, status, last_error)
               values (?, 'failed', 'transient create error')""",
            (todo_id,),
        )
        db.execute(
            """insert into work_todo_dingtalk_links (
                work_todo_id, dingtalk_task_id, status
            ) values (?, 'external-task', 'active')""",
            (todo_id,),
        )

    report = scan_hourly_quality(store.path, now=NOW)

    assert not [item for item in report.violations if item.source == "work_todo_dingtalk_links"]


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


def test_quality_required_channels_skips_inactive_optional_channel(tmp_path):
    from app.cli import _quality_required_channels

    store = AutoReplyStore(tmp_path / "state.sqlite3")
    with store._connect() as db:
        db.execute(
            """insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, status
            ) values ('lark', 'lark-group', 'Lark', 0, 'msg-lark',
                '2026-08-12 10:00:00', 'Derek', 'check', 'pending')"""
        )

    assert _quality_required_channels(store.path) == {"dingtalk", "lark"}

    with store._connect() as db:
        db.execute("update reply_tasks set status='done' where channel='lark'")

    assert _quality_required_channels(store.path) == {"dingtalk"}


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
