from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentKind(StrEnum):
    REPLY = "reply"
    OA_APPROVAL = "oa_approval"
    OKR_REVIEW = "okr_review"
    NO_ACTION = "no_action"
    ERROR = "error"


class UserResponseMode(StrEnum):
    SEND_REPLY = "send_reply"
    ASK_CLARIFYING_QUESTION = "ask_clarifying_question"
    HANDOFF_TO_HUMAN = "handoff_to_human"
    NO_REPLY = "no_reply"


class AgentSensitivityKind(StrEnum):
    GENERAL = "general"
    INTERNAL_PERSONNEL = "internal_personnel"
    EXTERNAL_CANDIDATE = "external_candidate"


class UserResponse(StrictBaseModel):
    mode: UserResponseMode
    text: str
    sensitivity_kind: AgentSensitivityKind


class AgentAuditDocument(StrictBaseModel):
    title: str
    url: str
    relevance: str


class AgentAudit(StrictBaseModel):
    summary: str = Field(min_length=1)
    documents: list[AgentAuditDocument]
    confidence: float = Field(ge=0, le=1)


class SendDingTalkReplyAction(StrictBaseModel):
    type: Literal["send_dingtalk_reply"]
    reply_text_ref: Literal["user_response.text"]


class DwsOaApprovalAction(StrictBaseModel):
    type: Literal["dws_oa_approval_action"]
    process_instance_id: str
    task_id: str
    action: Literal["通过", "拒绝"]
    remark: str = Field(min_length=1)


class DwsOaApprovalCommentAction(StrictBaseModel):
    type: Literal["dws_oa_approval_comment"]
    process_instance_id: str
    text: str = Field(min_length=1)


class PersistOkrReviewAction(StrictBaseModel):
    type: Literal["persist_okr_review"]
    request_id: int


SystemAction = Annotated[
    Union[
        SendDingTalkReplyAction,
        DwsOaApprovalAction,
        DwsOaApprovalCommentAction,
        PersistOkrReviewAction,
    ],
    Field(discriminator="type"),
]


class AgentEnvelope(StrictBaseModel):
    kind: AgentKind
    user_response: UserResponse
    system_actions: list[SystemAction]
    domain_payload: dict[str, Any]
    audit: AgentAudit

    @model_validator(mode="after")
    def validate_system_actions_match_response(self) -> "AgentEnvelope":
        if (
            self.kind in {AgentKind.NO_ACTION, AgentKind.ERROR}
            and self.user_response.mode != UserResponseMode.NO_REPLY
        ):
            raise ValueError(f"{self.kind.value} requires user_response.mode=no_reply")
        has_reply_action = any(
            isinstance(action, SendDingTalkReplyAction)
            for action in self.system_actions
        )
        if not has_reply_action:
            return self
        if self.user_response.mode != UserResponseMode.SEND_REPLY:
            raise ValueError(
                "send_dingtalk_reply requires user_response.mode=send_reply"
            )
        if not self.user_response.text.strip():
            raise ValueError(
                "send_dingtalk_reply requires non-empty user_response.text"
            )
        return self
