"""End-to-end coverage for the Friday Runtime fallback route.

The default test uses an in-process HTTP server and a temporary SQLite store;
it never calls a provider or performs a business-side effect.  The opt-in test
is for a developer's local Friday Runtime only.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
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
from app.agent_runtime_production import build_friday_runtime_launch_environment
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
    assert attempts[-1].transcript_reference == "friday_operation:e2e-operation"
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


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("CEO_LIVE_FRIDAY_RUNTIME_E2E") != "1",
    reason="set CEO_LIVE_FRIDAY_RUNTIME_E2E=1 for local MiniMax E2E",
)
def test_live_friday_runtime_subprocess_minimax_chat_completions(tmp_path: Path):
    """Run the production CEO adapter against a real local Friday subprocess.

    The provider key is supplied only through the child environment.  Runtime,
    SQLite state, project workspace, logs, and artifacts all live under pytest's
    temporary directory and are removed by the fixture lifecycle.
    """

    provider_key = os.getenv("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY", "").strip()
    if not provider_key:
        pytest.fail("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY is required for live E2E")
    provider_base_url = os.getenv(
        "CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL", "https://api.minimaxi.com/v1"
    ).strip()
    provider_model = os.getenv("CEO_FRIDAY_RUNTIME_PROVIDER_MODEL", "MiniMax-M3").strip()

    friday_root = Path("/Users/derek/Documents/Projects/friday-agent/friday-runtime")
    friday_python = friday_root / ".venv/bin/python"
    if not friday_python.exists():
        pytest.fail(f"Friday runtime interpreter not found: {friday_python}")
    port = _free_local_port()
    runtime_db = tmp_path / "friday-runtime.sqlite3"
    runtime_home = tmp_path / "friday-home"
    project_workspace = tmp_path / "project"
    project_workspace.mkdir()
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(
        """server:
  host: 127.0.0.1
  port: 0
  auth:
    enabled: false
    required_for_v1: false
storage:
  db_path: ${RUNTIME_DB}
llm:
  provider: openai-compatible
  enabled: true
  json_mode: native
memory:
  provider: disabled
  enabled: false
output:
  root_path: ${RUNTIME_HOME}/output
observability:
  logging:
    root_path: ${RUNTIME_HOME}/logs
  trace_journal:
    root_path: ${RUNTIME_HOME}/traces
""".replace("${RUNTIME_DB}", str(runtime_db)).replace("${RUNTIME_HOME}", str(runtime_home)),
        encoding="utf-8",
    )

    # Build the child environment from the production mapping while removing
    # inherited local Friday provider values and all CEO Codex settings.
    base_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CEO_CODEX_")
        and not key.startswith("CEO_FRIDAY_RUNTIME_PROVIDER_")
        and not key.startswith("FRIDAY_LLM_")
    }
    config = _config_for_live_provider(
        provider_base_url=provider_base_url,
        provider_model=provider_model,
        provider_key=provider_key,
        project_id="pending",
    )
    child_environment = build_friday_runtime_launch_environment(
        config, base_environment=base_environment
    )
    child_environment.update(
        {
            "PYTHONPATH": str(friday_root / "src"),
            "FRIDAY_RUNTIME_CONFIG": str(config_path),
            "FRIDAY_HOME": str(runtime_home),
            "FRIDAY_LLM_JSON_MODE": "native",
        }
    )
    process = subprocess.Popen(
        [
            str(friday_python),
            "-m",
            "friday_runtime.api.main",
            "--config",
            str(config_path),
            "--db-path",
            str(runtime_db),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=friday_root,
        env=child_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_friday(base_url, process)
        project_payload = _post_json(
            f"{base_url}/v1/projects",
            {
                "name": "CEO live E2E",
                "description": "temporary live integration project",
                "workspace_root": str(project_workspace),
                "bootstrap_scan": {"enabled": False, "create_default_thread": False},
            },
        )
        project_data = project_payload.get("data", project_payload)
        project = project_data.get("project") if isinstance(project_data, dict) else None
        project_id = str(project.get("project_id") if isinstance(project, dict) else "").strip()
        assert project_id
        live_config = _config_for_live_provider(
            provider_base_url=provider_base_url,
            provider_model=provider_model,
            provider_key=provider_key,
            project_id=project_id,
            base_url=base_url,
        )
        store = AutoReplyStore(tmp_path / "ceo-agent.sqlite3")
        _task_id, run_id, generation = _seed_run(store)
        snapshots = {
            "friday_runtime": RuntimeCapabilitySnapshot(
                route_name="friday_runtime",
                capabilities=CAPABILITIES,
                healthy=True,
                checked_at="2026-08-27T00:00:00+00:00",
                expires_at="2099-08-27T00:00:00+00:00",
            )
        }
        routed = RoutedCodexExecution(
            store=store,
            config=live_config,
            router=AgentRuntimeRouter(routes=live_config.routes, store=store, snapshots=snapshots),
            adapter=_FailingCodexAdapter(),
            friday_adapter=FridayRuntimeAdapter(live_config, poll_interval_seconds=0.2),
            executor=lambda *_args, **_kwargs: ProcessRunResult(1, "", "unused"),
        )
        routed_result = routed.execute(
            workload_kind="agent_run",
            workload_key=str(run_id),
            prompt='Return exactly this JSON object and no other text: {"ok":true,"value":7}.',
            command_factory=ApprovedCodexCommandFactory.effectful(developer_instructions="Return JSON."),
            parser=_parse_json_text,
            result_codec=RoutedResultCodec.text(schema_id="friday-e2e.json.v1"),
        )
        assert json.loads(routed_result.value) == {"ok": True, "value": 7}
        attempts = store.list_agent_runtime_attempts(run_id)
        assert len(attempts) == 1
        assert attempts[0].route_name == "friday_runtime"
        assert attempts[0].status == "completed"
        assert attempts[0].session_id
        assert attempts[0].transcript_reference.startswith("friday_operation:")
        operation_id = attempts[0].transcript_reference.split(":", 1)[1]
        with sqlite3.connect(runtime_db) as runtime_state:
            thread_id = runtime_state.execute("select thread_id from threads").fetchone()[0]
            turn_id = runtime_state.execute("select turn_id from turns").fetchone()[0]
            operation = runtime_state.execute(
                "select operation_id, status from async_operations where operation_id = ?",
                (operation_id,),
            ).fetchone()
            artifact = runtime_state.execute(
                "select thread_id, created_turn_id, final_message from artifacts where thread_id = ?",
                (thread_id,),
            ).fetchone()
            run_model = runtime_state.execute("select model from runs where turn_id = ?", (turn_id,)).fetchone()
            run_state = runtime_state.execute(
                "select status, last_error_code, last_error_message from runs where turn_id = ?",
                (turn_id,),
            ).fetchone()
        assert attempts[0].session_id.startswith("friday_thread:")
        assert thread_id and thread_id == attempts[0].session_id.split(":", 1)[1]
        assert turn_id and operation == (operation_id, "completed")
        assert run_model and run_model[0] == provider_model
        assert run_state and run_state[0] == "completed", _safe_detail(run_state)
        assert artifact and artifact[0] == thread_id and artifact[1] == turn_id
        assert _parse_json_result(artifact[2]) == {"ok": True, "value": 7}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        stdout, stderr = process.communicate(timeout=2)
        assert provider_key not in stdout
        assert provider_key not in stderr
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert provider_key.encode() not in path.read_bytes()


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _config_for_live_provider(*, provider_base_url: str, provider_model: str, provider_key: str, project_id: str, base_url: str = "http://127.0.0.1:1"):
    return load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_BASE_URL": base_url,
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": project_id,
            "CEO_FRIDAY_RUNTIME_MODEL": provider_model,
            "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1",
            "CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL": provider_base_url,
            "CEO_FRIDAY_RUNTIME_PROVIDER_MODEL": provider_model,
            "CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY": provider_key,
        }
    )


def _wait_for_friday(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("Friday Runtime subprocess exited during startup")
        try:
            with urllib.request.urlopen(f"{base_url}/runtime/hello", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    pytest.fail("Friday Runtime subprocess did not become healthy")


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    assert isinstance(decoded, dict)
    return decoded


def _parse_json_result(text: str) -> object:
    """Parse a provider JSON result while tolerating reasoning wrappers."""

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        # Reasoning wrappers may themselves quote an example object; the final
        # object is the provider's answer.
        start = text.rfind("{")
        if start < 0:
            raise
        value, end = decoder.raw_decode(text[start:])
        assert not text[start + end :].strip()
        return value


def _parse_json_text(text: str) -> str:
    return json.dumps(_parse_json_result(text), separators=(",", ":"))


def _safe_detail(value: object) -> str:
    return str(value).replace(os.getenv("CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY", ""), "[redacted]")[:500]
