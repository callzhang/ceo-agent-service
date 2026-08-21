from datetime import timedelta

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import CredentialMode, RuntimeKind


def test_default_runtime_uses_only_codex_oauth():
    config = load_runtime_config({})

    assert [route.name for route in config.routes] == ["codex_oauth"]
    assert config.probe_interval == timedelta(minutes=5)
    assert config.retry_delay == timedelta(minutes=30)


def test_dual_auth_requires_a_private_api_key():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api",
            "CEO_CODEX_API_MODEL": "gpt-5.5",
            "CEO_CODEX_API_KEY": "secret-value",
        }
    )

    assert config.routes[1].name == "codex_api"
    assert "secret-value" not in repr(config)
    assert config.secret_for("codex_api").get_secret_value() == "secret-value"


def test_codex_api_route_rejects_a_missing_api_key():
    with pytest.raises(ValueError, match="codex_api requires CEO_CODEX_API_KEY"):
        load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_api"})


def test_runtime_routes_must_be_unique_and_supported():
    with pytest.raises(ValueError, match="unique routes"):
        load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_oauth"})
    with pytest.raises(ValueError, match="unsupported runtime routes"):
        load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "unknown_api"})


def test_claude_route_requires_anthropic_secret():
    with pytest.raises(ValueError, match="CEO_CLAUDE_API_KEY"):
        load_runtime_config(
            {"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,claude_api"}
        )


def test_claude_route_uses_independent_model_and_secret():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,claude_api",
            "CEO_CLAUDE_MODEL": "claude-sonnet-test",
            "CEO_CLAUDE_API_KEY": "anthropic-secret",
        }
    )

    route = config.routes[1]
    assert route.name == "claude_api"
    assert route.runtime_kind is RuntimeKind.CLAUDE_CLI
    assert route.credential_mode is CredentialMode.SERVICE_API
    assert route.model == "claude-sonnet-test"
    assert config.secret_for("claude_api").get_secret_value() == "anthropic-secret"
    assert "anthropic-secret" not in repr(config)


def test_runtime_duration_settings_are_parsed_from_the_supplied_environment():
    config = load_runtime_config(
        {
            "CEO_RUNTIME_PROBE_INTERVAL": "15m",
            "CEO_RUNTIME_ROUTE_RETRY_DELAY": "2h",
        }
    )

    assert config.probe_interval == timedelta(minutes=15)
    assert config.retry_delay == timedelta(hours=2)
