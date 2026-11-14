"""Synthetic resolved feedback through current public batch APIs; no external work."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.action_feedback_storage import _materialize
from app.action_storage import ACTION_RECORD_TYPES, _action_layout, _encoded_native_sqlite_value
from app.feedback_processing import ResolutionEvidence
from app.store import AutoReplyStore
from app.web_api.registration import register_console_routes


def completed_workbench_association(store: AutoReplyStore) -> dict[str, object]:
    """Create real local native completion; never call an Agent or provider."""
    from app.workbench.store import WorkbenchStore

    workbench = WorkbenchStore(store.path)
    task = workbench.create_task(title="Fixture feedback repair", runtime_kind="codex")
    turn = workbench.create_turn(task.id, user_text="Verify repair", client_request_id=f"repair-{task.id}")
    assert workbench.claim_next_turn(owner="feedback-fixture") is not None
    runtime = store.claim_runtime_operation_attempt(
        "workbench", turn.id, "codex_oauth", "codex_cli", "local_oauth", "test-model",
        owner="feedback-fixture",
    )
    store.complete_agent_runtime_attempt(
        runtime.id, "fixture-session", "", 0, 1, owner="feedback-fixture",
    )
    workbench.complete_turn(turn.id, status="completed", final_text="Verified", owner="feedback-fixture")
    return {"workbench_task_id": task.id, "workbench_turn_id": turn.id,
            "attempt_id": 0, "agent_run_id": 0}


def resolve_feedback_fixture(
    store: AutoReplyStore, feedback_key: str, *, batch_id: str | None = None
) -> None:
    """Resolve an already seeded event without replacing its source content.

    Receipt values describe this synthetic database fixture. No tests, service
    restart, health request, commit lookup, Agent or provider is executed here.
    """
    selected_batch = batch_id or f"resolved-fixture:{feedback_key}"
    receipt = ResolutionEvidence(
        commit_sha="b" * 40,
        test_evidence={"pytest": {"exit_code": 0}},
        restart_evidence={
            "launchd_label": "com.ceo-agent-service.main",
            "before_pid": 10,
            "after_pid": 11,
        },
        health_evidence={
            "url": "http://127.0.0.1:8765/healthz",
            "status_code": 200,
            "ok": True,
        },
        backlog_evidence={"processing": 0, "failed": 0, "retryable": 0},
    )
    store.create_feedback_processing_batch([feedback_key], batch_id=selected_batch)
    assert store.claim_feedback_processing_items(selected_batch, [feedback_key])
    store.associate_feedback_processing_turn(
        feedback_key,
        **completed_workbench_association(store),
    )
    store.patch_feedback_processing_item_evidence(
        feedback_key,
        commit_sha=receipt.commit_sha,
        test_evidence=receipt.test_evidence,
        restart_evidence=receipt.restart_evidence,
        health_evidence=receipt.health_evidence,
    )
    assert store.resolve_feedback_processing_batch(
        selected_batch, receipt, commit_is_ancestor=True
    )


def registered_feedback_route_client(
    tmp_path: Path,
    *,
    feedback_backlog_factory,
    status_payload_factory=lambda: {
        "service": {"state": "refreshing"},
        "summary": {"processing": 0, "failed": 0, "retryable": 0},
    },
) -> tuple[TestClient, AutoReplyStore]:
    db_path = tmp_path / "route-worker.sqlite3"
    store = AutoReplyStore(db_path)
    app = FastAPI()
    register_console_routes(
        app,
        store_factory=lambda: store,
        status_payload_factory=status_payload_factory,
        feedback_backlog_factory=feedback_backlog_factory,
        attention_rows_factory=lambda: [],
    )
    return (
        TestClient(
            app,
            client=("127.0.0.1", 50000),
            headers={"Host": "127.0.0.1:8765"},
        ),
        store,
    )


def seed_feedback_noise(
    store: AutoReplyStore,
    *,
    count: int,
    received_at: str = "2030-01-01 00:00:00",
) -> None:
    with store._connect() as db:
        first_rowid = db.execute(
            "select coalesce(max(cast(legacy_key as integer)),0)+1 "
            "from actions where record_type='feedback_events'"
        ).fetchone()[0]
        kind = "feedback_events"
        layout = _action_layout(kind)
        defaults = dict.fromkeys(layout.fields) | _materialize(db, kind, {"received_at": received_at})
        code = ACTION_RECORD_TYPES.code(kind)
        rows = (
            (
                code, str(first_rowid + index),
                layout.pack(defaults | {
                    "key": f"noise-{index:05d}",
                    "feedback_token": f"noise-token-{index:05d}",
                }, scalar_codec=_encoded_native_sqlite_value),
            )
            for index in range(count)
        )
        db.executemany(
            "insert into main.actions(_kind,legacy_key,payload_json) values(?,?,jsonb(?))",
            rows,
        )


def seed_feedback_batch_lookup(store: AutoReplyStore, *, noise_count: int) -> tuple[str, str]:
    summary = "Saved audit summary for feedback outside the first page."
    attempt_id = store.record_reply_attempt(
        conversation_id="feedback-lookup",
        conversation_title="Feedback lookup",
        trigger_message_id="feedback-source",
        trigger_sender="Fixture",
        trigger_text="Example request",
        action="send_reply",
        sensitivity_kind="general",
        audit_summary=summary,
        send_status="sent",
    )
    key = f"manual:{attempt_id}"
    store.upsert_feedback_event(
        key=key, feedback_token="target-token", received_at="2000-01-01 00:00:00",
    )
    store.claim_feedback_processing_items("batch-outside-limit", [key])
    seed_feedback_noise(store, count=noise_count)
    return key, summary
