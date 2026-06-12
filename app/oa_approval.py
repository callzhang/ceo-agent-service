import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.agent_envelope import AgentEnvelope, AgentKind
from app.structured_agent import (
    AgentSpec,
    StructuredCodexRunner,
)


OA_APPROVAL_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schemas" / "oa_approval.schema.json"
)
DEFAULT_OA_APPROVAL_SKILL_PATH = (
    Path.home() / ".agents" / "skills" / "dingtalk-oa-approval" / "SKILL.md"
)
AFLOW_HOST = "aflow.dingtalk.com"
URL_TRAILING_CHARS = "\"'`>,.。；;，"
OA_MUTATING_COMMAND_PATTERN = re.compile(
    r"\bdws\s+oa\s+approval\s+(?:approve|reject|return)\b",
    re.IGNORECASE,
)
OA_DETAIL_COMMAND_PATTERN = re.compile(
    r"\bdws\s+oa\s+approval\s+detail\b",
    re.IGNORECASE,
)


class OaApprovalResult(BaseModel):
    process_instance_id: str = ""
    task_id: str = ""
    oa_url: str = ""
    oa_action: Literal["通过", "拒绝", "退回"]
    oa_remark: str = Field(min_length=1)
    action_result: dict[str, Any]
    audit_summary: str = Field(min_length=1)
    audit_documents: list[dict[str, str]]

    @model_validator(mode="before")
    @classmethod
    def normalize_audit_documents(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        documents = data.get("audit_documents")
        if not isinstance(documents, list):
            return data
        normalized = []
        changed = False
        for document in documents:
            if isinstance(document, str):
                normalized.append({"name": document, "status": "mentioned"})
                changed = True
            else:
                normalized.append(document)
        if not changed:
            return data
        return {**data, "audit_documents": normalized}

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
            if value
        }
        if process_values and self.process_instance_id not in process_values:
            raise ValueError("process_instance_id does not match oa_url")
        task_values = {
            value
            for key in ("taskId", "task_id")
            for value in query.get(key, [])
            if value
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


class OaApprovalReviewClient:
    def __init__(
        self,
        workspace: Path,
        codex_bin: str = "codex",
        executor: Callable[[list[str], str], str] | None = None,
        timeout_seconds: int = 120,
        idle_timeout_seconds: int = 180,
        codex_home: Path | None = None,
        skill_path: Path | None = None,
        store: Any | None = None,
    ):
        self.codex_home = codex_home
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line: int = 0
        self.last_transcript_end_line: int = 0
        self._store = store or _EphemeralStructuredStore()
        self._conversation_id = "__oa_approval__"
        self.runner = StructuredCodexRunner(
            store=self._store,
            workspace=workspace,
            spec=AgentSpec(
                name="oa_approval",
                schema_path=Path(__file__).resolve().parent
                / "schemas"
                / "agent_envelope.schema.json",
                primary_skill_paths=[skill_path or DEFAULT_OA_APPROVAL_SKILL_PATH],
                reply_visible_skill_paths=[],
                developer_preamble=(
                    "You are the local CEO Agent OA approval handler. "
                    "Follow the injected dingtalk-oa-approval skill exactly. "
                    "Return only AgentEnvelope JSON. Do not expose tokens, "
                    "AppKey, AppSecret, cookies, OAuth codes, signed URLs, "
                    "or local credential paths."
                ),
            ),
            codex_bin=codex_bin,
            executor=_adapt_executor(executor) if executor is not None else None,
            timeout_seconds=timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            persist_conversation_session=False,
        )

    def run(
        self,
        prompt: str,
        session_id: str | None = None,
        allow_side_effects: bool = True,
    ) -> OaApprovalResult:
        if session_id:
            self._store.upsert_conversation(
                self._conversation_id,
                "OA approval",
                True,
                session_id,
            )
        try:
            result = self._run_once(
                prompt,
                allow_side_effects=allow_side_effects,
            )
        except (ValueError, ValidationError) as exc:
            if allow_side_effects:
                raise RuntimeError("invalid OA approval AgentEnvelope JSON") from exc
            try:
                result = self._run_once(
                    self._repair_prompt(),
                    allow_side_effects=False,
                )
            except (ValueError, ValidationError) as second_exc:
                raise RuntimeError(
                    "invalid OA approval AgentEnvelope JSON twice"
                ) from second_exc
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
            "执行模式：可以在完整审阅并确认 taskId 后执行通过或拒绝；"
            "退回会由服务作为审批单评论提交，服务不会用拒绝冒充退回。"
            if execute
            else "只读审阅模式：不得执行通过、拒绝、退回或评论；只输出建议动作和建议留言，action_result 填 {}。"
        )
        prompt = (
            "请审阅并处理下面这条 DingTalk OA 审批消息。\n\n"
            f"{mode}\n\n"
            "如果服务侧审批详情或你执行的 DWS 命令显示 DWS 未登录、登录态失效、"
            "not_authenticated、not authenticated、exit code 2 或 tool_status=dws_login_required，"
            "这是工具问题，不是申请人没有提供材料；audit_summary 和 oa_remark 必须如实说明"
            "当前因为 DWS 登录/工具问题无法读取或判断，不要表述为申请人没有提供材料。\n\n"
            "如果服务侧审批详情包含 dws_detail_status.status=recovered_by_openapi，"
            "说明 worker 已经用 OpenAPI 读取到审批详情。必须直接使用 openapi_detail、"
            "dws_records、dws_tasks 和 oa_attachment_fallbacks 判断；不要再调用 "
            "`dws oa approval detail`，也不要尝试 `--format raw` 或 `--fields` "
            "这类 DWS detail 变体。\n\n"
            f"OA URL:\n{oa_url}\n\n"
            f"触发消息:\n{trigger_text}\n\n"
            f"服务侧已读取的审批 API 详情:\n{approval_detail_text}\n\n"
            f"会话上下文:\n{context_text}"
        )
        recovered_by_openapi = _approval_detail_recovered_by_openapi(
            approval_detail_text
        )
        result = self.run(prompt, session_id=None, allow_side_effects=execute)
        if not recovered_by_openapi:
            return result
        try:
            _validate_no_redundant_oa_detail_read(self.last_audit_tool_events)
        except RuntimeError:
            if execute:
                raise
            result = self.run(
                prompt
                + "\n\n上一次审阅已经拿到 worker 注入的 openapi_detail，"
                "但又调用了 dws oa approval detail。不要再读取 DWS detail；"
                "直接使用服务侧材料输出 AgentEnvelope JSON。",
                session_id=None,
                allow_side_effects=False,
            )
            _validate_no_redundant_oa_detail_read(self.last_audit_tool_events)
        return result

    def _run_once(self, prompt: str, *, allow_side_effects: bool) -> OaApprovalResult:
        run = self.runner.run(
            conversation_id=self._conversation_id,
            conversation_title="OA approval",
            single_chat=True,
            prompt=prompt,
            owner="oa_approval",
            allow_side_effects=allow_side_effects,
        )
        self.last_session_id = run.codex_session_id
        self.last_transcript_start_line = run.transcript_start_line
        self.last_transcript_end_line = run.transcript_end_line
        self.last_audit_tool_events = run.audit_tool_events
        if run.envelope.kind != AgentKind.OA_APPROVAL:
            raise ValidationError.from_exception_data(
                "OaApprovalResult",
                [
                    {
                        "type": "value_error",
                        "loc": ("kind",),
                        "msg": "Value error, agent envelope kind must be oa_approval",
                        "input": run.envelope.kind,
                        "ctx": {
                            "error": ValueError(
                                "agent envelope kind must be oa_approval"
                            )
                        },
                    }
                ],
            )
        return OaApprovalResult.model_validate(run.envelope.domain_payload)

    @staticmethod
    def _repair_prompt() -> str:
        return (
            "上一次输出不是合法 OA 审批 AgentEnvelope JSON。不得执行通过、拒绝、退回或评论。"
            "只输出合法 JSON，不要解释。"
            'JSON schema: {"kind":"oa_approval",'
            '"user_response":{"mode":"no_reply","text":"","sensitivity_kind":"internal_personnel"},'
            '"system_actions":[],"domain_payload":{'
            '"process_instance_id":"","task_id":"","oa_url":"",'
            '"oa_action":"通过|拒绝|退回","oa_remark":"",'
            '"action_result":{},"audit_summary":"","audit_documents":[]},'
            '"audit":{"summary":"","documents":[],"confidence":0.8}}'
            "domain_payload.action_result 必须是空对象 {}。"
            "domain_payload.oa_action 只能是 通过、拒绝、退回 之一；"
            "domain_payload.oa_remark、domain_payload.audit_summary 必须非空。"
            "如果无法取得 process_instance_id、task_id 或 oa_url，对应字段填空字符串。"
        )


class _EphemeralStructuredStore:
    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}

    @contextmanager
    def codex_session_lock(
        self,
        conversation_id: str,
        owner: str,
        stale_after_seconds: int = 600,
    ) -> Iterator[None]:
        yield

    def get_codex_session_id(self, conversation_id: str) -> str | None:
        return self._sessions.get(conversation_id)

    def clear_codex_session(self, conversation_id: str) -> None:
        self._sessions.pop(conversation_id, None)

    def upsert_conversation(
        self,
        conversation_id: str,
        title: str,
        single_chat: bool,
        codex_session_id: str | None = None,
    ) -> None:
        if codex_session_id:
            self._sessions[conversation_id] = codex_session_id


def _adapt_executor(
    executor: Callable[[list[str], str], str],
) -> Callable[[list[str], str, dict[str, str]], str]:
    def wrapped(command: list[str], prompt: str, env: dict[str, str]) -> str:
        return executor(command, prompt)

    return wrapped


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


def _approval_detail_recovered_by_openapi(approval_detail_text: str) -> bool:
    try:
        documents = json.loads(approval_detail_text)
    except json.JSONDecodeError:
        return False
    if not isinstance(documents, dict):
        return False
    status = documents.get("dws_detail_status")
    return isinstance(status, dict) and status.get("status") == "recovered_by_openapi"


def _validate_no_redundant_oa_detail_read(
    audit_tool_events: list[dict[str, str]],
) -> None:
    for event in audit_tool_events:
        command = str(event.get("command") or event.get("cmd") or "")
        if OA_DETAIL_COMMAND_PATTERN.search(command):
            raise RuntimeError(
                "OA detail already recovered by OpenAPI; agent must not call "
                "dws oa approval detail again"
            )


def parse_oa_approval_json(raw: str, *, allow_legacy: bool = True) -> OaApprovalResult:
    for payload in reversed(_iter_json_payloads(raw)):
        result = _result_from_payload(payload, allow_legacy=allow_legacy)
        if result is not None:
            return result
    message = (
        "No OA approval AgentEnvelope JSON found"
        if not allow_legacy
        else "No OA approval result JSON found"
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


def _result_from_payload(
    payload: Any,
    *,
    allow_legacy: bool = True,
) -> OaApprovalResult | None:
    if not isinstance(payload, dict):
        return None
    result = _result_from_agent_envelope_payload(payload)
    if result is not None:
        return result
    if allow_legacy:
        try:
            return OaApprovalResult.model_validate(payload)
        except ValidationError:
            pass
    for text in _result_text_candidates(payload):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        result = _result_from_agent_envelope_payload(parsed)
        if result is not None:
            return result
        if not allow_legacy:
            continue
        try:
            return OaApprovalResult.model_validate(parsed)
        except ValidationError:
            continue
    return None


def _result_from_agent_envelope_payload(payload: Any) -> OaApprovalResult | None:
    if not isinstance(payload, dict):
        return None
    if "kind" not in payload or "user_response" not in payload:
        return None
    envelope = AgentEnvelope.model_validate(payload)
    if envelope.kind != AgentKind.OA_APPROVAL:
        raise ValidationError.from_exception_data(
            "OaApprovalResult",
            [
                {
                    "type": "value_error",
                    "loc": ("kind",),
                    "msg": "Value error, agent envelope kind must be oa_approval",
                    "input": envelope.kind,
                    "ctx": {
                        "error": ValueError(
                            "agent envelope kind must be oa_approval"
                        )
                    },
                }
            ],
        )
    return OaApprovalResult.model_validate(envelope.domain_payload)


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
