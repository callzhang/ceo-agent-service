"""Produce durable Email Agent tasks from already-persisted ActionPlans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.email_classifier_contracts import (
    EmailAction,
    EmailActionPlan,
    EmailAttachmentMetadata,
    EmailProviderLocator,
)
from app.email_store import EmailStore
from app.email_imap_readonly import (
    ephemeral_body_html,
    ephemeral_unsubscribe_authentication,
)
from app.email_task_adapter import (
    EmailAgentTaskInput,
    EmailAgentTaskRoute,
    EmailAgentTaskAdapter,
    EmailClassificationTask,
    EmailClassificationTaskAdapter,
    EmailClassificationTaskInput,
    EmailThreadMessage,
)
from app.email_unsubscribe import (
    browser_unsubscribe_entries,
    extract_unsubscribe_entries,
    select_exact_unsubscribe_entry,
)
from app.store import AutoReplyStore


class EmailClassificationTaskProducer:
    """Enqueue one durable classifier task without creating a CEO reply task."""

    def __init__(self, email_store: EmailStore):
        self.adapter = EmailClassificationTaskAdapter(email_store)

    def produce(
        self,
        message: Mapping[str, object],
        *,
        allowed_category_keys: Sequence[str],
        category_descriptions: Mapping[str, object],
        folder_targets: Mapping[str, str],
        config_version: str,
        unsubscribe_candidates: Sequence[object],
    ) -> EmailClassificationTask:
        return self.adapter.ensure_task(
            EmailClassificationTaskInput.from_message(
                message,
                allowed_category_keys=allowed_category_keys,
                category_descriptions=category_descriptions,
                folder_targets=folder_targets,
                config_version=config_version,
                unsubscribe_candidates=unsubscribe_candidates,
            )
        )


class EmailActionTaskProducer:
    """Turn an immutable, current ActionPlan into idempotent Agent tasks.

    The scan message is transient provider input.  The adapter is responsible
    for redacting it before durable task persistence, while the store performs
    the final current-ActionPlan authorization check atomically.
    """

    def __init__(self, task_store: AutoReplyStore, email_store: EmailStore):
        self.adapter = EmailAgentTaskAdapter(task_store, email_store)
        self.email_store = email_store

    def produce(
        self,
        action_plan: EmailActionPlan,
        message: Mapping[str, object],
    ) -> tuple[EmailAgentTaskRoute, ...]:
        if EmailAction.UNSUBSCRIBE not in action_plan.agent_actions:
            return ()
        task_input = self._task_input(action_plan, message)
        return self.produce_task_input(action_plan, task_input)

    def produce_task_input(
        self,
        action_plan: EmailActionPlan,
        task_input: EmailAgentTaskInput,
    ) -> tuple[EmailAgentTaskRoute, ...]:
        if EmailAction.UNSUBSCRIBE not in action_plan.agent_actions:
            return ()
        return self.adapter.ensure_action_plan_tasks(action_plan, task_input)

    def _task_input(
        self,
        action_plan: EmailActionPlan,
        message: Mapping[str, object],
    ) -> EmailAgentTaskInput:
        locator = EmailProviderLocator.model_validate(
            {
                "account_id": message.get("accountId"),
                "folder": message.get("folder"),
                "uidvalidity": message.get("uidValidity"),
                "uid": message.get("uid"),
                "rfc_message_id": message.get("messageId"),
                "thread_id": message.get("threadId"),
            }
        )
        stable_identity = str(
            message.get("stableMessageIdentity") or locator.stable_message_identity
        ).strip()
        if not stable_identity.startswith(f"{action_plan.account_id}:"):
            raise ValueError("email task message identity is not account-scoped")
        if locator.account_id != action_plan.account_id:
            raise ValueError("email task message account does not match ActionPlan")

        rows = self.email_store.list_email_context_thread(
            account_id=action_plan.account_id,
            stable_message_identity=stable_identity,
        )
        trigger_row = next(
            (
                row
                for row in rows
                if row.get("stable_message_identity") == stable_identity
            ),
            None,
        )
        if trigger_row is None:
            raise ValueError("persisted email task message is unavailable")
        thread_identity = str(
            trigger_row.get("thread_identity")
            or message.get("threadId")
            or stable_identity
        ).strip()
        if not thread_identity:
            raise ValueError("email task thread identity is unavailable")

        trigger = self._thread_message(
            stable_identity,
            trigger_row,
            message=message,
        )
        thread_messages = tuple(
            self._thread_message(str(row["stable_message_identity"]), row)
            for row in rows
            if row.get("stable_message_identity") != stable_identity
        )
        attachment_values = (
            message.get("attachments") or trigger_row.get("attachment_metadata") or ()
        )
        if not isinstance(attachment_values, Sequence) or isinstance(
            attachment_values, str | bytes
        ):
            raise ValueError("email task attachments must be a sequence")
        attachments = tuple(
            EmailAttachmentMetadata.model_validate(item) for item in attachment_values
        )
        body_text = str(message.get("markdownBody") or message.get("textBody") or "")
        body_html = ephemeral_body_html(message)
        authentication = ephemeral_unsubscribe_authentication(message)
        entries = browser_unsubscribe_entries(
            extract_unsubscribe_entries(
                list_unsubscribe=str(message.get("listUnsubscribe") or ""),
                list_unsubscribe_post=str(message.get("listUnsubscribePost") or ""),
                body_text=body_text,
                body_html=body_html,
                authentication_evidence=authentication,
            ),
            normalize_indexes=True,
        )
        selection = dict(action_plan.action_parameters.get(EmailAction.UNSUBSCRIBE, {}))
        if selection:
            entries = (select_exact_unsubscribe_entry(entries, selection),)
        return EmailAgentTaskInput(
            stable_message_identity=stable_identity,
            thread_identity=thread_identity,
            subject=str(message.get("subject") or trigger_row.get("subject") or ""),
            trigger=trigger,
            thread_messages=thread_messages,
            attachments=attachments,
            list_unsubscribe=str(message.get("listUnsubscribe") or ""),
            list_unsubscribe_post=str(message.get("listUnsubscribePost") or ""),
            body_text=body_text,
            body_html=body_html,
            unsubscribe_authentication=authentication,
        )

    @staticmethod
    def _thread_message(
        stable_identity: str,
        row: Mapping[str, object],
        *,
        message: Mapping[str, object] | None = None,
    ) -> EmailThreadMessage:
        sender = str(
            (
                (message or {}).get("from", {}).get("email")
                if isinstance((message or {}).get("from"), Mapping)
                else (message or {}).get("from")
            )
            or row.get("sender")
            or "unknown"
        ).strip()
        text = str(
            (message or {}).get("markdownBody")
            or (message or {}).get("textBody")
            or row.get("normalized_text")
            or ""
        )
        create_time = str(row.get("received_at") or "").strip()
        if not create_time:
            raise ValueError("email task message timestamp is unavailable")
        return EmailThreadMessage(
            message_id=stable_identity,
            sender=sender,
            text=text,
            create_time=create_time,
        )
