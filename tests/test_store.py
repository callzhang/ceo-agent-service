import errno
import importlib.util
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path
from queue import Queue
from threading import Barrier, Event, Thread

import pytest

import app.store as store_module
from app.store import (
    REPLY_ATTEMPT_CLOSED_AFTER_REVIEW,
    AgentRole,
    AgentRunLeaseLostError,
    AgentRuntimeAttemptStartConflictError,
    AutoReplyStore,
)


def _claim_audit_run(
    store: AutoReplyStore,
    reply_task_id: int,
    execution_generation: str,
    **kwargs,
):
    return store.claim_agent_run(
        reply_task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"direct-agent:{reply_task_id}:{execution_generation}",
        **kwargs,
    )


def _get_audit_run(
    store: AutoReplyStore,
    reply_task_id: int,
    execution_generation: str,
):
    return store.get_agent_run_for_turn(
        reply_task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )


def _enqueue_manual_rerun_in_process(
    db_path: str,
    attempt_id: int,
    barrier,
    results,
) -> None:
    store = AutoReplyStore(Path(db_path))
    barrier.wait(timeout=10)
    task = store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-process-rerun",
        conversation_title="Process rerun",
        single_chat=False,
        trigger_message_id="msg-process-rerun",
        trigger_create_time="2026-07-29 11:00:00",
        trigger_sender="ET",
        trigger_text="请重新处理",
        trigger_message_json="{}",
        attempt_id=attempt_id,
    )
    results.put((task.id, task.execution_generation))


def _enqueue_universal_reply_task(
    store: AutoReplyStore,
    *,
    execution_generation: str = "initial",
) -> int:
    inserted = store.enqueue_reply_task(
        conversation_id="cid-universal",
        conversation_title="Universal",
        single_chat=False,
        trigger_message_id="msg-universal",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="Handle this task",
        execution_generation=execution_generation,
    )
    assert inserted is True
    return store.claim_reply_tasks(limit=1)[0].id


def _claimed_runtime_agent_run(store: AutoReplyStore):
    task_id = _enqueue_universal_reply_task(store)
    return _claim_audit_run(store, task_id, "initial", owner="runtime-attempt").run


def _seed_runtime_operation_parent(
    store: AutoReplyStore, workload_kind: str, workload_key: str
) -> None:
    with store._connect() as db:
        assert db.execute("pragma foreign_keys").fetchone()[0] == 1
        if workload_kind == "structured":
            db.execute("insert into okr_review_requests (id, conversation_id, conversation_title, trigger_message_id, trigger_sender, trigger_text, period_label, period_start, period_end, status) values (?, 'cid', 'title', 'msg', 'sender', 'text', 'period', 'start', 'end', 'processing')", (int(workload_key),))
        elif workload_kind == "meeting":
            db.execute("insert into meeting_alignment_jobs (id, meeting_id) values (1, 'meeting-1')")
            db.execute("insert into meeting_alignment_runs (id, job_id, status) values (?, 1, 'running')", (int(workload_key),))
        elif workload_kind == "task":
            source_id, separator, _ = workload_key.partition(":")
            db.execute("insert into work_summary_inputs (id, source_type, source_ref, payload_json) values (1, 'test', 'task-parent', '{}')")
            if separator:
                db.execute("insert into work_projects (id, title, category, status, priority, risk_level) values (?, ?, 'other', 'active', 'none', 'none')", (int(source_id), f"project-{source_id}"))
            else:
                db.execute("update work_summary_inputs set status='processing' where id=1")
                db.execute("insert into task_agent_runs (id, summary_input_id, status) values (?, 1, 'running')", (int(source_id),))
        elif workload_kind == "weekly_okr":
            week_end, manager_user_id, source_digest = workload_key.split(":", 2)
            db.execute(
                "insert into weekly_okr_analysis_jobs "
                "(week_end, manager_user_id, source_digest, status) "
                "values (?, ?, ?, 'running')",
                (week_end, manager_user_id, source_digest),
            )
        elif workload_kind == "memory":
            source, _, source_id = workload_key.partition(":")
            table_name = {
                "memory_write_event": "memory_write_events",
                "wechat_memory_candidate": "wechat_memory_candidates",
                "wechat_memory_import_job": "wechat_memory_import_jobs",
            }[source]
            if table_name == "memory_write_events":
                db.execute("insert into reply_attempts (id, conversation_id, conversation_title, trigger_message_id, trigger_sender, trigger_text, action, sensitivity_kind, final_reply_text, permission_action, permission_reason, send_status) values (1, 'cid', 'title', 'msg', 'sender', 'text', 'none', 'none', '', 'none', '', 'pending')")
                db.execute("insert into memory_write_events (id, attempt_id, event_type, payload_json) values (?, 1, 'test', '{}')", (int(source_id),))
            elif table_name == "wechat_memory_candidates":
                db.execute("insert into wechat_memory_candidates (id, import_run_id, account_id, statement, category, confidence, sensitivity, status, memory_write_status) values (?, 'import', 'account', 'statement', 'fact', 1, 'low', 'approved', 'writing')", (int(source_id),))
            else:
                db.execute(
                    "insert into wechat_memory_import_jobs "
                    "(id, import_run_id, account_id, status) "
                    "values (?, 'import', 'account', 'running')",
                    (int(source_id),),
                )


def test_runtime_attempt_claim_is_ordered_and_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)

    first = store.claim_agent_runtime_attempt(
        run.id,
        route_name="codex_oauth",
        runtime_kind="codex_cli",
        credential_mode="local_oauth",
        model="gpt-5.5",
    )
    repeated = store.claim_agent_runtime_attempt(
        run.id,
        route_name="codex_oauth",
        runtime_kind="codex_cli",
        credential_mode="local_oauth",
        model="gpt-5.5",
    )

    assert first.attempt_number == 1
    assert repeated.id == first.id


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
def test_runtime_attempt_claim_requires_running_agent_parent(
    tmp_path: Path, terminal_status: str
):
    store = AutoReplyStore(tmp_path / "runtime-parent.sqlite3")
    run = _claimed_runtime_agent_run(store)
    if terminal_status == "completed":
        store.complete_agent_run(run.id, {"outcome": "done"}, owner="runtime-attempt")
    else:
        store.fail_agent_run(
            run.id, {"code": "terminal"}, owner="runtime-attempt"
        )

    with pytest.raises(ValueError, match="does not exist or is not running"):
        store.claim_agent_runtime_attempt(
            run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )

    assert store.list_agent_runtime_attempts(run.id) == []


def test_runtime_attempt_claim_rejects_missing_agent_parent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-parent.sqlite3")

    with pytest.raises(ValueError, match="does not exist or is not running"):
        store.claim_agent_runtime_attempt(
            999, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )

    with store._connect() as db:
        assert db.execute("select count(*) from agent_runtime_attempts").fetchone()[0] == 0


def _runtime_effect_started_event(operation_id: str) -> dict[str, object]:
    return {
        "type": "item.started",
        "item": {
            "type": "mcp_tool_call",
            "id": "write-1",
            "status": "in_progress",
            "metadata": {
                "effect": "effectful",
                "operation_id": operation_id,
                "capability": "agent_cli.dws",
                "operation": "chat message send",
                "operation_digest": "command-digest",
                "target_identifiers": {"group": "cid-universal"},
            },
        },
    }


def test_unknown_recovery_attempt_requires_owned_persisted_effect_evidence(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "runtime-recovery-parent.sqlite3")
    run = _claimed_runtime_agent_run(store)
    store.append_agent_run_event(
        run.id,
        _runtime_effect_started_event(run.operation_id),
        owner="runtime-attempt",
    )
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": True},
        owner="runtime-attempt",
    )
    assert store.claim_unknown_agent_run(run.id, owner="reconciler").claimed

    with pytest.raises(ValueError, match="not safely claimed"):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "claude_api",
            "claude_cli",
            "service_api",
            "claude-sonnet-4-5",
            owner="foreign-owner",
        )
    with pytest.raises(ValueError, match="does not exist or is not running"):
        store.claim_agent_runtime_attempt(
            run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )

    with store._connect() as db:
        db.execute("update reply_tasks set status='failed' where id=?", (run.reply_task_id,))
        db.commit()
    with pytest.raises(ValueError, match="not safely claimed"):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            owner="reconciler",
        )
    assert store.list_agent_runtime_attempts(run.id) == []
    with store._connect() as db:
        db.execute(
            "update reply_tasks set status='processing' where id=?",
            (run.reply_task_id,),
        )
        db.commit()

    start_claim = store.claim_unknown_recovery_agent_runtime_attempt(
        run.id,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.5",
        owner="reconciler",
    )
    recovery = start_claim.attempt

    assert start_claim.start_acquired is True
    assert recovery.session_mode == "fresh"
    assert recovery.source_session_id == ""
    assert recovery.status == "running"
    with pytest.raises(AgentRuntimeAttemptStartConflictError):
        store.mark_agent_runtime_attempt_running_once(recovery.id)
    with pytest.raises(AgentRuntimeAttemptStartConflictError):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            owner="reconciler",
        )


def test_unknown_recovery_attempt_rejects_unknown_run_without_effect(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-recovery-no-effect.sqlite3")
    run = _claimed_runtime_agent_run(store)
    store.mark_agent_run_unknown(
        run.id,
        {"code": "unknown_without_effect", "retryable": True},
        owner="runtime-attempt",
    )
    assert store.claim_unknown_agent_run(run.id, owner="reconciler").claimed

    with pytest.raises(ValueError, match="not safely claimed"):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            owner="reconciler",
        )

    assert store.list_agent_runtime_attempts(run.id) == []


@pytest.mark.parametrize("active_status", ["starting", "running"])
def test_unknown_recovery_never_takes_over_an_active_ordinary_attempt(
    tmp_path: Path, active_status: str
):
    store = AutoReplyStore(tmp_path / f"runtime-recovery-{active_status}.sqlite3")
    run = _claimed_runtime_agent_run(store)
    ordinary = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    if active_status == "running":
        ordinary = store.mark_agent_runtime_attempt_running_once(ordinary.id)
    store.append_agent_run_event(
        run.id,
        _runtime_effect_started_event(run.operation_id),
        owner="runtime-attempt",
    )
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": True},
        owner="runtime-attempt",
    )
    assert store.claim_unknown_agent_run(run.id, owner="reconciler").claimed

    with pytest.raises(AgentRuntimeAttemptStartConflictError):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            owner="reconciler",
        )
    with pytest.raises(AgentRuntimeAttemptStartConflictError):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "claude_api",
            "claude_cli",
            "service_api",
            "claude-sonnet-4-5",
            owner="reconciler",
        )

    [persisted] = store.list_agent_runtime_attempts(run.id)
    assert persisted.id == ordinary.id
    assert persisted.status == active_status


def test_concurrent_unknown_recovery_claims_grant_exactly_one_start(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-recovery-concurrent.sqlite3")
    run = _claimed_runtime_agent_run(store)
    store.append_agent_run_event(
        run.id,
        _runtime_effect_started_event(run.operation_id),
        owner="runtime-attempt",
    )
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": True},
        owner="runtime-attempt",
    )
    assert store.claim_unknown_agent_run(run.id, owner="reconciler").claimed
    barrier = Barrier(2)
    starts: list[int] = []
    conflicts: list[str] = []

    def claim_and_spawn() -> None:
        barrier.wait(timeout=10)
        try:
            claim = store.claim_unknown_recovery_agent_runtime_attempt(
                run.id,
                "codex_oauth",
                "codex_cli",
                "local_oauth",
                "gpt-5.5",
                owner="reconciler",
            )
        except AgentRuntimeAttemptStartConflictError as exc:
            conflicts.append(str(exc))
        else:
            if claim.start_acquired:
                starts.append(claim.attempt.id)

    threads = [Thread(target=claim_and_spawn) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert len(starts) == 1
    assert len(conflicts) == 1
    [attempt] = store.list_agent_runtime_attempts(run.id)
    assert attempt.id == starts[0]
    assert attempt.status == "running"


def test_expired_effect_free_recovery_lease_is_reclaimed_once(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-recovery-expired.sqlite3")
    run = _claimed_runtime_agent_run(store)
    store.append_agent_run_event(
        run.id,
        _runtime_effect_started_event(run.operation_id),
        owner="runtime-attempt",
    )
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": True},
        owner="runtime-attempt",
    )
    started_at = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)
    assert store.claim_unknown_agent_run(
        run.id, owner="reconciler", lease_seconds=1800, now=started_at
    ).claimed
    first = store.claim_unknown_recovery_agent_runtime_attempt(
        run.id,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.5",
        owner="reconciler",
        lease_seconds=60,
        now=started_at,
    ).attempt

    with pytest.raises(AgentRuntimeAttemptStartConflictError):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            owner="reconciler",
            lease_seconds=60,
            now=started_at + timedelta(seconds=59),
        )
    with pytest.raises(ValueError, match="not safely claimed"):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            owner="foreign-owner",
            lease_seconds=60,
            now=started_at + timedelta(seconds=61),
        )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set execution_generation='foreign-generation' "
            "where id=?",
            (run.reply_task_id,),
        )
        db.commit()
    with pytest.raises(ValueError, match="not safely claimed"):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            owner="reconciler",
            lease_seconds=60,
            now=started_at + timedelta(seconds=61),
        )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set execution_generation=? where id=?",
            (run.execution_generation, run.reply_task_id),
        )
        db.commit()

    reclaimed = store.claim_unknown_recovery_agent_runtime_attempt(
        run.id,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.5",
        owner="reconciler",
        lease_seconds=60,
        now=started_at + timedelta(seconds=61),
    )

    attempts = store.list_agent_runtime_attempts(run.id)
    assert [attempt.status for attempt in attempts] == ["failed", "running"]
    assert attempts[0].id == first.id
    assert attempts[0].failure_code == "runtime_recovery_lease_expired"
    assert attempts[0].failover_permitted is False
    assert reclaimed.start_acquired is True
    assert reclaimed.attempt.id == attempts[1].id
    assert reclaimed.attempt.lease_owner == "reconciler"

    barrier = Barrier(2)
    race_starts: list[int] = []
    race_conflicts: list[str] = []

    def reclaim_after_second_expiry() -> None:
        barrier.wait(timeout=10)
        try:
            claim = store.claim_unknown_recovery_agent_runtime_attempt(
                run.id,
                "codex_oauth",
                "codex_cli",
                "local_oauth",
                "gpt-5.5",
                owner="reconciler",
                lease_seconds=60,
                now=started_at + timedelta(seconds=122),
            )
        except AgentRuntimeAttemptStartConflictError as exc:
            race_conflicts.append(str(exc))
        else:
            race_starts.append(claim.attempt.id)

    threads = [Thread(target=reclaim_after_second_expiry) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert len(race_starts) == 1
    assert len(race_conflicts) == 1
    attempts = store.list_agent_runtime_attempts(run.id)
    assert [attempt.status for attempt in attempts] == ["failed", "failed", "running"]
    assert attempts[-1].id == race_starts[0]


def test_expired_recovery_with_effect_evidence_is_never_replayed(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-recovery-effect.sqlite3")
    run = _claimed_runtime_agent_run(store)
    store.append_agent_run_event(
        run.id,
        _runtime_effect_started_event(run.operation_id),
        owner="runtime-attempt",
    )
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": True},
        owner="runtime-attempt",
    )
    started_at = datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc)
    assert store.claim_unknown_agent_run(
        run.id, owner="reconciler", lease_seconds=1800, now=started_at
    ).claimed
    attempt = store.claim_unknown_recovery_agent_runtime_attempt(
        run.id,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.5",
        owner="reconciler",
        lease_seconds=60,
        now=started_at,
    ).attempt
    store.note_runtime_attempt_effect_started(
        attempt.id, at=started_at + timedelta(seconds=1)
    )

    with pytest.raises(AgentRuntimeAttemptStartConflictError):
        store.claim_unknown_recovery_agent_runtime_attempt(
            run.id,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            owner="reconciler",
            lease_seconds=60,
            now=started_at + timedelta(seconds=61),
        )

    [persisted] = store.list_agent_runtime_attempts(run.id)
    assert persisted.id == attempt.id
    assert persisted.status == "running"
    assert persisted.first_effect_started_at


def test_agent_runtime_attempt_claim_atomically_rechecks_route_pause(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-parent.sqlite3")
    run = _claimed_runtime_agent_run(store)
    store.open_runtime_route_pause(
        "codex_oauth",
        "codex_login_required",
        retry_at="2099-01-01T00:00:00+00:00",
    )

    with pytest.raises(Exception, match="runtime route is paused") as raised:
        store.claim_agent_runtime_attempt(
            run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )

    assert type(raised.value).__name__ == "RuntimeRoutePausedError"
    assert store.list_agent_runtime_attempts(run.id) == []


def test_runtime_attempt_claim_numbers_follow_terminal_attempts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    first = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    store.fail_agent_runtime_attempt(
        first.id,
        failure_class="authentication",
        failure_code="codex_login_required",
        failover_permitted=True,
    )
    second = store.claim_agent_runtime_attempt(
        run.id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )

    assert second.attempt_number == 2
    assert [attempt.id for attempt in store.list_agent_runtime_attempts(run.id)] == [
        first.id,
        second.id,
    ]
    assert store.mark_agent_runtime_attempt_superseded(first.id).status == "superseded"


def test_runtime_attempt_session_evidence_is_persisted_and_validated(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    fresh = store.claim_agent_runtime_attempt(
        run.id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )

    assert fresh.session_mode == "fresh"
    assert fresh.source_session_id == ""

    store.fail_agent_runtime_attempt(
        fresh.id, "session", "session_route_incompatible", True
    )
    resumed = store.claim_agent_runtime_attempt(
        run.id,
        "codex_api",
        "codex_cli",
        "service_api",
        "gpt-5.5",
        session_mode="resume",
        source_session_id="codex-session-1",
    )

    assert resumed.session_mode == "resume"
    assert resumed.source_session_id == "codex-session-1"
    assert store.get_agent_runtime_attempt(resumed.id) == resumed

    with pytest.raises(ValueError, match="fresh session evidence"):
        store.claim_agent_runtime_attempt(
            run.id,
            "other",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            session_mode="fresh",
            source_session_id="must-be-empty",
        )
    with pytest.raises(ValueError, match="resume session evidence"):
        store.claim_agent_runtime_attempt(
            run.id,
            "other",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            session_mode="resume",
        )
    with pytest.raises(TypeError, match="source_session_id must be a string"):
        store.claim_agent_runtime_attempt(
            run.id,
            "other",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            source_session_id=1,
        )
    with store._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            insert into agent_runtime_attempts (
                agent_run_id, workload_kind, workload_key, attempt_number,
                route_name, runtime_kind, credential_mode, model, session_mode,
                source_session_id, status, started_at, created_at, updated_at
            ) values (?, 'agent_run', ?, 99, 'direct', 'codex_cli',
                      'local_oauth', 'gpt-5.5', 'resume', '   ', 'starting',
                      '2026-08-20 10:00:00', '2026-08-20 10:00:00',
                      '2026-08-20 10:00:00')
            """,
            (run.id, str(run.id)),
        )


def test_runtime_attempt_active_claim_compares_session_evidence(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    store.claim_agent_runtime_attempt(
        run.id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )

    with pytest.raises(ValueError, match="conflicting active runtime attempt claim"):
        store.claim_agent_runtime_attempt(
            run.id,
            "codex_api",
            "codex_cli",
            "service_api",
            "gpt-5.5",
            session_mode="resume",
            source_session_id="codex-session-1",
        )


def test_runtime_attempt_upgrade_adds_session_evidence_constraints(tmp_path: Path):
    db_path = tmp_path / "runtime-attempt-upgrade.sqlite3"
    store = AutoReplyStore(db_path)
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    with store._connect() as db:
        db.execute("drop trigger if exists trg_runtime_attempt_session_evidence_insert")
        db.execute("drop trigger if exists trg_runtime_attempt_session_evidence_update")
        db.execute(
            "alter table agent_runtime_attempts rename to legacy_runtime_attempts"
        )
        db.execute(
            """
            create table agent_runtime_attempts (
                id integer primary key autoincrement,
                agent_run_id integer,
                workload_kind text not null,
                workload_key text not null,
                attempt_number integer not null,
                route_name text not null,
                runtime_kind text not null,
                credential_mode text not null,
                model text not null,
                session_id text not null default '',
                status text not null,
                failure_class text not null default '',
                failure_code text not null default '',
                failover_permitted integer not null default 0,
                transcript_reference text not null default '',
                transcript_start integer not null default 0,
                transcript_end integer not null default 0,
                first_effect_started_at text not null default '',
                started_at text not null,
                finished_at text not null default '',
                created_at text not null,
                updated_at text not null
            )
            """
        )
        db.execute(
            """
            insert into agent_runtime_attempts (
                id, agent_run_id, workload_kind, workload_key, attempt_number,
                route_name, runtime_kind, credential_mode, model, session_id,
                status, failure_class, failure_code, failover_permitted,
                transcript_reference, transcript_start, transcript_end,
                first_effect_started_at, started_at, finished_at, created_at, updated_at
            )
            select id, agent_run_id, workload_kind, workload_key, attempt_number,
                route_name, runtime_kind, credential_mode, model, session_id,
                status, failure_class, failure_code, failover_permitted,
                transcript_reference, transcript_start, transcript_end,
                first_effect_started_at, started_at, finished_at, created_at, updated_at
            from legacy_runtime_attempts
            """
        )
        db.execute("drop table legacy_runtime_attempts")
    assert store._schema_is_current() is False
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    upgraded = AutoReplyStore(db_path)
    restored = upgraded.get_agent_runtime_attempt(attempt.id)

    assert restored.session_mode == "fresh"
    assert restored.source_session_id == ""
    with upgraded._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "update agent_runtime_attempts set session_mode='resume', "
            "source_session_id='   ' where id=?",
            (attempt.id,),
        )


def test_runtime_attempt_upgrade_replaces_pretrim_session_evidence_triggers(
    tmp_path: Path,
):
    db_path = tmp_path / "runtime-attempt-pretrim-trigger-upgrade.sqlite3"
    store = AutoReplyStore(db_path)
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    with store._connect() as db:
        db.execute(
            "drop trigger if exists trg_runtime_attempt_session_evidence_trim_insert"
        )
        db.execute(
            "drop trigger if exists trg_runtime_attempt_session_evidence_trim_update"
        )
        db.execute(
            "alter table agent_runtime_attempts rename to legacy_runtime_attempts"
        )
        db.execute(
            """
            create table agent_runtime_attempts (
                id integer primary key autoincrement,
                agent_run_id integer,
                workload_kind text not null,
                workload_key text not null,
                attempt_number integer not null,
                route_name text not null,
                runtime_kind text not null,
                credential_mode text not null,
                model text not null,
                session_mode text not null default 'fresh'
                    check(session_mode in ('fresh', 'resume')),
                source_session_id text not null default '',
                session_id text not null default '',
                status text not null,
                failure_class text not null default '',
                failure_code text not null default '',
                failover_permitted integer not null default 0,
                transcript_reference text not null default '',
                transcript_start integer not null default 0,
                transcript_end integer not null default 0,
                first_effect_started_at text not null default '',
                started_at text not null,
                finished_at text not null default '',
                created_at text not null,
                updated_at text not null,
                check(
                    (session_mode='fresh' and source_session_id='')
                    or (session_mode='resume' and source_session_id<>'')
                ),
                unique(agent_run_id, attempt_number),
                foreign key(agent_run_id) references agent_runs(id)
            )
            """
        )
        db.execute(
            """
            insert into agent_runtime_attempts (
                id, agent_run_id, workload_kind, workload_key, attempt_number,
                route_name, runtime_kind, credential_mode, model, session_mode,
                source_session_id, session_id, status, failure_class, failure_code,
                failover_permitted, transcript_reference, transcript_start,
                transcript_end, first_effect_started_at, started_at, finished_at,
                created_at, updated_at
            )
            select id, agent_run_id, workload_kind, workload_key, attempt_number,
                route_name, runtime_kind, credential_mode, model, session_mode,
                source_session_id, session_id, status, failure_class, failure_code,
                failover_permitted, transcript_reference, transcript_start,
                transcript_end, first_effect_started_at, started_at, finished_at,
                created_at, updated_at
            from legacy_runtime_attempts
            """
        )
        db.execute("drop table legacy_runtime_attempts")
        db.execute(
            "create index idx_runtime_attempt_active_route "
            "on agent_runtime_attempts(agent_run_id, route_name) "
            "where status in ('starting', 'running')"
        )
        db.execute(
            """
            create trigger trg_runtime_attempt_session_evidence_insert
            before insert on agent_runtime_attempts
            when new.session_mode is null or new.source_session_id is null or not (
                (new.session_mode='fresh' and new.source_session_id='')
                or (new.session_mode='resume' and new.source_session_id<>'')
            )
            begin
                select raise(abort, 'invalid runtime attempt session evidence');
            end
            """
        )
        db.execute(
            """
            create trigger trg_runtime_attempt_session_evidence_update
            before update of session_mode, source_session_id on agent_runtime_attempts
            when new.session_mode is null or new.source_session_id is null or not (
                (new.session_mode='fresh' and new.source_session_id='')
                or (new.session_mode='resume' and new.source_session_id<>'')
            )
            begin
                select raise(abort, 'invalid runtime attempt session evidence');
            end
            """
        )
        trigger_names = {
            str(row["name"])
            for row in db.execute(
                "select name from sqlite_master where type='trigger'"
            ).fetchall()
        }
        assert {
            "trg_runtime_attempt_session_evidence_insert",
            "trg_runtime_attempt_session_evidence_update",
        } <= trigger_names
        db.execute(
            "update service_state set value=? where key=?",
            ("2026-08-20.1", store_module.STORE_SCHEMA_VERSION_KEY),
        )
        db.execute(
            "update agent_runtime_attempts set session_mode='resume', "
            "source_session_id='   ' where id=?",
            (attempt.id,),
        )
    assert store._schema_is_current() is False
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    upgraded = AutoReplyStore(db_path)

    with upgraded._connect() as db:
        trigger_sql = {
            str(row["name"]): str(row["sql"])
            for row in db.execute(
                "select name, sql from sqlite_master where type='trigger'"
            ).fetchall()
        }
        assert "trg_runtime_attempt_session_evidence_insert" not in trigger_sql
        assert "trg_runtime_attempt_session_evidence_update" not in trigger_sql
        assert "trim(new.source_session_id)<>''" in trigger_sql[
            "trg_runtime_attempt_session_evidence_trim_insert"
        ]
        assert "trim(new.source_session_id)<>''" in trigger_sql[
            "trg_runtime_attempt_session_evidence_trim_update"
        ]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "update agent_runtime_attempts set session_mode='resume', "
                "source_session_id='   ' where id=?",
                (attempt.id,),
            )


@pytest.mark.parametrize(
    ("workload_kind", "workload_key"),
    [
        ("structured", "12"),
        ("meeting", "13"),
        ("task", "14"),
        ("task", "15:memory_backfill"),
        ("weekly_okr", "2026-08-16:manager-1:" + "a" * 64),
        ("memory", "memory_write_event:16"),
        ("memory", "wechat_memory_import_job:17"),
    ],
)
def test_runtime_attempt_operation_accepts_approved_stable_workload_keys(
    tmp_path: Path,
    workload_kind: str,
    workload_key: str,
):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    _seed_runtime_operation_parent(store, workload_kind, workload_key)

    attempt = store.claim_runtime_operation_attempt(
        workload_kind,
        workload_key,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.5",
    )

    assert attempt.workload_kind == workload_kind
    assert attempt.workload_key == workload_key
    assert attempt.agent_run_id is None


def test_runtime_operation_attempts_are_listed_only_for_exact_workload(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-operation-list.sqlite3")
    _seed_runtime_operation_parent(store, "structured", "12")
    _seed_runtime_operation_parent(store, "memory", "memory_write_event:13")
    first = store.claim_runtime_operation_attempt(
        "structured", "12", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    store.mark_agent_runtime_attempt_running_once(first.id)
    store.fail_agent_runtime_attempt(
        first.id, "authentication", "codex_login_required", True
    )
    second = store.claim_runtime_operation_attempt(
        "structured", "12", "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )
    store.claim_runtime_operation_attempt(
        "memory", "memory_write_event:13", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    attempts = store.list_runtime_operation_attempts("structured", "12")

    assert [attempt.id for attempt in attempts] == [first.id, second.id]
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]


@pytest.mark.parametrize(
    ("workload_kind", "workload_key"),
    [
        ("agent_run", "1"),
        ("unknown", "1"),
        ("structured", "request-12"),
        ("meeting", "meeting-13"),
        ("task", "not-a-persisted-id"),
        ("structured", "999"),
        ("weekly_okr", "not-a-stable-key"),
        ("memory", "memory-16"),
        ("memory", "memory_write_event:999"),
        ("memory", "wechat_memory_import_job:999"),
    ],
)
def test_runtime_attempt_operation_rejects_unapproved_or_freeform_workload_keys(
    tmp_path: Path,
    workload_kind: str,
    workload_key: str,
):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")

    with pytest.raises(ValueError):
        store.claim_runtime_operation_attempt(
            workload_kind,
            workload_key,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
        )


def test_runtime_attempt_memory_keys_are_source_qualified_and_collision_free(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    _seed_runtime_operation_parent(store, "memory", "memory_write_event:1")
    _seed_runtime_operation_parent(store, "memory", "wechat_memory_candidate:1")
    _seed_runtime_operation_parent(store, "memory", "wechat_memory_import_job:1")

    event_attempt = store.claim_runtime_operation_attempt(
        "memory", "memory_write_event:1", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    candidate_attempt = store.claim_runtime_operation_attempt(
        "memory", "wechat_memory_candidate:1", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    import_attempt = store.claim_runtime_operation_attempt(
        "memory", "wechat_memory_import_job:1", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    assert len({
        event_attempt.workload_key,
        candidate_attempt.workload_key,
        import_attempt.workload_key,
    }) == 3
    assert {
        event_attempt.attempt_number,
        candidate_attempt.attempt_number,
        import_attempt.attempt_number,
    } == {1}


def test_runtime_attempt_requires_parent_to_be_in_runnable_state(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-parent-state.sqlite3")
    _seed_runtime_operation_parent(store, "meeting", "13")
    with store._connect() as db:
        db.execute("update meeting_alignment_runs set status='failed' where id=13")

    with pytest.raises(ValueError, match="parent does not exist or is not running"):
        store.claim_runtime_operation_attempt(
            "meeting", "13", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )


def test_runtime_attempt_correction_lineage_is_sealed_and_immutable(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-correction-lineage.sqlite3")
    _seed_runtime_operation_parent(store, "structured", "91")

    normal = store.claim_runtime_operation_attempt(
        "structured", "91", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    assert (
        normal.attempt_purpose,
        normal.validation_retry_policy_id,
        normal.validation_result_schema_id,
    ) == ("normal", "", "")
    store.fail_agent_runtime_attempt(
        normal.id,
        "result",
        "runtime_result_validation_failed",
        False,
    )
    correction = store.claim_runtime_operation_attempt(
        "structured",
        "91",
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.5",
        attempt_purpose="result_validation_correction",
        validation_retry_policy_id="result_validation_retry.v1:test",
        validation_result_schema_id="test.integer.v1",
    )
    assert correction.attempt_purpose == "result_validation_correction"

    with store._connect() as db, pytest.raises(
        sqlite3.IntegrityError, match="lineage is immutable"
    ):
        db.execute(
            "update agent_runtime_attempts set attempt_purpose='normal', "
            "validation_retry_policy_id='', validation_result_schema_id='' "
            "where id=?",
            (correction.id,),
        )
    with pytest.raises(ValueError, match="cannot carry correction lineage"):
        store.claim_runtime_operation_attempt(
            "structured",
            "91",
            "codex_api",
            "codex_cli",
            "service_api",
            "gpt-5.5",
            validation_retry_policy_id="forged",
        )


def test_task_memory_backfill_parent_is_work_project_not_summary_input(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task-memory-parent.sqlite3")
    with store._connect() as db:
        db.execute(
            "insert into work_summary_inputs "
            "(id, source_type, source_ref, payload_json) values (41, 'test', 'ref', '{}')"
        )

    with pytest.raises(ValueError, match="parent does not exist"):
        store.claim_runtime_operation_attempt(
            "task", "41:memory_backfill", "codex_oauth", "codex_cli",
            "local_oauth", "gpt-5.5",
        )


def test_weekly_okr_parent_matches_the_complete_natural_key(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "weekly-parent.sqlite3")
    digest = "a" * 64
    claim = store.begin_weekly_okr_analysis_job(
        week_end="2026-08-16", manager_user_id="manager-1", source_digest=digest
    )
    assert claim.outcome == "claimed"

    attempt = store.claim_runtime_operation_attempt(
        "weekly_okr", f"2026-08-16:manager-1:{digest}", "codex_oauth",
        "codex_cli", "local_oauth", "gpt-5.5",
    )
    assert attempt.workload_key.endswith(digest)

    store.finish_weekly_okr_analysis_job(claim.job_id, status="completed")
    with pytest.raises(ValueError, match="parent does not exist or is not running"):
        store.claim_runtime_operation_attempt(
            "weekly_okr", f"2026-08-16:manager-1:{digest}", "codex_oauth",
            "codex_cli", "local_oauth", "gpt-5.5",
        )


def test_weekly_okr_failed_job_reopens_and_completed_job_is_cache_hit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "weekly-reopen.sqlite3")
    values = {
        "week_end": "2026-08-16",
        "manager_user_id": "manager-1",
        "source_digest": "b" * 64,
    }
    first = store.begin_weekly_okr_analysis_job(**values)
    store.finish_weekly_okr_analysis_job(
        first.job_id, status="failed", error="provider unavailable"
    )

    reopened = store.begin_weekly_okr_analysis_job(**values)
    assert reopened.job_id == first.job_id
    assert reopened.outcome == "claimed"
    with store._connect() as db:
        row = db.execute(
            "select * from weekly_okr_analysis_jobs where id=?",
            (reopened.job_id,),
        ).fetchone()
    assert row["status"] == "running"
    assert row["error"] == ""
    assert row["finished_at"] == ""

    store.finish_weekly_okr_analysis_job(reopened.job_id, status="completed")
    cache_hit = store.begin_weekly_okr_analysis_job(**values)
    assert cache_hit.job_id == first.job_id
    assert cache_hit.outcome == "cache_hit"


def test_weekly_okr_manager_identity_is_canonicalized(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "weekly-manager-canonical.sqlite3")
    digest = "c" * 64
    claim = store.begin_weekly_okr_analysis_job(
        week_end="2026-08-16",
        manager_user_id="  manager-1  ",
        source_digest=digest,
    )
    with store._connect() as db:
        row = db.execute(
            "select manager_user_id from weekly_okr_analysis_jobs where id=?",
            (claim.job_id,),
        ).fetchone()
    assert row["manager_user_id"] == "manager-1"
    with pytest.raises(ValueError, match="canonical"):
        store.claim_runtime_operation_attempt(
            "weekly_okr", f"2026-08-16: manager-1 :{digest}", "codex_oauth",
            "codex_cli", "local_oauth", "gpt-5.5",
        )


def test_weekly_okr_failed_job_reopen_is_concurrency_safe(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "weekly-reopen-concurrent.sqlite3")
    values = {
        "week_end": "2026-08-16",
        "manager_user_id": "manager-1",
        "source_digest": "d" * 64,
    }
    original = store.begin_weekly_okr_analysis_job(**values)
    store.finish_weekly_okr_analysis_job(
        original.job_id, status="failed", error="retry"
    )
    barrier = Barrier(3)
    claims = []

    def reopen() -> None:
        barrier.wait(timeout=5)
        claims.append(store.begin_weekly_okr_analysis_job(**values))

    threads = [Thread(target=reopen) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert len(claims) == 2
    assert {claim.job_id for claim in claims} == {original.job_id}
    assert sorted(claim.outcome for claim in claims) == ["claimed", "in_progress"]


def test_weekly_okr_completed_cache_miss_reclaims_same_job(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "weekly-cache-miss.sqlite3")
    values = {
        "week_end": "2026-08-16",
        "manager_user_id": "manager-1",
        "source_digest": "e" * 64,
    }
    original = store.begin_weekly_okr_analysis_job(**values)
    store.finish_weekly_okr_analysis_job(original.job_id, status="completed")
    assert store.begin_weekly_okr_analysis_job(**values).outcome == "cache_hit"

    reclaimed = store.reclaim_weekly_okr_analysis_job_cache_miss(
        original.job_id, **values
    )

    assert reclaimed.job_id == original.job_id
    assert reclaimed.outcome == "claimed"
    with store._connect() as db:
        row = db.execute(
            "select status, error, finished_at from weekly_okr_analysis_jobs "
            "where id=?",
            (original.job_id,),
        ).fetchone()
    assert tuple(row) == ("running", "", "")


def test_weekly_okr_completed_cache_miss_reclaim_is_concurrency_safe(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "weekly-cache-miss-concurrent.sqlite3")
    values = {
        "week_end": "2026-08-16",
        "manager_user_id": "manager-1",
        "source_digest": "f" * 64,
    }
    original = store.begin_weekly_okr_analysis_job(**values)
    store.finish_weekly_okr_analysis_job(original.job_id, status="completed")
    barrier = Barrier(3)
    claims = []

    def reclaim() -> None:
        barrier.wait(timeout=5)
        claims.append(
            store.reclaim_weekly_okr_analysis_job_cache_miss(
                original.job_id, **values
            )
        )

    threads = [Thread(target=reclaim) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert len(claims) == 2
    assert {claim.job_id for claim in claims} == {original.job_id}
    assert sorted(claim.outcome for claim in claims) == ["claimed", "in_progress"]


def test_weekly_okr_live_lease_blocks_and_expired_owner_is_reclaimed(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "weekly-lease.sqlite3")
    values = {
        "week_end": "2026-08-16",
        "manager_user_id": "manager-1",
        "source_digest": "9" * 64,
    }
    now = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
    first = store.begin_weekly_okr_analysis_job(
        **values, owner="owner-1", lease_seconds=5, now=now
    )
    blocked = store.begin_weekly_okr_analysis_job(
        **values,
        owner="owner-2",
        lease_seconds=5,
        now=now + timedelta(seconds=4),
    )
    reclaimed = store.begin_weekly_okr_analysis_job(
        **values,
        owner="owner-2",
        lease_seconds=5,
        now=now + timedelta(seconds=6),
    )

    assert blocked.outcome == "in_progress"
    assert reclaimed.job_id == first.job_id
    assert reclaimed.outcome == "claimed"
    assert reclaimed.reclaimed_stale is True
    with pytest.raises(ValueError, match="lease ownership lost"):
        store.finish_weekly_okr_analysis_job(
            first.job_id,
            status="failed",
            owner="owner-1",
            now=now + timedelta(seconds=6),
        )
    store.finish_weekly_okr_analysis_job(
        first.job_id,
        status="completed",
        owner="owner-2",
        now=now + timedelta(seconds=7),
    )


def test_weekly_okr_cache_miss_reclaim_wrong_key_or_status_fails_closed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "weekly-cache-miss-guard.sqlite3")
    values = {
        "week_end": "2026-08-16",
        "manager_user_id": "manager-1",
        "source_digest": "1" * 64,
    }
    original = store.begin_weekly_okr_analysis_job(**values)
    store.finish_weekly_okr_analysis_job(original.job_id, status="completed")

    with pytest.raises(ValueError, match="natural key"):
        store.reclaim_weekly_okr_analysis_job_cache_miss(
            original.job_id,
            **{**values, "source_digest": "2" * 64},
        )
    store.reclaim_weekly_okr_analysis_job_cache_miss(original.job_id, **values)
    store.finish_weekly_okr_analysis_job(
        original.job_id, status="failed", error="provider unavailable"
    )
    with pytest.raises(ValueError, match="completed or running"):
        store.reclaim_weekly_okr_analysis_job_cache_miss(
            original.job_id, **values
        )


def test_task_agent_run_begin_is_concurrent_and_finish_is_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task-run-lifecycle.sqlite3")
    summary_id = store.enqueue_work_summary_input("local_file", "source", "{}")
    claimed = store.claim_work_summary_inputs(1)[0]
    assert claimed.id == summary_id
    barrier = Barrier(3)
    run_ids: list[int] = []

    def begin() -> None:
        barrier.wait(timeout=5)
        run_ids.append(store.begin_task_agent_run(summary_id))

    threads = [Thread(target=begin) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert len(run_ids) == 2
    assert len(set(run_ids)) == 1
    run_id = run_ids[0]
    store.finish_task_agent_run(
        run_id,
        status="completed",
        codex_session_id="session-task",
        decision_json='{"action":"discard"}',
        audit_summary="done",
        memory_recall_used=True,
    )
    store.finish_task_agent_run(
        run_id,
        status="completed",
        codex_session_id="session-task",
        decision_json='{"action":"discard"}',
        audit_summary="done",
        memory_recall_used=True,
    )
    with store._connect() as db:
        row = db.execute("select * from task_agent_runs where id=?", (run_id,)).fetchone()
    assert row["status"] == "completed"
    assert row["finished_at"]


def test_meeting_run_begin_is_idempotent_and_finish_closes_running_parent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "meeting-run-lifecycle.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-lifecycle", title="Meeting", source_json="{}",
        participants_json="[]", ended_at="2026-08-20T10:00:00+08:00",
        eligible_at="2026-08-20T10:10:00+08:00", status="pending",
    )
    [job] = store.claim_meeting_alignment_jobs(1, now="2026-08-20T10:11:00+08:00")
    assert job.id == job_id

    run_id = store.begin_meeting_alignment_run(job_id)
    assert store.begin_meeting_alignment_run(job_id) == run_id
    store.finish_meeting_alignment_run(
        run_id, status="no_action", decision_json='{"action":"no_action"}'
    )
    assert store.get_meeting_alignment_run(run_id).status == "no_action"


@pytest.mark.parametrize(
    "status",
    ["", "running", " running ", "READY_TO_SEND", "ready-to-send", "typo"],
)
def test_meeting_run_finish_rejects_noncanonical_terminal_status(
    tmp_path: Path, status: str
):
    store = AutoReplyStore(tmp_path / f"meeting-invalid-{len(status)}.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id=f"meeting-invalid-{len(status)}", title="Meeting",
        source_json="{}", participants_json="[]",
        ended_at="2026-08-20T10:00:00+08:00",
        eligible_at="2026-08-20T10:10:00+08:00", status="pending",
    )
    store.claim_meeting_alignment_jobs(1, now="2026-08-20T10:11:00+08:00")
    run_id = store.begin_meeting_alignment_run(job_id)

    with pytest.raises(ValueError, match="terminal status"):
        store.finish_meeting_alignment_run(run_id, status=status)


@pytest.mark.parametrize(
    "status", ["failed", "retry", "no_action", "ready_to_send"]
)
def test_meeting_run_finish_accepts_production_terminal_statuses(
    tmp_path: Path, status: str
):
    store = AutoReplyStore(tmp_path / f"meeting-valid-{status}.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id=f"meeting-valid-{status}", title="Meeting",
        source_json="{}", participants_json="[]",
        ended_at="2026-08-20T10:00:00+08:00",
        eligible_at="2026-08-20T10:10:00+08:00", status="pending",
    )
    store.claim_meeting_alignment_jobs(1, now="2026-08-20T10:11:00+08:00")
    run_id = store.begin_meeting_alignment_run(job_id)

    store.finish_meeting_alignment_run(run_id, status=status)

    assert store.get_meeting_alignment_run(run_id).status == status


def test_wechat_import_jobs_are_distinct_per_invocation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "wechat-import-job.sqlite3")
    first = store.begin_wechat_memory_import_job(
        import_run_id="wechat-scope", account_id="account-1"
    )
    second = store.begin_wechat_memory_import_job(
        import_run_id="wechat-scope", account_id="account-1"
    )
    assert first != second
    attempt = store.claim_runtime_operation_attempt(
        "memory", f"wechat_memory_import_job:{first}", "codex_oauth",
        "codex_cli", "local_oauth", "gpt-5.5",
    )
    assert attempt.workload_key == f"wechat_memory_import_job:{first}"
    store.finish_wechat_memory_import_job(first, status="completed")


def test_current_schema_sentinel_rejects_complete_legacy_task_run_shape(
    tmp_path: Path,
):
    db_path = tmp_path / "legacy-task-run-shape.sqlite3"
    store = AutoReplyStore(db_path)
    with store._connect() as db:
        db.execute(
            "insert into work_summary_inputs "
            "(id, source_type, source_ref, payload_json) "
            "values (1, 'local_file', 'legacy', '{}')"
        )
        db.execute(
            "insert into task_agent_runs "
            "(id, summary_input_id, codex_session_id, decision_json, "
            "audit_summary, memory_recall_used) "
            "values (9, 1, 'legacy-session', '{}', 'legacy', 0)"
        )
        db.execute("drop index idx_task_agent_runs_active_input")
        db.execute("alter table task_agent_runs rename to task_agent_runs_current")
        db.execute(
            "create table task_agent_runs ("
            "id integer primary key autoincrement, "
            "summary_input_id integer not null, "
            "codex_session_id text not null default '', "
            "decision_json text not null default '{}', "
            "audit_summary text not null default '', "
            "memory_recall_used integer not null default 0, "
            "created_at text not null default current_timestamp)"
        )
        db.execute(
            "insert into task_agent_runs select id, summary_input_id, "
            "codex_session_id, decision_json, audit_summary, memory_recall_used, "
            "created_at from task_agent_runs_current"
        )
        db.execute("drop table task_agent_runs_current")
    assert store._schema_is_current() is False
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    upgraded = AutoReplyStore(db_path)
    with upgraded._connect() as db:
        row = db.execute("select * from task_agent_runs where id=9").fetchone()
    assert row["status"] == "completed"
    assert row["finished_at"] == row["created_at"]
    assert row["updated_at"] == row["created_at"]


def test_current_schema_sentinel_requires_new_runtime_parent_tables(tmp_path: Path):
    db_path = tmp_path / "legacy-runtime-parent-tables.sqlite3"
    store = AutoReplyStore(db_path)
    with store._connect() as db:
        db.execute("drop index idx_weekly_okr_analysis_jobs_identity")
        db.execute("drop table weekly_okr_analysis_jobs")
    assert store._schema_is_current() is False
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    reopened = AutoReplyStore(db_path)
    with reopened._connect() as db:
        tables = {
            row["name"]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
    assert {"weekly_okr_analysis_jobs", "wechat_memory_import_jobs"} <= tables


def test_current_schema_sentinel_migrates_complete_legacy_meeting_run_shape(
    tmp_path: Path,
):
    db_path = tmp_path / "legacy-meeting-run-shape.sqlite3"
    store = AutoReplyStore(db_path)
    with store._connect() as db:
        db.execute(
            "insert into meeting_alignment_jobs (id, meeting_id) "
            "values (1, 'legacy-meeting')"
        )
        db.execute("drop index idx_meeting_alignment_runs_active_job")
        db.execute(
            "insert into meeting_alignment_runs "
            "(id, job_id, status, error, created_at) values "
            "(7, 1, 'running', '', '2026-08-20 09:00:00'), "
            "(8, 1, 'running', '', '2026-08-20 09:00:00')"
        )
        db.execute(
            "alter table meeting_alignment_runs rename to meeting_alignment_runs_current"
        )
        db.execute(
            "create table meeting_alignment_runs ("
            "id integer primary key autoincrement, job_id integer not null, "
            "codex_session_id text not null default '', "
            "codex_transcript_start_line integer not null default 0, "
            "codex_transcript_end_line integer not null default 0, "
            "decision_json text not null default '{}', "
            "audit_tool_events_json text not null default '[]', "
            "audit_summary text not null default '', status text not null, "
            "error text not null default '', "
            "created_at text not null default current_timestamp, "
            "foreign key(job_id) references meeting_alignment_jobs(id))"
        )
        db.execute(
            "insert into meeting_alignment_runs select id, job_id, codex_session_id, "
            "codex_transcript_start_line, codex_transcript_end_line, decision_json, "
            "audit_tool_events_json, audit_summary, status, error, created_at "
            "from meeting_alignment_runs_current"
        )
        db.execute("drop table meeting_alignment_runs_current")
    assert store._schema_is_current() is False
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    upgraded = AutoReplyStore(db_path)
    older = upgraded.get_meeting_alignment_run(7)
    newest = upgraded.get_meeting_alignment_run(8)
    assert older is not None and newest is not None
    assert older.status == "failed"
    assert older.error == "schema_migration_duplicate_running_meeting_run"
    assert older.finished_at == older.created_at
    assert older.updated_at == older.created_at
    assert newest.status == "running"
    assert newest.finished_at == ""
    with upgraded._connect() as db:
        ids = [
            row["id"]
            for row in db.execute(
                "select id from meeting_alignment_runs order by id"
            ).fetchall()
        ]
    assert ids == [7, 8]


def test_runtime_attempt_transitions_are_terminal_safe(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    running = store.mark_agent_runtime_attempt_running(attempt.id)
    completed = store.complete_agent_runtime_attempt(
        running.id,
        session_id="session-1",
        transcript_reference="run.jsonl",
        transcript_start=4,
        transcript_end=8,
    )

    assert running.status == "running"
    assert completed.status == "completed"
    with pytest.raises(ValueError, match="completed"):
        store.fail_agent_runtime_attempt(
            completed.id, "process", "codex_process_failed", False
        )
    with pytest.raises(ValueError, match="completed"):
        store.mark_agent_runtime_attempt_superseded(completed.id)


def test_runtime_attempt_completion_allows_missing_session_and_transcript(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    completed = store.complete_agent_runtime_attempt(attempt.id, "", "", 0, 0)

    assert completed.status == "completed"
    assert completed.session_id == ""
    assert completed.transcript_reference == ""


def test_runtime_attempt_and_conversation_session_commit_atomically(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "runtime-attempt-session-atomic.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "claude_api", "claude_cli", "service_api", "claude-sonnet-4-5"
    )
    attempt = store.mark_agent_runtime_attempt_running_once(attempt.id)

    def fail_slot(*args, **kwargs):
        raise RuntimeError("injected_slot_failure")

    monkeypatch.setattr(
        store, "_upsert_conversation_runtime_session_in_connection", fail_slot
    )
    with pytest.raises(RuntimeError, match="injected_slot_failure"):
        store.complete_agent_runtime_attempt(
            attempt.id,
            "claude-session",
            "",
            0,
            3,
            result_schema_id="schema-v1",
            result_envelope_json=json.dumps(
                {"schema_id": "schema-v1", "raw_result": "{}"}
            ),
            conversation_id="cid-runtime-atomic",
            route_name="claude_api",
            conversation_contract_hash="contract-v1",
        )

    persisted = store.get_agent_runtime_attempt(attempt.id)
    assert persisted is not None and persisted.status == "running"
    assert persisted.session_id == ""
    assert persisted.result_envelope_json == ""
    assert store.get_conversation_runtime_session(
        "cid-runtime-atomic", "claude_api"
    ) is None


def test_runtime_attempt_rejects_conflicting_terminal_rewrites(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    failed = store.fail_agent_runtime_attempt(
        attempt.id, "authentication", "codex_login_required", True
    )

    assert (
        store.fail_agent_runtime_attempt(
            attempt.id, "authentication", "codex_login_required", True
        )
        == failed
    )
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.fail_agent_runtime_attempt(
            attempt.id, "process", "codex_process_failed", False
        )


def test_runtime_attempt_effect_started_timestamp_is_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    first = store.note_runtime_attempt_effect_started(
        attempt.id, at="2026-08-20 10:00:00"
    )
    repeated = store.note_runtime_attempt_effect_started(
        attempt.id, at="2026-08-20 10:01:00"
    )

    assert first.first_effect_started_at == "2026-08-20 10:00:00"
    assert repeated.first_effect_started_at == first.first_effect_started_at


def test_route_sessions_do_not_overwrite_other_routes(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-sessions.sqlite3")

    store.upsert_conversation_runtime_session(
        "cid", "codex_oauth", "oauth-session", "oauth-contract"
    )
    store.upsert_conversation_runtime_session(
        "cid", "codex_api", "api-session", "api-contract"
    )

    assert (
        store.get_conversation_runtime_session("cid", "codex_oauth")
        == "oauth-session"
    )
    assert store.get_conversation_runtime_session("cid", "codex_api") == "api-session"
    assert store.get_conversation_runtime_session(
        "cid", "codex_oauth", required_contract_hash="api-contract"
    ) is None
    assert store.get_conversation_runtime_session(
        "cid", "codex_api", required_contract_hash="api-contract"
    ) == "api-session"


def test_route_session_initialization_backfills_legacy_codex_session(tmp_path: Path):
    db_path = tmp_path / "legacy-route-sessions.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            create table conversations (
                conversation_id text primary key,
                title text not null,
                single_chat integer not null,
                codex_session_id text
            )
            """
        )
        db.execute(
            """
            insert into conversations (
                conversation_id, title, single_chat, codex_session_id
            ) values ('legacy-cid', 'Legacy', 0, 'legacy-session')
            """
        )

    store = AutoReplyStore(db_path)

    assert store.get_codex_session_id("legacy-cid") == "legacy-session"
    assert (
        store.get_conversation_runtime_session("legacy-cid", "codex_oauth")
        == "legacy-session"
    )
    assert store.get_conversation_runtime_session_contract_hash(
        "legacy-cid", "codex_oauth"
    ) == ""
    assert store.get_conversation_runtime_session(
        "legacy-cid",
        "codex_oauth",
        required_contract_hash="current-wire-contract",
    ) is None


def test_route_session_migration_marks_existing_rows_contract_unknown(tmp_path: Path):
    db_path = tmp_path / "old-route-session-schema.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            create table conversation_runtime_sessions (
                conversation_id text not null,
                route_name text not null,
                session_id text not null,
                updated_at text not null default current_timestamp,
                primary key(conversation_id, route_name)
            )
            """
        )
        db.execute(
            """
            insert into conversation_runtime_sessions (
                conversation_id, route_name, session_id
            ) values ('cid', 'codex_api', 'old-api-session')
            """
        )

    store = AutoReplyStore(db_path)

    assert store.get_conversation_runtime_session_contract_hash(
        "cid", "codex_api"
    ) == ""
    assert store.get_conversation_runtime_session(
        "cid", "codex_api", required_contract_hash="current-contract"
    ) is None


def test_current_schema_reopens_and_repairs_old_route_session_shape(tmp_path: Path):
    db_path = tmp_path / "current-version-old-route-table.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_conversation_runtime_session(
        "cid", "codex_api", "old-api-session", "old-contract"
    )
    with store._connect() as db:
        db.execute(
            "alter table conversation_runtime_sessions rename to route_sessions_new"
        )
        db.execute(
            """
            create table conversation_runtime_sessions (
                conversation_id text not null,
                route_name text not null,
                session_id text not null,
                updated_at text not null default current_timestamp,
                primary key(conversation_id, route_name)
            )
            """
        )
        db.execute(
            """
            insert into conversation_runtime_sessions (
                conversation_id, route_name, session_id, updated_at
            )
            select conversation_id, route_name, session_id, updated_at
            from route_sessions_new
            """
        )
        db.execute("drop table route_sessions_new")
        version = db.execute(
            "select value from service_state where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        ).fetchone()[0]
    assert version == store_module.STORE_SCHEMA_VERSION
    assert store._schema_is_current() is False
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    reopened = AutoReplyStore(db_path)

    assert reopened.get_conversation_runtime_session_contract_hash(
        "cid", "codex_api"
    ) == ""
    assert reopened.get_conversation_runtime_session(
        "cid", "codex_api", required_contract_hash="current-contract"
    ) is None


def test_route_pause_is_independent_and_expires(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-pauses.sqlite3")

    assert store.open_runtime_route_pause(
        "codex_oauth", "codex_login_required", retry_at="2026-08-20 10:30:00"
    )
    assert store.active_runtime_route_pause("codex_api", now="2026-08-20 10:00:00") is None
    assert (
        store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:31:00")
        is None
    )


def test_route_pause_open_close_is_independent_and_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-pauses.sqlite3")

    assert store.open_runtime_route_pause(
        "codex_oauth", "codex_login_required", retry_at="2099-01-01 00:00:00"
    )
    assert not store.open_runtime_route_pause(
        "codex_oauth", "codex_login_required", retry_at="2099-01-01 00:00:00"
    )
    assert store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00") == (
        "codex_login_required"
    )
    assert store.close_runtime_route_pause("codex_oauth")
    assert not store.close_runtime_route_pause("codex_oauth")
    assert store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00") is None


def test_route_pause_reopens_after_expiry_without_prior_read(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-pauses.sqlite3")
    assert store.open_runtime_route_pause(
        "codex_oauth", "old_failure", retry_at="2000-01-01 00:00:00"
    )

    assert store.open_runtime_route_pause(
        "codex_oauth", "new_failure", retry_at="2099-01-01 00:00:00"
    )
    assert store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00") == (
        "new_failure"
    )


def test_route_pause_open_and_expiry_cleanup_are_serialized(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-pauses.sqlite3")
    assert store.open_runtime_route_pause(
        "codex_oauth", "old_failure", retry_at="2000-01-01 00:00:00"
    )
    barrier = Barrier(2)
    results = Queue()

    def open_new_pause():
        barrier.wait(timeout=5)
        results.put(store.open_runtime_route_pause(
            "codex_oauth", "new_failure", retry_at="2099-01-01 00:00:00"
        ))

    thread = Thread(target=open_new_pause)
    thread.start()
    barrier.wait(timeout=5)
    store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00")
    thread.join(timeout=5)

    assert results.get(timeout=1) is True
    assert store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00") == "new_failure"


@pytest.mark.parametrize("field, replacement", [
    ("runtime_kind", "claude_cli"),
    ("credential_mode", "service_api"),
    ("model", "gpt-5.6"),
])
def test_runtime_attempt_active_claim_rejects_conflicting_immutable_data(
    tmp_path: Path, field: str, replacement: str
):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    values = {"runtime_kind": "codex_cli", "credential_mode": "local_oauth", "model": "gpt-5.5"}
    store.claim_agent_runtime_attempt(run.id, "codex_oauth", **values)
    values[field] = replacement

    with pytest.raises(ValueError, match="conflicting active runtime attempt"):
        store.claim_agent_runtime_attempt(run.id, "codex_oauth", **values)


def test_runtime_attempt_readback_reflects_effect_start(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5")
    assert store.get_agent_runtime_attempt(attempt.id) == attempt
    assert store.get_agent_runtime_attempt(999999) is None
    store.note_runtime_attempt_effect_started(attempt.id, at="2026-08-20 10:00:00")
    assert store.get_agent_runtime_attempt(attempt.id).first_effect_started_at == "2026-08-20 10:00:00"


def test_agent_run_write_transaction_propagates_post_yield_lock_error(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "transaction.sqlite3")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        with store._agent_run_write_transaction(None):
            raise sqlite3.OperationalError("database is locked")


def test_agent_run_write_transaction_retries_begin_then_runs_body_once(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "transaction.sqlite3")
    original_connect = store._connect
    attempts = 0
    body_runs = 0

    @contextmanager
    def flaky_connect():
        nonlocal attempts
        attempts += 1
        with original_connect() as db:
            if attempts == 1:
                class BeginLocked:
                    def execute(self, _sql):
                        raise sqlite3.OperationalError("database is locked")
                yield BeginLocked()
            else:
                yield db

    monkeypatch.setattr(store, "_connect", flaky_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    with store._agent_run_write_transaction(None):
        body_runs += 1

    assert attempts == 2
    assert body_runs == 1


def test_agent_run_write_transaction_closes_every_failed_begin_connection(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "transaction.sqlite3")
    closed = 0

    @contextmanager
    def locked_connect():
        nonlocal closed
        try:
            class BeginLocked:
                def execute(self, _sql):
                    raise sqlite3.OperationalError("database is locked")
            yield BeginLocked()
        finally:
            closed += 1

    monkeypatch.setattr(store, "_connect", locked_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        with store._agent_run_write_transaction(None):
            pytest.fail("transaction body must not run")

    assert closed == store_module.AGENT_RUN_WRITE_LOCK_RETRY_ATTEMPTS


def test_list_agent_run_summaries_for_terminal_runs_batches_without_events(
    tmp_path: Path,
):
    statements: list[str] = []

    class TracedStore(AutoReplyStore):
        def _open_connection(self):
            connection = super()._open_connection()
            connection.set_trace_callback(statements.append)
            return connection

    store = TracedStore(tmp_path / "worker.sqlite3")

    def seed_generation(label: str, generation: str):
        store.enqueue_reply_task(
            conversation_id=f"cid-summary-{label}",
            conversation_title=f"Summary {label}",
            single_chat=False,
            trigger_message_id=f"msg-summary-{label}",
            trigger_create_time="2026-08-18 10:00:00",
            trigger_sender="Derek",
            trigger_text="Summarize the agent runs.",
            execution_generation=generation,
        )
        [task] = store.claim_reply_tasks(limit=1)
        consumer = store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="",
            owner=f"consumer-{label}",
        ).run
        audit = _claim_audit_run(
            store,
            task.id,
            task.execution_generation,
            owner=f"audit-{label}",
        ).run
        store.append_agent_run_event(
            audit.id,
            {"type": "tool", "name": f"summary-{label}"},
            owner=f"audit-{label}",
        )
        return consumer, audit

    first_consumer, first_terminal = seed_generation("first", "generation-first")
    second_consumer, second_terminal = seed_generation("second", "generation-second")
    method = getattr(store, "list_agent_run_summaries_for_terminal_runs", None)
    assert method is not None

    statements.clear()
    summaries = method(
        [second_terminal.id, first_terminal.id, first_terminal.id, 0, -1, 999999]
    )

    assert set(summaries) == {first_terminal.id, second_terminal.id}
    assert [run.id for run in summaries[first_terminal.id]] == [
        first_consumer.id,
        first_terminal.id,
    ]
    assert [run.id for run in summaries[second_terminal.id]] == [
        second_consumer.id,
        second_terminal.id,
    ]
    assert all(
        run.tool_events == []
        for summary_runs in summaries.values()
        for run in summary_runs
    )
    normalized_statements = [statement.casefold() for statement in statements]
    assert len(
        [statement for statement in normalized_statements if "from agent_runs" in statement]
    ) == 1
    assert not any("agent_run_events" in statement for statement in normalized_statements)
    assert method([]) == {}


def test_finalize_orchestration_records_confirmed_sent_reply_atomically(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-confirmed-direct",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-confirmed-direct",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please reply",
    )
    [task] = store.claim_reply_tasks(limit=1)
    audit = _claim_audit_run(
        store,
        task.id,
        task.execution_generation,
        owner="audit",
    ).run
    audit = store.complete_agent_run(
        audit.id,
        {"outcome": "executed", "summary": "Readback verified."},
        owner="audit",
        side_effect_state="confirmed",
    )

    store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=audit.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="Readback verified.",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="Readback verified.",
        send_status="completed",
        send_error="",
        channel="dingtalk",
        sent_reply_text="Verified direct reply",
        sent_reply_result_json='{"source":"test"}',
    )

    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)

    assert sent is not None
    assert sent.reply_text == "Verified direct reply"
    assert store.record_confirmed_sent_reply_if_absent(
        audit_run_id=audit.id,
        reply_text="Verified direct reply",
        send_result_json='{"source":"test"}',
    ) is False


def test_finalize_orchestration_inherits_oa_identity_from_reply_task(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    oa_url = (
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    )
    store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id="oa-pending:proc-1:revision-1",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Derek OA",
        trigger_text="吴柯欣提交的录用申请",
        oa_url=oa_url,
    )
    [task] = store.claim_reply_tasks(limit=1)
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    ).run
    consumer = store.complete_agent_run(
        consumer.id,
        {"outcome": "no_action", "summary": "当前审批节点已经完成。"},
        owner="consumer",
    )

    attempt_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=consumer.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="当前审批节点已经完成。",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="当前审批节点已经完成。",
        send_status="skipped",
        send_error="",
        channel="dingtalk",
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_url == oa_url
    assert attempt.oa_action == "review"


def test_finalize_pre_run_failure_inherits_oa_identity_from_reply_task(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    oa_url = (
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    )
    store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id="oa-pending:proc-1:revision-1",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Derek OA",
        trigger_text="吴柯欣提交的录用申请",
        oa_url=oa_url,
    )
    [task] = store.claim_reply_tasks(limit=1)

    attempt_id = store.finalize_reply_task_without_run(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        task_status="failed",
        task_error="worker_start_failed",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="worker_start_failed",
        audit_summary="worker_start_failed",
        send_status="failed",
        send_error="worker_start_failed",
        channel="dingtalk",
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_url == oa_url
    assert attempt.oa_action == "review"


def test_store_indexes_and_searches_codex_sessions_with_fts_and_embeddings(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.upsert_codex_session_search_index(
        session_id="session-risk-budget",
        source_type="meeting_alignment",
        source_id="10",
        title="上线评审",
        summary_text="话题：上线范围 风险预算。Derek 认为先定义可接受故障面。",
        fts_text="上线 上线范围 风险 风险预算 故障 故障面",
        embedding=[1.0, 0.0],
    )
    store.upsert_codex_session_search_index(
        session_id="session-customer-script",
        source_type="meeting_alignment",
        source_id="11",
        title="客服话术",
        summary_text="话题：客服解释口径。",
        fts_text="客服 话术 解释 口径",
        embedding=[0.0, 1.0],
    )

    results = store.search_codex_sessions(
        fts_query="上线 风险",
        query_embedding=[1.0, 0.0],
        limit=2,
    )

    assert [result.session_id for result in results] == [
        "session-risk-budget",
        "session-customer-script",
    ]
    assert results[0].embedding_score > results[1].embedding_score
    assert results[0].bm25_score is not None


def test_store_connections_enable_sqlite_concurrency_pragmas(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store._connect() as db:
        journal_mode = db.execute("pragma journal_mode").fetchone()[0]
        busy_timeout = db.execute("pragma busy_timeout").fetchone()[0]
        synchronous = db.execute("pragma synchronous").fetchone()[0]
        foreign_keys = db.execute("pragma foreign_keys").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout >= 30_000
    assert synchronous == 1
    assert foreign_keys == 1


def test_discard_unstarted_service_tasks_closes_only_no_effect_tasks(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for message_id in ("service-1", "service-2"):
        assert store.enqueue_reply_task(
            conversation_id="cid-service",
            conversation_title="Service",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time="2026-08-11 10:00:00",
            trigger_sender="Service",
            trigger_text="synthetic recovery",
            trigger_message_json=json.dumps(
                {"raw_payload": {"service_task": True, "source": "repair"}}
            ),
        )
    tasks = store.claim_reply_tasks(limit=2)
    run = store.claim_agent_run(
        tasks[0].id,
        tasks[0].execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-1",
    ).run

    discarded = store.discard_unstarted_service_tasks(
        [tasks[0].id, tasks[1].id],
        reason="The synthetic recovery source was invalid.",
    )

    assert [task.status for task in discarded] == ["done", "done"]
    assert all(task.error == "The synthetic recovery source was invalid." for task in discarded)
    failed_run = store.get_agent_run(run.id)
    assert failed_run is not None and failed_run.status == "failed"
    assert failed_run.side_effect_state == "none"
    assert json.loads(failed_run.structured_error_json)["code"] == (
        "invalid_service_task_discarded"
    )


def test_discard_unstarted_service_tasks_rejects_started_effect(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-service",
        conversation_title="Service",
        single_chat=True,
        trigger_message_id="service-effect",
        trigger_create_time="2026-08-11 10:00:00",
        trigger_sender="Service",
        trigger_text="synthetic recovery",
        trigger_message_json=json.dumps(
            {"raw_payload": {"service_task": True, "source": "repair"}}
        ),
    )
    task = store.claim_reply_tasks(limit=1)[0]
    run = _claim_audit_run(store, task.id, task.execution_generation, owner="worker-1").run
    store.append_agent_run_event(
        run.id,
        {
            "type": "item.started",
            "item": {"id": "write-1", "metadata": {"effect": "effectful"}},
        },
        owner="worker-1",
    )

    with pytest.raises(ValueError, match="started or uncertain effects"):
        store.discard_unstarted_service_tasks(
            [task.id],
            reason="The synthetic recovery source was invalid.",
        )


def test_discard_unstarted_service_tasks_requires_exact_legacy_source(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-service",
        conversation_title="Service",
        single_chat=True,
        trigger_message_id="legacy-service",
        trigger_create_time="2026-08-11 10:00:00",
        trigger_sender="Service",
        trigger_text="synthetic recovery",
        trigger_message_json=json.dumps(
            {"raw_payload": {"source": "oa_pending_scan"}}
        ),
    )
    task = store.claim_reply_tasks(limit=1)[0]

    with pytest.raises(ValueError, match="not an explicit service task"):
        store.discard_unstarted_service_tasks(
            [task.id],
            reason="The synthetic recovery source was invalid.",
            expected_source="wrong_source",
        )

    discarded = store.discard_unstarted_service_tasks(
        [task.id],
        reason="The synthetic recovery source was invalid.",
        expected_source="oa_pending_scan",
    )
    assert discarded[0].status == "done"


def test_store_connections_can_use_short_busy_timeout(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3", busy_timeout_seconds=2)

    with store._connect() as db:
        busy_timeout = db.execute("pragma busy_timeout").fetchone()[0]

    assert busy_timeout == 2_000


def test_store_connections_close_after_context_exit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store._connect() as db:
        db.execute("select 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("select 1").fetchone()


def test_store_initializes_same_path_once_per_process(tmp_path: Path, monkeypatch):
    calls: list[Path] = []
    original_initialize = AutoReplyStore._initialize

    def counted_initialize(self: AutoReplyStore) -> None:
        calls.append(self.path)
        original_initialize(self)

    monkeypatch.setattr(AutoReplyStore, "_initialize", counted_initialize)
    db_path = tmp_path / "worker.sqlite3"

    AutoReplyStore(db_path)
    AutoReplyStore(db_path)

    assert calls == [db_path]


def test_store_skips_schema_work_when_another_process_finished_it(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "worker.sqlite3"
    AutoReplyStore(db_path)


def test_store_repairs_missing_required_table_despite_current_schema_version(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    AutoReplyStore(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute("drop table follow_up_send_attempts")
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    AutoReplyStore(db_path)

    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "select 1 from sqlite_master "
            "where type='table' and name='follow_up_send_attempts'"
        ).fetchone() == (1,)


def test_store_rechecks_schema_after_transient_database_lock(tmp_path, monkeypatch):
    db_path = tmp_path / "worker.sqlite3"
    AutoReplyStore(db_path)
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    original_schema_check = AutoReplyStore._schema_is_current
    checks = 0

    def flaky_schema_check(self: AutoReplyStore) -> bool:
        nonlocal checks
        checks += 1
        if checks == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_schema_check(self)

    def unexpected_initialize(_self: AutoReplyStore) -> None:
        raise AssertionError("transient database lock must not trigger migration")

    monkeypatch.setattr(AutoReplyStore, "_schema_is_current", flaky_schema_check)
    monkeypatch.setattr(AutoReplyStore, "_initialize", unexpected_initialize)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    AutoReplyStore(db_path)

    assert checks == 2
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    def unexpected_initialize(_self: AutoReplyStore) -> None:
        raise AssertionError("schema work should not repeat after another process")

    monkeypatch.setattr(AutoReplyStore, "_initialize", unexpected_initialize)

    AutoReplyStore(db_path)


def test_store_migrates_existing_follow_up_drafts_without_nonconstant_defaults(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    db = sqlite3.connect(db_path)
    try:
        db.execute(
            """
            create table follow_up_drafts (
                id integer primary key autoincrement,
                project_id integer not null,
                todo_id integer not null default 0,
                owner_user_id text not null default '',
                owner_name text not null default '',
                target_conversation_id text not null default '',
                target_kind text not null default '',
                question_text text not null default '',
                risk_check_json text not null default '{}',
                status text not null default 'draft',
                send_result_json text not null default '{}',
                scheduled_at text not null default '',
                sent_at text not null default '',
                created_at text not null default current_timestamp
            )
            """
        )
        db.execute(
            """
            insert into follow_up_drafts (
                project_id, todo_id, owner_user_id, owner_name,
                target_conversation_id, target_kind, question_text,
                risk_check_json, status, send_result_json, scheduled_at, sent_at
            ) values (
                1, 1, 'owner-1', 'Alex',
                'cid-1', 'group', '请同步进展。',
                '{}', 'draft', '{}', '2026-06-26 09:00:00', ''
            )
            """
        )
        db.commit()
    finally:
        db.close()

    store = AutoReplyStore(db_path)

    with store._connect() as migrated:
        columns = {
            row["name"]
            for row in migrated.execute(
                "pragma table_info(follow_up_drafts)"
            ).fetchall()
        }
    assert "updated_at" in columns
    assert "evidence_check_json" in columns
    assert "title" in columns
    assert "description" in columns
    assert "owners_json" in columns
    assert "tags_json" in columns
    assert "participants_json" in columns
    assert "files_json" in columns


def test_store_writer_can_commit_while_reader_transaction_is_open(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    reader = sqlite3.connect(db_path)

    try:
        reader.execute("begin")
        reader.execute("select count(*) from errors").fetchone()

        store.record_error("cid-1", "msg-1", "producer", "database is locked")
    finally:
        reader.rollback()
        reader.close()

    assert store.list_errors(limit=1)[0].kind == "producer"


def test_conversation_session_persists(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation(
        conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )

    loaded = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert loaded.get_codex_session_id("cid-1") == "session-1"


def test_codex_session_lock_is_exclusive(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False

    store.release_codex_session_lock("cid-1", "okr:1")
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True


def test_codex_session_lock_retries_resource_deadlock(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    original_connect = store._connect
    attempts = 0

    @contextmanager
    def flaky_connect():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        with original_connect() as db:
            yield db

    monkeypatch.setattr(store, "_connect", flaky_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True
    assert attempts == 3


def test_retry_failed_pre_agent_reply_task_requires_no_run_or_sent_reply(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Alex",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    claimed = store.claim_reply_task(task_id)
    assert claimed is not None
    store.fail_reply_task(
        task_id,
        "pre_agent_lock_failure",
        expected_execution_generation=claimed.execution_generation,
    )

    recovered = store.retry_failed_pre_agent_reply_task(
        task_id,
        reason="operator_retry_after_lock_recovery",
    )

    assert recovered.status == "pending"
    assert recovered.attempts == 0
    assert recovered.error == "operator_retry_after_lock_recovery"

    claimed = store.claim_reply_task(task_id)
    assert claimed is not None
    store.fail_reply_task(
        task_id,
        "second_failure",
        expected_execution_generation=claimed.execution_generation,
    )
    run = store.claim_agent_run(
        task_id,
        claimed.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="test-run",
    ).run

    with pytest.raises(ValueError, match="already has an agent run"):
        store.retry_failed_pre_agent_reply_task(
            task_id,
            reason="must_not_retry",
        )
    assert run.id > 0


def test_codex_session_lock_replaces_stale_lock(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    with store._connect() as db:
        db.execute(
            """
            update codex_session_locks
            set locked_at=datetime('now', '-21 minutes')
            where conversation_id='cid-1'
            """
        )

    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True
    with store._connect() as db:
        rows = db.execute(
            "select owner from codex_session_locks where conversation_id='cid-1'"
        ).fetchall()
    assert [row["owner"] for row in rows] == ["reply:msg-1"]


def test_codex_session_lock_release_requires_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    assert store.release_codex_session_lock("cid-1", "other") is False
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False
    assert store.release_codex_session_lock("cid-1", "okr:1") is True


def test_codex_session_lock_renewal_requires_current_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "consumer:1") is True
    with store._connect() as db:
        db.execute(
            "update codex_session_locks "
            "set locked_at=datetime('now', '-19 minutes') "
            "where conversation_id='cid-1'"
        )

    assert store.renew_codex_session_lock("cid-1", "other") is False
    assert store.renew_codex_session_lock("cid-1", "consumer:1") is True
    assert store.acquire_codex_session_lock("cid-1", "other") is False

    with store._connect() as db:
        db.execute(
            "update codex_session_locks "
            "set locked_at=datetime('now', '-21 minutes') "
            "where conversation_id='cid-1'"
        )
    assert store.renew_codex_session_lock("cid-1", "consumer:1") is False


def test_codex_session_lock_context_manager_releases_without_swallowing(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store.codex_session_lock("cid-1", "okr:1"):
        assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False

    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True


def test_reply_task_queue_dedupes_by_conversation_and_message(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    first_inserted = store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    second_inserted = store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )

    assert first_inserted is True
    assert second_inserted is False
    assert store.count_reply_tasks(status="pending") == 1


def test_reply_task_execution_generation_defaults_and_survives_requeue(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claimed = store.list_reply_tasks(limit=1)[0]

    assert claimed.id == task_id
    assert claimed.execution_generation == "initial"

    store.requeue_reply_task(
        task_id,
        "retry",
        expected_execution_generation=claimed.execution_generation,
    )
    reclaimed = store.claim_reply_tasks(limit=1)[0]

    assert reclaimed.execution_generation == "initial"


def test_enqueue_reply_task_rejects_empty_execution_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(ValueError, match="execution_generation must be non-empty"):
        store.enqueue_reply_task(
            conversation_id="cid-1",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id="msg-1",
            trigger_create_time="2026-07-20 10:00:00",
            trigger_sender="Derek",
            trigger_text="Handle this task",
            execution_generation="   ",
        )


def test_store_migrates_reply_tasks_with_initial_execution_generation(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            create table reply_tasks (
                id integer primary key autoincrement,
                conversation_id text not null,
                conversation_title text not null,
                single_chat integer not null,
                trigger_message_id text not null,
                trigger_create_time text not null,
                trigger_sender text not null,
                trigger_text text not null,
                status text not null default 'pending',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(conversation_id, trigger_message_id)
            )
            """
        )
        db.execute(
            """
            insert into reply_tasks (
                conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender, trigger_text
            ) values ('cid-legacy', 'Legacy', 0, 'msg-legacy',
                      '2026-07-20 09:00:00', 'Derek', 'Legacy task')
            """
        )

    store = AutoReplyStore(db_path)

    assert store.claim_reply_tasks(limit=1)[0].execution_generation == "initial"


def test_store_channel_identity_migration_preserves_active_execution_generation(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table reply_tasks (
                id integer primary key autoincrement,
                channel text not null default 'dingtalk',
                conversation_id text not null,
                conversation_title text not null,
                single_chat integer not null,
                trigger_message_id text not null,
                trigger_create_time text not null,
                trigger_sender text not null,
                trigger_text text not null,
                trigger_message_json text not null default '{}',
                available_at text not null default '',
                force_new_decision integer not null default 0,
                oa_url text not null default '',
                manual_rerun_attempt_id integer not null default 0,
                manual_rerun_revision_key text not null default '',
                execution_generation text not null default 'initial',
                status text not null default 'pending',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(conversation_id, trigger_message_id)
            );
            insert into reply_tasks (
                conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, execution_generation, status
            ) values (
                'cid-active', 'Active', 0, 'msg-active',
                '2026-07-20 09:00:00', 'Derek', 'Active task',
                'gen-active', 'processing'
            );
            """
        )

    store = AutoReplyStore(db_path)
    migrated = store.get_reply_task(1)
    assert migrated is not None
    assert migrated.status == "processing"
    assert migrated.execution_generation == "gen-active"

    AutoReplyStore(db_path)
    assert store.get_reply_task(1).execution_generation == "gen-active"


def test_reply_task_channel_identity_migration_rolls_back_on_rebuild_failure(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            create table reply_tasks (
                id integer primary key autoincrement,
                channel text not null default 'dingtalk',
                conversation_id text not null,
                conversation_title text not null,
                single_chat integer not null,
                trigger_message_id text not null,
                trigger_create_time text not null,
                trigger_sender text not null,
                trigger_text text not null,
                execution_generation text not null default 'initial',
                status text not null default 'pending',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(conversation_id, trigger_message_id)
            );
            insert into reply_tasks (
                conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, execution_generation, status
            ) values (
                'cid-active', 'Active', 0, 'msg-active',
                '2026-07-20 09:00:00', 'Derek', 'Active task',
                'gen-active', 'processing'
            );
            create table reply_tasks_channel_migration (id integer primary key);
            """
        )

        with pytest.raises(sqlite3.OperationalError):
            AutoReplyStore._migrate_reply_task_channel_identity(db)

        row = db.execute(
            "select execution_generation, status from reply_tasks where id=1"
        ).fetchone()
        assert dict(row) == {
            "execution_generation": "gen-active",
            "status": "processing",
        }
        assert db.in_transaction is False
        assert db.execute("pragma foreign_keys").fetchone()[0] == 1


def test_enqueue_manual_rerun_reply_task_requeues_existing_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
        trigger_message_json='{"open_message_id":"msg-1","content":"old"}',
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "old failure",
        expected_execution_generation=task.execution_generation,
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )

    rerun = store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:01:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 重新看",
        trigger_message_json='{"open_message_id":"msg-1","content":"new"}',
        oa_url="https://oa.example/process",
        attempt_id=attempt_id,
    )

    assert rerun.id == task.id
    assert rerun.status == "pending"
    assert rerun.locked_at is None
    assert rerun.force_new_decision is True
    assert rerun.oa_url == "https://oa.example/process"
    assert rerun.manual_rerun_attempt_id == attempt_id
    assert rerun.error == f"manual_rerun_from_attempt:{attempt_id}"
    assert rerun.trigger_text == "@Alex Chen 重新看"
    claimed = store.claim_reply_tasks(limit=1)
    assert [claimed_task.id for claimed_task in claimed] == [task.id]


def test_manual_rerun_dedupes_same_pending_source_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _enqueue_universal_reply_task(store)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-universal",
        conversation_title="Universal",
        trigger_message_id="msg-universal",
        trigger_sender="Derek",
        trigger_text="Handle this task",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )

    rerun_args = {
        "conversation_id": "cid-universal",
        "conversation_title": "Universal",
        "single_chat": False,
        "trigger_message_id": "msg-universal",
        "trigger_create_time": "2026-07-20 10:01:00",
        "trigger_sender": "Derek",
        "trigger_text": "Run it again",
        "trigger_message_json": "{}",
        "attempt_id": attempt_id,
    }
    first = store.enqueue_manual_rerun_reply_task(**rerun_args)
    second = store.enqueue_manual_rerun_reply_task(**rerun_args)

    assert first.execution_generation
    assert second.execution_generation
    assert first.execution_generation != "initial"
    assert second.execution_generation != "initial"
    assert first.execution_generation == second.execution_generation
    assert first.id == second.id


def test_forced_manual_rerun_rotates_failed_pending_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original = store.get_reply_task(task_id)
    assert original is not None
    run = _claim_audit_run(store,
        task_id,
        original.execution_generation,
        owner="worker-1",
    ).run
    store.fail_agent_run(
        run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="worker-1",
    )
    store.defer_reply_task(
        task_id,
        "codex_process_failed",
        expected_execution_generation=original.execution_generation,
    )

    rerun = store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-universal",
        conversation_title="Universal",
        single_chat=False,
        trigger_message_id="msg-universal",
        trigger_create_time="2026-07-20 10:01:00",
        trigger_sender="Derek",
        trigger_text="Run it again",
        trigger_message_json="{}",
        force_rotation=True,
    )

    assert rerun.id == task_id
    assert rerun.execution_generation != original.execution_generation
    assert rerun.status == "pending"


def test_authorization_defer_preserves_claim_attempt_count(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original = store.get_reply_task(task_id)
    assert original is not None
    assert original.status == "processing" and original.attempts == 1

    store.defer_reply_task_for_authorization(
        task_id,
        "dws_forbidden_accessDenied",
        expected_execution_generation=original.execution_generation,
        available_at="2026-08-13 16:00:00",
    )

    deferred = store.get_reply_task(task_id)
    assert deferred is not None
    assert deferred.status == "pending"
    assert deferred.attempts == 1


def test_forced_manual_rerun_does_not_supersede_active_agent_run(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original = store.get_reply_task(task_id)
    assert original is not None
    _claim_audit_run(store,
        task_id,
        original.execution_generation,
        owner="worker-1",
    )

    with pytest.raises(ValueError, match="active agent run must finish"):
        store.enqueue_manual_rerun_reply_task(
            conversation_id="cid-universal",
            conversation_title="Universal",
            single_chat=False,
            trigger_message_id="msg-universal",
            trigger_create_time="2026-07-20 10:01:00",
            trigger_sender="Derek",
            trigger_text="Run it again",
            trigger_message_json="{}",
            force_rotation=True,
        )

    unchanged = store.get_reply_task(task_id)
    assert unchanged is not None
    assert unchanged.execution_generation == original.execution_generation


def test_manual_rerun_dedupes_same_attempt_across_processes(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.enqueue_reply_task(
        conversation_id="cid-process-rerun",
        conversation_title="Process rerun",
        single_chat=False,
        trigger_message_id="msg-process-rerun",
        trigger_create_time="2026-07-29 10:59:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-process-rerun",
        conversation_title="Process rerun",
        trigger_message_id="msg-process-rerun",
        trigger_sender="ET",
        trigger_text="请处理",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    context = get_context("spawn")
    barrier = context.Barrier(8)
    results = context.Queue()
    processes = [
        context.Process(
            target=_enqueue_manual_rerun_in_process,
                args=(str(db_path), attempt_id, barrier, results),
        )
        for _ in range(8)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=2) for _ in processes]
    assert len({task_id for task_id, _ in outcomes}) == 1
    assert len({generation for _, generation in outcomes}) == 1


def test_manual_rerun_new_source_attempt_rotates_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    common = {
        "conversation_id": "cid-corrected-rerun",
        "conversation_title": "Corrected rerun",
        "single_chat": False,
        "trigger_message_id": "msg-corrected-rerun",
        "trigger_create_time": "2026-07-29 11:00:00",
        "trigger_sender": "ET",
        "trigger_text": "请重新处理",
        "trigger_message_json": "{}",
    }
    attempt_ids = [
        store.record_reply_attempt(
            conversation_id=common["conversation_id"],
            conversation_title=common["conversation_title"],
            trigger_message_id=common["trigger_message_id"],
            trigger_sender=common["trigger_sender"],
            trigger_text=common["trigger_text"],
            action="send_reply",
            sensitivity_kind="general",
            send_status="failed",
        )
        for _ in range(2)
    ]

    first = store.enqueue_manual_rerun_reply_task(
        **common, attempt_id=attempt_ids[0]
    )
    corrected = store.enqueue_manual_rerun_reply_task(
        **common, attempt_id=attempt_ids[1]
    )

    assert corrected.id == first.id
    assert corrected.execution_generation != first.execution_generation
    assert corrected.manual_rerun_attempt_id == attempt_ids[1]


def test_manual_rerun_changed_attempt_revision_rotates_processing_generation(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-revised-attempt",
        conversation_title="Revised attempt",
        trigger_message_id="msg-revised-attempt",
        trigger_sender="ET",
        trigger_text="请重新处理",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    common = {
        "conversation_id": "cid-revised-attempt",
        "conversation_title": "Revised attempt",
        "single_chat": False,
        "trigger_message_id": "msg-revised-attempt",
        "trigger_create_time": "2026-07-29 11:00:00",
        "trigger_sender": "ET",
        "trigger_text": "请重新处理",
        "trigger_message_json": "{}",
        "attempt_id": attempt_id,
    }

    first = store.enqueue_manual_rerun_reply_task(**common)
    claimed = store.claim_reply_tasks(limit=1)
    assert claimed[0].execution_generation == first.execution_generation
    assert store.record_reply_feedback(
        attempt_id,
        feedback="请根据审核意见重新处理",
        corrected_reply_text="这是修正版回复。",
    )

    revised = store.enqueue_manual_rerun_reply_task(**common)
    repeated = store.enqueue_manual_rerun_reply_task(**common)

    assert revised.execution_generation != first.execution_generation
    assert revised.status == "pending"
    assert revised.execution_generation == repeated.execution_generation


def test_generation_rotation_waits_for_unknown_effect_reconciliation(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.append_agent_run_event(
        run.id,
        {
            "type": "item.started",
            "item": {
                "id": "send-1",
                "type": "command_execution",
                "metadata": {"effect": "effectful"},
            },
        },
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )

    with pytest.raises(ValueError, match="reconciliation required"):
        store.rotate_reply_task_execution_generation(task_id)

    task = store.get_reply_task(task_id)
    unresolved = store.get_agent_run(run.id)
    assert task is not None and task.execution_generation == "initial"
    assert task.status == "processing"
    assert unresolved is not None and unresolved.status == "unknown"
    assert [item.id for item in store.list_unknown_agent_runs()] == [run.id]
    assert _claim_audit_run(store,
        task_id,
        "initial",
        owner="new-worker",
    ).claimed is False


def test_service_restart_releases_pending_unknown_audit_reconciliation_lease(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="stopped-worker"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": True},
        owner="stopped-worker",
    )
    claim = store.claim_unknown_agent_run(run.id, owner="stopped-reconciler")
    assert claim.claimed
    store.requeue_reply_task(
        task.id,
        "service_restart_before_reconciliation",
        expected_execution_generation=task.execution_generation,
    )

    released = store.release_unknown_audit_reconciliation_leases_after_service_restart()

    assert [item.id for item in released] == [run.id]
    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.lease_owner == ""
    claimed_task = store.claim_reply_tasks(limit=1)
    assert [item.id for item in claimed_task] == [task.id]
    assert store.claim_unknown_agent_run(run.id, owner="new-reconciler").claimed


def test_finalize_closed_failed_audit_run_repairs_completed_unknown_state(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(
        store,
        task_id,
        "initial",
        owner="worker-1",
    ).run
    for event in (
        {
            "type": "item.started",
            "item": {
                "id": "write-1",
                "metadata": {"effect": "effectful", "action_index": 0},
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "write-1",
                "metadata": {"effect": "effectful", "action_index": 0},
            },
        },
        {
            "type": "item.started",
            "item": {
                "id": "write-2",
                "metadata": {"effect": "effectful", "action_index": 1},
            },
        },
        {
            "type": "item.failed",
            "item": {
                "id": "write-2",
                "metadata": {
                    "effect": "effectful",
                    "action_index": 1,
                    "failure_code": "reconciliation_read_failed",
                    "failure_retryable": False,
                    "failure_gate_state": "unavailable",
                },
            },
        },
    ):
        store.append_agent_run_event(run.id, event, owner="worker-1")
    unknown = store.mark_agent_run_unknown(
        run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="worker-1",
    )
    claim = store.claim_unknown_agent_run(unknown.id, owner="reconciler")
    assert claim.claimed
    store.complete_agent_run(
        unknown.id,
        {
            "outcome": "needs_human",
            "summary": "audit_recovery_ambiguous",
        },
        owner="reconciler",
        side_effect_state="unknown",
        expected_status="unknown",
    )

    repaired = store.finalize_closed_failed_audit_run(
        unknown.id,
        reason="Applicant identity is not available in the current organization.",
    )

    assert repaired.status == "failed"
    assert repaired.side_effect_state == "confirmed"
    assert repaired.final_result_json == ""
    assert json.loads(repaired.structured_error_json) == {
        "authorization_required": False,
        "code": "reconciliation_read_failed",
        "reason": "Applicant identity is not available in the current organization.",
        "retryable": False,
    }


def test_reconciliation_defer_rejects_stale_generation_even_with_live_lease(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    claim = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )
    assert claim.claimed
    with store._connect() as db:
        db.execute(
            "update reply_tasks set execution_generation='new-generation' where id=?",
            (task_id,),
        )

    with pytest.raises(AgentRunLeaseLostError):
        store.defer_unknown_agent_run_reconciliation(
            run.id,
            {"code": "temporary_failure"},
            owner="reconciler-1",
            expected_execution_generation="initial",
            next_attempt_at="2026-07-29 09:10:00",
            now="2026-07-29 09:00:03",
        )

    unchanged = store.get_agent_run(run.id)
    assert unchanged is not None
    assert unchanged.reconciliation_next_attempt_at == ""
    assert unchanged.lease_owner == "reconciler-1"


def test_reviewed_reply_and_rerun_roll_back_together(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-atomic-review",
        conversation_title="Review",
        single_chat=False,
        trigger_message_id="msg-atomic-review",
        trigger_create_time="2026-07-29 10:00:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    with store._connect() as db:
        db.executescript(
            """
            create trigger reject_review_rerun before update on reply_tasks
            when new.manual_rerun_attempt_id > 0
            begin
                select raise(abort, 'forced review rerun failure');
            end;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced review rerun failure"):
        store.record_reviewed_reply_rerun(
            conversation_id="cid-atomic-review",
            conversation_title="Review",
            single_chat=False,
            trigger_message_id="msg-atomic-review",
            trigger_create_time="2026-07-29 10:00:00",
            trigger_sender="ET",
            trigger_text="请处理",
            trigger_message_json="{}",
            suggested_reply_text="建议内容",
            reviewer_feedback="审核意见",
        )

    attempts = store.list_reply_attempts(limit=20)
    task = store.get_reply_task_for_message(
        "cid-atomic-review", "msg-atomic-review"
    )
    assert attempts == []
    assert task is not None and task.manual_rerun_attempt_id == 0


def test_reviewed_reply_rerun_is_idempotent_across_concurrent_connections(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.enqueue_reply_task(
        conversation_id="cid-concurrent-review",
        conversation_title="Review",
        single_chat=False,
        trigger_message_id="msg-concurrent-review",
        trigger_create_time="2026-07-29 10:00:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    barrier = Barrier(12)
    results: Queue = Queue()

    def enqueue_review() -> None:
        thread_store = AutoReplyStore(db_path)
        try:
            barrier.wait(timeout=5)
            results.put(
                thread_store.record_reviewed_reply_rerun(
                    conversation_id="cid-concurrent-review",
                    conversation_title="Review",
                    single_chat=False,
                    trigger_message_id="msg-concurrent-review",
                    trigger_create_time="2026-07-29 10:00:00",
                    trigger_sender="ET",
                    trigger_text="请处理",
                    trigger_message_json="{}",
                    suggested_reply_text="建议内容",
                    reviewer_feedback="审核意见",
                )
            )
        except Exception as exc:  # pragma: no cover - surfaced below
            results.put(exc)

    threads = [Thread(target=enqueue_review) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    outcomes = [results.get_nowait() for _ in threads]
    errors = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert errors == []
    attempt_ids = {outcome[0] for outcome in outcomes}
    generations = {outcome[1].execution_generation for outcome in outcomes}
    assert len(attempt_ids) == 1
    assert len(generations) == 1

    matching_attempts = [
        attempt
        for attempt in store.list_reply_attempts(limit=20)
        if attempt.codex_reason == "reviewed_message_reply"
    ]
    assert [attempt.id for attempt in matching_attempts] == list(attempt_ids)


def test_reviewed_reply_rerun_allows_changed_feedback_to_rotate_generation(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    common = {
        "conversation_id": "cid-revised-review",
        "conversation_title": "Review",
        "single_chat": False,
        "trigger_message_id": "msg-revised-review",
        "trigger_create_time": "2026-07-29 10:00:00",
        "trigger_sender": "ET",
        "trigger_text": "请处理",
        "trigger_message_json": "{}",
        "suggested_reply_text": "建议内容",
    }

    first_attempt_id, first_task = store.record_reviewed_reply_rerun(
        **common,
        reviewer_feedback="审核意见",
    )
    revised_attempt_id, revised_task = store.record_reviewed_reply_rerun(
        **common,
        reviewer_feedback="补充后的审核意见",
    )

    assert revised_attempt_id != first_attempt_id
    assert revised_task.id == first_task.id
    assert revised_task.execution_generation != first_task.execution_generation


def test_actionable_attempt_decision_resolves_source_and_requeues_same_task(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-actionable-decision",
        conversation_title="HR",
        single_chat=False,
        trigger_message_id="msg-actionable-decision",
        trigger_create_time="2026-08-11 05:00:00",
        trigger_sender="Mina",
        trigger_text="Please decide.",
        trigger_message_json="{}",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "decision required",
        expected_execution_generation=task.execution_generation,
    )
    source_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="A manager decision is required.",
        send_status="failed",
    )
    feedback = f"Human decision for source attempt #{source_id}: 暂不处理"

    selected_id, requeued = store.record_actionable_attempt_decision(
        source_id,
        reviewer_feedback=feedback,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_create_time=task.trigger_create_time,
        trigger_message_json=task.trigger_message_json,
    )
    repeated_id, repeated_task = store.record_actionable_attempt_decision(
        source_id,
        reviewer_feedback=feedback,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_create_time=task.trigger_create_time,
        trigger_message_json=task.trigger_message_json,
    )

    source = store.get_reply_attempt(source_id)
    selected = store.get_reply_attempt(selected_id)
    assert requeued.id == task.id
    assert requeued.status == "pending"
    assert source is not None and source.send_status == "decision_selected"
    assert source.reviewer_feedback == feedback
    assert selected is not None and selected.reviewer_feedback == feedback
    assert repeated_id == selected_id
    assert repeated_task.execution_generation == requeued.execution_generation


def test_actionable_attempt_decision_rolls_back_queue_when_source_update_fails(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-actionable-rollback",
        conversation_title="HR",
        single_chat=False,
        trigger_message_id="msg-actionable-rollback",
        trigger_create_time="2026-08-11 05:00:00",
        trigger_sender="Mina",
        trigger_text="Please decide.",
        trigger_message_json="{}",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "decision required",
        expected_execution_generation=task.execution_generation,
    )
    source_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    with store._connect() as db:
        db.executescript(
            f"""
            create trigger reject_actionable_resolution before update on reply_attempts
            when old.id={source_id} and new.send_status='decision_selected'
            begin
                select raise(abort, 'forced actionable resolution failure');
            end;
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="forced actionable resolution failure",
    ):
        store.record_actionable_attempt_decision(
            source_id,
            reviewer_feedback="Human decision: 暂不处理",
            conversation_title=task.conversation_title,
            single_chat=task.single_chat,
            trigger_create_time=task.trigger_create_time,
            trigger_message_json=task.trigger_message_json,
        )

    source = store.get_reply_attempt(source_id)
    unchanged_task = store.get_reply_task(task.id)
    assert source is not None and source.send_status == "failed"
    assert unchanged_task is not None and unchanged_task.status == "failed"
    assert unchanged_task.execution_generation == task.execution_generation
    assert store.count_reply_attempts() == 1


def test_agent_run_is_unique_per_task_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)

    first = _claim_audit_run(store, task_id, "initial", owner="worker-1")
    second = _claim_audit_run(store, task_id, "initial", owner="worker-2")

    assert first.claimed is True
    assert second.claimed is False
    assert second.run.id == first.run.id
    assert second.run.lease_owner == "worker-1"


def test_stale_unknown_effect_without_session_is_recoverable_by_orchestrator(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(
        store,
        task_id,
        "initial",
        owner="failed-worker",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": False},
        owner="failed-worker",
    )
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') "
            "where id=?",
            (task_id,),
        )

    assert [
        task.id for task in store.list_stale_processing_reply_tasks(30 * 60)
    ] == [task_id]
    assert [item.id for item in store.list_unknown_agent_runs()] == [run.id]


def test_stale_task_waits_for_active_unknown_recovery_lease(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="failed-worker").run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": False},
        owner="failed-worker",
    )
    claimed = store.claim_unknown_agent_run(
        run.id,
        owner="recovery-worker",
        lease_seconds=3600,
    )
    assert claimed.claimed is True
    with store._connect() as db:
        db.execute(
            "update reply_tasks set locked_at=datetime('now', '-31 minutes') "
            "where id=?",
            (task_id,),
        )

    assert store.list_stale_processing_reply_tasks(30 * 60) == []

    with store._connect() as db:
        db.execute(
            "update agent_runs set lease_expires_at=datetime('now', '-1 second') "
            "where id=?",
            (run.id,),
        )

    assert [
        task.id for task in store.list_stale_processing_reply_tasks(30 * 60)
    ] == [task_id]


def test_agent_run_concurrent_claims_choose_exactly_one_owner(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    first_store = AutoReplyStore(db_path)
    second_store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(first_store)
    barrier = Barrier(2)
    results: Queue = Queue()

    def claim(store: AutoReplyStore, owner: str) -> None:
        try:
            barrier.wait(timeout=5)
            results.put((_claim_audit_run(store, task_id, "initial", owner=owner), None))
        except BaseException as exc:
            results.put((None, exc))

    threads = [
        Thread(target=claim, args=(first_store, "worker-1")),
        Thread(target=claim, args=(second_store, "worker-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    outcomes = [results.get_nowait(), results.get_nowait()]
    errors = [error for _, error in outcomes if error is not None]
    assert errors == []
    claims = [claim for claim, _ in outcomes]
    assert sum(claim.claimed for claim in claims) == 1
    assert len({claim.run.id for claim in claims}) == 1
    winner = next(claim for claim in claims if claim.claimed)
    loser = next(claim for claim in claims if not claim.claimed)
    assert loser.run.lease_owner == winner.run.lease_owner


def test_agent_run_fresh_lease_cannot_be_stolen_but_expired_lease_recovers(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        lease_seconds=1800,
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-1",
        transcript_start_line=8,
        now="2026-07-29 00:01:00",
    )

    fresh = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-2",
        now="2026-07-29 00:29:59",
    )
    expired = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-2",
        now="2026-07-29 00:30:01",
    )

    assert fresh.claimed is False
    assert expired.claimed is True
    assert expired.run.id == first.run.id
    assert expired.run.codex_session_id == "session-1"
    assert expired.run.transcript_start_line == 8
    assert expired.run.lease_owner == "worker-2"


def test_expired_sessionless_no_effect_agent_run_can_be_reclaimed(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        lease_seconds=1800,
        now="2026-07-29 00:00:00",
    )

    expired = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-2",
        now="2026-07-29 00:30:01",
    )

    assert expired.claimed is True
    assert expired.run.id == first.run.id
    assert expired.run.codex_session_id == ""
    assert expired.run.lease_owner == "worker-2"
    assert expired.run.lease_expires_at > first.run.lease_expires_at


def test_agent_run_claim_rejects_generation_not_owned_by_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)

    with pytest.raises(ValueError, match="execution generation mismatch"):
        _claim_audit_run(store, task_id, "other-generation", owner="worker-1")


def test_agent_run_lease_renewal_requires_current_owner(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claim = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 00:00:00",
    )

    renewed = store.renew_agent_run_lease(
        claim.run.id,
        owner="worker-1",
        lease_seconds=900,
        now="2026-07-29 00:10:00",
    )

    assert renewed.lease_expires_at == "2026-07-29 00:25:00"
    with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
        store.renew_agent_run_lease(
            claim.run.id,
            owner="worker-2",
            now="2026-07-29 00:11:00",
        )


def test_agent_run_lease_renewal_retries_transient_database_lock(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claim = _claim_audit_run(
        store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 00:00:00",
    )
    original_connect = store._connect
    attempts = 0

    @contextmanager
    def flaky_connect():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        with original_connect() as db:
            yield db

    monkeypatch.setattr(store, "_connect", flaky_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    renewed = store.renew_agent_run_lease(
        claim.run.id,
        owner="worker-1",
        lease_seconds=900,
        now="2026-07-29 00:10:00",
    )

    assert attempts == 2
    assert renewed.lease_expires_at == "2026-07-29 00:25:00"


def test_reclaimed_agent_run_rejects_every_stale_owner_mutation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:01:00",
    )
    reclaimed = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:30:01",
    )
    assert reclaimed.claimed is True
    before = store.get_agent_run(first.run.id)
    assert before is not None

    stale_mutations = [
        lambda: store.set_agent_run_session(
            first.run.id,
            "session-1",
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.append_agent_run_event(
            first.run.id,
            {"type": "item.started", "call_id": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.complete_agent_run(
            first.run.id,
            {"outcome": "completed", "summary": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.fail_agent_run(
            first.run.id,
            {"code": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.mark_agent_run_unknown(
            first.run.id,
            {"code": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.renew_agent_run_lease(
            first.run.id,
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.record_agent_execution_receipt(
            first.run.id,
            receipt_id="stale-receipt",
            operation_id="stale-write",
            cli="dws",
            command_path="chat message send",
            command_digest="digest",
            exit_code=0,
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
    ]
    for mutate in stale_mutations:
        with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
            mutate()
        assert store.get_agent_run(first.run.id) == before

    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-b",
        now="2026-07-29 00:30:02",
    )
    store.append_agent_run_event(
        first.run.id,
        {"type": "item.completed", "call_id": "owned"},
        owner="worker-b",
        now="2026-07-29 00:30:02",
    )
    renewed = store.renew_agent_run_lease(
        first.run.id,
        owner="worker-b",
        now="2026-07-29 00:31:00",
    )
    completed = store.complete_agent_run(
        first.run.id,
        {"outcome": "completed", "summary": "owned"},
        owner="worker-b",
        now="2026-07-29 00:31:01",
    )

    assert renewed.lease_owner == "worker-b"
    assert completed.status == "completed"
    assert [event["call_id"] for event in completed.tool_events] == ["owned"]


def test_expired_lease_blocks_writes_until_session_recovery(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:01:00",
    )

    before = store.get_agent_run(first.run.id)
    assert before is not None
    expired_mutations = [
        lambda: store.set_agent_run_session(
            first.run.id,
            "session-1",
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.append_agent_run_event(
            first.run.id,
            {"type": "item.started", "call_id": "blocked"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.complete_agent_run(
            first.run.id,
            {"outcome": "completed", "summary": "expired"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.fail_agent_run(
            first.run.id,
            {"code": "expired"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.mark_agent_run_unknown(
            first.run.id,
            {"code": "expired"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.renew_agent_run_lease(
            first.run.id,
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
    ]
    for mutate in expired_mutations:
        with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
            mutate()
        assert store.get_agent_run(first.run.id) == before

    recovered = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:30:02",
    )
    appended = store.append_agent_run_event(
        first.run.id,
        {"type": "item.started", "call_id": "recovered"},
        owner="worker-b",
        now="2026-07-29 00:30:03",
    )

    assert recovered.claimed is True
    assert appended.tool_events == []
    persisted = store.get_agent_run(first.run.id)
    assert persisted is not None
    assert [event["call_id"] for event in persisted.tool_events] == ["recovered"]


def test_expired_agent_run_with_incomplete_effect_cannot_be_reclaimed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        lease_seconds=60,
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:00:01",
    )
    store.append_agent_run_event(
        first.run.id,
        {
            "type": "item.started",
            "item": {
                "id": "write-1",
                "type": "mcp_tool_call",
                "metadata": {"effect": "effectful"},
            },
        },
        owner="worker-a",
        now="2026-07-29 00:00:02",
    )

    reclaim = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:02:00",
    )

    assert reclaim.claimed is False
    assert reclaim.run.lease_owner == "worker-a"
    assert reclaim.run.side_effect_state == "unknown"


@pytest.mark.parametrize(
    ("cli", "command_path"),
    (
        ("dws", "chat message send"),
        ("mcp:xiaoqing_interview", "upload_interview_result"),
    ),
)
def test_expired_agent_run_with_confirmed_receipt_enters_reconciliation_without_replay(
    tmp_path: Path,
    cli: str,
    command_path: str,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        lease_seconds=60,
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:00:01",
    )
    store.record_agent_execution_receipt(
        first.run.id,
        receipt_id=f"receipt-{cli}",
        operation_id="write-1",
        cli=cli,
        command_path=command_path,
        command_digest="digest",
        exit_code=0,
        owner="worker-a",
        now="2026-07-29 00:00:02",
    )

    reclaim = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:02:00",
    )

    assert reclaim.claimed is False
    assert reclaim.run.status == "unknown"
    assert reclaim.run.side_effect_state == "unknown"
    assert reclaim.run.lease_owner == ""
    assert store.list_agent_execution_receipts(first.run.id)[0].operation_id == "write-1"


def test_running_agent_events_are_persisted_incrementally(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "call_id": "c1",
        "effect": {"kind": "write", "provider": "dws"},
    }
    completed = {
        "type": "item.completed",
        "call_id": "c1",
        "receipt": {"accepted": True},
    }

    store.append_agent_run_event(run.id, started, owner="worker-1")
    store.append_agent_run_event(run.id, completed, owner="worker-1")

    loaded = store.get_agent_run(run.id)
    assert loaded is not None
    assert loaded.tool_events == [started, completed]
    assert loaded.transcript_end_line == 2


def test_agent_run_effect_state_tracks_structured_call_lifecycle(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    completed = {
        "type": "item.completed",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }

    unknown = store.append_agent_run_event(run.id, started, owner="worker-1")
    confirmed = store.append_agent_run_event(run.id, completed, owner="worker-1")

    assert unknown.side_effect_state == "unknown"
    assert confirmed.side_effect_state == "confirmed"


def test_failed_agent_effect_is_terminal_and_not_unknown(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    failed = {
        "type": "item.failed",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }

    store.append_agent_run_event(run.id, started, owner="worker-1")
    terminal = store.append_agent_run_event(run.id, failed, owner="worker-1")

    assert terminal.side_effect_state == "none"


def test_agent_run_events_use_append_only_rows_in_sequence(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    first = {"type": "item.started", "call_id": "c1"}
    second = {"type": "item.completed", "call_id": "c1"}

    store.append_agent_run_event(run.id, first, owner="worker-1")
    store.append_agent_run_event(run.id, second, owner="worker-1")

    with sqlite3.connect(store.path) as db:
        rows = db.execute(
            "select sequence, event_json from agent_run_events "
            "where agent_run_id=? order by sequence",
            (run.id,),
        ).fetchall()
        compact = db.execute(
            "select tool_events_json from agent_runs where id=?",
            (run.id,),
        ).fetchone()[0]
    assert [(row[0], json.loads(row[1])) for row in rows] == [
        (1, first),
        (2, second),
    ]
    assert compact == "[]"
    assert store.get_agent_run(run.id).tool_events == [first, second]


def test_append_agent_events_does_not_reparse_prior_json(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    original_loads = store_module.json.loads
    calls = 0

    def counted_loads(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_loads(*args, **kwargs)

    monkeypatch.setattr(store_module.json, "loads", counted_loads)
    appended = None
    for index in range(50):
        appended = store.append_agent_run_event(
            run.id,
            {
                "type": "item.completed",
                "item": {
                    "id": f"read-{index}",
                    "metadata": {"effect": "read_only"},
                },
            },
            owner="worker-1",
        )

    assert appended is not None and appended.tool_events == []
    assert calls <= 300


def test_agent_run_event_migration_backfills_legacy_json_once(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    legacy_events = [
        {"type": "item.started", "call_id": "legacy-1"},
        {"type": "item.failed", "call_id": "legacy-1"},
    ]
    with sqlite3.connect(db_path) as db:
        db.execute("drop table agent_run_events")
        db.execute(
            "update agent_runs set tool_events_json=? where id=?",
            (json.dumps(legacy_events), run.id),
        )
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    migrated = AutoReplyStore(db_path)
    first_load = migrated.get_agent_run(run.id)
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    second_load = AutoReplyStore(db_path).get_agent_run(run.id)

    assert first_load.tool_events == legacy_events
    assert second_load.tool_events == legacy_events
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "select count(*) from agent_run_events where agent_run_id=?",
            (run.id,),
        ).fetchone()[0] == 2
        assert db.execute(
            "select tool_events_json from agent_runs where id=?",
            (run.id,),
        ).fetchone()[0] == "[]"


def test_safe_persisted_receipt_closes_started_agent_effect(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    receipt = {
        "type": "item.completed",
        "item": {
            "id": "receipt-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "read_only"},
            "result": {
                "receipt_id": "receipt-1",
                "operation_id": "write-1",
                "completed": True,
                "persisted": True,
                "safe_to_confirm": True,
            },
        },
    }

    store.append_agent_run_event(run.id, started, owner="worker-1")
    confirmed = store.append_agent_run_event(run.id, receipt, owner="worker-1")

    assert confirmed.side_effect_state == "confirmed"


def test_safe_persisted_receipt_closes_only_one_started_agent_effect(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    receipt = {
        "type": "item.completed",
        "item": {
            "id": "receipt-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "read_only"},
            "result": {
                "receipt_id": "receipt-1",
                "operation_id": "write-1",
                "completed": True,
                "persisted": True,
                "safe_to_confirm": True,
            },
        },
    }

    store.append_agent_run_event(run.id, started, owner="worker-1")
    store.append_agent_run_event(run.id, started, owner="worker-1")
    persisted = store.append_agent_run_event(run.id, receipt, owner="worker-1")

    assert persisted.side_effect_state == "unknown"


def test_execution_receipt_requires_current_unexpired_owner(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        lease_seconds=60,
        now="2026-07-29 00:00:00",
    ).run

    with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
        store.record_agent_execution_receipt(
            run.id,
            receipt_id="receipt-stale-owner",
            operation_id="write-1",
            cli="dws",
            command_path="chat message send",
            command_digest="digest",
            exit_code=0,
            owner="worker-b",
            now="2026-07-29 00:00:30",
        )
    with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
        store.record_agent_execution_receipt(
            run.id,
            receipt_id="receipt-expired",
            operation_id="write-1",
            cli="dws",
            command_path="chat message send",
            command_digest="digest",
            exit_code=0,
            owner="worker-a",
            now="2026-07-29 00:01:01",
        )

    assert store.list_agent_execution_receipts(run.id) == []


def test_agent_run_concurrent_event_writers_do_not_drop_events(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    first_store = AutoReplyStore(db_path)
    second_store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(first_store)
    run = _claim_audit_run(first_store, task_id, "initial", owner="worker-1").run
    barrier = Barrier(2)
    results: Queue = Queue()

    def append(store: AutoReplyStore, call_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            store.append_agent_run_event(
                run.id,
                {"type": "item.completed", "call_id": call_id},
                owner="worker-1",
            )
            results.put(None)
        except BaseException as exc:
            results.put(exc)

    threads = [
        Thread(target=append, args=(first_store, "c1")),
        Thread(target=append, args=(second_store, "c2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert [results.get_nowait(), results.get_nowait()] == [None, None]

    loaded = first_store.get_agent_run(run.id)
    assert loaded is not None
    assert len(loaded.tool_events) == 2
    assert {event["call_id"] for event in loaded.tool_events} == {"c1", "c2"}


def test_append_rechecks_default_time_after_waiting_for_write_lock(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        lease_seconds=2,
    ).run
    original_utc_store_time = store_module._utc_store_time
    clock_called = Event()

    def observed_utc_store_time(now=None):
        if now is None:
            clock_called.set()
        return original_utc_store_time(now)

    monkeypatch.setattr(store_module, "_utc_store_time", observed_utc_store_time)
    lock_db = sqlite3.connect(db_path, timeout=5)
    lock_db.execute("begin immediate")
    started = Event()
    outcomes: Queue = Queue()

    def append() -> None:
        started.set()
        try:
            result = store.append_agent_run_event(
                run.id,
                {"type": "item.started", "call_id": "expired-while-waiting"},
                owner="worker-1",
            )
            outcomes.put(result)
        except BaseException as exc:
            outcomes.put(exc)

    thread = Thread(target=append)
    thread.start()
    try:
        assert started.wait(timeout=2)
        clock_called_before_release = clock_called.wait(timeout=0.2)
        expires_at = datetime.strptime(
            run.lease_expires_at,
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=timezone.utc)
        wait_seconds = max(
            0.0,
            (expires_at - datetime.now(timezone.utc)).total_seconds(),
        ) + 0.2
        assert wait_seconds < 3
        time.sleep(wait_seconds)
    finally:
        lock_db.rollback()
        lock_db.close()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert clock_called_before_release is False
    outcome = outcomes.get_nowait()
    assert isinstance(outcome, AgentRunLeaseLostError)
    loaded = store.get_agent_run(run.id)
    assert loaded is not None
    assert loaded.tool_events == []


@pytest.mark.parametrize("event", [[], "event", 1, None])
def test_agent_run_event_must_be_a_json_object(tmp_path: Path, event):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run

    with pytest.raises(ValueError, match="event must be a JSON object"):
        store.append_agent_run_event(run.id, event, owner="worker-1")


def test_agent_run_event_rejects_non_json_object_values(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run

    with pytest.raises(ValueError, match="event must be a JSON object"):
        store.append_agent_run_event(
            run.id,
            {"value": object()},
            owner="worker-1",
        )


def test_agent_run_terminal_transitions_are_strict_and_exactly_idempotent(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    final_result = {"outcome": "completed", "summary": "sent"}

    completed = store.complete_agent_run(
        run.id,
        final_result,
        owner="worker-1",
        side_effect_state="confirmed",
        transcript_end_line=12,
    )
    repeated = store.complete_agent_run(
        run.id,
        final_result,
        owner="worker-1",
        side_effect_state="confirmed",
        transcript_end_line=12,
    )

    assert completed.status == "completed"
    assert repeated == completed
    assert completed.lease_owner == ""
    assert completed.lease_expires_at == ""
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.complete_agent_run(
            run.id,
            {"outcome": "completed", "summary": "different"},
            owner="worker-1",
            side_effect_state="confirmed",
            transcript_end_line=12,
        )
    with pytest.raises(ValueError, match="transition from completed"):
        store.fail_agent_run(
            run.id,
            {"code": "late_failure"},
            owner="worker-1",
        )
    with pytest.raises(ValueError, match="terminal agent run"):
        store.append_agent_run_event(
            run.id,
            {"type": "late"},
            owner="worker-1",
        )


def test_unknown_agent_run_resolves_atomically_and_cannot_return_to_running(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    unknown_error = {"code": "effect_completion_missing", "call_id": "c1"}

    unknown = store.mark_agent_run_unknown(
        run.id,
        unknown_error,
        owner="worker-1",
    )
    listed = store.list_unknown_agent_runs()
    reconciliation = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
    )
    assert reconciliation.claimed
    completed = store.resolve_unknown_agent_run_confirmed(
        run.id,
        task_id,
        {"outcome": "completed", "summary": "effect confirmed"},
        owner="reconciler-1",
    )

    assert unknown.status == "unknown"
    assert unknown.side_effect_state == "unknown"
    assert [item.id for item in listed] == [run.id]
    assert completed.status == "completed"
    with pytest.raises(ValueError, match="transition from completed"):
        store.mark_agent_run_unknown(
            run.id,
            unknown_error,
            owner="worker-1",
        )


def test_done_unknown_audit_with_legacy_sent_reply_is_settled_from_delivery_ledger(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store,
        task_id,
        task.execution_generation,
        owner="worker-1",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
    )
    store.record_sent_reply(task.conversation_id, task.trigger_message_id, "delivered")
    with store._connect() as db:
        db.execute(
            "update reply_tasks set status='done' where id=?",
            (task_id,),
        )

    assert store.settle_unknown_audit_runs_with_sent_reply() == 1

    settled = store.get_agent_run(run.id)
    assert settled is not None
    assert settled.status == "completed"
    assert settled.side_effect_state == "confirmed"
    assert settled.reconciliation_suspended is False
    assert store.get_reply_attempt(1) is None


def test_pending_unknown_audit_with_exact_sent_reply_is_settled_atomically(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store,
        task_id,
        task.execution_generation,
        owner="worker-1",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
    )
    store.requeue_reply_task(
        task_id,
        "unknown_agent_run_reconciliation",
        expected_execution_generation=task.execution_generation,
    )
    store.record_sent_reply(
        task.conversation_id,
        task.trigger_message_id,
        "delivered",
        send_result_json=json.dumps(
            {"agent_run_id": run.id, "operation_id": run.operation_id},
            separators=(",", ":"),
        ),
    )

    assert store.settle_unknown_audit_runs_with_sent_reply() == 1

    settled_run = store.get_agent_run(run.id)
    settled_task = store.get_reply_task(task_id)
    assert settled_run is not None
    assert settled_run.status == "completed"
    assert settled_run.side_effect_state == "confirmed"
    assert settled_task is not None
    assert settled_task.status == "done"
    assert settled_task.error == ""


@pytest.mark.parametrize(
    ("agent_run_id_delta", "operation_id"),
    ((1, None), (0, "different-operation")),
)
def test_pending_unknown_audit_rejects_inexact_sent_reply_binding(
    tmp_path: Path,
    agent_run_id_delta: int,
    operation_id: str | None,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store,
        task_id,
        task.execution_generation,
        owner="worker-1",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
    )
    store.requeue_reply_task(
        task_id,
        "unknown_agent_run_reconciliation",
        expected_execution_generation=task.execution_generation,
    )
    store.record_sent_reply(
        task.conversation_id,
        task.trigger_message_id,
        "older delivery",
        send_result_json=json.dumps(
            {
                "agent_run_id": run.id + agent_run_id_delta,
                "operation_id": operation_id or run.operation_id,
            },
            separators=(",", ":"),
        ),
    )

    assert store.settle_unknown_audit_runs_with_sent_reply() == 0
    assert store.get_agent_run(run.id).status == "unknown"
    assert store.get_reply_task(task_id).status == "pending"


def test_unknown_agent_run_uses_explicit_reconciliation_event_path(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing", "call_id": "c1"},
        owner="worker-1",
    )

    with pytest.raises(ValueError, match="terminal agent run"):
        store.append_agent_run_event(
            run.id,
            {"type": "reconciliation.completed", "call_id": "r1"},
            owner="worker-1",
        )
    claim = store.claim_unknown_agent_run(run.id, owner="reconciler-1")
    assert claim.claimed
    appended = store.append_unknown_agent_run_event(
        run.id,
        {"type": "reconciliation.completed", "call_id": "r1"},
        owner="reconciler-1",
    )

    assert appended is None
    persisted = store.get_agent_run(run.id)
    assert [event["call_id"] for event in persisted.tool_events] == ["r1"]


def test_failed_agent_run_rejects_conflicting_terminal_rewrite(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    error = {"code": "command_failed", "retryable": True}

    failed = store.fail_agent_run(
        run.id,
        error,
        owner="worker-1",
        transcript_end_line=5,
    )
    repeated = store.fail_agent_run(
        run.id,
        error,
        owner="worker-1",
        transcript_end_line=5,
    )

    assert failed == repeated
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.fail_agent_run(
            run.id,
            {"code": "different_failure"},
            owner="worker-1",
            transcript_end_line=5,
        )


def test_retry_failed_reply_task_creates_a_new_retryable_consumer_turn(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    claim = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-1",
    )
    store.fail_agent_run(
        claim.run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "codex_process_failed",
        expected_execution_generation=task.execution_generation,
    )

    recovered = store.retry_failed_reply_task(
        task_id,
        claim.run.id,
        reason="operator_retry_after_runtime_fix",
    )

    assert recovered.status == "pending"
    assert recovered.execution_generation == task.execution_generation
    assert recovered.error == "operator_retry_after_runtime_fix"
    retry_claim = store.claim_reply_task(task_id)
    assert retry_claim is not None
    same_turn = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-2",
    )
    assert same_turn.claimed is False
    next_turn = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=store.next_agent_run_turn_attempt(
            task_id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
        ),
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-2",
    )
    assert next_turn.claimed is True
    assert next_turn.run.id != claim.run.id
    assert next_turn.run.turn_attempt == 1
    assert store.get_agent_run(claim.run.id).status == "failed"


def test_recover_failed_native_codex_auth_task_requires_no_effect_or_delivery(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    claim = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-1",
    )
    store.fail_agent_run(
        claim.run.id,
        {"code": "codex_provider_auth_failed: native login unavailable", "retryable": False},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "codex_provider_auth_failed: native login unavailable",
        expected_execution_generation=task.execution_generation,
    )

    assert store.has_failed_native_codex_auth_tasks(channel="dingtalk") is True
    assert store.recover_failed_native_codex_auth_tasks(
        channel="dingtalk", reason="codex_auth_recovered"
    ) == [task_id]
    recovered = store.get_reply_task(task_id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.attempts == 0
    assert recovered.error == "codex_auth_recovered"
    assert recovered.execution_generation != task.execution_generation

    rerun = store.claim_agent_run(
        task_id,
        recovered.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-2",
    )
    assert rerun.claimed is True

    claimed = store.claim_reply_task(task_id)
    assert claimed is not None
    store.fail_reply_task(
        task_id,
        "codex_provider_auth_failed: native login unavailable",
        expected_execution_generation=claimed.execution_generation,
    )
    store.record_sent_reply(claimed.conversation_id, claimed.trigger_message_id, "done")
    assert store.recover_failed_native_codex_auth_tasks(
        channel="dingtalk", reason="codex_auth_recovered"
    ) == []


def test_recover_native_codex_auth_resumes_orphaned_recovery_lock(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    with store._connect() as db:
        db.execute(
            """
            update reply_tasks
            set status='processing', locked_at=datetime('now', '-2 minutes'),
                error='codex_auth_recovered'
            where id=?
            """,
            (task_id,),
        )

    assert store.has_failed_native_codex_auth_tasks(channel="dingtalk") is True
    assert store.recover_failed_native_codex_auth_tasks(
        channel="dingtalk", reason="codex_auth_recovered"
    ) == [task_id]
    recovered = store.get_reply_task(task_id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.execution_generation != task.execution_generation


@pytest.mark.parametrize(
    ("retryable", "side_effect_state"),
    ((False, "none"), (True, "unknown"), (True, "confirmed")),
)
def test_retry_failed_reply_task_rejects_unsafe_runs(
    tmp_path: Path,
    retryable: bool,
    side_effect_state: str,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    if side_effect_state == "none":
        claim = store.claim_agent_run(
            task_id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="",
            owner="worker-1",
        )
    else:
        claim = _claim_audit_run(
            store,
            task_id,
            task.execution_generation,
            owner="worker-1",
        )
    store.fail_agent_run(
        claim.run.id,
        {"code": "runtime_failure", "retryable": retryable},
        owner="worker-1",
        side_effect_state=side_effect_state,
    )
    store.fail_reply_task(
        task_id,
        "runtime_failure",
        expected_execution_generation=task.execution_generation,
    )

    with pytest.raises(ValueError, match="not safely retryable"):
        store.retry_failed_reply_task(
            task_id,
            claim.run.id,
            reason="operator_retry_after_runtime_fix",
        )

    assert store.get_reply_task(task_id).status == "failed"


def test_retry_failed_reply_task_rejects_exact_delivery_ledger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store,
        task_id,
        task.execution_generation,
        owner="worker-1",
    ).run
    store.fail_agent_run(
        run.id,
        {"code": "runtime_failure", "retryable": True},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "runtime_failure",
        expected_execution_generation=task.execution_generation,
    )
    store.record_sent_reply(
        task.conversation_id,
        task.trigger_message_id,
        "delivered",
        send_result_json=json.dumps(
            {"agent_run_id": run.id, "operation_id": run.operation_id},
            separators=(",", ":"),
        ),
    )

    with pytest.raises(ValueError, match="not safely retryable"):
        store.retry_failed_reply_task(
            task_id,
            run.id,
            reason="operator_retry_after_runtime_fix",
        )


def test_requeue_failed_unknown_audit_reconciliation_preserves_generation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store,
        task_id,
        task.execution_generation,
        owner="worker-1",
    ).run
    unknown = store.mark_agent_run_unknown(
        run.id,
        {"code": "codex_result_invalid", "retryable": True},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "codex_result_invalid",
        expected_execution_generation=task.execution_generation,
    )

    resumed = store.requeue_failed_unknown_audit_reconciliation(
        task_id,
        unknown.id,
        reason="manual_unknown_audit_reconciliation",
    )

    assert resumed.status == "pending"
    assert resumed.execution_generation == task.execution_generation
    assert resumed.attempts == task.attempts
    claimed = store.claim_reply_task(task_id)
    assert claimed is not None
    assert store.claim_unknown_agent_run(unknown.id, owner="reconciler").claimed


def test_retry_failed_reply_task_rejects_older_run_and_reopens_safe_latest_audit(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    older = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="audit-attempt-0",
        owner="worker-1",
    )
    store.fail_agent_run(
        older.run.id,
        {"code": "runtime_failure", "retryable": True},
        owner="worker-1",
    )
    latest = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=None,
        operation_id="audit-attempt-1",
        owner="worker-1",
    )
    store.fail_agent_run(
        latest.run.id,
        {"code": "runtime_failure", "retryable": True},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "runtime_failure",
        expected_execution_generation=task.execution_generation,
    )

    with pytest.raises(ValueError, match="not safely retryable"):
        store.retry_failed_reply_task(
            task_id,
            older.run.id,
            reason="operator_retry_after_runtime_fix",
        )

    recovered = store.retry_failed_reply_task(
        task_id,
        latest.run.id,
        reason="operator_retry_after_runtime_fix",
    )

    assert recovered.status == "pending"
    assert recovered.execution_generation == task.execution_generation
    assert recovered.error == "operator_retry_after_runtime_fix"


def test_unknown_agent_run_confirmed_absent_rotates_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing", "call_id": "c1"},
        owner="worker-1",
    )
    claim = store.claim_unknown_agent_run(run.id, owner="reconciler-1")
    assert claim.claimed

    generation = store.resolve_unknown_agent_run_absent(
        run.id,
        task_id,
        code="reconciliation_confirmed_no_effect",
        owner="reconciler-1",
    )

    failed = store.get_agent_run(run.id)
    assert failed.status == "failed"
    assert failed.side_effect_state == "none"
    assert store.get_reply_task(task_id).execution_generation == generation


def test_unknown_reconciliation_claim_is_atomic_and_stale_owner_cannot_append(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing", "call_id": "c1"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )

    winner = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-a",
        lease_seconds=60,
        now="2026-07-29 09:00:02",
    )
    loser = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-b",
        lease_seconds=60,
        now="2026-07-29 09:00:02",
    )

    assert winner.claimed is True
    assert loser.claimed is False
    with pytest.raises(AgentRunLeaseLostError):
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"id": "q1"}},
            owner="reconciler-b",
            now="2026-07-29 09:00:03",
        )


def test_confirmed_reconciliation_atomically_completes_run_and_reply_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )

    completed = store.resolve_unknown_agent_run_confirmed(
        run.id,
        task_id,
        {"outcome": "completed", "summary": "effect confirmed"},
        owner="reconciler-1",
        transcript_end_line=4,
        now="2026-07-29 09:00:03",
    )

    assert completed.status == "completed"
    assert completed.side_effect_state == "confirmed"
    assert store.get_reply_task(task_id).status == "done"


def test_absent_reconciliation_atomically_fails_run_and_rotates_pending_task(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original_generation = store.get_reply_task(task_id).execution_generation
    run = _claim_audit_run(store,
        task_id, original_generation, owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )

    generation = store.resolve_unknown_agent_run_absent(
        run.id,
        task_id,
        code="reconciliation_confirmed_no_effect",
        owner="reconciler-1",
        transcript_end_line=4,
        now="2026-07-29 09:00:03",
    )

    task = store.get_reply_task(task_id)
    assert store.get_agent_run(run.id).status == "failed"
    assert task.status == "pending"
    assert task.force_new_decision is True
    assert task.execution_generation == generation != original_generation
    assert task.error == "reconciliation_confirmed_no_effect"


@pytest.mark.parametrize(
    ("resolution", "run_status", "task_status", "send_status", "rotates"),
    [
        ("confirmed_occurred", "completed", "done", "completed", False),
        ("confirmed_not_occurred", "failed", "pending", "failed", True),
        ("terminate_unrecoverable", "failed", "failed", "blocked", False),
    ],
)
def test_suspended_unknown_run_requires_structured_manual_resolution(
    tmp_path: Path,
    resolution: str,
    run_status: str,
    task_status: str,
    send_status: str,
    rotates: bool,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original_generation = store.get_reply_task(task_id).execution_generation
    run = _claim_audit_run(store,
        task_id, original_generation, owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "reconciliation_needs_human", "retryable": False},
        owner="reconciler-1",
        expected_execution_generation=original_generation,
        next_attempt_at="",
        suspended=True,
        now="2026-07-29 09:00:03",
    )

    resolved = store.resolve_agent_run_manually(
        run.id,
        expected_execution_generation=original_generation,
        resolution=resolution,
        reason="人工核对外部系统后的结构化结论",
        actor="Derek",
        now="2026-07-29 09:00:04",
    )

    persisted_run = store.get_agent_run(run.id)
    task = store.get_reply_task(task_id)
    attempt = store.get_reply_attempt(resolved.attempt_id)
    assert persisted_run is not None and persisted_run.status == run_status
    assert task is not None and task.status == task_status
    assert (task.execution_generation != original_generation) is rotates
    assert attempt is not None and attempt.send_status == send_status
    assert attempt.send_error == f"manual_reconciliation_{resolution}"
    assert "Derek" in attempt.audit_summary
    assert store.list_suspended_unknown_agent_runs(limit=10) == []


def test_manual_resolution_closes_suspended_unknown_run_requeued_pending(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store, task_id, task.execution_generation, owner="worker-1"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
    )
    store.claim_unknown_agent_run(run.id, owner="reconciler-1")
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "reconciliation_needs_human", "retryable": False},
        owner="reconciler-1",
        expected_execution_generation=task.execution_generation,
        next_attempt_at="",
        suspended=True,
    )
    store.requeue_reply_task(
        task.id,
        "unknown_agent_run_reconciliation",
        expected_execution_generation=task.execution_generation,
    )

    resolved = store.resolve_agent_run_manually(
        run.id,
        expected_execution_generation=task.execution_generation,
        resolution="confirmed_occurred",
        reason="外部回读已验证受控操作发生",
        actor="Codex",
    )

    assert resolved.resolution == "confirmed_occurred"
    assert store.get_agent_run(run.id).status == "completed"
    assert store.get_reply_task(task.id).status == "done"


def test_suspended_unknown_run_remains_visible_until_manual_resolution(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id, owner="reconciler-1", now="2026-07-29 09:00:02"
    )
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "reconciliation_needs_human", "retryable": False},
        owner="reconciler-1",
        expected_execution_generation="initial",
        next_attempt_at="",
        suspended=True,
        now="2026-07-29 09:00:03",
    )

    assert [item.id for item in store.list_suspended_unknown_agent_runs(limit=10)] == [
        run.id
    ]
    assert store.list_unknown_agent_runs(now="2026-07-29 09:00:04") == []
    with pytest.raises(ValueError, match="reconciliation required"):
        store.rotate_reply_task_execution_generation(task_id)


def test_manual_reconciliation_closes_failed_run_after_external_effect_is_confirmed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(store, task_id, task.execution_generation, owner="worker").run
    store.fail_agent_run(
        run.id,
        {"code": "codex_result_invalid", "retryable": False},
        owner="worker",
    )
    store.finalize_reply_task_without_run(
        task_id=task_id,
        expected_execution_generation=task.execution_generation,
        task_status="failed",
        task_error="codex_result_invalid",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="codex_result_invalid",
        audit_summary="codex_result_invalid",
        send_status="failed",
        send_error="codex_result_invalid",
        channel=task.channel,
    )

    resolved = store.resolve_agent_run_manually(
        run.id,
        expected_execution_generation=task.execution_generation,
        resolution="confirmed_occurred",
        reason="已从外部系统读回并确认动作完成",
        actor="Derek",
    )

    persisted_run = store.get_agent_run(run.id)
    persisted_task = store.get_reply_task(task_id)
    attempt = store.get_reply_attempt(resolved.attempt_id)
    assert persisted_run is not None and persisted_run.status == "completed"
    assert persisted_run.side_effect_state == "confirmed"
    assert persisted_task is not None and persisted_task.status == "done"
    assert attempt is not None and attempt.send_status == "completed"


def test_manual_reconciliation_cannot_mark_failed_run_without_effect_as_completed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(store, task_id, task.execution_generation, owner="worker").run
    store.fail_agent_run(
        run.id,
        {"code": "codex_result_invalid", "retryable": False},
        owner="worker",
    )
    store.finalize_reply_task_without_run(
        task_id=task_id,
        expected_execution_generation=task.execution_generation,
        task_status="failed",
        task_error="codex_result_invalid",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="codex_result_invalid",
        audit_summary="codex_result_invalid",
        send_status="failed",
        send_error="codex_result_invalid",
        channel=task.channel,
    )

    with pytest.raises(AgentRunLeaseLostError, match="manual reconciliation target is stale"):
        store.resolve_agent_run_manually(
            run.id,
            expected_execution_generation=task.execution_generation,
            resolution="confirmed_not_occurred",
            reason="没有可读回的外部动作",
            actor="Derek",
        )


def test_manual_resolution_rolls_back_run_task_and_attempt_on_insert_failure(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id, owner="reconciler-1", now="2026-07-29 09:00:02"
    )
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "reconciliation_needs_human", "retryable": False},
        owner="reconciler-1",
        expected_execution_generation="initial",
        next_attempt_at="",
        suspended=True,
        now="2026-07-29 09:00:03",
    )
    before_attempts = store.count_reply_attempts()
    monkeypatch.setattr(
        store,
        "_insert_reconciliation_attempt_in_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.IntegrityError("forced attempt failure")
        ),
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced attempt failure"):
        store.resolve_agent_run_manually(
            run.id,
            expected_execution_generation="initial",
            resolution="confirmed_occurred",
            reason="人工确认",
            actor="Derek",
            now="2026-07-29 09:00:04",
        )

    unchanged_run = store.get_agent_run(run.id)
    unchanged_task = store.get_reply_task(task_id)
    assert unchanged_run is not None and unchanged_run.status == "unknown"
    assert unchanged_run.reconciliation_suspended is True
    assert unchanged_task is not None and unchanged_task.status == "processing"
    assert store.count_reply_attempts() == before_attempts


@pytest.mark.parametrize("outcome", ["confirmed", "absent"])
def test_automatic_reconciliation_rolls_back_terminal_state_when_attempt_fails(
    tmp_path: Path, monkeypatch, outcome: str
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id, owner="reconciler-1", now="2026-07-29 09:00:02"
    )
    monkeypatch.setattr(
        store,
        "_insert_reconciliation_attempt_in_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.IntegrityError("forced attempt failure")
        ),
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced attempt failure"):
        if outcome == "confirmed":
            store.resolve_unknown_agent_run_confirmed(
                run.id,
                task_id,
                {"outcome": "completed", "summary": "confirmed"},
                owner="reconciler-1",
                now="2026-07-29 09:00:03",
            )
        else:
            store.resolve_unknown_agent_run_absent(
                run.id,
                task_id,
                code="reconciliation_confirmed_no_effect",
                owner="reconciler-1",
                now="2026-07-29 09:00:03",
            )

    unchanged_run = store.get_agent_run(run.id)
    unchanged_task = store.get_reply_task(task_id)
    assert unchanged_run is not None and unchanged_run.status == "unknown"
    assert unchanged_run.lease_owner == "reconciler-1"
    assert unchanged_task is not None and unchanged_task.status == "processing"
    assert unchanged_task.execution_generation == "initial"
    assert store.count_reply_attempts() == 0


def test_reply_task_state_writes_reject_stale_generation_before_run_creation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    new_generation = store.rotate_reply_task_execution_generation(task_id)

    for operation in (
        lambda: store.requeue_reply_task(
            task_id, "old context failure", expected_execution_generation="initial"
        ),
        lambda: store.fail_reply_task(
            task_id, "old authorization failure", expected_execution_generation="initial"
        ),
        lambda: store.defer_reply_task(
            task_id, "old active run", expected_execution_generation="initial"
        ),
        lambda: store.complete_reply_task(
            task_id, expected_execution_generation="initial"
        ),
    ):
        with pytest.raises(AgentRunLeaseLostError):
            operation()

    task = store.get_reply_task(task_id)
    assert task is not None
    assert task.execution_generation == new_generation
    assert task.status == "pending"
    assert task.locked_at is None
    assert task.error == "execution_generation_rotated"


def test_completed_reconciliation_atomically_finishes_processing_task(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )
    store.resolve_unknown_agent_run_confirmed(
        run.id,
        task_id,
        {
            "outcome": "completed",
            "summary": "effect confirmed",
            "proof": {"observed_state": "completed"},
        },
        owner="reconciler-1",
        now="2026-07-29 09:00:03",
    )

    assert store.get_reply_task(task_id).status == "done"


def test_unknown_reconciliation_must_finish_before_generation_rotation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )
    with pytest.raises(ValueError, match="reconciliation required"):
        store.rotate_reply_task_execution_generation(task_id)

    store.resolve_unknown_agent_run_absent(
        run.id,
        task_id,
        code="reconciliation_confirmed_no_effect",
        owner="reconciler-1",
        now="2026-07-29 09:00:03",
    )

    assert store.get_agent_run(run.id).status == "failed"
    task = store.get_reply_task(task_id)
    assert task.status == "pending"
    assert task.execution_generation != "initial"


def test_generation_switch_revokes_old_run_write_access_and_only_new_run_claims(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    old = _claim_audit_run(store,
        task_id,
        "initial",
        owner="old-worker",
        now="2026-07-29 09:00:00",
    ).run

    new_generation = store.rotate_reply_task_execution_generation(task_id)

    with pytest.raises(AgentRunLeaseLostError):
        store.append_agent_run_event(
            old.id,
            {
                "type": "item.completed",
                "item": {
                    "id": "send-1",
                    "type": "command_execution",
                    "metadata": {"effect": "effectful"},
                },
            },
            owner="old-worker",
            now="2026-07-29 09:00:01",
        )
    with pytest.raises(AgentRunLeaseLostError):
        store.record_agent_execution_receipt(
            old.id,
            receipt_id="receipt-old",
            operation_id="send-1",
            cli="dws",
            command_path="chat message send",
            command_digest="digest-old",
            exit_code=0,
            owner="old-worker",
            now="2026-07-29 09:00:01",
        )
    with pytest.raises(AgentRunLeaseLostError):
        store.complete_agent_run(
            old.id,
            {"outcome": "completed"},
            owner="old-worker",
            side_effect_state="confirmed",
            now="2026-07-29 09:00:01",
        )

    superseded = store.get_agent_run(old.id)
    assert superseded is not None
    assert superseded.status == "failed"
    assert "superseded" in superseded.structured_error_json
    assert superseded.lease_owner == ""
    assert superseded.lease_expires_at == ""

    claimed_task = store.claim_reply_task(
        task_id,
        now="2026-07-29 09:00:01",
    )
    assert claimed_task is not None
    new_claim = _claim_audit_run(store,
        task_id,
        new_generation,
        owner="new-worker",
        now="2026-07-29 09:00:01",
    )
    assert new_claim.claimed is True
    assert new_claim.run.execution_generation == new_generation


def test_rotation_request_keeps_unknown_run_due_and_claimable(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id, "initial", owner="worker-1", now="2026-07-29 09:00:00"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )
    with pytest.raises(ValueError, match="reconciliation required"):
        store.rotate_reply_task_execution_generation(task_id)
    before = store.get_agent_run(run.id)

    due = store.list_unknown_agent_runs(now="2026-07-29 09:00:02")
    claim = store.claim_unknown_agent_run(
        run.id,
        owner="reconciler-1",
        now="2026-07-29 09:00:02",
    )

    assert [item.id for item in due] == [run.id]
    assert claim.claimed is True
    assert claim.run.execution_generation == "initial"
    assert before is not None and before.execution_generation == "initial"


def test_manual_rerun_waits_for_running_unknown_effect(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-universal",
        conversation_title="Universal",
        trigger_message_id="msg-universal",
        trigger_sender="ET",
        trigger_text="请处理",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.append_agent_run_event(
        run.id,
        {
            "type": "item.started",
            "item": {
                "id": "send-1",
                "type": "command_execution",
                "metadata": {"effect": "effectful"},
            },
        },
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )

    with pytest.raises(ValueError, match="reconciliation required"):
        store.enqueue_manual_rerun_reply_task(
            conversation_id="cid-universal",
            conversation_title="Universal",
            single_chat=False,
            trigger_message_id="msg-universal",
            trigger_create_time="2026-07-29 09:00:00",
            trigger_sender="ET",
            trigger_text="请处理",
            trigger_message_json="{}",
            attempt_id=attempt_id,
        )

    task = store.get_reply_task(task_id)
    assert task is not None and task.execution_generation == "initial"
    assert store.get_agent_run(run.id).status == "unknown"


def test_reviewed_rerun_does_not_persist_instruction_before_reconciliation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 09:00:00",
    ).run
    store.append_agent_run_event(
        run.id,
        {
            "type": "item.started",
            "item": {
                "id": "send-1",
                "type": "command_execution",
                "metadata": {"effect": "effectful"},
            },
        },
        owner="worker-1",
        now="2026-07-29 09:00:01",
    )

    with pytest.raises(ValueError, match="reconciliation required"):
        store.record_reviewed_reply_rerun(
            conversation_id="cid-universal",
            conversation_title="Universal",
            single_chat=False,
            trigger_message_id="msg-universal",
            trigger_create_time="2026-07-29 09:00:00",
            trigger_sender="ET",
            trigger_text="请处理",
            trigger_message_json="{}",
            suggested_reply_text="修正版",
            reviewer_feedback="请修正",
        )

    assert store.count_reply_attempts() == 0
    assert store.get_agent_run(run.id).status == "unknown"


def test_unknown_event_append_is_bounded_and_does_not_reload_agent_run(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
    )
    store.claim_unknown_agent_run(run.id, owner="reconciler-1")

    assert (
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"id": "q1"}},
            owner="reconciler-1",
        )
        is None
    )
    monkeypatch.setattr("app.store.MAX_RECONCILIATION_EVENTS", 1)
    with pytest.raises(ValueError, match="reconciliation event limit exceeded"):
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"id": "q2"}},
            owner="reconciler-1",
        )
    monkeypatch.setattr("app.store.MAX_RECONCILIATION_EVENTS", 256)
    with pytest.raises(ValueError, match="agent run event exceeds size limit"):
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"output": "x" * (256 * 1024)}},
            owner="reconciler-1",
        )


def test_reconciliation_event_limit_excludes_direct_run_history(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    for index in range(256):
        store.append_agent_run_event(
            run.id,
            {
                "type": "item.completed",
                "item": {"id": f"direct-{index}"},
                "_ceo_event_scope": "reconciliation",
            },
            owner="worker-1",
        )
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="worker-1",
    )
    store.claim_unknown_agent_run(run.id, owner="reconciler-1")
    monkeypatch.setattr("app.store.MAX_RECONCILIATION_EVENTS", 1)

    store.append_unknown_agent_run_event(
        run.id,
        {"type": "item.completed", "item": {"id": "reconcile-1"}},
        owner="reconciler-1",
    )
    with pytest.raises(ValueError, match="reconciliation event limit exceeded"):
        store.append_unknown_agent_run_event(
            run.id,
            {"type": "item.completed", "item": {"id": "reconcile-2"}},
            owner="reconciler-1",
        )


def test_reconciliation_event_limit_uses_incremental_run_counter(tmp_path: Path):
    statements: list[str] = []

    class TracedStore(AutoReplyStore):
        def _open_connection(self):
            connection = super()._open_connection()
            connection.set_trace_callback(statements.append)
            return connection

    store = TracedStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    store.mark_agent_run_unknown(
        run.id, {"code": "effect_completion_missing"}, owner="worker-1"
    )
    store.claim_unknown_agent_run(run.id, owner="reconciler-1")
    statements.clear()

    store.append_unknown_agent_run_event(
        run.id,
        {"type": "item.completed", "item": {"id": "reconcile-1"}},
        owner="reconciler-1",
    )

    persisted = store.get_agent_run(run.id)
    assert persisted is not None and persisted.reconciliation_event_count == 1
    normalized = [statement.casefold() for statement in statements]
    assert not any(
        "count(*) from agent_run_events" in statement
        and "event_scope='reconciliation'" in statement
        for statement in normalized
    )


def test_legacy_agent_run_events_adds_scope_before_index_and_is_idempotent(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table agent_run_events (
                id integer primary key autoincrement,
                agent_run_id integer not null,
                sequence integer not null,
                event_json text not null,
                event_type text not null default '',
                call_id text not null default '',
                effect_kind text not null default '',
                receipt_operation_id text not null default '',
                created_at text not null default current_timestamp,
                unique(agent_run_id, sequence)
            );
            insert into agent_run_events (
                agent_run_id, sequence, event_json, event_type
            ) values (7, 1, '{"type":"item.completed"}', 'item.completed');
            """
        )

    store = AutoReplyStore(db_path)
    store._initialize()

    with sqlite3.connect(db_path) as db:
        columns = {
            row[1] for row in db.execute("pragma table_info(agent_run_events)")
        }
        indexes = {
            row[1] for row in db.execute("pragma index_list(agent_run_events)")
        }
        preserved = db.execute(
            "select agent_run_id, sequence, event_json, event_scope "
            "from agent_run_events"
        ).fetchall()
        plan = db.execute(
            "explain query plan select count(*) from agent_run_events "
            "where agent_run_id=? and event_scope='reconciliation'",
            (7,),
        ).fetchall()

    assert "event_scope" in columns
    assert "idx_agent_run_events_run_scope" in indexes
    assert preserved == [(7, 1, '{"type":"item.completed"}', "direct")]
    assert any(
        "USING COVERING INDEX idx_agent_run_events_run_scope" in row[3]
        for row in plan
    ), plan


def test_get_agent_run_for_turn_returns_exact_row(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claimed = _claim_audit_run(store, task_id, "initial", owner="worker-1")

    loaded = _get_audit_run(store, task_id, "initial")

    assert loaded == claimed.run
    assert _get_audit_run(store, task_id, "missing") is None


def test_claim_reply_tasks_marks_tasks_processing_atomically(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )

    claimed = store.claim_reply_tasks(limit=1)
    second_claim = store.claim_reply_tasks(limit=1)

    assert len(claimed) == 1
    assert claimed[0].conversation_id == "cid-1"
    assert claimed[0].trigger_message_id == "msg-1"
    assert claimed[0].status == "processing"
    assert claimed[0].attempts == 1
    assert second_claim == []
    assert store.count_reply_tasks(status="pending") == 0
    assert store.count_reply_tasks(status="processing") == 1


def test_peek_reply_tasks_does_not_claim_or_increment_attempts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Derek",
        trigger_text="read this",
        trigger_message_json="{}",
    )

    peeked = store.peek_reply_tasks(limit=1, now="2026-05-13 18:01:00")
    task_id = peeked[0].id

    task = store.get_reply_task(task_id)
    assert task is not None
    assert task.status == "pending"
    assert task.attempts == 0


def test_peek_pending_reconciliation_reply_tasks_prioritizes_unknown_audit(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-normal",
        conversation_title="Normal",
        single_chat=False,
        trigger_message_id="msg-normal",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="Normal task",
    )
    store.enqueue_reply_task(
        conversation_id="cid-reconcile",
        conversation_title="Reconcile",
        single_chat=False,
        trigger_message_id="msg-reconcile",
        trigger_create_time="2026-07-20 10:01:00",
        trigger_sender="Derek",
        trigger_text="Reconciliation task",
    )
    priority = store.claim_reply_task(store.peek_reply_tasks(limit=10)[-1].id)
    assert priority is not None
    run = _claim_audit_run(
        store,
        priority.id,
        priority.execution_generation,
        owner="crashed-audit",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="crashed-audit",
    )
    store.requeue_reply_task(
        priority.id,
        "awaiting_reconciliation",
        expected_execution_generation=priority.execution_generation,
    )

    tasks = store.peek_pending_reconciliation_reply_tasks(limit=10)

    assert [task.id for task in tasks] == [priority.id]


def test_peek_pending_reconciliation_reply_tasks_respects_run_backoff(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store,
        task.id,
        task.execution_generation,
        owner="crashed-audit",
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_missing"},
        owner="crashed-audit",
    )
    claim = store.claim_unknown_agent_run(
        run.id,
        owner="recovery",
        now="2026-08-10 10:00:00",
    )
    assert claim.claimed
    store.defer_unknown_agent_run_reconciliation(
        run.id,
        {"code": "audit_recovery_failed"},
        owner="recovery",
        expected_execution_generation=task.execution_generation,
        next_attempt_at="2026-08-10 10:15:00",
        now="2026-08-10 10:00:01",
    )
    store.requeue_reply_task(
        task.id,
        "awaiting_reconciliation",
        expected_execution_generation=task.execution_generation,
    )

    assert store.peek_pending_reconciliation_reply_tasks(
        limit=10, now="2026-08-10 10:14:59"
    ) == []
    assert [
        item.id
        for item in store.peek_pending_reconciliation_reply_tasks(
            limit=10, now="2026-08-10 10:15:00"
        )
    ] == [task.id]


def test_peek_reply_tasks_pages_after_id_without_claiming(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in range(3):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time=f"2026-05-13 18:00:0{index}",
            trigger_sender="Derek",
            trigger_text=str(index),
            trigger_message_json="{}",
        )

    first_page = store.peek_reply_tasks(
        limit=2, now="2026-05-13 18:01:00"
    )
    second_page = store.peek_reply_tasks(
        limit=2,
        now="2026-05-13 18:01:00",
        after_id=first_page[-1].id,
    )

    assert [task.trigger_message_id for task in first_page] == ["msg-0", "msg-1"]
    assert [task.trigger_message_id for task in second_page] == ["msg-2"]
    assert store.count_reply_tasks(status="pending") == 3


def test_peek_reply_tasks_respects_pending_snapshot_upper_bound(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in range(3):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time=f"2026-05-13 18:00:0{index}",
            trigger_sender="Derek",
            trigger_text=str(index),
            trigger_message_json="{}",
        )
    max_id = store.max_pending_reply_task_id(
        now="2026-05-13 18:01:00",
        channel="dingtalk",
    )
    assert max_id is not None
    store.enqueue_reply_task(
        conversation_id="cid-new",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-new",
        trigger_create_time="2026-05-13 18:00:03",
        trigger_sender="Derek",
        trigger_text="new",
        trigger_message_json="{}",
    )

    snapshot = store.peek_reply_tasks(
        limit=10,
        now="2026-05-13 18:01:00",
        channel="dingtalk",
        max_id=max_id,
    )

    assert [task.trigger_message_id for task in snapshot] == [
        "msg-0",
        "msg-1",
        "msg-2",
    ]


def test_claim_reply_task_claims_only_requested_pending_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in (1, 2):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time=f"2026-05-13 18:00:0{index}",
            trigger_sender="Derek",
            trigger_text=str(index),
            trigger_message_json="{}",
        )
    first, second = store.peek_reply_tasks(limit=2, now="2026-05-13 18:01:00")

    claimed = store.claim_reply_task(second.id, now="2026-05-13 18:01:00")

    assert claimed is not None
    assert claimed.id == second.id
    assert claimed.status == "processing"
    assert claimed.attempts == 1
    unchanged = store.get_reply_task(first.id)
    assert unchanged is not None
    assert unchanged.status == "pending"
    assert unchanged.attempts == 0


def test_claim_reply_tasks_waits_until_available_at(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
        available_at="2026-05-13 17:05:00",
        error="waiting_fast_path_unread_backoff",
    )

    before = store.claim_reply_tasks(limit=1, now="2026-05-13 17:04:59")
    after = store.claim_reply_tasks(limit=1, now="2026-05-13 17:05:00")

    assert before == []
    assert len(after) == 1
    assert after[0].status == "processing"
    assert after[0].available_at == ""
    assert after[0].error == "waiting_fast_path_unread_backoff"


def test_requeue_reply_task_can_delay_next_claim(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1, now="2026-05-13 17:00:00")

    store.requeue_reply_task(
        claimed[0].id,
        "temporary failure",
        expected_execution_generation=claimed[0].execution_generation,
        available_at="2026-05-13 17:01:00",
    )

    before = store.claim_reply_tasks(limit=1, now="2026-05-13 17:00:59")
    after = store.claim_reply_tasks(limit=1, now="2026-05-13 17:01:00")

    assert before == []
    assert len(after) == 1
    assert after[0].attempts == 2
    assert after[0].available_at == ""
    assert after[0].error == "temporary failure"


def test_complete_reply_task_marks_generation_bound_task_done(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)[0]
    store.complete_reply_task(
        claimed.id,
        expected_execution_generation=claimed.execution_generation,
    )

    tasks = store.list_reply_tasks(limit=1)
    assert tasks[0].status == "done"
    assert tasks[0].error == ""


def test_settle_failed_reply_task_without_replay_records_skipped_terminal_attempt(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Private chat",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="time-sensitive fragment",
        channel="dingtalk",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "provider unavailable",
        expected_execution_generation=task.execution_generation,
    )

    attempt_id = store.settle_failed_reply_task_without_replay(
        task.id,
        reason="Later live conversation made the fragment stale.",
        audit_summary="Read-only reconciliation found no delivery or side effect.",
    )

    settled = store.list_reply_tasks(limit=1)[0]
    attempt = store.get_reply_attempt(attempt_id)
    assert settled.status == "done"
    assert settled.recovery_code == "settled_without_replay"
    assert attempt is not None
    assert attempt.action == "no_reply"
    assert attempt.send_status == "skipped"
    assert attempt.send_error == "settled_without_replay"


def test_settle_failed_reply_task_without_replay_rejects_delivery_receipt(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Private chat",
        single_chat=True,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="hello",
        channel="dingtalk",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "provider unavailable",
        expected_execution_generation=task.execution_generation,
    )
    store.record_sent_reply(
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="delivered",
        send_result_json="{}",
    )

    with pytest.raises(ValueError, match="external reconciliation"):
        store.settle_failed_reply_task_without_replay(
            task.id,
            reason="stale",
            audit_summary="read-only reconciliation",
        )


def test_list_reply_tasks_filters_statuses_newest_first(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    store.enqueue_reply_task(
        conversation_id="cid-2",
        conversation_title="HR管理",
        single_chat=False,
        trigger_message_id="msg-2",
        trigger_create_time="2026-05-13 18:01:00",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 再看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)
    store.complete_reply_task(
        claimed[0].id,
        expected_execution_generation=claimed[0].execution_generation,
    )

    tasks = store.list_reply_tasks(statuses=("pending", "processing", "failed"))

    assert [task.trigger_message_id for task in tasks] == ["msg-2"]


def test_requeue_reply_task_keeps_attempt_count_for_retry(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)

    store.requeue_reply_task(
        claimed[0].id,
        "temporary dws auth failure",
        expected_execution_generation=claimed[0].execution_generation,
    )
    reclaimed = store.claim_reply_tasks(limit=1)

    assert reclaimed[0].id == claimed[0].id
    assert reclaimed[0].attempts == 2
    assert reclaimed[0].error == "temporary dws auth failure"


def test_defer_reply_task_for_authorization_preserves_claim_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)

    store.defer_reply_task_for_authorization(
        claimed[0].id,
        "authorization required",
        expected_execution_generation=claimed[0].execution_generation,
    )
    reclaimed = store.claim_reply_tasks(limit=1)

    assert reclaimed[0].id == claimed[0].id
    assert reclaimed[0].attempts == 2
    assert reclaimed[0].error == "authorization required"


def test_create_and_claim_okr_review_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )

    claimed = store.claim_okr_review_requests(limit=1)

    assert [item.id for item in claimed] == [request_id]
    assert claimed[0].status == "processing"


def test_recreating_okr_review_request_requeues_failed_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_failed(request_id, "source unavailable")

    recreated_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"processed":{"okrRows":[]}}',
    )

    assert recreated_id == request_id
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "pending"
    assert loaded.error == ""
    assert loaded.codex_session_id == ""
    assert json.loads(loaded.okr_source_json)["processed"]["okrRows"] == []


def test_marking_okr_review_request_discarded_keeps_it_out_of_the_claim_queue(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )

    store.mark_okr_review_request_discarded(request_id, "not assigned to principal")

    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "discarded"
    assert loaded.error == "not assigned to principal"
    assert store.claim_okr_review_requests(limit=1) == []


def test_recreating_okr_review_request_does_not_requeue_done_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_done(request_id, codex_session_id="session-1")

    recreated_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"processed":{"okrRows":[]}}',
    )

    assert recreated_id == request_id
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "done"
    assert loaded.codex_session_id == "session-1"
    assert json.loads(loaded.okr_source_json)["objectives"] == []


def test_recreating_okr_review_request_does_not_reset_processing_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    claimed = store.claim_okr_review_requests(limit=1)

    recreated_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"processed":{"okrRows":[]}}',
    )

    assert [item.id for item in claimed] == [request_id]
    assert recreated_id == request_id
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "processing"
    assert json.loads(loaded.okr_source_json)["objectives"] == []


def test_reset_recoverable_okr_review_requests_requeues_stale_processing(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    claimed = store.claim_okr_review_requests(limit=1)[0]
    assert store.acquire_codex_session_lock("cid-1", f"okr_review:{request_id}")
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update okr_review_requests set updated_at=datetime('now', '-31 minutes') where id=?",
            (request_id,),
        )

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert [request.id for request in recovered] == [claimed.id]
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "pending"
    assert loaded.error == ""
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1")


def test_reset_recoverable_okr_review_requests_keeps_fresh_processing(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    store.claim_okr_review_requests(limit=1)

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert recovered == []
    assert store.get_okr_review_request(request_id).status == "processing"


def test_reset_recoverable_okr_review_requests_requeues_stale_lock_failure(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="再查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_failed(request_id, "codex session locked: cid-1")

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert [request.id for request in recovered] == [request_id]
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "pending"
    assert loaded.error == ""


def test_reset_recoverable_okr_review_requests_keeps_fresh_lock_failure(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="再查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_failed(request_id, "codex session locked: cid-1")
    assert store.acquire_codex_session_lock("cid-1", "okr_review:other")

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert recovered == []
    assert store.get_okr_review_request(request_id).status == "failed"


def test_record_okr_review_run_and_items(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="韩露",
        trigger_message_id="msg-1",
        trigger_sender="韩露",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    run_id = store.record_okr_review_run(
        request_id=request_id,
        codex_session_id="session-1",
        codex_transcript_start_line=1,
        codex_transcript_end_line=10,
        envelope_json='{"kind":"okr_review"}',
        audit_tool_events_json='[]',
        audit_summary="审核完成。",
    )
    item_id = store.record_okr_review_item(
        request_id=request_id,
        objective_title="O",
        objective_weight=1.0,
        kr_title="KR",
        kr_weight=0.5,
        item_json='{"kr_title":"KR"}',
    )
    store.mark_okr_review_request_done(request_id, codex_session_id="session-1")

    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "done"
    assert run_id > 0
    assert item_id > 0


def test_create_okr_review_request_requires_source_json(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(TypeError):
        store.create_okr_review_request(
            conversation_id="cid-1",
            conversation_title="韩露",
            trigger_message_id="msg-1",
            trigger_sender="韩露",
            trigger_sender_user_id="user-1",
            trigger_text="帮我审核 OKR",
            period_label="2026 Q2",
            period_start="2026-04-01",
            period_end="2026-06-30",
        )


def test_record_okr_review_run_requires_audit_fields(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(TypeError):
        store.record_okr_review_run(
            request_id=1,
            codex_session_id="session-1",
            codex_transcript_start_line=1,
            codex_transcript_end_line=10,
            audit_tool_events_json="[]",
        )


def test_record_okr_review_item_requires_item_json(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(TypeError):
        store.record_okr_review_item(
            request_id=1,
            objective_title="O",
            objective_weight=1.0,
            kr_title="KR",
            kr_weight=0.5,
        )


def test_reset_codex_sessions_clears_conversation_mapping_only(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", False, "session-1")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_session_id="session-1",
        codex_transcript_start_line=3,
        codex_transcript_end_line=9,
    )

    cleared = store.reset_codex_sessions()

    assert cleared == 1
    assert store.get_codex_session_id("cid-1") is None
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 3
    assert attempt.codex_transcript_end_line == 9


def test_record_reply_attempt_extracts_memory_write_events(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    memory_output = {
        "structured_content": {
            "result": json.dumps(
                {
                    "ok": True,
                    "episode_uuid": "episode-1",
                    "processing_status": "completed",
                }
            )
        }
    }

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="记一下这个项目口径",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "memory_write",
                    "call_id": "call-1",
                    "input": json.dumps({"data": "stable fact"}),
                    "output": json.dumps(memory_output),
                }
            ]
        ),
    )

    events = store.list_memory_write_events_for_attempt(attempt_id)

    assert len(events) == 1
    assert events[0].status == "written"
    assert events[0].memory_episode_id == "episode-1"


def test_record_reply_attempt_extracts_memory_write_output_from_tool_output(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    memory_output = {
        "result": json.dumps(
            {
                "ok": True,
                "episode_uuid": "episode-2",
                "processing_status": "pending",
            }
        )
    }

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="记一下这个项目口径",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "memory_write",
                    "call_id": "call-1",
                    "input": json.dumps({"data": "stable fact"}),
                },
                {
                    "event_type": "response_item",
                    "tool": "tool_output",
                    "call_id": "call-1",
                    "output": "Wall time: 1.1 seconds\nOutput:\n"
                    + json.dumps(memory_output),
                },
            ]
        ),
    )

    events = store.list_memory_write_events_for_attempt(attempt_id)

    assert len(events) == 1
    assert events[0].status == "written"
    assert events[0].memory_episode_id == "episode-2"


def test_record_reply_attempt_ignores_tool_search_memory_write_mentions(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="查一下记忆",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "tool_search_call",
                    "call_id": "call-1",
                    "input": json.dumps({"query": "memory_connector memory_write"}),
                }
            ]
        ),
    )

    assert store.list_memory_write_events_for_attempt(attempt_id) == []


def test_reply_attempt_migration_backfills_codex_session_from_conversation(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table conversations (
                conversation_id text primary key,
                title text not null,
                single_chat integer not null,
                codex_session_id text
            );
            create table reply_attempts (
                id integer primary key autoincrement,
                conversation_id text not null,
                conversation_title text not null,
                trigger_message_id text not null,
                trigger_sender text not null,
                trigger_text text not null,
                action text not null,
                sensitivity_kind text not null,
                codex_reason text not null default '',
                draft_reply_text text not null default '',
                audit_documents_json text not null default '[]',
                audit_tool_events_json text not null default '[]',
                audit_summary text not null default '',
                final_reply_text text not null default '',
                permission_action text not null default '',
                permission_reason text not null default '',
                send_status text not null,
                send_error text not null default '',
                retry_count integer not null default 0,
                reviewed_at text,
                reviewer_feedback text not null default '',
                corrected_reply_text text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );
            insert into conversations (
                conversation_id, title, single_chat, codex_session_id
            ) values ('cid-1', 'Friday', 0, 'session-1');
            insert into reply_attempts (
                conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind, send_status
            ) values (
                'cid-1', 'Friday', 'msg-1', 'Xiaomin',
                '@Alex Chen 这个怎么处理？', 'send_reply', 'general', 'sent'
            );
            """
        )

    store = AutoReplyStore(db_path)
    attempt = store.get_reply_attempt(1)

    assert attempt is not None
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 0
    assert attempt.codex_transcript_end_line == 0


def test_reply_attempt_migration_normalizes_authorization_status_to_failed(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table reply_attempts (
                id integer primary key autoincrement,
                conversation_id text not null,
                conversation_title text not null,
                trigger_message_id text not null,
                trigger_sender text not null,
                trigger_text text not null,
                action text not null,
                sensitivity_kind text not null,
                codex_reason text not null default '',
                draft_reply_text text not null default '',
                audit_documents_json text not null default '[]',
                audit_tool_events_json text not null default '[]',
                audit_summary text not null default '',
                final_reply_text text not null default '',
                permission_action text not null default '',
                permission_reason text not null default '',
                send_status text not null,
                send_error text not null default '',
                retry_count integer not null default 0,
                reviewed_at text,
                reviewer_feedback text not null default '',
                corrected_reply_text text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );
            insert into reply_attempts (
                conversation_id, conversation_title, trigger_message_id,
                trigger_sender, trigger_text, action, sensitivity_kind, send_status
            ) values (
                'cid-1', 'Friday', 'msg-1', 'Xiaomin',
                '@Alex Chen 这个怎么处理？', 'send_reply', 'general',
                'needs_authorization'
            );
            """
        )

    store = AutoReplyStore(db_path)
    attempt = store.get_reply_attempt(1)

    assert attempt is not None
    assert attempt.send_status == "failed"


def test_seen_messages_are_deduplicated(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.has_seen("msg-1") is False
    assert store.mark_seen("msg-1", "cid-1") is True
    assert store.has_seen("msg-1") is True
    assert store.mark_seen("msg-1", "cid-1") is False


def test_records_sent_reply_and_error(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "收到（by明哥分身）",
        send_result_json='{"result":{"processQueryKey":"key-1"}}',
        recall_key="key-1",
    )
    store.record_error("cid-1", "msg-2", "codex_json", "invalid json")
    sent_reply = store.get_sent_reply("cid-1", "msg-1")

    assert store.count_sent_replies() == 1
    assert sent_reply is not None
    assert sent_reply.recall_key == "key-1"
    assert sent_reply.recall_status == ""
    assert store.count_errors() == 1


def test_records_sent_reply_recall_result(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "收到（by明哥分身）", recall_key="key-1")
    sent_reply = store.get_sent_reply("cid-1", "msg-1")

    assert sent_reply is not None

    store.update_sent_reply_recall(
        sent_reply.id,
        recall_status="recalled",
        recall_error="",
    )
    updated = store.get_sent_reply("cid-1", "msg-1")

    assert updated is not None
    assert updated.recall_status == "recalled"
    assert updated.recalled_at is not None


def test_feedback_pressure_counts_unanswered_replies_since_last_feedback(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply(
        "cid-1",
        "old-before-feedback",
        "旧回复",
        feedback_token="token-old",
    )
    store.record_sent_reply(
        "cid-1",
        "old-unanswered",
        "旧回复",
        feedback_token="token-1",
    )
    store.record_sent_reply(
        "cid-1",
        "recent-unanswered",
        "近回复",
        feedback_token="token-2",
    )
    store.record_sent_reply(
        "cid-2",
        "other-conversation",
        "其他会话",
        feedback_token="token-3",
    )
    store.upsert_feedback_event(
        key="event-old",
        feedback_token="token-old",
        rating="up",
        received_at="2026-06-01 12:00:00",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-05-30 12:00:00", "old-before-feedback"),
        )
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-06-02 12:00:00", "old-unanswered"),
        )
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-06-09 12:00:00", "recent-unanswered"),
        )
        db.execute(
            "update sent_replies set sent_at=? where trigger_message_id=?",
            ("2026-06-02 12:00:00", "other-conversation"),
        )

    stats = store.feedback_pressure_stats(
        "cid-1",
        now_utc="2026-06-12 12:00:00",
    )

    assert stats.unanswered_since_last_feedback == 2
    assert stats.unanswered_older_than_7_days == 1
    assert stats.unanswered_older_than_10_days == 1


def test_list_sent_replies_with_feedback_tokens_for_conversation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "无反馈")
    store.record_sent_reply("cid-1", "msg-2", "旧回复", feedback_token="token-1")
    store.record_sent_reply("cid-2", "msg-3", "其他会话", feedback_token="token-2")
    store.record_sent_reply("cid-1", "msg-4", "新回复", feedback_token="token-3")

    replies = store.list_sent_replies_with_feedback_tokens_for_conversation(
        "cid-1",
        limit=10,
    )

    assert [reply.trigger_message_id for reply in replies] == ["msg-4", "msg-2"]


def test_list_sent_replies_waiting_for_feedback_events_filters_answered_tokens(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "无反馈")
    store.record_sent_reply("cid-1", "msg-2", "已有本地反馈", feedback_token="token-1")
    store.record_sent_reply("cid-1", "msg-3", "等待反馈同步", feedback_token="token-2")
    store.upsert_feedback_event(
        key="event-1",
        feedback_token="token-1",
        rating="useful",
        received_at="2026-06-18T08:00:00.000Z",
    )

    replies = store.list_sent_replies_waiting_for_feedback_events(limit=10)

    assert [reply.trigger_message_id for reply in replies] == ["msg-3"]


def test_reply_attempt_tracing_and_feedback_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="先收敛问题",
        codex_session_id="session-1",
        codex_transcript_start_line=2,
        codex_transcript_end_line=7,
        audit_documents_json='[{"path":"面试/岗位画像.md"}]',
        audit_tool_events_json='[{"tool":"exec_command","command":"rg 岗位"}]',
        audit_summary="查看岗位画像后判断需要先收敛问题。",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="先收敛问题（by明哥分身）",
        permission_action="allow",
        permission_reason="",
        send_status="sent",
        retry_count=1,
    )
    store.record_reply_feedback(
        attempt_id,
        feedback="语气可以，但需要更具体",
        corrected_reply_text="先明确负责人和时间点。",
    )

    attempt = store.get_reply_attempt(attempt_id)

    assert store.count_reply_attempts() == 1
    assert attempt is not None
    assert attempt.conversation_title == "技术部"
    assert attempt.trigger_message_id == "msg-1"
    assert attempt.action == "send_reply"
    assert attempt.audit_documents_json == '[{"path":"面试/岗位画像.md"}]'
    assert attempt.audit_tool_events_json == '[{"tool":"exec_command","command":"rg 岗位"}]'
    assert attempt.audit_summary == "查看岗位画像后判断需要先收敛问题。"
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 2
    assert attempt.codex_transcript_end_line == 7
    assert attempt.final_reply_text == "先收敛问题（by明哥分身）"
    assert attempt.send_status == "sent"
    assert attempt.retry_count == 1
    assert attempt.reviewed_at is not None
    assert attempt.reviewer_feedback == "语气可以，但需要更具体"
    assert attempt.corrected_reply_text == "先明确负责人和时间点。"


def test_reply_attempt_records_oa_metadata(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="审批通知",
        trigger_message_id="msg-1",
        trigger_sender="工作通知",
        trigger_text="[Ding]张静提醒您审批他的录用申请",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="oa approval handled by dingtalk-oa-approval skill",
        codex_session_id="session-1",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_url="https://aflow.dingtalk.com/dingtalk/mobile/query/formService#/detail?procInstId=proc-1",
        oa_action="退回",
        oa_remark="请补充试用期考核标准和完整面试记录后再提交。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="skipped",
    )

    loaded = store.get_reply_attempt(attempt_id)

    assert loaded is not None
    assert loaded.action == "oa_approval"
    assert loaded.oa_process_instance_id == "proc-1"
    assert loaded.oa_task_id == "task-1"
    assert loaded.oa_url.startswith("https://aflow.dingtalk.com/")
    assert loaded.oa_action == "退回"
    assert loaded.oa_remark == "请补充试用期考核标准和完整面试记录后再提交。"
    assert loaded.oa_action_result_json == '{"errcode":0,"errmsg":"ok"}'


def test_reply_attempt_records_calendar_response_metadata(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Mina",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="[日程]",
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="calendar invite handled",
        calendar_event_id="event-1",
        calendar_response_status="accepted",
        calendar_response_result_json='{"success":true}',
        send_status="skipped",
    )

    loaded = store.get_reply_attempt(attempt_id)

    assert loaded is not None
    assert loaded.calendar_event_id == "event-1"
    assert loaded.calendar_response_status == "accepted"
    assert loaded.calendar_response_result_json == '{"success":true}'


def test_record_reply_attempt_for_trigger_reuses_existing_attempt_id(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    first_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="system_or_notification_message",
        send_status="skipped",
    )
    store.update_reply_attempt(
        first_id,
        final_reply_text="旧回复",
        send_error="no_reply",
        retry_count=2,
    )

    second_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="先按A方案走",
        codex_session_id="session-1",
        audit_documents_json='[{"title":"chat"}]',
        audit_tool_events_json='[{"tool":"dws"}]',
        audit_summary="已重新判断，需要回复。",
        send_status="pending",
    )

    attempt = store.get_reply_attempt(first_id)

    assert second_id == first_id
    assert store.count_reply_attempts() == 1
    assert attempt is not None
    assert attempt.action == "send_reply"
    assert attempt.codex_reason == "direct ask"
    assert attempt.draft_reply_text == "先按A方案走"
    assert attempt.codex_session_id == "session-1"
    assert attempt.audit_documents_json == '[{"title":"chat"}]'
    assert attempt.audit_tool_events_json == '[{"tool":"dws"}]'
    assert attempt.audit_summary == "已重新判断，需要回复。"
    assert attempt.final_reply_text == ""
    assert attempt.send_status == "pending"
    assert attempt.send_error == ""
    assert attempt.retry_count == 0


def test_record_reply_attempt_for_trigger_does_not_overwrite_sent_reply_attempt(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    first_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="先按A方案走",
        send_status="pending",
    )
    store.update_reply_attempt(
        first_id,
        final_reply_text="先按A方案走",
        send_status="sent",
    )
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "先按A方案走",
        send_result_json='{"success":true}',
        feedback_token="token-1",
    )

    second_id = store.record_reply_attempt_for_trigger(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="stop_with_error",
        sensitivity_kind="general",
        codex_reason="provider failed",
        send_status="pending",
    )

    first_attempt = store.get_reply_attempt(first_id)
    second_attempt = store.get_reply_attempt(second_id)

    assert second_id != first_id
    assert store.count_reply_attempts() == 2
    assert first_attempt is not None
    assert first_attempt.action == "send_reply"
    assert first_attempt.send_status == "sent"
    assert first_attempt.final_reply_text == "先按A方案走"
    assert second_attempt is not None
    assert second_attempt.action == "stop_with_error"
    assert second_attempt.send_status == "pending"


def test_get_latest_reply_attempt_for_trigger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="dry_run",
    )

    attempt = store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")

    assert first_id != second_id
    assert attempt is not None
    assert attempt.id == second_id
    assert store.get_latest_reply_attempt_for_trigger("cid-1", "missing") is None


def test_history_query_skips_search_text_materialization_without_search():
    query, args = AutoReplyStore._history_items_query(
        send_statuses=None,
        query_text="",
        kinds=None,
        reply_channels=None,
        object_types=("reply", "meeting", "task"),
        created_since="",
    )

    assert query.count("iif(?1,") == 4
    assert args == [False, "reply", "meeting", "task"]


def test_history_query_has_indexes_for_correlated_reply_lookups(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with sqlite3.connect(store.path) as db:
        index_names = {
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            ).fetchall()
        }
        query, args = store._history_items_query(
            send_statuses=None,
            query_text="",
            kinds=None,
            reply_channels=None,
            object_types=("replay", "wechat", "approval", "task", "meeting"),
            created_since="",
        )
        plan = [
            str(row[3])
            for row in db.execute(
                f"explain query plan {query} order by created_at desc, source_id desc, kind desc limit 1",
                args,
            ).fetchall()
        ]

    assert {
        "idx_reply_attempts_oa_history",
        "idx_reply_attempts_trigger_history",
        "idx_reply_attempts_current_trigger",
        "idx_sent_replies_history",
        "idx_work_summary_inputs_updated",
    } <= index_names
    assert not any("scan process_attempts" in detail.lower() for detail in plan)
    assert not any("scan sent" in detail.lower() for detail in plan)


def test_history_treats_superseded_blocked_reply_as_skipped(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    blocked_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="stop_with_error",
        sensitivity_kind="general",
        send_status="blocked",
    )
    sent_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    blocked_items = store.list_history_items(send_statuses=("blocked",))
    skipped_items = store.list_history_items(send_statuses=("skipped",))

    assert blocked_id != sent_id
    assert [item.source_id for item in blocked_items] == []
    assert [item.source_id for item in skipped_items] == [blocked_id]


def test_history_groups_approval_retries_under_latest_attempt_for_filters_and_counts(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    reviewed_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-1:first",
        trigger_sender="Derek OA",
        trigger_text="付款申请",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="review",
        send_status="needs_human",
    )
    failed_retry_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-1:own-remark",
        trigger_sender="Derek OA",
        trigger_text="付款申请",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="review",
        send_status="failed",
    )
    completed_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-2:first",
        trigger_sender="Derek OA",
        trigger_text="合同申请",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-2",
        oa_task_id="task-2",
        oa_action="review",
        send_status="completed",
    )

    items = store.list_history_items(object_types=("approval",))
    failed_items = store.list_history_items(
        object_types=("approval",), send_statuses=("failed",)
    )
    needs_human_items = store.list_history_items(
        object_types=("approval",), send_statuses=("needs_human",)
    )

    assert len({reviewed_id, failed_retry_id, completed_id}) == 3
    assert [item.source_id for item in items] == [completed_id, failed_retry_id]
    assert [item.status for item in items] == ["completed", "failed"]
    assert [item.source_id for item in failed_items] == [failed_retry_id]
    assert needs_human_items == []
    assert store.count_history_items(object_types=("approval",)) == 2
    assert (
        store.count_history_items(
            object_types=("approval",), send_statuses=("failed",)
        )
        == 1
    )
    assert [
        item.source_id
        for item in store.list_history_items(
            limit=1, offset=0, object_types=("approval",)
        )
    ] == [completed_id]
    assert [
        item.source_id
        for item in store.list_history_items(
            limit=1, offset=1, object_types=("approval",)
        )
    ] == [failed_retry_id]


def test_history_approval_group_breaks_created_at_ties_with_larger_id(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    older_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-tie:first",
        trigger_sender="Derek OA",
        trigger_text="采购申请",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-tie",
        send_status="commented",
    )
    newer_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-tie:retry",
        trigger_sender="Derek OA",
        trigger_text="采购申请",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-tie",
        send_status="failed",
    )
    with store._connect() as db:
        db.execute(
            "update reply_attempts set created_at=? where id in (?, ?)",
            ("2026-08-19 00:00:00", older_id, newer_id),
        )

    items = store.list_history_items(object_types=("approval",))

    assert [item.source_id for item in items] == [newer_id]
    assert items[0].status == "failed"


def test_history_keeps_blocked_side_effects_visible_after_terminal_reply(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-memory", "msg-memory", "reply delivered")
    memory_id = store.record_reply_attempt(
        conversation_id="cid-memory",
        conversation_title="Strategy",
        trigger_message_id="msg-memory",
        trigger_sender="Derek",
        trigger_text="Remember this",
        action="memory_write",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(memory_id, send_error="memory backend unavailable")
    oa_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="Approvals",
        trigger_message_id="msg-oa",
        trigger_sender="System",
        trigger_text="Pending approval",
        action="oa_approval",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(oa_id, send_error="oa_task_not_current_user")
    store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="Approvals",
        trigger_message_id="msg-oa",
        trigger_sender="System",
        trigger_text="Pending approval",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )

    blocked_items = store.list_history_items(send_statuses=("blocked",))

    assert {item.source_id for item in blocked_items} == {memory_id, oa_id}
    assert store.count_recoverable_blocked_reply_attempts() == 2


def test_history_preserves_superseded_meeting_failure(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-1",
        title="招聘站会",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-07-22T10:12:44+00:00",
        eligible_at="2026-07-22T10:22:44+00:00",
        status="pending",
    )
    failed_run_id = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-session-failed",
        decision_json="{}",
        audit_summary="首次生成失败",
        status="retry",
        error="Codex did not return a valid MeetingAlignmentDecision",
    )
    sent_run_id = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-session-sent",
        decision_json='{"action":"send"}',
        audit_summary="再次生成完成",
        status="ready_to_send",
        error="",
    )
    store.update_meeting_alignment_job(
        job_id,
        status="sent",
        target_kind="group",
        target_title="HR",
        final_message="会后对齐已发送。",
        send_result_json='{"status":"sent"}',
    )

    failed_items = store.list_history_items(send_statuses=("failed",))
    skipped_items = store.list_history_items(send_statuses=("skipped",))
    sent_items = store.list_history_items(send_statuses=("sent",))

    assert failed_run_id != sent_run_id
    assert [item.source_id for item in failed_items] == [failed_run_id]
    assert [item.source_id for item in skipped_items] == []
    assert [item.source_id for item in sent_items] == [sent_run_id]


def test_lists_reply_attempts_newest_first_with_limit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="HR",
        trigger_message_id="msg-2",
        trigger_sender="HR",
        trigger_text="张三转正怎么看？",
        action="no_reply",
        sensitivity_kind="internal_personnel",
        codex_reason="privacy",
    )

    all_attempts = store.list_reply_attempts()
    attempts = store.list_reply_attempts(limit=1)
    offset_attempts = store.list_reply_attempts(limit=1, offset=1)

    assert [attempt.id for attempt in all_attempts] == [second_id, first_id]
    assert [attempt.id for attempt in attempts] == [second_id]
    assert [attempt.id for attempt in offset_attempts] == [first_id]
    assert attempts[0].conversation_title == "HR"
    assert attempts[0].send_status == "pending"
    assert first_id != second_id


def test_lists_reply_attempts_since_timestamp(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old_id = store.record_reply_attempt(
        conversation_id="cid-old",
        conversation_title="Old",
        trigger_message_id="msg-old",
        trigger_sender="Old",
        trigger_text="old",
        action="send_reply",
        sensitivity_kind="general",
    )
    new_id = store.record_reply_attempt(
        conversation_id="cid-new",
        conversation_title="New",
        trigger_message_id="msg-new",
        trigger_sender="New",
        trigger_text="new",
        action="send_reply",
        sensitivity_kind="general",
    )
    with store._connect() as db:
        db.execute(
            "update reply_attempts set created_at=? where id=?",
            ("2026-06-04 00:00:00", old_id),
        )
        db.execute(
            "update reply_attempts set created_at=? where id=?",
            ("2026-06-05 00:00:00", new_id),
        )

    attempts = store.list_reply_attempts_since("2026-06-04 12:00:00")

    assert [attempt.id for attempt in attempts] == [new_id]


def test_lists_reviewed_reply_attempts_for_optimization(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    unreviewed_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
    )
    reviewed_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="Claire",
        trigger_message_id="msg-2",
        trigger_sender="Claire",
        trigger_text="明哥上会啦",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="收到，我现在进会。",
    )
    store.update_reply_attempt(
        reviewed_id,
        final_reply_text="收到，我现在进会。（by明哥分身）",
        send_status="sent",
    )
    store.record_reply_feedback(
        reviewed_id,
        feedback="不能代 Alex 声称正在进会",
        corrected_reply_text="我让明哥本人看一下。（by明哥分身）",
    )

    attempts = store.list_reviewed_reply_attempts()

    assert [attempt.id for attempt in attempts] == [reviewed_id]
    assert attempts[0].reviewer_feedback == "不能代 Alex 声称正在进会"
    assert attempts[0].corrected_reply_text == "我让明哥本人看一下。（by明哥分身）"
    assert unreviewed_id != reviewed_id


def test_lists_errors_newest_first_with_limit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "codex", "invalid json")
    store.record_error("cid-2", "msg-2", "send", "authorization required")

    all_errors = store.list_errors()
    errors = store.list_errors(limit=1)
    offset_errors = store.list_errors(limit=1, offset=1)

    assert [error.kind for error in all_errors] == ["send", "codex"]
    assert len(errors) == 1
    assert errors[0].conversation_id == "cid-2"
    assert errors[0].message_id == "msg-2"
    assert errors[0].kind == "send"
    assert errors[0].detail == "authorization required"
    assert errors[0].created_at
    assert len(offset_errors) == 1
    assert offset_errors[0].kind == "codex"


def test_lists_run_delta_records_after_ids(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )
    store.record_sent_reply("cid-1", "msg-1", "收到（by明哥分身）")
    store.record_error("cid-1", "msg-1", "codex", "invalid json")

    baseline_attempt_id = store.max_reply_attempt_id()
    baseline_sent_reply_id = store.max_sent_reply_id()
    baseline_error_id = store.max_error_id()

    second_attempt_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="BA",
        trigger_message_id="msg-2",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 需要看一下吗？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="pending",
    )
    store.record_sent_reply("cid-2", "msg-2", "可以（by明哥分身）")
    store.record_error("cid-2", "msg-2", "read_messages", "dws timeout")

    assert baseline_attempt_id == first_attempt_id
    assert baseline_sent_reply_id == 1
    assert baseline_error_id == 1
    assert [attempt.id for attempt in store.list_reply_attempts_after(baseline_attempt_id)] == [
        second_attempt_id
    ]
    assert [
        sent.trigger_message_id for sent in store.list_sent_replies_after(baseline_sent_reply_id)
    ] == ["msg-2"]
    assert [error.kind for error in store.list_errors_after(baseline_error_id)] == [
        "read_messages"
    ]


def test_org_user_profile_cache_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.upsert_org_user_profile(
        user_id="user-1",
        name="张三",
        open_dingtalk_id="open-1",
        manager_user_id="manager-1",
        department_ids={"dept-1", "dept-2"},
        title="产品负责人",
        manager_name="李四",
        department_names={"产品部", "售前解决方案部"},
        org_labels=["职务: 产品负责人", "岗位: 管理层"],
        has_subordinate=True,
    )

    profile = store.get_org_user_profile("user-1")

    assert profile is not None
    assert profile.user_id == "user-1"
    assert profile.name == "张三"
    assert profile.open_dingtalk_id == "open-1"
    assert profile.manager_user_id == "manager-1"
    assert profile.manager_name == "李四"
    assert profile.department_ids == {"dept-1", "dept-2"}
    assert profile.department_names == {"产品部", "售前解决方案部"}
    assert profile.title == "产品负责人"
    assert profile.org_labels == ["职务: 产品负责人", "岗位: 管理层"]
    assert profile.has_subordinate is True
    assert store.find_org_user_by_open_dingtalk_id("open-1").user_id == "user-1"
    assert [user.user_id for user in store.find_org_users_by_name("张三")] == ["user-1"]
    assert store.list_org_user_ids() == ["user-1"]


def test_org_cache_metadata_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.set_current_user_id("principal-user-1")
    store.set_hr_department_ids({"hr-dept-1"})

    assert store.get_current_user_id() == "principal-user-1"
    assert store.get_hr_department_ids() == {"hr-dept-1"}


def test_service_state_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.set_service_state("dws_upgrade_checked_date", "2026-05-25")
    loaded = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert loaded.get_service_state("dws_upgrade_checked_date") == "2026-05-25"


def test_codex_capacity_pause_is_shared_and_expires(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    now = datetime.fromisoformat("2026-08-12T10:00:00+00:00")
    retry_at = "2026-08-12T10:30:00+00:00"

    assert store.open_codex_capacity_pause(retry_at=retry_at, now=now) is True
    assert store.open_codex_capacity_pause(retry_at=retry_at, now=now) is False
    assert store.active_codex_capacity_pause(now=now) == retry_at
    assert (
        store.active_codex_capacity_pause(
            now=datetime.fromisoformat("2026-08-12T10:30:00+00:00")
        )
        == ""
    )


def test_codex_capacity_failure_count_persists_until_successful_clear(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first = datetime.fromisoformat("2026-08-12T10:00:00+00:00")

    assert store.codex_capacity_failure_count() == 0
    assert store.open_codex_capacity_pause(
        retry_at="2026-08-12T10:30:00+00:00",
        now=first,
    )
    assert store.codex_capacity_failure_count() == 1
    assert store.open_codex_capacity_pause(
        retry_at="2026-08-12T11:31:00+00:00",
        now=datetime.fromisoformat("2026-08-12T10:31:00+00:00"),
    )
    assert store.codex_capacity_failure_count() == 2

    store.clear_codex_capacity_pause()

    assert store.codex_capacity_failure_count() == 0
    assert store.active_codex_capacity_pause(
        now=datetime.fromisoformat("2026-08-12T10:32:00+00:00")
    ) == ""


def test_resolve_errors_keeps_history_with_a_resolution(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error(None, None, "consumer", "temporary failure")
    [error] = store.list_errors()

    assert store.resolve_errors([error.id], resolution="recovered by queue retry") == 1
    resolved = store.list_errors()[0]

    assert resolved.resolved_at
    assert resolved.resolution == "recovered by queue retry"


def test_redact_and_resolve_error_replaces_unsafe_historical_detail(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error(None, None, "follow_up", "outbound message body")
    [error] = store.list_errors()

    assert store.redact_and_resolve_error(
        error.id,
        detail="direct recipient rejected; message not sent",
        resolution="target is no longer available",
    )

    [resolved] = store.list_errors()
    assert resolved.detail == "direct recipient rejected; message not sent"
    assert resolved.resolved_at
    assert resolved.resolution == "target is no longer available"


def test_resolve_errors_recovered_by_later_terminal_reply_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Management",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="Please handle this.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="completed",
    )
    store.update_reply_attempt(attempt_id, send_status="completed")

    assert store.resolve_errors_recovered_by_reply_attempts() == 1
    [resolved] = store.list_errors()
    assert resolved.resolved_at
    assert resolved.resolution == "recovered by later terminal reply attempt"


def test_resolve_errors_recovered_by_reply_attempts_keeps_unrelated_errors_open(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")
    store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="Management",
        trigger_message_id="msg-2",
        trigger_sender="Mina",
        trigger_text="Please handle this.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="completed",
    )

    assert store.resolve_errors_recovered_by_reply_attempts() == 0
    [error] = store.list_errors()
    assert error.resolved_at == ""


def test_completed_reply_task_resolves_trigger_error_and_closed_blocked_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Management",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-12 12:00:00",
        trigger_sender="Mina",
        trigger_text="Please handle this.",
    )
    task = store.claim_reply_task(1)
    assert task is not None
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Management",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="Please handle this.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(attempt_id, send_error="external material unavailable")
    store.complete_reply_task(
        task.id,
        expected_execution_generation=task.execution_generation,
    )

    assert store.resolve_errors_recovered_by_completed_reply_tasks() == 1
    assert store.resolve_closed_blocked_reply_attempts() == 1

    [error] = store.list_errors()
    attempt = store.get_reply_attempt(attempt_id)
    assert error.resolution == "recovered by completed reply task"
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == ""
    assert attempt.permission_action == REPLY_ATTEMPT_CLOSED_AFTER_REVIEW


def test_closed_blocked_attempt_keeps_active_or_unknown_work_open(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Management",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-12 12:00:00",
        trigger_sender="Mina",
        trigger_text="Please handle this.",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Management",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="Please handle this.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="blocked",
    )

    assert store.resolve_closed_blocked_reply_attempts() == 0
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "blocked"


def test_resolve_unattributed_errors_after_quiet_period_keeps_history(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("", "", "producer_loop_error", "temporary DWS outage")
    with store._connect() as db:
        db.execute(
            "update errors set created_at='2026-08-12 00:00:00' where kind='producer_loop_error'"
        )

    resolved = store.resolve_unattributed_errors_after_quiet_period(
        now=datetime.fromisoformat("2026-08-12T05:00:00+00:00")
    )

    assert resolved == 1
    [error] = store.list_errors()
    assert error.resolved_at
    assert "healthy observation window" in error.resolution


def test_resolve_unattributed_errors_after_quiet_period_keeps_trigger_error_open(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "send", "delivery not confirmed")
    with store._connect() as db:
        db.execute("update errors set created_at='2026-08-12 00:00:00'")

    assert store.resolve_unattributed_errors_after_quiet_period(
        now=datetime.fromisoformat("2026-08-12T05:00:00+00:00")
    ) == 0
    assert store.list_errors()[0].resolved_at == ""


def test_resolve_inactive_trigger_errors_after_quiet_period_keeps_history(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")
    with store._connect() as db:
        db.execute("update errors set created_at='2026-08-12 00:00:00'")

    resolved = store.resolve_inactive_trigger_errors_after_quiet_period(
        now=datetime.fromisoformat("2026-08-12T05:00:00+00:00")
    )

    assert resolved == 1
    [error] = store.list_errors()
    assert error.resolved_at
    assert "no active workflow" in error.resolution


def test_resolve_inactive_trigger_errors_keeps_active_recovery_open(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Management",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-12 00:00:00",
        trigger_sender="Mina",
        trigger_text="Please handle this.",
    )
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")
    with store._connect() as db:
        db.execute("update errors set created_at='2026-08-12 00:00:00'")

    assert store.resolve_inactive_trigger_errors_after_quiet_period(
        now=datetime.fromisoformat("2026-08-12T05:00:00+00:00")
    ) == 0
    assert store.list_errors()[0].resolved_at == ""


def test_missing_service_state_returns_none(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.get_service_state("missing") is None


def test_list_oa_attempt_history_returns_newest_first(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 群",
        trigger_message_id="msg-1",
        trigger_sender="Derek",
        trigger_text="审批 1",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="退回",
        draft_reply_text="请补材料",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="退回",
        oa_remark="请补材料",
        send_status="commented",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 群",
        trigger_message_id="msg-2",
        trigger_sender="Derek",
        trigger_text="审批 2",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="同意",
        draft_reply_text="同意",
        oa_process_instance_id="proc-1",
        oa_task_id="task-2",
        oa_action="同意",
        oa_remark="同意",
        send_status="skipped",
    )
    store.record_reply_attempt(
        conversation_id="cid-other",
        conversation_title="其他",
        trigger_message_id="msg-3",
        trigger_sender="Derek",
        trigger_text="审批 3",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="同意",
        draft_reply_text="同意",
        oa_process_instance_id="proc-2",
        send_status="skipped",
    )

    history = store.list_oa_attempt_history("proc-1")
    histories = store.list_oa_attempt_histories(
        ["proc-1", "proc-2", "proc-1", "missing", ""]
    )

    assert [attempt.id for attempt in history] == [second_id, first_id]
    assert store.list_oa_attempt_history("") == []
    assert {
        process_id: [attempt.id for attempt in attempts]
        for process_id, attempts in histories.items()
    } == {
        "proc-1": [second_id, first_id],
        "proc-2": [second_id + 1],
        "missing": [],
    }


def test_backfill_oa_audit_metadata_recovers_completed_agent_scan_attempt(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger_message_id = "oa-pending:proc-1:revision-1"
    assert store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id=trigger_message_id,
        trigger_create_time="2026-08-06T05:41:00+00:00",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id=trigger_message_id,
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="已审阅并评论要求补充材料。",
        send_status="needs_human",
    )

    assert store.backfill_oa_audit_metadata() == 1

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_url.endswith("procInstId=proc-1&taskId=task-1")
    assert attempt.oa_action == "review"


def test_setup_wizard_step_state_round_trips(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.upsert_setup_wizard_step(
        step_id="mcp",
        status="done",
        summary="Codex config contains memory_connector",
        manual_confirmed_by="",
    )
    row = store.get_setup_wizard_step("mcp")

    assert row["step_id"] == "mcp"
    assert row["status"] == "done"
    assert row["summary"] == "Codex config contains memory_connector"
    assert row["manual_confirmed_by"] == ""
    assert row["updated_at"]


def test_setup_wizard_event_history_round_trips(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    event_id = store.record_setup_wizard_event(
        step_id="mcp",
        action_id="setup_mcp",
        status="done",
        summary="wrote config",
        evidence_json='{"codex_config": "/tmp/config.toml"}',
        stdout_excerpt="setup-memory-connector codex_config=/tmp/config.toml",
        stderr_excerpt="",
    )
    events = store.list_setup_wizard_events("mcp")

    assert event_id > 0
    assert len(events) == 1
    assert events[0]["step_id"] == "mcp"
    assert events[0]["action_id"] == "setup_mcp"
    assert events[0]["evidence_json"] == '{"codex_config": "/tmp/config.toml"}'


def test_setup_wizard_running_event_is_not_finished(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.record_setup_wizard_event(
        step_id="mcp",
        action_id="setup_mcp",
        status="running",
    )
    events = store.list_setup_wizard_events("mcp")

    assert events[0]["started_at"]
    assert events[0]["finished_at"] == ""


def test_setup_wizard_running_event_ignores_legacy_finished_default(tmp_path):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table setup_wizard_events (
                id integer primary key autoincrement,
                step_id text not null,
                action_id text not null,
                status text not null,
                summary text not null default '',
                evidence_json text not null default '{}',
                stdout_excerpt text not null default '',
                stderr_excerpt text not null default '',
                started_at text not null default current_timestamp,
                finished_at text not null default current_timestamp
            );
            """
        )
    store = AutoReplyStore(db_path)

    store.record_setup_wizard_event(
        step_id="mcp",
        action_id="setup_mcp",
        status="running",
    )

    events = store.list_setup_wizard_events("mcp")
    assert events[0]["finished_at"] == ""


def test_setup_wizard_steps_list_has_stable_tie_breaker(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(step_id="mcp", status="done", summary="ok")
    store.upsert_setup_wizard_step(step_id="preflight", status="done", summary="ok")
    with sqlite3.connect(tmp_path / "worker.sqlite3") as db:
        db.execute("update setup_wizard_steps set updated_at='2026-06-12 12:00:00'")

    rows = store.list_setup_wizard_steps()

    assert [row["step_id"] for row in rows] == ["mcp", "preflight"]


def test_reply_attempt_round_trips_mail_action_state(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="HR",
        trigger_message_id="msg-1",
        trigger_sender="Alan",
        trigger_text="审批并回复邮件",
        action="send_reply",
        sensitivity_kind="general",
        mail_mailbox="derek@example.com",
        mail_message_id="mail-1",
        mail_subject="Re: 评奖结果",
        mail_reply_text="确认无误，可以发布。",
    )
    store.update_reply_attempt(
        attempt_id,
        mail_action_result_json='{"success": true}',
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.mail_mailbox == "derek@example.com"
    assert attempt.mail_message_id == "mail-1"
    assert attempt.mail_subject == "Re: 评奖结果"
    assert attempt.mail_reply_text == "确认无误，可以发布。"
    assert attempt.mail_action_result_json == '{"success": true}'


def test_sent_reply_exists_matches_exact_conversation_and_trigger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "Sent")

    assert store.sent_reply_exists("cid-1", "msg-1") is True
    assert store.sent_reply_exists("cid-1", "msg-other") is False
    assert store.sent_reply_exists("cid-other", "msg-1") is False


def test_channel_login_claim_requires_owner_to_finalize_and_persists_safe_state(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)

    claimed, reserved = store.claim_channel_login_request(
        channel="dingtalk",
        reason_code="live_probe_auth_failed",
        now=now,
        suppression_seconds=3600,
        reservation_owner="owner-1",
    )

    assert claimed is True
    assert reserved["status"] == "starting"
    assert (
        store.update_claimed_channel_login_request(
            channel="dingtalk",
            reservation_owner="owner-2",
            state={"status": "running", "pid": 99},
        )
        is False
    )
    assert store.update_claimed_channel_login_request(
        channel="dingtalk",
        reservation_owner="owner-1",
        state={"status": "failed", "exited_at": now.isoformat()},
    )
    state = json.loads(
        store.get_service_state("channel_login_request:dingtalk") or "{}"
    )
    assert state == {
        "checked_at": now.isoformat(),
        "exited_at": now.isoformat(),
        "reason_code": "live_probe_auth_failed",
        "started_at": now.isoformat(),
        "status": "failed",
    }


def test_removed_runtime_tables_modules_and_apis_are_absent(tmp_path: Path) -> None:
    from app.worker import DingTalkAutoReplyWorker

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._connect() as db:
        tables = {
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }

    assert "agent_runs" in tables
    assert "universal_plan_executions" not in tables
    assert "universal_action_executions" not in tables
    assert store.get_service_state("dws_auth_backup") is None
    assert not hasattr(store, "create_universal_plan_execution")
    assert not hasattr(store, "claim_universal_action_execution")
    assert not hasattr(DingTalkAutoReplyWorker, "execute_universal_send_reply")
    assert importlib.util.find_spec("app.universal_consumer") is None
    assert importlib.util.find_spec("app.universal_plan") is None


def test_removed_runtime_migrates_unreferenced_history_before_drop(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    assert store.enqueue_reply_task(
        conversation_id="cid-legacy",
        conversation_title="Legacy history",
        single_chat=False,
        trigger_message_id="msg-legacy",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="Handle this task",
    )
    task_id = store.claim_reply_tasks(limit=1)[0].id
    existing_attempt_id = store.record_reply_attempt(
        conversation_id="cid-legacy",
        conversation_title="Legacy history",
        trigger_message_id="msg-legacy",
        trigger_sender="Derek",
        trigger_text="Handle this task",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    with store._connect() as db:
        db.executescript(
            """
            create table universal_plan_executions (
                execution_scope_id text primary key,
                reply_task_id integer not null
            );
            create table universal_action_executions (
                execution_id text primary key,
                execution_scope_id text not null,
                attempt_id integer,
                action_kind text not null,
                status text not null,
                error text not null default '',
                result_json text not null default '',
                created_at text not null,
                updated_at text not null
            );
            """
        )
        db.execute(
            "insert into universal_plan_executions values (?, ?)",
            ("scope-1", task_id),
        )
        db.executemany(
            """
            insert into universal_action_executions values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "action-existing",
                    "scope-1",
                    existing_attempt_id,
                    "send_reply",
                    "succeeded",
                    "",
                    '{"receipt":"existing"}',
                    "2026-07-20 10:01:00",
                    "2026-07-20 10:02:00",
                ),
                (
                    "action-missing",
                    "scope-1",
                    None,
                    "oa_approval",
                    "failed",
                    "legacy failure",
                    '{"outcome":"failed"}',
                    "2026-07-20 10:03:00",
                    "2026-07-20 10:04:00",
                ),
            ),
        )
        db.execute(
            "insert or replace into service_state (key, value) values (?, ?)",
            ("dws_auth_backup", '{"archive":"removed"}'),
        )

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    migrated = AutoReplyStore(db_path)

    attempts = migrated.list_reply_attempts(limit=20)
    assert len(attempts) == 2
    historical = next(attempt for attempt in attempts if attempt.id != existing_attempt_id)
    assert historical.action == "oa_approval"
    assert historical.send_status == "failed"
    assert historical.send_error == "legacy failure"
    assert historical.audit_summary == '{"outcome":"failed"}'
    with migrated._connect() as db:
        tables = {
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
    assert "universal_plan_executions" not in tables
    assert "universal_action_executions" not in tables
    assert migrated.get_service_state("dws_auth_backup") is None


_LEGACY_EFFECT_SUCCESS_CASES: dict[str, tuple[str, dict[str, object]]] = {
    "send_reply": (
        "sent",
        {"action_kind": "send_reply", "outcome": "delivered"},
    ),
    "ask_clarifying_question": (
        "sent",
        {"action_kind": "ask_clarifying_question", "outcome": "delivered"},
    ),
    "oa_approval": (
        "completed",
        {
            "action": "同意",
            "outcome": "applied",
            "process_instance_id": "process-1",
            "task_id": "task-1",
        },
    ),
    "mail_reply": ("sent", {"success": True}),
    "calendar_response": ("calendar", {"success": True}),
    "dws_markdown_document_reply": (
        "document",
        {
            "node_id": "node-1",
            "url": "https://alidocs.dingtalk.com/i/nodes/node-1",
            "delivery": {"messageId": "message-1"},
        },
    ),
    "dws_message_reaction": ("reacted", {"reactionId": "reaction-1"}),
    "queue_okr_review": (
        "completed",
        {
            "action_kind": "queue_okr_review",
            "outcome": "okr_review_queued_and_acknowledged",
        },
    ),
    "memory_write": (
        "completed",
        {
            "episode_uuid": "episode-1",
            "processing_status": "completed",
            "duplicate": False,
        },
    ),
}


def test_removed_runtime_migrates_every_action_status_with_terminal_semantics(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    actions = (
        "send_reply",
        "ask_clarifying_question",
        "oa_approval",
        "mail_reply",
        "calendar_response",
        "dws_markdown_document_reply",
        "dws_message_reaction",
        "queue_okr_review",
        "memory_write",
        "no_reply",
        "handoff_to_human",
        "blocked",
        "stop_with_error",
    )
    legacy_statuses = ("succeeded", "failed", "blocked", "unknown", "not_started")
    succeeded_status = {
        **{
            action: status
            for action, (status, _) in _LEGACY_EFFECT_SUCCESS_CASES.items()
        },
        "no_reply": "skipped",
        "handoff_to_human": "blocked",
        "blocked": "blocked",
        "stop_with_error": "failed",
    }
    expected: dict[str, str] = {}
    with store._connect() as db:
        db.executescript(
            """
            create table universal_plan_executions (
                execution_scope_id text primary key,
                reply_task_id integer not null
            );
            create table universal_action_executions (
                execution_id text primary key,
                execution_scope_id text not null,
                attempt_id integer,
                action_kind text not null,
                status text not null,
                error text not null default '',
                result_json text not null default '',
                created_at text not null,
                updated_at text not null
            );
            """
        )
        for action in actions:
            for legacy_status in legacy_statuses:
                key = f"{action}-{legacy_status}"
                db.execute(
                    """
                    insert into reply_tasks (
                        conversation_id, conversation_title, single_chat,
                        trigger_message_id, trigger_create_time, trigger_sender,
                        trigger_text
                    ) values ('cid-migration', 'Migration', 0, ?,
                              '2026-07-20 09:00:00', 'Derek', ?)
                    """,
                    (key, key),
                )
                task_id = int(db.execute("select last_insert_rowid()").fetchone()[0])
                scope = f"scope-{key}"
                db.execute(
                    "insert into universal_plan_executions values (?, ?)",
                    (scope, task_id),
                )
                db.execute(
                    """
                    insert into universal_action_executions
                    values (?, ?, null, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"execution-{key}",
                        scope,
                        action,
                        legacy_status,
                        f"legacy-{legacy_status}" if legacy_status != "succeeded" else "",
                            json.dumps(
                                _LEGACY_EFFECT_SUCCESS_CASES.get(action, ("", {}))[1]
                            )
                            if legacy_status == "succeeded"
                            else "",
                        "2026-07-20 10:00:00",
                        "2026-07-20 10:01:00",
                    ),
                )
                expected[key] = (
                    succeeded_status[action]
                    if legacy_status == "succeeded"
                    else "blocked"
                    if legacy_status in {"blocked", "unknown"}
                    else "failed"
                )

    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    migrated = AutoReplyStore(db_path)
    actual = {
        attempt.trigger_message_id: attempt.send_status
        for attempt in migrated.list_reply_attempts(limit=200)
    }

    assert actual == expected
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    AutoReplyStore(db_path)
    assert len(migrated.list_reply_attempts(limit=200)) == len(expected)


@pytest.mark.parametrize(
    ("action", "success_receipt", "expected_status"),
    [
        (action, receipt, status)
        for action, (status, receipt) in _LEGACY_EFFECT_SUCCESS_CASES.items()
    ],
)
def test_removed_runtime_requires_action_specific_success_receipt(
    action: str,
    success_receipt: dict[str, object],
    expected_status: str,
) -> None:
    success = AutoReplyStore._removed_runtime_attempt_status(
        action=action,
        legacy_status="succeeded",
        result_json=json.dumps(success_receipt, ensure_ascii=False),
    )
    unknown = AutoReplyStore._removed_runtime_attempt_status(
        action=action,
        legacy_status="succeeded",
        result_json='{"unexpected":"value"}',
    )

    assert success == (expected_status, "")
    for error_receipt in (
        {"error": "failed"},
        {"error": {"code": "failed"}},
        {"success": False},
    ):
        assert AutoReplyStore._removed_runtime_attempt_status(
            action=action,
            legacy_status="succeeded",
            result_json=json.dumps(error_receipt),
        ) == ("failed", "migrated_explicit_execution_failure")
    assert unknown == ("failed", "migrated_unverified_execution_receipt")


@pytest.mark.parametrize(
    "receipt",
    [
        {
            "tool_events": [
                {
                    "type": "item.completed",
                    "item": {
                        "call_id": "call-1",
                        "metadata": {"effect": "effectful"},
                    },
                }
            ]
        },
        {
            "receipt": {
                "receipt_id": "receipt-1",
                "operation_id": "operation-1",
                "completed": True,
                "persisted": True,
                "safe_to_confirm": True,
            }
        },
    ],
)
def test_removed_runtime_accepts_completed_effect_evidence(
    receipt: dict[str, object],
) -> None:
    assert AutoReplyStore._removed_runtime_attempt_status(
        action="agent_action",
        legacy_status="succeeded",
        result_json=json.dumps(receipt),
    ) == ("completed", "")


def test_removed_runtime_does_not_collapse_duplicate_effect_starts() -> None:
    started = {
        "type": "item.started",
        "item": {
            "call_id": "call-1",
            "metadata": {"effect": "effectful"},
        },
    }
    receipt = {
        "tool_events": [
            started,
            started,
            {**started, "type": "item.completed"},
        ]
    }

    assert AutoReplyStore._removed_runtime_attempt_status(
        action="agent_action",
        legacy_status="succeeded",
        result_json=json.dumps(receipt),
    ) == ("failed", "migrated_unverified_execution_receipt")


def test_removed_runtime_structured_block_is_not_completed() -> None:
    assert AutoReplyStore._removed_runtime_attempt_status(
        action="oa_approval",
        legacy_status="succeeded",
        result_json=json.dumps(
            {
                "action": "同意",
                "outcome": "blocked",
                "process_instance_id": "process-1",
                "task_id": "task-1",
            },
            ensure_ascii=False,
        ),
    ) == ("blocked", "migrated_structured_execution_block")


def test_removed_runtime_migration_starts_immediate_transaction(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    statements: list[str] = []
    with store._connect() as db:
        db.set_trace_callback(statements.append)
        AutoReplyStore._migrate_removed_runtime(db)

    assert any(statement.strip().upper() == "BEGIN IMMEDIATE" for statement in statements)


def test_removed_runtime_migration_rolls_back_every_change_on_failure(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-rollback",
        conversation_title="Rollback",
        single_chat=False,
        trigger_message_id="msg-rollback",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="rollback",
    )
    task = store.get_reply_task_for_message("cid-rollback", "msg-rollback")
    assert task is not None
    with store._connect() as db:
        db.executescript(
            """
            create table universal_plan_executions (
                execution_scope_id text primary key,
                reply_task_id integer not null
            );
            create table universal_action_executions (
                execution_id text primary key,
                execution_scope_id text not null,
                attempt_id integer,
                action_kind text not null,
                status text not null,
                error text not null default '',
                result_json text not null default '',
                created_at text not null,
                updated_at text not null
            );
            create trigger reject_auth_cleanup before delete on service_state
            when old.key='dws_auth_backup'
            begin
                select raise(abort, 'forced migration failure');
            end;
            """
        )
        db.execute(
            "insert into universal_plan_executions values ('scope-rollback', ?)",
            (task.id,),
        )
        db.execute(
            """
            insert into universal_action_executions values (
                'action-rollback', 'scope-rollback', null, 'send_reply',
                'succeeded', '', '{"receipt":{"completed":true}}',
                '2026-07-20 10:01:00', '2026-07-20 10:02:00'
            )
            """
        )
        db.execute(
            "insert or replace into service_state (key, value) values (?, ?)",
            ("dws_auth_backup", "present"),
        )

    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(sqlite3.IntegrityError, match="forced migration failure"):
            AutoReplyStore._migrate_removed_runtime(db)
        tables = {
            row["name"]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
        attempts = db.execute(
            "select count(*) from reply_attempts where trigger_message_id='msg-rollback'"
        ).fetchone()[0]
        auth_state = db.execute(
            "select value from service_state where key='dws_auth_backup'"
        ).fetchone()

    assert "universal_plan_executions" in tables
    assert "universal_action_executions" in tables
    assert attempts == 0
    assert auth_state is not None


def test_recover_orphaned_processing_reply_tasks_is_generation_aware(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_ids = []
    for index in range(3):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title=f"Conversation {index}",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time="2026-07-30 09:00:00",
            trigger_sender="Derek",
            trigger_text="handle this",
        )
        task_ids.append(store.claim_reply_tasks(1)[0].id)

    running_task = store.get_reply_task(task_ids[1])
    unknown_task = store.get_reply_task(task_ids[2])
    assert running_task is not None and unknown_task is not None
    _claim_audit_run(store,
        running_task.id,
        running_task.execution_generation,
        owner="running-worker",
    )
    unknown_run = _claim_audit_run(store,
        unknown_task.id,
        unknown_task.execution_generation,
        owner="unknown-worker",
    ).run
    store.mark_agent_run_unknown(
        unknown_run.id,
        {"code": "effect_completion_missing"},
        owner="unknown-worker",
    )

    recovered = store.recover_orphaned_processing_reply_tasks(limit=10)

    assert [task.id for task in recovered] == [task_ids[0]]
    assert store.get_reply_task(task_ids[0]).status == "pending"
    assert store.get_reply_task(task_ids[0]).attempts == 0
    assert store.get_reply_task(task_ids[1]).status == "processing"
    assert store.get_reply_task(task_ids[2]).status == "processing"


def test_service_restart_recovers_running_no_effect_consumer_turn(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    store.upsert_conversation(task.conversation_id, task.conversation_title, False, "session-a")
    assert store.acquire_codex_session_lock(task.conversation_id, "stopped-worker")
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="stopped-worker",
        lease_seconds=3600,
    ).run

    recovered = store.recover_no_effect_agent_runs_after_service_restart()

    assert [item.id for item in recovered] == [task.id]
    recovered_task = store.get_reply_task(task.id)
    recovered_run = store.get_agent_run(run.id)
    assert recovered_task is not None and recovered_task.status == "pending"
    assert recovered_task.execution_generation != task.execution_generation
    assert recovered_task.error == "service_restart_before_effect"
    assert recovered_run is not None and recovered_run.status == "failed"
    assert json.loads(recovered_run.structured_error_json)["code"] == (
        "service_restart_before_effect"
    )
    assert store.get_codex_session_id(task.conversation_id) == "session-a"
    assert store.acquire_codex_session_lock(task.conversation_id, "next-worker") is True
    next_claim = store.claim_agent_run(
        task.id,
        recovered_task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="next-worker",
    )
    assert next_claim.claimed is True
    assert next_claim.run.id != run.id


def test_service_restart_keeps_unknown_or_effectful_agent_turns_for_recovery(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="stopped-worker"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": False},
        owner="stopped-worker",
    )

    assert store.recover_no_effect_agent_runs_after_service_restart() == []
    assert store.get_reply_task(task.id).status == "processing"
    assert store.get_agent_run(run.id).status == "unknown"


def test_service_restart_releases_unknown_audit_reconciliation_lease(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="stopped-worker"
    ).run
    store.mark_agent_run_unknown(
        run.id,
        {"code": "effect_completion_unknown", "retryable": True},
        owner="stopped-worker",
    )
    claim = store.claim_unknown_agent_run(run.id, owner="stopped-reconciler")
    assert claim.claimed

    released = store.release_unknown_audit_reconciliation_leases_after_service_restart()

    assert [item.id for item in released] == [run.id]
    persisted = store.get_agent_run(run.id)
    assert persisted is not None
    assert persisted.status == "unknown"
    assert persisted.lease_owner == ""
    assert persisted.lease_expires_at == ""
    assert persisted.reconciliation_next_attempt_at == ""
    assert [item.id for item in store.list_unknown_agent_runs()] == [run.id]


def test_service_restart_requeues_running_effectful_audit_for_reconciliation(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(store, task.id, task.execution_generation, owner="stopped-worker").run
    started = {
        "type": "item.started",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    completed = {
        "type": "item.completed",
        "item": {
            "id": "write-1",
            "type": "mcp_tool_call",
            "metadata": {"effect": "effectful"},
        },
    }
    store.append_agent_run_event(run.id, started, owner="stopped-worker")
    store.append_agent_run_event(run.id, completed, owner="stopped-worker")

    recovered = store.recover_effectful_audit_runs_after_service_restart()

    assert [item.id for item in recovered] == [task.id]
    recovered_task = store.get_reply_task(task.id)
    recovered_run = store.get_agent_run(run.id)
    assert recovered_task is not None and recovered_task.status == "pending"
    assert recovered_task.execution_generation == task.execution_generation
    assert recovered_task.error == "service_restart_effect_reconciliation"
    assert recovered_run is not None and recovered_run.status == "unknown"
    assert recovered_run.side_effect_state == "unknown"
    assert json.loads(recovered_run.structured_error_json)["code"] == (
        "service_restart_effect_requires_reconciliation"
    )


def test_service_restart_resumes_completed_turn_without_replaying_it(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="stopped-worker",
    ).run
    store.complete_agent_run(
        run.id,
        {"outcome": "no_action", "summary": "Nothing remains."},
        owner="stopped-worker",
    )

    recovered = store.resume_completed_agent_turns_after_service_restart()

    assert [item.id for item in recovered] == [task.id]
    recovered_task = store.get_reply_task(task.id)
    recovered_run = store.get_agent_run(run.id)
    assert recovered_task is not None and recovered_task.status == "pending"
    assert recovered_task.execution_generation == task.execution_generation
    assert recovered_task.error == "service_restart_after_completed_turn"
    assert recovered_run is not None and recovered_run.status == "completed"


def test_recover_interrupted_wechat_read_only_decision_has_precise_reason(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        channel="wechat",
        conversation_id="wechat-conversation",
        conversation_title="Wechat contact",
        single_chat=True,
        trigger_message_id="wechat-message",
        trigger_create_time="2026-08-07 01:00:00",
        trigger_sender="contact",
        trigger_text="Can you reply?",
    )
    [task] = store.claim_reply_tasks(1, channel="wechat")

    store.mark_wechat_read_only_decision_started(
        task.id,
        expected_execution_generation=task.execution_generation,
    )
    recovered = store.recover_orphaned_processing_reply_tasks(limit=10)

    assert [item.id for item in recovered] == [task.id]
    recovered_task = store.get_reply_task(task.id)
    assert recovered_task is not None
    assert recovered_task.status == "pending"
    assert recovered_task.error == "interrupted_read_only_decision"


def test_requeue_failed_work_summary_input_is_scoped_to_failed_record(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    input_id = store.enqueue_work_summary_input(
        "local_file",
        "file:reference",
        "{}",
    )
    [claimed] = store.claim_work_summary_inputs(1)
    store.mark_work_summary_input_failed(claimed.id, "validation failed")

    assert store.requeue_failed_work_summary_input(
        input_id,
        "retry_after_reviewed_root_cause_fix",
    )
    with store._connect() as db:
        row = db.execute(
            "select status, attempts, error, available_at from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
    assert dict(row) == {
        "status": "pending",
        "attempts": 1,
        "error": "retry_after_reviewed_root_cause_fix",
        "available_at": "",
    }
    assert not store.requeue_failed_work_summary_input(input_id, "again")


def test_terminal_work_summary_input_resolves_its_own_error(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    input_id = store.enqueue_work_summary_input("local_file", "file:reference", "{}")
    [claimed] = store.claim_work_summary_inputs(1)
    store.mark_work_summary_input_discarded(claimed.id, "no usable material")
    store.record_error(
        "work_summary_input",
        str(input_id),
        "task_agent",
        "validation failed",
    )

    assert store.resolve_errors_recovered_by_terminal_work_summary_inputs() == 1
    with store._connect() as db:
        row = db.execute("select resolved_at from errors").fetchone()
    assert row is not None
    assert row["resolved_at"]


def test_current_schema_reopens_and_repairs_old_runtime_attempt_execution_shape(
    tmp_path: Path,
):
    db_path = tmp_path / "current-version-old-runtime-attempt.sqlite3"
    store = AutoReplyStore(db_path)
    with store._connect() as db:
        db.execute("drop index idx_runtime_attempt_active_lease")
        db.execute("drop trigger trg_runtime_attempt_generalized_lease_insert")
        db.execute("drop trigger trg_runtime_attempt_generalized_lease_update")
        db.execute("drop trigger trg_runtime_attempt_lineage_insert")
        db.execute("drop trigger trg_runtime_attempt_lineage_update")
        db.execute("drop trigger trg_runtime_attempt_lineage_immutable")
        for column in (
            "validation_result_schema_id",
            "validation_retry_policy_id",
            "attempt_purpose",
            "result_envelope_json",
            "result_schema_id",
            "lease_expires_at",
            "lease_owner",
        ):
            db.execute(f"alter table agent_runtime_attempts drop column {column}")
        db.execute(
            "update service_state set value='2026-08-20.1' where key=?",
            (store_module.STORE_SCHEMA_VERSION_KEY,),
        )
    assert store._schema_is_current() is False
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    reopened = AutoReplyStore(db_path)

    with reopened._connect() as db:
        columns = {
            row["name"]
            for row in db.execute("pragma table_info(agent_runtime_attempts)")
        }
    assert store_module.STORE_SCHEMA_VERSION == "2026-08-20.1"
    assert {
        "lease_owner",
        "lease_expires_at",
        "attempt_purpose",
        "validation_retry_policy_id",
        "validation_result_schema_id",
        "result_schema_id",
        "result_envelope_json",
    } <= columns
    assert reopened._schema_is_current() is True
