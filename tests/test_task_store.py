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


def test_reset_stale_processing_work_summary_inputs_requeues_orphans(tmp_path: Path):
    db_path = tmp_path / "task.sqlite3"
    store = AutoReplyStore(db_path)
    payload_json = _work_item().model_dump_json()
    input_id = store.enqueue_work_summary_input("reply_attempt", "1", payload_json)

    claimed = store.claim_work_summary_inputs(limit=1)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update work_summary_inputs set updated_at=datetime('now', '-31 minutes') where id=?",
            (claimed[0].id,),
        )

    reset_count = store.reset_stale_processing_work_summary_inputs(30 * 60)
    reclaimed = store.claim_work_summary_inputs(limit=1)

    assert reset_count == 1
    assert reclaimed[0].id == input_id
    assert reclaimed[0].attempts == 2


def test_reset_stale_processing_work_summary_inputs_keeps_fresh_processing(
    tmp_path: Path,
):
    store = _store(tmp_path)
    payload_json = _work_item().model_dump_json()
    store.enqueue_work_summary_input("reply_attempt", "1", payload_json)
    store.claim_work_summary_inputs(limit=1)

    reset_count = store.reset_stale_processing_work_summary_inputs(30 * 60)

    assert reset_count == 0
    assert store.claim_work_summary_inputs(limit=1) == []


def test_work_summary_retry_backoff_delays_claim_until_available(tmp_path: Path):
    store = _store(tmp_path)
    payload_json = _work_item().model_dump_json()
    input_id = store.enqueue_work_summary_input("reply_attempt", "1", payload_json)
    store.claim_work_summary_inputs(limit=1)

    store.schedule_work_summary_input_retry(
        input_id,
        "stream disconnected before completion",
        available_at="2099-01-01 00:00:00",
    )

    assert store.claim_work_summary_inputs(limit=1) == []

    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        db.execute(
            "update work_summary_inputs set available_at=datetime('now', '-1 second') where id=?",
            (input_id,),
        )

    claimed = store.claim_work_summary_inputs(limit=1)

    assert claimed[0].id == input_id
    assert claimed[0].attempts == 2


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
    fetched_draft = store.get_follow_up_draft(draft_id)
    assert fetched_draft is not None
    assert fetched_draft.id == draft_id
    assert store.get_follow_up_draft(999) is None

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


def test_list_recent_follow_up_candidates_returns_linked_context(tmp_path: Path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="海外数据合规与中美开发隔离闭环",
        category="strategy",
        status="active",
        priority="P0",
        risk_level="high",
        owner_user_id="owner-project",
        owner_name="Ming Hu",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认中美开发隔离方案执行状态",
        owner_user_id="owner-1",
        owner_name="Ming Hu",
        status="open",
        priority="P0",
        deadline_at="2026-06-29 18:00:00",
        next_follow_up_at="2026-06-29 09:00:00",
        follow_up_question="隔离方案今天能闭环吗？",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Ming Hu",
        target_conversation_id="cid-data",
        target_kind="group",
        question_text="隔离方案今天能闭环吗？",
        status="sent",
        sent_at="2026-06-29 09:30:00",
        scheduled_at="2026-06-29 09:00:00",
        reaction_status="",
        reaction_summary="",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Ming Hu",
        target_conversation_id="cid-data",
        target_kind="group",
        question_text="旧跟进不应该作为候选",
        status="sent",
        sent_at="2026-06-20 09:30:00",
        scheduled_at="2026-06-20 09:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-2",
        owner_name="Lily",
        target_conversation_id="cid-other",
        target_kind="direct",
        question_text="无关会话不应该作为候选",
        status="sent",
        sent_at="2026-06-29 10:00:00",
        scheduled_at="2026-06-29 09:50:00",
    )

    candidates = store.list_recent_follow_up_candidates(
        conversation_id="cid-data",
        owner_user_id="owner-1",
        since="2026-06-28 00:00:00",
        limit=10,
    )

    assert [candidate.follow_up_id for candidate in candidates] == [draft_id]
    candidate = candidates[0]
    assert candidate.project_id == project_id
    assert candidate.project_title == "海外数据合规与中美开发隔离闭环"
    assert candidate.project_status == "active"
    assert candidate.project_priority == "P0"
    assert candidate.todo_id == todo_id
    assert candidate.todo_title == "确认中美开发隔离方案执行状态"
    assert candidate.todo_status == "open"
    assert candidate.todo_priority == "P0"
    assert candidate.owner_user_id == "owner-1"
    assert candidate.owner_name == "Ming Hu"
    assert candidate.target_conversation_id == "cid-data"
    assert candidate.target_kind == "group"
    assert candidate.question_text == "隔离方案今天能闭环吗？"
    assert candidate.scheduled_at == "2026-06-29 09:00:00"
    assert candidate.sent_at == "2026-06-29 09:30:00"
    assert candidate.status == "sent"
    assert candidate.reaction_status == ""
    assert candidate.reaction_summary == ""


def test_list_recent_follow_up_candidates_prefers_conversation_then_owner(
    tmp_path: Path,
):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户验收",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    owner_match_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-other",
        target_kind="direct",
        question_text="owner 近期跟进",
        status="sent",
        sent_at="2026-06-29 10:00:00",
        scheduled_at="2026-06-29 09:50:00",
    )
    conversation_match_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-2",
        owner_name="Mina",
        target_conversation_id="cid-target",
        target_kind="group",
        question_text="同群较早跟进",
        status="sent",
        sent_at="2026-06-29 09:00:00",
        scheduled_at="2026-06-29 08:50:00",
    )

    candidates = store.list_recent_follow_up_candidates(
        conversation_id="cid-target",
        owner_user_id="owner-1",
        since="2026-06-28 00:00:00",
        limit=10,
    )

    assert [candidate.follow_up_id for candidate in candidates] == [
        conversation_match_id,
        owner_match_id,
    ]


def test_list_recent_follow_up_candidates_includes_scheduled_actionable_statuses(
    tmp_path: Path,
):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户验收",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-target",
        target_kind="group",
        question_text="draft 候选应该按 scheduled_at 命中",
        status="draft",
        scheduled_at="2026-06-29 10:00:00",
    )
    approved_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-2",
        owner_name="Mina",
        target_conversation_id="cid-target",
        target_kind="group",
        question_text="approved 候选也应该按 scheduled_at 命中",
        status="approved",
        scheduled_at="2026-06-29 11:00:00",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-target",
        target_kind="group",
        question_text="旧 draft 不应该命中",
        status="draft",
        scheduled_at="2026-06-20 10:00:00",
    )

    candidates = store.list_recent_follow_up_candidates(
        conversation_id="cid-target",
        owner_user_id="",
        since="2026-06-28 00:00:00",
        limit=10,
    )

    assert [candidate.follow_up_id for candidate in candidates] == [
        approved_id,
        draft_id,
    ]
    assert [candidate.status for candidate in candidates] == ["approved", "draft"]


def test_list_recent_follow_up_candidates_requires_conversation_or_owner(
    tmp_path: Path,
):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户验收",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-target",
        target_kind="group",
        question_text="验收 ETA 有更新吗？",
        status="sent",
        sent_at="2026-06-29 10:00:00",
        scheduled_at="2026-06-29 09:50:00",
    )

    assert (
        store.list_recent_follow_up_candidates(
            conversation_id=" ",
            owner_user_id="",
            since="2026-06-28 00:00:00",
            limit=10,
        )
        == []
    )


def test_list_recent_follow_up_candidates_respects_non_positive_limit(
    tmp_path: Path,
):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户验收",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-target",
        target_kind="group",
        question_text="验收 ETA 有更新吗？",
        status="sent",
        sent_at="2026-06-29 10:00:00",
        scheduled_at="2026-06-29 09:50:00",
    )

    assert (
        store.list_recent_follow_up_candidates(
            conversation_id="cid-target",
            owner_user_id="owner-1",
            since="2026-06-28 00:00:00",
            limit=0,
        )
        == []
    )


def test_dingtalk_todo_link_create_get_update_and_active_lookup(tmp_path: Path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )

    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="",
        executor_user_id="owner-1",
        executor_name="Alex",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="creating",
    )

    link = store.get_work_todo_dingtalk_link(link_id)
    assert link is not None
    assert link.work_todo_id == todo_id
    assert link.status == "creating"
    assert store.get_active_work_todo_dingtalk_link(todo_id).id == link_id

    store.update_work_todo_dingtalk_link(
        link_id,
        dingtalk_task_id="dt-task-1",
        status="active",
        last_dingtalk_done=False,
        last_dingtalk_payload_json='{"id":"dt-task-1","done":false}',
        last_push_at="2026-06-27 10:00:00",
    )

    updated = store.get_work_todo_dingtalk_link(link_id)
    assert updated.dingtalk_task_id == "dt-task-1"
    assert updated.status == "active"
    assert updated.last_dingtalk_done is False
    assert updated.last_error == ""


def test_dingtalk_todo_link_prevents_duplicate_active_links(tmp_path: Path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        status="open",
        deadline_at="2026-07-01 18:00:00",
    )
    first_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="creating",
    )

    second_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="creating",
    )

    assert second_id == first_id
    assert len(store.list_work_todo_dingtalk_links(statuses=("creating",))) == 1


def test_operation_logs_include_dingtalk_todo_links(tmp_path: Path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        status="open",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="failed",
        last_error="todo get failed",
    )

    logs = store.list_operation_logs(query="dt-task-1")

    assert len(logs) == 1
    assert logs[0].category == "DingTalk Todo"
    assert logs[0].status == "failed"
    assert "dt-task-1" in logs[0].context
    assert "todo get failed" in logs[0].detail


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


def test_list_work_todo_dingtalk_links_filters_by_work_todo_before_limit(
    tmp_path: Path,
):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    other_todo_id = store.create_work_todo(
        project_id=project_id,
        title="同步其他事项",
        owner_user_id="owner-1",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    target_todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-2",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=other_todo_id,
        dingtalk_task_id="dt-other",
        status="failed",
    )
    target_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=target_todo_id,
        dingtalk_task_id="dt-target",
        status="failed",
    )

    links = store.list_work_todo_dingtalk_links(
        statuses=("failed",),
        work_todo_id=target_todo_id,
        limit=1,
    )

    assert [link.id for link in links] == [target_link_id]

    second_target_todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认第二个验收 ETA",
        owner_user_id="owner-3",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    store.create_work_todo_dingtalk_link(
        work_todo_id=second_target_todo_id,
        dingtalk_task_id="",
        status="failed",
    )
    second_target_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=second_target_todo_id,
        dingtalk_task_id="dt-second-target",
        status="failed",
    )
    recoverable_links = store.list_work_todo_dingtalk_links(
        statuses=("failed",),
        work_todo_id=second_target_todo_id,
        with_dingtalk_task_id=True,
        limit=1,
    )

    assert [link.id for link in recoverable_links] == [second_target_link_id]


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
