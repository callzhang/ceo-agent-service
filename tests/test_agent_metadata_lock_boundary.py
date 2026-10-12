import sqlite3

import pytest

from app.store import AgentRole, AutoReplyStore


@pytest.mark.parametrize("operation", ["claim", "completed_claim", "renew", "session", "expire"])
def test_agent_metadata_hydration_releases_writer_and_preserves_snapshot(
    tmp_path, monkeypatch, operation,
):
    from app import native_trajectory

    path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        conversation_id="cid", conversation_title="Title", single_chat=False,
        trigger_message_id="msg", trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek", trigger_text="Task",
    )
    task_id = store.claim_reply_tasks(limit=1)[0].id

    def claim():
        return store.claim_agent_run(
            task_id, "initial", role=AgentRole.AUDIT,
            proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
            operation_id=f"direct-agent:{task_id}:initial", owner="worker",
            lease_seconds=60, now="2026-07-29 00:00:00",
        )

    if operation != "claim":
        existing = claim().run
        if operation == "completed_claim":
            store.complete_agent_run(
                existing.id, {"outcome": "done"}, owner="worker",
                now="2026-07-29 00:00:01",
            )

    events = [{"type": "native-test-event", "item": {"id": "exact-id"}}]
    calls = []

    def hydrate(row, attempts, db_path):
        calls.append(row["id"])
        other = sqlite3.connect(path, timeout=0)
        try:
            # Native hydration may be arbitrarily slow. It must not retain
            # either a writer lock or an uncommitted metadata change.
            other.execute("begin immediate")
            persisted = other.execute(
                "select status, codex_session_id, lease_expires_at from agent_runs where id=?",
                (row["id"],),
            ).fetchone()
            assert persisted == (
                row["status"], row["codex_session_id"], row["lease_expires_at"],
            )
            other.execute(
                "update agent_runs set updated_at='concurrent' where id=?",
                (row["id"],),
            )
            other.commit()
        finally:
            other.close()
        return events

    monkeypatch.setattr(native_trajectory, "hydrate_run_events_from_attempts", hydrate)
    def native_result(row, attempt):
        with sqlite3.connect(path, timeout=0) as other:
            other.execute("begin immediate")
        return '{"outcome":"done","native":true}'

    monkeypatch.setattr(native_trajectory, "read_native_result_json_from_attempt", native_result)
    if operation == "claim":
        result = claim()
        assert result.claimed
        run = result.run
    elif operation == "completed_claim":
        result = claim()
        assert not result.claimed
        run = result.run
        assert run.status == "completed"
        assert run.final_result_json == '{"outcome":"done","native":true}'
    elif operation == "renew":
        run = store.renew_agent_run_lease(
            existing.id, owner="worker", lease_seconds=120,
            now="2026-07-29 00:00:01",
        )
        assert run.lease_expires_at == "2026-07-29 00:02:01"
    elif operation == "session":
        run = store.set_agent_run_session(
            existing.id, "native-session", owner="worker",
            transcript_start_line=7, now="2026-07-29 00:00:01",
        )
        assert run.codex_session_id == "native-session"
        assert run.transcript_start_line == 7
    else:
        run = store.fail_expired_agent_run(
            existing.id, {"error": "expired"},
            expected_execution_generation="initial",
            now="2026-07-29 00:02:00",
        )
        assert run.status == "failed"
    assert calls == [run.id]
    assert run.updated_at != "concurrent"
    assert run.tool_events == events
