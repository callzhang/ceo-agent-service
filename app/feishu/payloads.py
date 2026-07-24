"""Restricted outbound reply payloads built only from local trusted fields."""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_TEXT_BYTES = 150 * 1024
MAX_POST_BYTES = 30 * 1024
MAX_MENTIONS = 20
LONG_REPLY_THRESHOLD = 2_800
# ``lark-channel-sdk==1.2.0`` materializes text/post bodies in chunks of at
# most 3,500 Python characters.  Splitting before the SDK is important: the
# SDK gives only the final failed result when a later implicit chunk fails and
# generates random UUIDs after the first request.  Keeping this constant local
# makes every remote request independently observable and idempotent.
MAX_WIRE_CHUNK_CHARS = 3_500
MAX_DELIVERY_CHUNKS = 100
DELIVERY_CHUNK_UUID_NAMESPACE = UUID("50d74149-5306-5ea5-ad8f-18f436e49c15")
_OPEN_ID_RE = re.compile(r"^ou_[A-Za-z0-9_-]+$")
# The pinned SDK interprets inline ``<at ...>...</at>`` markup as a real
# mention.  Reply text is untrusted model output, so only the separate typed
# ``mention_open_ids`` field may create that markup.  Match an opening or
# closing tag boundary without rejecting innocent text such as ``<atlas>``.
_UNTRUSTED_AT_MARKUP_RE = re.compile(
    r"<\s*/?\s*at(?=\s|/?>)",
    re.IGNORECASE,
)
_MARKDOWN_RE = re.compile(
    r"(^|\n)(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|```)|\[[^\]]+\]\([^)]+\)",
    re.MULTILINE,
)


def contains_untrusted_at_markup(value: str) -> bool:
    """Detect SDK-active mention markup, including encoded variants.

    HTML entities are decoded a bounded number of times because otherwise a
    later rendering layer can turn apparently inert model text back into an
    active ``<at>`` tag.  A leading Markdown escape does not make the SDK sink
    safe, so the search deliberately need not start at the backslash.
    """
    candidate = str(value or "")
    for _ in range(4):
        if _UNTRUSTED_AT_MARKUP_RE.search(candidate):
            return True
        decoded = html.unescape(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return False


class FeishuReplyPayload(BaseModel):
    """A text/post payload; arbitrary SDK dictionaries are impossible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["text", "post"]
    text: str = Field(min_length=1)
    mention_open_ids: tuple[str, ...] = Field(default=(), max_length=MAX_MENTIONS)
    version: Literal[1] = 1

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Feishu reply text is empty")
        if contains_untrusted_at_markup(stripped):
            raise ValueError("Feishu reply contains untrusted at markup")
        return stripped

    @field_validator("mention_open_ids")
    @classmethod
    def _validate_mentions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        ordered: list[str] = []
        for open_id in value:
            if not isinstance(open_id, str) or not _OPEN_ID_RE.fullmatch(open_id):
                raise ValueError("Feishu mention must be a validated open_id")
            if open_id not in ordered:
                ordered.append(open_id)
        return tuple(ordered)

    @model_validator(mode="after")
    def _validate_size(self) -> "FeishuReplyPayload":
        size = len(self.text.encode("utf-8"))
        maximum = MAX_TEXT_BYTES if self.kind == "text" else MAX_POST_BYTES
        if size > maximum:
            raise ValueError(f"Feishu {self.kind} payload exceeds byte limit")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def choose_reply_payload(
    text: str, *, trusted_mention_open_ids: tuple[str, ...] = ()
) -> FeishuReplyPayload:
    """Choose deterministically; only trusted code may supply mention IDs."""
    normalized = str(text or "").strip()
    text_bytes = len(normalized.encode("utf-8"))
    # A post has the smaller provider contract.  Content above that contract
    # remains supported as plain text and is split deterministically at send
    # time instead of being rejected merely because it contains Markdown.
    kind = (
        "post"
        if text_bytes <= MAX_POST_BYTES
        and len(normalized) <= MAX_WIRE_CHUNK_CHARS
        and (text_bytes > LONG_REPLY_THRESHOLD or _MARKDOWN_RE.search(normalized))
        else "text"
    )
    return FeishuReplyPayload(
        kind=kind,
        text=normalized,
        mention_open_ids=trusted_mention_open_ids,
    )


def split_reply_payload(
    payload: FeishuReplyPayload, *, limit: int = MAX_WIRE_CHUNK_CHARS
) -> tuple[str, ...]:
    """Return an exact, deterministic local wire-chunk plan.

    The split is character based because that is the pinned SDK's own
    boundary.  Newline cuts are preferred without dropping the newline, so
    concatenating the chunks reconstructs the approved payload exactly.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("Feishu wire chunk limit must be positive")
    text = payload.text
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline >= start:
                end = newline + 1
        if end <= start:  # defensive; the hard limit always makes progress
            end = min(start + limit, len(text))
        chunks.append(text[start:end])
        start = end
    if not chunks or len(chunks) > MAX_DELIVERY_CHUNKS:
        raise ValueError("Feishu delivery chunk plan is invalid")
    if payload.kind == "post" and len(chunks) != 1:
        raise ValueError(
            "multi-chunk Feishu replies must use text format"
        )
    return tuple(chunks)


def delivery_chunk_plan_sha256(chunks: tuple[str, ...]) -> str:
    """Hash exact ordered wire chunks, including every split boundary."""
    if (
        not isinstance(chunks, tuple)
        or not chunks
        or len(chunks) > MAX_DELIVERY_CHUNKS
        or any(not isinstance(chunk, str) or not chunk for chunk in chunks)
    ):
        raise ValueError("Feishu delivery chunk plan is invalid")
    canonical = json.dumps(
        {
            "chunk_sha256": [
                hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                for chunk in chunks
            ],
            "version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def delivery_approval_hash(
    *,
    reply_task_id: int,
    attempt_id: int,
    app_id: str,
    chat_id: str,
    reply_to_message_id: str,
    reply_in_thread: bool,
    payload_sha256: str,
    idempotency_key: str,
    expected_chunks: int,
    chunk_plan_sha256: str,
    review_generation: int,
) -> str:
    """Hash the complete immutable delivery preview used by approval CAS."""
    if (
        reply_task_id <= 0
        or attempt_id <= 0
        or expected_chunks <= 0
        or isinstance(review_generation, bool)
        or review_generation <= 0
    ):
        raise ValueError("Feishu delivery approval identity is incomplete")
    values = {
        "app_id": app_id.strip(),
        "attempt_id": attempt_id,
        "chat_id": chat_id.strip(),
        "chunk_plan_sha256": chunk_plan_sha256.strip(),
        "expected_chunks": expected_chunks,
        "idempotency_key": idempotency_key.strip(),
        "payload_sha256": payload_sha256.strip(),
        "reply_in_thread": bool(reply_in_thread),
        "reply_task_id": reply_task_id,
        "reply_to_message_id": reply_to_message_id.strip(),
        "review_generation": review_generation,
        "version": 3,
    }
    if any(
        not values[key]
        for key in (
            "app_id",
            "chat_id",
            "chunk_plan_sha256",
            "idempotency_key",
            "payload_sha256",
            "reply_to_message_id",
        )
    ):
        raise ValueError("Feishu delivery approval identity is incomplete")
    canonical = json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def delivery_chunk_idempotency_key(
    *,
    delivery_key: str,
    ordinal: int,
    expected_chunks: int,
    chunk_plan_sha256: str,
    payload_sha256: str,
) -> str:
    """Derive one stable provider UUID for each immutable local chunk."""
    key = str(delivery_key or "").strip()
    plan_hash = str(chunk_plan_sha256 or "").strip()
    payload_hash = str(payload_sha256 or "").strip()
    if (
        not key
        or len(plan_hash) != 64
        or any(character not in "0123456789abcdef" for character in plan_hash)
        or len(payload_hash) != 64
        or any(character not in "0123456789abcdef" for character in payload_hash)
        or ordinal < 0
        or expected_chunks <= 0
        or ordinal >= expected_chunks
    ):
        raise ValueError("Feishu delivery chunk identity is incomplete")
    return str(
        uuid5(
            DELIVERY_CHUNK_UUID_NAMESPACE,
            f"{key}\0{ordinal}\0{expected_chunks}\0{plan_hash}\0{payload_hash}",
        )
    )
