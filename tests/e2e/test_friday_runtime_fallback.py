"""End-to-end coverage for the Friday Runtime fallback route.

The default test uses an in-process HTTP server and a temporary SQLite store;
it never calls a provider or performs a business-side effect.  The opt-in test
is for a developer's local Friday Runtime only.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import (
    RuntimeCapabilitySnapshot,
    RuntimeFailure,
    RuntimeFailureClass,
)
from app.agent_runtime_router import (
    AgentRuntimeRouter,
    ApprovedCodexCommandFactory,
    RoutedCodexExecution,
    RoutedResultCodec,
)
from app.friday_runtime_adapter import FridayRuntimeAdapter
from app.process_runner import ProcessRunResult
from app.store import AgentRole, AutoReplyStore


CAPABILITIES = frozenset({"structured_output", "local_schema_validation"})
INT_CODEC = RoutedResultCodec.integer(schema_id="friday-e2e.integer.v1")


class _FakeFridayHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict[str, object] | None]] = []
    lock = threading.Lock()

    def log_message(self, *_args):  # pragma: no cover - keep pytest output quiet
        return

    def _reply(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _record(self, body: dict[str, object] | None) -> None:
        with self.lock:
            self.requests.append((self.command, self.path, body))

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        body = json.loads(raw.decode("utf-8")) if raw else None
        self._record(body)
        if self.path == "/v1/threads":
            self._reply({"result": "success", "data": {"thread": {"thread_id": "e2e-thread"}}})
            return
        if self.path == "/v1/threads/e2e-thread/turns":
            self._reply(
                {
                    "result": "success",
                    "data": {
                        "turn": {"turn_id": "e2e-turn"},
                        "operation": {
                            "operation_id": "e2e-operation",
                            "request_payload": {"turn_id": "e2e-turn"},
                        },
                    },
                }
            )
            return
        self.send_error(404)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._record(None)
        if self.path == "/v1/operations/e2e-operation":
            self._reply({"result": "success", "data": {"operation": {"status": "completed"}}})
            return
        if self.path == "/v1/artifacts?thread_id=e2e-thread":
            self._reply(
                {
                    "result": "success",
                    "data": {
                        "items": [
                            {"thread_id": "e2e-thread", "final_message": "7"}
                        ]
                    },
                }
            )
            return
        self.send_error(404)


@pytest.fixture
def fake_friday_server():
    _FakeFridayHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeFridayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class _FailingCodexAdapter:
    def build_command(self, route, prompt, session_id, **_kwargs):
        return ["synthetic-codex", route.name, session_id or "fresh"]

    def build_env(self, route):
        return {"ROUTE": route.name}

    def classify_failure(self, *_args, **_kwargs):
        return RuntimeFailure(
            failure_class=RuntimeFailureClass.TRANSPORT,
            code="codex_transport_disconnected",
            detail="synthetic provider unavailable",
            failover_permitted=True,
        )


def _config(base_url: str):
    return load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api,friday_runtime",
            "CEO_CODEX_API_KEY": "synthetic-codex-key",
            "CEO_FRIDAY_RUNTIME_BASE_URL": base_url,
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-agent-e2e",
            "CEO_FRIDAY_RUNTIME_MODEL": "MiniMax-M3",
            "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1",
        }
    )


def _seed_run(store: AutoReplyStore) -> tuple[int, int, str]:
    generation = "friday-runtime-e2e-generation"
    with store._connect() as db:
        cursor = db.execute(
            """
            insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, trigger_message_json, execution_generation, status
            ) values ('synthetic', 'friday-e2e', 'Friday E2E', 1,
                      'friday-e2e-message', current_timestamp, 'test',
                      'synthetic fallback', '{}', ?, 'processing')
            """,
            (generation,),
        )
        task_id = int(cursor.lastrowid)
    claim = store.claim_agent_run(
        task_id,
        generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="friday-e2e-owner",
    )
    assert claim.claimed
    return task_id, claim.run.id, generation


def test_oauth_and_codex_failure_fall_back_to_friday_in_one_agent_run(
    tmp_path: Path, fake_friday_server
):
    server, base_url = fake_friday_server
    config = _config(base_url)
    store = AutoReplyStore(tmp_path / "friday-fallback.sqlite3")
    _task_id, run_id, generation = _seed_run(store)
    snapshots = {
        route.name: RuntimeCapabilitySnapshot(
            route_name=route.name,
            capabilities=CAPABILITIES,
            healthy=True,
            checked_at="2026-08-27T00:00:00+00:00",
            expires_at="2099-08-27T00:00:00+00:00",
        )
        for route in config.routes
    }

    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=AgentRuntimeRouter(routes=config.routes, store=store, snapshots=snapshots),
        adapter=_FailingCodexAdapter(),
        friday_adapter=FridayRuntimeAdapter(config, poll_interval_seconds=0),
        executor=lambda *_args, **_kwargs: ProcessRunResult(1, "", "provider unavailable"),
        now=lambda: __import__("datetime").datetime.now(__import__("datetime").UTC),
    )

    result = routed.execute(
        workload_kind="agent_run",
        workload_key=str(run_id),
        prompt="return the integer 7",
        command_factory=ApprovedCodexCommandFactory.effectful(
            developer_instructions="Return one integer."
        ),
        parser=int,
        result_codec=INT_CODEC,
    )

    assert result.value == 7
    attempts = store.list_agent_runtime_attempts(run_id)
    assert [attempt.route_name for attempt in attempts] == [
        "codex_oauth",
        "codex_api",
        "friday_runtime",
    ]
    assert attempts[-1].status == "completed"
    runs = store.list_agent_runs_for_task_generation(
        _task_id_from_run(store, run_id), generation
    )
    assert len(runs) == 1
    assert [path for _method, path, _body in server.RequestHandlerClass.requests] == [
        "/v1/threads",
        "/v1/threads/e2e-thread/turns",
        "/v1/operations/e2e-operation",
        "/v1/artifacts?thread_id=e2e-thread",
    ]


def _task_id_from_run(store: AutoReplyStore, run_id: int) -> int:
    run = store.get_agent_run(run_id)
    assert run is not None
    assert run.reply_task_id is not None
    return run.reply_task_id


@pytest.mark.skipif(
    os.getenv("CEO_LIVE_FRIDAY_RUNTIME_E2E") != "1",
    reason="set CEO_LIVE_FRIDAY_RUNTIME_E2E=1 for local Friday Runtime E2E",
)
def test_local_friday_runtime_e2e():
    base_url = os.getenv("FRIDAY_RUNTIME_BASE_URL", "").strip()
    project_id = os.getenv("CEO_FRIDAY_RUNTIME_PROJECT_ID", "").strip()
    if not base_url or not project_id:
        pytest.fail("FRIDAY_RUNTIME_BASE_URL and CEO_FRIDAY_RUNTIME_PROJECT_ID are required")
    config = load_runtime_config(
        {
            **os.environ,
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_BASE_URL": base_url,
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": project_id,
            "CEO_FRIDAY_RUNTIME_MODEL": os.getenv("CEO_FRIDAY_RUNTIME_MODEL", "default"),
        }
    )
    result = FridayRuntimeAdapter(config, poll_interval_seconds=1).execute(
        'Return exactly the JSON object {"ok":true}.',
        project_id=project_id,
        model=config.friday_runtime_model,
        timeout_seconds=60,
    )
    assert json.loads(result.text) == {"ok": True}
