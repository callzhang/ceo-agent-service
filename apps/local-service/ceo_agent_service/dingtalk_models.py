from enum import StrEnum

from pydantic import BaseModel, Field

from ceo_agent_service.config import mention_aliases


class DingTalkConversation(BaseModel):
    open_conversation_id: str
    title: str
    single_chat: bool
    unread_point: int
    notification_off: bool = False
    last_message_create_at: int | None = None


class DingTalkMessage(BaseModel):
    open_conversation_id: str
    open_message_id: str
    conversation_title: str
    single_chat: bool
    sender_name: str
    sender_open_dingtalk_id: str | None = None
    sender_user_id: str | None = None
    message_type: str | None = None
    create_time: str
    content: str
    mentioned_user_ids: list[str] = Field(default_factory=list)
    quoted_message_id: str | None = None
    quoted_content: str | None = None

    def mentions_derek(self) -> bool:
        return any(mention in self.content for mention in mention_aliases())


class CodexAction(StrEnum):
    SEND_REPLY = "send_reply"
    ASK_CLARIFYING_QUESTION = "ask_clarifying_question"
    HANDOFF_TO_HUMAN = "handoff_to_human"
    NO_REPLY = "no_reply"
    STOP_WITH_ERROR = "stop_with_error"


class SensitivityKind(StrEnum):
    GENERAL = "general"
    INTERNAL_PERSONNEL = "internal_personnel"
    EXTERNAL_CANDIDATE = "external_candidate"


class CodexDecision(BaseModel):
    action: CodexAction
    reply_text: str = ""
    reason: str = ""
    ding_self: bool = False
    macos_notify: bool = True
    sensitivity_kind: SensitivityKind = SensitivityKind.GENERAL
    personnel_subject_user_id: str | None = None
    candidate_context_known: bool = False
    candidate_department_ids: list[str] = []
    audit_documents: list[dict[str, str]] = Field(default_factory=list)
    audit_summary: str = ""
