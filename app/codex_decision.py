import json
import re
import shlex
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.codex_history import (
    count_codex_session_lines,
    extract_codex_audit_events_from_session,
    find_codex_session_path,
)
from app.codex_runner import CodexRunner
from app.config import assistant_signature, forbidden_path_prefixes
from app.dingtalk_models import CodexAction, CodexDecision
from app.process_runner import run_process_with_idle_timeout


SIGNATURE = assistant_signature()
CODEX_TIMEOUT_REASON_PREFIX = "codex exec timed out after"
TIMEOUT_SESSION_DECISION_GRACE_SECONDS = 90
SESSION_DECISION_GRACE_SECONDS = 15
REPLY_AGENT_ENVELOPE_SCHEMA_HINT = (
    'JSON schema: {"kind":"reply|okr_review|no_action|error",'
    '"user_response":{"mode":"send_reply|ask_clarifying_question|handoff_to_human|no_reply",'
    '"text":"","sensitivity_kind":"general|internal_personnel|external_candidate"},'
    '"system_actions":[{"type":"send_dingtalk_reply|dws_markdown_document_reply|dws_message_reaction|queue_okr_review"}],'
    '"domain_payload":{},'
    '"audit":{"summary":"","documents":[{"title":"","url":"","relevance":""}],"confidence":0.8}}'
)
SECRET_PATTERNS = (
    re.compile(r"access_token=[^\s&]+", re.IGNORECASE),
    re.compile(r"appsecret=[^\s]+", re.IGNORECASE),
    re.compile(r"appkey=[^\s]+", re.IGNORECASE),
    re.compile(r"cookie[:=][^\s]+", re.IGNORECASE),
    re.compile(r"oauth[_-]?code=[^\s&]+", re.IGNORECASE),
)


def append_signature(text: str) -> str:
    stripped = text.strip()
    if stripped.endswith(SIGNATURE):
        return stripped
    return f"{stripped}{SIGNATURE}"


def error_agent_envelope_json(reason: str) -> str:
    return json.dumps(
        {
            "kind": "error",
            "user_response": {
                "mode": "no_reply",
                "text": "",
                "sensitivity_kind": "general",
            },
            "system_actions": [],
            "domain_payload": {},
            "audit": {"summary": reason, "documents": [], "confidence": 0},
        },
        ensure_ascii=False,
    )


def codex_decision_from_envelope(envelope: Any) -> CodexDecision:
    from app.agent_envelope import AgentEnvelope, AgentKind, UserResponseMode

    parsed = AgentEnvelope.model_validate(envelope)
    if parsed.kind == AgentKind.ERROR:
        return CodexDecision(
            action=CodexAction.STOP_WITH_ERROR,
            reason=parsed.audit.summary,
            audit_summary=parsed.audit.summary,
        )
    if parsed.user_response.mode == UserResponseMode.NO_REPLY:
        action = CodexAction.NO_REPLY
    elif parsed.user_response.mode == UserResponseMode.ASK_CLARIFYING_QUESTION:
        action = CodexAction.ASK_CLARIFYING_QUESTION
    elif parsed.user_response.mode == UserResponseMode.HANDOFF_TO_HUMAN:
        action = CodexAction.HANDOFF_TO_HUMAN
    else:
        action = CodexAction.SEND_REPLY
    return CodexDecision(
        action=action,
        reply_text=parsed.user_response.text,
        reason=parsed.audit.summary,
        sensitivity_kind=parsed.user_response.sensitivity_kind.value,
        personnel_subject_user_id=parsed.domain_payload.get(
            "personnel_subject_user_id"
        ),
        candidate_context_known=bool(
            parsed.domain_payload.get("candidate_context_known", False)
        ),
        candidate_department_ids=parsed.domain_payload.get(
            "candidate_department_ids", []
        ),
        calendar_response_status=parsed.domain_payload.get(
            "calendar_response_status", ""
        ),
        system_actions=[action.model_dump() for action in parsed.system_actions],
        audit_documents=[doc.model_dump() for doc in parsed.audit.documents],
        audit_summary=parsed.audit.summary,
    )


def parse_codex_json(raw: str, *, allow_legacy: bool = True) -> CodexDecision:
    stripped = raw.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return _parse_codex_jsonl(stripped, allow_legacy=allow_legacy)

    decision = _decision_from_payload(payload, allow_legacy=allow_legacy)
    if decision is not None:
        return decision
    message = (
        "No AgentEnvelope JSON found"
        if not allow_legacy
        else "No Codex decision JSON found"
    )
    raise json.JSONDecodeError(message, raw, 0)


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


def _parse_codex_jsonl(raw: str, *, allow_legacy: bool = True) -> CodexDecision:
    for payload in reversed(list(_iter_json_payloads(raw))):
        decision = _decision_from_payload(payload, allow_legacy=allow_legacy)
        if decision is not None:
            return decision
    message = (
        "No AgentEnvelope JSON found"
        if not allow_legacy
        else "No Codex decision JSON found"
    )
    raise json.JSONDecodeError(message, raw, 0)


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


def _decision_from_payload(
    payload: Any,
    *,
    allow_legacy: bool = True,
) -> CodexDecision | None:
    if isinstance(payload, dict):
        if _looks_like_agent_envelope(payload):
            try:
                return codex_decision_from_envelope(payload)
            except Exception:
                decision = _decision_from_agent_envelope_like(payload)
                if decision is not None:
                    return decision
        if allow_legacy:
            try:
                return CodexDecision.model_validate(payload)
            except ValidationError:
                pass

        for text in _decision_text_candidates(payload):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and _looks_like_agent_envelope(parsed):
                try:
                    return codex_decision_from_envelope(parsed)
                except Exception:
                    decision = _decision_from_agent_envelope_like(parsed)
                    if decision is not None:
                        return decision
            if not allow_legacy:
                continue
            try:
                return CodexDecision.model_validate(parsed)
            except ValidationError:
                continue
    return None


def _looks_like_agent_envelope(payload: dict[str, Any]) -> bool:
    return "kind" in payload and "user_response" in payload


def _decision_from_agent_envelope_like(
    payload: dict[str, Any],
) -> CodexDecision | None:
    user_response = payload.get("user_response")
    if not isinstance(user_response, dict):
        return None
    try:
        action = CodexAction(str(user_response.get("mode") or ""))
    except ValueError:
        return None
    reply_text = _string_value(user_response, "text")
    sensitivity_kind = _string_value(user_response, "sensitivity_kind") or "general"
    audit = payload.get("audit")
    audit_summary = ""
    audit_documents: list[dict[str, str]] = []
    if isinstance(audit, dict):
        audit_summary = (
            _string_value(audit, "summary")
            or _string_value(audit, "evidence_summary")
            or _string_value(audit, "reason")
        )
        audit_documents = _audit_documents_from_payload(audit.get("documents"))
    if not audit_summary:
        audit_summary = "Agent returned a non-standard envelope; extracted user_response."
    system_actions = payload.get("system_actions")
    domain_payload = payload.get("domain_payload")
    if not isinstance(domain_payload, dict):
        domain_payload = {}
    candidate_department_ids = domain_payload.get("candidate_department_ids", [])
    if not isinstance(candidate_department_ids, list):
        candidate_department_ids = []
    return CodexDecision(
        action=action,
        reply_text=reply_text,
        reason=audit_summary,
        sensitivity_kind=sensitivity_kind,
        personnel_subject_user_id=domain_payload.get("personnel_subject_user_id"),
        candidate_context_known=bool(
            domain_payload.get("candidate_context_known", False)
        ),
        candidate_department_ids=[
            str(department_id)
            for department_id in candidate_department_ids
            if str(department_id).strip()
        ],
        calendar_response_status=domain_payload.get("calendar_response_status", ""),
        system_actions=system_actions if isinstance(system_actions, list) else [],
        audit_documents=audit_documents,
        audit_summary=audit_summary,
    )


def _audit_documents_from_payload(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    documents = []
    for item in value:
        if isinstance(item, str):
            documents.append({"title": item, "url": "", "relevance": "mentioned"})
            continue
        if not isinstance(item, dict):
            continue
        documents.append(
            {
                "title": str(item.get("title") or item.get("name") or ""),
                "url": str(item.get("url") or ""),
                "relevance": str(
                    item.get("relevance")
                    or item.get("summary")
                    or item.get("status")
                    or "mentioned"
                ),
            }
        )
    return documents


def _decision_text_candidates(payload: dict[str, Any]) -> list[str]:
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
        candidates.extend(_decision_text_candidates(item))
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
    source_type = _string_value(source, "type")
    output = _output_text(source)
    is_output = source_type in {"tool_result", "function_call_output"} or (
        output and source_type in {"tool_output"}
    )
    tool = (
        "tool_output"
        if is_output
        else _string_value(source, "tool_name")
        or _string_value(source, "name")
        or _string_value(source, "tool")
        or _string_value(source, "type")
    )
    call_id = _string_value(source, "call_id") or _string_value(payload, "call_id")
    arguments = source.get("arguments")
    input_text = _json_text(arguments)
    command = _first_string_for_keys(arguments, {"cmd", "command"})
    if not command:
        command = _first_string_for_keys(source, {"cmd", "command"})
    path = _first_pathish_string(output) if output else ""
    if not path:
        path = _first_pathish_string(source)
    if not any([command, path, input_text, output]) and not _is_tool_call_event(
        source_type
    ):
        return None
    event: dict[str, str] = {}
    if event_type:
        event["event_type"] = _short_text(event_type)
    if tool:
        event["tool"] = _short_text(tool)
    if call_id:
        event["call_id"] = _short_text(call_id, 500)
    if input_text:
        event["input"] = input_text
    if command:
        event["command"] = _short_text(command, 500)
    if output:
        event["output"] = output
    if path:
        event["path"] = _short_text(path, 500)
    return event


def _is_tool_call_event(source_type: str) -> bool:
    return source_type in {
        "function_call",
        "mcp_tool_call",
        "tool_call",
        "tool_search_call",
    }


def _string_value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        if not value:
            return ""
        try:
            return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return ""


def _output_text(payload: dict[str, Any]) -> str:
    for key in ("output", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
    return ""


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
    for pattern in SECRET_PATTERNS:
        normalized = pattern.sub("[REDACTED]", normalized)
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _codex_stdout_error_reason(stdout: str) -> str:
    for payload in reversed(_iter_json_payloads(stdout)):
        if not isinstance(payload, dict) or payload.get("type") != "error":
            continue
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            continue
        detail = message
        code = ""
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "")
                detail = str(error.get("message") or detail)
        return _short_text(f"{code}: {detail}" if code else detail, 1200)
    return ""


def _subprocess_failure_reason(stderr: str, stdout: str) -> str:
    stdout_error = _codex_stdout_error_reason(stdout)
    if stdout_error:
        return stdout_error
    stderr_lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in stderr_lines:
        if " ERROR " in f" {line} ":
            return _short_text(line, 1200)
    for line in stderr_lines:
        if " WARN " not in f" {line} ":
            return _short_text(line, 1200)
    return "codex exec failed without a valid AgentEnvelope"


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

    def decide(
        self,
        prompt: str,
        session_id: str | None,
        image_paths: list[Path] | None = None,
    ) -> CodexDecision:
        raw_outputs: list[str] = []
        self.last_audit_tool_events = []
        self.last_session_id = session_id
        self.last_transcript_start_line = self._session_line_count(session_id)
        self.last_transcript_end_line = self.last_transcript_start_line
        first_raw = self.executor(
            self.runner.build_command(prompt, session_id, image_paths=image_paths),
            prompt,
        )
        raw_outputs.append(first_raw)
        self._remember_session_id(first_raw)
        try:
            decision = parse_codex_json(first_raw, allow_legacy=False)
            timeout_session_decision = self._timeout_session_decision(decision)
            if timeout_session_decision is not None:
                self._remember_audit_tool_events(raw_outputs)
                return timeout_session_decision
            self._validate_decision(decision)
            self._remember_audit_tool_events(raw_outputs)
            return decision
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            wait_seconds = (
                0
                if not isinstance(exc, (json.JSONDecodeError, ValidationError))
                else SESSION_DECISION_GRACE_SECONDS
            )
            session_decision = self._current_session_decision(
                wait_seconds=wait_seconds
            )
            if session_decision is not None:
                try:
                    self._validate_decision(session_decision)
                    self._remember_audit_tool_events(raw_outputs)
                    return session_decision
                except ValueError:
                    pass
            retry_session_id = session_id or self.last_session_id
            repair_prompt = (
                "上一次输出不是合法 AgentEnvelope JSON，或需要回复但 user_response.text 为空。"
                "只输出合法 JSON，不要解释。"
                "send_reply 和 ask_clarifying_question 的 user_response.text 必须非空。"
                "audit.summary 必须非空。"
                "send_reply/ask_clarifying_question 如果 audit.documents 为空，"
                "audit.summary 必须说明未找到可用文档证据或只需上下文判断。"
                f"{REPLY_AGENT_ENVELOPE_SCHEMA_HINT}"
            )
            second_raw = self.executor(
                self.runner.build_command(
                    repair_prompt,
                    retry_session_id,
                    image_paths=image_paths,
                ),
                repair_prompt,
            )
            raw_outputs.append(second_raw)
            self._remember_session_id(second_raw)
            try:
                decision = parse_codex_json(second_raw, allow_legacy=False)
                timeout_session_decision = self._timeout_session_decision(decision)
                if timeout_session_decision is not None:
                    self._remember_audit_tool_events(raw_outputs)
                    return timeout_session_decision
                self._validate_decision(decision)
                self._remember_audit_tool_events(raw_outputs)
                return decision
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                wait_seconds = (
                    0
                    if not isinstance(exc, (json.JSONDecodeError, ValidationError))
                    else SESSION_DECISION_GRACE_SECONDS
                )
                session_decision = self._current_session_decision(
                    wait_seconds=wait_seconds
                )
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
            return parse_codex_json(current_turn, allow_legacy=False)
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
            stop_error = error_agent_envelope_json(
                completed.timeout_reason
                if completed.timeout_kind == "idle"
                else f"{CODEX_TIMEOUT_REASON_PREFIX} {self.timeout_seconds} seconds"
            )
            if stdout:
                return f"{stdout}\n{stop_error}"
            return stop_error
        if completed.returncode != 0:
            stdout = completed.stdout.strip()
            if stdout:
                try:
                    parse_codex_json(stdout, allow_legacy=False)
                    return stdout
                except (json.JSONDecodeError, ValidationError):
                    pass
            reason = _subprocess_failure_reason(completed.stderr, stdout)
            stop_error = error_agent_envelope_json(reason)
            if stdout:
                return f"{stdout}\n{stop_error}"
            return stop_error
        return completed.stdout.strip()

    def _session_line_count(self, session_id: str | None) -> int:
        if not session_id:
            return 0
        return count_codex_session_lines(session_id, codex_home=self.codex_home)
