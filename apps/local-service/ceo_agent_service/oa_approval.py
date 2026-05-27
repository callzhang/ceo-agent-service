import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import BaseModel, ValidationError

from ceo_agent_service.codex_decision import (
    extract_codex_audit_events,
    extract_codex_session_id,
)
from ceo_agent_service.codex_history import (
    count_codex_session_lines,
    extract_codex_audit_events_from_session,
)
from ceo_agent_service.codex_runner import (
    CODEX_BYPASS_APPROVALS_AND_SANDBOX,
    _config_string,
)


OA_APPROVAL_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "oa_approval.schema.json"
)
DEFAULT_OA_APPROVAL_SKILL_PATH = (
    Path.home() / ".agents" / "skills" / "dingtalk-oa-approval" / "SKILL.md"
)
AFLOW_HOST = "aflow.dingtalk.com"


class OaApprovalResult(BaseModel):
    process_instance_id: str
    task_id: str
    oa_url: str
    oa_action: Literal["通过", "拒绝", "退回"]
    oa_remark: str
    action_result: dict[str, Any]
    audit_summary: str
    audit_documents: list[dict[str, str]]


def extract_oa_url(text: str) -> str:
    for candidate in _urlish_candidates(text):
        direct = _aflow_url(candidate)
        if direct:
            return direct
        decoded = unquote(candidate)
        direct = _aflow_url(decoded)
        if direct:
            return direct
        nested = _nested_aflow_url(candidate)
        if nested:
            return nested
    return ""


def _urlish_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    text = text.replace("\\/", "/").replace("\\u0026", "&")
    for separator in ('"', "'", " ", "\n", "\t", "\r", "<", ">", ","):
        text = text.replace(separator, "\n")
    for raw in text.splitlines():
        candidate = raw.strip()
        if candidate:
            candidates.append(candidate)
    return candidates


def _aflow_url(value: str) -> str:
    marker = f"https://{AFLOW_HOST}"
    start = value.find(marker)
    if start < 0:
        return ""
    url = value[start:]
    for delimiter in ("&quot;", "\\u0026quot;", "}", "]"):
        position = url.find(delimiter)
        if position >= 0:
            url = url[:position]
    parsed = urlparse(url)
    if parsed.netloc != AFLOW_HOST:
        return ""
    return url


def _nested_aflow_url(value: str) -> str:
    parsed = urlparse(value)
    query = parse_qs(parsed.query)
    for values in query.values():
        for item in values:
            direct = _aflow_url(unquote(item))
            if direct:
                return direct
    return ""


class OaApprovalCodexRunner:
    def __init__(
        self,
        workspace: Path,
        codex_bin: str = "codex",
        executor: Callable[[list[str], str], str] | None = None,
        timeout_seconds: int = 120,
        codex_home: Path | None = None,
        skill_path: Path | None = None,
    ):
        self.runner = _OaApprovalCommandBuilder(
            workspace=workspace,
            codex_bin=codex_bin,
            skill_path=skill_path or DEFAULT_OA_APPROVAL_SKILL_PATH,
        )
        self.executor = executor or self._subprocess_executor
        self.timeout_seconds = timeout_seconds
        self.codex_home = codex_home
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line: int = 0
        self.last_transcript_end_line: int = 0

    def run(self, prompt: str, session_id: str | None = None) -> OaApprovalResult:
        raw_outputs: list[str] = []
        self.last_audit_tool_events = []
        self.last_session_id = session_id
        self.last_transcript_start_line = self._session_line_count(session_id)
        self.last_transcript_end_line = self.last_transcript_start_line

        raw = self.executor(self.runner.build_command(prompt, session_id), prompt)
        raw_outputs.append(raw)
        self._remember_session_id(raw)
        result = parse_oa_approval_json(raw)
        self._remember_audit_tool_events(raw_outputs)
        return result

    def _remember_session_id(self, raw: str) -> None:
        session_id = extract_codex_session_id(raw)
        if session_id:
            self.last_session_id = session_id

    def _remember_audit_tool_events(self, raw_outputs: list[str]) -> None:
        session_id = self.last_session_id
        self.last_transcript_end_line = self._session_line_count(session_id)
        session_events = []
        if session_id:
            session_events = extract_codex_audit_events_from_session(
                session_id,
                codex_home=self.codex_home,
                start_line=self.last_transcript_start_line,
                end_line=self.last_transcript_end_line,
            )
        self.last_audit_tool_events = session_events or extract_codex_audit_events(
            "\n".join(raw_outputs)
        )

    def _subprocess_executor(self, command: list[str], prompt: str) -> str:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            input=prompt,
            env=self.runner.build_env(),
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0 and not completed.stdout.strip():
            raise RuntimeError(completed.stderr.strip())
        return completed.stdout.strip()

    def _session_line_count(self, session_id: str | None) -> int:
        if not session_id:
            return 0
        return count_codex_session_lines(session_id, codex_home=self.codex_home)


class _OaApprovalCommandBuilder:
    def __init__(self, workspace: Path, codex_bin: str, skill_path: Path):
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.skill_path = skill_path

    def build_env(self) -> dict[str, str]:
        return os.environ.copy()

    def build_command(self, prompt: str, session_id: str | None) -> list[str]:
        common_options = [
            "--json",
            "-m",
            "gpt-5.5",
            "--ignore-user-config",
            "--ignore-rules",
            "-c",
            'approval_policy="untrusted"',
            "-c",
            'approvals_reviewer="auto_review"',
            "-c",
            _config_string("developer_instructions", self._developer_instructions()),
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
                *common_options,
                CODEX_BYPASS_APPROVALS_AND_SANDBOX,
                session_id,
                "-",
            ]
        return [
            self.codex_bin,
            "exec",
            *common_options,
            CODEX_BYPASS_APPROVALS_AND_SANDBOX,
            "--output-schema",
            str(OA_APPROVAL_SCHEMA_PATH),
            "--cd",
            str(self.workspace),
            "-",
        ]

    def _developer_instructions(self) -> str:
        skill_text = self.skill_path.read_text(encoding="utf-8")
        return (
            "You are the local DingTalk OA approval runner. Follow the injected "
            "dingtalk-oa-approval skill exactly. Return only the requested JSON.\n\n"
            "# Injected dingtalk-oa-approval skill\n\n"
            f"{skill_text}"
        )


def parse_oa_approval_json(raw: str) -> OaApprovalResult:
    for payload in reversed(_iter_json_payloads(raw)):
        result = _result_from_payload(payload)
        if result is not None:
            return result
    raise json.JSONDecodeError("No OA approval result JSON found", raw, 0)


def _iter_json_payloads(raw: str) -> list[Any]:
    stripped = raw.strip()
    if not stripped:
        return []
    try:
        return [json.loads(stripped)]
    except json.JSONDecodeError:
        payloads: list[Any] = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payloads.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return payloads


def _result_from_payload(payload: Any) -> OaApprovalResult | None:
    if not isinstance(payload, dict):
        return None
    try:
        return OaApprovalResult.model_validate(payload)
    except ValidationError:
        pass
    for text in _result_text_candidates(payload):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        try:
            return OaApprovalResult.model_validate(parsed)
        except ValidationError:
            continue
    return None


def _result_text_candidates(payload: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    message = payload.get("message")
    if isinstance(message, str):
        candidates.append(message)
    content = payload.get("content")
    if isinstance(content, str):
        candidates.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                candidates.append(item["text"])
    item = payload.get("item")
    if (
        isinstance(item, dict)
        and item.get("type") == "agent_message"
        and isinstance(item.get("text"), str)
    ):
        candidates.append(item["text"])
    nested_payload = payload.get("payload")
    if isinstance(nested_payload, dict):
        candidates.extend(_result_text_candidates(nested_payload))
    return candidates
