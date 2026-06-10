import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.agent_envelope import AgentEnvelope
from app.codex_decision import (
    _subprocess_failure_reason,
    extract_codex_audit_events,
    extract_codex_audit_events_from_session,
    extract_codex_session_id,
)
from app.codex_history import find_codex_session_path
from app.codex_runner import (
    CODEX_BYPASS_APPROVALS_AND_SANDBOX,
    CodexRunner,
    _codex_home,
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
        session_exists: Callable[[str], bool] | None = None,
        timeout_seconds: int = 420,
        idle_timeout_seconds: int = 180,
    ):
        self.store = store
        self.workspace = workspace
        self.spec = spec
        self.codex_bin = codex_bin
        self.executor = executor
        self.session_exists = session_exists or self._local_session_exists
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
            session_id = self._usable_session_id(conversation_id)
            transcript_start_line = self._session_line_count(session_id)
            command = self._build_command(prompt, session_id)
            try:
                raw = self._execute(command, prompt)
            except RuntimeError as exc:
                if not session_id or not _is_codex_session_refresh_error(str(exc)):
                    raise
                self.store.clear_codex_session(conversation_id)
                session_id = None
                transcript_start_line = 0
                command = self._build_command(prompt, session_id)
                raw = self._execute(command, prompt)
            parsed_session_id = extract_codex_session_id(raw) or session_id or ""
            envelope = parse_agent_envelope(raw)
            transcript_end_line = self._session_line_count(parsed_session_id)
            audit_tool_events = self._audit_tool_events(
                raw=raw,
                session_id=parsed_session_id,
                start_line=transcript_start_line,
                end_line=transcript_end_line,
            )
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
                transcript_start_line=transcript_start_line,
                transcript_end_line=transcript_end_line,
                audit_tool_events=audit_tool_events,
            )

    def _usable_session_id(self, conversation_id: str) -> str | None:
        session_id = self.store.get_codex_session_id(conversation_id)
        if not session_id:
            return None
        if self.session_exists(session_id):
            return session_id
        self.store.clear_codex_session(conversation_id)
        return None

    @staticmethod
    def _local_session_exists(session_id: str) -> bool:
        return find_codex_session_path(session_id, codex_home=_codex_home()) is not None

    @staticmethod
    def _session_line_count(session_id: str | None) -> int:
        if not session_id:
            return 0
        path = find_codex_session_path(session_id, codex_home=_codex_home())
        if path is None:
            return 0
        return len(path.read_text(encoding="utf-8").splitlines())

    @staticmethod
    def _audit_tool_events(
        *,
        raw: str,
        session_id: str,
        start_line: int,
        end_line: int,
    ) -> list[dict[str, str]]:
        session_events = []
        if session_id and end_line >= start_line:
            session_events = extract_codex_audit_events_from_session(
                session_id,
                codex_home=_codex_home(),
                start_line=start_line,
                end_line=end_line,
            )
        return session_events or extract_codex_audit_events(raw)

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
                return _parse_agent_envelope_payload(json.loads(item["text"]))
            message = payload.get("message")
            if isinstance(message, str) and message.strip().startswith("{"):
                return _parse_agent_envelope_payload(json.loads(message))
    raise ValueError("no valid AgentEnvelope found")


def _parse_agent_envelope_payload(payload: object) -> AgentEnvelope:
    if not isinstance(payload, dict):
        raise ValueError("AgentEnvelope payload must be an object")
    if "kind" in payload and "user_response" in payload:
        return AgentEnvelope.model_validate(_normalize_agent_envelope_payload(payload))
    if payload.get("kind") == "okr_review" and isinstance(payload.get("result"), dict):
        request_id = payload.get("request_id")
        if not isinstance(request_id, int):
            raise ValueError("legacy okr_review payload requires integer request_id")
        return AgentEnvelope.model_validate(
            {
                "kind": "okr_review",
                "user_response": {
                    "mode": "send_reply",
                    "text": "OKR review completed.",
                    "sensitivity_kind": "internal_personnel",
                },
                "system_actions": [
                    {"type": "persist_okr_review", "request_id": request_id}
                ],
                "domain_payload": payload["result"],
                "audit": {
                    "summary": str(
                        payload.get("audit_summary")
                        or payload["result"].get("summary")
                        or "OKR review completed."
                    ),
                    "documents": [],
                    "confidence": 0.7,
                },
            }
        )
    return AgentEnvelope.model_validate(payload)


def _normalize_agent_envelope_payload(payload: dict) -> dict:
    if payload.get("kind") != "okr_review":
        return payload
    audit = payload.get("audit")
    if not isinstance(audit, dict):
        return payload
    if all(key in audit for key in ("summary", "documents", "confidence")):
        return payload
    domain_payload = payload.get("domain_payload")
    domain_summary = (
        domain_payload.get("summary")
        if isinstance(domain_payload, dict)
        and isinstance(domain_payload.get("summary"), str)
        else ""
    )
    summary = audit.get("summary") or audit.get("method") or domain_summary
    if not isinstance(summary, str) or not summary.strip():
        summary = "OKR review completed."
    return {
        **payload,
        "audit": {
            "summary": summary.strip(),
            "documents": [],
            "confidence": 0.7,
        },
    }


def _is_codex_session_refresh_error(message: str) -> bool:
    normalized = message.casefold()
    return (
        "failed to refresh token" in normalized
        or "your session has ended" in normalized
    )
