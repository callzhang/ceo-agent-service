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


def test_codex_api_base_url_is_normalized_for_the_runtime_route():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_api",
            "CEO_CODEX_API_KEY": "secret-value",
            "CEO_CODEX_API_BASE_URL": "https://gateway.example/v1/",
        }
    )

    assert config.codex_api_base_url == "https://gateway.example/v1"


@pytest.mark.parametrize(
    "base_url",
    [
        "gateway.example/v1",
        "https://user:pass@gateway.example/v1",
        "https://gateway.example/v1?tenant=ceo",
        "https://gateway.example/v1#fragment",
    ],
)
def test_codex_api_base_url_rejects_unsafe_or_ambiguous_urls(base_url: str):
    with pytest.raises(ValueError, match="CEO_CODEX_API_BASE_URL"):
        load_runtime_config(
            {
                "CEO_AGENT_RUNTIME_ROUTES": "codex_api",
                "CEO_CODEX_API_KEY": "secret-value",
                "CEO_CODEX_API_BASE_URL": base_url,
            }
        )


def test_codex_api_route_rejects_a_missing_api_key():
    with pytest.raises(ValueError, match="codex_api requires CEO_CODEX_API_KEY"):
        load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_api"})


def test_codex_api_route_rejects_an_unsuffixed_gpt_5_6_model():
    with pytest.raises(ValueError, match="CEO_CODEX_API_MODEL"):
        load_runtime_config(
            {
                "CEO_AGENT_RUNTIME_ROUTES": "codex_api",
                "CEO_CODEX_API_KEY": "secret-value",
                "CEO_CODEX_API_MODEL": "gpt-5.6",
            }
        )


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


def test_load_runtime_config_accepts_friday_runtime():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,friday_runtime",
            "CEO_FRIDAY_RUNTIME_BASE_URL": "http://127.0.0.1:8080/",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-project",
            "CEO_FRIDAY_RUNTIME_MODEL": "MiniMax-M3",
            "CEO_FRIDAY_RUNTIME_TICKET": "runtime-ticket",
        }
    )

    route = next(item for item in config.routes if item.name == "friday_runtime")
    assert route.runtime_kind is RuntimeKind.FRIDAY_RUNTIME
    assert route.credential_mode is CredentialMode.SERVICE_API
    assert route.model == "MiniMax-M3"
    assert config.friday_runtime_base_url == "http://127.0.0.1:8080"
    assert config.friday_runtime_project_id == "ceo-project"
    assert config.secret_for("friday_runtime").get_secret_value() == "runtime-ticket"
    assert "runtime-ticket" not in repr(config)


def test_friday_runtime_accepts_explicit_auth_disabled_for_local_runtime():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-project",
            "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1",
        }
    )

    assert config.friday_runtime_auth_disabled is True
    assert config.secret_for("friday_runtime") is None


def test_friday_runtime_does_not_require_a_service_model_override():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-project",
            "CEO_FRIDAY_RUNTIME_TICKET": "runtime-ticket",
        }
    )

    assert config.friday_runtime_model == "default"
    assert config.routes[0].model == "default"


def test_friday_runtime_accepts_session_token_credential():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
            "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-project",
            "CEO_FRIDAY_SESSION_TOKEN": "session-token",
        }
    )

    assert config.secret_for("friday_runtime").get_secret_value() == "session-token"


@pytest.mark.parametrize(
    "credential_name",
    ["CEO_FRIDAY_RUNTIME_TICKET", "CEO_FRIDAY_SESSION_TOKEN"],
)
def test_friday_runtime_auth_disabled_rejects_credentials(credential_name: str):
    with pytest.raises(ValueError, match="auth_disabled"):
        load_runtime_config(
            {
                "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
                "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-project",
                "CEO_FRIDAY_RUNTIME_AUTH_DISABLED": "1",
                credential_name: "credential",
            }
        )


def test_friday_runtime_requires_project_and_one_auth_credential():
    with pytest.raises(ValueError, match="PROJECT_ID"):
        load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "friday_runtime"})
    with pytest.raises(ValueError, match="exactly one"):
        load_runtime_config(
            {
                "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
                "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-project",
            }
        )


@pytest.mark.parametrize(
    "base_url",
    ["friday.local", "http://user:pass@friday.local", "http://friday.local?v=1"],
)
def test_friday_runtime_base_url_rejects_unsafe_urls(base_url: str):
    with pytest.raises(ValueError, match="CEO_FRIDAY_RUNTIME_BASE_URL"):
        load_runtime_config(
            {
                "CEO_AGENT_RUNTIME_ROUTES": "friday_runtime",
                "CEO_FRIDAY_RUNTIME_PROJECT_ID": "ceo-project",
                "CEO_FRIDAY_RUNTIME_TICKET": "ticket",
                "CEO_FRIDAY_RUNTIME_BASE_URL": base_url,
            }
        )


def test_runtime_duration_settings_are_parsed_from_the_supplied_environment():
    config = load_runtime_config(
        {
            "CEO_RUNTIME_PROBE_INTERVAL": "15m",
            "CEO_RUNTIME_ROUTE_RETRY_DELAY": "2h",
        }
    )

    assert config.probe_interval == timedelta(minutes=15)
    assert config.retry_delay == timedelta(hours=2)
