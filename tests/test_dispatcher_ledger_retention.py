from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.dispatcher.adapters import _complete_ledger
from app.dispatcher.models import DispatchEnvelope
from app.store import AutoReplyStore


NOW = datetime(2026, 10, 12, tzinfo=UTC)


def _ledger(store: AutoReplyStore, count: int) -> DispatchEnvelope:
    with store._connect() as db:
        db.executemany(
            "insert into dispatcher_claim_leases(adapter_name,source_id,generation,terminal_at) "
            "values('history',?,1,?)",
            [(str(index), f"2026-10-{1 + index % 10:02d} 00:00:00") for index in range(count)],
        )
        db.executemany(
            "insert into dispatcher_claim_leases(adapter_name,source_id,owner,generation) "
            "values('scheduled',?,'worker',1)",
            [("current",), ("active",)],
        )
    return DispatchEnvelope("scheduled", "current", NOW, 0, 1, 1)


@pytest.mark.parametrize("count", [0, 3, 10000, 10005])
def test_completion_retains_exact_newest_terminal_leases_and_active_claims(
    tmp_path: Path, count: int,
) -> None:
    store = AutoReplyStore(tmp_path / "leases.sqlite3")
    envelope = _ledger(store, count)
    with store._connect() as db:
        expected = [(row[0], row[1]) for row in db.execute(
            "select terminal_at,rowid from dispatcher_claim_leases where terminal_at<>''"
        )]
        current_rowid = db.execute(
            "select rowid from dispatcher_claim_leases where source_id='current'"
        ).fetchone()[0]
    expected.append(("2026-10-12 00:00:00", current_rowid))
    expected_ids = {rowid for _, rowid in sorted(expected, reverse=True)[:10000]}

    _complete_ledger(store, envelope=envelope, owner="worker", now=NOW)

    with store._connect() as db:
        actual_ids = {row[0] for row in db.execute(
            "select rowid from dispatcher_claim_leases where terminal_at<>''"
        )}
        active = db.execute(
            "select owner,generation,terminal_at from dispatcher_claim_leases where source_id='active'"
        ).fetchone()
    assert actual_ids == expected_ids
    assert tuple(active) == ("worker", 1, "")


def test_completion_prune_uses_bounded_index_work_inside_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "lease-cost.sqlite3")
    envelope = _ledger(store, 10000)
    original_open = store._open_connection
    steps = 0

    def metered_connection():
        db = original_open()

        def progress():
            nonlocal steps
            steps += 1000
            return int(steps > 100000)

        db.set_progress_handler(progress, 1000)
        return db

    monkeypatch.setattr(store, "_open_connection", metered_connection)
    _complete_ledger(store, envelope=envelope, owner="worker", now=NOW)
    assert steps <= 100000


def test_existing_schema_adds_terminal_index_without_changing_lease_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.store as store_module

    store = AutoReplyStore(tmp_path / "existing-leases.sqlite3")
    _ledger(store, 3)
    with store._connect() as db:
        before = [tuple(row) for row in db.execute(
            "select * from dispatcher_claim_leases order by rowid"
        )]
        db.execute("drop index idx_dispatcher_claim_leases_terminal")
    monkeypatch.setattr(store_module, "_INITIALIZED_STORE_PATHS", set())
    reopened = AutoReplyStore(store.path)
    with reopened._connect() as db:
        indexes = {row[1] for row in db.execute("pragma index_list(dispatcher_claim_leases)")}
        after = [tuple(row) for row in db.execute(
            "select * from dispatcher_claim_leases order by rowid"
        )]
    assert "idx_dispatcher_claim_leases_terminal" in indexes
    assert after == before
