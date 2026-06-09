import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.agent_envelope import AgentEnvelope
from app.codex_decision import (
    _subprocess_failure_reason,
    extract_codex_audit_events,
    extract_codex_session_id,
)
from app.codex_runner import (
    CODEX_BYPASS_APPROVALS_AND_SANDBOX,
    CodexRunner,
    _config_string,
    memory_connector_config_options,
)
from app.external_retry import run_external
from app.process_runner import run_process_with_idle_timeout


class SkillLoadError(RuntimeError):
    pass


def load_skill_text(paths: list[Path]) -> str:
    sections: list[str] = []
    for path in paths:
        if not path.exists():
            raise SkillLoadError(f"missing skill file: {path}")
        if not path.is_file():
            raise SkillLoadError(f"skill path is not a file: {path}")
        sections.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(section for section in sections if section)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    schema_path: Path
    primary_skill_paths: list[Path] = field(default_factory=list)
    reply_visible_skill_paths: list[Path] = field(default_factory=list)
    developer_preamble: str = ""

    def developer_instructions(self) -> str:
        if not self.schema_path.exists():
            raise SkillLoadError(f"missing schema file: {self.schema_path}")
        skill_text = load_skill_text(
            [*self.primary_skill_paths, *self.reply_visible_skill_paths]
        )
        parts = [
            self.developer_preamble.strip(),
            f"# Agent spec\n\nname: {self.name}",
            skill_text,
        ]
        return "\n\n".join(part for part in parts if part)


@dataclass(frozen=True)
class StructuredAgentRun:
    envelope: AgentEnvelope
    codex_session_id: str
    transcript_start_line: int
    transcript_end_line: int
    audit_tool_events: list[dict[str, str]]


class StructuredCodexRunner:
    def __init__(
        self,
        *,
        store,
        workspace: Path,
        spec: AgentSpec,
        codex_bin: str = "codex",
        executor: Callable[[list[str], str, dict[str, str]], str] | None = None,
        timeout_seconds: int = 420,
        idle_timeout_seconds: int = 180,
    ):
        self.store = store
        self.workspace = workspace
        self.spec = spec
        self.codex_bin = codex_bin
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self._run_process_with_idle_timeout = run_process_with_idle_timeout

    def run(
        self,
        conversation_id: str,
        conversation_title: str,
        single_chat: bool,
        prompt: str,
        *,
        owner: str,
    ) -> StructuredAgentRun:
        with self.store.codex_session_lock(conversation_id, owner):
            session_id = self.store.get_codex_session_id(conversation_id)
            command = self._build_command(prompt, session_id)
            raw = self._execute(command, prompt)
            parsed_session_id = extract_codex_session_id(raw) or session_id or ""
            envelope = parse_agent_envelope(raw)
            if parsed_session_id:
                self.store.upsert_conversation(
                    conversation_id,
                    conversation_title,
                    single_chat,
                    parsed_session_id,
                )
            return StructuredAgentRun(
                envelope=envelope,
                codex_session_id=parsed_session_id,
                transcript_start_line=0,
                transcript_end_line=0,
                audit_tool_events=extract_codex_audit_events(raw),
            )

    def _execute(self, command: list[str], prompt: str) -> str:
        env = self.runner.build_env()
        if self.executor is not None:
            return run_external(
                "codex exec",
                lambda: self.executor(command, prompt, env),
                max_attempts=3,
            )
        completed = run_external(
            "codex exec",
            lambda: self._run_process_with_idle_timeout(
                command,
                prompt=prompt,
                env=env,
                total_timeout_seconds=self.timeout_seconds,
                idle_timeout_seconds=self.idle_timeout_seconds,
            ),
            max_attempts=3,
        )
        if completed.timed_out:
            raise RuntimeError(completed.timeout_reason or "codex exec timed out")
        if completed.returncode != 0:
            raise RuntimeError(
                _subprocess_failure_reason(completed.stderr, completed.stdout)
            )
        return completed.stdout

    def _build_command(self, prompt: str, session_id: str | None) -> list[str]:
        common = [
            "--json",
            "-m",
            "gpt-5.5",
            "--ignore-user-config",
            "--ignore-rules",
            "--disable",
            "hooks",
            "--disable",
            "plugins",
            *memory_connector_config_options(),
            "-c",
            'approval_policy="untrusted"',
            "-c",
            'approvals_reviewer="auto_review"',
            "-c",
            _config_string("developer_instructions", self.spec.developer_instructions()),
            "-c",
            'model_reasoning_summary="concise"',
            "-c",
            "include_permissions_instructions=false",
            "-c",
            "include_apps_instructions=false",
            "-c",
            "include_environment_context=false",
        ]
        if session_id:
            return [
                self.codex_bin,
                "exec",
                "resume",
                *common,
                CODEX_BYPASS_APPROVALS_AND_SANDBOX,
                session_id,
                "-",
            ]
        return [
            self.codex_bin,
            "exec",
            *common,
            CODEX_BYPASS_APPROVALS_AND_SANDBOX,
            "--cd",
            str(self.workspace),
            "-",
        ]


def parse_agent_envelope(raw: str) -> AgentEnvelope:
    payloads = [json.loads(line) for line in raw.splitlines() if line.strip()]
    for payload in reversed(payloads):
        if isinstance(payload, dict):
            if "kind" in payload and "user_response" in payload:
                return AgentEnvelope.model_validate(payload)
            item = payload.get("item")
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return AgentEnvelope.model_validate(json.loads(item["text"]))
            message = payload.get("message")
            if isinstance(message, str) and message.strip().startswith("{"):
                return AgentEnvelope.model_validate(json.loads(message))
    raise ValueError("no valid AgentEnvelope found")
