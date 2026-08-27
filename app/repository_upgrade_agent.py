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
REDACTED_CONTROL_LINE = "[redacted control character line]"
SUGGESTION_DEVELOPER_INSTRUCTIONS = """
Suggestion only: propose editable Git branch and commit names from the supplied
change summary. Do not call tools, run commands, inspect files, use MCP, write
data, mutate Git, reuse a session, or persist a conversation.
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
            _reject_control_characters(value, field_name="change path")
            raw = (
                unquote_to_bytes(value.removeprefix("path-bytes:"))
                if value.startswith("path-bytes:")
                else value.encode("utf-8")
            )
            if (
                not raw
                or raw.startswith(b"/")
                or b".." in raw.split(b"/")
                or _bytes_contain_control(raw)
            ):
                raise ValueError("change paths must be repository-relative")
        return paths

    @field_validator("diff_stat")
    @classmethod
    def diff_stat_has_no_controls(cls, value: str) -> str:
        _reject_control_characters(value, field_name="diff stat")
        return value


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
    redacted_paths = [_redact_prompt_line(path) for path in change.paths]
    redacted_stat = _redact_prompt_line(change.diff_stat)
    redacted_diff = "\n".join(
        _redact_prompt_line(line) for line in change.diff.splitlines()
    )
    prompt = "\n".join(
        [
            "Suggest editable preservation metadata for these repository changes.",
            "Return exactly branch_name and commit_message as JSON.",
            "Paths:",
            *redacted_paths,
            "Diff stat:",
            redacted_stat,
            "Redacted diff:",
            redacted_diff,
        ]
    )
    return _truncate_utf8(prompt, SUGGESTION_PROMPT_MAX_BYTES)


def _redact_prompt_line(line: str) -> str:
    if _contains_control_characters(line):
        return REDACTED_CONTROL_LINE
    if _contains_credential_url(line):
        return REDACTED_URL_CREDENTIAL_LINE
    if contains_credential(line):
        return REDACTED_CREDENTIAL_LINE
    if contains_local_runtime_leak(line):
        return REDACTED_RUNTIME_PATH_LINE
    return line


def _reject_control_characters(value: str, *, field_name: str) -> None:
    if _contains_control_characters(value):
        raise ValueError(f"{field_name} must not contain control characters")


def _contains_control_characters(value: str) -> bool:
    return len(value.splitlines()) > 1 or any(
        ord(character) < 32 or 127 <= ord(character) <= 159
        for character in value
    )


def _bytes_contain_control(value: bytes) -> bool:
    return any(byte < 32 or 127 <= byte <= 159 for byte in value)


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
    event_seen = False
    thread_started = False
    turn_started = False
    turn_completed = False
    item_seen = False
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            raise SuggestionError() from None
        if not isinstance(payload, dict):
            raise SuggestionError()
        if turn_completed:
            raise SuggestionError()

        event_type = payload.get("type")
        if event_type == "thread.started":
            if (
                event_seen
                or thread_started
                or candidate is not None
                or _contains_suggestion_payload(payload)
            ):
                raise SuggestionError()
            thread_started = True
        elif event_type == "turn.started":
            if (
                turn_started
                or item_seen
                or candidate is not None
                or _contains_suggestion_payload(payload)
            ):
                raise SuggestionError()
            turn_started = True
        elif event_type in {"item.started", "item.completed"}:
            item = payload.get("item")
            if not isinstance(item, dict):
                raise SuggestionError()
            item_type = item.get("type")
            if item_type == "reasoning":
                if candidate is not None or _contains_suggestion_payload(item):
                    raise SuggestionError()
            elif item_type == "agent_message" and event_type == "item.completed":
                text = item.get("text")
                if (
                    candidate is not None
                    or not isinstance(text, str)
                    or any(
                        key in item
                        for key in ("message", "result", "last_agent_message")
                    )
                ):
                    raise SuggestionError()
                candidate = text
            else:
                raise SuggestionError()
            item_seen = True
        elif event_type == "turn.completed":
            if candidate is None or _contains_suggestion_payload(payload):
                raise SuggestionError()
            turn_completed = True
        else:
            raise SuggestionError()
        event_seen = True
    if candidate is None:
        raise SuggestionError()
    return candidate


def _contains_suggestion_payload(value: object) -> bool:
    if isinstance(value, dict):
        if "branch_name" in value or "commit_message" in value:
            return True
        return any(_contains_suggestion_payload(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_suggestion_payload(item) for item in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return False
        return _contains_suggestion_payload(parsed)
    return False


def _harden_read_only_command(command: list[str]) -> list[str]:
    hardened = list(command)
    insertion_index = len(hardened) - 1 if hardened[-1:] == ["-"] else len(hardened)
    hardened[insertion_index:insertion_index] = [
        "--ephemeral",
        "--sandbox",
        "read-only",
    ]
    return hardened


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
            command = _harden_read_only_command(
                self.runner.build_command(
                    prompt=prompt,
                    session_id=None,
                    output_schema_path=SUGGESTION_SCHEMA_PATH,
                    approval_policy="never",
                    use_approval_bypass=False,
                    preserve_native_model_config=True,
                    developer_instructions=SUGGESTION_DEVELOPER_INSTRUCTIONS,
                )
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
