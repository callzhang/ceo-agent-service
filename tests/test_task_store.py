from tests.support.legacy_todo_facts import seed_legacy_todo_link, seed_legacy_work_project, revise_legacy_work_project, seed_legacy_work_todo, revise_legacy_work_todo, seed_legacy_work_update
from app.action_storage import REPLY_ATTEMPT_COMMON_SQL, update_action, update_sync_action
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from pathlib import Path
from threading import Barrier

import pytest

from app.state_storage import update_state_rows
from app.store import TASK_LOGICAL_RUN_SQL, REPLY_TASK_SQL, WORK_SUMMARY_INPUT_SQL
from app.store import AgentRole, AutoReplyStore
from app.task_models import WorkItem


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def test_current_task_execution_schema_has_no_legacy_auxiliary_tables(tmp_path):
    store = _store(tmp_path)
    from app.current_schema_manifest import schema_is_current
    with sqlite3.connect(store.path) as db:
        assert schema_is_current(db)
        assert not db.execute("select 1 from sqlite_schema where name in "
                              "('business_task_dingtalk_links','business_task_follow_ups',"
                              "'business_task_follow_up_send_attempts','business_task_todo_sync_outbox')").fetchone()


def test_business_task_dingtalk_links_list_filters_status_and_paginates(tmp_path):
    store = _store(tmp_path)
    task_ids = [store.create_business_task(title=f"Task {index}", stage="candidate") for index in range(3)]
    for index, task_id in enumerate(task_ids):
        store.create_business_task_dingtalk_link(
            business_task_id=task_id,
            dingtalk_task_id=f"dt-{index}",
            status="active" if index < 2 else "done",
        )

    first = store.list_business_task_dingtalk_links(statuses=("active",), limit=1)
    second = store.list_business_task_dingtalk_links(statuses=("active",), limit=1, offset=1)

    assert [row["business_task_id"] for row in first] == [task_ids[0]]
    assert [row["business_task_id"] for row in second] == [task_ids[1]]
    assert len(store.list_business_task_dingtalk_links(statuses=("done",))) == 1


def _email_task_values(trigger_message_id: str) -> dict[str, object]:
    return {
        "channel": "email",
        "conversation_id": "email-thread:atomic",
        "conversation_title": "Email action",
        "single_chat": False,
        "trigger_message_id": trigger_message_id,
        "trigger_create_time": "2026-08-30T08:00:00+00:00",
        "trigger_sender": "sender@example.com",
        "trigger_text": "Immutable ActionPlan authorizes action.",
        "trigger_message_json": json.dumps(
            {"action_identity": trigger_message_id},
            sort_keys=True,
        ),
    }


def _claim_audit_run(
    store: AutoReplyStore,
    reply_task_id: int,
    execution_generation: str,
    **kwargs,
):
    return store.claim_agent_run(
        reply_task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"direct-agent:{reply_task_id}:{execution_generation}",
        **kwargs,
    )




def test_complete_agent_run_returns_after_transaction_connection_closes(
    tmp_path: Path,
):
    store = _store(tmp_path)
    task_id = store.enqueue_reply_task(
        conversation_id="cid-terminal-run",
        conversation_title="Terminal run",
        single_chat=False,
        trigger_message_id="msg-terminal-run",
        trigger_create_time="2026-08-27 00:00:00",
        trigger_sender="Derek",
        trigger_text="Complete this run",
        execution_generation="generation-terminal",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    assert task.id == task_id
    claim = _claim_audit_run(store, task.id, task.execution_generation, owner="worker-1")

    completed = store.complete_agent_run(
        claim.run.id,
        {"outcome": "no_action"},
        owner="worker-1",
    )

    assert completed.status == "completed"
    assert completed.id == claim.run.id
    assert store.get_agent_run(claim.run.id).status == "completed"


def test_ensure_reply_task_returns_one_immutable_queue_identity(tmp_path: Path):
    store = _store(tmp_path)
    first = store.ensure_reply_task(
        channel="email",
        conversation_id="email-thread:abc",
        conversation_title="Original subject",
        single_chat=False,
        trigger_message_id="email-action:def",
        trigger_create_time="2026-08-30T08:00:00+00:00",
        trigger_sender="sender@example.com",
        trigger_text="original safe trigger",
        trigger_message_json=json.dumps({"action_identity": "email-action:def"}),
    )
    replay = store.ensure_reply_task(
        channel="email",
        conversation_id="email-thread:abc",
        conversation_title="Changed subject must not rewrite history",
        single_chat=False,
        trigger_message_id="email-action:def",
        trigger_create_time="2026-08-30T08:01:00+00:00",
        trigger_sender="different@example.com",
        trigger_text="changed trigger must not replace the task",
        trigger_message_json=json.dumps({"action_identity": "different"}),
    )

    assert replay.id == first.id
    assert replay.conversation_title == "Original subject"
    assert replay.trigger_text == "original safe trigger"
    assert json.loads(replay.trigger_message_json) == {
        "action_identity": "email-action:def"
    }
    assert store.count_reply_tasks(channel="email") == 1


def test_ensure_reply_tasks_rolls_back_group_on_identity_conflict(tmp_path: Path):
    store = _store(tmp_path)
    conflicting = _email_task_values("email-action:second")
    store.ensure_reply_task(
        **{
            **conflicting,
            "trigger_message_json": json.dumps({"action_identity": "different"}),
        }
    )

    with pytest.raises(RuntimeError, match="identity"):
        store.ensure_reply_tasks(
            (
                _email_task_values("email-action:first"),
                conflicting,
            )
        )

    assert store.count_reply_tasks(channel="email") == 1
    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        assert (
            db.execute(
                f"""
                select count(*) from ({REPLY_TASK_SQL})
                where 1=1
                  and channel='email'
                  and conversation_id='email-thread:atomic'
                  and trigger_message_id='email-action:first'
                """
            ).fetchone()[0]
            == 0
        )


def test_ensure_reply_tasks_rolls_back_group_on_database_error(tmp_path: Path):
    database = tmp_path / "task.sqlite3"
    store = AutoReplyStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            """
            create trigger fail_second_email_task
            before insert on task_inputs
            when new.trigger_message_id='email-action:second'
            begin
                select raise(abort, 'injected batch failure');
            end
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected batch failure"):
        store.ensure_reply_tasks(
            (
                _email_task_values("email-action:first"),
                _email_task_values("email-action:second"),
            )
        )

    assert store.count_reply_tasks(channel="email") == 0


def test_concurrent_ensure_reply_task_groups_share_immutable_identities(
    tmp_path: Path,
):
    database = tmp_path / "task.sqlite3"
    AutoReplyStore(database)
    ready = Barrier(2)
    specs = (
        _email_task_values("email-action:first"),
        _email_task_values("email-action:second"),
    )

    def ensure_group() -> tuple[int, ...]:
        store = AutoReplyStore(database)
        ready.wait()
        return tuple(task.id for task in store.ensure_reply_tasks(specs))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(lambda _: ensure_group(), range(2))

    assert first == second
    assert AutoReplyStore(database).count_reply_tasks(channel="email") == 2




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
                "sender": "Avery",
                "participants": ["Avery", "Derek", "Alex"],
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
            f"select status from ({WORK_SUMMARY_INPUT_SQL}) where id=?",
            (input_id,),
        ).fetchone()
    assert row == ("done",)


@pytest.mark.parametrize("terminal_status", ["failed", "skipped"])
def test_duplicate_work_summary_input_does_not_reopen_terminal_status(
    tmp_path: Path,
    terminal_status: str,
):
    store = _store(tmp_path)
    payload_json = _work_item().model_dump_json()
    input_id = store.enqueue_work_summary_input("reply_attempt", "1", payload_json)

    if terminal_status == "failed":
        store.mark_work_summary_input_failed(input_id, "provider unavailable")
    else:
        store.mark_work_summary_input_skipped(input_id, "no lifecycle transition")

    duplicate_id = store.enqueue_work_summary_input(
        "reply_attempt",
        "1",
        _work_item()
        .model_copy(update={"summary": "Updated source snapshot"})
        .model_dump_json(),
    )

    assert duplicate_id == input_id
    assert store.claim_work_summary_inputs(limit=1) == []
    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        row = db.execute(
            f"select status, attempts, error from ({WORK_SUMMARY_INPUT_SQL}) where id=?",
            (input_id,),
        ).fetchone()
    assert row == (
        terminal_status,
        0,
        "provider unavailable"
        if terminal_status == "failed"
        else "no lifecycle transition",
    )


def test_todo_evidence_candidate_dedupes_and_marks_decision(tmp_path: Path):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="客户验收",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="确认客户验收完成",
        status="open",
    )

    first = store.upsert_todo_evidence_candidate(
        project_id=project_id,
        todo_id=todo_id,
        source_type="dws_message",
        source_ref="msg-1",
        source_created_at="2026-06-28 10:00:00",
        evidence_text="客户已经回复验收完成。",
        reason="消息提到验收完成。",
        confidence=0.8,
    )
    duplicate = store.upsert_todo_evidence_candidate(
        project_id=project_id,
        todo_id=todo_id,
        source_type="dws_message",
        source_ref="msg-1",
        source_created_at="2026-06-28 10:00:00",
        evidence_text="客户已经回复验收完成。",
        reason="同一消息重复扫描。",
        confidence=0.9,
    )

    assert duplicate.id == first.id
    assert store.list_todo_evidence_candidates(todo_id=todo_id)[0].status == "candidate"

    input_id = store.enqueue_work_summary_input(
        "todo_completion_evidence_candidate",
        f"todo-evidence:{first.id}",
        "{}",
    )
    store.mark_todo_evidence_candidate_enqueued(first.id, input_id)
    store.mark_todo_evidence_candidate(
        first.id,
        status="accepted",
        decision_json=json.dumps({"todo_changes": [{"todo_id": todo_id, "action": "close"}]}),
    )

    candidate = store.get_todo_evidence_candidate(first.id)
    assert candidate is not None
    assert candidate.status == "accepted"
    assert candidate.work_summary_input_id == input_id
    assert json.loads(candidate.decision_json)["todo_changes"][0]["todo_id"] == todo_id

    logs = store.list_operation_logs(query="客户已经回复验收完成")
    assert len(logs) == 1
    assert logs[0].category == "TODO completion evidence"
    assert logs[0].status == "accepted"
    assert logs[0].context == "确认客户验收完成"


def test_claim_work_summary_input_uses_lock_retrying_transaction(
    monkeypatch, tmp_path: Path
):
    store = _store(tmp_path)
    input_id = store.enqueue_work_summary_input(
        "reply_attempt", "1", _work_item().model_dump_json()
    )
    original_transaction = store._immediate_write_transaction
    calls = 0

    def retrying_transaction():
        nonlocal calls
        calls += 1
        return original_transaction()

    monkeypatch.setattr(store, "_immediate_write_transaction", retrying_transaction)

    claimed = store.claim_work_summary_inputs(limit=1)

    assert [item.id for item in claimed] == [input_id]
    assert calls == 1


def test_reset_stale_processing_work_summary_inputs_requeues_orphans(tmp_path: Path):
    db_path = tmp_path / "task.sqlite3"
    store = AutoReplyStore(db_path)
    payload_json = _work_item().model_dump_json()
    input_id = store.enqueue_work_summary_input("reply_attempt", "1", payload_json)

    claimed = store.claim_work_summary_inputs(limit=1)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "update tasks set updated_at=datetime('now', '-31 minutes') "
            "where kind='work_summary' and id=?",
            (claimed[0].id,),
        )

    reset_count = store.reset_stale_processing_work_summary_inputs(30 * 60)
    reclaimed = store.claim_work_summary_inputs(limit=1)

    assert reset_count == 1
    assert reclaimed[0].id == input_id
    assert reclaimed[0].attempts == 1


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
            "update tasks set available_at=datetime('now', '-1 second') "
            "where kind='work_summary' and id=?",
            (input_id,),
        )

    claimed = store.claim_work_summary_inputs(limit=1)

    assert claimed[0].id == input_id
    assert claimed[0].attempts == 2


def test_historical_project_todo_facts_and_follow_up_readers(tmp_path: Path):
    store = _store(tmp_path)

    project_id = seed_legacy_work_project(store,
        title="售前知识库建设",
        category="sales",
        tags_json='["售前","知识库"]',
        status="active",
        owner_user_id="owner-1",
        owner_name="Alex",
        goal="沉淀可复用售前材料",
        background="销售支持项目",
        facts_json='[{"description":"已确认材料路径","source":"reply_attempt","created":"2026-06-07","updated":"2026-06-07"}]',
        current_state="整理来源材料",
        next_step="确认边界",
        source_conversations_json='[{"id":"cid-1","title":"售前项目群"}]',
    )

    project = store.get_work_project(project_id)
    assert project is not None
    assert project.title == "售前知识库建设"
    assert project.category == "sales"
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
    assert project.source_conversations_json == (
        '[{"id":"cid-1","title":"售前项目群"}]'
    )

    revise_legacy_work_project(store,
        project_id,
        current_state="等待 owner 回复",
        blocker="缺少来源链接",
        next_step="owner 补齐来源链接",
    )
    updated_project = store.get_work_project(project_id)
    assert updated_project is not None
    assert updated_project.current_state == "等待 owner 回复"
    assert updated_project.blocker == "缺少来源链接"
    assert updated_project.next_step == "owner 补齐来源链接"

    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="补齐售前材料来源链接",
        description="确认售前知识库里每份材料的来源链接、使用场景和缺口 owner。",
        owner_user_id="owner-1",
        owner_name="Alex",
        deadline_at="2026-06-10 18:00:00",
        follow_up_question="现在来源链接补齐到哪一步了？",
    )
    todos = store.list_work_todos(project_id=project_id)
    assert [todo.id for todo in todos] == [todo_id]
    assert todos[0].title == "补齐售前材料来源链接"
    assert todos[0].description == "确认售前知识库里每份材料的来源链接、使用场景和缺口 owner。"
    revise_legacy_work_todo(store,
        todo_id,
        description="补齐来源链接，并写清每份材料用于哪个客户场景。",
    )
    updated_todo = store.get_work_todo(todo_id)
    assert updated_todo is not None
    assert updated_todo.description == "补齐来源链接，并写清每份材料用于哪个客户场景。"

    update_id = seed_legacy_work_update(store,
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="1",
        summary="新增 P1 跟进项",
        changes_json='{"todo_created":true}',
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


def test_list_follow_up_drafts_due_before_handles_iso_timezone(tmp_path: Path):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="宝马项目周末攻坚与客户Demo推进",
        category="sales",
        status="active",
    )
    due_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_name="Casey Huang",
        target_kind="direct",
        question_text="准备宝马专家邀请材料了吗？",
        scheduled_at="2026-07-22T10:00:00+08:00",
        status="draft",
    )
    future_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_name="Casey Huang",
        target_kind="direct",
        question_text="宝马报价材料准备好了吗？",
        scheduled_at="2026-07-23T10:00:00+08:00",
        status="draft",
    )

    drafts = store.list_follow_up_drafts(
        statuses=("draft",),
        due_before="2026-07-22 16:00:00",
    )

    assert [draft.id for draft in drafts] == [due_id]
    assert future_id not in [draft.id for draft in drafts]

    parent = store.enqueue_work_summary_input("local_file", "follow-up", "{}")
    run_id = store.create_professional_run(parent, session_id="sid")
    with sqlite3.connect(tmp_path / "task.sqlite3") as db:
        columns = {column[0] for column in db.execute(
            f"select * from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,),
        ).description}
    assert "memory_recall_used" not in columns


def test_create_follow_up_draft_dedupes_skipped_terminal_draft(tmp_path: Path):
    store = _store(tmp_path)

    project_id = seed_legacy_work_project(store,
        title="售前知识库建设",
        category="sales",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="补齐售前材料来源链接",
        owner_user_id="owner-1",
        owner_name="Alex",
    )
    skipped_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="售前材料来源链接现在补齐到哪一步了？",
        status="skipped",
        scheduled_at="2026-06-10 09:00:00",
    )

    duplicate_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="售前材料来源链接现在补齐到哪一步了？",
        status="draft",
        scheduled_at="2026-06-11 09:00:00",
    )

    assert duplicate_id == skipped_id
    assert store.list_follow_up_drafts(statuses=("draft",)) == []


def test_list_recent_follow_up_candidates_returns_linked_context(tmp_path: Path):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="海外数据合规与中美开发隔离闭环",
        category="strategy",
        status="active",
        owner_user_id="owner-project",
        owner_name="Ming Hu",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="确认中美开发隔离方案执行状态",
        owner_user_id="owner-1",
        owner_name="Ming Hu",
        status="open",
        deadline_at="2026-06-29 18:00:00",
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
        owner_name="Riley",
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
    assert candidate.todo_id == todo_id
    assert candidate.todo_title == "确认中美开发隔离方案执行状态"
    assert candidate.todo_status == "open"
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
    project_id = seed_legacy_work_project(store,
        title="客户验收",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
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
        owner_name="Avery",
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
    project_id = seed_legacy_work_project(store,
        title="客户验收",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
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
        owner_name="Avery",
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
    project_id = seed_legacy_work_project(store,
        title="客户验收",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
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
    project_id = seed_legacy_work_project(store,
        title="客户验收",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="确认客户验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
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


def test_historical_dingtalk_todo_link_get_and_active_lookup(tmp_path: Path):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="客户交付",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        deadline_at="2026-07-01 18:00:00",
    )

    link_id = seed_legacy_todo_link(store,
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

    with store._immediate_write_transaction() as db:
        from app.action_storage import update_sync_action

        update_sync_action(db, "work_todo_dingtalk_links", link_id, {
            "dingtalk_task_id": "dt-task-1", "status": "active",
            "last_dingtalk_done": False,
            "last_dingtalk_payload_json": '{"id":"dt-task-1","done":false}',
            "last_push_at": "2026-06-27 10:00:00",
        })

    updated = store.get_work_todo_dingtalk_link(link_id)
    assert updated.dingtalk_task_id == "dt-task-1"
    assert updated.status == "active"
    assert updated.last_dingtalk_done is False
    assert updated.last_error == ""


def test_operation_logs_include_dingtalk_todo_links(tmp_path: Path):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="客户交付",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="给客户同步验收 ETA",
        status="open",
    )
    seed_legacy_todo_link(store,
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
    assert logs[0].context == "客户交付"
    assert logs[0].action == "dt-task-1"
    assert "todo get failed" in logs[0].detail


def test_list_sent_todo_records_combines_dingtalk_todos_and_followups(
    tmp_path: Path,
):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="客户交付",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="给客户同步验收 ETA",
        description="确认交付验收时间和阻塞。",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        deadline_at="2026-07-01 18:00:00",
    )
    seed_legacy_todo_link(store,
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        executor_name="Alex",
        title_snapshot="给客户同步验收 ETA：来源于客户群红灯风险",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
        last_push_at="2026-06-27 09:00:00",
    )
    seed_legacy_todo_link(store,
        work_todo_id=todo_id,
        dingtalk_task_id="",
        executor_user_id="owner-1",
        executor_name="Alex",
        title_snapshot="失败前未拿到 task id",
        status="failed",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="基于客户群红灯风险，请确认验收 ETA。",
        status="sent",
        sent_at="2026-06-27 10:00:00",
    )

    records = store.list_sent_todo_records()

    assert [record.kind for record in records] == ["follow_up", "dingtalk_todo"]
    assert records[0].original_text == "基于客户群红灯风险，请确认验收 ETA。"
    assert records[0].target_conversation_id == "cid-1"
    assert records[1].external_id == "dt-task-1"
    assert records[1].project_title == "客户交付"
    assert records[1].todo_description == "确认交付验收时间和阻塞。"
    assert records[1].original_text == "给客户同步验收 ETA：来源于客户群红灯风险"


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


def test_recent_single_chat_recovery_preserves_latest_trigger_identity(tmp_path: Path):
    store = _store(tmp_path)
    store.upsert_conversation(
        conversation_id="cid-mina",
        title="Avery",
        single_chat=True,
        codex_session_id=None,
    )
    task_id = store.enqueue_reply_task(
        conversation_id="cid-mina",
        conversation_title="Avery",
        single_chat=True,
        trigger_message_id="msg-mina",
        trigger_create_time="2026-09-07 17:02:04",
        trigger_sender="Avery",
        trigger_text="上一条消息",
        trigger_message_json=json.dumps(
            {
                "sender_user_id": "mina-user",
                "sender_open_dingtalk_id": "mina-open-id",
            },
            ensure_ascii=False,
        ),
    )
    assert task_id is not None
    store.mark_seen("msg-mina", "cid-mina")

    records = store.list_recent_single_chat_conversations(
        "2000-01-01 00:00:00",
        limit=10,
    )

    assert len(records) == 1
    assert records[0].direct_user_id == "mina-user"
    assert records[0].direct_open_dingtalk_id == "mina-open-id"


def test_recent_single_chat_recovery_uses_task_activity_when_seen_row_is_old(
    tmp_path: Path,
):
    store = _store(tmp_path)
    store.upsert_conversation(
        conversation_id="cid-mina",
        title="Avery",
        single_chat=True,
        codex_session_id=None,
    )
    store.mark_seen("msg-old", "cid-mina")
    with store._connect() as db:
        db.execute("begin immediate")
        update_state_rows(
            db, "seen_messages", {"seen_at": "2026-01-01 00:00:00"},
            where_sql="message_id=?", parameters=("msg-old",),
        )
    task_id = store.enqueue_reply_task(
        conversation_id="cid-mina",
        conversation_title="Avery",
        single_chat=True,
        trigger_message_id="msg-recent",
        trigger_create_time="2026-09-07 17:02:04",
        trigger_sender="Avery",
        trigger_text="最近消息",
        trigger_message_json=json.dumps(
            {"sender_open_dingtalk_id": "mina-open-id"},
            ensure_ascii=False,
        ),
    )
    assert task_id is not None

    records = store.list_recent_single_chat_conversations(
        "2026-09-01 00:00:00",
        limit=10,
    )

    assert [record.conversation_id for record in records] == ["cid-mina"]


def test_recent_single_chat_recovery_ignores_compacted_task_json(tmp_path: Path):
    store = _store(tmp_path)
    store.upsert_conversation(
        conversation_id="cid-compacted",
        title="Compacted contact",
        single_chat=True,
        codex_session_id=None,
    )
    task_id = store.enqueue_reply_task(
        conversation_id="cid-compacted",
        conversation_title="Compacted contact",
        single_chat=True,
        trigger_message_id="msg-compacted",
        trigger_create_time="2026-09-07 17:02:04",
        trigger_sender="Contact",
        trigger_text="old content",
        trigger_message_json=json.dumps({"sender_user_id": "old-user"}),
    )
    assert task_id is not None
    store.mark_seen("msg-compacted", "cid-compacted")
    with store._connect() as db:
        db.execute(
            "update task_inputs set trigger_text='', trigger_message_json='', "
            "input_compacted=1 where task_id=?",
            (task_id,),
        )

    records = store.list_recent_single_chat_conversations(
        "2000-01-01 00:00:00", limit=10,
    )

    assert [record.conversation_id for record in records] == ["cid-compacted"]
    assert records[0].direct_user_id == ""
    assert records[0].direct_open_dingtalk_id == ""


def test_list_work_todo_dingtalk_links_filters_by_work_todo_before_limit(
    tmp_path: Path,
):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="客户交付",
        category="projects",
        status="active",
    )
    other_todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="同步其他事项",
        owner_user_id="owner-1",
        status="open",
        deadline_at="2026-07-01 18:00:00",
    )
    target_todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-2",
        status="open",
        deadline_at="2026-07-01 18:00:00",
    )
    seed_legacy_todo_link(store,
        work_todo_id=other_todo_id,
        dingtalk_task_id="dt-other",
        status="failed",
    )
    target_link_id = seed_legacy_todo_link(store,
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

    second_target_todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="确认第二个验收 ETA",
        owner_user_id="owner-3",
        status="open",
        deadline_at="2026-07-01 18:00:00",
    )
    seed_legacy_todo_link(store,
        work_todo_id=second_target_todo_id,
        dingtalk_task_id="",
        status="failed",
    )
    second_target_link_id = seed_legacy_todo_link(store,
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


def test_list_work_todo_dingtalk_links_for_todo_returns_all_matches(
    tmp_path: Path,
):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="客户交付",
        category="projects",
        status="active",
    )
    todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        status="open",
        deadline_at="2026-07-01 18:00:00",
    )
    other_todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="同步其他事项",
        owner_user_id="owner-2",
        status="open",
        deadline_at="2026-07-01 18:00:00",
    )
    link_ids = [
        seed_legacy_todo_link(store,
            work_todo_id=todo_id,
            dingtalk_task_id=f"dt-target-{index}",
            status="failed",
        )
        for index in range(101)
    ]
    seed_legacy_todo_link(store,
        work_todo_id=other_todo_id,
        dingtalk_task_id="dt-other",
        status="failed",
    )

    links = store.list_work_todo_dingtalk_links_for_todo(
        todo_id,
        statuses=("failed",),
    )

    assert [link.id for link in links] == link_ids


def test_list_follow_up_drafts_for_todos_groups_by_todo(
    tmp_path: Path,
):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="客户交付",
        category="projects",
        status="active",
    )
    first_todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="第一项",
        status="open",
    )
    second_todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="第二项",
        status="open",
    )
    other_todo_id = seed_legacy_work_todo(store,
        project_id=project_id,
        title="第三项",
        status="open",
    )
    first_draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=first_todo_id,
        owner_name="Alex",
        target_kind="direct",
        question_text="第一项进展？",
        scheduled_at="2026-08-29 09:00:00",
        status="sent",
    )
    second_draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=second_todo_id,
        owner_name="Avery",
        target_kind="direct",
        question_text="第二项进展？",
        scheduled_at="2026-08-29 10:00:00",
        status="completed",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=other_todo_id,
        owner_name="ET",
        target_kind="direct",
        question_text="第三项进展？",
        scheduled_at="2026-08-29 11:00:00",
        status="completed",
    )

    grouped = store.list_follow_up_drafts_for_todos([first_todo_id, second_todo_id])

    assert [draft.id for draft in grouped[first_todo_id]] == [first_draft_id]
    assert [draft.id for draft in grouped[second_todo_id]] == [second_draft_id]
    assert other_todo_id not in grouped


def test_list_work_todos_for_projects_groups_by_project(
    tmp_path: Path,
):
    store = _store(tmp_path)
    first_project_id = seed_legacy_work_project(store,
        title="第一项目",
        category="projects",
        status="active",
    )
    second_project_id = seed_legacy_work_project(store,
        title="第二项目",
        category="projects",
        status="active",
    )
    other_project_id = seed_legacy_work_project(store,
        title="第三项目",
        category="projects",
        status="active",
    )
    first_todo_id = seed_legacy_work_todo(store,
        project_id=first_project_id,
        title="第一项",
        status="open",
    )
    second_todo_id = seed_legacy_work_todo(store,
        project_id=second_project_id,
        title="第二项",
        status="done",
    )
    seed_legacy_work_todo(store,
        project_id=other_project_id,
        title="第三项",
        status="open",
    )

    grouped = store.list_work_todos_for_projects([first_project_id, second_project_id])

    assert [todo.id for todo in grouped[first_project_id]] == [first_todo_id]
    assert [todo.id for todo in grouped[second_project_id]] == [second_todo_id]
    assert other_project_id not in grouped


def test_list_work_project_ids_for_todo_owner_filters_active_projects(
    tmp_path: Path,
):
    store = _store(tmp_path)
    active_project_id = seed_legacy_work_project(store,
        title="技术部招聘",
        category="recruiting",
        status="active",
    )
    archived_project_id = seed_legacy_work_project(store,
        title="历史招聘",
        category="recruiting",
        status="archived",
    )
    seed_legacy_work_todo(store,
        project_id=active_project_id,
        title="评估 Colin",
        owner_user_id="owner-1",
        owner_name="Avery",
    )
    seed_legacy_work_todo(store,
        project_id=archived_project_id,
        title="归档候选人",
        owner_user_id="owner-1",
        owner_name="Avery",
    )

    assert store.list_work_project_ids_for_todo_owner("owner-1") == {
        active_project_id
    }


def test_operation_logs_sort_follow_up_by_operation_time_not_schedule(tmp_path: Path):
    store = _store(tmp_path)
    project_id = seed_legacy_work_project(store,
        title="售前知识库建设",
        category="sales",
        status="active",
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
        trigger_sender="Riley",
        trigger_text="@Alex 这个怎么看？",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="先按这个口径回复。",
    )
    with store._connect() as db:
        db.execute("begin immediate")
        update_sync_action(db, "follow_up_drafts", draft_id, {
            "created_at": "2026-06-01 10:00:00",
        })
        update_action(db, "reply_attempts", attempt_id, {
            "created_at": "2026-06-02 10:00:00",
            "updated_at": "2026-06-02 10:00:00",
        })

    logs = store.list_operation_logs(limit=2)

    assert [log.category for log in logs] == ["Reply", "Follow-up"]


def test_operation_logs_query_prunes_unrequested_sources(tmp_path: Path):
    store = _store(tmp_path)

    query = store._operation_logs_base_query(source_tables=("reply_attempts",))

    assert REPLY_ATTEMPT_COMMON_SQL in query
    assert "from errors" not in query
    assert "'reply_tasks' as source_table" not in query


def test_operation_logs_page_and_count_share_one_materialized_query(tmp_path: Path):
    store = _store(tmp_path)
    for index in range(3):
        store.record_reply_attempt(
            conversation_id=f"history-{index}",
            conversation_title="History",
            trigger_message_id=f"message-{index}",
            trigger_sender="Derek",
            trigger_text=f"History event {index}",
            action="chat_message",
            sensitivity_kind="",
            codex_reason="test",
            draft_reply_text="done",
            send_status="sent",
        )

    total, rows = store.list_operation_logs_with_count(
        limit=2,
        source_tables=("reply_attempts",),
    )

    assert total == 3
    assert len(rows) == 2
    assert all(row.source_table == "reply_attempts" for row in rows)


def test_operation_logs_project_recovered_failures_without_losing_history(tmp_path: Path):
    store = _store(tmp_path)
    failed_id = store.record_reply_attempt(
        conversation_id="history-recovered",
        conversation_title="Recovered history",
        trigger_message_id="history-recovered-message",
        trigger_sender="Derek",
        trigger_text="Retry this message",
        action="agent_run",
        sensitivity_kind="",
        codex_reason="temporary failure",
        draft_reply_text="",
        send_status="failed",
    )
    store.update_reply_attempt(failed_id, send_error="temporary provider error")
    store.record_reply_attempt(
        conversation_id="history-recovered",
        conversation_title="Recovered history",
        trigger_message_id="history-recovered-message",
        trigger_sender="Derek",
        trigger_text="Retry this message",
        action="send_reply",
        sensitivity_kind="",
        codex_reason="recovered",
        draft_reply_text="done",
        send_status="sent",
    )

    recovered = store.list_operation_logs(
        statuses=("recovered",), source_tables=("reply_attempts",)
    )
    failed = store.list_operation_logs(
        statuses=("failed",), source_tables=("reply_attempts",)
    )

    assert [row.source_id for row in recovered] == [failed_id]
    assert recovered[0].detail == "temporary provider error"
    assert failed == []


def test_operation_logs_carry_the_outcome_a_reply_attempt_reached(tmp_path: Path):
    """A history row must say what the run did, not repeat what triggered it.

    A skipped run produces no reply, so a row that carries only the trigger text
    reads as though the service answered with the message it was given.
    """
    store = _store(tmp_path)
    skipped_id = store.record_reply_attempt(
        conversation_id="history-outcome",
        conversation_title="磊哥",
        trigger_message_id="history-outcome-message",
        trigger_sender="磊哥",
        trigger_text="【会议跟进】销售周会",
        action="agent_run",
        sensitivity_kind="general",
        send_status="skipped",
    )
    store.update_reply_attempt(
        skipped_id,
        audit_summary="No action: the follow-up asks nothing of Derek.",
    )
    sent_id = store.record_reply_attempt(
        conversation_id="history-outcome",
        conversation_title="磊哥",
        trigger_message_id="history-outcome-message-2",
        trigger_sender="磊哥",
        trigger_text="这条要回复吗？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )
    store.update_reply_attempt(
        sent_id,
        audit_summary="Replied with the agreed scope.",
        final_reply_text="先按灰度推进。",
    )

    rows = {
        row.source_id: row
        for row in store.list_operation_logs(source_tables=("reply_attempts",))
    }

    assert rows[skipped_id].detail == "No action: the follow-up asks nothing of Derek."
    assert rows[sent_id].detail == "先按灰度推进。"


def test_operation_logs_project_recovered_meeting_failures(tmp_path: Path):
    store = _store(tmp_path)
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="history-recovered-meeting",
        title="Recovered meeting",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-09-01T00:00:00Z",
        eligible_at="2026-09-01T00:10:00Z",
        status="pending",
    )
    failed_id = store.create_professional_run(job_id, session_id="meeting-failed", status="failed", error="provider unavailable")
    store.update_meeting_alignment_job(job_id, status="sent")

    recovered = store.list_operation_logs(
        statuses=("recovered",), source_tables=("meeting_alignment_runs",)
    )
    failed = store.list_operation_logs(
        statuses=("failed",), source_tables=("meeting_alignment_runs",)
    )

    assert [row.source_id for row in recovered] == [failed_id]
    assert failed == []


def test_read_connections_use_bounded_cache_without_mmap_for_shared_wal(tmp_path: Path):
    store = _store(tmp_path)

    with store._connect() as db:
        cache_size = int(db.execute("pragma cache_size").fetchone()[0])
        mmap_size = int(db.execute("pragma mmap_size").fetchone()[0])

    assert cache_size <= -32768
    assert mmap_size == 0
