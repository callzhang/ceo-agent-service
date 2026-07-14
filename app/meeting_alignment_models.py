from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    current_user_id: str
    summary: str
    transcript: list[TranscriptLine]
    source_url: str = ""


class AlignmentView(StrictModel):
    speaker: str
    view: str
    reason: str


class AlignmentTopic(StrictModel):
    title: str
    state: Literal["aligned", "unresolved"]
    views: list[AlignmentView]
    conclusion: str
    alignment_reason: str

    @model_validator(mode="after")
    def validate_aligned_result(self) -> Self:
        if self.state == "aligned" and (
            not self.conclusion.strip() or not self.alignment_reason.strip()
        ):
            raise ValueError(
                "aligned topic requires conclusion and alignment_reason"
            )
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
    kind: Literal["group", "direct"]
    conversation_id: str
    direct_user_id: str
    title: str
    candidates: list[TargetCandidate]


class MeetingAlignmentDecision(StrictModel):
    action: Literal["no_action", "send"]
    trigger_reasons: list[
        Literal[
            "aligned_disagreement",
            "unresolved_disagreement",
            "derek_viewpoint",
        ]
    ]
    topics: list[AlignmentTopic]
    derek_viewpoint: DerekViewpoint | None
    key_questions: list[KeyQuestion]
    mention_names: list[str]
    target: DeliveryTarget | None
    final_message: str
    audit_summary: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_action_payload(self) -> Self:
        trigger_reasons = set(self.trigger_reasons)
        topic_states = {topic.state for topic in self.topics}
        if (
            "aligned_disagreement" in trigger_reasons
            and "aligned" not in topic_states
        ):
            raise ValueError("aligned_disagreement requires an aligned topic")
        if (
            "aligned" in topic_states
            and "aligned_disagreement" not in trigger_reasons
        ):
            raise ValueError(
                "aligned topic requires aligned_disagreement trigger"
            )
        if "unresolved_disagreement" in trigger_reasons:
            if "unresolved" not in topic_states:
                raise ValueError(
                    "unresolved_disagreement requires an unresolved topic"
                )
            if not self.key_questions:
                raise ValueError(
                    "unresolved_disagreement requires key_questions"
                )
        if (
            "unresolved" in topic_states
            and "unresolved_disagreement" not in trigger_reasons
        ):
            raise ValueError(
                "unresolved topic requires unresolved_disagreement trigger"
            )
        has_derek_viewpoint_trigger = "derek_viewpoint" in trigger_reasons
        has_derek_viewpoint = self.derek_viewpoint is not None
        if has_derek_viewpoint_trigger != has_derek_viewpoint:
            raise ValueError(
                "derek_viewpoint trigger and payload must appear together"
            )

        if self.action == "no_action":
            if (
                self.trigger_reasons
                or self.topics
                or self.derek_viewpoint is not None
                or self.key_questions
                or self.mention_names
                or self.target is not None
                or self.final_message.strip()
            ):
                raise ValueError(
                    "no_action requires empty analysis and delivery output"
                )
            return self
        if not self.final_message.strip():
            raise ValueError("send requires final_message")
        if not self.trigger_reasons:
            raise ValueError("send requires trigger_reasons")
        # A genuine multi-party trigger can outlive temporary group discovery
        # failure. The consumer keeps the decision and retries delivery rather
        # than converting it to no_action or falling back to a direct message.
        if self.target is None:
            return self
        if self.target.kind == "group":
            if (
                not self.target.conversation_id.strip()
                or not self.target.candidates
            ):
                raise ValueError(
                    "group target requires candidates and conversation_id"
                )
            if self.target.direct_user_id.strip():
                raise ValueError("group target cannot contain direct_user_id")
            if (
                self.target.candidates[0].conversation_id
                != self.target.conversation_id
            ):
                raise ValueError(
                    "group target must select the first ranked candidate"
                )
        else:
            if not self.target.direct_user_id.strip():
                raise ValueError("direct target requires direct_user_id")
            if self.target.conversation_id.strip() or self.target.candidates:
                raise ValueError(
                    "direct target cannot contain group delivery fields"
                )
        return self


MeetingAlignmentQueueStatus = Literal[
    "waiting",
    "pending",
    "processing",
    "no_action",
    "ready_to_send",
    "sent",
    "retry",
    "failed",
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
    available_at: str
    error: str
    decision_json: str
    target_kind: str
    target_id: str
    target_title: str
    mentions_json: str
    final_message: str
    send_result_json: str
    created_at: str
    updated_at: str


class MeetingAlignmentRun(StrictModel):
    id: int
    job_id: int
    codex_session_id: str
    codex_transcript_start_line: int
    codex_transcript_end_line: int
    decision_json: str
    audit_tool_events_json: str
    audit_summary: str
    status: str
    error: str
    created_at: str
