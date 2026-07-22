from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.quality.snapshot import build_quality_snapshot
from app.store import AutoReplyStore


def test_snapshot_is_ready_with_fresh_successful_components(tmp_path) -> None:
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    now = datetime.now(timezone.utc)
    for component in ("producer", "consumer", "codex", "dws", "memory"):
        store.record_component_health(component, success=True, observed_at=now)

    snapshot = build_quality_snapshot(
        store,
        commit="abc123",
        pid=123,
        now=now,
        required_components=("producer", "consumer", "codex", "dws", "memory"),
    )

    assert snapshot.ready is True
    assert snapshot.slo_status == "pass"


def test_snapshot_is_not_ready_after_three_missed_polls(tmp_path) -> None:
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    now = datetime.now(timezone.utc)
    store.record_component_health(
        "consumer", success=True, observed_at=now - timedelta(seconds=181)
    )

    snapshot = build_quality_snapshot(
        store,
        commit="abc123",
        pid=123,
        now=now,
        required_components=("consumer",),
        poll_seconds=60,
    )

    assert snapshot.ready is False
    assert snapshot.components[0].status == "stale"


def test_snapshot_fails_closed_when_queue_exceeds_slo(tmp_path, monkeypatch) -> None:
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    now = datetime.now(timezone.utc)
    store.record_component_health("consumer", success=True, observed_at=now)
    monkeypatch.setattr(store, "oldest_quality_queue_age_seconds", lambda: 1801)

    snapshot = build_quality_snapshot(
        store,
        commit="abc123",
        pid=123,
        now=now,
        required_components=("consumer",),
    )

    assert snapshot.ready is False
    assert snapshot.slo_status == "fail"


def test_invalid_slo_environment_uses_safe_defaults(
    tmp_path, monkeypatch
) -> None:
    store = AutoReplyStore(tmp_path / "service.sqlite3")
    now = datetime.now(timezone.utc)
    store.record_component_health("consumer", success=True, observed_at=now)
    monkeypatch.setattr(store, "oldest_quality_queue_age_seconds", lambda: 1)
    monkeypatch.setenv("CEO_QUALITY_TASK_SLO_SECONDS", "invalid")
    monkeypatch.setenv("CEO_QUALITY_PROCESSING_TIMEOUT_SECONDS", "0")

    snapshot = build_quality_snapshot(
        store,
        commit="abc123",
        pid=123,
        now=now,
        required_components=("consumer",),
    )

    assert snapshot.ready is True
