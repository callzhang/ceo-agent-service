"""History chart reads should scale with events and distinct message keys."""

from app.action_storage import insert_action
from app.audit_web import _history_chart_payload
from app.store import AutoReplyStore


def _insert_failed_attempts(store: AutoReplyStore, count: int, *, message: str) -> list[int]:
    with store._connect() as db:
        return [
            insert_action(
                db,
                "reply_attempts",
                {
                    "channel": "email",
                    "conversation_id": "chart-retry-thread",
                    "conversation_title": "Chart retry thread",
                    "trigger_message_id": message,
                    "trigger_sender": "sender",
                    "trigger_text": "retry",
                    "action": "direct_unsubscribe",
                    "sensitivity_kind": "general",
                    "send_status": "failed",
                },
            ).lastrowid
            for _ in range(count)
        ]


def _status_vm_steps(store: AutoReplyStore, attempt_ids: list[int], monkeypatch) -> int:
    steps = 0
    open_connection = store._open_connection

    def counted_connection():
        nonlocal steps
        db = open_connection()

        def count_steps():
            nonlocal steps
            steps += 1000
            return 0

        db.set_progress_handler(count_steps, 1000)
        return db

    with monkeypatch.context() as patch:
        patch.setattr(store, "_open_connection", counted_connection)
        statuses = store.list_reply_attempt_operation_statuses(attempt_ids)
    assert set(statuses) == set(attempt_ids)
    return steps


def test_reply_history_status_projection_scales_with_repeated_failed_attempts(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "chart-status-cost.sqlite3")
    initial = _insert_failed_attempts(store, 80, message="same-action")
    small_steps = _status_vm_steps(store, initial, monkeypatch)

    more = _insert_failed_attempts(store, 320, message="same-action")
    all_ids = initial + more
    large_steps = _status_vm_steps(store, all_ids, monkeypatch)

    assert large_steps < small_steps * 8
    assert large_steps < 500_000


def test_chart_reads_one_task_per_failed_message_in_one_snapshot(
    tmp_path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "chart-failed-keys.sqlite3")
    _insert_failed_attempts(store, 20, message="same-action")
    calls = 0
    original_lookup = store.get_reply_task_for_message

    def counted_lookup(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_lookup(*args, **kwargs)

    monkeypatch.setattr(store, "get_reply_task_for_message", counted_lookup)
    payload = _history_chart_payload(store)

    assert payload["total"] == 20
    assert {series["name"]: sum(series["data"]) for series in payload["series"]} == {
        "Failed": 20
    }
    assert calls == 1
