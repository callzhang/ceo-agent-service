from __future__ import annotations


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
        references.append(reference)
    return references
