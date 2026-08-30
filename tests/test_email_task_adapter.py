import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.agent_context import PriorReceipt
from app.email_classifier_contracts import (
    EmailAction,
    EmailAttachmentMetadata,
    EmailCategory,
    build_versioned_email_action_plan,
)
from app.email_task_adapter import (
    EmailAgentTaskAdapter,
    EmailAgentTaskInput,
    EmailAgentTaskMetadataError,
    EmailThreadMessage,
    email_action_identity,
    email_conversation_id,
)
from app.store import AutoReplyStore


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "email-agent.sqlite3")


def _plan(
    actions: tuple[EmailAction, ...],
    *,
    version: int = 1,
    account_id: str = "account-primary",
    instruction: str = "Reply in Chinese and acknowledge receipt.",
    classification_source: str = "model",
):
    parameters = {}
    for action in actions:
        if action is EmailAction.AUTO_REPLY:
            parameters[action] = {"instruction": instruction}
        elif action is EmailAction.LABEL:
            parameters[action] = {"labels": ["Work"]}
        else:
            parameters[action] = {}
    return build_versioned_email_action_plan(
        action_plan_version=version,
        classification_id=41,
        account_id=account_id,
        category=EmailCategory.WORK,
        classification_source=classification_source,
        confidence=0.98,
        model_id="email-model:2026-08-30:sha256:test",
        config_version="email-config:v7",
        actions=actions,
        action_parameters=parameters,
        created_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
    )


def _task_input(
    *,
    account_id: str = "account-primary",
) -> EmailAgentTaskInput:
    stable_identity = f"{account_id}:message-id:<mail-41@example.com>"
    return EmailAgentTaskInput(
        stable_message_identity=stable_identity,
        thread_identity="thread-customer-41",
        subject="Re: 合同确认",
        trigger=EmailThreadMessage(
            message_id=stable_identity,
            sender="customer@example.com",
            text=(
                "请确认合同。退订入口 https://example.com/unsubscribe?token=private-token "
                "以及 password=do-not-persist /Users/derek/private/attachment.bin"
            ),
            create_time="2026-08-30T08:00:00+00:00",
        ),
        thread_messages=(
            EmailThreadMessage(
                message_id=f"{account_id}:message-id:<mail-40@example.com>",
                sender="derek@stardust.ai",
                text="上一封邮件的纯文本回复。",
                create_time="2026-08-29T08:00:00+00:00",
            ),
        ),
        attachments=(
            EmailAttachmentMetadata(
                filename="contract.pdf",
                mime_type="application/pdf",
                size_bytes=1234,
                inline=False,
            ),
        ),
        prior_receipts=(
            PriorReceipt(
                receipt_id="sent-state-1",
                operation="sent_state_readback",
                summary="No equivalent reply exists.",
                completed=True,
            ),
        ),
    )


def test_only_agent_actions_create_idempotent_email_reply_tasks(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan(
        (
            EmailAction.LABEL,
            EmailAction.MARK_READ,
            EmailAction.AUTO_REPLY,
            EmailAction.UNSUBSCRIBE,
        )
    )
    task_input = _task_input()
    adapter = EmailAgentTaskAdapter(store)

    first = adapter.ensure_action_plan_tasks(plan, task_input)
    replay = adapter.ensure_action_plan_tasks(plan, task_input)

    assert [route.action_type for route in first] == [
        EmailAction.AUTO_REPLY,
        EmailAction.UNSUBSCRIBE,
    ]
    assert [route.task.id for route in replay] == [route.task.id for route in first]
    assert store.count_reply_tasks(channel="email") == 2
    assert {route.task.channel for route in first} == {"email"}
    assert {route.task.status for route in first} == {"pending"}
    assert store.count_sent_replies() == 0
    assert all(
        store.list_agent_runs_for_task_generation(
            route.task.id,
            route.task.execution_generation,
        )
        == []
        for route in first
    )
    assert {route.task.conversation_id for route in first} == {
        email_conversation_id(plan.account_id, task_input.thread_identity)
    }
    for route in first:
        assert route.task.trigger_message_id == email_action_identity(
            account_id=plan.account_id,
            stable_message_identity=task_input.stable_message_identity,
            action_type=route.action_type,
            action_plan_version=plan.action_plan_version,
        )


def test_classification_with_zero_agent_actions_creates_no_reply_task(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan(
        (EmailAction.LABEL, EmailAction.ARCHIVE),
        classification_source="user",
    )

    routes = EmailAgentTaskAdapter(store).ensure_action_plan_tasks(
        plan,
        _task_input(),
    )

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_action_plan_version_and_account_are_part_of_task_identity(tmp_path: Path):
    store = _store(tmp_path)
    adapter = EmailAgentTaskAdapter(store)
    first_plan = _plan((EmailAction.AUTO_REPLY,), version=1)
    second_plan = _plan((EmailAction.AUTO_REPLY,), version=2)
    other_account_plan = _plan(
        (EmailAction.AUTO_REPLY,),
        version=1,
        account_id="account-secondary",
    )

    first = adapter.ensure_action_plan_tasks(first_plan, _task_input())[0]
    second = adapter.ensure_action_plan_tasks(second_plan, _task_input())[0]
    other = adapter.ensure_action_plan_tasks(
        other_account_plan,
        _task_input(account_id="account-secondary"),
    )[0]

    assert len({first.task.id, second.task.id, other.task.id}) == 3
    assert len(
        {
            first.task.trigger_message_id,
            second.task.trigger_message_id,
            other.task.trigger_message_id,
        }
    ) == 3


def test_persisted_payload_is_traceable_without_message_secrets_or_attachments(
    tmp_path: Path,
):
    route = EmailAgentTaskAdapter(_store(tmp_path)).ensure_action_plan_tasks(
        _plan((EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE)),
        _task_input(),
    )[0]

    payload = json.loads(route.task.trigger_message_json)
    encoded = route.task.trigger_message_json

    assert payload["schema"] == "email_agent_action.v1"
    assert payload["account_id"] == "account-primary"
    assert payload["action_plan_version"] == 1
    assert payload["action_type"] == "auto_reply"
    assert payload["action_identity"] == route.task.trigger_message_id
    assert payload["model_id"] == "email-model:2026-08-30:sha256:test"
    assert payload["action_parameters"] == {
        "instruction": "Reply in Chinese and acknowledge receipt."
    }
    for forbidden in (
        "private-token",
        "do-not-persist",
        "/Users/derek/private",
        "attachment.bin",
        "contract.pdf",
        "application/pdf",
    ):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    "unsafe_instruction",
    (
        "Use password=do-not-store",
        "Read /Users/derek/private/reply.txt before replying",
        "Open https://example.com/unsubscribe?token=private-token",
    ),
)
def test_unsafe_action_metadata_is_rejected_before_task_persistence(
    tmp_path: Path,
    unsafe_instruction: str,
):
    store = _store(tmp_path)

    with pytest.raises(EmailAgentTaskMetadataError):
        EmailAgentTaskAdapter(store).ensure_action_plan_tasks(
            _plan((EmailAction.AUTO_REPLY,), instruction=unsafe_instruction),
            _task_input(),
        )

    assert store.count_reply_tasks(channel="email") == 0


def test_local_path_in_trace_identity_is_rejected_before_persistence(tmp_path: Path):
    store = _store(tmp_path)

    with pytest.raises(EmailAgentTaskMetadataError):
        EmailAgentTaskAdapter(store).ensure_action_plan_tasks(
            _plan((EmailAction.AUTO_REPLY,)),
            replace(
                _task_input(),
                thread_identity="/Users/derek/private/provider-thread.json",
            ),
        )

    assert store.count_reply_tasks(channel="email") == 0


def test_unsafe_prior_receipt_is_rejected_before_context_or_task_persistence(
    tmp_path: Path,
):
    store = _store(tmp_path)
    unsafe_receipt = PriorReceipt(
        receipt_id="receipt-unsafe",
        operation="sent_state_readback",
        summary="authorization_token=do-not-persist",
        completed=False,
    )

    with pytest.raises(EmailAgentTaskMetadataError):
        EmailAgentTaskAdapter(store).ensure_action_plan_tasks(
            _plan((EmailAction.AUTO_REPLY,)),
            replace(_task_input(), prior_receipts=(unsafe_receipt,)),
        )

    assert store.count_reply_tasks(channel="email") == 0


def test_email_context_contains_text_metadata_receipts_and_no_image_inputs(
    tmp_path: Path,
):
    route = EmailAgentTaskAdapter(_store(tmp_path)).ensure_action_plan_tasks(
        _plan((EmailAction.AUTO_REPLY,)),
        _task_input(),
    )[0]
    context = route.context
    rendered = context.render_business_context(
        current_time="2026-08-30T09:00:00+00:00"
    )

    assert context.channel == "email"
    assert context.image_paths == ()
    assert context.image_sha256s == ()
    assert [message.text for message in context.messages] == [
        "上一封邮件的纯文本回复。",
        _task_input().trigger.text,
    ]
    assert len(context.materials) == 1
    assert context.materials[0].kind == "email_attachment_metadata"
    assert context.materials[0].read_commands == ()
    assert "contract.pdf" in context.materials[0].reference
    assert "application/pdf" in context.materials[0].reference
    assert "Safe prior execution receipts" in rendered
    assert "sent_state_readback" in rendered
    assert "attachment.bin" in rendered  # email text is allowed as text evidence
    assert "Actual Codex image inputs" not in rendered
    assert "read attachment" not in rendered.casefold()
