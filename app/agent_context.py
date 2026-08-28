import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.agent_contracts import AuditFeedback, ConsumerProposal
from app.agent_result import AgentError
from app.agent_skill_usage import LoadedSkillReceipt

CRITICAL_INFO_UNAVAILABLE_CODE = "critical_info_unavailable"
CRITICAL_INFO_UNAVAILABLE_SUMMARY = "关键信息暂时无法读取，未作出业务判断。"


@dataclass(frozen=True)
class AgentContextMessage:
    message_id: str
    sender: str
    text: str
    create_time: str


@dataclass(frozen=True)
class MaterialReference:
    kind: str
    reference: str
    source_message_id: str
    read_commands: tuple[str, ...]


@dataclass(frozen=True)
class PriorReceipt:
    receipt_id: str
    operation: str
    summary: str
    completed: bool


@dataclass(frozen=True)
class ManualRerunInstruction:
    source_attempt_id: int
    reviewer_feedback: str = ""
    suggested_reply_text: str = ""
    feedback_scope: str = "one_time"
    skill_update_requested: bool = False
    skill_update_receipts_json: str = "[]"


@dataclass(frozen=True)
class AgentTaskContext:
    task_id: int
    channel: str
    conversation_id: str
    conversation_title: str
    single_chat: bool
    trigger_message_id: str
    trigger_sender: str
    trigger_text: str
    trigger_create_time: str
    messages: tuple[AgentContextMessage, ...]
    materials: tuple[MaterialReference, ...]
    prior_receipts: tuple[PriorReceipt, ...]
    manual_rerun: ManualRerunInstruction | None = None
    trigger_sender_user_id: str = ""
    trigger_sender_open_dingtalk_id: str = ""
    trigger_mentioned_user_ids: tuple[str, ...] = ()
    trigger_raw_payload: dict[str, object] = field(default_factory=dict)
    image_paths: tuple[str, ...] = ()
    image_sha256s: tuple[str, ...] = ()
    required_reviewed_skills: tuple[LoadedSkillReceipt, ...] = ()

    @property
    def unresolved_image_count(self) -> int:
        # Missing attachments are an input-quality signal, not an automatic
        # business failure. The Consumer must decide from the readable text
        # whether the image is actually required for the requested action.
        return 0

    @property
    def image_dependency_error(self) -> AgentError | None:
        if not self.unresolved_image_count:
            return None
        return AgentError(code=CRITICAL_INFO_UNAVAILABLE_CODE, retryable=False)

    def render(
        self,
        *,
        proposal_revision: int = 0,
        feedback: AuditFeedback | None = None,
        current_time: str | None = None,
    ) -> str:
        sections = [
            _CONSUMER_AGENT_RULES,
            self.render_business_context(current_time=current_time),
        ]
        if feedback is not None:
            sections.append(
                "### Audit Feedback Requiring A Replacement Proposal\n"
                + _json(
                    {
                        "proposal_revision": proposal_revision,
                        "feedback": feedback.model_dump(mode="json"),
                    }
                )
            )
        return "\n\n".join(sections)

    def render_business_context(
        self,
        *,
        current_time: str | None = None,
        include_heading: bool = True,
    ) -> str:
        trigger = {
            "task_id": self.task_id,
            "channel": self.channel,
            "conversation_id": self.conversation_id,
            "conversation_title": self.conversation_title,
            "single_chat": self.single_chat,
            "message_id": self.trigger_message_id,
            "sender": self.trigger_sender,
            "sender_user_id": self.trigger_sender_user_id,
            "sender_open_dingtalk_id": self.trigger_sender_open_dingtalk_id,
            "mentioned_user_ids": list(self.trigger_mentioned_user_ids),
            "text": self.trigger_text,
            "create_time": self.trigger_create_time,
            "raw_payload": self.trigger_raw_payload,
        }
        messages = [
            {
                "message_id": message.message_id,
                "sender": message.sender,
                "text": message.text,
                "create_time": message.create_time,
            }
            for message in self.messages
        ]
        materials = [
            {
                "kind": material.kind,
                "reference": material.reference,
                "source_message_id": material.source_message_id,
                "read_commands": list(material.read_commands),
            }
            for material in self.materials
        ]
        effective_current_time = current_time or _current_local_time()
        sections = [
            "Current turn execution time\n"
            + effective_current_time,
            "Canonical time facts\n"
            + _json(
                {
                    "execution_time": _canonical_context_time(
                        effective_current_time
                    ),
                    "trigger_create_time": _canonical_context_time(
                        self.trigger_create_time
                    ),
                    "message_create_times": [
                        {
                            "message_id": message.message_id,
                            "create_time": _canonical_context_time(
                                message.create_time
                            ),
                        }
                        for message in self.messages
                    ],
                    "comparison_rule": (
                        "Compare only the UTC values. A DingTalk timestamp without "
                        "an explicit offset is Asia/Shanghai before conversion."
                    ),
                }
            ),
            "Original trigger\n" + _json(trigger),
            "Recent conversation context\n" + _json(messages),
            "Raw material references and exact read commands\n" + _json(materials),
        ]
        if self.image_paths:
            sections.append(
                "Actual Codex image inputs\n"
                + _json(
                    [
                        {"path": path, "sha256": sha256}
                        for path, sha256 in zip(
                            self.image_paths,
                            self.image_sha256s,
                            strict=True,
                        )
                    ]
                )
            )
        if self.unresolved_image_count:
            sections.append(
                "Unavailable image inputs\n"
                + _json(
                    {
                        "count": self.unresolved_image_count,
                        "instruction": (
                            "The referenced image could not be downloaded. "
                            "Treat it as unavailable, never infer its contents. "
                            "Continue from text and other readable materials when "
                            "they are sufficient. If the requested judgment depends "
                            "on the image, ask the sender to provide the relevant "
                            "facts as text or resend a readable image."
                        ),
                    }
                )
            )
        if self.prior_receipts:
            sections.append(
                "Safe prior execution receipts\n"
                + _json(
                    [
                        {
                            "receipt_id": receipt.receipt_id,
                            "operation": receipt.operation,
                            "summary": receipt.summary,
                            "completed": receipt.completed,
                        }
                        for receipt in self.prior_receipts
                    ]
                )
            )
        if self.manual_rerun is not None:
            sections.append(
                "Manual rerun instruction\n"
                + _json(
                    {
                        "source_attempt_id": self.manual_rerun.source_attempt_id,
                        "reviewer_feedback": self.manual_rerun.reviewer_feedback,
                        "suggested_reply_text": self.manual_rerun.suggested_reply_text,
                        "feedback_scope": self.manual_rerun.feedback_scope,
                        "skill_update_requested": self.manual_rerun.skill_update_requested,
                        "skill_update_receipts_json": self.manual_rerun.skill_update_receipts_json,
                    }
                )
            )
        body = "\n\n".join(sections)
        return f"## Context Facts\n{body}" if include_heading else body


@dataclass(frozen=True)
class AuditTurnContext:
    task: AgentTaskContext
    proposal_revision: int
    operation_id: str
    proposal: ConsumerProposal
    audit_rules: str
    consumer_skills: tuple[LoadedSkillReceipt, ...] = ()

    def render(self, *, current_time: str | None = None) -> str:
        context_facts = "\n\n".join(
            (
                self.task.render_business_context(
                    current_time=current_time,
                    include_heading=False,
                ),
                "Verified Skills read by Consumer A\n"
                + _json(
                    [
                        {
                            "name": receipt.name,
                            "path": receipt.path,
                            "sha256": receipt.sha256,
                        }
                        for receipt in self.consumer_skills
                    ]
                ),
                "Candidate revision\n"
                + _json(
                    {
                        "proposal_revision": self.proposal_revision,
                        "operation_id": self.operation_id,
                        "proposal": self.proposal.model_dump(mode="json"),
                    }
                ),
            )
        )
        return f"{_AUDIT_AGENT_RULES}\n\n## Context Facts\n{context_facts}"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _current_local_time() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


_DINGTALK_MESSAGE_TIME_ZONE = ZoneInfo("Asia/Shanghai")


def _canonical_context_time(value: str) -> dict[str, str]:
    raw = value.strip()
    if not raw:
        return {"raw": "", "status": "missing"}
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return {"raw": raw, "status": "unparseable"}
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_DINGTALK_MESSAGE_TIME_ZONE)
        assumed_timezone = "Asia/Shanghai"
    else:
        assumed_timezone = "explicit"
    return {
        "raw": raw,
        "assumed_timezone": assumed_timezone,
        "utc": parsed.astimezone(timezone.utc).isoformat(),
    }


_CONSUMER_AGENT_RULES = """## Application Result Contract
1. [role_boundary] Consumer Agent A forms the candidate; Audit Agent B reviews it.
2. [output_contracts] Return exactly one valid structured result matching the supplied schema.
3. [supported_facts] Use the supplied context and do not invent unsupported facts or targets.
4. [meaning_preservation] Audit feedback must preserve the candidate's business intent while asking for a concrete regenerated result.
5. [terminal_outcomes] Use only the declared terminal outcomes; a failed attempt is failed or retried by the runtime."""


_AUDIT_AGENT_RULES = """## Application Result Contract
1. [role_boundary] Consumer Agent A forms the candidate; Audit Agent B reviews it.
2. [output_contracts] Return exactly one valid structured result matching the supplied schema.
3. [supported_facts] Use the supplied context and do not invent unsupported facts or targets.
4. [feedback] Return feedback_provided with concrete rule, observation, and requested_revision when Consumer must regenerate its result.
5. [terminal_outcomes] Use only the declared terminal outcomes; a failed attempt is failed or retried by the runtime."""
