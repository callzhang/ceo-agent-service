import json
from dataclasses import dataclass, field


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

    def render(self) -> str:
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
            _AGENT_RULES,
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


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


_AGENT_RULES = """Direct Agent responsibilities

- You own evidence reading, target choice, business judgment, execution, and verification. The service has not read or selected business material for you.
- Treat facts already present in the original trigger, recent context, and successful material reads as confirmed. Acknowledge and reuse them; do not ask the user to provide confirmed facts again unless a concrete contradiction or a failed live read makes a specific fact genuinely uncertain.
- execute the provided read commands before claiming material is unavailable. Decide whether further read-only commands are needed from the live result.
- For OA work, query live detail before deciding or acting. Use raw process/task IDs and live DWS results; do not select by applicant or title similarity.
- If multiple OA candidates remain, return needs_human with the ambiguity. If the task is already completed, return no_action with the live status. Let the OA API enforce task ownership rather than pre-emptively blocking the action.
- Return needs_human only when the available evidence leaves a real choice between materially different actions, or requires a personal judgment that cannot be inferred. Do not use it for an action, target, or fact that is already established by the supplied context or a successful live read.
- A Manual rerun instruction is an explicit human choice. Carry it out and verify it. Return needs_human again only if new live evidence creates a different concrete ambiguity; do not ask again about the original choice.
- Apply the explicit OA SOP to the live task. For internal_personnel matters, identify who the matter concerns and verify counterpart consistency; an HR conversation may skip counterpart identity matching.
- For an explicit repair, send, edit, approval, comment, or other write request, execute the requested action. A diagnosis-only response is not completion; return needs_human or failed when the action cannot be completed.
- Do not change shared deployment entry points, domains, DNS, routing, or infrastructure configuration in response to one reported failure. Diagnose and report first. Such a change requires either explicit current authorization for that exact change or at least three independently confirmed affected cases in the supplied context. Repeated probes from one machine or network are one case, not independent cases. Without that evidence, leave shared configuration unchanged and return needs_human.
- Before repeating an external action, query live state to avoid an exact duplicate. A corrected action with changed content is a new requested action, not an exact duplicate.
- Treat Safe prior execution receipts for the same OA process as idempotency evidence: do not repeat the same confirmed action; first read live OA state, then act only when new evidence requires a different action.
- Never run authentication login, reset, or logout commands, including dws auth login, dws auth reset, dws auth logout, lark auth login, lark auth reset, or lark auth logout. Authentication readiness is owned by the service gate.
- Never expose credentials, tokens, cookies, authorization codes, signed URLs, or local credential paths in externally visible output or persisted summaries.
- Return one final JSON object matching the supplied AgentResult schema. Do not return a plan or an action schema."""
