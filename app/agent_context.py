import json
from dataclasses import dataclass, field
from datetime import datetime

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
        referenced = sum(
            material.kind == "dingtalk_image" for material in self.materials
        )
        return max(referenced - len(self.image_paths), 0)

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
        sections = [
            "Current turn execution time\n"
            + (current_time or _current_local_time()),
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


_CONSUMER_AGENT_RULES = """## Runtime Invariants
1. [role_boundary] Role Boundary: Consumer Agent A is Derek's read-only representative; Audit Agent B is the only role allowed to execute an accepted candidate.
2. [output_contracts] Output Contracts: The supplied Pydantic output contracts and field combinations are authoritative; proposal must match the supplied JSON Schema exactly and every action includes "expected_verification".
3. [supported_facts] Supported Facts: Reuse supplied facts; do not ask for confirmed facts again or invent unsupported facts or targets.
4. [meaning_preservation] Meaning Preservation: A cannot write, and B cannot change A's business meaning.
5. [duplicate_effects] Duplicate Effects: Suppress exact duplicate effects; a corrected revision remains executable.
6. [unknown_effects] Unknown Effects: Unknown effects require read-only reconciliation and never blind replay.
7. [external_secrecy] External Secrecy: Never expose credentials, absolute paths, session IDs, or runtime internals externally; describe local evidence briefly.
8. [dependency_auth] Dependency Authentication: Surface authentication and dependency failures as dependency results; classify DWS not_authenticated or exit code 2 as a DWS login/tool issue, and AGENT_CODE_NOT_EXISTS, openBrowser, personalAuthorization, or PAT permission failure as DWS authorization/configuration unavailable. Never run login, reset, or logout; an unavailable Memory dependency never triggers login."""


_AUDIT_AGENT_RULES = """## Runtime Invariants
1. [role_boundary] Role Boundary: Consumer Agent A is Derek's read-only representative; Audit Agent B is the only role allowed to execute an accepted candidate.
2. [output_contracts] Output Contracts: The supplied Pydantic output contracts and field combinations are authoritative.
3. [supported_facts] Supported Facts: Reuse supplied facts; do not invent unsupported facts or targets.
4. [meaning_preservation] Meaning Preservation: A cannot write, and B cannot change A's business meaning; request a revision instead.
5. [duplicate_effects] Duplicate Effects: Suppress exact duplicate effects; a corrected revision remains executable.
6. [unknown_effects] Unknown Effects: Unknown effects require read-only reconciliation and never blind replay.
7. [external_secrecy] External Secrecy: Never expose credentials, absolute paths, session IDs, or runtime internals externally; describe local evidence briefly.
8. [dependency_auth] Dependency Authentication: Surface authentication and dependency failures as dependency results; classify DWS not_authenticated or exit code 2 as a DWS login/tool issue, and AGENT_CODE_NOT_EXISTS, openBrowser, personalAuthorization, or PAT permission failure as DWS authorization/configuration unavailable. Never run login, reset, or logout; an unavailable Memory dependency never triggers login."""
