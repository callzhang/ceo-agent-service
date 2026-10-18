"""Exact source-version identity, independent of Task and Signal identity."""

import hashlib
import json
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from app.task_models import WorkItem
    from app.business_source_reader import BusinessTaskSignalFact, PreparedSources
    from app.task_semantic_models import SourceCitation


SOURCE_CONTEXT_LIMIT = 2048


def source_is_observed(source_type: str) -> bool:
    """Provenance quotations are not observations of their referenced original."""
    return source_type not in {"memory_provenance", "session_provenance"}


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
        source_type,
        source_ref,
        source_time,
        conversation_id,
        author_user_id,
        author_name,
        author_kind,
        evidence_text,
    ]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _decoded_quote(value: object, quote: str, path: tuple = ()) -> dict | None:
    if isinstance(value, str):
        start = value.find(quote)
        if start >= 0:
            return {
                "path": list(path),
                "start": start,
                "end": start + len(quote),
                "text": quote,
            }
    elif isinstance(value, dict):
        for key, child in value.items():
            found = _decoded_quote(child, quote, (*path, key))
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _decoded_quote(child, quote, (*path, index))
            if found is not None:
                return found
    return None


def _document_projection(body: str, quotes: Iterable[str], *, full: bool) -> dict:
    pinned: list[tuple[int, int]] = []
    decoded: list[dict] = []
    # Structured-source parsing is presentation only, never a new proof or identity.
    try:
        structured = json.loads(body)
    except ValueError:
        structured = None
    for quote in dict.fromkeys(quotes):
        start = body.find(quote)
        if start >= 0:
            pinned.append((start, start + len(quote)))
        else:
            excerpt = _decoded_quote(structured, quote)
            if excerpt is not None and excerpt not in decoded:
                decoded.append(excerpt)
    if full or len(body) <= SOURCE_CONTEXT_LIMIT:
        ranges = [(0, len(body))]
    else:
        remaining = max(
            0, SOURCE_CONTEXT_LIMIT - sum(end - start for start, end in pinned)
            - sum(len(excerpt["text"]) for excerpt in decoded)
        )
        head = remaining // 2
        ranges = pinned + [(0, head), (len(body) - (remaining - head), len(body))]
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start == end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    visible_length = sum(end - start for start, end in merged)
    projected_length = visible_length + sum(len(excerpt["text"]) for excerpt in decoded)
    return {
        "full_length": len(body),
        "truncated": visible_length != len(body),
        "visible_ranges": [
            {"start": start, "end": end, "text": body[start:end]}
            for start, end in merged
        ],
        "decoded_excerpts": decoded,
        "citation_budget_exceeded": not full and projected_length > SOURCE_CONTEXT_LIMIT,
    }


def source_bundle(
    signals: Iterable["BusinessTaskSignalFact"],
    *,
    prepared_sources: "PreparedSources",
    current_work_item: "WorkItem | None" = None,
    citations: Iterable["SourceCitation"] = (),
) -> dict:
    """One exact version key per original, with bounded visible source text."""
    from app.business_source_reader import AvailableSource

    documents: dict[str, dict] = {}
    signal_documents: dict[int, str] = {}
    references: list[dict] = []
    before = 0
    unknown_length = False
    for signal in signals:
        key = signal.source.identity_key
        reading = prepared_sources.reading_for(signal)
        body = reading.body if isinstance(reading, AvailableSource) else None
        if body is None:
            unknown_length = True
        else:
            before += len(body)
        references.append({
            "id": signal.id, "document_id": key,
            "source_type": signal.source_type, "source_ref": signal.source_ref,
            "source_time": signal.source_time,
            "conversation_id": signal.conversation_id,
            "conversation_title": signal.conversation_title,
            "author_user_id": signal.author_user_id,
            "author_name": signal.author_name,
            "author_kind": signal.author_kind.value,
            "context_json": signal.context_json, "created_at": signal.created_at,
            "source_available": body is not None,
            "source_unavailable_reason": None if body is not None else reading.reason.value,
        })
        signal_documents[signal.id] = key
        documents.setdefault(key, {
            "body": body, "quotes": [], "full": False, "binding": signal.source,
            "reason": None if body is not None else reading.reason.value,
        })
    for citation in citations:
        key = signal_documents.get(citation.signal_id)
        if key is not None and any(
            ref["id"] == citation.signal_id and ref["source_ref"] == citation.source_ref
            for ref in references
        ):
            documents[key]["quotes"].append(citation.source_excerpt)
    current = None
    if current_work_item is not None:
        item = current_work_item
        key = source_document_key(
            source_type=item.source.type.value,
            source_ref=item.source.ref,
            source_time=item.source.created_at,
            conversation_id=item.source.conversation_id,
            author_user_id=item.context.sender_user_id,
            author_name=item.context.sender,
            author_kind="human" if item.context.sender_user_id else "unknown",
            evidence_text=item.summary,
        )
        documents.setdefault(key, {
            "body": item.summary, "quotes": [], "full": True,
            "binding": None, "reason": None,
        })["full"] = True
        if documents[key]["body"] is None:
            documents[key]["body"] = item.summary
            documents[key]["reason"] = None
        before += len(item.summary)
        current = item.model_dump(mode="json")
        current.pop("summary")
        current["scheduled_consumer"].pop("skill_protocol", None)
        current["scheduled_consumer"].pop("skill_materials", None)
        current["document_id"] = key
    rendered = []
    for key, doc in documents.items():
        body = doc["body"]
        if body is None:
            binding = doc["binding"]
            rendered.append({
                "document_id": key, "source_available": False,
                "source_unavailable_reason": doc["reason"],
                "body_utf8_bytes": binding.body_utf8_bytes,
                "source_locator": (binding.native_provenance_json
                                   or binding.provider_locator_json),
                "saved_quotations": list(dict.fromkeys(doc["quotes"])),
                "full_length": None, "truncated": None,
                "visible_ranges": [], "decoded_excerpts": [],
                "citation_budget_exceeded": None,
            })
        else:
            rendered.append({
                "document_id": key, "source_available": True,
                "source_unavailable_reason": None,
                **_document_projection(body, doc["quotes"], full=doc["full"]),
            })
    return {
        "source_documents": rendered,
        "source_signals": references,
        **({"current_work_item": current} if current is not None else {}),
        "source_metrics": {
            "signal_count": len(references),
            "document_count": len(rendered),
            "body_chars_before_sharing": None if unknown_length else before,
            "unique_body_chars": None if any(doc["full_length"] is None for doc in rendered)
                                 else sum(doc["full_length"] for doc in rendered),
            "visible_body_chars": sum(
                len(span["text"]) for doc in rendered
                for span in (*doc["visible_ranges"], *doc["decoded_excerpts"])
            ),
        },
    }
