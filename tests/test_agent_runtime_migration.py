"""The one-time move of the retired built-in API route settings."""

import os
from pathlib import Path

import pytest

from app.agent_runtime_config import load_runtime_config
from app.agent_runtime_migration import BACKUP_SUFFIX, migrate_retired_api_routes
from app.config import read_env_file


LEGACY_ENV = (
    "CEO_CODEX_MODEL=gpt-5.5\n"
    "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api,qwen_gpu4,claude_oauth,claude_api\n"
    'CEO_AGENT_RUNTIME_HIDDEN_ROUTES=""\n'
    "CEO_CODEX_API_BASE_URL=https://gateway.example/v1\n"
    "CEO_CODEX_API_MODEL=MiniMax-M3\n"
    "CEO_CODEX_API_KEY=codex-secret\n"
    "CEO_CLAUDE_MODEL=sonnet\n"
    "CEO_CLAUDE_API_KEY=claude-secret\n"
    "CEO_RUNTIME_QWEN_GPU4_KIND=codex_api\n"
    "CEO_RUNTIME_QWEN_GPU4_BASE_URL=http://100.93.145.69:8900/v1\n"
    "CEO_RUNTIME_QWEN_GPU4_MODEL=qwen3.8-27b\n"
    "CEO_RUNTIME_QWEN_GPU4_API_KEY=gateway-key\n"
)


@pytest.fixture
def env_path(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / ".env"
    path.write_text(LEGACY_ENV, encoding="utf-8")
    # The migration updates the process environment it runs in; keep this
    # test's changes out of the others.
    for key in list(os.environ):
        if key.startswith(("CEO_CODEX_API_", "CEO_CLAUDE_API_", "CEO_RUNTIME_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("CEO_AGENT_RUNTIME_HIDDEN_ROUTES", "")
    return path


def test_retired_api_settings_become_added_routes_under_the_same_names(env_path: Path):
    report = migrate_retired_api_routes(env_path)

    values = read_env_file(env_path)
    # Route names and order are unchanged, so every reference stays valid.
    assert values["CEO_AGENT_RUNTIME_ROUTES"] == (
        "codex_oauth,codex_api,qwen_gpu4,claude_oauth,claude_api"
    )
    assert {key: values[key] for key in values if key.startswith("CEO_RUNTIME_CODEX_API_")} == {
        "CEO_RUNTIME_CODEX_API_KIND": "codex_api",
        "CEO_RUNTIME_CODEX_API_BASE_URL": "https://gateway.example/v1",
        "CEO_RUNTIME_CODEX_API_MODEL": "MiniMax-M3",
        "CEO_RUNTIME_CODEX_API_API_KEY": "codex-secret",
    }
    # Claude API had no model of its own and used the login's model.
    assert values["CEO_RUNTIME_CLAUDE_API_KIND"] == "claude_api"
    assert values["CEO_RUNTIME_CLAUDE_API_MODEL"] == "sonnet"
    assert values["CEO_RUNTIME_CLAUDE_API_API_KEY"] == "claude-secret"
    assert not [key for key in values if key.startswith(("CEO_CODEX_API_", "CEO_CLAUDE_API_"))]
    # An added route that was already there is untouched.
    assert values["CEO_RUNTIME_QWEN_GPU4_API_KEY"] == "gateway-key"
    assert sorted(report["removed"]) == sorted(
        [
            "CEO_CODEX_API_BASE_URL",
            "CEO_CODEX_API_MODEL",
            "CEO_CODEX_API_KEY",
            "CEO_CLAUDE_API_KEY",
        ]
    )
    # Reports carry key names only, never values.
    assert "codex-secret" not in repr(report)
    config = load_runtime_config(values)
    routes = {route.name: route for route in config.routes}
    assert routes["codex_api"].base_url == "https://gateway.example/v1"
    assert routes["codex_api"].is_cli_api_route
    assert config.secret_for("claude_api").get_secret_value() == "claude-secret"


def test_the_env_file_is_backed_up_before_it_is_rewritten(env_path: Path):
    report = migrate_retired_api_routes(env_path)

    backup = env_path.with_name(env_path.name + BACKUP_SUFFIX)
    assert report["backup"] == [str(backup)]
    assert backup.read_text(encoding="utf-8") == LEGACY_ENV


def test_running_it_again_changes_nothing(env_path: Path):
    migrate_retired_api_routes(env_path)
    migrated = env_path.read_text(encoding="utf-8")
    backup = env_path.with_name(env_path.name + BACKUP_SUFFIX)
    backup_text = backup.read_text(encoding="utf-8")

    assert migrate_retired_api_routes(env_path) == {}
    assert env_path.read_text(encoding="utf-8") == migrated
    # The backup of the pre-migration file is not overwritten by a no-op run.
    assert backup.read_text(encoding="utf-8") == backup_text


def test_defaults_fill_what_the_old_built_in_route_left_implicit(env_path: Path):
    env_path.write_text(
        "CEO_CODEX_MODEL=gpt-5.6-sol\n"
        "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api\n"
        "CEO_CODEX_API_KEY=codex-secret\n",
        encoding="utf-8",
    )

    migrate_retired_api_routes(env_path)

    values = read_env_file(env_path)
    assert values["CEO_RUNTIME_CODEX_API_BASE_URL"] == "https://api.openai.com/v1"
    assert values["CEO_RUNTIME_CODEX_API_MODEL"] == "gpt-5.6-sol"
    load_runtime_config(values)


def test_an_unlisted_retired_route_is_dropped_and_leaves_the_hidden_list(
    env_path: Path,
):
    """An added route exists only while it is listed; the backup keeps its keys."""

    env_path.write_text(
        "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,claude_oauth\n"
        "CEO_AGENT_RUNTIME_HIDDEN_ROUTES=codex_api,friday_runtime\n"
        "CEO_CLAUDE_API_MODEL=claude-opus-5\n",
        encoding="utf-8",
    )

    report = migrate_retired_api_routes(env_path)

    values = read_env_file(env_path)
    assert values["CEO_AGENT_RUNTIME_ROUTES"] == "codex_oauth,claude_oauth"
    assert values["CEO_AGENT_RUNTIME_HIDDEN_ROUTES"] == "friday_runtime"
    assert "CEO_CLAUDE_API_MODEL" not in values
    assert not [key for key in values if key.startswith("CEO_RUNTIME_")]
    assert report["removed"] == ["CEO_CLAUDE_API_MODEL"]


def test_a_file_without_retired_settings_is_left_alone(tmp_path: Path):
    path = tmp_path / ".env"
    text = "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,claude_oauth\n"
    path.write_text(text, encoding="utf-8")

    assert migrate_retired_api_routes(path) == {}
    assert path.read_text(encoding="utf-8") == text
    assert not path.with_name(path.name + BACKUP_SUFFIX).exists()
