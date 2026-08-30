"""Deliver one Audit-accepted automatic email reply with Sent reconciliation.

This adapter does not classify mail, generate reply text, or approve an action.
It consumes the exact immutable effect accepted by Audit and preserves it through
SMTP and Sent-folder verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal, Mapping, Protocol

from app.email_classifier_contracts import EmailAction
from app.email_store import EmailReplyReceiptConflict, EmailStore


_UNRESOLVED_ERROR = "email_reply_outcome_unresolved"


def email_action_identity(
    *,
    account_id: str,
    stable_message_identity: str,
    action_type: EmailAction,
    action_plan_version: int,
) -> str:
    """Identify one immutable email action across scans and restarts."""

    account_id = account_id.strip()
    stable_message_identity = stable_message_identity.strip()
    action_type = EmailAction(action_type)
    if not account_id or not stable_message_identity:
        raise ValueError("email action identity fields must be non-empty")
    if action_plan_version <= 0:
        raise ValueError("action_plan_version must be positive")
    canonical = json.dumps(
        {
            "account_id": account_id,
            "stable_message_identity": stable_message_identity,
            "action_type": action_type.value,
            "action_plan_version": action_plan_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"email-action:{sha256(canonical.encode('utf-8')).hexdigest()}"


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
class SmtpAcceptance:
    provider_result_id: str


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
    ) -> SmtpAcceptance: ...


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

    def __init__(self, store: EmailStore, provider: EmailReplyProvider):
        self.store = store
        self.provider = provider

    def deliver(self, effect: EmailReplyEffect) -> EmailReplyDeliveryResult:
        authorization = self.store.get_email_reply_delivery_authorization(
            effect.classification_id
        )
        if not self._is_authorized(effect, authorization):
            return _failed(
                code="email_reply_authorization_stale",
                target=effect.action_identity,
                retryable=False,
            )

        account_row = self.store.get_account(effect.account_id)
        if (
            account_row is None
            or not account_row["enabled"]
            or effect.sender != account_row["email_address"]
        ):
            return _failed(
                code="email_reply_sender_account_mismatch",
                target=effect.action_identity,
                retryable=False,
            )
        account = EmailReplyAccount.from_store(account_row)
        domain = effect.sender.rsplit("@", 1)[-1]
        message_id = outgoing_message_id(effect.action_identity, domain)
        query = effect.sent_query(message_id)

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
            return EmailReplyDeliveryResult(
                status="done",
                operation="persisted_receipt",
                target=effect.action_identity,
                provider_result_id=str(receipt["provider_result_id"]),
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
            return self._complete_from_sent(effect, query, existing, smtp_result_id="")

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
            acceptance = self.provider.send_smtp(account, outgoing)
        except TimeoutError:
            return _failed(
                code=_UNRESOLVED_ERROR,
                target=effect.action_identity,
                retryable=True,
            )
        except Exception:
            return _failed(
                code="email_reply_provider_send_failed",
                target=effect.action_identity,
                retryable=True,
            )

        try:
            sent = self.provider.search_sent(account, query)
        except Exception:
            return _failed(
                code="email_reply_provider_readback_failed",
                target=effect.action_identity,
                retryable=True,
            )
        if sent is None:
            return _failed(
                code="email_reply_provider_readback_mismatch",
                target=effect.action_identity,
                retryable=True,
            )
        return self._complete_from_sent(
            effect,
            query,
            sent,
            smtp_result_id=acceptance.provider_result_id,
        )

    @staticmethod
    def _is_authorized(
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
            and authorization["thread_identity"] == effect.thread_identity
            and authorization["current_action_plan_id"] == effect.action_plan_id
            and authorization["action_plan_version"]
            == effect.action_plan_version
            and EmailAction.AUTO_REPLY.value in authorization["actions"]
        )

    def _complete_from_sent(
        self,
        effect: EmailReplyEffect,
        query: SentReplyQuery,
        sent: SentReply,
        *,
        smtp_result_id: str,
    ) -> EmailReplyDeliveryResult:
        try:
            operation = sent.match_operation(query)
        except ValueError:
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
            )
        except (EmailReplyReceiptConflict, ValueError):
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
