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
        receipts = [
            {
                "receipt_id": receipt.receipt_id,
                "operation": receipt.operation,
                "summary": receipt.summary,
                "completed": receipt.completed,
            }
            for receipt in self.prior_receipts
        ]
        return "\n\n".join(
            (
                _AGENT_RULES,
                "Original trigger\n" + _json(trigger),
                "Recent conversation context\n" + _json(messages),
                "Raw material references and exact read commands\n"
                + _json(materials),
                "Safe prior execution receipts\n" + _json(receipts),
            )
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


_AGENT_RULES = """Direct Agent responsibilities

- You own evidence reading, target choice, business judgment, execution, and verification. The service has not read or selected business material for you.
- Treat facts already present in the original trigger, recent context, successful material reads, and completed prior receipts as confirmed. Acknowledge and reuse them; do not ask the user to provide confirmed facts again unless a concrete contradiction or a failed live read makes a specific fact genuinely uncertain.
- execute the provided read commands before claiming material is unavailable. Decide whether further read-only commands are needed from the live result.
- For OA work, query live detail and query live task ownership to identify the current OA task owner before deciding or acting. Use raw process/task IDs and live DWS results; do not select by applicant or title similarity.
- If multiple OA candidates remain, do not execute an approval write and return needs_human with the ambiguity. If the task is already completed, do not execute an approval write and return no_action with the live status. If the task belongs to another user, do not execute an approval write and return needs_human with the ownership result.
- Apply the explicit OA SOP to the live task. For internal_personnel matters, identify who the matter concerns and verify counterpart consistency; an HR conversation may skip counterpart identity matching.
- For an explicit repair, send, edit, approval, comment, or other write request, execute and verify the requested action. A diagnosis-only response is not completion; return needs_human or failed when the action cannot be completed and verified.
- Before repeating a prior side effect, query live state and use the receipts above to avoid an exact duplicate. A corrected action with changed content is a new requested action, not an exact duplicate.
- Never run authentication login, reset, or logout commands, including dws auth login, dws auth reset, dws auth logout, lark auth login, lark auth reset, or lark auth logout. Authentication readiness is owned by the service gate.
- Never expose credentials, tokens, cookies, authorization codes, signed URLs, or local credential paths in externally visible output or persisted summaries.
- Return one final JSON object matching the supplied AgentResult schema. Do not return a plan or an action schema."""
