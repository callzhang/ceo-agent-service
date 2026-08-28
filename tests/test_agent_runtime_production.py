import json
from datetime import UTC, datetime, timedelta

import pytest

from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_production import (
    PRODUCTION_RUNTIME_CAPABILITIES,
    RuntimeCapabilityRegistry,
    build_friday_runtime_launch_environment,
    build_production_agent_runtime,
    build_production_routed_codex_execution,
    build_production_runtime_refresher,
)
from app.process_runner import ProcessRunResult
from app.store import AutoReplyStore


def _snapshot(route_name: str) -> RuntimeCapabilitySnapshot:
    now = datetime.now(UTC)
    return RuntimeCapabilitySnapshot(
        route_name=route_name,
        capabilities=frozenset({"structured_output"}),
        healthy=True,
        checked_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
    )


def test_production_runtime_builds_friday_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "friday_runtime")
    monkeypatch.setenv("CEO_FRIDAY_RUNTIME_PROJECT_ID", "ceo-agent")
    monkeypatch.setenv("CEO_FRIDAY_RUNTIME_MODEL", "MiniMax-M3")
    monkeypatch.setenv("CEO_FRIDAY_RUNTIME_AUTH_DISABLED", "1")
    registry = RuntimeCapabilityRegistry()

    runtime = build_production_agent_runtime(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        capability_registry=registry,
    )

    assert runtime.friday_adapter is not None
    assert runtime.config.routes[0].name == "friday_runtime"
    routed = build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "routed.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=10,
        idle_timeout_seconds=5,
        capability_registry=registry,
    )
    assert routed._friday_adapter is not None


def test_friday_launcher_environment_uses_independent_provider_config():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_PROVIDER_BASE_URL": "https://api.minimaxi.com/v1",
            "CEO_FRIDAY_RUNTIME_PROVIDER_MODEL": "MiniMax-M3",
            "CEO_FRIDAY_RUNTIME_PROVIDER_API_KEY": "minimax-secret",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-agent",
            "CEO_FRIDAY_RUNTIME_MODEL": "MiniMax-M3",
            "CEO_FRIDAY_RUNTIME_TICKET": "runtime-ticket",
        }
    )

    launch_env = build_friday_runtime_launch_environment(
        config,
        base_environment={"PATH": "/usr/bin", "FRIDAY_LLM_MODEL": "explicit-model"},
    )

    assert launch_env["FRIDAY_LLM_PROVIDER"] == "openai-compatible"
    assert launch_env["FRIDAY_LLM_BASE_URL"] == "https://api.minimaxi.com/v1"
    assert launch_env["FRIDAY_LLM_API_KEY"] == "minimax-secret"
    assert launch_env["FRIDAY_LLM_MODEL"] == "explicit-model"
    assert "minimax-secret" not in repr(config)


def test_production_registry_refreshes_existing_router_view(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth,codex_api")
    monkeypatch.setenv("CEO_CODEX_API_KEY", "test-secret")
    registry = RuntimeCapabilityRegistry()
    stdout = "\n".join(
        json.dumps(payload)
        for payload in (
            {"type": "thread.started", "thread_id": "probe-session"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"ok":true}'},
            },
            {"type": "turn.completed"},
        )
    )
    routed = build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=10,
        idle_timeout_seconds=5,
        capability_registry=registry,
        executor=lambda *_args, **_kwargs: ProcessRunResult(0, stdout, ""),
    )

    initial = routed._router.first_route_decision(
        required_capabilities=frozenset({"structured_output"}),
        allow_legacy_oauth_bootstrap=routed._allow_legacy_oauth_bootstrap,
    )
    assert initial.route is None
    assert "snapshot_missing" in initial.reason

    registry.refresh({"codex_api": _snapshot("codex_api")})
    refreshed = routed._router.first_route_decision(
        required_capabilities=frozenset({"structured_output"})
    )
    assert refreshed.route is not None
    assert refreshed.route.name == "codex_api"


def test_production_factory_requires_preinitialized_api_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_api")
    monkeypatch.setenv("CEO_CODEX_API_KEY", "test-secret")
    stdout = "\n".join(
        json.dumps(payload)
        for payload in (
            {"type": "thread.started", "thread_id": "probe-session"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"ok":true}'},
            },
            {"type": "turn.completed"},
        )
    )
    routed = build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=10,
        idle_timeout_seconds=5,
        capability_registry=RuntimeCapabilityRegistry(),
        executor=lambda *_args, **_kwargs: ProcessRunResult(0, stdout, ""),
    )

    decision = routed._router.first_route_decision(
        required_capabilities=frozenset(),
        allow_legacy_oauth_bootstrap=routed._allow_legacy_oauth_bootstrap,
    )
    assert decision.route is None
    assert "snapshot_missing" in decision.reason


def test_production_runtime_probe_matches_task_idle_timeout_by_default(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    calls = []

    def executor(*_args, **kwargs):
        calls.append(kwargs["idle_timeout_seconds"])
        stdout = "\n".join(
            json.dumps(payload)
            for payload in (
                {"type": "thread.started", "thread_id": "probe-session"},
                {"type": "turn.started"},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": '{"ok":true}'},
                },
                {"type": "turn.completed"},
            )
        )
        return ProcessRunResult(0, stdout, "")

    refresher = build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        executor=executor,
    )

    refresher.refresh_expired(force=True)

    assert calls == [300.0]


def test_capability_registry_rejects_mismatched_key():
    with pytest.raises(ValueError, match="key mismatch"):
        RuntimeCapabilityRegistry({"codex_api": _snapshot("codex_oauth")})


def test_production_refresher_publishes_into_shared_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    PRODUCTION_RUNTIME_CAPABILITIES.refresh({})
    stdout = "\n".join(
        json.dumps(payload)
        for payload in (
            {"type": "thread.started", "thread_id": "probe-session"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"ok":true}'},
            },
            {"type": "turn.completed"},
        )
    )
    refresher = build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        codex_bin="codex-test",
        executor=lambda *_args, **_kwargs: ProcessRunResult(0, stdout, ""),
        temporary_root=tmp_path,
    )

    snapshots = refresher.refresh_expired(force=True)

    assert snapshots["codex_oauth"].healthy is True
    assert PRODUCTION_RUNTIME_CAPABILITIES["codex_oauth"].healthy is True


def test_production_execution_factory_never_probes_or_spawns(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    PRODUCTION_RUNTIME_CAPABILITIES.refresh({})
    stdout = "\n".join(
        json.dumps(payload)
        for payload in (
            {"type": "thread.started", "thread_id": "probe-session"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"ok":true}'},
            },
            {"type": "turn.completed"},
        )
    )
    calls = []

    build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=30,
        idle_timeout_seconds=10,
        codex_bin="codex-test",
        executor=lambda *_args, **_kwargs: (
            calls.append("probe") or ProcessRunResult(0, stdout, "")
        ),
    )

    assert calls == []
    assert len(PRODUCTION_RUNTIME_CAPABILITIES) == 0


def test_production_refresher_publishes_reviewed_surfaces_from_exact_transports(
    tmp_path, monkeypatch
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "[mcp_servers.agent_cli]\ncommand='agent-cli'\n"
        "[mcp_servers.memory_connector]\nurl='https://memory.invalid/mcp'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    registry = RuntimeCapabilityRegistry()
    build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        capability_registry=registry,
        temporary_root=tmp_path,
    )

    manifest = registry.surface_manifest("codex_oauth")
    assert manifest is not None
    assert {
        "reviewed_read_tools",
        "reviewed_write_tools",
        "memory_connector_read",
        "mcp:memory_connector:memory_write",
        "dws_read",
        "agent_cli.dws",
        "agent_cli.lark-cli",
    } <= manifest.capabilities
    assert not any(
        capability.startswith("reviewed_skill:") for capability in manifest.capabilities
    )


def test_reviewed_surfaces_keep_all_production_callers_eligible_without_claiming_health(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        "[mcp_servers.agent_cli]\ncommand='agent-cli'\n"
        "[mcp_servers.memory_connector]\nurl='https://memory.invalid/mcp'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    registry = RuntimeCapabilityRegistry()
    build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        capability_registry=registry,
        temporary_root=tmp_path,
    )
    registry.refresh(
        {
            "codex_oauth": _snapshot("codex_oauth").model_copy(
                update={
                    "capabilities": frozenset(
                        {
                            "structured_output",
                            "local_schema_validation",
                        }
                    )
                }
            )
        }
    )
    routed = build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=10,
        idle_timeout_seconds=5,
        capability_registry=registry,
    )
    manifest = registry.surface_manifest("codex_oauth")
    assert manifest is not None
    assert not any(
        capability.startswith("reviewed_skill:") for capability in manifest.capabilities
    )
    caller_requirements = (
        {"structured_output", "reviewed_read_tools"},
        {"structured_output", "reviewed_write_tools", "audit_effect_visibility"},
        {"structured_output", "reviewed_read_tools"},
        {"structured_output", "memory_connector_read"},
        {"structured_output", "mcp:memory_connector:memory_write"},
        {"structured_output", "dws_read", "agent_cli.dws"},
    )

    for required in caller_requirements:
        decision = routed._router.first_route_decision(
            required_capabilities=frozenset(required)
        )
        assert decision.route is not None, (required, decision.reason)


def test_reviewed_surface_accepts_a_validated_skill_receipt_requirement(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        "[mcp_servers.agent_cli]\ncommand='agent-cli'\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    registry = RuntimeCapabilityRegistry()
    build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        capability_registry=registry,
        temporary_root=tmp_path,
    )
    registry.refresh({"codex_oauth": _snapshot("codex_oauth")})
    routed = build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=10,
        idle_timeout_seconds=5,
        capability_registry=registry,
    )

    decision = routed._router.first_route_decision(
        required_capabilities=frozenset(
            {"structured_output", "reviewed_skill:ceo-message-triage:abc123"}
        )
    )

    assert decision.route is not None, decision.reason
    assert decision.route.name == "codex_oauth"

def test_agent_cli_action_capabilities_do_not_depend_on_bulk_metadata(
    tmp_path, monkeypatch
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "[mcp_servers.agent_cli]\ncommand='agent-cli'\n", encoding="utf-8"
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    registry = RuntimeCapabilityRegistry()

    build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        capability_registry=registry,
        temporary_root=tmp_path,
    )

    manifest = registry.surface_manifest("codex_oauth")
    assert manifest is not None
    expected = {"agent_cli.dws", "agent_cli.lark-cli"}
    assert expected <= manifest.capabilities
    assert "agent_cli.dwz" not in manifest.capabilities
    registry.refresh(
        {
            "codex_oauth": _snapshot("codex_oauth").model_copy(
                update={
                    "capabilities": frozenset(
                        {
                            "structured_output",
                            "local_schema_validation",
                            "consumer_read_only_enforcement",
                        }
                    )
                }
            )
        }
    )
    routed = build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=10,
        idle_timeout_seconds=5,
        capability_registry=registry,
    )
    for capability in expected:
        decision = routed._router.first_route_decision(
            required_capabilities=frozenset({capability})
        )
        assert decision.route is not None, capability
    for unknown in ("agent_cli.dwz", "agent_cli.unknown"):
        decision = routed._router.first_route_decision(
            required_capabilities=frozenset({unknown})
        )
        assert decision.route is None
        assert f"surface_missing:{unknown}" in decision.reason


def test_agent_cli_action_capabilities_use_service_owned_transport(
    tmp_path, monkeypatch
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    registry = RuntimeCapabilityRegistry()

    build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        capability_registry=registry,
        temporary_root=tmp_path,
    )

    manifest = registry.surface_manifest("codex_oauth")
    assert manifest is not None
    # The user-global Codex config is intentionally empty.  Production Agent
    # turns add the reviewed local agent_cli transport themselves.
    assert {"agent_cli.dws", "agent_cli.lark-cli"} <= manifest.capabilities
    registry.refresh({"codex_oauth": _snapshot("codex_oauth")})
    routed = build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=10,
        idle_timeout_seconds=5,
        capability_registry=registry,
    )
    decision = routed._router.first_route_decision(
        required_capabilities=frozenset({"agent_cli.dws"})
    )
    assert decision.route is not None


def test_skill_files_do_not_create_static_route_capabilities(tmp_path, monkeypatch):
    home = tmp_path / "home"
    arbitrary = home / ".agents" / "skills" / "unreviewed" / "SKILL.md"
    arbitrary.parent.mkdir(parents=True)
    arbitrary.write_text("# ambient file\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth")
    registry = RuntimeCapabilityRegistry()

    build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        capability_registry=registry,
        temporary_root=tmp_path,
    )

    manifest = registry.surface_manifest("codex_oauth")
    assert manifest is not None
    assert not any("unreviewed" in item for item in manifest.capabilities)


def test_claude_surface_claims_only_service_owned_exact_read_tools(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    reviewed = home / ".agents" / "skills" / "dingtang-okr-review" / "SKILL.md"
    reviewed.parent.mkdir(parents=True)
    reviewed.write_text("# reviewed exact workload skill\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "claude_api")
    monkeypatch.setenv("CEO_CLAUDE_API_KEY", "test-anthropic-secret")
    registry = RuntimeCapabilityRegistry()

    build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        capability_registry=registry,
        temporary_root=tmp_path,
    )

    manifest = registry.surface_manifest("claude_api")
    assert manifest is not None
    assert not any(
        item.startswith("reviewed_skill:") for item in manifest.capabilities
    )
    assert "reviewed_read_tools" in manifest.capabilities
    assert "mcp:agent_cli:reviewed_read" in manifest.capabilities
    assert "reviewed_write_tools" not in manifest.capabilities
    assert "mcp:agent_cli:reviewed_write" not in manifest.capabilities
    assert "audit_effect_visibility" not in manifest.capabilities
    assert "dws_read" in manifest.capabilities
    assert "memory_connector_read" not in manifest.capabilities


def test_production_agent_runtime_is_pure_and_injects_exact_claude_transports(
    tmp_path, monkeypatch
):
    service_config = tmp_path / "service-mcp.json"
    service_config.write_text(
        json.dumps(
            {
                "servers": {
                    "memory_connector": {
                        "url": "https://memory.example.test/mcp"
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_SERVICE_MCP_CONFIG_PATH", str(service_config))
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "claude_api")
    monkeypatch.setenv("CEO_CLAUDE_API_KEY", "test-anthropic-secret")
    registry = RuntimeCapabilityRegistry()
    build_production_runtime_refresher(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        capability_registry=registry,
        temporary_root=tmp_path,
    )

    runtime = build_production_agent_runtime(
        store=AutoReplyStore(tmp_path / "agent.sqlite3"),
        workspace=tmp_path,
        capability_registry=registry,
    )

    assert runtime.claude_adapter is not None
    assert runtime.claude_adapter.active_proxy_process_count == 0
    assert {
        server.name for server in runtime.claude_adapter._service_mcp_servers or ()
    } == {"agent_cli", "memory_connector"}
    manifest = registry.surface_manifest("claude_api")
    assert manifest is not None
    assert "mcp:memory_connector:read" in manifest.capabilities
    assert "reviewed_write_tools" not in manifest.capabilities
