from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dingtalk_models import SensitivityKind


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
            for field_name in ("mailbox", "message_id"):
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
