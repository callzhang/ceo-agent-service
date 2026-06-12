import json
import sqlite3
from pathlib import Path

from app.store import AutoReplyStore
from app.task_models import WorkItem


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def _work_item() -> WorkItem:
    return WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1",
                "title": "项目进展",
                "conversation_id": "cid-1",
                "conversation_title": "售前项目群",
                "created_at": "2026-06-07 09:00:00",
            },
            "summary": "P1 项目需要三天内确认进展。",
            "project_name": "售前知识库建设",
            "context": {
                "sender": "Mina",
                "participants": ["Mina", "Derek", "Alex"],
                "source_conversation_kind": "group",
                "source_conversation_title": "售前项目群",
            },
        }
    )


def test_enqueue_and_claim_work_summary_input(tmp_path: Path):
    store = _store(tmp_path)
    payload_json = _work_item().model_dump_json()

    input_id = store.enqueue_work_summary_input("reply_attempt", "1", payload_json)
    duplicate_id = store.enqueue_work_summary_input("reply_attempt", "1", payload_json)

    assert input_id > 0
    assert duplicate_id == input_id

    claimed = store.claim_work_summary_inputs(limit=1)
    second_claim = store.claim_work_summary_inputs(limit=1)

    assert len(claimed) == 1
    assert claimed[0].id == input_id
    assert claimed[0].status == "processing"
    assert claimed[0].attempts == 1
    assert second_claim == []

    store.mark_work_summary_input_done(input_id)
    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        row = db.execute(
            "select status from work_summary_inputs where id=?",
            (input_id,),
        ).fetchone()
    assert row == ("done",)


def test_create_project_todo_update_and_follow_up(tmp_path: Path):
    store = _store(tmp_path)

    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json='["售前","知识库"]',
        status="active",
        priority="P1",
        risk_level="medium",
        needs_derek_attention=True,
        owner_user_id="owner-1",
        owner_name="Alex",
        goal="沉淀可复用售前材料",
        background="销售支持项目",
        facts_json='[{"description":"已确认材料路径","source":"reply_attempt","created":"2026-06-07","updated":"2026-06-07"}]',
        current_state="整理来源材料",
        next_step="确认边界",
        next_follow_up_at="2026-06-10 09:00:00",
        follow_up_mode="draft",
        source_conversations_json='[{"id":"cid-1","title":"售前项目群"}]',
    )

    project = store.get_work_project(project_id)
    assert project is not None
    assert project.title == "售前知识库建设"
    assert project.category == "sales"
    assert project.priority == "P1"
    assert project.risk_level == "medium"
    assert project.needs_derek_attention is True
    assert project.owner_user_id == "owner-1"
    assert project.owner_name == "Alex"
    assert project.goal == "沉淀可复用售前材料"
    assert project.background == "销售支持项目"
    assert project.facts_json == (
        '[{"description":"已确认材料路径","source":"reply_attempt",'
        '"created":"2026-06-07","updated":"2026-06-07"}]'
    )
    assert project.current_state == "整理来源材料"
    assert project.next_step == "确认边界"
    assert project.next_follow_up_at == "2026-06-10 09:00:00"
    assert project.follow_up_mode == "draft"
    assert project.source_conversations_json == (
        '[{"id":"cid-1","title":"售前项目群"}]'
    )

    store.update_work_project(
        project_id,
        current_state="等待 owner 回复",
        blocker="缺少来源链接",
        next_step="owner 补齐来源链接",
        next_follow_up_at="2026-06-11 09:00:00",
    )
    updated_project = store.get_work_project(project_id)
    assert updated_project is not None
    assert updated_project.current_state == "等待 owner 回复"
    assert updated_project.blocker == "缺少来源链接"
    assert updated_project.next_step == "owner 补齐来源链接"
    assert updated_project.next_follow_up_at == "2026-06-11 09:00:00"

    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐售前材料来源链接",
        owner_user_id="owner-1",
        owner_name="Alex",
        priority="P1",
        deadline_at="2026-06-10 18:00:00",
        next_follow_up_at="2026-06-10 09:00:00",
        follow_up_question="现在来源链接补齐到哪一步了？",
    )
    todos = store.list_work_todos(project_id=project_id)
    assert [todo.id for todo in todos] == [todo_id]
    assert todos[0].title == "补齐售前材料来源链接"

    update_id = store.create_work_update(
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="1",
        summary="新增 P1 跟进项",
        changes_json='{"todo_created":true}',
        merge_reason="同一售前项目",
        confidence=0.86,
    )
    updates = store.list_work_updates(project_id)
    assert [update.id for update in updates] == [update_id]
    assert updates[0].summary == "新增 P1 跟进项"

    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="售前材料来源链接现在补齐到哪一步了？",
        risk_check_json='{"owner_in_group":true}',
        scheduled_at="2026-06-10 09:00:00",
    )
    drafts = store.list_follow_up_drafts(statuses=("draft",))
    assert [draft.id for draft in drafts] == [draft_id]
    assert drafts[0].question_text == "售前材料来源链接现在补齐到哪一步了？"

    run_id = store.record_task_agent_run(
        summary_input_id=123,
        codex_session_id="sid",
        decision_json='{"action":"update_project"}',
        audit_summary="ok",
        memory_recall_used=True,
    )
    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        row = db.execute(
            "select memory_recall_used from task_agent_runs where id=?",
            (run_id,),
        ).fetchone()
    assert row == (1,)


def test_list_and_update_project_memory_context_backfill_targets(tmp_path: Path):
    store = _store(tmp_path)
    missing_id = store.create_work_project(
        title="缺少记忆背景项目",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    filled_id = store.create_work_project(
        title="已有记忆背景项目",
        category="sales",
        status="active",
        priority="P2",
        risk_level="low",
        memory_context_json='{"query":"已有","summary":"已有背景","memories":[]}',
    )
    with store._connect() as db:
        db.execute(
            """
            update work_projects
            set last_activity_at='2026-06-01 10:00:00',
                updated_at='2026-06-01 10:00:00'
            where id=?
            """,
            (missing_id,),
        )

    targets = store.list_work_projects_missing_memory_context(limit=10)

    assert [project.id for project in targets] == [missing_id]

    store.update_work_project_memory_context(
        missing_id,
        json.dumps(
            {
                "query": "缺少记忆背景项目",
                "summary": "已通过 memory_recall 回填。",
                "memories": [],
            },
            ensure_ascii=False,
        ),
    )

    updated = store.get_work_project(missing_id)
    filled = store.get_work_project(filled_id)
    assert updated is not None
    assert filled is not None
    assert json.loads(updated.memory_context_json)["summary"] == "已通过 memory_recall 回填。"
    assert updated.last_activity_at == "2026-06-01 10:00:00"
    assert filled.memory_context_json == '{"query":"已有","summary":"已有背景","memories":[]}'


def test_scan_state_round_trip(tmp_path: Path):
    store = _store(tmp_path)

    store.set_daily_scan_state(
        "ai_minutes",
        "2026-06-07 10:00:00",
        cursor_json='{"last_id":"m1"}',
        last_error="",
    )

    state = store.get_daily_scan_state("ai_minutes")
    assert state is not None
    assert state["last_success_at"] == "2026-06-07 10:00:00"
    assert state["cursor_json"] == '{"last_id":"m1"}'
    assert state["last_error"] == ""

    store.set_daily_scan_state(
        "ai_minutes",
        "2026-06-08 10:00:00",
        cursor_json='{"last_id":"m2"}',
        last_error="boom",
    )
    updated_state = store.get_daily_scan_state("ai_minutes")
    assert updated_state is not None
    assert updated_state["last_success_at"] == "2026-06-08 10:00:00"
    assert updated_state["cursor_json"] == '{"last_id":"m2"}'
    assert updated_state["last_error"] == "boom"


def test_operation_logs_sort_follow_up_by_operation_time_not_schedule(tmp_path: Path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=1,
        owner_name="Alex",
        target_kind="group",
        target_conversation_id="cid-1",
        question_text="进展如何？",
        scheduled_at="2099-01-01 10:00:00",
        status="draft",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="融资群",
        trigger_message_id="msg-2",
        trigger_sender="Lily",
        trigger_text="@Alex 这个怎么看？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按这个口径回复。",
    )
    with store._connect() as db:
        db.execute(
            "update follow_up_drafts set created_at='2026-06-01 10:00:00' where id=?",
            (draft_id,),
        )
        db.execute(
            """
            update reply_attempts
            set created_at='2026-06-02 10:00:00',
                updated_at='2026-06-02 10:00:00'
            where id=?
            """,
            (attempt_id,),
        )

    logs = store.list_operation_logs(limit=2)

    assert [log.category for log in logs] == ["Reply", "Follow-up"]
