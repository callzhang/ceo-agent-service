from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from app.agent_contracts import ProposedAction
from app.email_classifier_contracts import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailClassificationStatus,
    build_versioned_email_action_plan,
)
from app.email_reply_delivery import (
    EmailReplyDelivery,
    EmailReplyEffect,
    OutgoingEmailReply,
    SentReply,
    SentReplyQuery,
    SmtpAcceptance,
    outgoing_message_id,
)
from app.email_store import EmailReplyDispatchConflict, EmailStore
from app.email_task_adapter import (
    EmailAgentTaskAdapter,
    EmailAgentTaskInput,
    EmailThreadMessage,
    accepted_email_reply_effect,
    email_action_identity,
)
from app.store import AutoReplyStore


ACCOUNT_VALUES = {
    "account_id": "account-primary",
    "display_name": "Primary",
    "email_address": "derek@stardust.ai",
    "imap_host": "imap.primary.example",
    "imap_port": 993,
    "imap_tls": True,
    "imap_username": "derek@stardust.ai",
    "imap_secret_reference": "keychain://imap-primary",
    "smtp_host": "smtp.primary.example",
    "smtp_port": 465,
    "smtp_tls": True,
    "smtp_username": "derek@stardust.ai",
    "smtp_secret_reference": "keychain://smtp-primary",
    "enabled": True,
    "scan_folders": ["INBOX", "Sent"],
    "scan_interval_seconds": 60,
}


class FakeSmtpSentHarness:
    def __init__(self) -> None:
        self.sent: dict[str, list[SentReply]] = {}
        self.smtp_accounts: list[tuple[str, str]] = []
        self.sent_accounts: list[tuple[str, str]] = []
        self.accepted_messages: list[OutgoingEmailReply] = []
        self.timeout_before_send = False
        self.timeout_after_acceptance = False
        self.fail_sent_reads: set[int] = set()
        self.sent_read_count = 0
        self.provider_result_id = ""
        self.smtp_acceptance_id = ""
        self.after_sent_search = None
        self.before_smtp = None
        self.crash_before_acceptance = False

    @property
    def acceptance_count(self) -> int:
        return len(self.accepted_messages)

    def search_sent(self, account, query: SentReplyQuery) -> SentReply | None:
        self.sent_accounts.append((account.account_id, account.imap_host))
        self.sent_read_count += 1
        if self.sent_read_count in self.fail_sent_reads:
            raise TimeoutError("sent read timed out with private-token")
        for sent in self.sent.get(account.account_id, []):
            if sent.matches(query):
                return sent
        if self.after_sent_search is not None:
            callback = self.after_sent_search
            self.after_sent_search = None
            callback()
        return None

    def send_smtp(
        self,
        account,
        message: OutgoingEmailReply,
    ) -> SmtpAcceptance:
        self.smtp_accounts.append((account.account_id, account.smtp_host))
        if self.before_smtp is not None:
            callback = self.before_smtp
            self.before_smtp = None
            callback()
        if self.crash_before_acceptance:
            self.crash_before_acceptance = False
            raise KeyboardInterrupt("simulated process crash before provider acceptance")
        if self.timeout_before_send:
            self.timeout_before_send = False
            raise TimeoutError("smtp timed out before acceptance password=private")
        self.accepted_messages.append(message)
        sent = SentReply.from_outgoing(
            message,
            provider_result_id=(
                self.provider_result_id or f"sent-{self.acceptance_count}"
            ),
            sent_folder="Sent",
            sent_uidvalidity=77,
            sent_uid=self.acceptance_count,
        )
        self.sent.setdefault(account.account_id, []).append(sent)
        if self.timeout_after_acceptance:
            self.timeout_after_acceptance = False
            raise TimeoutError("smtp timed out after acceptance token=private")
        return SmtpAcceptance(
            provider_result_id=(
                self.smtp_acceptance_id or f"smtp-{self.acceptance_count}"
            )
        )

    def add_equivalent(
        self,
        effect: EmailReplyEffect,
        *,
        message_id: str = "<existing-equivalent@example.com>",
    ) -> None:
        query = effect.sent_query(outgoing_message_id(effect.action_identity, "stardust.ai"))
        self.sent.setdefault(effect.account_id, []).append(
            SentReply(
                message_id=message_id,
                sender=query.sender,
                recipient=query.recipient,
                thread_identity=query.thread_identity,
                subject=query.subject,
                body_sha256=query.body_sha256,
                provider_result_id="sent-existing",
                sent_folder="Sent",
                sent_uidvalidity=77,
                sent_uid=44,
            )
        )


def _plan(*, account_id: str = "account-primary", version: int = 1):
    return build_versioned_email_action_plan(
        action_plan_version=version,
        classification_id=41 if account_id == "account-primary" else 42,
        account_id=account_id,
        category=EmailCategory.WORK,
        classification_source="model",
        confidence=0.98,
        model_id="email-model:2026-08-30:sha256:test",
        config_version="email-config:v7",
        actions=(EmailAction.AUTO_REPLY,),
        action_parameters={
            EmailAction.AUTO_REPLY: {"instruction": "Acknowledge receipt."}
        },
        created_at=datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
    )


def _task_input(*, account_id: str = "account-primary") -> EmailAgentTaskInput:
    stable_identity = f"{account_id}:message-id:<mail-41@example.com>"
    return EmailAgentTaskInput(
        stable_message_identity=stable_identity,
        thread_identity="thread-customer-41",
        subject="Re: Contract confirmation",
        trigger=EmailThreadMessage(
            message_id=stable_identity,
            sender="customer@example.com",
            text="Please confirm the contract.",
            create_time="2026-08-30T08:00:00+00:00",
        ),
    )


def _persist_authorization(
    store: EmailStore,
    plan,
    task_input: EmailAgentTaskInput,
) -> None:
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
                "action_plan": plan,
            }
        ),
        sender=task_input.trigger.sender,
        subject=task_input.subject,
        model_text="__subject__contract confirmation",
        received_at=task_input.trigger.create_time,
    )


def _effect(
    *,
    account_id: str = "account-primary",
    recipient: str = "customer@example.com",
    body: str = "已收到，我会尽快确认。\n\nDerek",
) -> EmailReplyEffect:
    plan = _plan(account_id=account_id)
    stable_identity = f"{account_id}:message-id:<mail-41@example.com>"
    return EmailReplyEffect(
        action_identity=email_action_identity(
            account_id=account_id,
            stable_message_identity=stable_identity,
            action_type=EmailAction.AUTO_REPLY,
            action_plan_version=plan.action_plan_version,
        ),
        action_plan_id=plan.action_plan_id,
        action_plan_version=plan.action_plan_version,
        classification_id=plan.classification_id,
        account_id=account_id,
        stable_message_identity=stable_identity,
        sender=(
            "derek@stardust.ai"
            if account_id == "account-primary"
            else "derek@secondary.example"
        ),
        recipient=recipient,
        thread_identity="thread-customer-41",
        in_reply_to="<mail-41@example.com>",
        subject="Re: Contract confirmation",
        body=body,
    )


def _delivery_setup(tmp_path: Path):
    database = tmp_path / "email-reply.sqlite3"
    store = EmailStore(database)
    store.create_account(ACCOUNT_VALUES)
    plan = _plan()
    task_input = _task_input()
    _persist_authorization(store, plan, task_input)
    harness = FakeSmtpSentHarness()
    return store, harness, _effect()


def test_outgoing_message_id_is_stable_and_uses_first_32_sha256_hex() -> None:
    assert outgoing_message_id(
        "email-action:example",
        "stardust.ai",
    ) == "<ceo-email-c2ec94cd412b5ef4991dfb77edecf47b@stardust.ai>"


def test_v7_store_migrates_reply_dispatch_claims_without_changing_existing_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v6-migration.sqlite3"
    store = EmailStore(database)
    store.create_account(ACCOUNT_VALUES)
    account_before = store.get_account("account-primary")
    with sqlite3.connect(database) as db:
        db.execute("drop table email_reply_dispatch_claims")
        db.execute("update email_schema_migrations set version=7")

    migrated = EmailStore(database)

    assert migrated.get_account("account-primary") == account_before
    with sqlite3.connect(database) as db:
        assert db.execute(
            "select version from email_schema_migrations order by version desc limit 1"
        ).fetchone()[0] == 8
        assert db.execute(
            "select count(*) from email_reply_dispatch_claims"
        ).fetchone()[0] == 0


def test_normal_send_preserves_exact_audit_accepted_fields_and_persists_receipt(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)

    result = EmailReplyDelivery(store, harness).deliver(effect)

    assert result.status == "done"
    assert harness.acceptance_count == 1
    message = harness.accepted_messages[0]
    assert message.sender == effect.sender
    assert message.recipient == effect.recipient
    assert message.thread_identity == effect.thread_identity
    assert message.subject == effect.subject
    assert message.body == effect.body
    assert message.in_reply_to == effect.in_reply_to
    receipt = store.get_email_reply_receipt(effect.action_identity)
    assert receipt is not None
    assert receipt["provider_result_id"] == "sent-1"
    assert receipt["outgoing_message_id"] == message.message_id
    assert effect.body not in str(receipt)
    assert "private" not in str(receipt).casefold()


def test_correct_account_selects_matching_smtp_and_sent_connectors(tmp_path: Path) -> None:
    store, harness, primary = _delivery_setup(tmp_path)
    secondary_values = {
        **ACCOUNT_VALUES,
        "account_id": "account-secondary",
        "display_name": "Secondary",
        "email_address": "derek@secondary.example",
        "imap_host": "imap.secondary.example",
        "imap_username": "derek@secondary.example",
        "imap_secret_reference": "keychain://imap-secondary",
        "smtp_host": "smtp.secondary.example",
        "smtp_username": "derek@secondary.example",
        "smtp_secret_reference": "keychain://smtp-secondary",
    }
    store.create_account(secondary_values)
    secondary_plan = _plan(account_id="account-secondary")
    secondary_input = _task_input(account_id="account-secondary")
    _persist_authorization(store, secondary_plan, secondary_input)
    secondary = _effect(account_id="account-secondary")

    primary_result = EmailReplyDelivery(store, harness).deliver(primary)
    secondary_result = EmailReplyDelivery(store, harness).deliver(secondary)

    assert primary_result.status == secondary_result.status == "done"
    assert harness.smtp_accounts == [
        ("account-primary", "smtp.primary.example"),
        ("account-secondary", "smtp.secondary.example"),
    ]
    assert harness.sent_accounts == [
        ("account-primary", "imap.primary.example"),
        ("account-primary", "imap.primary.example"),
        ("account-secondary", "imap.secondary.example"),
        ("account-secondary", "imap.secondary.example"),
    ]


def test_timeout_before_send_becomes_reconciliation_only_without_blind_retry(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    harness.timeout_before_send = True
    delivery = EmailReplyDelivery(store, harness)

    first = delivery.deliver(effect)
    second = delivery.deliver(effect)

    assert first.status == "failed"
    assert first.error_code == "email_reply_outcome_unresolved"
    assert first.retryable is True
    assert second.status == "failed"
    assert second.error_code == "email_reply_outcome_unresolved"
    assert harness.acceptance_count == 0
    assert len(harness.smtp_accounts) == 1


def test_timeout_after_acceptance_reconciles_after_restart_without_resend(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    harness.timeout_after_acceptance = True

    first = EmailReplyDelivery(store, harness).deliver(effect)
    restarted_store = EmailStore(store.path)
    second = EmailReplyDelivery(restarted_store, harness).deliver(effect)

    assert first.status == "failed"
    assert first.error_code == "email_reply_outcome_unresolved"
    assert second.status == "done"
    assert second.operation == "sent_readback"
    assert harness.acceptance_count == 1


def test_restart_during_post_send_reconciliation_does_not_duplicate_smtp(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    harness.fail_sent_reads = {2}

    first = EmailReplyDelivery(store, harness).deliver(effect)
    second = EmailReplyDelivery(EmailStore(store.path), harness).deliver(effect)

    assert first.status == "failed"
    assert first.error_code == "email_reply_provider_readback_failed"
    assert second.status == "done"
    assert harness.acceptance_count == 1


def test_current_plan_switch_after_sent_precheck_prevents_stale_smtp(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    v2 = _plan(version=2)
    harness.after_sent_search = lambda: store.append_action_plan_version(
        effect.classification_id,
        v2,
        confirmed_category=v2.category,
    )

    result = EmailReplyDelivery(store, harness).deliver(effect)

    assert result.status == "failed"
    assert result.error_code == "email_reply_authorization_stale"
    assert harness.acceptance_count == 0
    assert harness.smtp_accounts == []
    assert store.get_classification(effect.classification_id)[
        "current_action_plan_id"
    ] == v2.action_plan_id


def test_dispatch_claim_blocks_plan_correction_until_dispatch_is_reconciled(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    v2 = _plan(version=2)

    def attempt_correction() -> None:
        with pytest.raises(
            EmailReplyDispatchConflict,
            match="email_reply_dispatch_in_flight",
        ):
            store.append_action_plan_version(
                effect.classification_id,
                v2,
                confirmed_category=v2.category,
            )

    harness.before_smtp = attempt_correction

    result = EmailReplyDelivery(store, harness).deliver(effect)

    assert result.status == "done"
    assert harness.acceptance_count == 1
    assert store.get_classification(effect.classification_id)[
        "current_action_plan_id"
    ] == effect.action_plan_id


def test_historical_timeout_acceptance_reconciles_after_plan_switch_and_restart(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    harness.timeout_after_acceptance = True

    first = EmailReplyDelivery(store, harness).deliver(effect)
    v2 = _plan(version=2)
    store.append_action_plan_version(
        effect.classification_id,
        v2,
        confirmed_category=v2.category,
    )
    second = EmailReplyDelivery(EmailStore(store.path), harness).deliver(effect)

    assert first.status == "failed"
    assert first.error_code == "email_reply_outcome_unresolved"
    assert second.status == "done"
    assert second.operation == "sent_readback"
    assert harness.acceptance_count == 1
    assert store.get_email_reply_receipt(effect.action_identity) is not None


def test_historical_effect_without_sent_evidence_never_dispatches_smtp(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    v2 = _plan(version=2)
    store.append_action_plan_version(
        effect.classification_id,
        v2,
        confirmed_category=v2.category,
    )

    result = EmailReplyDelivery(store, harness).deliver(effect)

    assert result.status == "failed"
    assert result.error_code == "email_reply_authorization_stale"
    assert harness.sent_read_count == 1
    assert harness.acceptance_count == 0
    assert harness.smtp_accounts == []


def test_stale_dispatch_claim_recovery_never_repeats_possible_smtp(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    harness.crash_before_acceptance = True

    with pytest.raises(KeyboardInterrupt, match="simulated process crash"):
        EmailReplyDelivery(store, harness).deliver(effect)

    assert store.recover_stale_email_reply_dispatch_claims(
        stale_before="9999-01-01T00:00:00+00:00",
        recovered_at="2026-08-30T09:00:00+00:00",
    ) == 1
    replay = EmailReplyDelivery(EmailStore(store.path), harness).deliver(effect)

    assert replay.status == "failed"
    assert replay.error_code == "email_reply_outcome_unresolved"
    assert harness.acceptance_count == 0
    assert len(harness.smtp_accounts) == 1


def test_equivalent_reply_in_sent_is_persisted_without_smtp(tmp_path: Path) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    harness.add_equivalent(effect)

    result = EmailReplyDelivery(store, harness).deliver(effect)

    assert result.status == "done"
    assert result.operation == "sent_equivalent_readback"
    assert harness.acceptance_count == 0
    assert store.get_email_reply_receipt(effect.action_identity)[
        "provider_result_id"
    ] == "sent-existing"


def test_provider_read_failure_before_smtp_is_retryable_and_redacted(tmp_path: Path) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    harness.fail_sent_reads = {1}

    result = EmailReplyDelivery(store, harness).deliver(effect)

    assert result.status == "failed"
    assert result.error_code == "email_reply_provider_read_failed"
    assert result.retryable is True
    assert "private-token" not in result.error_excerpt
    assert effect.body not in result.error_excerpt
    assert harness.acceptance_count == 0


def test_credential_like_provider_identifier_is_not_persisted_or_exposed(
    tmp_path: Path,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    harness.provider_result_id = "token=private-provider-id"

    result = EmailReplyDelivery(store, harness).deliver(effect)

    assert result.status == "failed"
    assert result.error_code == "email_reply_receipt_rejected"
    assert "private-provider-id" not in result.error_excerpt
    assert store.get_email_reply_receipt(effect.action_identity) is None


@pytest.mark.parametrize(
    ("field", "provider_value"),
    (
        ("provider_result_id", "已收到，我会尽快确认。\n\nDerek"),
        ("smtp_acceptance_id", "已收到，我会尽快确认。\n\nDerek"),
        ("smtp_acceptance_id", "Re: Contract confirmation"),
        ("smtp_acceptance_id", "250 accepted\r\nqueue-id=provider-42"),
        ("smtp_acceptance_id", "a" * 300),
    ),
)
def test_provider_identifiers_are_bounded_opaque_and_never_echoed(
    tmp_path: Path,
    field: str,
    provider_value: str,
) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    setattr(harness, field, provider_value)

    result = EmailReplyDelivery(store, harness).deliver(effect)

    assert result.status == "failed"
    assert result.error_code == "email_reply_receipt_rejected"
    assert result.error_excerpt == "email_reply_receipt_rejected"
    assert effect.subject not in result.error_excerpt
    assert effect.body not in result.error_excerpt
    assert provider_value not in result.error_excerpt
    assert store.get_email_reply_receipt(effect.action_identity) is None


def test_persisted_receipt_short_circuits_sent_and_smtp_after_restart(tmp_path: Path) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    first = EmailReplyDelivery(store, harness).deliver(effect)
    reads_after_first = harness.sent_read_count

    replay = EmailReplyDelivery(EmailStore(store.path), harness).deliver(effect)

    assert first.status == replay.status == "done"
    assert replay.operation == "persisted_receipt"
    assert harness.sent_read_count == reads_after_first
    assert harness.acceptance_count == 1


def test_persisted_receipt_does_not_confirm_different_accepted_body(tmp_path: Path) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    first = EmailReplyDelivery(store, harness).deliver(effect)
    changed = replace(effect, body="A different Audit-accepted body.")
    reads_after_first = harness.sent_read_count

    replay = EmailReplyDelivery(EmailStore(store.path), harness).deliver(changed)

    assert first.status == "done"
    assert replay.status == "failed"
    assert replay.error_code == "email_reply_receipt_conflict"
    assert harness.sent_read_count == reads_after_first
    assert harness.acceptance_count == 1


def test_stale_action_plan_is_rejected_before_provider_access(tmp_path: Path) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    stale = replace(effect, action_plan_id="historical-plan")

    result = EmailReplyDelivery(store, harness).deliver(stale)

    assert result.status == "failed"
    assert result.error_code == "email_reply_authorization_stale"
    assert harness.sent_read_count == 0
    assert harness.acceptance_count == 0


def test_forged_action_identity_is_rejected_before_provider_access(tmp_path: Path) -> None:
    store, harness, effect = _delivery_setup(tmp_path)
    forged = replace(effect, action_identity="email-action:forged")

    result = EmailReplyDelivery(store, harness).deliver(forged)

    assert result.status == "failed"
    assert result.error_code == "email_reply_authorization_stale"
    assert harness.sent_read_count == 0
    assert harness.acceptance_count == 0


def test_task_adapter_preserves_exact_accepted_proposal_fields(tmp_path: Path) -> None:
    database = tmp_path / "adapter.sqlite3"
    email_store = EmailStore(database)
    email_store.create_account(ACCOUNT_VALUES)
    plan = _plan()
    task_input = _task_input()
    _persist_authorization(email_store, plan, task_input)
    route = EmailAgentTaskAdapter(
        AutoReplyStore(database),
        email_store,
    ).ensure_action_plan_tasks(plan, task_input)[0]
    action = ProposedAction.model_validate(
        {
            "description": "Send the reviewed automatic reply",
            "capability": "email",
            "operation": "reply",
            "target": {
                "action_identity": route.task.trigger_message_id,
                "account_id": plan.account_id,
                "stable_message_identity": task_input.stable_message_identity,
                "sender": "derek@stardust.ai",
                "recipient": "customer@example.com",
                "thread_identity": task_input.thread_identity,
            },
            "payload": {
                "in_reply_to": "<mail-41@example.com>",
                "subject": "Re: Contract confirmation",
                "body": "Exact Agent proposal body.\nDo not rewrite.",
            },
            "expected_verification": "Read Sent and match Message-ID and recipient.",
        }
    )

    effect = accepted_email_reply_effect(route.task, action)

    assert effect.action_identity == route.task.trigger_message_id
    assert effect.sender == action.target["sender"]
    assert effect.recipient == action.target["recipient"]
    assert effect.thread_identity == action.target["thread_identity"]
    assert effect.subject == action.payload["subject"]
    assert effect.body == action.payload["body"]


def test_task_adapter_rejects_reply_target_that_does_not_match_task(tmp_path: Path) -> None:
    database = tmp_path / "adapter-mismatch.sqlite3"
    email_store = EmailStore(database)
    email_store.create_account(ACCOUNT_VALUES)
    plan = _plan()
    task_input = _task_input()
    _persist_authorization(email_store, plan, task_input)
    route = EmailAgentTaskAdapter(
        AutoReplyStore(database),
        email_store,
    ).ensure_action_plan_tasks(plan, task_input)[0]
    action = ProposedAction.model_validate(
        {
            "description": "Send reply",
            "capability": "email",
            "operation": "reply",
            "target": {
                "action_identity": route.task.trigger_message_id,
                "account_id": "wrong-account",
                "stable_message_identity": task_input.stable_message_identity,
                "sender": "derek@stardust.ai",
                "recipient": "customer@example.com",
                "thread_identity": task_input.thread_identity,
            },
            "payload": {
                "in_reply_to": "<mail-41@example.com>",
                "subject": "Re: Contract confirmation",
                "body": "Exact body",
            },
            "expected_verification": "Read Sent.",
        }
    )

    with pytest.raises(ValueError, match="accepted reply target"):
        accepted_email_reply_effect(route.task, action)
