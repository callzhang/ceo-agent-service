"""Status Attempt reads should stay bounded as completed history grows."""

from app import audit_web
from app.action_storage import insert_action, reply_action_projection, _reply_variant_for
from app.store import AutoReplyStore


def _seed_attempt_history(store: AutoReplyStore, *, failed: int, sent: int) -> None:
    first_id = store.record_reply_attempt(
        conversation_id="failure-0",
        conversation_title="Synthetic failure",
        trigger_message_id="trigger-0",
        trigger_sender="System",
        trigger_text="Synthetic trigger",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    with store._connect() as db:
        payload = dict(db.execute(
            "select * from (" + reply_action_projection("reply_attempts", _reply_variant_for(db)) + ") where id=?",
            (first_id,),
        ).fetchone())
        db.execute("begin immediate")
        for index in range(1, failed + sent):
            is_failed = index < failed
            insert_action(db, "reply_attempts", {
                **payload,
                "id": first_id + index,
                "conversation_id": f"failure-{index}" if is_failed else f"sent-{index}",
                "trigger_message_id": f"trigger-{index}",
                "send_status": "failed" if is_failed else "sent",
            })


def test_unresolved_attempt_sql_work_is_bounded_by_history_size(tmp_path):
    store = AutoReplyStore(tmp_path / "status.sqlite3")
    _seed_attempt_history(store, failed=80, sent=4000)

    with store.read_snapshot():
        db = store._read_snapshot_connection.get()
        assert db is not None
        progress_calls = 0

        def progress():
            nonlocal progress_calls
            progress_calls += 1
            return 0

        db.set_progress_handler(progress, 1000)
        rows = store.list_current_unresolved_problem_attempt_summaries(limit=None)

    assert len(rows) == 80
    assert {row["send_status"] for row in rows} == {"failed"}
    assert progress_calls * 1000 < 800_000


def test_unresolved_attempt_keeps_latest_id_across_channels(tmp_path):
    store = AutoReplyStore(tmp_path / "status.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="same-conversation",
        conversation_title="Synthetic failure",
        trigger_message_id="same-trigger",
        trigger_sender="System",
        trigger_text="Synthetic trigger",
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    with store._connect() as db:
        payload = dict(db.execute(
            "select * from (" + reply_action_projection("reply_attempts", _reply_variant_for(db)) + ") where id=?",
            (first_id,),
        ).fetchone())
        db.execute("begin immediate")
        insert_action(db, "reply_attempts", {
            **payload, "id": first_id + 1, "channel": "wechat", "send_status": "sent",
        })
        insert_action(db, "reply_attempts", {
            **payload, "id": first_id + 2, "conversation_id": "visible",
            "trigger_message_id": "visible-trigger",
        })

    rows = store.list_current_unresolved_problem_attempt_summaries(limit=None)
    assert [int(row["id"]) for row in rows] == [first_id + 2]


def test_reply_attempt_status_does_not_repeat_projection_for_each_aggregate(
    tmp_path, monkeypatch,
):
    store = AutoReplyStore(tmp_path / "status.sqlite3")
    _seed_attempt_history(store, failed=80, sent=4000)
    original = audit_web.REPLY_ATTEMPT_COMMON_SQL
    instrumented = original.replace(
        "a.attempt_send_status as send_status",
        "counted_status(a.attempt_send_status) as send_status",
    )
    assert instrumented != original
    monkeypatch.setattr(audit_web, "REPLY_ATTEMPT_COMMON_SQL", instrumented)

    with store.read_snapshot():
        db = store._read_snapshot_connection.get()
        assert db is not None
        status_reads = 0

        def counted_status(status):
            nonlocal status_reads
            status_reads += 1
            return status

        db.create_function("counted_status", 1, counted_status)
        snapshot = audit_web._reply_attempt_queue_snapshot(db)

    assert snapshot["counts"] == {"failed": 80, "sent": 4000}
    assert status_reads < 8 * 4080
