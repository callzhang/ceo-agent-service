from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.universal_context import (
    UniversalTaskContext,
    build_universal_context,
)


def make_conversation() -> DingTalkConversation:
    return DingTalkConversation(
        open_conversation_id="conversation-1",
        title="Friday planning",
        single_chat=True,
        unread_point=7,
    )


def make_message(message_id: str, sender: str, content: str) -> DingTalkMessage:
    return DingTalkMessage(
        open_conversation_id="conversation-1",
        open_message_id=message_id,
        conversation_title="Friday planning",
        single_chat=True,
        sender_name=sender,
        create_time="2026-07-20 10:00:00",
        content=content,
    )


def build_context(
    context_messages: list[DingTalkMessage],
) -> UniversalTaskContext:
    return build_universal_context(
        conversation=make_conversation(),
        trigger=make_message("trigger-1", "Derek", "Please review this."),
        context_messages=context_messages,
        task_id=42,
        force_new_decision=True,
        dry_run=False,
    )


def test_maps_metadata_and_renders_trigger_and_recent_message() -> None:
    prior = make_message("prior-1", "Alex", "The previous decision was recorded.")

    context = build_context([prior])

    assert context.task_id == 42
    assert context.conversation_id == "conversation-1"
    assert context.conversation_title == "Friday planning"
    assert context.single_chat is True
    assert context.trigger_message_id == "trigger-1"
    assert context.trigger_sender == "Derek"
    assert context.trigger_text == "Please review this."
    assert context.force_new_decision is True
    assert context.dry_run is False
    assert context.render_for_agent() == (
        "Task ID: 42\n"
        "Conversation ID: conversation-1\n"
        "Conversation title: Friday planning\n"
        "Single chat: true\n"
        "Trigger message ID: trigger-1\n"
        "Trigger sender: Derek\n"
        "Trigger text: Please review this.\n"
        "Required dependencies: dws\n"
        "Force new decision: true\n"
        "Dry run: false\n"
        "Recent messages:\n"
        "- Alex (prior-1): The previous decision was recorded.\n"
        "- Derek (trigger-1): Please review this."
    )


def test_dws_is_required_for_dingtalk_context() -> None:
    assert build_context([]).required_dependencies == ["dws"]


def test_trigger_is_appended_when_absent_and_not_duplicated_when_present() -> None:
    trigger = make_message("trigger-1", "Derek", "Please review this.")
    prior = make_message("prior-1", "Alex", "Earlier message.")

    absent = build_universal_context(
        conversation=make_conversation(),
        trigger=trigger,
        context_messages=[prior],
        task_id=1,
        force_new_decision=False,
        dry_run=True,
    )
    present = build_universal_context(
        conversation=make_conversation(),
        trigger=trigger,
        context_messages=[prior, trigger, trigger],
        task_id=1,
        force_new_decision=False,
        dry_run=True,
    )

    assert [message.open_message_id for message in absent.context_messages] == [
        "prior-1",
        "trigger-1",
    ]
    assert [message.open_message_id for message in present.context_messages] == [
        "prior-1",
        "trigger-1",
    ]


def test_preserves_duplicate_non_trigger_messages_and_uses_actual_trigger() -> None:
    trigger = make_message("trigger-1", "Derek", "Current trigger.")
    stale_trigger = make_message("trigger-1", "Old sender", "Stale trigger.")
    repeated = make_message("repeat-1", "Alex", "Repeated context.")

    context = build_universal_context(
        conversation=make_conversation(),
        trigger=trigger,
        context_messages=[repeated, stale_trigger, repeated, stale_trigger],
        task_id=1,
        force_new_decision=False,
        dry_run=True,
    )

    assert context.context_messages == [repeated, trigger, repeated]


def test_build_does_not_mutate_callers_context_list() -> None:
    prior = make_message("prior-1", "Alex", "Earlier message.")
    original = [prior]

    build_context(original)

    assert original == [prior]


def test_render_explicitly_describes_missing_context() -> None:
    rendered = UniversalTaskContext(
        task_id=42,
        conversation_id="conversation-1",
        conversation_title="Friday planning",
        single_chat=True,
        trigger_message_id="trigger-1",
        trigger_sender="Derek",
        trigger_text="Please review this.",
        context_messages=[],
        required_dependencies=["dws"],
        force_new_decision=True,
        dry_run=False,
    ).render_for_agent()

    assert "Recent messages:\n- No context messages." in rendered
