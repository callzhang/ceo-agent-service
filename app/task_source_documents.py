"""Exact source-version identity, independent of Task and Signal identity."""

import hashlib
import json


def source_contains_quote(raw: str, quote: str) -> bool:
    """Check raw text or one decoded string inside a structured source body."""
    if not quote.strip():
        return False
    if quote in raw:
        return True
    try:
        payload = json.loads(raw)
    except ValueError:
        return False

    def contains(value: object) -> bool:
        if isinstance(value, str):
            return quote in value
        if isinstance(value, dict):
            return any(contains(child) for child in value.values())
        if isinstance(value, list):
            return any(contains(child) for child in value)
        return False

    return contains(payload)


def source_document_key(
    *,
    source_type: str,
    source_ref: str,
    source_time: str,
    conversation_id: str,
    author_user_id: str,
    author_name: str,
    author_kind: str,
    evidence_text: str,
) -> str:
    identity = [
        source_type, source_ref, source_time, conversation_id,
        author_user_id, author_name, author_kind, evidence_text,
    ]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
