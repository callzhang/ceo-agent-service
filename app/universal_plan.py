from enum import StrEnum
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dingtalk_models import SensitivityKind
from app.leak_check import contains_credential, contains_local_runtime_leak

MAX_MEMORY_WRITE_DATA_LENGTH = 2_000
MAX_MEMORY_WRITE_LINES = 12

_MEMORY_STACK_OR_EXCEPTION_PATTERNS = (
    re.compile(r"traceback\s*\(most recent call last\)", re.IGNORECASE),
    re.compile(
        r"\b[A-Za-z_][\w.]*(?:Error|Exception)\s*:",
        re.IGNORECASE,
    ),
    re.compile(r"\bstack\s+trace\s*:", re.IGNORECASE),
    re.compile(r'^\s*file\s+"[^"]+",\s+line\s+\d+', re.IGNORECASE | re.MULTILINE),
)
_MEMORY_RAW_LOG_LINE_PATTERN = re.compile(
    r"^\s*\[?(?:\d{4}-\d{2}-\d{2}[T ][0-9:.+Z-]+|\d{2}:\d{2}:\d{2})\]?"
    r"\s+(?:DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|TRACE)\b",
    re.IGNORECASE | re.MULTILINE,
)
_MEMORY_TRANSIENT_STATE_PATTERNS = (
    re.compile(
        r"^\s*(?:status\s*[:=]\s*)?"
        r"(?:pending|processing|temporary|failed|retrying|unavailable)\s*[.!。]?$",
        re.IGNORECASE,
    ),
    re.compile(r"(?:一次性错误|临时错误|处理中|等待处理|待处理状态)"),
)
_MEMORY_RUNTIME_FAILURE_PATTERN = re.compile(
    r"\b(?:failed|retrying|unavailable|connection\s+reset|timed?\s*out|timeout)\b",
    re.IGNORECASE,
)
_MEMORY_DURABLE_FRAMING_PATTERN = re.compile(
    r"\b(?:prefers?|preference|decision|policy|rule|strategy|design|standard)\b"
    r".{0,120}\b(?:handle|handling|retry|retries|use|uses|require|requires|must|should|backoff)\b",
    re.IGNORECASE,
)


def _contains_forbidden_memory_content(data: str) -> bool:
    if contains_credential(data) or contains_local_runtime_leak(data):
        return True
    if _MEMORY_RAW_LOG_LINE_PATTERN.search(data):
        return True
    if any(pattern.search(data) for pattern in _MEMORY_STACK_OR_EXCEPTION_PATTERNS):
        return True
    if any(pattern.search(data) for pattern in _MEMORY_TRANSIENT_STATE_PATTERNS):
        return True
    return bool(
        _MEMORY_RUNTIME_FAILURE_PATTERN.search(data)
        and not _MEMORY_DURABLE_FRAMING_PATTERN.search(data)
    )


def _contains_secret_shaped_token(data: str) -> bool:
    for token in data.split():
        if len(token) < 48 or not token.isascii():
            continue
        character_classes = sum(
            (
                any(character.islower() for character in token),
                any(character.isupper() for character in token),
                any(character.isdigit() for character in token),
            )
        )
        if character_classes < 3:
            continue
        frequencies = {character: token.count(character) for character in set(token)}
        entropy = -sum(
            (count / len(token)) * math.log2(count / len(token))
            for count in frequencies.values()
        )
        if entropy >= 4.0:
            return True
    return False


class DependencyName(StrEnum):
    DWS = "dws"
    LARK = "lark"
    EXA = "exa"
    MEMORY = "memory"
    XIAOQING_INTERVIEW = "xiaoqing_interview"
    MAIL = "mail"
    CALENDAR = "calendar"


class PlannedActionKind(StrEnum):
    SEND_REPLY = "send_reply"
    ASK_CLARIFYING_QUESTION = "ask_clarifying_question"
    OA_APPROVAL = "oa_approval"
    MAIL_REPLY = "mail_reply"
    CALENDAR_RESPONSE = "calendar_response"
    DWS_MARKDOWN_DOCUMENT_REPLY = "dws_markdown_document_reply"
    DWS_MESSAGE_REACTION = "dws_message_reaction"
    QUEUE_OKR_REVIEW = "queue_okr_review"
    MEMORY_WRITE = "memory_write"
    NO_REPLY = "no_reply"
    HANDOFF_TO_HUMAN = "handoff_to_human"
    BLOCKED = "blocked"
    STOP_WITH_ERROR = "stop_with_error"


class UniversalPlanBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _non_empty(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be non-empty after trimming")
    return value.strip()


class UniversalAudit(UniversalPlanBase):
    summary: str
    documents: list[dict[str, str]] = Field(default_factory=list)
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    _summary_non_empty = field_validator("summary")(_non_empty)


class PlannedAction(UniversalPlanBase):
    kind: PlannedActionKind
    reason: str
    sensitivity_kind: SensitivityKind | None = None
    personnel_subject_user_id: str | None = None
    candidate_context_known: bool = False
    candidate_department_ids: list[str] = Field(default_factory=list)
    target: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)

    _reason_non_empty = field_validator("reason")(_non_empty)

    @model_validator(mode="after")
    def validate_payload(self) -> "PlannedAction":
        if self.kind in {
            PlannedActionKind.SEND_REPLY,
            PlannedActionKind.ASK_CLARIFYING_QUESTION,
        }:
            if self.sensitivity_kind is None:
                raise ValueError(
                    f"{self.kind.value} sensitivity_kind is required"
                )
            text = self.payload.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(
                    f"{self.kind.value} payload.text must be non-empty"
                )

        if self.kind is PlannedActionKind.MAIL_REPLY:
            for field_name in ("mailbox", "message_id", "subject"):
                value = self.target.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"mail_reply requires target.{field_name}"
                    )
            content = self.payload.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("mail_reply requires payload.content")

        if self.kind is PlannedActionKind.OA_APPROVAL:
            if self.payload.get("action") not in {"同意", "拒绝", "退回", "comment"}:
                raise ValueError(
                    "oa_approval payload.action must be one of 同意/拒绝/退回/comment"
                )
            remark = self.payload.get("remark")
            if not isinstance(remark, str) or not remark.strip():
                raise ValueError("oa_approval payload.remark must be non-empty")
            if self.payload.get("action") == "退回":
                target_activity_id = self.payload.get("target_activity_id")
                if not isinstance(target_activity_id, str) or not target_activity_id.strip():
                    raise ValueError(
                        "oa_approval return payload.target_activity_id must be non-empty"
                    )
                if self.payload.get("revert_action") not in {
                    "REVERT_FOR_APPROVAL",
                    "REVERT_FOR_RESUBMIT",
                }:
                    raise ValueError(
                        "oa_approval return payload.revert_action must be "
                        "REVERT_FOR_APPROVAL or REVERT_FOR_RESUBMIT"
                    )

        if self.kind is PlannedActionKind.CALENDAR_RESPONSE:
            event_id = self.target.get("event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError("calendar_response requires target.event_id")
            if self.payload.get("response_status") not in {
                "accepted",
                "tentative",
                "declined",
            }:
                raise ValueError(
                    "calendar_response payload.response_status must be one of "
                    "accepted/tentative/declined"
                )

        if self.kind is PlannedActionKind.MEMORY_WRITE:
            if set(self.payload) != {"data", "type"}:
                raise ValueError(
                    "memory_write payload must contain only data and type"
                )
            data = self.payload.get("data")
            if not isinstance(data, str) or not data.strip():
                raise ValueError("memory_write payload.data must be non-empty")
            data = data.strip()
            if len(data) > MAX_MEMORY_WRITE_DATA_LENGTH:
                raise ValueError("memory_write payload.data is too long")
            if len(data.splitlines()) > MAX_MEMORY_WRITE_LINES:
                raise ValueError("memory_write payload.data resembles raw logs")
            if _contains_forbidden_memory_content(
                data
            ) or _contains_secret_shaped_token(data):
                raise ValueError("memory_write payload.data contains sensitive data")
            if self.payload.get("type") not in {"text", "message"}:
                raise ValueError(
                    "memory_write payload.type must be text or message"
                )
            self.payload = {"data": data, "type": self.payload["type"]}

        if self.kind is PlannedActionKind.DWS_MARKDOWN_DOCUMENT_REPLY:
            for field_name in ("conversation_id", "trigger_message_id"):
                value = self.target.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        "dws_markdown_document_reply requires "
                        f"target.{field_name}"
                    )
            text = self.payload.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(
                    "dws_markdown_document_reply requires payload.text"
                )

        if self.kind is PlannedActionKind.DWS_MESSAGE_REACTION:
            for field_name in ("conversation_id", "message_id"):
                value = self.target.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"dws_message_reaction requires target.{field_name}"
                    )
            reaction_type = self.payload.get("reaction_type", "emoji")
            if reaction_type not in {"emoji", "text_emotion"}:
                raise ValueError(
                    "dws_message_reaction payload.reaction_type must be "
                    "emoji or text_emotion"
                )
            field_name = "emoji" if reaction_type == "emoji" else "text"
            value = self.payload.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"dws_message_reaction requires payload.{field_name}"
                )

        if self.kind is PlannedActionKind.QUEUE_OKR_REVIEW:
            for field_name in ("conversation_id", "trigger_message_id"):
                value = self.target.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"queue_okr_review requires target.{field_name}"
                    )
            if self.payload:
                raise ValueError("queue_okr_review payload must be empty")

        return self


class UniversalPlan(UniversalPlanBase):
    planner_version: Literal["2026-07-20"] = "2026-07-20"
    task_kind: str
    reason: str
    dependencies: list[DependencyName] = Field(default_factory=list)
    actions: list[PlannedAction] = Field(min_length=1)
    audit: UniversalAudit

    _task_kind_non_empty = field_validator("task_kind")(_non_empty)
    _reason_non_empty = field_validator("reason")(_non_empty)

    def execution_dependencies(self) -> tuple[str, ...]:
        return ()


def with_context_action_targets(
    plan: UniversalPlan,
    *,
    conversation_id: str,
    trigger_message_id: str,
) -> UniversalPlan:
    context_target = {
        "conversation_id": conversation_id,
        "trigger_message_id": trigger_message_id,
    }
    normalized_actions: list[PlannedAction] = []
    changed = False
    for action in plan.actions:
        if action.kind not in {
            PlannedActionKind.SEND_REPLY,
            PlannedActionKind.ASK_CLARIFYING_QUESTION,
            PlannedActionKind.QUEUE_OKR_REVIEW,
        }:
            normalized_actions.append(action)
            continue

        target = dict(action.target)
        for field_name, field_value in context_target.items():
            if not target.get(field_name):
                target[field_name] = field_value
                changed = True
        normalized_actions.append(action.model_copy(update={"target": target}))

    if not changed:
        return plan
    return plan.model_copy(update={"actions": normalized_actions})
