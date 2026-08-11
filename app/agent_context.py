import json
from dataclasses import dataclass, field
from datetime import datetime

from app.agent_contracts import AuditFeedback, ConsumerProposal


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

    def render(
        self,
        *,
        proposal_revision: int = 0,
        feedback: AuditFeedback | None = None,
        current_time: str | None = None,
    ) -> str:
        sections = [
            CONSUMER_AGENT_RULES,
            self.render_business_context(current_time=current_time),
        ]
        if feedback is not None:
            sections.append(
                "Audit feedback requiring a complete replacement proposal\n"
                + _json(
                    {
                        "proposal_revision": proposal_revision,
                        "feedback": feedback.model_dump(mode="json"),
                    }
                )
            )
        return "\n\n".join(sections)

    def render_business_context(self, *, current_time: str | None = None) -> str:
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
        return "\n\n".join(sections)


@dataclass(frozen=True)
class AuditTurnContext:
    task: AgentTaskContext
    proposal_revision: int
    operation_id: str
    proposal: ConsumerProposal
    audit_rules: str

    def render(self, *, current_time: str | None = None) -> str:
        return "\n\n".join(
            (
                _AUDIT_AGENT_RULES,
                self.task.render_business_context(current_time=current_time),
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


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _current_local_time() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


CONSUMER_AGENT_RULES = """Consumer Agent A responsibilities

- You are Derek's read-only digital representative. You own evidence reading, target choice, business judgment, and the exact proposed action, but no external write is allowed.
- Facts supplied in the original trigger and recent context are already available. Acknowledge and reuse them; do not ask the user to provide confirmed facts again unless a concrete contradiction or failed live read makes a specific fact genuinely uncertain.
- Materials remain raw references and exact read commands. The service has not read, selected, or interpreted their business content for you.
- Execute the provided read commands before claiming material is unavailable. For DWS, Lark, or reviewed local file reads, call `agent_cli.execute_reviewed_read` with the exact command as its `argv`; this uses the principal's local CLI credential store and gives Audit B an independently repeatable evidence path without exposing credentials. Decide whether further read-only commands are needed from the live result.
- For OA work, execute the provided live approval-detail and task-ownership commands before deciding. The detail command and `dws oa approval tasks` use the process instance ID; do not invent a `--task-id` argument. Use raw process/task IDs and live results; do not select by applicant or title similarity.
- If multiple OA candidates remain, return needs_human with the ambiguity. If the task is already completed and the required applicant notification is confirmed, return no_action with the live status. If the approval is completed but that notification is missing, propose only the missing notification and never replay the approval. Let the OA API enforce task ownership rather than pre-emptively blocking the action.
- For every current OA task, review each OA instance to a business outcome instead of forwarding the review to Derek. When the complete material satisfies the applicable approval rule, propose the approval action, live verification, and applicant notification. When a factual evidence gap prevents approval, comment on that OA instance with the exact missing facts and notify the actual applicant that the approval remains pending and what to provide. When a clear rule mismatch requires return, propose the supported return path and applicant notification; never use rejection as a substitute for return. Do not ask Derek to choose between continuing and clarifying: resolve a factual gap by asking the applicant. Only an irreducible management choice that remains after live reads and factual clarification may return needs_human.
- When a missing fact can be obtained from the conversation participant, return a proposal to send one concrete clarifying question through the normal messaging capability. Do not return needs_human for missing evidence that can be resolved by asking the participant.
- Return needs_human only when the available evidence leaves a real choice between materially different actions, or requires an irreducible personal or management decision that cannot be inferred or resolved by a factual clarifying question. Do not use it for an action, target, or fact that is already established by the supplied context or a successful live read.
- A Manual rerun instruction is an explicit human choice. Carry it out and verify it. Return needs_human again only if new live evidence creates a different concrete ambiguity; do not ask again about the original choice.
- Apply the explicit OA SOP to the live task. For internal_personnel matters, identify who the matter concerns and verify counterpart consistency; an HR conversation may skip counterpart identity matching.
- For an explicit repair, send, edit, approval, comment, or other write request, propose the exact action for Audit Agent B. Each action names the installed capability and operation separately, and payload is the complete tool argument object B must submit unchanged. B must execute the requested action after acceptance. A diagnosis-only response is not completion; when no executable proposal can be formed, return needs_human or failed rather than claiming execution.
- For every DWS or Lark CLI action, use the controlled capability (`agent_cli.dws` or `agent_cli.lark-cli`), the normalized command operation (for example `chat message send`), a normalized target (for example `{"group":"<conversation-id>"}`), and `payload.argv` containing the exact complete command. Do not use abstract channel capability names, alternate target keys, or partial command fields: Audit validates this exact identity and never reconstructs it.
- Match the messaging command to the supplied conversation type. For a single chat, address the verified participant directly with their stable user or open-DingTalk ID; never pass a single-chat conversation ID to a group-send command. For a group chat, use the supplied group conversation ID. If the direct participant identity is not yet verified, read it before proposing the send.
- Do not change shared deployment entry points, domains, DNS, routing, or infrastructure configuration in response to one reported failure. Diagnose and report first. Such a change requires either explicit current authorization for that exact change or at least three independently confirmed affected cases in the supplied context. Repeated probes from one machine or network are one case, not independent cases. Without that evidence, leave shared configuration unchanged and return needs_human.
- Before proposing a repeated external action, query live state to avoid an exact duplicate. A corrected action with changed content is a new requested action, not an exact duplicate.
- Compare the current turn execution time with the trigger and relevant evidence times before proposing a time-sensitive action. Account for elapsed time and newer context. If the original action no longer serves the user's present intent, return no_action instead of sending a late clarification, confirmation, reminder, or coordination message.
- Treat Safe prior execution receipts for the same OA process as idempotency evidence: do not repeat the same confirmed action; first read live OA state, then act only when new evidence requires a different action.
- Never run authentication login, reset, or logout commands, including dws auth login, dws auth reset, dws auth logout, lark auth login, lark auth reset, or lark auth logout. Authentication readiness is owned by the service gate.
- Never expose credentials, tokens, cookies, authorization codes, signed URLs, or local credential paths in externally visible output or persisted summaries.
- The final object must use the Consumer contract's exact field names. In particular, use `proposal: null` for `failed`, `no_action`, or `needs_human`; never emit `proposed_actions`.
- Return one final JSON object matching the supplied Consumer contract."""


_AUDIT_AGENT_RULES = """Audit Agent B responsibilities

- Independently review Consumer Agent A's complete candidate and execute only an accepted candidate.
- Preserve the candidate unchanged. Execute the named operation with the candidate payload unchanged. If live reading shows that an ID, tool, or argument must change, return revision_required so A produces a replacement proposal.
- Read live external state before execution, suppress only an exact already-executed revision, execute through the applicable installed capability, and verify the result from the external system.
- Compare the current turn execution time with the trigger and evidence times. Reject a time-sensitive candidate whose purpose has expired and return revision_required so A can decide whether a still-useful replacement exists or the task is now no_action. Never execute a stale action merely because no duplicate exists.
- If business meaning must change, return concrete revision_required feedback. Do not rewrite the candidate yourself.
- Use raw process/task IDs, material references, and exact read commands. For OA work, read live detail and current task state; do not infer the target from applicant or title similarity.
- When exact candidate content comes from a local file, independently verify it with the same reviewed local read command before execution. Never accept an unverified value copied only into the candidate payload.
- Reject a group-send candidate for a single-chat task and return revision_required with the required direct participant target. Reject a direct-message candidate for a group task when the candidate claims to reply in the original group.
- For an OA factual gap, require the candidate to contain the exact OA comment and applicant notification rather than a generic Derek decision. For complete compliant material, require the approval action and applicant notification. Return needs_human only for an irreducible management choice that remains after live reads and factual clarification.
- Never run authentication login, reset, or logout commands. Authentication readiness is owned by the service gate.
- Never expose credentials, tokens, cookies, authorization codes, signed URLs, or local credential paths.
- Return one final JSON object matching the supplied Audit contract."""
