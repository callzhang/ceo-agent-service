import json
from datetime import UTC, datetime, timedelta

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_probe import AgentRuntimeProbe, RuntimeCapabilityRefresher
from app.agent_runtime_production import RuntimeCapabilityRegistry
from app.process_runner import ProcessRunResult
from app.store import AutoReplyStore

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def _config(monkeypatch, *, routes: str = "codex_oauth,codex_api"):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", routes)
    monkeypatch.setenv("CEO_CODEX_API_KEY", "test-api-secret")
    monkeypatch.setenv("CEO_RUNTIME_PROBE_INTERVAL", "5m")
    monkeypatch.setenv("CEO_RUNTIME_ROUTE_RETRY_DELAY", "30m")
    return load_runtime_config(dict(__import__("os").environ))


def _successful_probe_stream(*, session_id: str = "probe-session") -> str:
    return "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": session_id}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps({"ok": True}),
                    },
                }
            ),
            json.dumps({"type": "turn.completed"}),
        )
    )


def test_probe_requires_structured_completion(monkeypatch, tmp_path):
    config = _config(monkeypatch, routes="codex_api")
    probe = AgentRuntimeProbe(
        config=config,
        codex_bin="codex-test",
        executor=lambda *_args, **_kwargs: ProcessRunResult(
            0,
            json.dumps({"type": "turn.started"}),
            "",
        ),
        now=lambda: NOW,
        temporary_root=tmp_path,
    )

    snapshot = probe.run(route_name="codex_api")

    assert snapshot.healthy is False
    assert snapshot.capabilities == frozenset()
    assert snapshot.failure is not None
    assert snapshot.failure.code == "runtime_probe_incomplete"
    assert snapshot.checked_at == NOW.isoformat()
    assert snapshot.expires_at == (NOW + timedelta(minutes=5)).isoformat()


def test_probe_rejects_any_started_action_even_with_valid_final_result(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, routes="codex_oauth")
    stream = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "probe-session"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "mcp_tool_call",
                        "server": "memory_connector",
                        "tool": "memory_write",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps({"ok": True}),
                    },
                }
            ),
            json.dumps({"type": "turn.completed"}),
        )
    )
    snapshot = AgentRuntimeProbe(
        config=config,
        executor=lambda *_args, **_kwargs: ProcessRunResult(0, stream, ""),
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="codex_oauth")

    assert snapshot.healthy is False
    assert snapshot.failure is not None
    assert snapshot.failure.code == "runtime_probe_policy_violation"


@pytest.mark.parametrize(
    "extra_payload",
    [
        {"type": "item.completed", "item": {"type": "mcp_tool_call"}},
        {"type": "item.failed", "item": {"type": "agent_message"}},
        {"type": "future.dynamic.event"},
    ],
)
def test_probe_rejects_every_event_outside_exact_no_tools_grammar(
    monkeypatch, tmp_path, extra_payload
):
    config = _config(monkeypatch, routes="codex_oauth")
    stream = _successful_probe_stream().splitlines()
    stream.insert(-1, json.dumps(extra_payload))

    snapshot = AgentRuntimeProbe(
        config=config,
        executor=lambda *_args, **_kwargs: ProcessRunResult(
            0, "\n".join(stream), ""
        ),
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="codex_oauth")

    assert snapshot.healthy is False
    assert snapshot.failure is not None
    assert snapshot.failure.code == "runtime_probe_policy_violation"


def test_probe_uses_isolated_read_only_command_and_validates_complete_stream(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, routes="codex_api")
    calls = []

    def executor(command, **kwargs):
        calls.append((command, kwargs))
        return ProcessRunResult(0, _successful_probe_stream(), "")

    snapshot = AgentRuntimeProbe(
        config=config,
        codex_bin="codex-test",
        executor=executor,
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="codex_api")

    assert snapshot.healthy is True
    assert snapshot.failure is None
    assert {
        "structured_output",
        "local_schema_validation",
        "consumer_read_only_enforcement",
    }.issubset(snapshot.capabilities)
    [(command, kwargs)] = calls
    assert command[:2] == ["codex-test", "exec"]
    assert "--json" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--cd") + 1].startswith(str(tmp_path))
    assert "tools.enabled_tools=[]" in command
    assert 'web_search="disabled"' in command
    assert kwargs["prompt"].startswith("Return only the synthetic probe result")
    assert kwargs["env"]["OPENAI_API_KEY"] == "test-api-secret"
    assert "CEO_CODEX_API_KEY" not in kwargs["env"]


def test_refresher_opens_and_closes_only_the_probed_route_pause(monkeypatch, tmp_path):
    config = _config(monkeypatch)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    registry = RuntimeCapabilityRegistry()
    outcomes = {
        "codex_oauth": ProcessRunResult(1, "", "login failed"),
        "codex_api": ProcessRunResult(0, _successful_probe_stream(), ""),
    }

    def executor(command, **kwargs):
        route = "codex_api" if "OPENAI_API_KEY" in kwargs["env"] else "codex_oauth"
        return outcomes[route]

    refresher = RuntimeCapabilityRefresher(
        config=config,
        store=store,
        registry=registry,
        probe=AgentRuntimeProbe(
            config=config,
            executor=executor,
            now=lambda: NOW,
            temporary_root=tmp_path,
        ),
        now=lambda: NOW,
    )

    first = refresher.refresh_expired(force=True)

    assert first["codex_oauth"].healthy is False
    assert first["codex_api"].healthy is True
    assert store.active_runtime_route_pause("codex_oauth", now=NOW) is not None
    assert store.active_runtime_route_pause("codex_api", now=NOW) is None
    assert registry["codex_api"].healthy is True

    outcomes["codex_oauth"] = ProcessRunResult(
        0, _successful_probe_stream(session_id="oauth-recovered"), ""
    )
    second = refresher.refresh_expired(route_names=("codex_oauth",), force=True)

    assert second["codex_oauth"].healthy is True
    assert store.active_runtime_route_pause("codex_oauth", now=NOW) is None
    assert registry["codex_api"].healthy is True


def test_refresher_isolates_unexpected_probe_exception_and_continues_routes(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch)
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    registry = RuntimeCapabilityRegistry()

    class Probe:
        def run(self, *, route_name):
            if route_name == "codex_oauth":
                raise RuntimeError("secret path and credential detail")
            return AgentRuntimeProbe(
                config=config,
                executor=lambda *_args, **_kwargs: ProcessRunResult(
                    0, _successful_probe_stream(), ""
                ),
                now=lambda: NOW,
                temporary_root=tmp_path,
            ).run(route_name=route_name)

    snapshots = RuntimeCapabilityRefresher(
        config=config,
        store=store,
        registry=registry,
        probe=Probe(),
        now=lambda: NOW,
    ).refresh_expired(force=True)

    assert snapshots["codex_oauth"].healthy is False
    assert snapshots["codex_oauth"].failure is not None
    assert snapshots["codex_oauth"].failure.code == "runtime_probe_failed"
    assert "secret" not in snapshots["codex_oauth"].model_dump_json()
    assert snapshots["codex_api"].healthy is True


def test_successful_no_tools_probe_does_not_claim_unverified_business_capabilities(
    monkeypatch, tmp_path
):
    config = _config(monkeypatch, routes="codex_oauth")

    snapshot = AgentRuntimeProbe(
        config=config,
        executor=lambda *_args, **_kwargs: ProcessRunResult(
            0, _successful_probe_stream(), ""
        ),
        now=lambda: NOW,
        temporary_root=tmp_path,
    ).run(route_name="codex_oauth")

    assert snapshot.capabilities == frozenset(
        {
            "structured_output",
            "local_schema_validation",
            "consumer_read_only_enforcement",
        }
    )
    assert "reviewed_read_tools" not in snapshot.capabilities
    assert "memory_connector_read" not in snapshot.capabilities
    assert "reviewed_write_tools" not in snapshot.capabilities

def test_refresher_skips_current_snapshots(monkeypatch, tmp_path):
    config = _config(monkeypatch, routes="codex_oauth")
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    registry = RuntimeCapabilityRegistry()
    calls = []
    probe = AgentRuntimeProbe(
        config=config,
        executor=lambda *_args, **_kwargs: (
            calls.append("probe")
            or ProcessRunResult(0, _successful_probe_stream(), "")
        ),
        now=lambda: NOW,
        temporary_root=tmp_path,
    )
    refresher = RuntimeCapabilityRefresher(
        config=config,
        store=store,
        registry=registry,
        probe=probe,
        now=lambda: NOW,
    )

    refresher.refresh_expired()
    refresher.refresh_expired()

    assert calls == ["probe"]
