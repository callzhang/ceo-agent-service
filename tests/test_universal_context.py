import hashlib
import json
from dataclasses import replace

import pytest

from app.dingtalk_models import DingTalkConversation, DingTalkMessage
from app.store import AutoReplyStore
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
        "Trusted OA process instance ID: none\n"
        "Trusted OA task ID: none\n"
        "Trusted mail target: none\n"
        "Trusted calendar target: none\n"
        "Required dependencies: dws\n"
        "Execution generation: initial\n"
        "Force new decision: true\n"
        "Dry run: false\n"
        "Recent messages:\n"
        "- Alex (prior-1): The previous decision was recorded.\n"
        "- Derek (trigger-1): Please review this."
    )


def test_snapshots_every_behaviorally_relevant_message_field() -> None:
    trigger = DingTalkMessage(
        open_conversation_id="conversation-1",
        open_message_id="trigger-rich",
        conversation_title="Friday planning",
        single_chat=False,
        sender_name="Derek",
        sender_open_dingtalk_id="open-derek",
        sender_user_id="user-derek",
        message_type="text",
        create_time="2026-07-20 10:01:02",
        content="Please review this.",
        mentioned_user_ids=["user-alex", "user-bob"],
        quoted_message_id="quoted-1",
        quoted_content="Quoted content",
        raw_payload={
            "ceo_agent_source": "robot_direct",
            "nested": {"z": 2, "a": 1},
        },
    )

    context = build_universal_context(
        conversation=make_conversation(),
        trigger=trigger,
        context_messages=[],
        task_id=42,
        force_new_decision=False,
        dry_run=False,
    )

    snapshot = context.context_messages[-1]
    assert snapshot.sender_open_dingtalk_id == "open-derek"
    assert snapshot.sender_user_id == "user-derek"
    assert snapshot.message_type == "text"
    assert snapshot.create_time == "2026-07-20 10:01:02"
    assert snapshot.mentioned_user_ids == ("user-alex", "user-bob")
    assert snapshot.quoted_message_id == "quoted-1"
    assert snapshot.quoted_content == "Quoted content"
    assert json.loads(snapshot.raw_payload_json) == trigger.raw_payload
    canonical = json.loads(canonical_universal_context_json(context))
    assert canonical["context_messages"][-1] == {
        "content": "Please review this.",
        "create_time": "2026-07-20 10:01:02",
        "mentioned_user_ids": ["user-alex", "user-bob"],
        "message_type": "text",
        "open_message_id": "trigger-rich",
        "quoted_content": "Quoted content",
        "quoted_message_id": "quoted-1",
        "raw_payload": {
            "ceo_agent_source": "robot_direct",
            "nested": {"a": 1, "z": 2},
        },
        "sender_name": "Derek",
        "sender_open_dingtalk_id": "open-derek",
        "sender_user_id": "user-derek",
    }


def test_dws_is_required_for_dingtalk_context() -> None:
    assert build_context([]).required_dependencies == ("dws",)


def test_build_derives_trusted_oa_target_from_trigger_payload() -> None:
    trigger = make_message("trigger-oa", "Derek", "Please review this approval.")
    trigger.raw_payload = {
        "processInstanceId": "proc-trusted",
        "taskId": "task-trusted",
    }

    context = build_universal_context(
        conversation=make_conversation(),
        trigger=trigger,
        context_messages=[],
        task_id=42,
        force_new_decision=False,
        dry_run=False,
    )

    assert context.trusted_oa_process_instance_id == "proc-trusted"
    assert context.trusted_oa_task_id == "task-trusted"
    assert "Trusted OA process instance ID: proc-trusted" in context.render_for_agent()
    assert "Trusted OA task ID: task-trusted" in context.render_for_agent()


def test_build_derives_trusted_oa_target_from_reply_task_url() -> None:
    context = build_universal_context(
        conversation=make_conversation(),
        trigger=make_message("trigger-oa", "Derek", "Approval notification"),
        context_messages=[],
        task_id=42,
        force_new_decision=False,
        dry_run=False,
        reply_task_oa_url=(
            "https://aflow.dingtalk.com/dingtalk/web/query/pchomepage.htm"
            "?procInstId=proc-url&taskId=task-url"
        ),
    )

    assert context.trusted_oa_process_instance_id == "proc-url"
    assert context.trusted_oa_task_id == "task-url"


def test_conflicting_trusted_oa_sources_fail_closed() -> None:
    trigger = make_message("trigger-oa", "Derek", "Approval notification")
    trigger.raw_payload = {
        "processInstanceId": "proc-payload",
        "taskId": "task-payload",
    }

    context = build_universal_context(
        conversation=make_conversation(),
        trigger=trigger,
        context_messages=[],
        task_id=42,
        force_new_decision=False,
        dry_run=False,
        reply_task_oa_url=(
            "https://aflow.dingtalk.com/dingtalk/web/query/pchomepage.htm"
            "?procInstId=proc-url&taskId=task-url"
        ),
    )

    assert context.trusted_oa_process_instance_id == ""
    assert context.trusted_oa_task_id == ""


def test_trusted_oa_target_changes_canonical_identity() -> None:
    context = build_context([])
    trusted = replace(
        context,
        trusted_oa_process_instance_id="proc-1",
        trusted_oa_task_id="task-1",
    )

    assert universal_context_sha256(trusted) != universal_context_sha256(context)


def test_build_derives_trusted_mail_target_from_trigger_payload() -> None:
    trigger = make_message("trigger-mail", "Mail", "Mail notification")
    trigger.raw_payload = {
        "mail": {
            "mailbox": "derek@example.com",
            "messageId": "mail-1",
            "subject": "Approval request",
        }
    }

    context = build_universal_context(
        conversation=make_conversation(),
        trigger=trigger,
        context_messages=[],
        task_id=42,
        force_new_decision=False,
        dry_run=False,
    )

    assert context.trusted_mail_mailbox == "derek@example.com"
    assert context.trusted_mail_message_id == "mail-1"
    assert context.trusted_mail_subject == "Approval request"


def test_build_derives_trusted_calendar_target_and_status_from_trigger_payload() -> None:
    trigger = make_message("trigger-calendar", "Calendar", "Calendar invitation")
    trigger.raw_payload = {
        "calendarEvent": {
            "eventId": "event-1",
            "summary": "Strategy review",
            "start": {"dateTime": "2026-07-21T10:00:00+08:00"},
            "end": {"dateTime": "2026-07-21T11:00:00+08:00"},
            "organizer": {"displayName": "Mina"},
            "selfResponseStatus": "tentative",
        }
    }

    context = build_universal_context(
        conversation=make_conversation(),
        trigger=trigger,
        context_messages=[],
        task_id=42,
        force_new_decision=False,
        dry_run=False,
    )

    assert context.trusted_calendar_event_id == "event-1"
    assert context.trusted_calendar_response_status == "tentative"
    assert context.trusted_calendar_organizer == "Mina"


def test_trusted_mail_and_calendar_targets_change_canonical_identity() -> None:
    context = build_context([])
    trusted_mail = replace(
        context,
        trusted_mail_mailbox="derek@example.com",
        trusted_mail_message_id="mail-1",
        trusted_mail_subject="Subject",
    )
    trusted_calendar = replace(
        context,
        trusted_calendar_event_id="event-1",
        trusted_calendar_response_status="accepted",
        trusted_calendar_organizer="Mina",
    )

    assert universal_context_sha256(trusted_mail) != universal_context_sha256(context)
    assert universal_context_sha256(trusted_calendar) != universal_context_sha256(context)


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

    assert canonical == json.dumps(
        {
            "context_messages": [
                {
                    "content": "Earlier message.",
                    "create_time": "",
                    "mentioned_user_ids": [],
                    "message_type": None,
                    "open_message_id": "prior-1",
                    "quoted_content": None,
                    "quoted_message_id": None,
                    "raw_payload": {},
                    "sender_name": "Alex",
                    "sender_open_dingtalk_id": None,
                    "sender_user_id": None,
                },
                {
                    "content": "Please review this.",
                    "create_time": "",
                    "mentioned_user_ids": [],
                    "message_type": None,
                    "open_message_id": "trigger-1",
                    "quoted_content": None,
                    "quoted_message_id": None,
                    "raw_payload": {},
                    "sender_name": "Derek",
                    "sender_open_dingtalk_id": None,
                    "sender_user_id": None,
                },
            ],
            "conversation_id": "conversation-1",
            "conversation_title": "Friday planning",
            "dry_run": False,
            "execution_generation": "manual-rerun-2",
            "force_new_decision": True,
            "required_dependencies": ["dws", "memory"],
            "single_chat": True,
            "task_id": 42,
            "trigger_message_id": "trigger-1",
            "trigger_sender": "Derek",
            "trigger_text": "Please review this.",
            "trusted_oa_process_instance_id": "",
            "trusted_oa_task_id": "",
            "trusted_mail_mailbox": "",
            "trusted_mail_message_id": "",
            "trusted_mail_subject": "",
            "trusted_calendar_event_id": "",
            "trusted_calendar_response_status": "",
            "trusted_calendar_organizer": "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
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


def test_canonical_context_identity_covers_delivery_metadata() -> None:
    context = build_context([])
    trigger = context.context_messages[-1]
    changed_sender_identity = replace(
        context,
        context_messages=(
            replace(trigger, sender_open_dingtalk_id="different-open-id"),
        ),
    )
    changed_raw_payload = replace(
        context,
        context_messages=(replace(trigger, raw_payload_json='{"source":"task"}'),),
    )

    assert universal_context_sha256(changed_sender_identity) != universal_context_sha256(
        context
    )
    assert universal_context_sha256(changed_raw_payload) != universal_context_sha256(
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
        UniversalContextMessage(
            "Alex",
            "repeat-1",
            "Repeated context.",
            create_time="2026-07-20 10:00:00",
        ),
        UniversalContextMessage(
            "Derek",
            "trigger-1",
            "Current trigger.",
            create_time="2026-07-20 10:00:00",
        ),
        UniversalContextMessage(
            "Alex",
            "repeat-1",
            "Repeated context.",
            create_time="2026-07-20 10:00:00",
        ),
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
    trigger.mentioned_user_ids = ["mentioned-before"]
    trigger.raw_payload = {"nested": {"before": True}}
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
    trigger.mentioned_user_ids.append("mentioned-after")
    trigger.raw_payload["nested"]["before"] = False
    prior.sender_name = "Changed prior sender"
    prior.content = "Changed prior"

    assert context.trigger_sender == "Derek"
    assert context.trigger_text == "Current trigger."
    assert context.context_messages[-1] == UniversalContextMessage(
        "Derek",
        "trigger-1",
        "Current trigger.",
        create_time="2026-07-20 10:00:00",
        mentioned_user_ids=("mentioned-before",),
        raw_payload_json='{"nested":{"before":true}}',
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


def test_reply_task_trigger_json_can_build_complete_universal_snapshot(
    tmp_path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger = DingTalkMessage(
        open_conversation_id="conversation-1",
        open_message_id="trigger-from-task",
        conversation_title="Friday planning",
        single_chat=False,
        sender_name="Derek",
        sender_open_dingtalk_id="open-from-task",
        sender_user_id="user-from-task",
        message_type="text",
        create_time="2026-07-20 12:34:56",
        content="Handle from durable task",
        mentioned_user_ids=["mentioned-from-task"],
        quoted_message_id="quoted-from-task",
        quoted_content="quoted body",
        raw_payload={"source": "reply_task"},
    )
    assert store.enqueue_reply_task(
        conversation_id=trigger.open_conversation_id,
        conversation_title=trigger.conversation_title,
        single_chat=trigger.single_chat,
        trigger_message_id=trigger.open_message_id,
        trigger_create_time=trigger.create_time,
        trigger_sender=trigger.sender_name,
        trigger_text=trigger.content,
        trigger_message_json=trigger.model_dump_json(),
    )
    task = store.claim_reply_tasks(limit=1)[0]
    durable_trigger = DingTalkMessage.model_validate_json(task.trigger_message_json)

    context = build_universal_context(
        conversation=make_conversation().model_copy(update={"single_chat": False}),
        trigger=durable_trigger,
        context_messages=[],
        task_id=task.id,
        force_new_decision=False,
        dry_run=False,
        execution_generation=task.execution_generation,
    )

    snapshot = context.context_messages[-1]
    assert snapshot.sender_open_dingtalk_id == "open-from-task"
    assert snapshot.sender_user_id == "user-from-task"
    assert snapshot.create_time == "2026-07-20 12:34:56"
    assert snapshot.mentioned_user_ids == ("mentioned-from-task",)
    assert snapshot.quoted_message_id == "quoted-from-task"
    assert json.loads(snapshot.raw_payload_json) == {"source": "reply_task"}
