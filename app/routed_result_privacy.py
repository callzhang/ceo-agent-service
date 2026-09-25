from __future__ import annotations

from typing import Any


_SOURCE_REF_KEYS = frozenset({"source_ref", "sourceRef"})


def _source_refs(value: Any, found: list[str], depth: int = 0) -> None:
    """Keep only explicit source references, never raw tool material."""
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _SOURCE_REF_KEYS and isinstance(item, str) and item.strip():
                found.append(item.strip()[:500])
            else:
                _source_refs(item, found, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _source_refs(item, found, depth + 1)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        import json
        try:
            _source_refs(json.loads(value), found, depth + 1)
        except (TypeError, ValueError):
            return


def audit_references_from_full_events(
    events: object,
    *,
    limit: int,
) -> list[dict[str, str]]:
    """Validate transient audit events and retain only recovery-safe references."""
    if not isinstance(events, list):
        raise TypeError("audit events must be a list")
    if limit <= 0:
        raise ValueError("audit event limit must be positive")

    references: list[dict[str, str]] = []
    for event in events[:limit]:
        if not isinstance(event, dict):
            raise TypeError("audit event must be an object")
        tool = event.get("tool", "")
        call_id = event.get("call_id", "")
        if not isinstance(tool, str) or not isinstance(call_id, str):
            raise TypeError("audit event references must be strings")
        tool = tool.strip()
        call_id = call_id.strip()
        if not tool:
            continue
        reference = {"tool": tool[:160]}
        if call_id:
            reference["call_id"] = call_id[:500]
        for key in ("effect", "outcome"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                reference[key] = value.strip()[:160]
        refs: list[str] = []
        _source_refs(event, refs)
        if refs:
            reference["source_refs"] = "\n".join(dict.fromkeys(refs))
        references.append(reference)
    return references
