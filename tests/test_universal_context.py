import hashlib
from dataclasses import replace

import pytest

from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.universal_context import (
    UniversalContextMessage,
    UniversalTaskContext,
    build_universal_context,
    canonical_universal_context_json,
    universal_context_sha256,
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
    assert context.execution_generation == "initial"
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
        "Execution generation: initial\n"
        "Force new decision: true\n"
        "Dry run: false\n"
        "Recent messages:\n"
        "- Alex (prior-1): The previous decision was recorded.\n"
        "- Derek (trigger-1): Please review this."
    )


def test_dws_is_required_for_dingtalk_context() -> None:
    assert build_context([]).required_dependencies == ("dws",)


def test_explicit_execution_generation_is_snapshotted_and_rendered() -> None:
    context = build_universal_context(
        conversation=make_conversation(),
        trigger=make_message("trigger-1", "Derek", "Please review this."),
        context_messages=[],
        task_id=42,
        force_new_decision=True,
        dry_run=False,
        execution_generation="manual-rerun-2",
    )

    assert context.execution_generation == "manual-rerun-2"
    assert "Execution generation: manual-rerun-2" in context.render_for_agent()


def test_empty_execution_generation_is_rejected() -> None:
    with pytest.raises(ValueError, match="execution_generation must be non-empty"):
        UniversalTaskContext(
            task_id=42,
            conversation_id="conversation-1",
            conversation_title="Friday planning",
            single_chat=True,
            trigger_message_id="trigger-1",
            trigger_sender="Derek",
            trigger_text="Please review this.",
            context_messages=(),
            required_dependencies=("dws",),
            force_new_decision=True,
            dry_run=False,
            execution_generation="   ",
        )


def test_canonical_context_json_covers_every_field_with_stable_order() -> None:
    context = UniversalTaskContext(
        task_id=42,
        conversation_id="conversation-1",
        conversation_title="Friday planning",
        single_chat=True,
        trigger_message_id="trigger-1",
        trigger_sender="Derek",
        trigger_text="Please review this.",
        context_messages=(
            UniversalContextMessage("Alex", "prior-1", "Earlier message."),
            UniversalContextMessage("Derek", "trigger-1", "Please review this."),
        ),
        required_dependencies=("dws", "memory"),
        force_new_decision=True,
        dry_run=False,
        execution_generation="manual-rerun-2",
    )

    canonical = canonical_universal_context_json(context)

    assert canonical == (
        '{"context_messages":[{"content":"Earlier message.",'
        '"open_message_id":"prior-1","sender_name":"Alex"},'
        '{"content":"Please review this.","open_message_id":"trigger-1",'
        '"sender_name":"Derek"}],"conversation_id":"conversation-1",'
        '"conversation_title":"Friday planning","dry_run":false,'
        '"execution_generation":"manual-rerun-2","force_new_decision":true,'
        '"required_dependencies":["dws","memory"],"single_chat":true,'
        '"task_id":42,"trigger_message_id":"trigger-1",'
        '"trigger_sender":"Derek","trigger_text":"Please review this."}'
    )
    assert universal_context_sha256(context) == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    assert canonical_universal_context_json(replace(context)) == canonical


def test_canonical_context_identity_preserves_message_and_dependency_order() -> None:
    context = build_context([make_message("prior-1", "Alex", "Earlier message.")])
    reversed_messages = replace(
        context,
        context_messages=tuple(reversed(context.context_messages)),
    )
    reordered_dependencies = replace(
        context,
        required_dependencies=("memory", "dws"),
    )

    assert universal_context_sha256(reversed_messages) != universal_context_sha256(
        context
    )
    assert universal_context_sha256(reordered_dependencies) != universal_context_sha256(
        context
    )


def test_canonical_context_json_rejects_non_strict_field_types() -> None:
    context = build_context([])

    with pytest.raises(TypeError, match="context_messages must be a tuple"):
        canonical_universal_context_json(
            replace(context, context_messages=list(context.context_messages))
        )
    with pytest.raises(TypeError, match="task_id must be an int"):
        canonical_universal_context_json(replace(context, task_id=True))


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

    assert context.context_messages == (
        UniversalContextMessage("Alex", "repeat-1", "Repeated context."),
        UniversalContextMessage("Derek", "trigger-1", "Current trigger."),
        UniversalContextMessage("Alex", "repeat-1", "Repeated context."),
    )


def test_snapshot_membership_cannot_be_mutated() -> None:
    context = build_context([make_message("prior-1", "Alex", "Earlier message.")])

    assert isinstance(context.context_messages, tuple)
    assert isinstance(context.required_dependencies, tuple)
    with pytest.raises(AttributeError):
        context.context_messages.append(
            UniversalContextMessage("Alex", "new-1", "New message.")
        )
    with pytest.raises(AttributeError):
        context.required_dependencies.append("memory")


def test_snapshot_values_do_not_change_when_original_messages_are_mutated() -> None:
    trigger = make_message("trigger-1", "Derek", "Current trigger.")
    prior = make_message("prior-1", "Alex", "Earlier message.")
    context = build_universal_context(
        conversation=make_conversation(),
        trigger=trigger,
        context_messages=[prior],
        task_id=1,
        force_new_decision=False,
        dry_run=False,
    )

    trigger.sender_name = "Changed sender"
    trigger.content = "Changed trigger"
    prior.sender_name = "Changed prior sender"
    prior.content = "Changed prior"

    assert context.trigger_sender == "Derek"
    assert context.trigger_text == "Current trigger."
    assert context.context_messages[-1] == UniversalContextMessage(
        "Derek", "trigger-1", "Current trigger."
    )
    assert "- Derek (trigger-1): Current trigger." in context.render_for_agent()


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
        context_messages=(),
        required_dependencies=("dws",),
        force_new_decision=True,
        dry_run=False,
    ).render_for_agent()

    assert "Recent messages:\n- No context messages." in rendered
