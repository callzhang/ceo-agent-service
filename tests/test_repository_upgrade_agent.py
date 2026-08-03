from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.codex_runner import CODEX_BYPASS_APPROVALS_AND_SANDBOX
from app.process_runner import ProcessRunResult
from app.repository_upgrade import GitRepository
from app.repository_upgrade_agent import (
    REDACTED_CREDENTIAL_LINE,
    REDACTED_RUNTIME_PATH_LINE,
    REDACTED_URL_CREDENTIAL_LINE,
    SUGGESTION_FAILURE_REASON,
    SUGGESTION_PROMPT_MAX_BYTES,
    PreservationSuggestion,
    RepositoryChangeSummary,
    RepositoryUpgradeSuggestionAgent,
    SuggestionError,
    build_preservation_suggestion_prompt,
    extract_final_agent_message,
    validate_suggested_branch,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _jsonl_suggestion(**overrides: object) -> str:
    suggestion = {
        "branch_name": "codex/preserve-local-changes",
        "commit_message": "chore: preserve local changes",
        **overrides,
    }
    return "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "reasoning", "text": "done"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(suggestion),
                    },
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12}}),
        ]
    )


class RecordingExecutor:
    def __init__(self, result: ProcessRunResult):
        self.result = result
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> ProcessRunResult:
        self.calls.append((command, kwargs))
        return self.result


def _successful_executor(**overrides: object) -> RecordingExecutor:
    return RecordingExecutor(
        ProcessRunResult(
            returncode=0,
            stdout=_jsonl_suggestion(**overrides),
            stderr="",
        )
    )


def test_suggestion_models_and_schema_are_strict():
    schema_path = (
        Path(__file__).parents[1]
        / "app"
        / "schemas"
        / "repository_upgrade_suggestion.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"branch_name", "commit_message"}
    assert schema["properties"]["branch_name"]["minLength"] == 1
    assert schema["properties"]["branch_name"]["maxLength"] == 120
    assert schema["properties"]["commit_message"]["maxLength"] == 200
    with pytest.raises(ValueError):
        PreservationSuggestion(
            branch_name="valid",
            commit_message="message",
            unexpected="nope",
        )


def test_suggestion_prompt_uses_allowed_summary_and_redacts_sensitive_lines():
    change = RepositoryChangeSummary(
        paths=("app/history.py", "path-bytes:.env.local"),
        diff_stat="2 files changed, 8 insertions(+)",
        diff="\n".join(
            [
                "+Authorization: Bearer bearer-secret",
                "+API_TOKEN=assignment-secret",
                "+remote=https://user:password@example.test/repo",
                "+remote=https://example.test/repo?access_token=query-secret",
                "+socket=/private/var/run/agent.sock",
                "+def render_history(): pass",
            ]
        ),
    )

    prompt = build_preservation_suggestion_prompt(change)

    assert "app/history.py" in prompt
    assert "path-bytes:.env.local" in prompt
    assert "2 files changed, 8 insertions(+)" in prompt
    assert REDACTED_CREDENTIAL_LINE in prompt
    assert REDACTED_URL_CREDENTIAL_LINE in prompt
    assert REDACTED_RUNTIME_PATH_LINE in prompt
    assert "+def render_history(): pass" in prompt
    for secret in (
        "bearer-secret",
        "assignment-secret",
        "user:password",
        "query-secret",
        "/private/var/run",
    ):
        assert secret not in prompt


def test_suggestion_prompt_is_utf8_byte_bounded_after_redaction():
    change = RepositoryChangeSummary(
        paths=("src/changed.py",),
        diff_stat="1 file changed",
        diff=("+API_TOKEN=secret-value\n" + "+界" * SUGGESTION_PROMPT_MAX_BYTES),
    )

    prompt = build_preservation_suggestion_prompt(change)

    assert "secret-value" not in prompt
    assert REDACTED_CREDENTIAL_LINE in prompt
    assert len(prompt.encode("utf-8")) <= SUGGESTION_PROMPT_MAX_BYTES
    prompt.encode("utf-8").decode("utf-8")


@pytest.mark.parametrize(
    "path",
    ["/Users/private/file.py", "../outside.py", "path-bytes:../outside.py"],
)
def test_change_summary_rejects_non_repository_relative_paths(path: str):
    with pytest.raises(ValueError):
        RepositoryChangeSummary(paths=(path,), diff_stat="1 file", diff="+x")


def test_agent_command_is_read_only_and_preserves_local_cli_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CODEX_API_KEY", "local-cli-secret")
    executor = _successful_executor()
    agent = RepositoryUpgradeSuggestionAgent(tmp_path, executor=executor)

    result = agent.suggest(
        RepositoryChangeSummary(paths=("app/a.py",), diff_stat="1 file", diff="+x")
    )

    command, kwargs = executor.calls[0]
    assert result.branch_name == "codex/preserve-local-changes"
    assert 'approval_policy="never"' in command
    assert CODEX_BYPASS_APPROVALS_AND_SANDBOX not in command
    assert "--ignore-user-config" not in command
    assert "--output-schema" in command
    assert kwargs["env"]["CODEX_API_KEY"] == "local-cli-secret"
    assert kwargs["prompt"]


def test_extract_final_agent_message_supports_current_result_payload():
    raw = "\n".join(
        [
            json.dumps({"type": "item.started", "item": {"type": "reasoning"}}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "result": json.dumps(
                        {"branch_name": "topic/exact", "commit_message": "Exact text"}
                    ),
                }
            ),
        ]
    )

    assert json.loads(extract_final_agent_message(raw)) == {
        "branch_name": "topic/exact",
        "commit_message": "Exact text",
    }


@pytest.mark.parametrize(
    "result",
    [
        ProcessRunResult(1, "", "Bearer raw-secret"),
        ProcessRunResult(0, "", "", timed_out=True, timeout_kind="idle"),
        ProcessRunResult(0, "not-json", ""),
        ProcessRunResult(0, _jsonl_suggestion(extra="not allowed"), ""),
        ProcessRunResult(
            0,
            _jsonl_suggestion(branch_name="invalid branch"),
            "Bearer raw-secret",
        ),
    ],
)
def test_suggestion_failures_are_typed_fixed_and_do_not_expose_raw_output(
    tmp_path: Path, result: ProcessRunResult
):
    agent = RepositoryUpgradeSuggestionAgent(
        tmp_path,
        executor=RecordingExecutor(result),
    )

    with pytest.raises(SuggestionError) as caught:
        agent.suggest(
            RepositoryChangeSummary(paths=("app/a.py",), diff_stat="1 file", diff="+x")
        )

    assert caught.value.reason == SUGGESTION_FAILURE_REASON
    assert str(caught.value) == SUGGESTION_FAILURE_REASON
    assert "raw-secret" not in repr(caught.value)


def test_executor_exception_is_a_fixed_suggestion_error(tmp_path: Path):
    def failed_executor(*args: object, **kwargs: object) -> ProcessRunResult:
        raise RuntimeError("Authorization: Bearer executor-secret")

    agent = RepositoryUpgradeSuggestionAgent(tmp_path, executor=failed_executor)

    with pytest.raises(SuggestionError) as caught:
        agent.suggest(
            RepositoryChangeSummary(paths=("app/a.py",), diff_stat="1 file", diff="+x")
        )

    assert str(caught.value) == SUGGESTION_FAILURE_REASON
    assert "executor-secret" not in repr(caught.value)


def test_extract_final_agent_message_rejects_malformed_or_extra_output():
    with pytest.raises(SuggestionError):
        extract_final_agent_message(_jsonl_suggestion() + "\nnot-json")
    malformed_message = _jsonl_suggestion() + " trailing-output"
    with pytest.raises(SuggestionError):
        extract_final_agent_message(malformed_message)


def test_branch_validation_rejects_target_and_existing_refs(tmp_path: Path):
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-m", "initial")
    _git(tmp_path, "branch", "existing")
    repository = GitRepository(tmp_path)

    assert validate_suggested_branch(repository, "topic/new", target_branch="main")
    assert not validate_suggested_branch(repository, "invalid branch")
    assert not validate_suggested_branch(repository, "main", target_branch="main")
    assert not validate_suggested_branch(
        repository,
        "existing",
        target_branch="main",
        reject_existing_refs=True,
    )


def test_suggestion_does_not_mutate_repository(tmp_path: Path):
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-m", "initial")
    before = (_git(tmp_path, "rev-parse", "HEAD"), _git(tmp_path, "status", "--porcelain"))

    RepositoryUpgradeSuggestionAgent(
        tmp_path,
        executor=_successful_executor(),
    ).suggest(
        RepositoryChangeSummary(paths=("tracked.txt",), diff_stat="1 file", diff="+x")
    )

    after = (_git(tmp_path, "rev-parse", "HEAD"), _git(tmp_path, "status", "--porcelain"))
    assert after == before
