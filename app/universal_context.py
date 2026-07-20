from dataclasses import dataclass

from app.dingtalk_models import DingTalkConversation, DingTalkMessage


@dataclass(frozen=True)
class UniversalContextMessage:
    sender_name: str
    open_message_id: str
    content: str


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
                f"Force new decision: {str(self.force_new_decision).lower()}",
                f"Dry run: {str(self.dry_run).lower()}",
                "Recent messages:",
                *message_lines,
            ]
        )


def build_universal_context(
    *,
    conversation: DingTalkConversation,
    trigger: DingTalkMessage,
    context_messages: list[DingTalkMessage],
    task_id: int,
    force_new_decision: bool,
    dry_run: bool,
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
    )


def _snapshot_message(message: DingTalkMessage) -> UniversalContextMessage:
    return UniversalContextMessage(
        sender_name=message.sender_name,
        open_message_id=message.open_message_id,
        content=message.content,
    )
