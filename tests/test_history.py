import sqlite3

from app.store import AutoReplyStore
from app.universal_context import UniversalTaskContext
from app.universal_executor import build_universal_action_execution
from app.universal_plan import PlannedAction, PlannedActionKind, UniversalAudit, UniversalPlan


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


def test_history_reply_item_includes_redacted_universal_plan_summary(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-observe",
        conversation_title="可观测性群",
        single_chat=False,
        trigger_message_id="msg-observe",
        trigger_create_time="2026-07-21 09:00:00",
        trigger_sender="Mina",
        trigger_text="请审阅文档",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    context = UniversalTaskContext(
        task_id=task.id,
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_message_id=task.trigger_message_id,
        trigger_create_time=task.trigger_create_time,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        context_messages=(),
        required_dependencies=("dws",),
        force_new_decision=False,
        dry_run=False,
        execution_generation=task.execution_generation,
    )
    plan = UniversalPlan(
        task_kind="document_review",
        reason="Review document",
        dependencies=["dws"],
        actions=[
            PlannedAction(
                kind=PlannedActionKind.SEND_REPLY,
                reason="Send review",
                sensitivity_kind="general",
                payload={"text": "HISTORY_SECRET_SENTINEL"},
            )
        ],
        audit=UniversalAudit(summary="Document reviewed", confidence=0.9),
    )
    plan_execution = store.create_universal_plan_execution(context, plan)
    execution = build_universal_action_execution(
        context, plan_execution, plan_execution.plan.actions[0], 0
    )
    store.claim_universal_action_execution(execution)
    attempt_id = store.record_universal_reply_attempt(
        execution,
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    store.complete_universal_action_execution(execution, attempt_id=attempt_id)

    [item] = store.list_history_items(limit=20, kinds=("reply",))

    assert item.planner_kind == "universal"
    assert item.capability == "document_review"
    assert item.blocking_dependency == ""
    assert [(action.kind, action.status) for action in item.planned_actions] == [
        ("send_reply", "succeeded")
    ]
    assert "HISTORY_SECRET_SENTINEL" not in item.model_dump_json()


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


def test_history_includes_task_updates_and_follow_ups(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = store.create_work_project(
        title="AI 待办推进",
        category="product",
        priority="P1",
        risk_level="medium",
        owner_name="Mina",
        current_state="等待 Mina 反馈",
        next_step="补充 TODO 描述",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="向 Mina 解释待办更新",
        description="说明重要事项判断口径，并同步更新后的 TODO。",
        owner_name="Mina",
        priority="P1",
    )
    update_id = store.create_work_update(
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="42",
        summary="更新 TODO：补充 Mina 反馈事项描述",
        changes_json='{"action":"update_project"}',
        merge_reason="同一任务项目",
        confidence=0.9,
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="user-mina",
        owner_name="Mina",
        target_kind="direct",
        question_text="Mina，这个 TODO 描述是否清楚？",
        scheduled_at="2026-07-15 10:00:00",
        status="sent",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update work_updates set created_at='2026-07-15 10:00:00' where id=?",
            (update_id,),
        )
        db.execute(
            """
            update follow_up_drafts
            set sent_at='2026-07-15 10:01:00',
                updated_at='2026-07-15 10:01:00'
            where id=?
            """,
            (follow_up_id,),
        )

    items = store.list_history_items(limit=20, query_text="Mina")

    assert [(item.kind, item.source_id) for item in items[:2]] == [
        ("task", follow_up_id),
        ("task", update_id),
    ]
    assert items[0].project_id == project_id
    assert items[0].todo_id == todo_id
    assert items[0].follow_up_id == follow_up_id
    assert items[0].status == "sent"
    assert items[1].project_id == project_id
    assert items[1].status == "done"
    assert store.count_history_items(send_statuses=("done",), query_text="Mina") == 1


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
