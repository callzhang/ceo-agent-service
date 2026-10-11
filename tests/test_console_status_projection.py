from app.audit_web import _reply_attempt_queue_snapshot
from app.store import AutoReplyStore


def test_status_reads_errors_only_for_current_failed_attempts(tmp_path):
    store = AutoReplyStore(tmp_path / "status.sqlite3")
    ids = []
    for trigger, status in [
        ("retry", "failed"),
        ("retry", "skipped"),
        ("open", "failed"),
    ]:
        ids.append(
            store.record_reply_attempt(
                conversation_id="status",
                conversation_title="Status",
                trigger_message_id=trigger,
                trigger_sender="sender",
                trigger_text="test",
                action="agent_run",
                sensitivity_kind="general",
                send_status=status,
            )
        )
    error_reads = []
    with store._connect() as db:
        for attempt_id, error in zip(
            ids, ["old failure", "skipped detail", "current failure"]
        ):
            db.execute(
                "update reply_attempts set send_error=? where id=?", (error, attempt_id)
            )
        expected = _reply_attempt_queue_snapshot(db)
        columns = [
            row["name"] for row in db.execute("pragma table_info(reply_attempts)")
        ]
        projection = ",".join(
            "read_status_error(send_error) as send_error"
            if column == "send_error"
            else f'"{column}"'
            for column in columns
        )
        db.create_function(
            "read_status_error", 1, lambda value: error_reads.append(value) or value
        )
        db.execute(
            f"create temp view reply_attempts as select {projection} from main.reply_attempts"
        )
        actual = _reply_attempt_queue_snapshot(db)
    assert actual == expected
    assert actual["counts"] == {"failed": 1, "skipped": 1}
    assert actual["latest_error"] == "current failure"
    assert set(error_reads) == {"current failure"}
