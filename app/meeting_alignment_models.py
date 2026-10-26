import json
import hashlib
from typing import Any, Literal, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.decision_quality import (
    DecisionQualityResult,
    DecisionRisk,
    classify_decision_quality,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CrossFieldRule(NamedTuple):
    """One cross-field validator rule: what it raises and the shape that satisfies it."""

    message: str
    requirement: str


# Every cross-field rule below is raised by a validator in this module and is
# rendered into the derived JSON schema and both prompts. The validators raise
# `<rule>.message` rather than a literal, so the contract the model reads and
# the error it gets back can never drift apart.
ALIGNED_TOPIC_RESULT_RULE = CrossFieldRule(
    "aligned topic requires conclusion and alignment_reason",
    "topics[].state=aligned 时，同一条 topic 的 conclusion 与 alignment_reason 都必须非空；"
    "state=unresolved 时两者写空字符串",
)
PRIVATE_MESSAGE_DIRECT_TARGET_RULE = CrossFieldRule(
    "sensitive private message requires a direct target",
    "sensitive_private_message.target.kind 必须是 direct",
)
PRIVATE_MESSAGE_USER_ID_RULE = CrossFieldRule(
    "sensitive private message requires a stable direct user id",
    "sensitive_private_message.target.direct_user_id 必须是非空的稳定 user_id，"
    "写收件人本人在名册里的 user_id，不能留空",
)
PRIVATE_MESSAGE_EVIDENCE_RULE = CrossFieldRule(
    "sensitive private message requires non-empty recipient evidence",
    "sensitive_private_message.recipient_evidence 至少一项，且每一项都非空",
)
ALIGNED_TRIGGER_NEEDS_TOPIC_RULE = CrossFieldRule(
    "aligned_disagreement requires an aligned topic",
    "trigger_reasons 含 aligned_disagreement 时，topics 中至少一条 state=aligned",
)
ALIGNED_TOPIC_NEEDS_TRIGGER_RULE = CrossFieldRule(
    "aligned topic requires aligned_disagreement trigger",
    "topics 中出现 state=aligned 时，trigger_reasons 必须含 aligned_disagreement；"
    "与上一条互为充要，两者要么同时成立要么同时不成立",
)
UNRESOLVED_TRIGGER_NEEDS_TOPIC_RULE = CrossFieldRule(
    "unresolved_disagreement requires an unresolved topic",
    "trigger_reasons 含 unresolved_disagreement 时，topics 中至少一条 state=unresolved",
)
UNRESOLVED_TRIGGER_NEEDS_QUESTIONS_RULE = CrossFieldRule(
    "unresolved_disagreement requires key_questions",
    "trigger_reasons 含 unresolved_disagreement 时，key_questions 至少一条",
)
UNRESOLVED_TOPIC_NEEDS_TRIGGER_RULE = CrossFieldRule(
    "unresolved topic requires unresolved_disagreement trigger",
    "topics 中出现 state=unresolved 时，trigger_reasons 必须含 unresolved_disagreement；"
    "与上一条互为充要，两者要么同时成立要么同时不成立",
)
DEREK_VIEWPOINT_PAIRING_RULE = CrossFieldRule(
    "derek_viewpoint trigger and payload must appear together",
    "trigger_reasons 含 derek_viewpoint 与 derek_viewpoint 为对象必须同时成立；"
    "不含该 trigger 时 derek_viewpoint 必须是 null",
)
SEND_FINAL_MESSAGE_RULE = CrossFieldRule(
    "send requires final_message",
    "final_message 必须非空",
)
SEND_TRIGGER_REASONS_RULE = CrossFieldRule(
    "send requires trigger_reasons",
    "trigger_reasons 至少一项",
)
SEND_TARGET_RULE = CrossFieldRule(
    "send requires an explicit delivery target",
    "target 必须是对象，不能是 null",
)
GROUP_TARGET_FIELDS_RULE = CrossFieldRule(
    "group target requires candidates and conversation_id",
    "target.kind=group 时，conversation_id 非空且 candidates 至少一条",
)
GROUP_TARGET_NO_DIRECT_USER_RULE = CrossFieldRule(
    "group target cannot contain direct_user_id",
    "target.kind=group 时，direct_user_id 这个键仍然必填，但值必须是空字符串 \"\"",
)
GROUP_TARGET_FIRST_CANDIDATE_RULE = CrossFieldRule(
    "group target must select the first ranked candidate",
    "target.kind=group 时，conversation_id 必须等于 candidates[0].conversation_id",
)
DIRECT_TARGET_NO_GROUP_FIELDS_RULE = CrossFieldRule(
    "direct target cannot contain group delivery fields",
    "target.kind=direct 时，conversation_id 与 candidates 两个键仍然必填，"
    "但值必须分别是空字符串 \"\" 与空数组 []",
)
DIRECT_TARGET_TITLE_RULE = CrossFieldRule(
    "direct target requires title",
    "target.kind=direct 时，title 必须非空",
)
PRIVATE_MESSAGE_BUSINESS_SCOPE_RULE = CrossFieldRule(
    "sensitive private message is only valid beside a business summary",
    "sensitive_private_message 非 null 时，audience_scope 必须是 business",
)
MEETING_AUDIT_FEEDBACK_RULE = CrossFieldRule(
    "meeting Audit return requires actionable feedback",
    "Meeting Audit outcome=return 时必须返回 rule、observation、requested_revision；"
    "approve/reject 时 feedback 必须为 null",
)

MEETING_ALIGNMENT_CROSS_FIELD_RULES: tuple[CrossFieldRule, ...] = (
    ALIGNED_TOPIC_RESULT_RULE,
    ALIGNED_TRIGGER_NEEDS_TOPIC_RULE,
    ALIGNED_TOPIC_NEEDS_TRIGGER_RULE,
    UNRESOLVED_TRIGGER_NEEDS_TOPIC_RULE,
    UNRESOLVED_TOPIC_NEEDS_TRIGGER_RULE,
    UNRESOLVED_TRIGGER_NEEDS_QUESTIONS_RULE,
    DEREK_VIEWPOINT_PAIRING_RULE,
    SEND_TRIGGER_REASONS_RULE,
    SEND_FINAL_MESSAGE_RULE,
    SEND_TARGET_RULE,
    GROUP_TARGET_FIELDS_RULE,
    GROUP_TARGET_FIRST_CANDIDATE_RULE,
    GROUP_TARGET_NO_DIRECT_USER_RULE,
    DIRECT_TARGET_NO_GROUP_FIELDS_RULE,
    DIRECT_TARGET_TITLE_RULE,
    PRIVATE_MESSAGE_BUSINESS_SCOPE_RULE,
    PRIVATE_MESSAGE_DIRECT_TARGET_RULE,
    PRIVATE_MESSAGE_USER_ID_RULE,
    PRIVATE_MESSAGE_EVIDENCE_RULE,
)


def render_meeting_alignment_cross_field_rules() -> str:
    """Render every cross-field rule the validators enforce, one line per rule."""
    lines = [
        "跨字段规则（由 MeetingAlignmentDecision 及其子模型的校验器生成，"
        "违反任意一条整轮判定失败）："
    ]
    lines.extend(
        f"- {rule.message}：{rule.requirement}"
        for rule in MEETING_ALIGNMENT_CROSS_FIELD_RULES
    )
    return "\n".join(lines)


def meeting_alignment_rule_for_message(problem: str) -> CrossFieldRule | None:
    """Find the rule a reported schema problem broke, matching the raised message.

    Pydantic renders a model validator failure as `Value error, <message>` and the
    caller may prefix a field path, so the raised message is a suffix of the report.
    """
    for rule in MEETING_ALIGNMENT_CROSS_FIELD_RULES:
        if problem.endswith(rule.message):
            return rule
    return None


class MeetingParticipant(StrictModel):
    name: str
    user_id: str
    open_dingtalk_id: str = ""


class TranscriptLine(StrictModel):
    speaker_name: str
    speaker_user_id: str = ""
    timestamp: str = ""
    text: str


class MeetingSource(StrictModel):
    meeting_id: str
    title: str
    status: Literal["ended"]
    started_at: str
    ended_at: str
    participants: list[MeetingParticipant]
    attendee_evidence: Literal["calendar", "transcript"]
    attendee_roster_complete: bool
    creator: MeetingParticipant | None = None
    current_user_id: str
    summary: str
    transcript: list[TranscriptLine]
    source_url: str = ""
    # Every recording this meeting was assembled from, in order, the first being
    # `meeting_id`. More than one means the recorder was stopped and restarted
    # during the meeting, and the summary covers all of the parts.
    segment_ids: list[str] = Field(default_factory=list)
    # Set when this meeting was already followed up and a later recording arrived,
    # so the summary is being produced again over the whole meeting.
    resummary: bool = False


class AlignmentView(StrictModel):
    speaker: str
    view: str
    reason: str


class AlignmentTopic(StrictModel):
    title: str
    state: Literal["aligned", "unresolved"] = Field(
        description=(
            "aligned 还要求外层 trigger_reasons 含 aligned_disagreement，"
            "unresolved 要求外层含 unresolved_disagreement。"
        )
    )
    views: list[AlignmentView]
    conclusion: str = Field(
        description="state=aligned 时必须非空；state=unresolved 时写空字符串。"
    )
    alignment_reason: str = Field(
        description="state=aligned 时必须非空；state=unresolved 时写空字符串。"
    )

    @model_validator(mode="after")
    def validate_aligned_result(self) -> Self:
        if self.state == "aligned" and (
            not self.conclusion.strip() or not self.alignment_reason.strip()
        ):
            raise ValueError(ALIGNED_TOPIC_RESULT_RULE.message)
        return self


class DerekViewpoint(StrictModel):
    expressed_view: str
    meeting_evidence: list[str]
    omitted_layer: str
    plain_explanation: str
    analogy: str
    example: str
    historical_sources: list[str]


class KeyQuestion(StrictModel):
    question: str
    answer_owner_names: list[str]


class TargetCandidate(StrictModel):
    conversation_id: str
    title: str
    evidence: list[str]


class DeliveryTarget(StrictModel):
    # This shape is used in two slots whose rules differ in what they allow, not
    # in what they mean: MeetingAlignmentDecision.target may be group or direct,
    # sensitive_private_message.target must be direct. Each field states the part
    # that always holds and points at the containing object for the rest.
    kind: Literal["group", "direct"] = Field(
        description=(
            "group 与 direct 各自的必填/必空字段由所在对象的 description 规定；"
            "sensitive_private_message.target 只允许 direct。"
        )
    )
    conversation_id: str = Field(
        description="群会话 id；direct 形态必须写空字符串。"
    )
    direct_user_id: str = Field(
        description=(
            "收件人的稳定 user_id；group 形态必须写空字符串，direct 形态写对方在"
            "来源里的真实 user_id；业务私信必须非空，且不得是当前用户或其已核验别名；"
            "只有个人完整日历 1:1 名册解析不出 user_id 时才写空字符串。"
        )
    )
    title: str = Field(description="群名或收件人姓名。")
    candidates: list[TargetCandidate] = Field(
        description="按业务承接证据排序的群候选；direct 形态必须写空数组。"
    )


class SensitivePrivateMessage(StrictModel):
    # Deliberately carries no description. This field's type is a single model,
    # so pydantic renders a description beside the `$ref`, and OpenAI's
    # structured-output validator rejects the whole request for it: "$ref
    # cannot have keywords {'description'}". Every meeting alignment turn then
    # fails before the model reads a word of the prompt. What the description
    # said is already in MEETING_ALIGNMENT_CROSS_FIELD_RULES, which both
    # prompts print in full, and is enforced below by validate_private_target.
    target: DeliveryTarget
    message: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    recipient_evidence: list[str] = Field(
        description="至少一项且每一项非空，写清实时身份或职责依据。"
    )

    @model_validator(mode="after")
    def validate_private_target(self) -> Self:
        if self.target.kind != "direct":
            raise ValueError(PRIVATE_MESSAGE_DIRECT_TARGET_RULE.message)
        if not self.target.direct_user_id.strip():
            raise ValueError(PRIVATE_MESSAGE_USER_ID_RULE.message)
        if not self.recipient_evidence or any(
            not evidence.strip() for evidence in self.recipient_evidence
        ):
            raise ValueError(PRIVATE_MESSAGE_EVIDENCE_RULE.message)
        return self


def meeting_business_direct_recipient(
    source: MeetingSource,
    target: DeliveryTarget,
    *,
    participant_only: bool = False,
) -> MeetingParticipant:
    """Resolve an explicit audience without directory search or self fallback."""
    user_id = target.direct_user_id.strip()
    if not source.current_user_id.strip():
        raise ValueError("principal identity is required before private delivery")
    if user_id == source.current_user_id.strip():
        raise ValueError("private target must not be the principal")
    if target.kind != "direct" or not user_id:
        raise ValueError("business direct target requires an explicit stable user_id")
    matches = [
        person for person in source.participants if person.user_id.strip() == user_id
    ]
    if not participant_only and not matches and source.attendee_evidence == "calendar":
        organizer = source.creator
        if organizer is not None and organizer.user_id.strip() == user_id:
            matches = [organizer]
    if len(matches) != 1:
        raise ValueError(
            "private target must identify one meeting participant or verified calendar organizer"
        )
    recipient = matches[0]
    if " ".join(target.title.split()).casefold() != " ".join(
        recipient.name.split()
    ).casefold():
        raise ValueError("private target title must match the stable source recipient")
    principal = [
        person for person in source.participants
        if person.user_id.strip() == source.current_user_id.strip()
    ]
    if recipient.open_dingtalk_id.strip() and any(
        person.open_dingtalk_id.strip() == recipient.open_dingtalk_id.strip()
        for person in principal
    ):
        raise ValueError("private target must not be a verified alias of the principal")
    return recipient


def _decision_schema_extra(schema: dict[str, Any]) -> None:
    schema["description"] = render_meeting_alignment_cross_field_rules()
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        # OpenAI strict structured outputs require every declared property to
        # appear in `required`. Pydantic defaults remain useful to internal and
        # persisted legacy callers, but the provider must emit these fields.
        schema["required"] = list(properties)
        for property_schema in properties.values():
            if isinstance(property_schema, dict):
                property_schema.pop("default", None)

    # A description beside a $ref is rejected by the provider schema checker.
    risk_schema = properties.get("risk") if isinstance(properties, dict) else None
    if isinstance(risk_schema, dict):
        risk_schema.pop("description", None)


class MeetingAlignmentDecision(StrictModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra=_decision_schema_extra
    )

    action: Literal["send"]
    audience_scope: Literal["business", "personal"] = Field(
        description=(
            "business=业务承接群总结；personal=整场均为个人事项的完整日历 1:1。"
            "sensitive_private_message 非 null 时必须是 business。"
        )
    )
    trigger_reasons: list[
        Literal[
            "aligned_disagreement",
            "unresolved_disagreement",
            "derek_viewpoint",
            "meeting_summary",
        ]
    ] = Field(
        description=(
            "至少一项。与 topics 的 state 互为充要：出现 state=aligned 必须含 "
            "aligned_disagreement，出现 state=unresolved 必须含 unresolved_disagreement "
            "且 key_questions 非空；含 derek_viewpoint 时 derek_viewpoint 必须是对象。"
        )
    )
    topics: list[AlignmentTopic] = Field(
        description=(
            "每条 topic 的 state 反过来约束 trigger_reasons：aligned 需要 "
            "aligned_disagreement，unresolved 需要 unresolved_disagreement。"
        )
    )
    derek_viewpoint: DerekViewpoint | None = Field(
        description=(
            "与 trigger_reasons 中的 derek_viewpoint 同现同缺：含该 trigger 时必须是对象，"
            "不含时必须是 null。"
        )
    )
    key_questions: list[KeyQuestion] = Field(
        description=(
            "trigger_reasons 含 unresolved_disagreement 时至少一条；否则可以是空数组。"
        )
    )
    mention_names: list[str]
    target: DeliveryTarget | None = Field(
        description=(
            "必填且不能为 null。group 与 direct 两种形态的字段互斥，但 kind、"
            "conversation_id、direct_user_id、title、candidates 五个键在任何形态下都必须"
            "出现，未使用的一侧要显式写成空值而不是省略："
            "audience_scope=business 时 target.kind 必须是 group；"
            "kind=group 时 conversation_id 非空、candidates 非空且 conversation_id 必须等于 candidates[0]."
            "conversation_id、direct_user_id 必须是 \"\"；kind=direct 时 title 非空、"
            "conversation_id 必须是 \"\"、candidates 必须是 []，direct_user_id 写对方"
            "在来源里的真实 user_id；业务私信必须非空，唯一匹配来源参会人或已核验日历组织者，"
            "不得是当前用户或其已核验别名；只有个人完整日历 1:1 名册解析不出 user_id 时才写 \"\"。"
        )
    )
    message_title: str = Field(
        default="会议跟进", min_length=1,
        description=(
            "面向实际接收受众核验后的消息标题，同时用于正文首行和推送。"
            "仅在该受众有权知道原会议标题时沿用原题；否则写中性业务标题。"
            "裁员、减员、人员优化本身即敏感，没有名单也不得在业务标题中暗示。"
        ),
    )
    final_message: str = Field(
        description=(
            "必须非空；按实际内容、业务目的和披露范围核验近期讨论与业务承接；含人员敏感信息时，"
            "必须移除全部敏感详情及间接暗示，发送到业务群的内容仅保留普通业务总结；"
            "普通业务群选群无需逐一核验成员披露授权。"
        )
    )
    sensitive_private_message: SensitivePrivateMessage | None = Field(
        description=(
            "没有人员敏感内容时必须是 null；只要存在人员敏感内容就必须独立私聊，即使业务群是 HR 群或参会人高度匹配也不得合并；"
            "缺少受众授权不能私聊兜底，禁止当前用户及其已核验别名。"
            "recipient_evidence 记录实时身份、当前职务职责和实际内容披露授权；"
            "非 null 时 audience_scope 必须是 business。"
        )
    )
    audit_summary: str = Field(min_length=1)
    risk: DecisionRisk = Field(
        default=DecisionRisk.LOW,
        description="Acting on an incorrect meeting decision is low, medium, or high risk.",
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description="Confidence in the meeting decision, from 0 to 1.",
    )
    rule_coverage: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="How completely the meeting Skill rules cover this case.",
    )
    information_completeness: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="How complete the evidence needed for this decision is.",
    )

    def decision_quality(self) -> DecisionQualityResult:
        """Classify this meeting decision with the shared result-quality rules."""
        return classify_decision_quality(
            risk=self.risk,
            confidence=self.confidence,
            rule_coverage=self.rule_coverage,
            information_completeness=self.information_completeness,
        )

    @model_validator(mode="before")
    @classmethod
    def default_legacy_sensitive_private_message(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if "sensitive_private_message" not in value:
                return {**value, "sensitive_private_message": None}
        return value

    @model_validator(mode="after")
    def validate_action_payload(self) -> Self:
        trigger_reasons = set(self.trigger_reasons)
        topic_states = {topic.state for topic in self.topics}
        if (
            "aligned_disagreement" in trigger_reasons
            and "aligned" not in topic_states
        ):
            raise ValueError(ALIGNED_TRIGGER_NEEDS_TOPIC_RULE.message)
        if (
            "aligned" in topic_states
            and "aligned_disagreement" not in trigger_reasons
        ):
            raise ValueError(ALIGNED_TOPIC_NEEDS_TRIGGER_RULE.message)
        if "unresolved_disagreement" in trigger_reasons:
            if "unresolved" not in topic_states:
                raise ValueError(UNRESOLVED_TRIGGER_NEEDS_TOPIC_RULE.message)
            if not self.key_questions:
                raise ValueError(UNRESOLVED_TRIGGER_NEEDS_QUESTIONS_RULE.message)
        if (
            "unresolved" in topic_states
            and "unresolved_disagreement" not in trigger_reasons
        ):
            raise ValueError(UNRESOLVED_TOPIC_NEEDS_TRIGGER_RULE.message)
        has_derek_viewpoint_trigger = "derek_viewpoint" in trigger_reasons
        has_derek_viewpoint = self.derek_viewpoint is not None
        if has_derek_viewpoint_trigger != has_derek_viewpoint:
            raise ValueError(DEREK_VIEWPOINT_PAIRING_RULE.message)

        if not self.final_message.strip():
            raise ValueError(SEND_FINAL_MESSAGE_RULE.message)
        if not self.trigger_reasons:
            raise ValueError(SEND_TRIGGER_REASONS_RULE.message)
        if self.target is None:
            raise ValueError(SEND_TARGET_RULE.message)
        if self.target.kind == "group":
            if (
                not self.target.conversation_id.strip()
                or not self.target.candidates
            ):
                raise ValueError(GROUP_TARGET_FIELDS_RULE.message)
            if self.target.direct_user_id.strip():
                raise ValueError(GROUP_TARGET_NO_DIRECT_USER_RULE.message)
            if (
                self.target.candidates[0].conversation_id
                != self.target.conversation_id
            ):
                raise ValueError(GROUP_TARGET_FIRST_CANDIDATE_RULE.message)
        else:
            if self.target.conversation_id.strip() or self.target.candidates:
                raise ValueError(DIRECT_TARGET_NO_GROUP_FIELDS_RULE.message)
            if not self.target.title.strip():
                raise ValueError(DIRECT_TARGET_TITLE_RULE.message)
        if (
            self.sensitive_private_message is not None
            and self.audience_scope != "business"
        ):
            raise ValueError(PRIVATE_MESSAGE_BUSINESS_SCOPE_RULE.message)
        return self


class MeetingAlignmentAuditFeedback(StrictModel):
    rule: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    requested_revision: str = Field(min_length=1)


class MeetingAlignmentAuditResult(StrictModel):
    """Independent, read-only review of one exact meeting outbound candidate."""

    outcome: Literal["approve", "return", "reject"]
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(min_length=1)
    feedback: MeetingAlignmentAuditFeedback | None

    @model_validator(mode="after")
    def require_feedback_for_return(self) -> Self:
        if (self.outcome == "return") != (self.feedback is not None):
            raise ValueError(MEETING_AUDIT_FEEDBACK_RULE.message)
        return self


def meeting_alignment_decision_digest(decision: MeetingAlignmentDecision) -> str:
    canonical = json.dumps(
        decision.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def persist_audited_meeting_decision(
    decision: MeetingAlignmentDecision,
    audit: MeetingAlignmentAuditResult,
    *,
    audit_session_id: str = "",
    audit_transcript_start_line: int = 0,
    audit_transcript_end_line: int = 0,
    feedback_cycles: int = 0,
) -> str:
    digest = meeting_alignment_decision_digest(decision)
    if audit.candidate_sha256 != digest:
        raise ValueError("meeting Audit result is not bound to this candidate")
    payload = decision.model_dump(mode="json")
    payload["_service_audit"] = {
        "version": 1,
        "feedback_cycles": feedback_cycles,
        "candidate_sha256": digest,
        "result": audit.model_dump(mode="json"),
        "session_id": audit_session_id,
        "transcript_start_line": audit_transcript_start_line,
        "transcript_end_line": audit_transcript_end_line,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def previous_meeting_audit_feedback(raw: str) -> tuple[MeetingAlignmentAuditFeedback | None, int]:
    try:
        payload = json.loads(raw)
        audit = payload.get("_service_audit") if isinstance(payload, dict) else None
        if not isinstance(audit, dict) or audit.get("version") != 1:
            return None, 0
        result = MeetingAlignmentAuditResult.model_validate(audit.get("result"))
        cycles = int(audit.get("feedback_cycles", 0))
        if cycles < 0:
            return None, 0
        return (result.feedback if result.outcome == "return" else None), cycles
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, 0


def meeting_decision_audit_is_approved(raw: str) -> bool:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return False
        audit = payload.get("_service_audit")
        if not isinstance(audit, dict) or audit.get("version") != 1:
            return False
        decision_payload = {key: value for key, value in payload.items() if key != "_service_audit"}
        if "audience_scope" not in decision_payload and decision_payload.get("action") == "send":
            target = decision_payload.get("target")
            if isinstance(target, dict):
                target_kind = target.get("kind")
                if target_kind == "group":
                    decision_payload["audience_scope"] = "business"
                elif target_kind == "direct":
                    decision_payload["audience_scope"] = "personal"
        decision = MeetingAlignmentDecision.model_validate(decision_payload)
        result = MeetingAlignmentAuditResult.model_validate(audit.get("result"))
        digest = meeting_alignment_decision_digest(decision)
        return (
            audit.get("candidate_sha256") == digest
            and result.candidate_sha256 == digest
            and result.outcome == "approve"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def load_persisted_meeting_alignment_decision(
    raw: str,
) -> tuple[MeetingAlignmentDecision, str]:
    """Load a stored decision and canonicalize the one retired scope omission.

    `audience_scope` is required for all current Agent output. Older stored send
    decisions predate that field, but their target kind already determines the
    same scope without re-analyzing the meeting: groups are business delivery
    and direct targets are personal delivery.
    """
    payload: Any = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("persisted meeting decision must be a JSON object")
    if "audience_scope" not in payload and payload.get("action") == "send":
        target = payload.get("target")
        if isinstance(target, dict):
            target_kind = target.get("kind")
            if target_kind == "group":
                payload["audience_scope"] = "business"
            elif target_kind == "direct":
                payload["audience_scope"] = "personal"
    audit = payload.pop("_service_audit", None)
    decision = MeetingAlignmentDecision.model_validate(payload)
    canonical_payload = decision.model_dump(mode="json")
    if audit is not None:
        canonical_payload["_service_audit"] = audit
    return decision, json.dumps(canonical_payload, ensure_ascii=False, separators=(",", ":"))


MeetingAlignmentQueueStatus = Literal[
    "waiting",
    "pending",
    "processing",
    "no_action",
    "ready_to_send",
    "sent",
    "retry",
    "failed",
    "quarantined",
    "skipped",
    "needs_human",
]


class MeetingAlignmentJob(StrictModel):
    id: int
    meeting_id: str
    title: str
    source_json: str
    participants_json: str
    ended_at: str
    eligible_at: str
    status: MeetingAlignmentQueueStatus
    attempts: int
    locked_at: str | None = None
    delivery_claim_token: str = ""
    available_at: str
    error: str
    decision_json: str
    target_kind: str
    target_id: str
    target_title: str
    mentions_json: str
    final_message: str
    send_result_json: str
    calendar_summary_status: str = "not_started"
    calendar_summary_result_json: str = "{}"
    created_at: str
    updated_at: str


class MeetingAlignmentRun(StrictModel):
    id: int
    task_id: int | None = None
    job_id: int | None = None
    codex_session_id: str
    codex_transcript_start_line: int
    codex_transcript_end_line: int
    decision_json: str
    audit_tool_events_json: str
    audit_summary: str
    status: str
    error: str
    created_at: str
    finished_at: str = ""
    updated_at: str = ""
    native_available: bool = False
    native_reason: str = "native_source_unavailable"
    tool_events_available: bool = False
    tool_events_reason: str = "native_source_unavailable"
