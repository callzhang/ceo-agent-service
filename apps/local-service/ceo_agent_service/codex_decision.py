import json
import shlex
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ceo_agent_service.codex_history import (
    count_codex_session_lines,
    extract_codex_audit_events_from_session,
    find_codex_session_path,
)
from ceo_agent_service.codex_runner import CodexRunner
from ceo_agent_service.config import assistant_signature, forbidden_path_prefixes
from ceo_agent_service.dingtalk_models import CodexAction, CodexDecision
from ceo_agent_service.process_runner import run_process_with_idle_timeout


SIGNATURE = assistant_signature()
CODEX_TIMEOUT_REASON_PREFIX = "codex exec timed out after"
TIMEOUT_SESSION_DECISION_GRACE_SECONDS = 90


def append_signature(text: str) -> str:
    stripped = text.strip()
    if stripped.endswith(SIGNATURE):
        return stripped
    return f"{stripped}{SIGNATURE}"


def parse_codex_json(raw: str) -> CodexDecision:
    stripped = raw.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return _parse_codex_jsonl(stripped)

    decision = _decision_from_payload(payload)
    if decision is not None:
        return decision
    raise json.JSONDecodeError("No Codex decision JSON found", raw, 0)


def extract_codex_session_id(raw: str) -> str | None:
    session_id: str | None = None
    for payload in _iter_json_payloads(raw):
        found = _session_id_from_payload(payload)
        if found:
            session_id = found
    return session_id


def extract_codex_audit_events(raw: str, limit: int = 40) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for payload in _iter_json_payloads(raw):
        event = _audit_event_from_payload(payload)
        if event:
            events.append(event)
        if len(events) >= limit:
            break
    return events


def _parse_codex_jsonl(raw: str) -> CodexDecision:
    for payload in reversed(list(_iter_json_payloads(raw))):
        decision = _decision_from_payload(payload)
        if decision is not None:
            return decision
    raise json.JSONDecodeError("No Codex decision JSON found", raw, 0)


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


def _decision_from_payload(payload: Any) -> CodexDecision | None:
    if isinstance(payload, dict):
        try:
            return CodexDecision.model_validate(payload)
        except ValidationError:
            pass

        for text in _decision_text_candidates(payload):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            try:
                return CodexDecision.model_validate(parsed)
            except ValidationError:
                continue
    return None


def _decision_text_candidates(payload: dict[str, Any]) -> list[str]:
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
        candidates.extend(_decision_text_candidates(nested_payload))
    return candidates


def _session_id_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("session_id", "sessionId", "thread_id", "threadId"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    if payload.get("type") == "session":
        value = payload.get("id")
        if isinstance(value, str) and value:
            return value
    if payload.get("type") == "session_meta":
        meta = payload.get("payload")
        if isinstance(meta, dict):
            value = meta.get("id")
            if isinstance(value, str) and value:
                return value
    return None


def _audit_event_from_payload(payload: Any) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    item = payload.get("item")
    source = item if isinstance(item, dict) else payload
    event_type = _string_value(payload, "type")
    tool = (
        _string_value(source, "tool_name")
        or _string_value(source, "name")
        or _string_value(source, "type")
    )
    command = _first_string_for_keys(source, {"cmd", "command"})
    path = _first_pathish_string(source)
    if not command and not path:
        return None
    event: dict[str, str] = {}
    if event_type:
        event["event_type"] = _short_text(event_type)
    if tool:
        event["tool"] = _short_text(tool)
    if command:
        event["command"] = _short_text(command, 500)
    if path:
        event["path"] = _short_text(path, 500)
    return event


def _string_value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _first_string_for_keys(payload: Any, keys: set[str]) -> str:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str):
                return value
            found = _first_string_for_keys(value, keys)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_string_for_keys(item, keys)
            if found:
                return found
    return ""


def _first_pathish_string(payload: Any) -> str:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, str):
                path = _extract_pathish_token(value)
                if path:
                    return path
            found = _first_pathish_string(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_pathish_string(item)
            if found:
                return found
    return ""


def _extract_pathish_token(value: str) -> str:
    if not any(char.isspace() for char in value) and _looks_like_path(value):
        return value
    try:
        tokens = shlex.split(value)
    except ValueError:
        tokens = value.split()
    for token in tokens:
        stripped = token.strip("'\"`[](),")
        if _looks_like_path(stripped):
            return stripped
    return ""


def _looks_like_path(value: str) -> bool:
    return (
        any(value.startswith(prefix) for prefix in forbidden_path_prefixes())
        or value.startswith("AI听记/")
        or value.startswith("management/")
        or value.startswith("projects/")
        or value.endswith(".md")
        or value.endswith(".pdf")
        or value.endswith(".docx")
        or value.endswith(".xlsx")
    )


def _short_text(text: str, limit: int = 240) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _subprocess_failure_reason(stderr: str, stdout: str) -> str:
    stderr_lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in stderr_lines:
        if " ERROR " in f" {line} ":
            return _short_text(line, 1200)
    for line in stderr_lines:
        if " WARN " not in f" {line} ":
            return _short_text(line, 1200)
    return _short_text(stderr.strip() or stdout, 1200)


def _timeout_output_text(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace").strip()
    return output.strip()


def audit_summary_explains_no_documents(summary: str) -> bool:
    normalized = " ".join(summary.split())
    no_document_markers = (
        "未找到可用文档",
        "没有找到可用文档",
        "未找到文档证据",
        "没有找到文档证据",
        "无可用文档",
        "无文档证据",
        "没有查看文档",
        "未查看文档",
        "只需上下文判断",
        "仅根据上下文判断",
        "基于上下文判断",
        "只需当前消息判断",
        "仅根据当前消息判断",
    )
    return any(marker in normalized for marker in no_document_markers)


class CodexDecisionRunner:
    def __init__(
        self,
        workspace: Path,
        codex_bin: str = "codex",
        executor: Callable[[list[str], str], str] | None = None,
        timeout_seconds: int = 120,
        idle_timeout_seconds: int = 180,
        codex_home: Path | None = None,
    ):
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor or self._subprocess_executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self.codex_home = codex_home
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line: int = 0
        self.last_transcript_end_line: int = 0

    def decide(self, prompt: str, session_id: str | None) -> CodexDecision:
        raw_outputs: list[str] = []
        self.last_audit_tool_events = []
        self.last_session_id = session_id
        self.last_transcript_start_line = self._session_line_count(session_id)
        self.last_transcript_end_line = self.last_transcript_start_line
        first_raw = self.executor(self.runner.build_command(prompt, session_id), prompt)
        raw_outputs.append(first_raw)
        self._remember_session_id(first_raw)
        try:
            decision = parse_codex_json(first_raw)
            timeout_session_decision = self._timeout_session_decision(decision)
            if timeout_session_decision is not None:
                self._remember_audit_tool_events(raw_outputs)
                return timeout_session_decision
            self._validate_decision(decision)
            self._remember_audit_tool_events(raw_outputs)
            return decision
        except (json.JSONDecodeError, ValidationError, ValueError):
            session_decision = self._current_session_decision()
            if session_decision is not None:
                try:
                    self._validate_decision(session_decision)
                    self._remember_audit_tool_events(raw_outputs)
                    return session_decision
                except ValueError:
                    pass
            retry_session_id = session_id or self.last_session_id
            repair_prompt = (
                "上一次输出不是合法 JSON，或 action 需要回复但 reply_text 为空。"
                "只输出合法 JSON，不要解释。send_reply 和 ask_clarifying_question 的 reply_text 必须非空。"
                "audit_summary 必须非空。"
                "send_reply/ask_clarifying_question 如果 audit_documents 为空，"
                "audit_summary 必须说明未找到可用文档证据或只需上下文判断。"
                'JSON schema: {"action":"send_reply|ask_clarifying_question|handoff_to_human|no_reply|stop_with_error",'
                '"reply_text":"","reason":"","ding_self":false,"macos_notify":true,'
                '"sensitivity_kind":"general|internal_personnel|external_candidate",'
                '"personnel_subject_user_id":null,"candidate_context_known":false,"candidate_department_ids":[],'
                '"audit_documents":[],"audit_summary":""}'
            )
            second_raw = self.executor(
                self.runner.build_command(repair_prompt, retry_session_id),
                repair_prompt,
            )
            raw_outputs.append(second_raw)
            self._remember_session_id(second_raw)
            try:
                decision = parse_codex_json(second_raw)
                timeout_session_decision = self._timeout_session_decision(decision)
                if timeout_session_decision is not None:
                    self._remember_audit_tool_events(raw_outputs)
                    return timeout_session_decision
                self._validate_decision(decision)
                self._remember_audit_tool_events(raw_outputs)
                return decision
            except (json.JSONDecodeError, ValidationError, ValueError):
                session_decision = self._current_session_decision()
                if session_decision is not None:
                    try:
                        self._validate_decision(session_decision)
                        self._remember_audit_tool_events(raw_outputs)
                        return session_decision
                    except ValueError:
                        pass
                self._remember_audit_tool_events(raw_outputs)
                return CodexDecision(
                    action=CodexAction.STOP_WITH_ERROR,
                    reason=f"invalid JSON or Codex decision twice: {first_raw[:200]} | {second_raw[:200]}",
                    macos_notify=True,
                )

    def _remember_session_id(self, raw: str) -> None:
        session_id = extract_codex_session_id(raw)
        if session_id:
            self.last_session_id = session_id

    def _timeout_session_decision(self, decision: CodexDecision) -> CodexDecision | None:
        if (
            decision.action != CodexAction.STOP_WITH_ERROR
            or CODEX_TIMEOUT_REASON_PREFIX not in decision.reason
        ):
            return None
        session_decision = self._current_session_decision(
            wait_seconds=TIMEOUT_SESSION_DECISION_GRACE_SECONDS
        )
        if session_decision is None:
            return None
        try:
            self._validate_decision(session_decision)
        except ValueError:
            return None
        return session_decision

    def _current_session_decision(self, wait_seconds: int = 0) -> CodexDecision | None:
        if not self.last_session_id:
            return None
        deadline = time.monotonic() + wait_seconds
        while True:
            decision = self._read_current_session_decision()
            if decision is not None:
                return decision
            if time.monotonic() >= deadline:
                return None
            time.sleep(5)

    def _read_current_session_decision(self) -> CodexDecision | None:
        session_id = self.last_session_id
        if not session_id:
            return None
        path = find_codex_session_path(session_id, codex_home=self.codex_home)
        if path is None:
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        current_turn = "\n".join(lines[self.last_transcript_start_line :])
        if not current_turn.strip():
            return None
        try:
            return parse_codex_json(current_turn)
        except (json.JSONDecodeError, ValidationError):
            return None

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

    @staticmethod
    def _validate_decision(decision: CodexDecision) -> None:
        if (
            decision.action != CodexAction.STOP_WITH_ERROR
            and not decision.audit_summary.strip()
        ):
            raise ValueError("audit_summary is required for Codex decisions")
        if (
            decision.action
            in {CodexAction.SEND_REPLY, CodexAction.ASK_CLARIFYING_QUESTION}
            and not decision.reply_text.strip()
        ):
            raise ValueError("reply_text is required for reply actions")
        if (
            decision.action
            in {CodexAction.SEND_REPLY, CodexAction.ASK_CLARIFYING_QUESTION}
            and not decision.audit_documents
            and not audit_summary_explains_no_documents(decision.audit_summary)
        ):
            raise ValueError(
                "audit_summary must explain missing document evidence when audit_documents is empty"
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
            stdout = _timeout_output_text(completed.stdout)
            stop_error = json.dumps(
                {
                    "action": "stop_with_error",
                    "reason": (
                        completed.timeout_reason
                        if completed.timeout_kind == "idle"
                        else f"{CODEX_TIMEOUT_REASON_PREFIX} {self.timeout_seconds} seconds"
                    ),
                    "macos_notify": True,
                },
                ensure_ascii=False,
            )
            if stdout:
                return f"{stdout}\n{stop_error}"
            return stop_error
        if completed.returncode != 0:
            stdout = completed.stdout.strip()
            if stdout:
                try:
                    parse_codex_json(stdout)
                    return stdout
                except (json.JSONDecodeError, ValidationError):
                    pass
            reason = _subprocess_failure_reason(completed.stderr, stdout)
            stop_error = json.dumps(
                {
                    "action": "stop_with_error",
                    "reason": reason,
                    "macos_notify": True,
                },
                ensure_ascii=False,
            )
            if stdout:
                return f"{stdout}\n{stop_error}"
            return stop_error
        return completed.stdout.strip()

    def _session_line_count(self, session_id: str | None) -> int:
        if not session_id:
            return 0
        return count_codex_session_lines(session_id, codex_home=self.codex_home)
