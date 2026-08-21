"""Opt-in live verification for the dual-auth Codex runtime boundary.

These tests deliberately use only synthetic prompts and the sealed no-tools,
read-only command policy.  They never run unless the operator explicitly sets
``CEO_LIVE_RUNTIME_FAILOVER_E2E=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_probe import AgentRuntimeProbe
from app.agent_runtime_router import (
    AgentRuntimeRouter,
    ApprovedCodexCommandFactory,
    RoutedCodexExecution,
    RoutedResultCodec,
    count_codex_session_lines,
    local_codex_session_effect_probe,
)
from app.audit_web import _runtime_attempt_evidence_card
from app.codex_runtime_adapter import CodexRuntimeAdapter
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.store import AgentRole, AutoReplyStore

pytestmark = [
    pytest.mark.skipif(
        os.getenv("CEO_LIVE_RUNTIME_FAILOVER_E2E") != "1",
        reason="set CEO_LIVE_RUNTIME_FAILOVER_E2E=1 to run live runtime failover E2E",
    ),
]

_SYNTHETIC_PROMPT = 'Return exactly the synthetic JSON object {"ok":true}.'
_SYNTHETIC_INSTRUCTIONS = (
    "This is an isolated health check. Do not call tools, read files, access "
    'business data, or perform effects. Return exactly {"ok":true}.'
)
_REQUIRED_CAPABILITIES = frozenset(
    {
        "structured_output",
        "local_schema_validation",
        "consumer_read_only_enforcement",
    }
)


class _RecordingExecutor:
    def __init__(self) -> None:
        self.results: list[ProcessRunResult] = []
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, **kwargs) -> ProcessRunResult:
        result = run_process_with_idle_timeout(command, **kwargs)
        self.commands.append(tuple(command))
        self.results.append(result)
        return result


def _live_config():
    try:
        config = load_runtime_config(os.environ)
    except ValueError as exc:
        pytest.fail(f"live runtime configuration is invalid: {exc}")
    configured = {route.name for route in config.routes}
    missing = {"codex_oauth", "codex_api"} - configured
    if missing:
        pytest.fail(f"live runtime routes are missing: {sorted(missing)}")
    secret = config.secret_for("codex_api")
    if secret is None or not secret.get_secret_value():
        pytest.fail("CEO_CODEX_API_KEY is required for live failover E2E")
    return config


def _run_probe(config, route_name: str, tmp_path: Path, executor=None):
    probe = AgentRuntimeProbe(
        config=config,
        executor=executor or run_process_with_idle_timeout,
        temporary_root=tmp_path,
    )
    snapshot = probe.run(route_name=route_name)
    assert snapshot.route_name == route_name
    assert snapshot.healthy is True, snapshot.failure
    assert snapshot.failure is None
    assert _REQUIRED_CAPABILITIES <= snapshot.capabilities
    assert snapshot.checked_at
    assert snapshot.expires_at
    return snapshot


def _runtime_database_paths(path: Path) -> tuple[Path, ...]:
    return (
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    )


def _recorded_surfaces(recorder: _RecordingExecutor) -> list[str]:
    return [
        *(result.stdout for result in recorder.results),
        *(result.stderr for result in recorder.results),
        *(" ".join(command) for command in recorder.commands),
    ]


def _assert_secret_absent(
    secret: str,
    *,
    recorder: _RecordingExecutor,
    store: AutoReplyStore,
    rendered_history: str,
) -> None:
    captured = [*_recorded_surfaces(recorder), rendered_history]
    assert all(secret not in surface for surface in captured)
    for path in _runtime_database_paths(store.path):
        if path.exists():
            assert secret.encode() not in path.read_bytes()


def _parse_synthetic_result(raw: str) -> str:
    messages: list[str] = []
    for line in raw.splitlines():
        payload = json.loads(line)
        item = payload.get("item") if isinstance(payload, dict) else None
        if (
            payload.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            messages.append(item["text"])
    if len(messages) != 1 or json.loads(messages[0]) != {"ok": True}:
        raise ValueError("runtime did not return the exact synthetic result")
    return json.dumps({"ok": True}, separators=(",", ":"))


def _seed_agent_run(store: AutoReplyStore) -> tuple[int, str, str]:
    generation = "live-runtime-failover-generation"
    owner = "live-runtime-failover-owner"
    with store._connect() as db:
        cursor = db.execute(
            """
            insert into reply_tasks (
                channel, conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text, trigger_message_json, execution_generation, status
            ) values (
                'synthetic', 'synthetic-runtime-e2e', 'Synthetic runtime E2E', 1,
                'synthetic-runtime-e2e-message', current_timestamp, 'test',
                'synthetic read-only probe', '{}', ?, 'processing'
            )
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
        owner=owner,
    )
    assert claim.claimed is True
    return claim.run.id, generation, owner


def test_oauth_route_probe_from_service_environment(tmp_path):
    config = _live_config()

    snapshot = _run_probe(config, "codex_oauth", tmp_path)

    assert "consumer_read_only_enforcement" in snapshot.capabilities


def test_api_route_probe_does_not_expose_secret(tmp_path):
    config = _live_config()
    recorder = _RecordingExecutor()
    business_db = tmp_path / "runtime-api-probe.sqlite3"
    assert not any(path.exists() for path in _runtime_database_paths(business_db))

    _run_probe(config, "codex_api", tmp_path, recorder)

    secret = config.secret_for("codex_api")
    assert secret is not None
    assert all(
        secret.get_secret_value() not in surface
        for surface in _recorded_surfaces(recorder)
    )
    assert not any(path.exists() for path in _runtime_database_paths(business_db))


def test_read_only_turn_fails_over_under_same_agent_run(tmp_path):
    config = _live_config()
    recorder = _RecordingExecutor()
    snapshots = {
        route_name: _run_probe(config, route_name, tmp_path, recorder)
        for route_name in ("codex_oauth", "codex_api")
    }
    store = AutoReplyStore(tmp_path / "runtime-failover.sqlite3")
    run_id, generation, _owner = _seed_agent_run(store)
    adapter = CodexRuntimeAdapter(tmp_path, config)
    real_effect_probe = local_codex_session_effect_probe()

    def injected_executor(command, **kwargs):
        if "OPENAI_API_KEY" not in kwargs["env"]:
            result = ProcessRunResult(
                1,
                json.dumps(
                    {
                        "type": "thread.started",
                        "thread_id": "synthetic-injected-oauth-failure",
                    }
                ),
                "Not logged in",
            )
            recorder.commands.append(tuple(command))
            recorder.results.append(result)
            return result
        return recorder(command, **kwargs)

    def effect_probe(session_id: str, start: int, end: int):
        if session_id == "synthetic-injected-oauth-failure":
            return False
        return real_effect_probe(session_id, start, end)

    schema_path = tmp_path / "runtime-failover-result.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"ok": {"const": True}},
                "required": ["ok"],
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    routed = RoutedCodexExecution(
        store=store,
        config=config,
        router=AgentRuntimeRouter(
            routes=config.routes,
            store=store,
            snapshots=snapshots,
        ),
        adapter=adapter,
        executor=injected_executor,
        session_line_counter=lambda session_id: (
            0
            if session_id == "synthetic-injected-oauth-failure"
            else count_codex_session_lines(session_id)
        ),
        session_effect_probe=effect_probe,
    )

    result = routed.execute(
        workload_kind="agent_run",
        workload_key=str(run_id),
        prompt=_SYNTHETIC_PROMPT,
        command_factory=ApprovedCodexCommandFactory.read_only_without_tools(
            developer_instructions=_SYNTHETIC_INSTRUCTIONS,
            output_schema_path=schema_path,
            use_output_schema=True,
        ),
        parser=_parse_synthetic_result,
        result_codec=RoutedResultCodec.text(schema_id="live_runtime_probe.v1"),
        conversation_id="synthetic-runtime-e2e",
        required_capabilities=_REQUIRED_CAPABILITIES,
    )

    persisted_run = store.get_agent_run(run_id)
    attempts = store.list_agent_runtime_attempts(run_id)
    assert result.route_name == "codex_api"
    assert persisted_run is not None
    assert persisted_run.execution_generation == generation
    assert {attempt.agent_run_id for attempt in attempts} == {run_id}
    assert [attempt.route_name for attempt in attempts] == ["codex_oauth", "codex_api"]
    assert [attempt.status for attempt in attempts] == ["superseded", "completed"]
    assert attempts[0].failure_code == "codex_login_required"
    rendered_history = _runtime_attempt_evidence_card(attempts)
    assert "Runtime attempts" in rendered_history
    assert "Route: codex_oauth" in rendered_history
    assert "Route: codex_api" in rendered_history
    for command in recorder.commands:
        argv = "\n".join(command)
        assert "tools.enabled_tools=[]" in argv
        assert 'web_search="disabled"' in argv
    secret = config.secret_for("codex_api")
    assert secret is not None
    _assert_secret_absent(
        secret.get_secret_value(),
        recorder=recorder,
        store=store,
        rendered_history=rendered_history,
    )
