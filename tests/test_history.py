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


def test_history_preserves_immutable_retry_run_after_job_succeeds(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-retry",
        title="重试会议",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-07-14T09:50:00+08:00",
        eligible_at="2026-07-14T10:00:00+08:00",
        status="pending",
    )
    retry_run = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="session-retry",
        decision_json="{}",
        audit_summary="首次调用失败",
        status="retry",
        error="temporary",
    )
    sent_run = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="session-sent",
        decision_json='{"action":"send"}',
        audit_summary="第二次调用成功",
        status="ready_to_send",
        error="",
    )
    store.update_meeting_alignment_job(job_id, status="sent")

    items = store.list_history_items(limit=20)
    statuses = {item.source_id: item.status for item in items}

    assert statuses[retry_run] == "failed"
    assert statuses[sent_run] == "sent"
    assert [item.source_id for item in store.list_history_items(
        limit=20, send_statuses=("failed",)
    )] == [retry_run]


def test_history_marks_earlier_ready_run_failed_after_later_send_succeeds(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="minutes-two-ready",
        title="两次投递会议",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-07-14T09:50:00+08:00",
        eligible_at="2026-07-14T10:00:00+08:00",
        status="pending",
    )
    first_run = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="session-first-ready",
        decision_json='{"action":"send"}',
        audit_summary="首次分析完成但发送失败",
        status="ready_to_send",
        error="",
    )
    second_run = store.record_meeting_alignment_run(
        job_id=job_id,
        codex_session_id="session-second-ready",
        decision_json='{"action":"send"}',
        audit_summary="重新分析并发送成功",
        status="ready_to_send",
        error="",
    )
    store.update_meeting_alignment_job(job_id, status="sent")

    statuses = {
        item.source_id: item.status for item in store.list_history_items(limit=20)
    }

    assert statuses[first_run] == "failed"
    assert statuses[second_run] == "sent"


def test_history_retains_reply_legacy_search_fields(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-search-legacy",
        conversation_title="研发群",
        trigger_message_id="msg-search-legacy",
        trigger_sender="Mina",
        trigger_text="原问题",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update reply_attempts set corrected_reply_text='修正版答案', send_error='legacy-error' where id=?",
            (attempt_id,),
        )

    for query in (
        "cid-search-legacy",
        "msg-search-legacy",
        "修正版答案",
        "legacy-error",
        "send_reply",
        "failed",
    ):
        assert [item.source_id for item in store.list_history_items(
            limit=20, query_text=query
        )] == [attempt_id]


def test_history_tie_order_is_stable_across_kinds(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    reply_id = store.record_reply_attempt(
        conversation_id="cid-tie",
        conversation_title="同秒群",
        trigger_message_id="msg-tie",
        trigger_sender="Mina",
        trigger_text="同秒",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )
    meeting_run_id = _seed_meeting_run(store)
    with sqlite3.connect(store.path) as db:
        db.execute("update reply_attempts set created_at='2026-07-14 10:00:00'")
        db.execute("update meeting_alignment_runs set created_at='2026-07-14 10:00:00'")

    first_page = store.list_history_items(limit=1, offset=0)
    second_page = store.list_history_items(limit=1, offset=1)

    assert [(item.kind, item.source_id) for item in first_page + second_page] == [
        ("reply", reply_id),
        ("meeting", meeting_run_id),
    ]
