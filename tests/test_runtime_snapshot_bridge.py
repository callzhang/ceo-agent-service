from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.agent_runtime_contracts import PROBE_VERIFIED_RUNTIME_CAPABILITIES, RuntimeCapabilitySnapshot
from app.store import AutoReplyStore


def test_latest_runtime_capability_snapshot_can_be_read_across_service_processes(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "runtime.sqlite3")
    snapshot = RuntimeCapabilitySnapshot(
        route_name="codex_oauth",
        capabilities=PROBE_VERIFIED_RUNTIME_CAPABILITIES,
        healthy=True,
        checked_at=datetime.now(UTC).isoformat(),
        expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    store.record_runtime_capability_snapshot(snapshot, pid=701)

    assert store.runtime_capability_snapshots_latest(("codex_oauth",)) == {
        "codex_oauth": snapshot
    }
