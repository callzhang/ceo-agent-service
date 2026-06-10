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

    @model_validator(mode="before")
    @classmethod
    def normalize_documents(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        documents = data.get("documents")
        if not isinstance(documents, list):
            return data
        normalized = []
        changed = False
        for document in documents:
            if isinstance(document, str):
                normalized.append(
                    {"title": document, "url": "", "relevance": "mentioned"}
                )
                changed = True
            elif isinstance(document, dict) and "title" not in document:
                name = document.get("name")
                if isinstance(name, str):
                    status = document.get("status")
                    summary = document.get("summary")
                    normalized.append(
                        {
                            "title": name,
                            "url": str(document.get("url") or ""),
                            "relevance": str(summary or status or "mentioned"),
                        }
                    )
                    changed = True
                else:
                    normalized.append(document)
            else:
                normalized.append(document)
        if not changed:
            return data
        return {**data, "documents": normalized}


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


class DwsMessageReactionAction(StrictBaseModel):
    type: Literal["dws_message_reaction"]
    reaction_type: Literal["emoji", "text_emotion"] = "emoji"
    emoji: str = ""
    text: str = ""
    emotion_id: str = ""
    emotion_name: str = ""
    background_id: str = ""

    @model_validator(mode="after")
    def validate_reaction_payload(self) -> "DwsMessageReactionAction":
        if self.reaction_type == "emoji":
            if not self.emoji.strip():
                raise ValueError("emoji reaction requires emoji")
            return self
        missing = [field for field in ("text",) if not getattr(self, field).strip()]
        if missing:
            raise ValueError(
                "text emotion reaction requires " + ", ".join(missing)
            )
        return self


SystemAction = Annotated[
    Union[
        SendDingTalkReplyAction,
        DwsOaApprovalAction,
        DwsOaApprovalCommentAction,
        PersistOkrReviewAction,
        DwsMessageReactionAction,
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
            if any(
                isinstance(action, DwsMessageReactionAction)
                for action in self.system_actions
            ) and self.user_response.mode != UserResponseMode.NO_REPLY:
                raise ValueError(
                    "dws_message_reaction requires user_response.mode=no_reply"
                )
            return self
        if self.user_response.mode not in {
            UserResponseMode.SEND_REPLY,
            UserResponseMode.ASK_CLARIFYING_QUESTION,
        }:
            raise ValueError(
                "send_dingtalk_reply requires user_response.mode=send_reply or ask_clarifying_question"
            )
        if not self.user_response.text.strip():
            raise ValueError(
                "send_dingtalk_reply requires non-empty user_response.text"
            )
        return self
