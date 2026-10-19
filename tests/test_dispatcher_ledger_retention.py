from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.dispatcher.adapters import _complete_ledger
from app.dispatcher.models import DispatchEnvelope
from app.store import AutoReplyStore
from app.state_definitions import state_projection
from app.state_storage import insert_state


NOW = datetime(2026, 10, 12, tzinfo=UTC)


def _ledger(store: AutoReplyStore, count: int) -> DispatchEnvelope:
    with store._connect() as db:
        db.execute("begin immediate")
        for index in range(count):
            insert_state(
                db, "dispatcher_claim_leases",
                {"adapter_name": "history", "source_id": str(index), "generation": 1,
                 "terminal_at": f"2026-10-{1 + index % 10:02d} 00:00:00"},
                source_rowid=index + 1,
            )
        insert_state(
            db, "dispatcher_claim_leases",
            {"adapter_name": "scheduled", "source_id": "current", "owner": "worker",
             "generation": 1},
            source_rowid=count + 1,
        )
        insert_state(
            db, "dispatcher_claim_leases",
            {"adapter_name": "scheduled", "source_id": "active", "owner": "worker",
             "generation": 1},
            source_rowid=count + 2,
        )
    return DispatchEnvelope("scheduled", "current", NOW, 0, 1, 1)


@pytest.mark.parametrize("count", [0, 3, 10000, 10005])
def test_completion_retains_exact_newest_terminal_leases_and_active_claims(
    tmp_path: Path, count: int,
) -> None:
    store = AutoReplyStore(tmp_path / "leases.sqlite3")
    envelope = _ledger(store, count)
    with store._connect() as db:
        projection = state_projection("dispatcher_claim_leases", include_rowid=True)
        expected = [(row[0], row[1]) for row in db.execute(
            f"select terminal_at,rowid from ({projection}) where terminal_at<>''"
        )]
        current_rowid = db.execute(
            f"select rowid from ({projection}) where source_id='current'"
        ).fetchone()[0]
    expected.append(("2026-10-12 00:00:00", current_rowid))
    expected_ids = {rowid for _, rowid in sorted(expected, reverse=True)[:10000]}

    _complete_ledger(store, envelope=envelope, owner="worker", now=NOW)

    with store._connect() as db:
        projection = state_projection("dispatcher_claim_leases", include_rowid=True)
        actual_ids = {row[0] for row in db.execute(
            f"select rowid from ({projection}) where terminal_at<>''"
        )}
        active = db.execute(
            f"select owner,generation,terminal_at from ({projection}) where source_id='active'"
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

    def metered_connection(*args, **kwargs):
        db = original_open(*args, **kwargs)

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
        projection = state_projection("dispatcher_claim_leases", include_rowid=True)
        before = [tuple(row) for row in db.execute(
            f"select * from ({projection}) order by rowid"
        )]
        db.execute("drop index idx_state_dispatcher_terminal")
    monkeypatch.setattr(store_module, "_INITIALIZED_STORE_PATHS", set())
    reopened = AutoReplyStore(store.path)
    with reopened._connect() as db:
        indexes = {row[1] for row in db.execute("pragma index_list(state)")}
        after = [tuple(row) for row in db.execute(
            f"select * from ({projection}) order by rowid"
        )]
    assert "idx_state_dispatcher_terminal" in indexes
    assert after == before
