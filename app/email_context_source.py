"""Complete metadata-only Email context projected from durable message relations."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
from typing import Any

from app.agent_context import PriorReceipt
from app.email_classifier_contracts import EmailAttachmentMetadata
from app.email_task_adapter import EmailAgentTaskInput, EmailThreadMessage


class EmailContextSource:
    """Load one task from durable context instead of a fixed recent-UID window."""

    def __init__(
        self,
        store: Any,
        *,
        source_factory: Callable[[Mapping[str, object]], object] | None = None,
    ):
        self.store = store
        self.source_factory = source_factory

    def load_task_input(self, task: object) -> EmailAgentTaskInput:
        payload = _task_payload(task)
        classification_id = _required_int(payload, "classification_id")
        classification = self.store.get_classification(classification_id)
        if not isinstance(classification, Mapping):
            raise ValueError("email task classification is unavailable")

        account_id = _required_text(payload, "account_id")
        stable_identity = _required_text(payload, "stable_message_identity")
        thread_identity = _required_text(payload, "thread_identity")
        if any(
            str(classification.get(name) or "") != expected
            for name, expected in (
                ("account_id", account_id),
                ("stable_message_identity", stable_identity),
                ("thread_id", thread_identity),
            )
        ):
            raise ValueError("email task identity does not match durable state")
        _validate_action_plan_snapshot(payload, classification)

        rows = self.store.list_email_context_thread(
            account_id=account_id,
            stable_message_identity=stable_identity,
        )
        if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
            raise ValueError("email context thread is invalid")
        trigger_row = next(
            (
                row
                for row in rows
                if isinstance(row, Mapping)
                and row.get("stable_message_identity") == stable_identity
            ),
            None,
        )
        if trigger_row is None:
            raise ValueError("email task source message is unavailable")

        provider_trigger = self._read_current_provider_message(
            account_id=account_id,
            classification=classification,
        )
        source_row = dict(trigger_row)
        if provider_trigger is not None:
            source_row.update(
                {
                    "sender": _message_sender(provider_trigger),
                    "subject": provider_trigger.get("subject") or source_row["subject"],
                    "normalized_text": _message_text(provider_trigger),
                    "attachment_metadata": provider_trigger.get("attachments") or (),
                }
            )
        trigger = _thread_message(source_row)
        thread_messages = tuple(
            _thread_message(row)
            for row in rows
            if isinstance(row, Mapping)
            and row.get("stable_message_identity") != stable_identity
        )
        attachment_values = source_row.get("attachment_metadata") or ()
        if not isinstance(attachment_values, Sequence) or isinstance(
            attachment_values, str | bytes
        ):
            raise ValueError("email attachment metadata is invalid")
        attachments = tuple(
            EmailAttachmentMetadata.model_validate(item) for item in attachment_values
        )
        receipt_values = self.store.list_email_context_receipts(
            account_id=account_id,
            stable_message_identity=stable_identity,
        )
        if not isinstance(receipt_values, Sequence) or isinstance(
            receipt_values, str | bytes
        ):
            raise ValueError("email context receipts are invalid")
        prior_receipts = tuple(
            PriorReceipt(
                receipt_id=_required_text(receipt, "receipt_id"),
                operation=_required_text(receipt, "operation"),
                summary=str(receipt.get("summary") or ""),
                completed=bool(receipt.get("completed")),
            )
            for receipt in receipt_values
            if isinstance(receipt, Mapping)
        )
        return EmailAgentTaskInput(
            stable_message_identity=stable_identity,
            thread_identity=thread_identity,
            subject=str(
                source_row.get("subject") or classification.get("subject") or ""
            ),
            trigger=trigger,
            thread_messages=thread_messages,
            attachments=attachments,
            prior_receipts=prior_receipts,
            list_unsubscribe=(
                str(provider_trigger.get("listUnsubscribe") or "")
                if provider_trigger is not None
                else ""
            ),
            list_unsubscribe_post=(
                str(provider_trigger.get("listUnsubscribePost") or "")
                if provider_trigger is not None
                else ""
            ),
            body_text=trigger.text,
        )

    def _read_current_provider_message(
        self,
        *,
        account_id: str,
        classification: Mapping[str, object],
    ) -> Mapping[str, object] | None:
        if self.source_factory is None:
            return None
        account = self.store.get_account(account_id)
        if not isinstance(account, Mapping):
            raise ValueError("email task account is unavailable")
        source = self.source_factory(account)
        try:
            uid = _required_int(classification, "uid")
            batch = source.fetch_uid_batch(
                str(classification["folder"]),
                cursor_uidvalidity=_required_int(classification, "uidvalidity"),
                last_seen_uid=max(0, uid - 1),
                limit=2,
            )
            if int(batch.uidvalidity) != _required_int(classification, "uidvalidity"):
                raise ValueError("email task provider generation changed")
            for message in batch.messages:
                if _message_identity(message) == str(
                    classification["stable_message_identity"]
                ):
                    return message
            raise ValueError("email task source message is unavailable")
        finally:
            close = getattr(source, "logout", None)
            if callable(close):
                close()


def _task_payload(task: object) -> dict[str, object]:
    raw = getattr(task, "trigger_message_json", "")
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("email task metadata is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != "email_agent_action.v1":
        raise ValueError("email task metadata schema is invalid")
    return value


def _required_text(source: Mapping[str, object], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _required_int(source: Mapping[str, object], name: str) -> int:
    value = source.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_action_plan_snapshot(
    payload: Mapping[str, object],
    classification: Mapping[str, object],
) -> None:
    plan = classification.get("action_plan")
    if not isinstance(plan, Mapping):
        raise ValueError("email task action plan is unavailable")
    expected = {
        "action_plan_id": payload.get("action_plan_id"),
        "action_plan_version": payload.get("action_plan_version"),
        "model_id": payload.get("model_id"),
        "config_version": payload.get("config_version"),
    }
    if classification.get("current_action_plan_id") != expected[
        "action_plan_id"
    ] or any(plan.get(name) != value for name, value in expected.items()):
        raise ValueError("email task action plan version does not match durable state")


def _thread_message(row: Mapping[str, object]) -> EmailThreadMessage:
    return EmailThreadMessage(
        message_id=_required_text(row, "stable_message_identity"),
        sender=_required_text(row, "sender"),
        text=str(row.get("normalized_text") or ""),
        create_time=_required_text(row, "received_at"),
    )


def _message_identity(message: Mapping[str, object]) -> str:
    return str(message.get("stableMessageIdentity") or message.get("id") or "").strip()


def _message_sender(message: Mapping[str, object]) -> str:
    sender = message.get("from")
    if isinstance(sender, Mapping):
        return str(sender.get("email") or sender.get("name") or "unknown").strip()
    return str(sender or "unknown").strip()


def _message_text(message: Mapping[str, object]) -> str:
    return str(message.get("markdownBody") or message.get("textBody") or "")
