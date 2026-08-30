import json

from app.store import AutoReplyStore
from app.task_owner_backfill import backfill_todo_owner_ids_from_follow_ups


def test_backfill_todo_owner_ids_dry_run_reports_unique_follow_up_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="Owner backfill")
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐 owner id",
        owner_name="Mina",
        status="open",
        priority="P1",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="mina-user-1",
        owner_name="Mina",
        target_kind="direct",
        question_text="请同步进展。",
        status="sent",
    )

    result = backfill_todo_owner_ids_from_follow_ups(store, dry_run=True)

    todo = store.get_work_todo(todo_id)
    assert result.changed == 0
    assert result.planned == 1
    assert result.items[0].todo_id == todo_id
    assert result.items[0].owner_user_id == "mina-user-1"
    assert result.items[0].follow_up_ids == [follow_up_id]
    assert todo is not None
    assert todo.owner_user_id == ""


def test_backfill_todo_owner_ids_apply_writes_owner_and_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="Owner backfill")
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐 owner id",
        owner_name="Mina",
        status="open",
        priority="P1",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="mina-user-1",
        owner_name="邹婧玮(Mina 邹)",
        target_kind="direct",
        question_text="请同步进展。",
        status="sent",
    )

    result = backfill_todo_owner_ids_from_follow_ups(
        store,
        dry_run=False,
        now="2026-08-29 16:10:00",
    )

    todo = store.get_work_todo(todo_id)
    updates = store.list_work_updates(project_id=project_id)
    assert result.changed == 1
    assert todo is not None
    assert todo.owner_user_id == "mina-user-1"
    assert todo.owner_name == "Mina"
    evidence = json.loads(todo.owner_evidence_json)
    assert evidence["user_id"] == "mina-user-1"
    assert evidence["name"] == "Mina"
    assert evidence["source"] == f"follow_up_drafts:{follow_up_id}"
    assert evidence["created_at"] == "2026-08-29 16:10:00"
    assert updates[0].source_type == "todo_owner_backfill"
    assert updates[0].source_ref == str(todo_id)


def test_backfill_todo_owner_ids_skips_conflicting_follow_up_owners(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(title="Owner backfill")
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐 owner id",
        owner_name="Mina",
        status="open",
        priority="P1",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="mina-user-1",
        owner_name="Mina",
        target_kind="direct",
        question_text="请同步进展。",
        status="sent",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="alex-user-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步进展。",
        status="sent",
    )

    result = backfill_todo_owner_ids_from_follow_ups(store, dry_run=False)

    todo = store.get_work_todo(todo_id)
    assert result.changed == 0
    assert result.planned == 0
    assert result.items[0].skipped_reason == "conflicting follow-up owner_user_id"
    assert todo is not None
    assert todo.owner_user_id == ""
