from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from pathlib import Path
from threading import Barrier

import pytest

from app.store import AgentRole, AutoReplyStore
from app.task_models import WorkItem


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


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


def test_existing_database_adds_agent_runs_without_rewriting_reply_tasks(
    tmp_path: Path,
):
    db_path = tmp_path / "task.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            create table reply_tasks (
                id integer primary key autoincrement,
                channel text not null default 'dingtalk',
                conversation_id text not null,
                conversation_title text not null,
                single_chat integer not null,
                trigger_message_id text not null,
                trigger_create_time text not null,
                trigger_sender text not null,
                trigger_text text not null,
                trigger_message_json text not null default '{}',
                available_at text not null default '',
                force_new_decision integer not null default 0,
                oa_url text not null default '',
                manual_rerun_attempt_id integer not null default 0,
                execution_generation text not null default 'initial',
                status text not null default 'pending',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                unique(channel, conversation_id, trigger_message_id)
            )
            """
        )
        db.execute(
            """
            insert into reply_tasks (
                conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text
            ) values ('cid-existing', 'Existing', 0, 'msg-existing',
                      '2026-07-29 00:00:00', 'Derek', 'existing task')
            """
        )

    store = AutoReplyStore(db_path)
    original = store.get_reply_task(1)
    claim = _claim_audit_run(store, 1, "initial", owner="worker-1")

    assert original is not None
    assert original.trigger_text == "existing task"
    assert claim.run.reply_task_id == 1
    with sqlite3.connect(db_path) as db:
        assert db.execute("pragma foreign_key_check").fetchall() == []
        indexes = {
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            ).fetchall()
        }
    assert "idx_agent_runs_status" in indexes


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
                """
                select count(*) from reply_tasks
                where channel='email'
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
            before insert on reply_tasks
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


def test_channel_identity_migration_accepts_quoted_index_names(tmp_path: Path):
    db_path = tmp_path / "task.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table reply_tasks (
                id integer primary key autoincrement,
                conversation_id text not null,
                conversation_title text not null,
                single_chat integer not null,
                trigger_message_id text not null,
                trigger_create_time text not null,
                trigger_sender text not null,
                trigger_text text not null,
                status text not null default 'pending',
                attempts integer not null default 0,
                locked_at text,
                error text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp
            );
            create unique index "legacy user's trigger"
                on reply_tasks(conversation_id, trigger_message_id);
            insert into reply_tasks (
                conversation_id, conversation_title, single_chat,
                trigger_message_id, trigger_create_time, trigger_sender,
                trigger_text
            ) values (
                'cid-quoted', 'Quoted', 0, 'msg-quoted',
                '2026-07-29 00:00:00', 'Derek', 'quoted index'
            );
            """
        )

    store = AutoReplyStore(db_path)

    task = store.get_reply_task(1)
    assert task is not None
    assert task.channel == "dingtalk"
    assert task.trigger_message_id == "msg-quoted"


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


def test_todo_evidence_candidate_dedupes_and_marks_decision(tmp_path: Path):
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
        title="确认客户验收完成",
        status="open",
        priority="P1",
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
    assert logs[0].context == f"project #{project_id} todo #{todo_id}"


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
            "update work_summary_inputs set updated_at=datetime('now', '-31 minutes') where id=?",
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
        description="确认售前知识库里每份材料的来源链接、使用场景和缺口 owner。",
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
    assert todos[0].description == "确认售前知识库里每份材料的来源链接、使用场景和缺口 owner。"
    store.update_work_todo(
        todo_id,
        description="补齐来源链接，并写清每份材料用于哪个客户场景。",
    )
    updated_todo = store.get_work_todo(todo_id)
    assert updated_todo is not None
    assert updated_todo.description == "补齐来源链接，并写清每份材料用于哪个客户场景。"

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


def test_list_follow_up_drafts_due_before_handles_iso_timezone(tmp_path: Path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="宝马项目周末攻坚与客户Demo推进",
        category="sales",
        status="active",
        priority="P0",
        risk_level="high",
    )
    due_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_name="Claire Huang",
        target_kind="direct",
        question_text="准备宝马专家邀请材料了吗？",
        scheduled_at="2026-07-22T10:00:00+08:00",
        status="draft",
    )
    future_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_name="Claire Huang",
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


def test_create_follow_up_draft_dedupes_skipped_terminal_draft(tmp_path: Path):
    store = _store(tmp_path)

    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        memory_context_json='{"query":"existing"}',
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐售前材料来源链接",
        owner_user_id="owner-1",
        owner_name="Alex",
        priority="P1",
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


def test_list_sent_todo_records_combines_dingtalk_todos_and_followups(
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
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        description="确认交付验收时间和阻塞。",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    store.create_work_todo_dingtalk_link(
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
    store.create_work_todo_dingtalk_link(
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


def test_list_work_todo_dingtalk_links_for_todo_returns_all_matches(
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
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    other_todo_id = store.create_work_todo(
        project_id=project_id,
        title="同步其他事项",
        owner_user_id="owner-2",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    link_ids = [
        store.create_work_todo_dingtalk_link(
            work_todo_id=todo_id,
            dingtalk_task_id=f"dt-target-{index}",
            status="failed",
        )
        for index in range(101)
    ]
    store.create_work_todo_dingtalk_link(
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
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    first_todo_id = store.create_work_todo(
        project_id=project_id,
        title="第一项",
        status="open",
        priority="P1",
    )
    second_todo_id = store.create_work_todo(
        project_id=project_id,
        title="第二项",
        status="open",
        priority="P1",
    )
    other_todo_id = store.create_work_todo(
        project_id=project_id,
        title="第三项",
        status="open",
        priority="P1",
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
        owner_name="Mina",
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
    first_project_id = store.create_work_project(
        title="第一项目",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    second_project_id = store.create_work_project(
        title="第二项目",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    other_project_id = store.create_work_project(
        title="第三项目",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    first_todo_id = store.create_work_todo(
        project_id=first_project_id,
        title="第一项",
        status="open",
        priority="P1",
    )
    second_todo_id = store.create_work_todo(
        project_id=second_project_id,
        title="第二项",
        status="done",
        priority="P1",
    )
    store.create_work_todo(
        project_id=other_project_id,
        title="第三项",
        status="open",
        priority="P1",
    )

    grouped = store.list_work_todos_for_projects([first_project_id, second_project_id])

    assert [todo.id for todo in grouped[first_project_id]] == [first_todo_id]
    assert [todo.id for todo in grouped[second_project_id]] == [second_todo_id]
    assert other_project_id not in grouped


def test_list_work_project_ids_for_todo_owner_filters_active_projects(
    tmp_path: Path,
):
    store = _store(tmp_path)
    active_project_id = store.create_work_project(
        title="技术部招聘",
        category="recruiting",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    archived_project_id = store.create_work_project(
        title="历史招聘",
        category="recruiting",
        status="archived",
        priority="P2",
        risk_level="low",
    )
    store.create_work_todo(
        project_id=active_project_id,
        title="评估 Colin",
        owner_user_id="owner-1",
        owner_name="Mina",
        priority="P1",
    )
    store.create_work_todo(
        project_id=archived_project_id,
        title="归档候选人",
        owner_user_id="owner-1",
        owner_name="Mina",
        priority="P2",
    )

    assert store.list_work_project_ids_for_todo_owner("owner-1") == {
        active_project_id
    }


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
