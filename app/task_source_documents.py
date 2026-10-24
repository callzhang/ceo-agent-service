"""Exact source-version identity, independent of Task and Signal identity."""

import hashlib
import json
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from app.task_models import WorkItem
    from app.business_source_reader import (
        BusinessTaskSignalFact,
        PreparedSources,
        SourceVersionBinding,
    )
    from app.task_semantic_models import SourceCitation


SOURCE_CONTEXT_LIMIT = 2048

# Fields every Signal of one source version normally shares. They are written
# once on the document entry; a Signal repeats one only when its value differs.
_DOCUMENT_SOURCE_FIELDS = (
    "source_type", "source_ref", "source_time", "conversation_id",
    "conversation_title", "author_user_id", "author_name", "author_kind",
)


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


def source_has_external_locator(binding: "SourceVersionBinding") -> bool:
    """Whether the Agent can fetch this source version itself.

    Such a binding keeps a provider or native locator, and the Signal's
    `source_ref` names the minutes or document. A source without one (a Console
    action record) exists only as the service's own record.
    """
    return bool(binding.native_provenance_json or binding.provider_locator_json)


def source_bundle(
    signals: Iterable["BusinessTaskSignalFact"],
    *,
    prepared_sources: "PreparedSources",
    citations: Iterable["SourceCitation"] = (),
) -> dict:
    """One exact version key per original; text only where the Agent cannot fetch it.

    A source with an external locator (its `source_ref` names the minutes or
    document, and the binding keeps a provider or native locator) is not
    copied into the input: the Agent reads it itself and the service checks
    every cited quote against the exact bound version when a decision is
    applied. A source with no locator has nowhere to be read from, so its text
    is projected as before.
    """
    from app.business_source_reader import AvailableSource

    documents: dict[str, dict] = {}
    signal_documents: dict[int, str] = {}
    references: list[dict] = []
    first_reference: dict[str, dict] = {}
    for signal in signals:
        key = signal.source.identity_key
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
        })
        signal_documents[signal.id] = key
        first_reference.setdefault(key, references[-1])
        doc = documents.setdefault(key, {"quotes": [], "signal": signal, "conflict": False})
        if signal.source != doc["signal"].source:
            # Two bindings claim one version key: neither may stand for it.
            doc["conflict"] = True
    for citation in citations:
        key = signal_documents.get(citation.signal_id)
        if key is not None and any(
            ref["id"] == citation.signal_id and ref["source_ref"] == citation.source_ref
            for ref in references
        ):
            documents[key]["quotes"].append(citation.source_excerpt)
    rendered = []
    for key, doc in documents.items():
        signal = doc["signal"]
        binding = signal.source
        quotes = list(dict.fromkeys(doc["quotes"]))
        if doc["conflict"]:
            rendered.append({
                "document_id": key, "source_available": False,
                "source_unavailable_reason": "binding_conflict",
                "body_utf8_bytes": binding.body_utf8_bytes,
                "saved_quotations": quotes,
                "full_length": None, "truncated": None,
                "visible_ranges": [], "decoded_excerpts": [],
                "citation_budget_exceeded": None,
            })
            continue
        if source_has_external_locator(binding):
            rendered.append({
                "document_id": key, "source_in_input": False,
                "body_utf8_bytes": binding.body_utf8_bytes,
                "saved_quotations": quotes,
            })
            continue
        reading = prepared_sources.reading_for(signal)
        if not isinstance(reading, AvailableSource):
            rendered.append({
                "document_id": key, "source_available": False,
                "source_unavailable_reason": reading.reason.value,
                "body_utf8_bytes": binding.body_utf8_bytes,
                "saved_quotations": quotes,
                "full_length": None, "truncated": None,
                "visible_ranges": [], "decoded_excerpts": [],
                "citation_budget_exceeded": None,
            })
            continue
        rendered.append({
            "document_id": key, "source_available": True,
            "source_unavailable_reason": None,
            **_document_projection(reading.body, quotes, full=False),
        })
    return _compact_by_document(rendered, references, first_reference)


def _compact_by_document(
    rendered: list[dict], references: list[dict], first_reference: dict[str, dict]
) -> dict:
    """Write each source's shared fields once and let Signals point to it.

    `doc` is the entry's number within this input. Only what the Agent reads is
    shortened: the Signal id, quotation and bound version are unchanged.
    """
    numbers: dict[str, int] = {}
    documents = []
    for number, entry in enumerate(rendered, 1):
        key = entry["document_id"]
        numbers[key] = number
        first = first_reference[key]
        documents.append({
            "doc": number, "document_id": key,
            **{name: first[name] for name in _DOCUMENT_SOURCE_FIELDS},
            **{name: value for name, value in entry.items() if name != "document_id"},
        })
    signals = []
    for reference in references:
        key = reference["document_id"]
        first = first_reference[key]
        signals.append({
            "id": reference["id"], "doc": numbers[key],
            **{
                name: reference[name] for name in _DOCUMENT_SOURCE_FIELDS
                if reference[name] != first[name]
            },
            "context_json": reference["context_json"],
            "created_at": reference["created_at"],
        })
    return {"source_documents": documents, "source_signals": signals}
