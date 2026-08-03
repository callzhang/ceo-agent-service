from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from urllib.parse import unquote_plus, unquote_to_bytes, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.codex_runner import CodexRunner
from app.leak_check import contains_credential, contains_local_runtime_leak
from app.process_runner import ProcessRunResult, run_process_with_idle_timeout
from app.repository_upgrade import GitCommandError, GitRepository


SUGGESTION_SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "repository_upgrade_suggestion.schema.json"
)
SUGGESTION_PROMPT_MAX_BYTES = 16 * 1024
SUGGESTION_TOTAL_TIMEOUT_SECONDS = 120.0
SUGGESTION_IDLE_TIMEOUT_SECONDS = 60.0
SUGGESTION_FAILURE_REASON = "suggestion_agent_failed"
REDACTED_CREDENTIAL_LINE = "[redacted credential line]"
REDACTED_URL_CREDENTIAL_LINE = "[redacted credential URL line]"
REDACTED_RUNTIME_PATH_LINE = "[redacted local runtime path line]"
SUGGESTION_DEVELOPER_INSTRUCTIONS = """
You suggest editable Git branch and commit names from the supplied change summary.
This is a read-only naming task. Do not call tools, run commands, inspect files,
use MCP, write data, mutate Git, reuse a session, or persist a conversation.
Return only JSON matching the supplied schema. Do not invent change details.
""".strip()


class RepositoryChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    paths: tuple[str, ...]
    diff_stat: str
    diff: str

    @field_validator("paths")
    @classmethod
    def paths_are_repository_relative(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        for value in paths:
            raw = (
                unquote_to_bytes(value.removeprefix("path-bytes:"))
                if value.startswith("path-bytes:")
                else value.encode("utf-8")
            )
            if not raw or raw.startswith(b"/") or b".." in raw.split(b"/"):
                raise ValueError("change paths must be repository-relative")
        return paths


class PreservationSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    branch_name: str = Field(min_length=1, max_length=120)
    commit_message: str = Field(min_length=1, max_length=200)


class SuggestionError(RuntimeError):
    def __init__(self) -> None:
        self.reason = SUGGESTION_FAILURE_REASON
        super().__init__(self.reason)


SuggestionExecutor = Callable[..., ProcessRunResult]


def build_preservation_suggestion_prompt(change: RepositoryChangeSummary) -> str:
    redacted_diff = "\n".join(_redact_diff_line(line) for line in change.diff.splitlines())
    prompt = "\n".join(
        [
            "Suggest editable preservation metadata for these repository changes.",
            "Return exactly branch_name and commit_message as JSON.",
            "Paths:",
            *change.paths,
            "Diff stat:",
            change.diff_stat,
            "Redacted diff:",
            redacted_diff,
        ]
    )
    return _truncate_utf8(prompt, SUGGESTION_PROMPT_MAX_BYTES)


def _redact_diff_line(line: str) -> str:
    if contains_credential(line):
        return REDACTED_CREDENTIAL_LINE
    if _contains_credential_url(line):
        return REDACTED_URL_CREDENTIAL_LINE
    if contains_local_runtime_leak(line):
        return REDACTED_RUNTIME_PATH_LINE
    return line


def _contains_credential_url(line: str) -> bool:
    lowered = line.lower()
    cursor = 0
    while cursor < len(line):
        starts = [
            index
            for scheme in ("http://", "https://")
            if (index := lowered.find(scheme, cursor)) >= 0
        ]
        if not starts:
            return False
        start = min(starts)
        end = next(
            (index for index in range(start, len(line)) if line[index].isspace()),
            len(line),
        )
        candidate = line[start:end].strip("<>()[]{}\"',.;")
        parsed = urlsplit(candidate)
        if parsed.username is not None or parsed.password is not None:
            return True
        query = unquote_plus(parsed.query).replace("&", " ")
        if query and contains_credential(query):
            return True
        cursor = start + 1
    return False


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def extract_final_agent_message(raw: str) -> str:
    candidate: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            raise SuggestionError() from None
        if not isinstance(payload, dict):
            raise SuggestionError()
        found = _result_candidate(payload)
        if found is not None:
            candidate = found
    if candidate is None:
        raise SuggestionError()
    return candidate


def _result_candidate(payload: dict[str, object]) -> str | None:
    item = payload.get("item")
    if isinstance(item, dict) and item.get("type") == "agent_message":
        for key in ("text", "message"):
            value = item.get(key)
            if isinstance(value, str):
                return value

    last_agent_message = payload.get("last_agent_message")
    if isinstance(last_agent_message, str):
        return last_agent_message

    payload_type = payload.get("type")
    if payload_type in {"agent_message", "task_complete"}:
        message = payload.get("message")
        if isinstance(message, str):
            return message

    if payload_type in {"result", "turn.completed", "response.completed"}:
        result = payload.get("result")
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
    return None


def validate_suggested_branch(
    repository: GitRepository,
    branch_name: str,
    *,
    target_branch: str | None = None,
    remote: str = "origin",
    reject_existing_refs: bool = False,
) -> bool:
    if target_branch is not None and branch_name == target_branch:
        return False
    try:
        valid = repository._run(
            ["check-ref-format", "--branch", branch_name],
            category="suggested_branch",
            accepted_returncodes=(0, 1),
        )
        if valid.returncode != 0:
            return False
        if not reject_existing_refs:
            return True
        for ref in (
            f"refs/heads/{branch_name}",
            f"refs/remotes/{remote}/{branch_name}",
        ):
            existing = repository._run(
                ["show-ref", "--verify", "--quiet", ref],
                category="suggested_branch_ref",
                accepted_returncodes=(0, 1),
            )
            if existing.returncode == 0:
                return False
        return True
    except GitCommandError:
        return False


class RepositoryUpgradeSuggestionAgent:
    def __init__(
        self,
        workspace: Path,
        *,
        executor: SuggestionExecutor = run_process_with_idle_timeout,
        runner: CodexRunner | None = None,
        repository: GitRepository | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.executor = executor
        self.runner = runner or CodexRunner(workspace=self.workspace)
        self.repository = repository or GitRepository(self.workspace)

    def suggest(self, change: RepositoryChangeSummary) -> PreservationSuggestion:
        prompt = build_preservation_suggestion_prompt(change)
        try:
            command = self.runner.build_command(
                prompt=prompt,
                session_id=None,
                output_schema_path=SUGGESTION_SCHEMA_PATH,
                approval_policy="never",
                use_approval_bypass=False,
                preserve_native_model_config=True,
                developer_instructions=SUGGESTION_DEVELOPER_INSTRUCTIONS,
            )
            result = self.executor(
                command,
                prompt=prompt,
                env=self.runner.build_env(preserve_local_cli_auth=True),
                total_timeout_seconds=SUGGESTION_TOTAL_TIMEOUT_SECONDS,
                idle_timeout_seconds=SUGGESTION_IDLE_TIMEOUT_SECONDS,
            )
        except Exception:
            raise SuggestionError() from None
        if result.timed_out or result.returncode != 0:
            raise SuggestionError()
        try:
            suggestion = PreservationSuggestion.model_validate_json(
                extract_final_agent_message(result.stdout)
            )
        except (SuggestionError, ValidationError, ValueError):
            raise SuggestionError() from None
        if not validate_suggested_branch(self.repository, suggestion.branch_name):
            raise SuggestionError()
        return suggestion
