import json
from datetime import UTC, datetime, timedelta

import pytest

from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_production import (
    PRODUCTION_RUNTIME_CAPABILITIES,
    RuntimeCapabilityRegistry,
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


def test_production_execution_factory_never_probes_or_spawns(
    tmp_path, monkeypatch
):
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
    } <= manifest.capabilities
    assert any(
        capability.startswith("reviewed_skill:")
        for capability in manifest.capabilities
    )


def test_reviewed_surfaces_keep_all_production_callers_eligible_without_claiming_health(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    skill = home / ".agents" / "skills" / "exact-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Exact reviewed skill\n", encoding="utf-8")
    codex_home = home / ".codex"
    codex_home.mkdir()
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
    manifest = registry.surface_manifest("codex_oauth")
    assert manifest is not None
    exact_skill = next(
        item
        for item in manifest.capabilities
        if item.startswith("reviewed_skill:exact-skill:")
    )
    caller_requirements = (
        {"structured_output", "consumer_read_only_enforcement"},
        {"structured_output", "reviewed_read_tools"},
        {"structured_output", "reviewed_write_tools", "audit_effect_visibility"},
        {"structured_output", "reconciliation_read_only", "reviewed_read_tools"},
        {"structured_output", "memory_connector_read"},
        {"structured_output", "mcp:memory_connector:memory_write"},
        {"structured_output", "dws_read", exact_skill},
    )

    for required in caller_requirements:
        decision = routed._router.first_route_decision(
            required_capabilities=frozenset(required)
        )
        assert decision.route is not None, (required, decision.reason)

    wrong_skill = exact_skill.rsplit(":", 1)[0] + ":different-sha"
    decision = routed._router.first_route_decision(
        required_capabilities=frozenset({wrong_skill})
    )
    assert decision.route is None
    assert f"surface_missing:{wrong_skill}" in decision.reason
