from __future__ import annotations

from app.quality.models import QualitySnapshot
from app.store import AutoReplyStore


def reconcile_snapshot_incidents(
    store: AutoReplyStore, snapshot: QualitySnapshot
) -> list[str]:
    active: dict[str, tuple[str, str]] = {}
    if snapshot.backlog.get("failed", 0):
        active["failed_backlog"] = ("high", "failed_backlog_nonzero")
    if snapshot.backlog.get("unknown", 0):
        active["unknown_side_effect"] = ("critical", "unknown_side_effect_nonzero")
    for component in snapshot.components:
        if component.status != "ready":
            active[f"component_{component.component}"] = (
                "high",
                f"component_{component.status}",
            )
    feedback = store.quality_feedback_rates(days=30)
    if feedback["sample_count"] >= 20 and feedback["negative_rate"] > 0.05:
        active["negative_feedback_rate"] = ("medium", "negative_feedback_rate_high")
    if feedback["sample_count"] >= 20 and feedback["correction_rate"] > 0.10:
        active["manual_correction_rate"] = ("medium", "manual_correction_rate_high")

    changed: list[str] = []
    existing = {
        str(incident["incident_key"]): str(incident["status"])
        for incident in store.list_quality_incidents()
    }
    for key, (severity, summary_code) in active.items():
        if store.open_quality_incident(
            key, severity=severity, summary_code=summary_code
        ):
            changed.append(key)
    for key, status in existing.items():
        if status in {"open", "acknowledged"} and key not in active:
            if store.resolve_quality_incident(key):
                changed.append(key)
    return changed
