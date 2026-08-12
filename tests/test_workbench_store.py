import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.workbench.models import TurnStatus
from app.workbench.store import WorkbenchStore


def _store(tmp_path: Path) -> WorkbenchStore:
    return WorkbenchStore(tmp_path / "workbench.sqlite3")


def _running_turn(tmp_path: Path) -> tuple[WorkbenchStore, str, str]:
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    claimed = store.claim_next_turn(
        owner="worker-1", lease_seconds=10, now="2026-08-13T00:00:00Z"
    )
    assert claimed is not None
    return store, task.id, turn.id


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
    store, _, turn_id = _running_turn(tmp_path)
    with pytest.raises(ValueError, match="event sequence must be next"):
        store.append_event(
            turn_id,
            sequence=2,
            event_type="text_delta",
            payload={"text": "South"},
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )
    first = store.append_event(
        turn_id,
        sequence=1,
        event_type="text_delta",
        payload={"text": "North"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )
    with pytest.raises(ValueError, match="event sequence must be next"):
        store.append_event(
            turn_id,
            sequence=3,
            event_type="text_delta",
            payload={"text": "gap"},
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )
    with pytest.raises(ValueError, match="event sequence must be next"):
        store.append_event(
            turn_id,
            sequence=1,
            event_type="text_delta",
            payload={"text": "duplicate"},
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )
    second = store.append_event(
        turn_id,
        sequence=2,
        event_type="text_delta",
        payload={"text": "South"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )

    assert store.events_after(turn_id, after_id=first.id) == [second]


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


def test_running_turn_executor_mutations_require_an_owner(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)

    with pytest.raises(ValueError, match="owner must be non-empty"):
        store.append_event(
            turn_id,
            sequence=1,
            event_type="text_delta",
            payload={"text": "North"},
        )

    store, _, turn_id = _running_turn(tmp_path / "confirmation")
    with pytest.raises(ValueError, match="owner must be non-empty"):
        store.create_confirmation(
            turn_id,
            action_kind="send_message",
            target="sales@example.com",
            summary="Send the regional comparison",
            risk="external communication",
            arguments_json={"channel": "email"},
        )

    store, _, turn_id = _running_turn(tmp_path / "complete")
    with pytest.raises(ValueError, match="owner must be non-empty"):
        store.complete_turn(turn_id, status=TurnStatus.COMPLETED)


def test_running_turn_executor_mutations_reject_mismatched_and_expired_owners(
    tmp_path: Path,
):
    store, _, turn_id = _running_turn(tmp_path)

    with pytest.raises(ValueError, match="turn lease is stale"):
        store.append_event(
            turn_id,
            sequence=1,
            event_type="text_delta",
            payload={"text": "North"},
            owner="worker-2",
            now="2026-08-13T00:00:01Z",
        )
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.create_confirmation(
            turn_id,
            action_kind="send_message",
            target="sales@example.com",
            summary="Send the regional comparison",
            risk="external communication",
            arguments_json={"channel": "email"},
            owner="worker-1",
            now="2026-08-13T00:00:11Z",
        )
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.complete_turn(
            turn_id,
            status=TurnStatus.COMPLETED,
            owner="worker-1",
            now="2026-08-13T00:00:11Z",
        )


def test_confirmation_list_redacts_arguments_and_executor_lookup_exposes_them(
    tmp_path: Path,
):
    store, task_id, turn_id = _running_turn(tmp_path)
    confirmation = store.create_confirmation(
        turn_id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )

    assert store.list_confirmations(task_id)[0].arguments_json == ""
    with pytest.raises(TypeError):
        store.list_confirmations(task_id, include_arguments=True)
    with pytest.raises(ValueError, match="confirmation is not confirmed"):
        store.get_confirmation_for_executor(
            task_id,
            confirmation.id,
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )
    assert store.decide_confirmation(
        task_id, confirmation.id, decision="confirmed"
    ).arguments_json == ""
    store.claim_next_turn(owner="worker-1", now="2026-08-13T00:00:02Z")
    assert store.get_confirmation_for_executor(
        task_id,
        confirmation.id,
        owner="worker-1",
        now="2026-08-13T00:00:02Z",
    ).arguments_json == '{"channel":"email"}'


def test_recovered_stale_worker_cannot_append_events(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)
    assert store.recover_expired_turns(now="2026-08-13T00:00:11Z") == 1

    with pytest.raises(ValueError, match="turn lease requires running status"):
        store.append_event(
            turn_id,
            sequence=1,
            event_type="text_delta",
            payload={"text": "stale"},
            owner="worker-1",
            now="2026-08-13T00:00:11Z",
        )


def test_provider_session_requires_running_turn_lease(tmp_path: Path):
    store, task_id, turn_id = _running_turn(tmp_path)

    with pytest.raises(TypeError):
        store.set_provider_session(turn_id, "session-1", now="2026-08-13T00:00:01Z")
    with pytest.raises(ValueError, match="owner must be non-empty"):
        store.set_provider_session(
            turn_id,
            "session-1",
            owner="",
            now="2026-08-13T00:00:01Z",
        )
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.set_provider_session(
            turn_id,
            "session-1",
            owner="worker-2",
            now="2026-08-13T00:00:01Z",
        )

    assert store.set_provider_session(
        turn_id,
        "session-1",
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    ).id == task_id
    assert store.get_task(task_id).provider_session_ref == "session-1"
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.set_provider_session(
            turn_id,
            "session-2",
            owner="worker-1",
            now="2026-08-13T00:00:11Z",
        )


@pytest.mark.parametrize("payload", [{"value": float("nan")}, '{"value":NaN}'])
def test_event_payload_rejects_nonfinite_json(tmp_path: Path, payload):
    store, _, turn_id = _running_turn(tmp_path)

    with pytest.raises(ValueError, match="payload must be a JSON object"):
        store.append_event(
            turn_id,
            sequence=1,
            event_type="text_delta",
            payload=payload,
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )


def test_confirmation_arguments_reject_nonfinite_json(tmp_path: Path):
    store, _, turn_id = _running_turn(tmp_path)

    with pytest.raises(ValueError, match="arguments_json must be a JSON object"):
        store.create_confirmation(
            turn_id,
            action_kind="send_message",
            target="sales@example.com",
            summary="Send the regional comparison",
            risk="external communication",
            arguments_json='{"value":Infinity}',
            owner="worker-1",
            now="2026-08-13T00:00:01Z",
        )


def test_executor_confirmation_lookup_requires_confirmed_running_lease(tmp_path: Path):
    store, task_id, turn_id = _running_turn(tmp_path)
    confirmation = store.create_confirmation(
        turn_id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the regional comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:01Z",
    )
    store.decide_confirmation(task_id, confirmation.id, decision="confirmed")

    with pytest.raises(TypeError):
        store.get_confirmation_for_executor(
            task_id, confirmation.id, now="2026-08-13T00:00:02Z"
        )
    with pytest.raises(ValueError, match="owner must be non-empty"):
        store.get_confirmation_for_executor(
            task_id,
            confirmation.id,
            owner="",
            now="2026-08-13T00:00:02Z",
        )
    store.claim_next_turn(
        owner="worker-1", lease_seconds=1, now="2026-08-13T00:00:02Z"
    )
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.get_confirmation_for_executor(
            task_id,
            confirmation.id,
            owner="worker-2",
            now="2026-08-13T00:00:02Z",
        )
    with pytest.raises(ValueError, match="turn lease is stale"):
        store.get_confirmation_for_executor(
            task_id,
            confirmation.id,
            owner="worker-1",
            now="2026-08-13T00:00:03Z",
        )


def test_stop_and_confirmation_decision_are_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    stopped_turn = store.create_turn(
        task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    first_stop = store.request_stop(stopped_turn.id, now="2026-08-13T00:00:01Z")
    assert store.request_stop(
        stopped_turn.id, now="2026-08-13T00:00:02Z"
    ) == first_stop

    active_turn = store.create_turn(
        task.id,
        user_text="Compare products",
        client_request_id="request-2",
    )
    store.claim_next_turn(owner="worker-1", now="2026-08-13T00:00:03Z")
    confirmation = store.create_confirmation(
        active_turn.id,
        action_kind="send_message",
        target="sales@example.com",
        summary="Send the product comparison",
        risk="external communication",
        arguments_json={"channel": "email"},
        owner="worker-1",
        now="2026-08-13T00:00:03Z",
    )
    confirmed = store.decide_confirmation(
        task.id, confirmation.id, decision="confirmed", now="2026-08-13T00:00:04Z"
    )
    assert store.decide_confirmation(
        task.id, confirmation.id, decision="confirmed", now="2026-08-13T00:00:05Z"
    ) == confirmed
    with pytest.raises(ValueError, match="already been decided"):
        store.decide_confirmation(
            task.id, confirmation.id, decision="cancelled", now="2026-08-13T00:00:05Z"
        )

    store.claim_next_turn(owner="worker-1", now="2026-08-13T00:00:06Z")
    completed = store.complete_turn(
        active_turn.id,
        status=TurnStatus.COMPLETED,
        owner="worker-1",
        now="2026-08-13T00:00:06Z",
    )
    with pytest.raises(ValueError, match="invalid turn transition"):
        store.request_stop(completed.id, now="2026-08-13T00:00:07Z")


def test_attachment_db_failure_leaves_no_orphan_file(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="Analyse sales", runtime_kind="codex")
    with sqlite3.connect(store.path) as db:
        db.execute(
            """
            create trigger reject_workbench_attachment
            before insert on workbench_attachments
            begin
                select raise(abort, 'injected attachment failure');
            end
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected attachment failure"):
        store.save_attachment(
            task.id,
            filename="report.txt",
            media_type="text/plain",
            content=b"private",
        )

    directory = tmp_path / "workbench" / "attachments" / task.id
    assert list(directory.iterdir()) == []


def test_independent_store_instances_idempotently_create_one_turn(tmp_path: Path):
    first = _store(tmp_path)
    task = first.create_task(title="Analyse sales", runtime_kind="codex")
    second = _store(tmp_path)
    barrier = threading.Barrier(2)

    def create(store: WorkbenchStore):
        barrier.wait()
        return store.create_turn(
            task.id,
            user_text="Compare regions",
            client_request_id="request-1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        created = list(executor.map(create, (first, second)))

    assert created[0] == created[1]


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
