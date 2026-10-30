from contextlib import contextmanager

import pytest

from app.store import AutoReplyStore


@pytest.mark.parametrize("existing", [False, True])
def test_meeting_partial_result_uses_one_write_and_preserves_other_facts(tmp_path, monkeypatch, existing):
    store = AutoReplyStore(tmp_path / "meeting.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-result", title="Meeting", source_json="{}",
        participants_json="[]", ended_at="", eligible_at="", status="pending",
    )
    with store._connect() as db:
        if existing:
            db.execute(
                "update task_results set final_message='approved body',send_result_json=? "
                "where task_id=? and result_kind='meeting_alignment'",
                ('{"receipt":"verified"}', job_id),
            )
        else:
            db.execute("delete from task_results where task_id=?", (job_id,))

    statements = []
    original_connect = store._connect

    @contextmanager
    def traced_connect():
        with original_connect() as db:
            db.set_trace_callback(statements.append)
            try:
                yield db
            finally:
                db.set_trace_callback(None)

    monkeypatch.setattr(store, "_connect", traced_connect)
    for summary in ("pending", "completed"):
        statements.clear()
        store.update_meeting_alignment_job(job_id, calendar_summary_status=summary)
        writes = [sql for sql in statements if sql.lower().startswith("insert into task_results")]
        assert len(writes) == 1
        assert "on conflict(task_id,result_kind) do update" in writes[0].lower()
        with store._connect() as db:
            row = db.execute(
                "select final_message,send_result_json,calendar_summary_status,decision_json,mentions_json "
                "from task_results where task_id=? and result_kind='meeting_alignment'", (job_id,),
            ).fetchone()
        assert tuple(row) == (
            "approved body" if existing else "",
            '{"receipt":"verified"}' if existing else "{}", summary, "{}", "[]",
        )
    assert store.get_meeting_alignment_job(job_id).status == "pending"
