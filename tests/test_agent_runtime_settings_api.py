"""The console's JSON endpoint owns every Agent Runtime settings write."""

from pathlib import Path

import pytest

import app.friday_runtime_adapter as friday_module
from tests.test_console_web_api import _client


ENDPOINT = "/api/console/settings/agent-runtime"
BASE_FIELDS = {
    "CEO_CODEX_MODEL": "gpt-5.5",
    "CEO_CODEX_MODEL_REASONING_EFFORT": "medium",
    "CEO_CLAUDE_MODEL": "sonnet",
    "CEO_CLAUDE_MODEL_REASONING_EFFORT": "medium",
}


@pytest.fixture
def env_path(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / ".env"
    path.write_text("CEO_AGENT_RUNTIME_ROUTES=codex_oauth\n", encoding="utf-8")
    monkeypatch.setenv("CEO_ENV_FILE", str(path))
    return path


def _save(tmp_path: Path, **fields: str):
    with _client(tmp_path) as client:
        return client.post(ENDPOINT, json={"fields": {**BASE_FIELDS, **fields}})


def test_friday_is_verified_against_the_desktop_cli_on_save(
    tmp_path: Path, env_path: Path, monkeypatch
):
    """A stored desktop configuration is one that answered, not one that parsed."""

    monkeypatch.setattr(friday_module, "bundled_friday_cli", lambda: "/tmp/friday-cli")
    monkeypatch.setattr(
        friday_module,
        "check_desktop_friday",
        lambda **_: (_ for _ in ()).throw(
            friday_module.FridayRuntimeError(
                "friday_runtime_unreachable", "Friday CLI is not serving", retryable=True
            )
        ),
    )

    response = _save(
        tmp_path,
        CEO_AGENT_RUNTIME_ROUTES="codex_oauth,friday_runtime",
        CEO_FRIDAY_RUNTIME_PROJECT_ID="ceo",
    )

    assert response.status_code == 400
    assert "Friday CLI" in response.json()["message"]
    assert "friday_runtime" not in env_path.read_text(encoding="utf-8")


def test_friday_is_refused_without_the_desktop_cli(
    tmp_path: Path, env_path: Path, monkeypatch
):
    monkeypatch.setattr(friday_module, "bundled_friday_cli", lambda: "")

    response = _save(
        tmp_path,
        CEO_AGENT_RUNTIME_ROUTES="codex_oauth,friday_runtime",
        CEO_FRIDAY_RUNTIME_PROJECT_ID="ceo",
    )

    assert response.status_code == 400
    assert "desktop app" in response.json()["message"]
    assert "friday_runtime" not in env_path.read_text(encoding="utf-8")


def test_friday_save_stores_the_address_the_cli_serves_and_a_provisioned_project(
    tmp_path: Path, env_path: Path, monkeypatch
):
    """Friday rejects an invented project id, so the save asks Friday for one."""

    created: list[dict] = []

    class Transport:
        def request(self, method, path, *, headers, body=None, timeout_seconds):
            created.append({"method": method, "path": path, "body": dict(body or {})})
            return friday_module.FridayHttpResponse(
                status_code=200,
                payload={"data": {"project_id": "project_created_1"}},
            )

    real = friday_module.ensure_friday_project
    monkeypatch.setattr(friday_module, "bundled_friday_cli", lambda: "/tmp/friday-cli")
    monkeypatch.setattr(
        friday_module, "check_desktop_friday", lambda **_: "http://127.0.0.1:62764"
    )
    monkeypatch.setattr(
        friday_module,
        "ensure_friday_project",
        lambda config, **kwargs: real(config, transport=Transport()),
    )

    response = _save(
        tmp_path,
        CEO_AGENT_RUNTIME_ROUTES="codex_oauth,friday_runtime",
        CEO_FRIDAY_RUNTIME_BASE_URL="http://127.0.0.1:8080/",
        CEO_FRIDAY_RUNTIME_PROJECT_ID="",
        CEO_FRIDAY_RUNTIME_AUTH_DISABLED="1",
    )

    assert response.status_code == 200, response.json()
    env_text = env_path.read_text(encoding="utf-8")
    assert "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,friday_runtime" in env_text
    assert "CEO_FRIDAY_RUNTIME_BASE_URL=http://127.0.0.1:62764" in env_text
    assert "CEO_FRIDAY_RUNTIME_PROJECT_ID=project_created_1" in env_text
    assert created and created[0]["path"] == "/v1/projects"
    assert created[0]["body"]["name"] == "CEO Agent"


def test_claude_oauth_is_enabled_with_its_model_and_no_api_key(
    tmp_path: Path, env_path: Path
):
    response = _save(
        tmp_path,
        CEO_AGENT_RUNTIME_ROUTES="codex_oauth,claude_oauth",
        CEO_CLAUDE_MODEL="opus",
        CEO_CLAUDE_MODEL_REASONING_EFFORT="high",
    )

    assert response.status_code == 200, response.json()
    env_text = env_path.read_text(encoding="utf-8")
    assert "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,claude_oauth" in env_text
    assert "CEO_CLAUDE_MODEL=opus" in env_text
    assert "CEO_CLAUDE_MODEL_REASONING_EFFORT=high" in env_text
    # The local login is the credential, so no Anthropic API key is written.
    assert "API_KEY" not in env_text


def test_unsupported_claude_effort_is_refused(tmp_path: Path, env_path: Path):
    response = _save(
        tmp_path,
        CEO_AGENT_RUNTIME_ROUTES="codex_oauth,claude_oauth",
        CEO_CLAUDE_MODEL_REASONING_EFFORT="ludicrous",
    )

    assert response.status_code == 400
    assert "Claude thinking strength" in response.json()["message"]
    assert "claude_oauth" not in env_path.read_text(encoding="utf-8")


def test_the_codex_oauth_model_comes_from_the_supported_list(
    tmp_path: Path, env_path: Path
):
    accepted = _save(
        tmp_path,
        CEO_AGENT_RUNTIME_ROUTES="codex_oauth",
        CEO_CODEX_MODEL="gpt-5.6-sol",
        CEO_CODEX_MODEL_REASONING_EFFORT="high",
    )
    (tmp_path / "refused").mkdir()
    refused = _save(
        tmp_path / "refused",
        CEO_AGENT_RUNTIME_ROUTES="codex_oauth",
        CEO_CODEX_MODEL="not-a-codex-model",
    )

    assert accepted.status_code == 200, accepted.json()
    assert "CEO_CODEX_MODEL=gpt-5.6-sol" in env_path.read_text(encoding="utf-8")
    assert refused.status_code == 400
    assert refused.json()["message"] == "Model must be selected from this page."


def test_the_primary_route_stays_even_when_the_submission_omits_it(
    tmp_path: Path, env_path: Path
):
    response = _save(tmp_path, CEO_AGENT_RUNTIME_ROUTES="claude_oauth")

    assert response.status_code == 200, response.json()
    assert "CEO_AGENT_RUNTIME_ROUTES=claude_oauth,codex_oauth" in env_path.read_text(
        encoding="utf-8"
    )


def test_claude_api_is_saved_as_an_added_route_with_its_own_key(
    tmp_path: Path, env_path: Path
):
    """The former built-in name carries the same settings as any added route."""

    refused = _save(
        tmp_path,
        CEO_AGENT_RUNTIME_ROUTES="codex_oauth,claude_api",
        CEO_RUNTIME_CLAUDE_API_KIND="claude_api",
        CEO_RUNTIME_CLAUDE_API_MODEL="claude-opus-5",
    )
    (tmp_path / "saved").mkdir()
    saved = _save(
        tmp_path / "saved",
        CEO_AGENT_RUNTIME_ROUTES="codex_oauth,claude_api",
        CEO_RUNTIME_CLAUDE_API_KIND="claude_api",
        CEO_RUNTIME_CLAUDE_API_MODEL="claude-opus-5",
        CEO_RUNTIME_CLAUDE_API_API_KEY="claude-secret",
    )

    assert refused.status_code == 400
    assert refused.json()["message"] == "Runtime claude_api requires an API token."
    assert saved.status_code == 200, saved.json()
    env_text = env_path.read_text(encoding="utf-8")
    assert "CEO_AGENT_RUNTIME_ROUTES=codex_oauth,claude_api" in env_text
    assert "CEO_RUNTIME_CLAUDE_API_MODEL=claude-opus-5" in env_text
    assert "CEO_RUNTIME_CLAUDE_API_API_KEY=claude-secret" in env_text
    assert "CEO_CLAUDE_API_KEY" not in env_text


def test_the_legacy_form_route_is_gone(tmp_path: Path, env_path: Path):
    with _client(tmp_path) as client:
        response = client.post(
            "/config/agent-runtime",
            content="codex_model=gpt-5.5",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert response.status_code in {404, 405}
    assert env_path.read_text(encoding="utf-8") == "CEO_AGENT_RUNTIME_ROUTES=codex_oauth\n"
