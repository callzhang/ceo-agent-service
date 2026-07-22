from datetime import datetime, timezone

import pytest

from app.quality.incidents import reconcile_snapshot_incidents
from app.quality.models import ComponentHealth, QualitySnapshot
from app.store import AutoReplyStore


def _snapshot(*, failed: int = 0, unknown: int = 0) -> QualitySnapshot:
    return QualitySnapshot(
        commit="abc123",
        pid=42,
        schema_version=1,
        generated_at=datetime.now(timezone.utc).isoformat(),
        components=[],
        backlog={
            "failed": failed,
            "processing": 0,
            "unknown": unknown,
            "failed_actions": failed,
            "unknown_actions": unknown,
        },
        oldest_queue_age_seconds=0,
        failed_actions=failed,
        unknown_actions=unknown,
        slo_status="fail" if failed or unknown else "pass",
        ready=not (failed or unknown),
    )


def test_incidents_are_deduplicated_and_resolved_on_state_change(tmp_path) -> None:
    store = AutoReplyStore(tmp_path / "service.sqlite3")

    reconcile_snapshot_incidents(store, _snapshot(unknown=1))
    reconcile_snapshot_incidents(store, _snapshot(unknown=1))

    incidents = store.list_quality_incidents()
    assert len(incidents) == 1
    assert incidents[0]["status"] == "open"

    reconcile_snapshot_incidents(store, _snapshot())

    assert store.list_quality_incidents()[0]["status"] == "resolved"


def test_incident_can_be_acknowledged_with_owner_and_due_date(tmp_path) -> None:
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    reconcile_snapshot_incidents(store, _snapshot(failed=1))

    assert store.acknowledge_quality_incident(
        "failed_backlog", owner="quality-owner", due_at="2026-08-01T00:00:00+00:00"
    )
    incident = store.list_quality_incidents()[0]
    assert incident["status"] == "acknowledged"
    assert incident["owner"] == "quality-owner"


def test_resolved_incident_can_reopen_and_health_identifiers_are_validated(tmp_path) -> None:
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    assert store.open_quality_incident(
        "repeat", severity="medium", summary_code="first"
    )
    assert store.resolve_quality_incident("repeat")
    assert store.open_quality_incident(
        "repeat", severity="high", summary_code="second"
    )
    incident = store.list_quality_incidents()[0]
    assert incident["status"] == "open"
    assert incident["severity"] == "high"

    with pytest.raises(ValueError, match="stable identifier"):
        store.record_component_health("person@example.invalid", success=True)
    with pytest.raises(ValueError, match="redacted error code"):
        store.record_component_health("worker", success=False, error_kind="bad value")


def test_feedback_thresholds_and_degraded_components_open_incidents() -> None:
    class Store:
        opened = []

        def quality_feedback_rates(self, *, days):
            assert days == 30
            return {
                "sample_count": 20,
                "negative_rate": 0.06,
                "correction_rate": 0.11,
            }

        def list_quality_incidents(self):
            return []

        def open_quality_incident(self, key, *, severity, summary_code):
            self.opened.append((key, severity, summary_code))
            return True

    snapshot = _snapshot().model_copy(
        update={
            "components": (
                ComponentHealth(
                    component="memory",
                    status="failed",
                    consecutive_failures=3,
                ),
            )
        }
    )
    store = Store()

    changed = reconcile_snapshot_incidents(store, snapshot)

    assert set(changed) == {
        "component_memory",
        "negative_feedback_rate",
        "manual_correction_rate",
    }
