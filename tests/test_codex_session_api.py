from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.action_storage import insert_action
from app.run_storage import insert_run
from app.store import AutoReplyStore
from app.task_storage import REPLY_TASK_SQL, task_physical_id
from app.web_api.attempts import _runtime_payload
from app.web_api.registration import register_console_routes


def _session_api(store: AutoReplyStore) -> TestClient:
    app = FastAPI()
    register_console_routes(
        app,
        store_factory=lambda: store,
        status_payload_factory=lambda: {},
        feedback_backlog_factory=lambda: {"processing": 0, "failed": 0, "retryable": 0},
        attention_rows_factory=lambda: [],
    )
    return TestClient(app)


def _seed_task_history(store: AutoReplyStore) -> tuple[int, int]:
    conversation_id = "cid-codex-session-history"
    trigger_message_id = "msg-codex-session-history"
    assert store.enqueue_reply_task(
        conversation_id=conversation_id,
        conversation_title="Codex session history",
        single_chat=False,
        trigger_message_id=trigger_message_id,
        trigger_create_time="2026-09-15 09:00:00",
        trigger_sender="Avery",
        trigger_text="Please review the release.",
        execution_generation="current-generation",
    )
    with store._connect() as db:
        task_id = int(
            db.execute(
                f"select id from ({REPLY_TASK_SQL}) where conversation_id=? and trigger_message_id=?",
                (conversation_id, trigger_message_id),
            ).fetchone()[0]
        )
        historical_audit_run_id = int(
            insert_run(
                db, 'agent_runs', {
                    'task_id': task_physical_id(db, 'reply_tasks', task_id),
                    'execution_generation': 'historical-generation',
                    'role': 'audit',
                    'status': 'completed',
                    'session_id': 'historical-session',
                },
            ).lastrowid
        )
        current_consumer_run_id = int(
            insert_run(
                db, 'agent_runs', {
                    'task_id': task_physical_id(db, 'reply_tasks', task_id),
                    'execution_generation': 'current-generation',
                    'role': 'consumer',
                    'status': 'completed',
                    'session_id': 'target-session',
                },
            ).lastrowid
        )
        current_audit_run_id = int(
            insert_run(
                db, 'agent_runs', {
                    'task_id': task_physical_id(db, 'reply_tasks', task_id),
                    'execution_generation': 'current-generation',
                    'role': 'audit',
                    'status': 'completed',
                    'parent_id': current_consumer_run_id,
                    'session_id': 'current-audit-session',
                },
            ).lastrowid
        )
        historical_attempt_id = int(
            insert_action(db, "reply_attempts", {
                "conversation_id": conversation_id,
                "conversation_title": "Codex session history",
                "trigger_message_id": trigger_message_id,
                "trigger_sender": "Avery",
                "trigger_text": "old result",
                "action": "agent_run",
                "sensitivity_kind": "general",
                "agent_run_id": historical_audit_run_id,
                "send_status": "failed",
            }).lastrowid
        )
        current_attempt_id = int(
            insert_action(db, "reply_attempts", {
                "conversation_id": conversation_id,
                "conversation_title": "Codex session history",
                "trigger_message_id": trigger_message_id,
                "trigger_sender": "Avery",
                "trigger_text": "current result",
                "action": "agent_run",
                "sensitivity_kind": "general",
                "agent_run_id": current_audit_run_id,
                "send_status": "skipped",
            }).lastrowid
        )
    return historical_attempt_id, current_attempt_id


def test_codex_session_endpoint_excludes_attempts_from_other_generations(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    historical_attempt_id, current_attempt_id = _seed_task_history(store)

    with _session_api(store) as client:
        response = client.get("/api/console/codex/sessions/target-session")

    assert response.status_code == 200
    related = response.json()["item"]["related_attempts"]
    assert related == [
        {
            "id": current_attempt_id,
            "status": "skipped",
            "role": "consumer",
            "role_label": "处理 Agent",
        }
    ]
    assert historical_attempt_id not in {row["id"] for row in related}


def test_runtime_payload_marks_a_missing_session_unavailable(monkeypatch):
    run = SimpleNamespace(
        id=12,
        role="consumer",
        status="completed",
        execution_generation="current-generation",
        proposal_revision=0,
        turn_attempt=0,
    )
    runtime_attempt = SimpleNamespace(
        session_id="missing-session",
        attempt_number=1,
        created_at="2026-09-15 09:00:00",
        finished_at="2026-09-15 09:01:00",
        route_name="codex",
        runtime_kind="codex",
        credential_mode="oauth",
        model="gpt-test",
        status="completed",
        failure_code="",
        failover_permitted=False,
        transcript_start=0,
        transcript_end=20,
        first_effect_started_at="",
    )
    store = SimpleNamespace(list_agent_runtime_attempts=lambda _run_id: [runtime_attempt])
    monkeypatch.setattr(
        "app.codex_history.find_codex_session_path",
        lambda _session_id: None,
    )

    payload = _runtime_payload([run], store)

    assert payload[0]["session_available"] is False
    assert payload[0]["run_status"] == "completed"
    assert payload[0]["status"] == "completed"
