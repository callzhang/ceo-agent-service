"""Share repeated source projections within one Task semantic context."""

import json
from collections import Counter
from typing import Any


def _projection_key(document: dict[str, Any]) -> str | None:
    visible_ranges = document.get("visible_ranges")
    decoded_excerpts = document.get("decoded_excerpts")
    if not isinstance(visible_ranges, list) or not isinstance(decoded_excerpts, list):
        return None
    if not visible_ranges and not decoded_excerpts:
        return None
    return json.dumps(
        [visible_ranges, decoded_excerpts],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def share_source_document_projections(payload: dict[str, Any]) -> dict[str, Any]:
    """Reference exact duplicate ranges/excerpts without merging document identity.

    The compact form is used only when serializing it reduces the complete payload.
    """
    documents = payload.get("source_documents")
    if not isinstance(documents, list):
        raise TypeError("Task semantic context requires source_documents")

    keys = [
        _projection_key(document) if isinstance(document, dict) else None
        for document in documents
    ]
    counts = Counter(key for key in keys if key is not None)
    repeated = {key for key, count in counts.items() if count > 1}
    if not repeated:
        return payload

    first_document_by_key: dict[str, Any] = {}
    compact_documents: list[Any] = []
    for document, key in zip(documents, keys, strict=True):
        if key not in repeated or not isinstance(document, dict):
            compact_documents.append(document)
            continue

        original_document_id = first_document_by_key.get(key)
        if original_document_id is None:
            original_document_id = document.get("document_id")
            first_document_by_key[key] = original_document_id
            compact_documents.append(document)
            continue

        compact_documents.append(
            {
                **{
                    name: value
                    for name, value in document.items()
                    if name not in {"visible_ranges", "decoded_excerpts"}
                },
                "duplicate_of_document_id": original_document_id,
            }
        )

    compacted = {**payload, "source_documents": compact_documents}
    original_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    compacted_size = len(
        json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
    )
    return compacted if compacted_size < original_size else payload
