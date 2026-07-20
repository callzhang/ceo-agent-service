import hashlib
import json
from dataclasses import dataclass

from app.dingtalk_models import DingTalkConversation, DingTalkMessage


@dataclass(frozen=True)
class UniversalContextMessage:
    sender_name: str
    open_message_id: str
    content: str
    sender_open_dingtalk_id: str | None = None
    sender_user_id: str | None = None
    message_type: str | None = None
    create_time: str = ""
    mentioned_user_ids: tuple[str, ...] = ()
    quoted_message_id: str | None = None
    quoted_content: str | None = None
    raw_payload_json: str = "{}"

    def __post_init__(self) -> None:
        if not isinstance(self.mentioned_user_ids, tuple):
            raise TypeError("mentioned_user_ids must be a tuple")
        try:
            raw_payload = json.loads(self.raw_payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("raw_payload_json must contain valid JSON") from exc
        if not isinstance(raw_payload, dict):
            raise ValueError("raw_payload_json must contain a JSON object")
        object.__setattr__(
            self,
            "raw_payload_json",
            json.dumps(
                raw_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )


@dataclass(frozen=True)
class UniversalTaskContext:
    task_id: int
    conversation_id: str
    conversation_title: str
    single_chat: bool
    trigger_message_id: str
    trigger_sender: str
    trigger_text: str
    context_messages: tuple[UniversalContextMessage, ...]
    required_dependencies: tuple[str, ...]
    force_new_decision: bool
    dry_run: bool
    execution_generation: str = "initial"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.execution_generation, str)
            or not self.execution_generation.strip()
        ):
            raise ValueError("execution_generation must be non-empty")

    def render_for_agent(self) -> str:
        message_lines = [
            f"- {message.sender_name} ({message.open_message_id}): {message.content}"
            for message in self.context_messages
        ]
        if not message_lines:
            message_lines.append("- No context messages.")

        return "\n".join(
            [
                f"Task ID: {self.task_id}",
                f"Conversation ID: {self.conversation_id}",
                f"Conversation title: {self.conversation_title}",
                f"Single chat: {str(self.single_chat).lower()}",
                f"Trigger message ID: {self.trigger_message_id}",
                f"Trigger sender: {self.trigger_sender}",
                f"Trigger text: {self.trigger_text}",
                f"Required dependencies: {', '.join(self.required_dependencies)}",
                f"Execution generation: {self.execution_generation}",
                f"Force new decision: {str(self.force_new_decision).lower()}",
                f"Dry run: {str(self.dry_run).lower()}",
                "Recent messages:",
                *message_lines,
            ]
        )


def canonical_universal_context_json(context: UniversalTaskContext) -> str:
    if not isinstance(context, UniversalTaskContext):
        raise TypeError("context must be UniversalTaskContext")
    if not isinstance(context.task_id, int) or isinstance(context.task_id, bool):
        raise TypeError("task_id must be an int")
    for field_name in (
        "conversation_id",
        "conversation_title",
        "trigger_message_id",
        "trigger_sender",
        "trigger_text",
        "execution_generation",
    ):
        if not isinstance(getattr(context, field_name), str):
            raise TypeError(f"{field_name} must be a str")
    for field_name in ("single_chat", "force_new_decision", "dry_run"):
        if not isinstance(getattr(context, field_name), bool):
            raise TypeError(f"{field_name} must be a bool")
    if not isinstance(context.context_messages, tuple):
        raise TypeError("context_messages must be a tuple")
    if not isinstance(context.required_dependencies, tuple):
        raise TypeError("required_dependencies must be a tuple")

    context_messages: list[dict[str, object]] = []
    for message in context.context_messages:
        if not isinstance(message, UniversalContextMessage):
            raise TypeError("context_messages items must be UniversalContextMessage")
        for field_name in (
            "sender_name",
            "open_message_id",
            "content",
            "create_time",
            "raw_payload_json",
        ):
            if not isinstance(getattr(message, field_name), str):
                raise TypeError(f"context message {field_name} must be a str")
        for field_name in (
            "sender_open_dingtalk_id",
            "sender_user_id",
            "message_type",
            "quoted_message_id",
            "quoted_content",
        ):
            value = getattr(message, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"context message {field_name} must be str or None")
        if not isinstance(message.mentioned_user_ids, tuple) or any(
            not isinstance(user_id, str) for user_id in message.mentioned_user_ids
        ):
            raise TypeError("context message mentioned_user_ids must be tuple[str, ...]")
        raw_payload = json.loads(message.raw_payload_json)
        if not isinstance(raw_payload, dict):
            raise TypeError("context message raw_payload_json must contain an object")
        context_messages.append(
            {
                "sender_name": message.sender_name,
                "sender_open_dingtalk_id": message.sender_open_dingtalk_id,
                "sender_user_id": message.sender_user_id,
                "open_message_id": message.open_message_id,
                "message_type": message.message_type,
                "create_time": message.create_time,
                "content": message.content,
                "mentioned_user_ids": list(message.mentioned_user_ids),
                "quoted_message_id": message.quoted_message_id,
                "quoted_content": message.quoted_content,
                "raw_payload": raw_payload,
            }
        )

    required_dependencies: list[str] = []
    for dependency in context.required_dependencies:
        if not isinstance(dependency, str):
            raise TypeError("required_dependencies items must be str")
        required_dependencies.append(dependency)

    return json.dumps(
        {
            "task_id": context.task_id,
            "conversation_id": context.conversation_id,
            "conversation_title": context.conversation_title,
            "single_chat": context.single_chat,
            "trigger_message_id": context.trigger_message_id,
            "trigger_sender": context.trigger_sender,
            "trigger_text": context.trigger_text,
            "context_messages": context_messages,
            "required_dependencies": required_dependencies,
            "force_new_decision": context.force_new_decision,
            "dry_run": context.dry_run,
            "execution_generation": context.execution_generation,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def universal_context_sha256(context: UniversalTaskContext) -> str:
    canonical = canonical_universal_context_json(context)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_universal_context(
    *,
    conversation: DingTalkConversation,
    trigger: DingTalkMessage,
    context_messages: list[DingTalkMessage],
    task_id: int,
    force_new_decision: bool,
    dry_run: bool,
    execution_generation: str = "initial",
) -> UniversalTaskContext:
    trigger_snapshot = _snapshot_message(trigger)
    messages: list[UniversalContextMessage] = []
    trigger_added = False
    for message in context_messages:
        if message.open_message_id == trigger.open_message_id:
            if not trigger_added:
                messages.append(trigger_snapshot)
                trigger_added = True
            continue
        messages.append(_snapshot_message(message))

    if not trigger_added:
        messages.append(trigger_snapshot)

    return UniversalTaskContext(
        task_id=task_id,
        conversation_id=conversation.open_conversation_id,
        conversation_title=conversation.title,
        single_chat=conversation.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        context_messages=tuple(messages),
        required_dependencies=("dws",),
        force_new_decision=force_new_decision,
        dry_run=dry_run,
        execution_generation=execution_generation,
    )


def _snapshot_message(message: DingTalkMessage) -> UniversalContextMessage:
    return UniversalContextMessage(
        sender_name=message.sender_name,
        open_message_id=message.open_message_id,
        content=message.content,
        sender_open_dingtalk_id=message.sender_open_dingtalk_id,
        sender_user_id=message.sender_user_id,
        message_type=message.message_type,
        create_time=message.create_time,
        mentioned_user_ids=tuple(message.mentioned_user_ids),
        quoted_message_id=message.quoted_message_id,
        quoted_content=message.quoted_content,
        raw_payload_json=json.dumps(
            message.raw_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
