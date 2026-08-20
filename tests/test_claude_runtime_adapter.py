import pytest

from app.agent_runtime_config import load_runtime_config
from app.claude_runtime_adapter import ClaudeRuntimeAdapter


@pytest.fixture
def config():
    return load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "claude_api",
            "CEO_CLAUDE_MODEL": "claude-sonnet-test",
            "CEO_CLAUDE_API_KEY": "anthropic-secret",
        }
    )


@pytest.fixture
def route(config):
    return config.routes[0]


@pytest.fixture
def adapter(tmp_path, config):
    return ClaudeRuntimeAdapter(
        workspace=tmp_path,
        config=config,
        claude_bin="claude-test",
    )


def test_claude_command_is_noninteractive_stream_json_and_prompt_free(
    adapter, route
):
    prompt = "private business prompt"

    command = adapter.build_command(
        route=route,
        session_id=None,
        max_turns=4,
    )

    assert command == [
        "claude-test",
        "-p",
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
        "--model",
        "claude-sonnet-test",
        "--max-turns",
        "4",
        "--verbose",
    ]
    assert prompt not in command


def test_claude_command_resumes_only_the_selected_session(adapter, route):
    command = adapter.build_command(
        route=route,
        session_id="claude-session-1",
        max_turns=2,
    )

    assert command[-2:] == ["--resume", "claude-session-1"]


def test_claude_child_receives_only_configured_anthropic_credential(
    adapter, route, monkeypatch
):
    ambient = {
        "OPENAI_API_KEY": "openai-secret",
        "CODEX_API_KEY": "codex-secret",
        "CEO_CODEX_API_KEY": "ceo-codex-secret",
        "ANTHROPIC_API_KEY": "ambient-anthropic-secret",
        "ANTHROPIC_AUTH_TOKEN": "ambient-token",
        "CEO_CLAUDE_API_KEY": "ambient-ceo-secret",
        "UNRELATED_SERVICE_TOKEN": "unrelated-secret",
    }
    for key, value in ambient.items():
        monkeypatch.setenv(key, value)

    env = adapter.build_env(route)

    assert env["ANTHROPIC_API_KEY"] == "anthropic-secret"
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_API_KEY" not in env
    assert "CEO_CODEX_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CEO_CLAUDE_API_KEY" not in env
    assert "UNRELATED_SERVICE_TOKEN" not in env


def test_claude_adapter_rejects_unconfigured_or_codex_route(adapter, config):
    codex = load_runtime_config({}).routes[0]
    with pytest.raises(ValueError, match="unsupported runtime route"):
        adapter.build_env(codex)

    other = config.routes[0].model_copy(update={"model": "different"})
    with pytest.raises(ValueError, match="not configured"):
        adapter.build_command(route=other, session_id=None, max_turns=1)


def test_claude_adapter_requires_positive_bounded_turns(adapter, route):
    with pytest.raises(ValueError, match="max_turns"):
        adapter.build_command(route=route, session_id=None, max_turns=0)
