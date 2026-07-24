"""Closed, provider-bound contracts for Feishu IM side effects.

The model never receives arbitrary SDK payloads.  Every action is derived from
trusted local context, serialized canonically, and bound to one authenticated
application before it can enter the outbox.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.feishu.payloads import contains_untrusted_at_markup


FeishuActionKind = Literal[
    "add_reaction",
    "recall_message",
    "handoff_notify",
]
FeishuActionStatus = Literal[
    "ready",
    "sending",
    "sent",
    "retry",
    "result_unknown",
    "failed",
    "rejected",
]

ACTION_UUID_NAMESPACE = UUID("d3aa88ae-560e-5424-9bd9-7cbd6463dc08")

# Keep this deliberately small.  These names are present in the official
# Feishu message-content documentation.  Expanding the set requires a contract
# test and documentation update rather than forwarding arbitrary model text.
REACTION_EMOJI_ALIASES = {
    "👍": "THUMBSUP",
    "THUMBSUP": "THUMBSUP",
    ":THUMBSUP:": "THUMBSUP",
    "👌": "OK",
    "✅": "OK",
    "OK": "OK",
    ":OK:": "OK",
    "🙂": "SMILE",
    "😊": "SMILE",
    "SMILE": "SMILE",
    ":SMILE:": "SMILE",
}


def normalize_reaction_emoji(value: str) -> str:
    normalized = str(value or "").strip()
    result = REACTION_EMOJI_ALIASES.get(normalized)
    if not result:
        raise ValueError("unsupported Feishu reaction emoji")
    return result


def canonical_payload(payload: dict[str, Any]) -> str:
    """Serialize a JSON-only payload identically across restarts."""
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Feishu action payload must be strict JSON") from exc
    if len(encoded.encode("utf-8")) > 8 * 1024:
        raise ValueError("Feishu action payload is too large")
    return encoded


def action_payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def action_idempotency_key(
    *, app_id: str, reply_task_id: int, action_key: str, target_id: str
) -> str:
    app = app_id.strip()
    key = action_key.strip()
    target = target_id.strip()
    if not app or reply_task_id <= 0 or not key or not target:
        raise ValueError("Feishu action idempotency identity is incomplete")
    return str(
        uuid5(
            ACTION_UUID_NAMESPACE,
            f"{app}\0{reply_task_id}\0{key}\0{target}",
        )
    )


def action_approval_hash(
    *,
    reply_task_id: int,
    attempt_id: int,
    app_id: str,
    chat_id: str,
    action_key: str,
    kind: FeishuActionKind,
    target_id: str,
    payload_sha256: str,
    idempotency_key: str,
    risk: str,
    review_generation: int,
) -> str:
    if (
        isinstance(reply_task_id, bool)
        or reply_task_id <= 0
        or isinstance(attempt_id, bool)
        or attempt_id <= 0
        or isinstance(review_generation, bool)
        or review_generation <= 0
    ):
        raise ValueError("Feishu approval identity is incomplete")
    fields = (
        "3",
        str(reply_task_id),
        str(attempt_id),
        app_id.strip(),
        chat_id.strip(),
        action_key.strip(),
        kind,
        target_id.strip(),
        payload_sha256,
        idempotency_key.strip(),
        risk,
        str(review_generation),
    )
    if any(not field for field in fields):
        raise ValueError("Feishu approval identity is incomplete")
    return hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()


class FeishuMessageAction(BaseModel):
    """One immutable IM mutation stored in the local action outbox."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = 0
    reply_task_id: int = Field(gt=0)
    attempt_id: int = Field(gt=0)
    app_id: str = Field(min_length=1, max_length=128)
    chat_id: str = Field(default="", max_length=256)
    action_key: str = Field(min_length=1, max_length=128)
    kind: FeishuActionKind
    target_message_id: str = Field(default="", max_length=256)
    target_open_id: str = Field(default="", max_length=256)
    payload_json: str = Field(default="{}", max_length=8192)
    payload_sha256: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=50)
    review_generation: int = Field(default=1, ge=1)
    approval_hash: str = Field(min_length=64, max_length=64)
    risk: Literal["R2", "R4"]
    status: FeishuActionStatus = "ready"
    remote_id: str = ""
    request_log_id: str = ""
    attempts: int = 0
    remote_failures: int = Field(default=0, ge=0)
    lease_token: str = ""
    mutation_started_at: str = ""
    approved_at: str = ""
    approved_by: str = ""
    locked_at: str = ""
    available_at: str = ""
    error_code: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""

    @field_validator("app_id", "chat_id", "action_key", "target_message_id", "target_open_id")
    @classmethod
    def _strip_identity(cls, value: str) -> str:
        normalized = value.strip()
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("Feishu action identity contains control characters")
        return normalized

    @model_validator(mode="after")
    def _validate_binding(self) -> "FeishuMessageAction":
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid Feishu action payload JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Feishu action payload must be an object")
        canonical = canonical_payload(payload)
        if canonical != self.payload_json:
            raise ValueError("Feishu action payload must be canonical")
        if action_payload_hash(payload) != self.payload_sha256:
            raise ValueError("Feishu action payload hash mismatch")
        if self.kind in {"add_reaction", "recall_message"}:
            if not self.target_message_id or self.target_open_id:
                raise ValueError(f"{self.kind} requires only target_message_id")
        elif self.kind == "handoff_notify":
            if not self.target_open_id or self.target_message_id:
                raise ValueError("handoff_notify requires only target_open_id")

        if self.kind == "add_reaction":
            if set(payload) != {"emoji_type"}:
                raise ValueError("add_reaction payload must contain only emoji_type")
            normalize_reaction_emoji(str(payload.get("emoji_type") or ""))
            if self.risk != "R2":
                raise ValueError("add_reaction must use risk R2")
        elif self.kind == "recall_message":
            if payload:
                raise ValueError("recall_message payload must be empty")
            if self.risk != "R4":
                raise ValueError("recall_message must use risk R4")
        elif self.kind == "handoff_notify":
            if set(payload) != {"text"}:
                raise ValueError("handoff_notify payload must contain only text")
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                raise ValueError("handoff notification text is invalid")
            if contains_untrusted_at_markup(text):
                raise ValueError(
                    "handoff notification contains untrusted at markup"
                )
            if self.risk != "R2":
                raise ValueError("handoff_notify must use risk R2")

        expected_approval = action_approval_hash(
            reply_task_id=self.reply_task_id,
            attempt_id=self.attempt_id,
            app_id=self.app_id,
            chat_id=self.chat_id,
            action_key=self.action_key,
            kind=self.kind,
            target_id=self.target_message_id or self.target_open_id,
            payload_sha256=self.payload_sha256,
            idempotency_key=self.idempotency_key,
            risk=self.risk,
            review_generation=self.review_generation,
        )
        if self.approval_hash != expected_approval:
            raise ValueError("Feishu action approval hash mismatch")
        return self


def build_message_action(
    *,
    reply_task_id: int,
    attempt_id: int,
    app_id: str,
    chat_id: str,
    action_key: str,
    kind: FeishuActionKind,
    target_message_id: str = "",
    target_open_id: str = "",
    payload: dict[str, Any] | None = None,
) -> FeishuMessageAction:
    normalized_payload = dict(payload or {})
    if kind == "add_reaction":
        normalized_payload = {
            "emoji_type": normalize_reaction_emoji(
                str(normalized_payload.get("emoji_type") or "")
            )
        }
    payload_json = canonical_payload(normalized_payload)
    payload_sha256 = action_payload_hash(normalized_payload)
    target_id = target_message_id.strip() or target_open_id.strip()
    risk = "R4" if kind == "recall_message" else "R2"
    idempotency_key = action_idempotency_key(
        app_id=app_id,
        reply_task_id=reply_task_id,
        action_key=action_key,
        target_id=target_id,
    )
    return FeishuMessageAction(
        reply_task_id=reply_task_id,
        attempt_id=attempt_id,
        app_id=app_id,
        chat_id=chat_id,
        action_key=action_key,
        kind=kind,
        target_message_id=target_message_id,
        target_open_id=target_open_id,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
        idempotency_key=idempotency_key,
        approval_hash=action_approval_hash(
            reply_task_id=reply_task_id,
            attempt_id=attempt_id,
            app_id=app_id,
            chat_id=chat_id,
            action_key=action_key,
            kind=kind,
            target_id=target_id,
            payload_sha256=payload_sha256,
            idempotency_key=idempotency_key,
            risk=risk,
            review_generation=1,
        ),
        review_generation=1,
        risk=risk,
    )
