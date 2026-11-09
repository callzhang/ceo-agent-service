"""DWS continuation for dependent actions after an exact verified DWS move."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Callable

from app.email_classifier_contracts import EmailAction
from app.email_action_reconcile import _received_datetime
from app.email_provider_actions import DeterministicEmailActionExecutor, ProviderActionResult
from app.email_store import EmailStore, email_imap_configuration_snapshot, StoredEmailAction


# DWS system follow-up flag maps to IMAP \Flagged. System important (11) and
# same-named custom labels are different signals, not substitutes.
DWS_FLAGGED_TAG_ID = "1"


class DwsVerifiedMoveActionExecutor:
    """Apply only read/important actions to the message bound by its DWS move receipt."""

    def __init__(
        self,
        dws: Any,
        *,
        mailbox: str,
        context: dict[str, Any],
        important_signals_reader: Callable[[], frozenset[str]],
    ):
        self.dws = dws
        self.mailbox = mailbox.strip()
        self.context = context
        self.important_signals_reader = important_signals_reader

    def execute(self, action: StoredEmailAction) -> ProviderActionResult:
        if action.action_type not in {EmailAction.FLAG_IMPORTANT, EmailAction.MARK_READ}:
            return self._failed(action, "unsupported_dws_action")
        try:
            before = self.read_verified_state(action)
            if before["satisfied"]:
                return self._done(action, "dws_readback_noop", before)
            if action.action_type is EmailAction.FLAG_IMPORTANT:
                tag_id = DWS_FLAGGED_TAG_ID
                command = [
                    self.dws.dws_bin, "mail", "message", "batch-update",
                    "--email", self.mailbox,
                    "--ids", str(self.context["message_id"]),
                    "--action", "addTags", "--tags", tag_id,
                    "--yes", "--format", "json",
                ]
            else:
                command = [
                    self.dws.dws_bin, "mail", "message", "batch-update",
                    "--email", self.mailbox,
                    "--ids", str(self.context["message_id"]),
                    "--action", "markRead", "--yes", "--format", "json",
                ]
            self._require_success(self.dws.run_json(command))
            after = self.read_verified_state(action)
            if not after["satisfied"]:
                return self._failed(action, "dws_action_readback_mismatch", after)
            return self._done(action, "dws_verified_readback", after)
        except Exception as exc:  # provider failures remain visible to the action owner
            return self._failed(action, f"dws_provider_error:{type(exc).__name__}")

    def _validate_context(self, action: StoredEmailAction) -> None:
        c = self.context
        if (
            not self.mailbox
            or c.get("account_id") != action.account_id
            or c.get("stable_message_identity") != action.locator.stable_message_identity
            or not str(c.get("message_id") or "").strip()
            or int(c.get("folder_id") or 0) <= 0
            or not str(c.get("receipt_digest") or "")
            or action.action_type not in {EmailAction.FLAG_IMPORTANT, EmailAction.MARK_READ}
        ):
            raise ValueError("verified DWS move binding is incomplete")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+", str(c.get("sender") or "")):
            raise ValueError("source sender is invalid")
        if not str(c.get("subject") or "").strip() or not str(c.get("received_at") or "").strip():
            raise ValueError("source message identity is incomplete")

    def read_verified_state(self, action: StoredEmailAction) -> dict[str, Any]:
        """Read the exact receipt-bound message without issuing a provider write."""
        self._validate_context(action)
        if action.action_type is EmailAction.FLAG_IMPORTANT and self.important_signals_reader() != frozenset({"\\Flagged"}):
            raise ValueError("DWS cannot prove the mailbox required important signals")
        query = self._identity_query()
        result = self._messages(query)
        exact = [m for m in result if self._matches_identity(m)]
        if len(exact) != 1:
            raise ValueError("DWS message identity is not unique")
        if action.action_type is EmailAction.FLAG_IMPORTANT:
            state_query = query + f" AND tag:{DWS_FLAGGED_TAG_ID}"
        else:
            state_query = query + " AND isRead:true"
        state_messages = self._messages(state_query)
        satisfied = any(self._matches_identity(m) for m in state_messages)
        safe_identity = {
            "message_id": self.context["message_id"],
            "folder_id": self.context["folder_id"],
            "conversation_id": self.context.get("conversation_id", ""),
            "action": action.action_type.value,
            "satisfied": satisfied,
            "system_tag_id": DWS_FLAGGED_TAG_ID if action.action_type is EmailAction.FLAG_IMPORTANT else None,
            "received_at": exact[0].get("receivedDateTime"),
        }
        digest = sha256(
            json.dumps(safe_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {"satisfied": satisfied, "digest": digest}

    def _identity_query(self) -> str:
        sender = str(self.context["sender"]).strip()
        subject = str(self.context["subject"]).replace('"', r'\"').strip()
        return (
            f'from:{sender} AND subject:"{subject}" '
            f'AND folderId:{int(self.context["folder_id"])}'
        )

    def _messages(self, query: str) -> list[dict[str, Any]]:
        result = self.dws.run_json([
            self.dws.dws_bin, "mail", "message", "search",
            "--email", self.mailbox, "--query", query,
            "--limit", "100", "--format", "json",
        ])
        if not isinstance(result, dict) or not isinstance(result.get("messages"), list):
            raise ValueError("DWS search response is incomplete")
        messages = [m for m in result["messages"] if isinstance(m, dict)]
        try:
            total = int(result.get("total", len(messages)))
        except (TypeError, ValueError) as exc:
            raise ValueError("DWS search total is invalid") from exc
        if total == 0 and messages == [{"ccRecipients": None, "toRecipients": None}]:
            return []
        if total != len(messages):
            raise ValueError("DWS search result is incomplete")
        return messages

    def _matches_identity(self, message: dict[str, Any]) -> bool:
        sender = message.get("from")
        sender_email = sender.get("email") if isinstance(sender, dict) else ""
        conversation_id = str(message.get("conversationId") or "")
        if not (
            str(message.get("id") or "") == str(self.context["message_id"])
            and bool(conversation_id)
            and str(sender_email).casefold() == str(self.context["sender"]).casefold()
            and str(message.get("subject") or "") == str(self.context["subject"])
        ):
            return False
        try:
            actual = _received_datetime(str(message["receivedDateTime"]))
            expected = _received_datetime(str(self.context["received_at"]))
        except (KeyError, TypeError, ValueError):
            return False
        if abs((actual - expected).total_seconds()) > 10:
            return False
        proof = sha256(
            json.dumps(
                {
                    "account": self.mailbox.casefold(),
                    "sender": str(self.context["sender"]).casefold(),
                    "subject": str(self.context["subject"]),
                    "conversation": conversation_id,
                    "received_at": message["receivedDateTime"],
                    "dws_message_id": str(self.context["message_id"]),
                    "folder_id": str(self.context["folder_id"]),
                    "action_id": str(self.context["move_action_id"]),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return proof == self.context["receipt_digest"]

    @staticmethod
    def _require_success(result: Any) -> None:
        if not isinstance(result, dict) or result.get("success") is False or result.get("ok") is False:
            raise ValueError("DWS did not confirm the message update")

    @staticmethod
    def _done(action: StoredEmailAction, operation: str, readback: dict[str, Any]) -> ProviderActionResult:
        return ProviderActionResult(
            status="done",
            provider_operation=operation,
            provider_target=action.locator.stable_message_identity,
            provider_result_id=(
                f"dws-mail:{readback['digest']}:{action.action_type.value}"
            ),
            retryable=False,
        )

    @staticmethod
    def _failed(action: StoredEmailAction, reason: str, readback: dict[str, Any] | None = None) -> ProviderActionResult:
        suffix = "" if readback is None else f":{readback.get('digest', '')}"
        return ProviderActionResult(
            status="failed",
            provider_operation="dws_mail_action",
            provider_target=action.locator.stable_message_identity,
            provider_result_id="",
            error=f"{reason}{suffix}",
            retryable=False,
        )


def release_dws_source_verified_retry(
    store: EmailStore, *, account_id: str, action_id: str, dws: Any, apply: bool,
    imap_provider_factory: Callable[[], Any],
) -> dict[str, Any]:
    """Operator-only source verification; the normal worker owns any execution."""
    rows = store.list_failed_direct_actions(
        account_id=account_id, action_id=action_id,
        action_types=(EmailAction.FLAG_IMPORTANT, EmailAction.MARK_READ), limit=1,
    )
    if not rows:
        return {"outcome": "not_current_failed", "eligible": False, "released": False}
    failed = rows[0]
    account = store.get_account(account_id)
    if account is None or not account.get("enabled"):
        return {"outcome": "account_unavailable", "eligible": False, "released": False}
    configuration_snapshot = getattr(imap_provider_factory, "account_configuration_snapshot", None)
    if configuration_snapshot is None:
        return {"outcome": "configuration_evidence_missing", "eligible": False, "released": False}
    if configuration_snapshot != email_imap_configuration_snapshot(account):
        return {"outcome": "configuration_changed", "eligible": False, "released": False}
    context = store.verified_dws_move_context_for_action(failed.action)
    if context is None:
        return {"outcome": "move_binding_unavailable", "eligible": False, "released": False}
    state = DwsVerifiedMoveActionExecutor(
        dws, mailbox=str(account["email_address"]), context=context,
        important_signals_reader=lambda: DeterministicEmailActionExecutor(
            imap_provider_factory()
        ).required_important_signals(context["target_folder"]),
    ).read_verified_state(failed.action)
    eligible = store.release_source_verified_dws_action(
        failed, dws_context=context, source_revision=state["digest"],
        requested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        apply=apply, account_configuration_snapshot=configuration_snapshot,
    )
    return {
        "outcome": "source_verified", "eligible": eligible,
        "released": eligible and apply, "source_satisfied": state["satisfied"],
        "source_readback_digest": state["digest"],
    }
