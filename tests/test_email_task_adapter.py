from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from urllib.parse import quote

import pytest

from app.agent_context import PriorReceipt
from app.agent_contracts import ProposedAction
from app.email_classifier_contracts import (
    EmailAction,
    EmailAttachmentMetadata,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_store import EmailStore
from app.email_task_adapter import (
    EmailAgentTaskAdapter,
    EmailAgentTaskConflict,
    EmailAgentTaskInput,
    EmailAgentTaskMetadataError,
    EmailThreadMessage,
    _assert_safe_email_metadata,
    accepted_email_unsubscribe_effect,
    email_action_identity,
    email_conversation_id,
)
from app.email_unsubscribe import UnsubscribeAuthenticationEvidence
from app.store import AutoReplyStore


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "email-agent.sqlite3")


def _plan(
    actions: tuple[EmailAction, ...],
    *,
    version: int = 1,
    classification_id: int = 41,
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
        classification_id=classification_id,
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


def _email_store(tmp_path: Path) -> EmailStore:
    return EmailStore(tmp_path / "email-agent.sqlite3")


def _percent_encode(value: str, rounds: int) -> str:
    encoded = value
    for _ in range(rounds):
        encoded = quote(encoded, safe="")
    return encoded


def _persist_authorization(
    store: EmailStore,
    plan,
    task_input: EmailAgentTaskInput,
) -> None:
    initial_plan = plan
    if plan.action_plan_version > 1:
        initial_plan = build_versioned_email_action_plan(
            action_plan_version=1,
            classification_id=plan.classification_id,
            account_id=plan.account_id,
            category=plan.category,
            classification_source=plan.classification_source,
            confidence=plan.confidence,
            model_id=plan.model_id,
            config_version=plan.config_version,
            actions=plan.actions,
            action_parameters=plan.action_parameters,
            created_at=plan.created_at,
        )
    store.upsert_classification(
        EmailClassification.model_validate(
            {
                "classification_id": plan.classification_id,
                "stable_message_identity": task_input.stable_message_identity,
                "provider_locator": {
                    "account_id": plan.account_id,
                    "folder": "INBOX",
                    "uidvalidity": 42,
                    "uid": plan.classification_id,
                    "rfc_message_id": "<mail-41@example.com>",
                    "thread_id": task_input.thread_identity,
                },
                "category": plan.category,
                "confidence": plan.confidence,
                "margin": 0.42,
                "probabilities": {plan.category.value: plan.confidence},
                "model_id": plan.model_id,
                "config_version": plan.config_version,
                "status": EmailClassificationStatus.PROCESSED,
                "classification_source": plan.classification_source,
                "action_plan": initial_plan,
            }
        ),
        sender=task_input.trigger.sender,
        subject=task_input.subject,
        model_text="__subject__contract confirmation",
        received_at=task_input.trigger.create_time,
    )
    if plan.action_plan_version > 1:
        store.append_action_plan_version(
            plan.classification_id,
            plan,
            confirmed_category=plan.category,
        )


def _authorized_adapter(
    tmp_path: Path,
    plan,
    task_input: EmailAgentTaskInput,
    *,
    task_store: AutoReplyStore | None = None,
    email_store: EmailStore | None = None,
) -> EmailAgentTaskAdapter:
    durable_email_store = email_store or _email_store(tmp_path)
    _persist_authorization(durable_email_store, plan, task_input)
    return EmailAgentTaskAdapter(
        task_store or _store(tmp_path),
        durable_email_store,
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
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    )

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

    routes = EmailAgentTaskAdapter(store, _email_store(tmp_path)).ensure_action_plan_tasks(
        plan,
        _task_input(),
    )

    assert routes == ()
    assert store.count_reply_tasks(channel="email") == 0


def test_adapter_requires_one_database_for_atomic_email_authorization(
    tmp_path: Path,
):
    task_store = AutoReplyStore(tmp_path / "tasks.sqlite3")
    email_store = EmailStore(tmp_path / "email.sqlite3")

    with pytest.raises(ValueError, match="share one database"):
        EmailAgentTaskAdapter(task_store, email_store)


def test_action_plan_version_and_account_are_part_of_task_identity(tmp_path: Path):
    store = _store(tmp_path)
    email_store = _email_store(tmp_path)
    first_plan = _plan((EmailAction.AUTO_REPLY,), version=1)
    second_plan = _plan((EmailAction.AUTO_REPLY,), version=2)
    other_account_plan = _plan(
        (EmailAction.AUTO_REPLY,),
        version=1,
        classification_id=42,
        account_id="account-secondary",
    )

    adapter = _authorized_adapter(
        tmp_path,
        first_plan,
        _task_input(),
        task_store=store,
        email_store=email_store,
    )
    first = adapter.ensure_action_plan_tasks(first_plan, _task_input())[0]
    _persist_authorization(email_store, second_plan, _task_input())
    second = adapter.ensure_action_plan_tasks(second_plan, _task_input())[0]
    _persist_authorization(
        email_store,
        other_account_plan,
        _task_input(account_id="account-secondary"),
    )
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
    plan = _plan((EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE))
    task_input = _task_input()
    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
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


def test_unsubscribe_task_projects_only_redacted_real_entry_references(
    tmp_path: Path,
) -> None:
    plan = _plan((EmailAction.UNSUBSCRIBE,))
    private_url = "https://news.example.com/unsubscribe?token=private-token"
    task_input = replace(
        _task_input(),
        list_unsubscribe=f"<{private_url}>",
        list_unsubscribe_post="List-Unsubscribe=One-Click",
        unsubscribe_authentication=UnsubscribeAuthenticationEvidence(
            dkim_covers_list_unsubscribe=True,
            dkim_covers_list_unsubscribe_post=True,
            evidence_reference="dkim-evidence:mail-41",
        ),
    )

    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
    )[0]
    payload = json.loads(route.task.trigger_message_json)
    entries = payload["unsubscribe_entries"]

    assert entries == [
        {
            "priority": 0,
            "reference": entries[0]["reference"],
            "source": "header_one_click_https",
        }
    ]
    assert entries[0]["reference"].startswith("unsubscribe-entry:")
    assert payload["unsubscribe_authentication"] == {
        "evidence_reference": "dkim-evidence:mail-41",
        "one_click_verified": True,
    }
    assert private_url not in route.task.trigger_message_json
    assert private_url not in repr(task_input)

    accepted = ProposedAction.model_validate(
        {
            "description": "Unsubscribe the current sender",
            "capability": "email_browser",
            "operation": "unsubscribe",
            "target": {
                "action_identity": payload["action_identity"],
                "account_id": payload["account_id"],
                "stable_message_identity": payload["stable_message_identity"],
                "thread_identity": payload["thread_identity"],
                "entry_reference": entries[0]["reference"],
                "network_policy_reference": payload[
                    "unsubscribe_network_policy_reference"
                ],
                "network_policy_origin_references": payload[
                    "unsubscribe_network_policy_origin_references"
                ],
            },
            "payload": {
                "operations": [
                    {
                        "operation_reference": "step-1",
                        "kind": "post_one_click",
                        "target_reference": entries[0]["reference"],
                    }
                ]
            },
            "expected_verification": "Read terminal provider evidence.",
        }
    )

    effect = accepted_email_unsubscribe_effect(route.task, accepted)
    assert effect.entry_reference == entries[0]["reference"]


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
    plan = _plan((EmailAction.AUTO_REPLY,), instruction=unsafe_instruction)
    task_input = _task_input()

    with pytest.raises(EmailAgentTaskMetadataError):
        _authorized_adapter(
            tmp_path,
            plan,
            task_input,
            task_store=store,
        ).ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 0


def test_local_path_in_trace_identity_is_rejected_before_persistence(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.AUTO_REPLY,))
    task_input = replace(
        _task_input(),
        thread_identity="/Users/derek/private/provider-thread.json",
    )

    with pytest.raises(EmailAgentTaskMetadataError):
        _authorized_adapter(
            tmp_path,
            plan,
            task_input,
            task_store=store,
        ).ensure_action_plan_tasks(plan, task_input)

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
    plan = _plan((EmailAction.AUTO_REPLY,))
    task_input = replace(_task_input(), prior_receipts=(unsafe_receipt,))

    with pytest.raises(EmailAgentTaskMetadataError):
        _authorized_adapter(
            tmp_path,
            plan,
            task_input,
            task_store=store,
        ).ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 0


def test_email_context_contains_text_metadata_receipts_and_no_image_inputs(
    tmp_path: Path,
):
    plan = _plan((EmailAction.AUTO_REPLY,))
    task_input = _task_input()
    route = _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
        plan, task_input
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


def test_task_creation_rejects_wrong_persisted_message_and_historical_plan(
    tmp_path: Path,
):
    task_store = _store(tmp_path)
    email_store = _email_store(tmp_path)
    current_plan = _plan((EmailAction.AUTO_REPLY,), version=2)
    persisted_input = _task_input()
    _persist_authorization(email_store, current_plan, persisted_input)
    adapter = EmailAgentTaskAdapter(task_store, email_store)

    wrong_message = replace(
        persisted_input,
        stable_message_identity="account-primary:message-id:<wrong@example.com>",
        trigger=replace(
            persisted_input.trigger,
            message_id="account-primary:message-id:<wrong@example.com>",
        ),
    )
    historical_plan = _plan((EmailAction.AUTO_REPLY,), version=1)

    with pytest.raises(EmailAgentTaskConflict):
        adapter.ensure_action_plan_tasks(current_plan, wrong_message)
    with pytest.raises(EmailAgentTaskConflict):
        adapter.ensure_action_plan_tasks(historical_plan, persisted_input)

    assert task_store.count_reply_tasks(channel="email") == 0


def test_current_plan_switch_cannot_interleave_after_authorization_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = tmp_path / "email-agent.sqlite3"
    task_store = _store(tmp_path)
    email_store = _email_store(tmp_path)
    task_input = _task_input()
    first_plan = _plan((EmailAction.AUTO_REPLY,), version=1)
    second_plan = _plan((EmailAction.AUTO_REPLY,), version=2)
    _persist_authorization(email_store, first_plan, task_input)
    adapter = EmailAgentTaskAdapter(task_store, email_store)
    authorization_checked = Event()
    allow_insert = Event()

    def observed_authorization_read(db, classification_id: int):
        row = db.execute(
            """
            select
                classifications.id as classification_id,
                classifications.account_id as account_id,
                classifications.stable_message_identity
                    as stable_message_identity,
                messages.thread_identity as thread_identity,
                classifications.current_action_plan_id
                    as current_action_plan_id
            from email_classifications as classifications
            join email_messages as messages
              on messages.account_id=classifications.account_id
             and messages.stable_message_identity=
                 classifications.stable_message_identity
            where classifications.id=?
              and classifications.status='processed'
              and classifications.current_action_plan_id is not null
            """,
            (classification_id,),
        ).fetchone()
        authorization_checked.set()
        if not allow_insert.wait(timeout=2):
            raise RuntimeError("test did not release email task insertion")
        return row

    monkeypatch.setattr(
        task_store,
        "_get_current_email_task_authorization",
        observed_authorization_read,
        raising=False,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            adapter.ensure_action_plan_tasks,
            first_plan,
            task_input,
        )
        try:
            assert authorization_checked.wait(timeout=1)
            competing_email_store = EmailStore(database)

            def zero_timeout_connect() -> sqlite3.Connection:
                connection = sqlite3.connect(database, timeout=0)
                connection.execute("pragma busy_timeout = 0")
                connection.execute("pragma foreign_keys = on")
                connection.row_factory = sqlite3.Row
                return connection

            monkeypatch.setattr(
                competing_email_store,
                "_connect",
                zero_timeout_connect,
            )
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing_email_store.append_action_plan_version(
                    first_plan.classification_id,
                    second_plan,
                    confirmed_category=second_plan.category,
                )
        finally:
            allow_insert.set()
        route = future.result(timeout=2)[0]

    email_store.append_action_plan_version(
        first_plan.classification_id,
        second_plan,
        confirmed_category=second_plan.category,
    )

    assert json.loads(route.task.trigger_message_json)["action_plan_id"] == (
        first_plan.action_plan_id
    )
    assert email_store.get_classification(first_plan.classification_id)[
        "current_action_plan_id"
    ] == second_plan.action_plan_id


def test_all_agent_payloads_are_validated_before_any_task_is_persisted(
    tmp_path: Path,
):
    store = _store(tmp_path)
    plan = _plan(
        (EmailAction.UNSUBSCRIBE, EmailAction.AUTO_REPLY),
        instruction="Read ~/private/reply.txt before replying",
    )
    task_input = _task_input()

    with pytest.raises(EmailAgentTaskMetadataError):
        _authorized_adapter(
            tmp_path,
            plan,
            task_input,
            task_store=store,
        ).ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 0


def test_agent_action_identity_conflict_rolls_back_the_whole_plan(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan((EmailAction.AUTO_REPLY, EmailAction.UNSUBSCRIBE))
    task_input = _task_input()
    conversation_id = email_conversation_id(
        plan.account_id,
        task_input.thread_identity,
    )
    unsubscribe_identity = email_action_identity(
        account_id=plan.account_id,
        stable_message_identity=task_input.stable_message_identity,
        action_type=EmailAction.UNSUBSCRIBE,
        action_plan_version=plan.action_plan_version,
    )
    auto_reply_identity = email_action_identity(
        account_id=plan.account_id,
        stable_message_identity=task_input.stable_message_identity,
        action_type=EmailAction.AUTO_REPLY,
        action_plan_version=plan.action_plan_version,
    )
    store.ensure_reply_task(
        channel="email",
        conversation_id=conversation_id,
        conversation_title="Conflicting historical input",
        single_chat=False,
        trigger_message_id=unsubscribe_identity,
        trigger_create_time=task_input.trigger.create_time,
        trigger_sender=task_input.trigger.sender,
        trigger_text="Conflicting historical input",
        trigger_message_json=json.dumps({"action_identity": "different"}),
    )
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    )

    with pytest.raises(EmailAgentTaskConflict):
        adapter.ensure_action_plan_tasks(plan, task_input)

    assert store.count_reply_tasks(channel="email") == 1
    with sqlite3.connect(tmp_path / "email-agent.sqlite3") as db:
        assert (
            db.execute(
                "select count(*) from reply_tasks where trigger_message_id=?",
                (auto_reply_identity,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "unsafe_metadata",
    (
        {"outer": [{"path": "~/private/reply.txt"}]},
        {"outer": {"path": "~someone/private/reply.txt"}},
        {"outer": [{"uri": "FiLe:///Users/derek/private/reply.txt"}]},
        {"outer": [{"uri": "file%3A///Users/derek/private/reply.txt"}]},
    ),
)
def test_nested_home_relative_paths_and_file_uris_are_rejected(
    unsafe_metadata: object,
):
    with pytest.raises(EmailAgentTaskMetadataError):
        _assert_safe_email_metadata(unsafe_metadata)


@pytest.mark.parametrize(
    "unsafe_value",
    (
        _percent_encode("file:///Users/derek/private/reply.txt", 3),
        _percent_encode("~/private/reply.txt", 3),
        _percent_encode("https://example.com/unsubscribe/confirm", 3),
        _percent_encode(
            "https://example.com/resource?token=do-not-persist",
            3,
        ),
    ),
)
def test_metadata_canonicalization_rejects_triple_encoded_unsafe_values(
    unsafe_value: str,
):
    with pytest.raises(EmailAgentTaskMetadataError) as error:
        _assert_safe_email_metadata({"outer": [{"value": unsafe_value}]})

    assert unsafe_value not in str(error.value)


def test_metadata_canonicalization_rejects_excessive_encoding_depth():
    unsafe_value = _percent_encode("file:///private/reply.txt", 10)

    with pytest.raises(EmailAgentTaskMetadataError) as error:
        _assert_safe_email_metadata(unsafe_value)

    assert unsafe_value not in str(error.value)


def test_metadata_canonicalization_rejects_oversized_text():
    oversized = "a" * 70_000

    with pytest.raises(EmailAgentTaskMetadataError):
        _assert_safe_email_metadata(oversized)


def test_safe_public_https_url_is_allowed_in_action_metadata(tmp_path: Path):
    store = _store(tmp_path)
    plan = _plan(
        (EmailAction.AUTO_REPLY,),
        instruction="Reference https://docs.example.com/help/getting-started?lang=zh.",
    )
    task_input = _task_input()

    routes = _authorized_adapter(
        tmp_path,
        plan,
        task_input,
        task_store=store,
    ).ensure_action_plan_tasks(
        plan,
        task_input,
    )

    assert len(routes) == 1


@pytest.mark.parametrize(
    "sensitive_url",
    (
        "https://example.com/resource?token=do-not-persist",
        "https://example.com/resource?X-Amz-Signature=do-not-persist",
        "https://example.com/resource?X-Amz-SignedHeaders=host",
        "https://example.com/resource#access_token=do-not-persist",
        "https://example.com/unsubscribe/confirm",
        "https://unsubscribe.example.com/confirm",
        "https://example.com/preferences?action=opt-out",
    ),
)
def test_sensitive_or_unsubscribe_url_is_rejected_without_echoing_it(
    tmp_path: Path,
    sensitive_url: str,
):
    plan = _plan(
        (EmailAction.AUTO_REPLY,),
        instruction=f"Reference {sensitive_url}",
    )
    task_input = _task_input()
    with pytest.raises(EmailAgentTaskMetadataError) as error:
        _authorized_adapter(tmp_path, plan, task_input).ensure_action_plan_tasks(
            plan, task_input
        )

    assert sensitive_url not in str(error.value)


def test_thread_identity_is_normalized_once_for_identity_payload_and_context(
    tmp_path: Path,
):
    store = _store(tmp_path)
    plan = _plan((EmailAction.AUTO_REPLY,))
    spaced = replace(_task_input(), thread_identity="  thread-customer-41  ")
    adapter = _authorized_adapter(
        tmp_path,
        plan,
        spaced,
        task_store=store,
    )

    first = adapter.ensure_action_plan_tasks(plan, spaced)[0]
    replay = adapter.ensure_action_plan_tasks(plan, _task_input())[0]

    assert first.task.id == replay.task.id
    assert json.loads(first.task.trigger_message_json)["thread_identity"] == (
        "thread-customer-41"
    )
    assert first.context.conversation_id == replay.context.conversation_id
