"""Exact source-version identity, independent of Task and Signal identity."""

import hashlib
import json


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
