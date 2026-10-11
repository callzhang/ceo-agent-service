import sqlite3
from contextlib import contextmanager

import pytest

from app.workbench.store import WorkbenchStore


@pytest.mark.parametrize("running", [False, True])
def test_idle_claim_does_not_compete_with_an_unrelated_writer(tmp_path, running):
    store = WorkbenchStore(tmp_path / "worker.sqlite3", busy_timeout_seconds=0)
    if running:
        task = store.create_task(title="Report", runtime_kind="codex")
        store.create_turn(task.id, user_text="Prepare", client_request_id="one")
        assert store.claim_next_turn(owner="active", now="2026-10-07T10:00:00Z")
    blocker = sqlite3.connect(store.path)
    try:
        blocker.execute("begin immediate")
        assert store.claim_next_turn(owner="idle", now="2026-10-07T10:00:01Z") is None
    finally:
        blocker.rollback()
        blocker.close()


def test_idle_preflight_does_not_lose_a_concurrently_queued_turn(tmp_path, monkeypatch):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Report", runtime_kind="codex")
    connect = store._connect
    injected = False

    @contextmanager
    def queue_after_read():
        nonlocal injected
        with connect() as db:
            yield db
        if not injected:
            injected = True
            store.create_turn(task.id, user_text="Prepare", client_request_id="one")

    monkeypatch.setattr(store, "_connect", queue_after_read)
    assert store.claim_next_turn(owner="idle") is None
    monkeypatch.setattr(store, "_connect", connect)
    claimed = store.claim_next_turn(owner="worker")
    assert claimed is not None and claimed.task_id == task.id


def test_preflight_keeps_expired_running_turn_recovery(tmp_path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Report", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Prepare", client_request_id="one")
    assert store.claim_next_turn(
        owner="dead", lease_seconds=1, now="2026-10-07T10:00:00Z"
    )
    claimed = store.claim_next_turn(owner="replacement", now="2026-10-07T10:00:02Z")
    assert claimed is not None and claimed.id == turn.id
    with store._connect() as db:
        assert (
            db.execute(
                "select lease_owner from workbench_turns where id=?", (turn.id,)
            ).fetchone()[0]
            == "replacement"
        )


@pytest.mark.parametrize("expired", [False, True])
def test_positive_preflight_rechecks_another_workers_claim(
    tmp_path, monkeypatch, expired
):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    other = WorkbenchStore(store.path)
    task = store.create_task(title="Report", runtime_kind="codex")
    turn = store.create_turn(task.id, user_text="Prepare", client_request_id="one")
    if expired:
        assert store.claim_next_turn(
            owner="dead", lease_seconds=1, now="2026-10-07T10:00:00Z"
        )
    connect = store._connect
    injected = False

    @contextmanager
    def claim_after_read():
        nonlocal injected
        with connect() as db:
            yield db
        if not injected:
            injected = True
            claimed = other.claim_next_turn(owner="winner", now="2026-10-07T10:00:02Z")
            assert claimed is not None and claimed.id == turn.id

    monkeypatch.setattr(store, "_connect", claim_after_read)
    assert store.claim_next_turn(owner="loser", now="2026-10-07T10:00:02Z") is None
    with other._connect() as db:
        assert (
            db.execute(
                "select lease_owner from workbench_turns where id=?", (turn.id,)
            ).fetchone()[0]
            == "winner"
        )
