from enum import StrEnum

from pydantic import BaseModel, Field

from app.config import broadcast_mention_aliases, mention_aliases


class DingTalkConversation(BaseModel):
    open_conversation_id: str
    title: str
    single_chat: bool
    unread_point: int
    notification_off: bool = False
    last_message_create_at: int | None = None
    direct_user_id: str = ""
    direct_open_dingtalk_id: str = ""


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
    raw_payload: dict = Field(default_factory=dict)

    def mentions_principal(self) -> bool:
        return self._contains_mention_alias(mention_aliases())

    def mentions_all(self) -> bool:
        return self._contains_mention_alias(broadcast_mention_aliases())

    def addresses_principal(self) -> bool:
        return self.mentions_principal() or self.mentions_all()

    def is_recalled(self) -> bool:
        return self._raw_payload_state("messageStatus") in {
            "recall",
            "recalled",
        } or self._raw_payload_state("status") in {
            "recall",
            "recalled",
        }

    def _contains_mention_alias(self, aliases: tuple[str, ...]) -> bool:
        content = self.content.casefold()
        return any(alias.casefold() in content for alias in aliases)

    def _raw_payload_state(self, key: str) -> str:
        value = self.raw_payload.get(key)
        if value is None:
            return ""
        return str(value).strip().casefold()


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


class CalendarResponseStatus(StrEnum):
    NONE = ""
    ACCEPTED = "accepted"
    TENTATIVE = "tentative"
    DECLINED = "declined"


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
    calendar_response_status: CalendarResponseStatus = CalendarResponseStatus.NONE
    system_actions: list[dict] = Field(default_factory=list)
    audit_documents: list[dict[str, str]] = Field(default_factory=list)
    audit_summary: str = ""
