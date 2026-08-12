import sqlite3
from pathlib import Path

import pytest

from app.workbench.models import TurnStatus
from app.workbench.store import WorkbenchStore


def _store(tmp_path: Path) -> WorkbenchStore:
    return WorkbenchStore(tmp_path / "workbench.sqlite3")


def test_create_task_and_idempotent_turn_request(tmp_path: Path):
    store = _store(tmp_path)

    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    first = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    second = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )

    assert first == second
    assert first.status is TurnStatus.QUEUED
    assert store.get_task(task.id) == task


def test_create_turn_rejects_second_active_request_for_task(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )

    with pytest.raises(ValueError, match="task already has an active turn"):
        store.create_turn(
            task.id,
            user_text="Compare products",
            client_request_id="request-2",
        )


def test_events_replay_in_id_order_and_reject_duplicate_sequence(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    first = store.append_event(
        turn.id,
        sequence=1,
        event_type="text_delta",
        payload={"text": "North"},
    )
    second = store.append_event(
        turn.id,
        sequence=2,
        event_type="text_delta",
        payload={"text": "South"},
    )

    with pytest.raises(ValueError, match="event sequence already exists"):
        store.append_event(
            turn.id,
            sequence=2,
            event_type="text_delta",
            payload={"text": "duplicate"},
        )

    assert store.events_after(turn.id, after_id=first.id) == [second]


def test_confirmation_cannot_be_decided_through_another_task(tmp_path: Path):
    store = _store(tmp_path)
    first_task = store.create_task(title="Analyse sales", runtime_kind="codex")
    second_task = store.create_task(title="Plan marketing", runtime_kind="codex")
    turn = store.create_turn(
        first_task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    claimed = store.claim_next_turn(owner="worker-1")
    assert claimed is not None
    confirmation = store.create_confirmation(
        turn.id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
    )

    with pytest.raises(ValueError, match="confirmation does not belong to task"):
        store.decide_confirmation(
            second_task.id,
            confirmation.id,
            decision="confirmed",
        )


def test_recover_expired_running_turn_as_queued(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    claimed = store.claim_next_turn(
        owner="worker-1", lease_seconds=1, now="2026-08-13T00:00:00Z"
    )
    assert claimed is not None

    assert store.recover_expired_turns(now="2026-08-13T00:00:02Z") == 1
    assert store.get_turn(turn.id).status is TurnStatus.QUEUED


def test_recovery_leaves_waiting_confirmation_turn_unchanged(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    store.claim_next_turn(
        owner="worker-1", lease_seconds=1, now="2026-08-13T00:00:00Z"
    )
    store.create_confirmation(
        turn.id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:00Z",
    )

    assert store.recover_expired_turns(now="2026-08-13T00:00:02Z") == 0
    assert store.get_turn(turn.id).status is TurnStatus.WAITING_CONFIRMATION


def test_transition_to_waiting_confirmation_releases_worker_lease(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    store.claim_next_turn(owner="worker-1", now="2026-08-13T00:00:00Z")

    waiting = store.complete_turn(
        turn.id,
        status=TurnStatus.WAITING_CONFIRMATION,
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )

    assert waiting.status is TurnStatus.WAITING_CONFIRMATION
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "select lease_owner from workbench_turns where id=?", (turn.id,)
        ).fetchone()[0] == ""


def test_running_transition_requires_claiming_a_lease(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )

    with pytest.raises(ValueError, match="running turns must be claimed"):
        store.complete_turn(turn.id, status=TurnStatus.RUNNING)


def test_attachment_filename_cannot_escape_generated_task_directory(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    attachment = store.save_attachment(
        task.id,
        filename="../../secret.txt",
        media_type="text/plain",
        content=b"private",
    )

    with sqlite3.connect(store.path) as db:
        storage_path = Path(
            db.execute(
                "select storage_path from workbench_attachments where id=?",
                (attachment.id,),
            ).fetchone()[0]
        )
    assert storage_path.parent == (
        tmp_path / "workbench" / "attachments" / task.id
    )
    assert storage_path.read_bytes() == b"private"
