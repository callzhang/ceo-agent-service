import os
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
            "CEO_CODEX_API_KEY": "service-secret",
        }
    )


@pytest.fixture
def adapter(tmp_path: Path, config):
    return CodexRuntimeAdapter(tmp_path, config, codex_bin="codex-test")


def route(config, name: str) -> RuntimeRoute:
    return next(item for item in config.routes if item.name == name)


@pytest.mark.parametrize("route_name", ["codex_oauth", "codex_api"])
def test_routes_rebuild_environment_without_ambient_credentials(
    adapter, config, monkeypatch, route_name
):
    ambient = {
        "OPENAI_API_KEY": "ambient",
        "CODEX_API_KEY": "ambient-codex",
        "CEO_CODEX_API_KEY": "service-secret",
        "CEO_CLAUDE_API_KEY": "claude",
        "ANTHROPIC_API_KEY": "anthropic",
        "ANTHROPIC_AUTH_TOKEN": "anthropic-token",
        "AZURE_OPENAI_API_KEY": "azure",
        "EXA_API_KEY": "exa",
        "TAILSCALE_API_KEY": "tailscale",
        "AWS_SECRET_ACCESS_KEY": "aws",
        "SSH_AUTH_SOCK": "/private/ssh-agent.sock",
        "HTTPS_PROXY": "http://proxy.invalid",
        "LC_UNREVIEWED": "blocked",
        "LC_BACKDOOR": "blocked",
    }
    for key, value in ambient.items():
        monkeypatch.setenv(key, value)

    base_env = adapter.runner.build_env()
    env = adapter.build_env(route(config, route_name))

    blocked = set(ambient) - {"OPENAI_API_KEY"}
    assert not blocked.intersection(env)
    if route_name == "codex_oauth":
        assert "OPENAI_API_KEY" not in env
    else:
        assert env["OPENAI_API_KEY"] == "service-secret"
    assert os.environ["SSH_AUTH_SOCK"] == ambient["SSH_AUTH_SOCK"]
    assert all(base_env[key] == value for key, value in ambient.items())


def test_child_environment_retains_only_reviewed_locale_keys(adapter, config, monkeypatch):
    monkeypatch.setenv("LC_TIME", "en_US.UTF-8")
    monkeypatch.setenv("LC_UNREVIEWED", "blocked")
    monkeypatch.setenv("LC_BACKDOOR", "blocked")

    env = adapter.build_env(route(config, "codex_oauth"))

    assert env["LC_TIME"] == "en_US.UTF-8"
    assert "LC_UNREVIEWED" not in env
    assert "LC_BACKDOOR" not in env


def test_api_route_injects_only_selected_secret(adapter, config):
    env = adapter.build_env(route(config, "codex_api"), api_key="service-secret")

    assert env["OPENAI_API_KEY"] == "service-secret"
    assert "CEO_CODEX_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_api_environment_is_allowlisted_and_parent_environment_is_unchanged(
    adapter, config, monkeypatch
):
    monkeypatch.setenv("CODEX_HOME", "/safe/codex-home")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("UNRELATED_CONFIGURATION", "must-not-pass")

    base_env = adapter.runner.build_env()
    env = adapter.build_env(route(config, "codex_api"))

    assert env["OPENAI_API_KEY"] == "service-secret"
    assert env["CODEX_HOME"] == "/safe/codex-home"
    assert env["LANG"] == "en_US.UTF-8"
    assert "UNRELATED_CONFIGURATION" not in env
    assert "OPENAI_API_KEY" not in base_env
    assert "OPENAI_API_KEY" not in os.environ


def test_child_environment_sets_default_codex_home_from_preserved_home(
    adapter, config, tmp_path, monkeypatch
):
    home = tmp_path / "installing-user"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    env = adapter.build_env(route(config, "codex_oauth"))

    assert env["CODEX_HOME"] == str((home / ".codex").resolve())
    assert "CODEX_HOME" not in os.environ


def test_child_environment_resolves_explicit_codex_home_without_parent_mutation(
    adapter, config, tmp_path, monkeypatch
):
    home = tmp_path / "installing-user"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", "runtime-codex-home")

    env = adapter.build_env(route(config, "codex_oauth"))

    assert env["CODEX_HOME"] == str((home / "runtime-codex-home").resolve())
    assert os.environ["CODEX_HOME"] == "runtime-codex-home"


def test_child_environment_resolves_exact_tilde_to_preserved_home(
    adapter, config, tmp_path, monkeypatch
):
    home = tmp_path / "installing-user"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", "~")

    env = adapter.build_env(route(config, "codex_oauth"))

    assert env["CODEX_HOME"] == str(home.resolve())


def test_api_route_uses_configured_secret_when_no_override_is_supplied(adapter, config):
    env = adapter.build_env(route(config, "codex_api"))

    assert env["OPENAI_API_KEY"] == "service-secret"


def test_api_route_rejects_a_secret_that_does_not_match_its_route(adapter, config):
    with pytest.raises(ValueError, match="does not match") as exc_info:
        adapter.build_env(route(config, "codex_api"), api_key="different")

    assert "service-secret" not in str(exc_info.value)
    assert "service-secret" not in repr(adapter)
    assert "service-secret" not in adapter.config.model_dump_json()


def test_oauth_route_rejects_an_api_key(adapter, config):
    with pytest.raises(ValueError, match="does not accept"):
        adapter.build_env(route(config, "codex_oauth"), api_key="service-secret")


def test_adapter_rejects_runtime_and_credential_mismatches(adapter):
    invalid_route = RuntimeRoute(
        name="codex_oauth",
        runtime_kind=RuntimeKind.CLAUDE_CLI,
        credential_mode=CredentialMode.LOCAL_OAUTH,
        model="gpt-5.5",
    )

    with pytest.raises(ValueError, match="unsupported runtime route"):
        adapter.build_env(invalid_route)


@pytest.mark.parametrize("session_id", [None, "session-1"])
def test_adapter_builds_equivalent_route_command_options(adapter, config, tmp_path, session_id):
    command = adapter.build_command(
        route(config, "codex_api"),
        prompt="hello",
        session_id=session_id,
        image_paths=[tmp_path / "image.png"],
        output_schema_path=tmp_path / "result.schema.json",
        use_output_schema=True,
        approval_policy="untrusted",
        developer_instructions="Follow the contract.",
        use_approval_bypass=True,
    )

    assert command[: 3 if session_id else 2] == (
        ["codex-test", "exec", "resume"] if session_id else ["codex-test", "exec"]
    )
    assert command[command.index("-m") + 1] == "gpt-5.5"
    assert 'model_provider="ceo_openai_api"' in command
    assert 'model_providers.ceo_openai_api.name="CEO OpenAI API fallback"' in command
    assert 'model_providers.ceo_openai_api.base_url="https://api.openai.com/v1"' in command
    assert 'model_providers.ceo_openai_api.env_key="OPENAI_API_KEY"' in command
    assert 'model_providers.ceo_openai_api.wire_api="responses"' in command
    assert not any("requires_openai_auth" in item for item in command)
    assert 'shell_environment_policy.inherit="core"' in command
    assert "shell_environment_policy.ignore_default_excludes=false" in command
    assert all("service-secret" not in item for item in command)
    assert "--image" in command
    assert str(tmp_path / "image.png") in command
    assert str(tmp_path / "result.schema.json") in command
    assert "Follow the contract." in " ".join(command)
    assert "--dangerously-bypass-approvals-and-sandbox" in command


def test_oauth_route_keeps_the_existing_provider_selection(adapter, config, monkeypatch):
    monkeypatch.setenv("CEO_CODEX_MODEL_PROVIDER", "minimax")

    command = adapter.build_command(
        route(config, "codex_oauth"),
        prompt="hello",
        session_id=None,
        image_paths=None,
        output_schema_path=None,
        use_output_schema=True,
        approval_policy="never",
        developer_instructions="Read only.",
        use_approval_bypass=False,
    )

    assert 'model_provider="minimax"' in command
    assert not any("model_providers.ceo_openai_api" in item for item in command)


def test_api_route_command_uses_the_configured_base_url(tmp_path):
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_api",
            "CEO_CODEX_API_KEY": "service-secret",
            "CEO_CODEX_API_BASE_URL": "https://gateway.example/v1/",
        }
    )
    adapter = CodexRuntimeAdapter(tmp_path, config, codex_bin="codex-test")

    command = adapter.build_command(
        route(config, "codex_api"),
        prompt="hello",
        session_id=None,
        image_paths=None,
        output_schema_path=None,
        use_output_schema=False,
        approval_policy="never",
        developer_instructions=None,
        use_approval_bypass=False,
    )

    assert (
        'model_providers.ceo_openai_api.base_url="https://gateway.example/v1"'
        in command
    )


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


@pytest.mark.parametrize(
    "stderr, stdout",
    [
        ("workspace is out of credits", ""),
        ("failed to refresh token: session has ended", ""),
        ("stream disconnected before completion", ""),
        ("unexpected status 401 Unauthorized: Invalid API key /v1/responses", ""),
    ],
)
def test_success_never_authorizes_failure_actions(adapter, stderr, stdout):
    failure = adapter.classify_failure(stderr=stderr, stdout=stdout, returncode=0)

    assert failure.retryable_on_same_route is False
    assert failure.failover_permitted is False
    assert failure.route_pause_required is False


def test_terminal_success_never_authorizes_failure_actions(adapter):
    failure = adapter.classify_failure(
        stderr="workspace is out of credits",
        stdout="",
        returncode=1,
        terminal_succeeded=True,
    )

    assert failure.code == "runtime_unclassified"
    assert failure.retryable_on_same_route is False
    assert failure.failover_permitted is False
    assert failure.route_pause_required is False


def test_structured_provider_error_is_classified_but_tool_output_is_not(adapter):
    structured = adapter.classify_failure(
        stderr="",
        stdout=(
            '{"type":"error","error":{"message":"Incorrect API key provided; '
            'code=invalid_api_key; /v1/responses"}}\n'
            '{"type":"response_item","item":{"type":"function_call_output",'
            '"output":"DWS tool failed: quota exceeded"}}'
        ),
        returncode=1,
    )
    tool_output = adapter.classify_failure(
        stderr="",
        stdout='{"type":"response_item","item":{"output":"DWS tool failed: quota exceeded"}}',
        returncode=1,
    )

    assert structured.code == "codex_provider_auth_failed"
    assert tool_output.code == "runtime_unclassified"


def test_structured_invalid_api_key_without_endpoint_has_provider_provenance(adapter):
    failure = adapter.classify_failure(
        stderr="",
        stdout=(
            '{"type":"error","error":{"message":"Incorrect API key provided",'
            '"code":"invalid_api_key"}}'
        ),
        returncode=1,
    )

    assert failure.code == "codex_provider_auth_failed"


@pytest.mark.parametrize(
    "stderr, code",
    [
        (
            (
                "stream disconnected before completion; unexpected status 401 "
                "Unauthorized: Invalid API key /v1/responses"
            ),
            "codex_provider_auth_failed",
        ),
        (
            "stream disconnected before completion; workspace is out of credits",
            "codex_provider_capacity_exhausted",
        ),
    ],
)
def test_terminal_cause_precedes_transport(adapter, stderr, code):
    failure = adapter.classify_failure(stderr=stderr, stdout="", returncode=1)

    assert failure.code == code
    assert failure.retryable_on_same_route is False


def test_responses_transport_error_is_typed(adapter):
    failure = adapter.classify_failure(
        stderr="error sending request for url (https://api.openai.com/v1/responses)",
        stdout="",
        returncode=1,
    )

    assert failure.code == "codex_transport_request_failed"
    assert failure.failure_class.value == "transport"


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"timed_out": True, "timeout_kind": "idle"}, "codex_idle_timeout"),
        ({"timed_out": True, "timeout_kind": "total"}, "codex_total_timeout"),
        ({}, "codex_transport_disconnected"),
    ],
)
def test_transport_failures_are_typed(adapter, kwargs, code):
    failure = adapter.classify_failure(
        stderr="" if kwargs else "stream disconnected before completion",
        stdout="",
        returncode=1,
        **kwargs,
    )

    assert failure.failure_class.value == "transport"
    assert failure.code == code
    assert failure.retryable_on_same_route is True
    assert failure.failover_permitted is True
    assert failure.route_pause_required is True


def test_empty_nonzero_process_failure_does_not_fail_over(adapter):
    failure = adapter.classify_failure(stderr="", stdout="", returncode=1)

    assert failure.failure_class.value == "process"
    assert failure.code == "codex_process_failed"
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


def test_chatgpt_oauth_rejected_model_is_classified_without_hiding_the_cause(
    adapter,
):
    failure = adapter.classify_failure(
        stderr="",
        stdout=(
            '{"type":"error","error":{"message":"The \'gpt-5.6\' model is '
            'not supported when using Codex with a ChatGPT account."}}'
        ),
        returncode=1,
    )

    assert failure.failure_class.value == "capability"
    assert failure.code == "codex_oauth_model_unsupported"
    assert failure.failover_permitted is False
    assert failure.route_pause_required is False
