"""DWS continuation for dependent actions after an exact verified DWS move."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any

from app.email_classifier_contracts import EmailAction
from app.email_provider_actions import ProviderActionResult
from app.email_store import StoredEmailAction


class DwsVerifiedMoveActionExecutor:
    """Apply only read/important actions to the message bound by its DWS move receipt."""

    def __init__(
        self,
        dws: Any,
        *,
        mailbox: str,
        context: dict[str, Any],
    ):
        self.dws = dws
        self.mailbox = mailbox.strip()
        self.context = context

    def execute(self, action: StoredEmailAction) -> ProviderActionResult:
        if action.action_type not in {EmailAction.FLAG_IMPORTANT, EmailAction.MARK_READ}:
            return self._failed(action, "unsupported_dws_action")
        try:
            self._validate_context(action)
            before = self._read(action)
            if before["satisfied"]:
                return self._done(action, "dws_readback_noop", before)
            if action.action_type is EmailAction.FLAG_IMPORTANT:
                tag_id = self._important_tag_id()
                command = [
                    self.dws.dws_bin, "mail", "message", "batch-update",
                    "--email", self.mailbox,
                    "--ids", str(self.context["message_id"]),
                    "--action", "addTags", "--tags", tag_id, "--format", "json",
                ]
            else:
                command = [
                    self.dws.dws_bin, "mail", "message", "batch-update",
                    "--email", self.mailbox,
                    "--ids", str(self.context["message_id"]),
                    "--action", "markRead", "--format", "json",
                ]
            self._require_success(self.dws.run_json(command))
            after = self._read(action)
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

    def _read(self, action: StoredEmailAction) -> dict[str, Any]:
        query = self._identity_query()
        result = self._messages(query)
        exact = [m for m in result if self._matches_identity(m)]
        if len(exact) != 1:
            raise ValueError("DWS message identity is not unique")
        if action.action_type is EmailAction.FLAG_IMPORTANT:
            state_query = query + " AND tag:important"
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
            actual = datetime.fromisoformat(
                str(message["receivedDateTime"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            expected = datetime.fromisoformat(
                str(self.context["received_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
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

    def _important_tag_id(self) -> str:
        result = self.dws.run_json([
            self.dws.dws_bin, "mail", "tag", "list",
            "--email", self.mailbox, "--format", "json",
        ])
        tags = result.get("tags", result.get("items", [])) if isinstance(result, dict) else result
        if not isinstance(tags, list):
            raise ValueError("DWS tag list is incomplete")
        matches = [
            str(tag.get("id")) for tag in tags
            if isinstance(tag, dict) and tag.get("name") == "重要" and str(tag.get("id") or "").isdigit()
        ]
        if len(matches) != 1:
            raise ValueError("DWS important tag is missing or ambiguous")
        return matches[0]

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
