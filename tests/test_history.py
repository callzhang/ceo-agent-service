import sqlite3

from app.store import AutoReplyStore


def _seed_meeting_run(store: AutoReplyStore, *, status: str = "sent") -> int:
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-1",
        title="项目评审会",
        source_json='{"summary":"讨论上线范围"}',
        participants_json='[{"name":"Derek"},{"name":"Mina"}]',
        ended_at="2026-07-14T09:50:00+08:00",
        eligible_at="2026-07-14T10:00:00+08:00",
        status="pending",
    )
    store.update_meeting_alignment_job(
        job_id,
        status=status,
        target_title="项目群",
        final_message="各方对上线范围仍有分歧。@Mina 请确认风险预算。",
    )
    return store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="meeting-session-1",
        decision_json='{"action":"send"}',
        audit_summary="会后对齐：上线范围仍未一致。",
        status=status,
        error="",
    )


def test_history_merges_reply_and_meeting_runs_by_time(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    reply_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="研发群",
        trigger_message_id="msg-1",
        trigger_sender="Mina",
        trigger_text="是否全量上线？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    store.update_reply_attempt(reply_id, final_reply_text="先灰度。")
    meeting_run_id = _seed_meeting_run(store)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update reply_attempts set created_at='2026-07-14 10:00:00' where id=?",
            (reply_id,),
        )
        db.execute(
            "update meeting_alignment_runs set created_at='2026-07-14 10:01:00' where id=?",
            (meeting_run_id,),
        )

    items = store.list_history_items(limit=20)

    assert [(item.kind, item.source_id) for item in items[:2]] == [
        ("meeting", meeting_run_id),
        ("reply", reply_id),
    ]
    assert items[0].source_title == "项目评审会"
    assert items[0].target_title == "项目群"
    assert items[0].codex_session_id == "meeting-session-1"


def test_history_applies_search_status_and_global_count(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _seed_meeting_run(store, status="no_action")

    assert store.count_history_items(send_statuses=("skipped",)) == 1
    [item] = store.list_history_items(
        limit=20,
        send_statuses=("skipped",),
        query_text="项目评审",
    )
    assert item.kind == "meeting"
    assert item.status == "skipped"
