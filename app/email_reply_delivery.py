"""Deliver one Audit-accepted automatic email reply with Sent reconciliation.

This adapter does not classify mail, generate reply text, or approve an action.
It consumes the exact immutable effect accepted by Audit and preserves it through
SMTP and Sent-folder verification. Vanilla SMTP cannot guarantee strict
exactly-once delivery: this module only retries when the provider explicitly
proves that dispatch did not occur; every other interrupted outcome is resolved
by read-only Sent reconciliation using the stable Message-ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
import time
from typing import Literal, Mapping, Protocol
from uuid import uuid4

from app.email_classifier_contracts import EmailAction
from app.email_store import (
    EmailReplyDispatchConflict,
    EmailReplyReceiptConflict,
    EmailStore,
    email_action_identity,
)


_UNRESOLVED_ERROR = "email_reply_outcome_unresolved"
_OPAQUE_PROVIDER_ID = re.compile(r"[A-Za-z0-9._:@<>\[\]{}/+=,!#$%&'*?^-]+")
_MAX_PROVIDER_IDENTIFIER_BYTES = 256
_PROCESS_GENERATION = time.time_ns()


def outgoing_message_id(action_identity: str, domain: str) -> str:
    """Derive the stable RFC Message-ID for one immutable email action."""

    if not action_identity or action_identity != action_identity.strip():
        raise ValueError("action_identity must be a non-empty stable identifier")
    if (
        not domain
        or domain != domain.strip()
        or "@" in domain
        or any(character.isspace() for character in domain)
    ):
        raise ValueError("domain must be a valid Message-ID domain")
    digest = sha256(action_identity.encode("utf-8")).hexdigest()[:32]
    return f"<ceo-email-{digest}@{domain}>"


def _body_digest(body: str) -> str:
    return sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmailReplyEffect:
    """The exact automatic reply proposed by Consumer and accepted by Audit."""

    action_identity: str
    action_plan_id: str
    action_plan_version: int
    classification_id: int
    account_id: str
    stable_message_identity: str
    sender: str
    recipient: str
    thread_identity: str
    in_reply_to: str
    subject: str
    body: str

    def __post_init__(self) -> None:
        required = (
            "action_identity",
            "action_plan_id",
            "account_id",
            "stable_message_identity",
            "sender",
            "recipient",
            "thread_identity",
            "in_reply_to",
            "subject",
            "body",
        )
        for field_name in required:
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        if self.action_plan_version <= 0:
            raise ValueError("action_plan_version must be positive")
        if self.classification_id <= 0:
            raise ValueError("classification_id must be positive")

    def sent_query(self, message_id: str) -> "SentReplyQuery":
        return SentReplyQuery(
            message_id=message_id,
            sender=self.sender,
            recipient=self.recipient,
            thread_identity=self.thread_identity,
            subject=self.subject,
            body_sha256=_body_digest(self.body),
        )

    @property
    def effect_digest(self) -> str:
        canonical = json.dumps(
            {
                "sender": self.sender,
                "recipient": self.recipient,
                "thread_identity": self.thread_identity,
                "in_reply_to": self.in_reply_to,
                "subject": self.subject,
                "body": self.body,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmailReplyAccount:
    account_id: str
    email_address: str
    imap_host: str
    imap_port: int
    imap_tls: bool
    imap_username: str
    imap_secret_reference: str
    smtp_host: str
    smtp_port: int
    smtp_tls: bool
    smtp_username: str
    smtp_secret_reference: str

    @classmethod
    def from_store(cls, value: Mapping[str, object]) -> "EmailReplyAccount":
        return cls(
            account_id=str(value["account_id"]),
            email_address=str(value["email_address"]),
            imap_host=str(value["imap_host"]),
            imap_port=int(value["imap_port"]),
            imap_tls=bool(value["imap_tls"]),
            imap_username=str(value["imap_username"]),
            imap_secret_reference=str(value["imap_secret_reference"]),
            smtp_host=str(value["smtp_host"]),
            smtp_port=int(value["smtp_port"]),
            smtp_tls=bool(value["smtp_tls"]),
            smtp_username=str(value["smtp_username"]),
            smtp_secret_reference=str(value["smtp_secret_reference"]),
        )


@dataclass(frozen=True)
class OutgoingEmailReply:
    message_id: str
    sender: str
    recipient: str
    thread_identity: str
    in_reply_to: str
    subject: str
    body: str


@dataclass(frozen=True)
class SentReplyQuery:
    message_id: str
    sender: str
    recipient: str
    thread_identity: str
    subject: str
    body_sha256: str


@dataclass(frozen=True)
class SentReply:
    message_id: str
    sender: str
    recipient: str
    thread_identity: str
    subject: str
    body_sha256: str
    provider_result_id: str
    sent_folder: str
    sent_uidvalidity: int
    sent_uid: int

    @classmethod
    def from_outgoing(
        cls,
        message: OutgoingEmailReply,
        *,
        provider_result_id: str,
        sent_folder: str,
        sent_uidvalidity: int,
        sent_uid: int,
    ) -> "SentReply":
        return cls(
            message_id=message.message_id,
            sender=message.sender,
            recipient=message.recipient,
            thread_identity=message.thread_identity,
            subject=message.subject,
            body_sha256=_body_digest(message.body),
            provider_result_id=provider_result_id,
            sent_folder=sent_folder,
            sent_uidvalidity=sent_uidvalidity,
            sent_uid=sent_uid,
        )

    def matches(self, query: SentReplyQuery) -> bool:
        return (
            self.sender == query.sender
            and self.recipient == query.recipient
            and self.thread_identity == query.thread_identity
            and self.subject == query.subject
            and self.body_sha256 == query.body_sha256
        )

    def match_operation(self, query: SentReplyQuery) -> str:
        if not self.matches(query):
            raise ValueError("Sent reply does not match the accepted effect")
        return (
            "sent_readback"
            if self.message_id == query.message_id
            else "sent_equivalent_readback"
        )


@dataclass(frozen=True)
class SmtpDispatchOutcome:
    """Provider-owned dispatch fact used to choose retry versus reconciliation.

    ``definitely_not_dispatched`` is the only outcome that permits a future SMTP
    retry. ``outcome_uncertain`` permanently routes this action through Sent
    reconciliation, while ``accepted`` requires immediate Sent readback before
    the effect can complete. An adapter that cannot prove one of these facts must
    raise; the delivery layer treats that exception as uncertain, never as safe
    to retry.
    """

    status: Literal["accepted", "definitely_not_dispatched", "outcome_uncertain"]
    provider_result_id: str = ""
    error_code: str = ""


class EmailReplyProvider(Protocol):
    def search_sent(
        self,
        account: EmailReplyAccount,
        query: SentReplyQuery,
    ) -> SentReply | None: ...

    def send_smtp(
        self,
        account: EmailReplyAccount,
        message: OutgoingEmailReply,
    ) -> SmtpDispatchOutcome:
        """Return an explicit outcome; do not encode dispatch state in exceptions."""
        ...


@dataclass(frozen=True)
class EmailReplyDeliveryResult:
    status: Literal["done", "failed"]
    operation: str
    target: str
    provider_result_id: str = ""
    error_code: str = ""
    error_excerpt: str = ""
    retryable: bool = False


def _failed(
    *,
    code: str,
    target: str,
    retryable: bool,
) -> EmailReplyDeliveryResult:
    return EmailReplyDeliveryResult(
        status="failed",
        operation="email_reply_delivery",
        target=target,
        error_code=code,
        error_excerpt=code,
        retryable=retryable,
    )


class EmailReplyDelivery:
    """Apply one accepted reply and make provider evidence durable first."""

    def __init__(
        self,
        store: EmailStore,
        provider: EmailReplyProvider,
        *,
        owner: Mapping[str, object] | None = None,
    ):
        self.store = store
        self.provider = provider
        self.owner = dict(owner or self._default_owner())

    @staticmethod
    def _default_owner() -> Mapping[str, object]:
        return {
            "owner_id": f"pid:{os.getpid()}",
            "generation": _PROCESS_GENERATION,
            "lease_token": uuid4().hex,
        }

    def deliver(self, effect: EmailReplyEffect) -> EmailReplyDeliveryResult:
        authorization = self.store.get_email_reply_delivery_authorization(
            effect.classification_id,
            effect.action_plan_id,
        )
        if not self._is_immutable_authorization(effect, authorization):
            return _failed(
                code="email_reply_authorization_stale",
                target=effect.action_identity,
                retryable=False,
            )
        domain = effect.sender.rsplit("@", 1)[-1]
        message_id = outgoing_message_id(effect.action_identity, domain)
        query = effect.sent_query(message_id)
        claim = self.store.get_email_reply_dispatch_claim(effect.action_identity)

        receipt = self.store.get_email_reply_receipt(effect.action_identity)
        if receipt is not None:
            if (
                receipt["outgoing_message_id"] != message_id
                or receipt["effect_digest"] != effect.effect_digest
            ):
                return _failed(
                    code="email_reply_receipt_conflict",
                    target=effect.action_identity,
                    retryable=False,
                )
            return self._complete_from_persisted_receipt(
                effect,
                receipt,
                claim=claim,
            )
        if claim is not None and not self._claim_matches_effect(
            claim,
            effect=effect,
            message_id=message_id,
        ):
            return _failed(
                code="email_reply_dispatch_claim_rejected",
                target=effect.action_identity,
                retryable=False,
            )
        authoritative_thread = (
            claim["thread_identity"]
            if claim is not None
            else authorization["thread_identity"]
        )
        if authoritative_thread != effect.thread_identity:
            return _failed(
                code="email_reply_authorization_stale",
                target=effect.action_identity,
                retryable=False,
            )

        account_row = self.store.get_account(effect.account_id)
        if claim is not None:
            account_values = claim["account_snapshot"]
        elif (
            account_row is not None
            and account_row["email_address"] == effect.sender
        ):
            account_values = account_row
        else:
            code = (
                "email_reply_reconciliation_blocked"
                if authorization["current_action_plan_id"] != effect.action_plan_id
                else "email_reply_sender_account_mismatch"
            )
            return _failed(
                code=code,
                target=effect.action_identity,
                retryable=code == "email_reply_reconciliation_blocked",
            )
        try:
            account = EmailReplyAccount.from_store(account_values)
        except (KeyError, TypeError, ValueError):
            return _failed(
                code="email_reply_reconciliation_blocked",
                target=effect.action_identity,
                retryable=True,
            )

        try:
            existing = self.provider.search_sent(account, query)
        except Exception:
            return _failed(
                code="email_reply_provider_read_failed",
                target=effect.action_identity,
                retryable=True,
            )
        if existing is not None:
            return self._complete_from_sent(
                effect,
                query,
                existing,
                smtp_result_id="",
                claim=claim,
            )

        if claim is not None and claim["status"] in {"dispatching", "uncertain"}:
            return _failed(
                code=_UNRESOLVED_ERROR,
                target=effect.action_identity,
                retryable=True,
            )
        if claim is not None and claim["status"] == "done":
            return _failed(
                code="email_reply_receipt_conflict",
                target=effect.action_identity,
                retryable=False,
            )

        if authorization["current_action_plan_id"] != effect.action_plan_id:
            return _failed(
                code="email_reply_authorization_stale",
                target=effect.action_identity,
                retryable=False,
            )
        if (
            account_row is None
            or not account_row["enabled"]
            or account_row["email_address"] != effect.sender
        ):
            return _failed(
                code="email_reply_sender_account_mismatch",
                target=effect.action_identity,
                retryable=False,
            )

        try:
            claim = self.store.claim_email_reply_dispatch(
                action_identity=effect.action_identity,
                effect_digest=effect.effect_digest,
                action_plan_id=effect.action_plan_id,
                classification_id=effect.classification_id,
                account_id=effect.account_id,
                stable_message_identity=effect.stable_message_identity,
                outgoing_message_id=message_id,
                sender=effect.sender,
                thread_identity=effect.thread_identity,
                expected_account_updated_at=str(account_row["updated_at"]),
                owner=self.owner,
            )
        except (EmailReplyDispatchConflict, ValueError):
            return _failed(
                code="email_reply_dispatch_claim_rejected",
                target=effect.action_identity,
                retryable=False,
            )
        if claim is None:
            return _failed(
                code="email_reply_authorization_stale",
                target=effect.action_identity,
                retryable=False,
            )
        if not claim["acquired"]:
            return _failed(
                code=_UNRESOLVED_ERROR,
                target=effect.action_identity,
                retryable=True,
            )
        account = EmailReplyAccount.from_store(claim["account_snapshot"])

        outgoing = OutgoingEmailReply(
            message_id=message_id,
            sender=effect.sender,
            recipient=effect.recipient,
            thread_identity=effect.thread_identity,
            in_reply_to=effect.in_reply_to,
            subject=effect.subject,
            body=effect.body,
        )
        try:
            smtp_outcome = self.provider.send_smtp(account, outgoing)
        except Exception:
            self.store.mark_email_reply_dispatch_uncertain(
                effect.action_identity,
                owner=self.owner,
            )
            return _failed(
                code="email_reply_provider_contract_error",
                target=effect.action_identity,
                retryable=True,
            )
        outcome_status = getattr(smtp_outcome, "status", "")
        if outcome_status == "definitely_not_dispatched":
            self.store.release_email_reply_dispatch_retryable(
                effect.action_identity,
                owner=self.owner,
            )
            return _failed(
                code="email_reply_definitely_not_dispatched",
                target=effect.action_identity,
                retryable=True,
            )
        if outcome_status == "outcome_uncertain":
            self.store.mark_email_reply_dispatch_uncertain(
                effect.action_identity,
                owner=self.owner,
            )
            return _failed(
                code=_UNRESOLVED_ERROR,
                target=effect.action_identity,
                retryable=True,
            )
        if outcome_status != "accepted":
            self.store.mark_email_reply_dispatch_uncertain(
                effect.action_identity,
                owner=self.owner,
            )
            return _failed(
                code="email_reply_provider_contract_error",
                target=effect.action_identity,
                retryable=True,
            )
        if not self._safe_provider_identifier(
            smtp_outcome.provider_result_id,
            effect=effect,
            allow_empty=True,
        ):
            self.store.mark_email_reply_dispatch_uncertain(
                effect.action_identity,
                owner=self.owner,
            )
            return _failed(
                code="email_reply_receipt_rejected",
                target=effect.action_identity,
                retryable=False,
            )

        try:
            sent = self.provider.search_sent(account, query)
        except Exception:
            self.store.mark_email_reply_dispatch_uncertain(
                effect.action_identity,
                owner=self.owner,
            )
            return _failed(
                code="email_reply_provider_readback_failed",
                target=effect.action_identity,
                retryable=True,
            )
        if sent is None:
            self.store.mark_email_reply_dispatch_uncertain(
                effect.action_identity,
                owner=self.owner,
            )
            return _failed(
                code="email_reply_provider_readback_mismatch",
                target=effect.action_identity,
                retryable=True,
            )
        return self._complete_from_sent(
            effect,
            query,
            sent,
            smtp_result_id=smtp_outcome.provider_result_id,
            claim=claim,
        )

    @staticmethod
    def _is_immutable_authorization(
        effect: EmailReplyEffect,
        authorization: Mapping[str, object] | None,
    ) -> bool:
        expected_action_identity = email_action_identity(
            account_id=effect.account_id,
            stable_message_identity=effect.stable_message_identity,
            action_type=EmailAction.AUTO_REPLY,
            action_plan_version=effect.action_plan_version,
        )
        return bool(
            authorization
            and effect.action_identity == expected_action_identity
            and authorization["classification_id"] == effect.classification_id
            and authorization["account_id"] == effect.account_id
            and authorization["stable_message_identity"]
            == effect.stable_message_identity
            and authorization["action_plan_id"] == effect.action_plan_id
            and authorization["action_plan_version"]
            == effect.action_plan_version
            and EmailAction.AUTO_REPLY.value in authorization["actions"]
        )

    @staticmethod
    def _claim_matches_effect(
        claim: Mapping[str, object],
        *,
        effect: EmailReplyEffect,
        message_id: str,
    ) -> bool:
        return bool(
            claim["effect_digest"] == effect.effect_digest
            and claim["action_plan_id"] == effect.action_plan_id
            and claim["classification_id"] == effect.classification_id
            and claim["account_id"] == effect.account_id
            and claim["stable_message_identity"]
            == effect.stable_message_identity
            and claim["outgoing_message_id"] == message_id
            and claim["sender"] == effect.sender
            and claim["thread_identity"] == effect.thread_identity
        )

    @staticmethod
    def _claim_owner(claim: Mapping[str, object] | None) -> Mapping[str, object] | None:
        if claim is None:
            return None
        return {
            "owner_id": claim["owner_id"],
            "generation": claim["owner_generation"],
            "lease_token": claim["lease_token"],
        }

    def _complete_from_persisted_receipt(
        self,
        effect: EmailReplyEffect,
        receipt: Mapping[str, object],
        *,
        claim: Mapping[str, object] | None,
    ) -> EmailReplyDeliveryResult:
        provider_receipt = receipt["provider_receipt"]
        assert isinstance(provider_receipt, Mapping)
        try:
            persisted = self.store.persist_email_reply_receipt(
                action_identity=str(receipt["action_identity"]),
                effect_digest=str(receipt["effect_digest"]),
                action_plan_id=str(receipt["action_plan_id"]),
                classification_id=int(receipt["classification_id"]),
                account_id=str(receipt["account_id"]),
                stable_message_identity=str(receipt["stable_message_identity"]),
                outgoing_message_id=str(receipt["outgoing_message_id"]),
                provider_operation=str(receipt["provider_operation"]),
                provider_target=str(receipt["provider_target"]),
                provider_result_id=str(receipt["provider_result_id"]),
                sent_folder=str(provider_receipt["sent_folder"]),
                sent_uidvalidity=int(provider_receipt["sent_uidvalidity"]),
                sent_uid=int(provider_receipt["sent_uid"]),
                smtp_acceptance_id=str(provider_receipt["smtp_acceptance_id"]),
                claim_owner=self._claim_owner(claim),
            )
        except (EmailReplyDispatchConflict, EmailReplyReceiptConflict, ValueError):
            return _failed(
                code="email_reply_receipt_conflict",
                target=effect.action_identity,
                retryable=False,
            )
        return EmailReplyDeliveryResult(
            status="done",
            operation="persisted_receipt",
            target=effect.action_identity,
            provider_result_id=str(persisted["provider_result_id"]),
        )

    @staticmethod
    def _safe_provider_identifier(
        value: str,
        *,
        effect: EmailReplyEffect,
        allow_empty: bool = False,
    ) -> bool:
        if not isinstance(value, str):
            return False
        if not value:
            return allow_empty
        if (
            value != value.strip()
            or len(value.encode("utf-8")) > _MAX_PROVIDER_IDENTIFIER_BYTES
            or _OPAQUE_PROVIDER_ID.fullmatch(value) is None
        ):
            return False
        return not any(
            accepted_text and accepted_text in value
            for accepted_text in (effect.subject, effect.body)
        )

    @staticmethod
    def _safe_provider_location(value: str, *, effect: EmailReplyEffect) -> bool:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value.encode("utf-8")) > _MAX_PROVIDER_IDENTIFIER_BYTES
            or any(ord(character) < 32 or ord(character) > 126 for character in value)
        ):
            return False
        return not any(
            accepted_text and accepted_text in value
            for accepted_text in (effect.subject, effect.body)
        )

    def _complete_from_sent(
        self,
        effect: EmailReplyEffect,
        query: SentReplyQuery,
        sent: SentReply,
        *,
        smtp_result_id: str,
        claim: Mapping[str, object] | None,
    ) -> EmailReplyDeliveryResult:
        if (
            not self._safe_provider_identifier(
                sent.message_id,
                effect=effect,
            )
            or not self._safe_provider_identifier(
                sent.provider_result_id,
                effect=effect,
            )
            or not self._safe_provider_location(sent.sent_folder, effect=effect)
            or not self._safe_provider_identifier(
                smtp_result_id,
                effect=effect,
                allow_empty=True,
            )
        ):
            self._mark_owned_claim_uncertain(effect.action_identity, claim)
            return _failed(
                code="email_reply_receipt_rejected",
                target=effect.action_identity,
                retryable=False,
            )
        try:
            operation = sent.match_operation(query)
        except ValueError:
            self._mark_owned_claim_uncertain(effect.action_identity, claim)
            return _failed(
                code="email_reply_provider_readback_mismatch",
                target=effect.action_identity,
                retryable=True,
            )
        try:
            receipt = self.store.persist_email_reply_receipt(
                action_identity=effect.action_identity,
                effect_digest=effect.effect_digest,
                action_plan_id=effect.action_plan_id,
                classification_id=effect.classification_id,
                account_id=effect.account_id,
                stable_message_identity=effect.stable_message_identity,
                outgoing_message_id=query.message_id,
                provider_operation=operation,
                provider_target=effect.stable_message_identity,
                provider_result_id=sent.provider_result_id,
                sent_folder=sent.sent_folder,
                sent_uidvalidity=sent.sent_uidvalidity,
                sent_uid=sent.sent_uid,
                smtp_acceptance_id=smtp_result_id,
                claim_owner=self._claim_owner(claim),
            )
        except (EmailReplyDispatchConflict, EmailReplyReceiptConflict, ValueError):
            self._mark_owned_claim_uncertain(effect.action_identity, claim)
            return _failed(
                code="email_reply_receipt_rejected",
                target=effect.action_identity,
                retryable=False,
            )
        return EmailReplyDeliveryResult(
            status="done",
            operation=operation,
            target=effect.action_identity,
            provider_result_id=str(receipt["provider_result_id"]),
        )

    def _mark_owned_claim_uncertain(
        self,
        action_identity: str,
        claim: Mapping[str, object] | None,
    ) -> None:
        if claim is None or claim["status"] != "dispatching":
            return
        claim_owner = self._claim_owner(claim)
        if claim_owner == self.owner:
            self.store.mark_email_reply_dispatch_uncertain(
                action_identity,
                owner=self.owner,
            )
