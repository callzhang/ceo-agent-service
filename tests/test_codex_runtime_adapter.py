from pathlib import Path

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.codex_runtime_adapter import CodexRuntimeAdapter


@pytest.fixture
def config():
    return load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api",
            "CEO_CODEX_API_KEY": "fallback",
        }
    )


@pytest.fixture
def adapter(tmp_path: Path, config):
    return CodexRuntimeAdapter(tmp_path, config, codex_bin="codex-test")


def route(config, name: str) -> RuntimeRoute:
    return next(item for item in config.routes if item.name == name)


def test_oauth_route_removes_provider_keys(adapter, config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient")
    monkeypatch.setenv("CODEX_API_KEY", "ambient-codex")
    monkeypatch.setenv("CEO_CODEX_API_KEY", "fallback")

    env = adapter.build_env(route(config, "codex_oauth"))

    assert "OPENAI_API_KEY" not in env
    assert "CODEX_API_KEY" not in env
    assert "CEO_CODEX_API_KEY" not in env


def test_api_route_injects_only_selected_secret(adapter, config):
    env = adapter.build_env(route(config, "codex_api"), api_key="fallback")

    assert env["OPENAI_API_KEY"] == "fallback"
    assert "CEO_CODEX_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_api_route_uses_configured_secret_when_no_override_is_supplied(adapter, config):
    env = adapter.build_env(route(config, "codex_api"))

    assert env["OPENAI_API_KEY"] == "fallback"


def test_api_route_rejects_a_secret_that_does_not_match_its_route(adapter, config):
    with pytest.raises(ValueError, match="does not match"):
        adapter.build_env(route(config, "codex_api"), api_key="different")


def test_oauth_route_rejects_an_api_key(adapter, config):
    with pytest.raises(ValueError, match="does not accept"):
        adapter.build_env(route(config, "codex_oauth"), api_key="fallback")


def test_adapter_rejects_runtime_and_credential_mismatches(adapter):
    invalid_route = RuntimeRoute(
        name="codex_oauth",
        runtime_kind=RuntimeKind.CLAUDE_CLI,
        credential_mode=CredentialMode.LOCAL_OAUTH,
        model="gpt-5.5",
    )

    with pytest.raises(ValueError, match="unsupported runtime route"):
        adapter.build_env(invalid_route)


def test_adapter_delegates_command_with_explicit_route_model_and_provider(
    adapter, config
):
    command = adapter.build_command(
        route(config, "codex_api"),
        prompt="hello",
        session_id=None,
        image_paths=None,
        output_schema_path=None,
        use_output_schema=True,
        approval_policy="untrusted",
        developer_instructions="Follow the contract.",
        use_approval_bypass=True,
    )

    assert command[:2] == ["codex-test", "exec"]
    assert command[command.index("-m") + 1] == "gpt-5.5"
    assert 'model_provider="openai"' in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command


def test_local_oauth_expiration_allows_failover(adapter):
    failure = adapter.classify_failure(
        stderr="failed to refresh token: session has ended",
        stdout="",
        returncode=1,
    )

    assert failure.code == "codex_login_required"
    assert failure.failover_permitted is True


def test_provider_capacity_is_typed_and_allows_failover(adapter):
    failure = adapter.classify_failure(
        stderr="workspace is out of credits",
        stdout="",
        returncode=1,
    )

    assert failure.code == "codex_provider_capacity_exhausted"
    assert failure.failure_class.value == "capacity"
    assert failure.failover_permitted is True
    assert failure.route_pause_required is True


def test_provider_authentication_is_typed_and_allows_failover(adapter):
    failure = adapter.classify_failure(
        stderr="missing bearer or basic authentication for /v1/responses",
        stdout="",
        returncode=1,
    )

    assert failure.code == "codex_provider_auth_failed"
    assert failure.failure_class.value == "authentication"
    assert failure.failover_permitted is True


@pytest.mark.parametrize(
    "stderr",
    [
        (
            "unexpected status 401 Unauthorized: Invalid API key, url: "
            "https://api.openai.com/v1/responses"
        ),
        (
            "unexpected status 403 Forbidden: blocked, url: "
            "https://chatgpt.com/backend-api/codex/responses"
        ),
    ],
)
def test_existing_provider_auth_signatures_allow_failover(adapter, stderr):
    failure = adapter.classify_failure(stderr=stderr, stdout="", returncode=1)

    assert failure.failure_class.value == "authentication"
    assert failure.code == "codex_provider_auth_failed"
    assert failure.failover_permitted is True
    assert failure.route_pause_required is True


def test_near_miss_provider_auth_signature_is_fail_closed(adapter):
    failure = adapter.classify_failure(
        stderr=(
            "unexpected status 401 Unauthorized: Invalid API key, url: "
            "https://api.openai.com/v1/chat/completions"
        ),
        stdout="",
        returncode=1,
    )

    assert failure.failure_class.value == "unclassified"
    assert failure.code == "runtime_unclassified"
    assert failure.failover_permitted is False


def test_unknown_failure_is_fail_closed(adapter):
    failure = adapter.classify_failure(
        stderr="unexpected command result",
        stdout="",
        returncode=1,
    )

    assert failure.code == "runtime_unclassified"
    assert failure.failure_class.value == "unclassified"
    assert failure.retryable_on_same_route is False
    assert failure.failover_permitted is False
    assert failure.route_pause_required is False
