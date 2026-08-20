from datetime import UTC, datetime, timedelta

import pytest

from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
from app.agent_runtime_production import (
    RuntimeCapabilityRegistry,
    build_production_routed_codex_execution,
)
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
    routed = build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=10,
        idle_timeout_seconds=5,
        capability_registry=registry,
    )

    initial = routed._router.first_route_decision(
        required_capabilities=frozenset({"structured_output"}),
        allow_legacy_oauth_bootstrap=routed._allow_legacy_oauth_bootstrap,
    )
    assert initial.route is not None
    assert initial.route.name == "codex_oauth"
    assert initial.reason == "legacy_oauth_bootstrap"

    registry.refresh({"codex_api": _snapshot("codex_api")})
    refreshed = routed._router.first_route_decision(
        required_capabilities=frozenset({"structured_output"})
    )
    assert refreshed.route is not None
    assert refreshed.route.name == "codex_api"


def test_production_bootstrap_never_admits_missing_api_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CEO_AGENT_RUNTIME_ROUTES", "codex_api")
    monkeypatch.setenv("CEO_CODEX_API_KEY", "test-secret")
    routed = build_production_routed_codex_execution(
        store=AutoReplyStore(tmp_path / "store.sqlite3"),
        workspace=tmp_path,
        total_timeout_seconds=10,
        idle_timeout_seconds=5,
        capability_registry=RuntimeCapabilityRegistry(),
    )

    decision = routed._router.first_route_decision(
        required_capabilities=frozenset(),
        allow_legacy_oauth_bootstrap=routed._allow_legacy_oauth_bootstrap,
    )
    assert decision.route is None
    assert "codex_api=snapshot_missing" in decision.reason


def test_capability_registry_rejects_mismatched_key():
    with pytest.raises(ValueError, match="key mismatch"):
        RuntimeCapabilityRegistry({"codex_api": _snapshot("codex_oauth")})
