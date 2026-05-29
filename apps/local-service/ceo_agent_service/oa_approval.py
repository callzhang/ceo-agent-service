import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import BaseModel, Field, ValidationError, model_validator

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
from ceo_agent_service.process_runner import run_process_with_idle_timeout


OA_APPROVAL_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "oa_approval.schema.json"
)
DEFAULT_OA_APPROVAL_SKILL_PATH = (
    Path.home() / ".agents" / "skills" / "dingtalk-oa-approval" / "SKILL.md"
)
AFLOW_HOST = "aflow.dingtalk.com"
URL_TRAILING_CHARS = "\"'`>,.。；;，"
SECRET_PATTERNS = (
    re.compile(r"access_token=[^\s&]+", re.IGNORECASE),
    re.compile(r"appsecret=[^\s&]+", re.IGNORECASE),
    re.compile(r"appkey=[^\s&]+", re.IGNORECASE),
    re.compile(r"cookie[:=][^\s]+", re.IGNORECASE),
    re.compile(r"oauth[_-]?code=[^\s&]+", re.IGNORECASE),
)
OA_MUTATING_COMMAND_PATTERN = re.compile(
    r"\bdws\s+oa\s+approval\s+(?:approve|reject|return)\b",
    re.IGNORECASE,
)
SESSION_OA_RESULT_GRACE_SECONDS = 15


class OaApprovalResult(BaseModel):
    process_instance_id: str = ""
    task_id: str = ""
    oa_url: str = ""
    oa_action: Literal["通过", "拒绝", "退回"]
    oa_remark: str = Field(min_length=1)
    action_result: dict[str, Any]
    audit_summary: str = Field(min_length=1)
    audit_documents: list[dict[str, str]]

    @model_validator(mode="after")
    def validate_oa_identifiers(self) -> "OaApprovalResult":
        if not self.oa_url:
            return self
        parsed = urlparse(self.oa_url)
        if parsed.netloc != AFLOW_HOST:
            raise ValueError("oa_url must be an aflow.dingtalk.com URL")
        query = parse_qs(parsed.query)
        process_values = {
            value
            for key in ("procInstId", "processInstanceId", "process_instance_id")
            for value in query.get(key, [])
        }
        if process_values and self.process_instance_id not in process_values:
            raise ValueError("process_instance_id does not match oa_url")
        task_values = {
            value
            for key in ("taskId", "task_id")
            for value in query.get(key, [])
        }
        if task_values and self.task_id not in task_values:
            raise ValueError("task_id does not match oa_url")
        return self


def extract_oa_url(text: str) -> str:
    for candidate in _urlish_candidates(text):
        nested = _nested_aflow_url(candidate)
        if nested:
            return nested
        direct = _aflow_url(candidate)
        if direct:
            return direct
        decoded = unquote(candidate)
        nested = _nested_aflow_url(decoded)
        if nested:
            return nested
        direct = _aflow_url(decoded)
        if direct:
            return direct
    return ""


def _urlish_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    text = text.replace("\\/", "/").replace("\\u0026", "&")
    for separator in ('"', "'", " ", "\n", "\t", "\r", "<", ">"):
        text = text.replace(separator, "\n")
    for raw in text.splitlines():
        candidate = raw.strip().strip("()[]{}")
        if candidate:
            candidates.append(candidate)
    return candidates


def _aflow_url(value: str) -> str:
    marker = f"https://{AFLOW_HOST}"
    start = value.find(marker)
    if start < 0:
        return ""
    url = value[start:]
    for delimiter in ("&quot;", "\\u0026quot;", "}", "]", ")"):
        position = url.find(delimiter)
        if position >= 0:
            url = url[:position]
    url = url.rstrip(URL_TRAILING_CHARS)
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
        idle_timeout_seconds: int = 180,
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
        self.idle_timeout_seconds = idle_timeout_seconds
        self.codex_home = codex_home
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line: int = 0
        self.last_transcript_end_line: int = 0

    def run(
        self,
        prompt: str,
        session_id: str | None = None,
        allow_side_effects: bool = True,
    ) -> OaApprovalResult:
        raw_outputs: list[str] = []
        self.last_audit_tool_events = []
        self.last_session_id = session_id
        self.last_transcript_start_line = self._session_line_count(session_id)
        self.last_transcript_end_line = self.last_transcript_start_line

        raw = self.executor(
            self.runner.build_command(
                prompt,
                session_id,
                allow_side_effects=allow_side_effects,
            ),
            prompt,
        )
        raw_outputs.append(raw)
        self._remember_session_id(raw)
        try:
            result = parse_oa_approval_json(raw)
        except (json.JSONDecodeError, ValidationError):
            session_result = self._current_session_result(
                wait_seconds=SESSION_OA_RESULT_GRACE_SECONDS
            )
            if session_result is not None:
                result = session_result
            elif allow_side_effects:
                self._remember_audit_tool_events(raw_outputs)
                raise RuntimeError(
                    f"invalid OA approval JSON: {raw[:200]}"
                ) from None
            else:
                retry_session_id = session_id or self.last_session_id
                repair_prompt = (
                    "上一次输出不是合法 OA 审批 JSON。不得执行通过、拒绝、退回或评论。"
                    "只输出合法 JSON，不要解释。action_result 必须是空对象 {}。"
                    "oa_action 只能是 通过、拒绝、退回 之一；oa_remark、audit_summary 必须非空。"
                    "如果无法取得 process_instance_id、task_id 或 oa_url，对应字段填空字符串。"
                )
                second_raw = self.executor(
                    self.runner.build_command(
                        repair_prompt,
                        retry_session_id,
                        allow_side_effects=False,
                    ),
                    repair_prompt,
                )
                raw_outputs.append(second_raw)
                self._remember_session_id(second_raw)
                try:
                    result = parse_oa_approval_json(second_raw)
                except (json.JSONDecodeError, ValidationError):
                    session_result = self._current_session_result(
                        wait_seconds=SESSION_OA_RESULT_GRACE_SECONDS
                    )
                    if session_result is None:
                        self._remember_audit_tool_events(raw_outputs)
                        raise RuntimeError(
                            "invalid OA approval JSON twice: "
                            f"{raw[:200]} | {second_raw[:200]}"
                        ) from None
                    result = session_result
        self._remember_audit_tool_events(raw_outputs)
        if not allow_side_effects:
            _validate_read_only_result(result, self.last_audit_tool_events)
        return result

    def handle(
        self,
        trigger_text: str,
        context_text: str,
        oa_url: str,
        approval_detail_text: str = "",
        execute: bool = True,
    ) -> OaApprovalResult:
        mode = (
            "执行模式：可以在完整审阅并确认 taskId 后执行通过、拒绝或退回。"
            if execute
            else "只读审阅模式：不得执行通过、拒绝、退回或评论；只输出建议动作和建议留言，action_result 填 {}。"
        )
        prompt = (
            "请审阅并处理下面这条 DingTalk OA 审批消息。\n\n"
            f"{mode}\n\n"
            f"OA URL:\n{oa_url}\n\n"
            f"触发消息:\n{trigger_text}\n\n"
            f"服务侧已读取的审批 API 详情:\n{approval_detail_text}\n\n"
            f"会话上下文:\n{context_text}"
        )
        return self.run(prompt, session_id=None, allow_side_effects=execute)

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
        completed = run_process_with_idle_timeout(
            command,
            prompt=prompt,
            env=self.runner.build_env(),
            total_timeout_seconds=self.timeout_seconds,
            idle_timeout_seconds=self.idle_timeout_seconds,
        )
        if completed.timed_out:
            raise RuntimeError(completed.timeout_reason)
        if completed.returncode != 0:
            raise RuntimeError(
                _codex_stdout_error_reason(completed.stdout)
                or _subprocess_failure_reason(completed.stderr)
            )
        return completed.stdout.strip()

    def _session_line_count(self, session_id: str | None) -> int:
        if not session_id:
            return 0
        return count_codex_session_lines(session_id, codex_home=self.codex_home)

    def _current_session_result(
        self,
        wait_seconds: int = 0,
    ) -> OaApprovalResult | None:
        if not self.last_session_id:
            return None
        deadline = time.monotonic() + wait_seconds
        while True:
            result = self._read_current_session_result()
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                return None
            time.sleep(5)

    def _read_current_session_result(self) -> OaApprovalResult | None:
        session_id = self.last_session_id
        if not session_id:
            return None
        from ceo_agent_service.codex_history import find_codex_session_path

        path = find_codex_session_path(session_id, codex_home=self.codex_home)
        if path is None:
            return None
        current_turn = "\n".join(
            path.read_text(encoding="utf-8").splitlines()[
                self.last_transcript_start_line :
            ]
        )
        if not current_turn.strip():
            return None
        try:
            return parse_oa_approval_json(current_turn)
        except (json.JSONDecodeError, ValidationError):
            return None


class _OaApprovalCommandBuilder:
    def __init__(self, workspace: Path, codex_bin: str, skill_path: Path):
        self.workspace = workspace
        self.codex_bin = codex_bin
        self.skill_path = skill_path

    def build_env(self) -> dict[str, str]:
        return os.environ.copy()

    def build_command(
        self,
        prompt: str,
        session_id: str | None,
        *,
        allow_side_effects: bool = True,
    ) -> list[str]:
        if allow_side_effects:
            safety_options = [
                "-c",
                'approval_policy="untrusted"',
                "-c",
                'approvals_reviewer="auto_review"',
            ]
            bypass_options = [CODEX_BYPASS_APPROVALS_AND_SANDBOX]
        else:
            safety_options = [
                "-c",
                'approval_policy="never"',
                "-c",
                'sandbox_mode="read-only"',
            ]
            bypass_options = []
        common_options = [
            "--json",
            "-m",
            "gpt-5.5",
            "--ignore-user-config",
            "--ignore-rules",
            *safety_options,
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
                *bypass_options,
                "--output-schema",
                str(OA_APPROVAL_SCHEMA_PATH),
                session_id,
                "-",
            ]
        return [
            self.codex_bin,
            "exec",
            *common_options,
            *bypass_options,
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
            "dingtalk-oa-approval skill exactly. Return only the requested JSON. "
            "Do not expose tokens, AppKey, AppSecret, cookies, OAuth codes, "
            "signed URLs, or local credential paths.\n\n"
            "# Injected dingtalk-oa-approval skill\n\n"
            f"{skill_text}"
        )


def _validate_read_only_result(
    result: OaApprovalResult,
    audit_tool_events: list[dict[str, str]],
) -> None:
    if result.action_result:
        raise RuntimeError("read-only OA approval review returned action_result")
    for event in audit_tool_events:
        command = str(event.get("command") or event.get("cmd") or "")
        if OA_MUTATING_COMMAND_PATTERN.search(command):
            raise RuntimeError("read-only OA approval review attempted a mutating action")


def _subprocess_failure_reason(stderr: str) -> str:
    normalized = " ".join(line.strip() for line in stderr.splitlines() if line.strip())
    if not normalized:
        return "codex exec failed without stderr"
    for pattern in SECRET_PATTERNS:
        normalized = pattern.sub("[REDACTED]", normalized)
    if len(normalized) <= 1200:
        return normalized
    return f"{normalized[:1200]}..."


def _codex_stdout_error_reason(stdout: str) -> str:
    for payload in reversed(_iter_json_payloads(stdout)):
        if not isinstance(payload, dict) or payload.get("type") != "error":
            continue
        message = payload.get("message")
        if not isinstance(message, str):
            continue
        code = ""
        detail = message
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "")
                detail = str(error.get("message") or detail)
        normalized = f"{code}: {detail}" if code else detail
        return _redact_failure_reason(normalized)
    return ""


def _redact_failure_reason(reason: str) -> str:
    normalized = " ".join(line.strip() for line in reason.splitlines() if line.strip())
    for pattern in SECRET_PATTERNS:
        normalized = pattern.sub("[REDACTED]", normalized)
    if len(normalized) <= 1200:
        return normalized
    return f"{normalized[:1200]}..."


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
    last_agent_message = payload.get("last_agent_message")
    if isinstance(last_agent_message, str):
        candidates.append(last_agent_message)
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
    elif isinstance(item, dict):
        candidates.extend(_result_text_candidates(item))
    nested_payload = payload.get("payload")
    if isinstance(nested_payload, dict):
        candidates.extend(_result_text_candidates(nested_payload))
    return candidates
