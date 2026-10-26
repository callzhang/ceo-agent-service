import inspect
import json

import pytest

from app import todo_completion, todo_sync
from app.dispatcher import adapters
from app.store import AutoReplyStore
from app.web_api.tasks import sent_todo_payload
from tests.support.legacy_todo_facts import seed_legacy_todo_link, seed_legacy_todo_outbox, seed_legacy_work_project, seed_legacy_work_todo


def test_legacy_todo_operational_api_is_retired():
    assert "work_todo_id" not in inspect.signature(todo_sync.maybe_create_dingtalk_todo).parameters
    assert not hasattr(adapters, "TaskTodoSyncOutboxQueueAdapter")
    for name in (
        "retry_failed_dingtalk_todo_links", "sync_completed_todo_to_dingtalk",
        "dispatch_task_todo_sync_outbox", "dispatch_claimed_task_todo_sync_outbox",
    ):
        assert not hasattr(todo_sync, name), name
    for name in ("close_todo_with_completion_evidence", "complete_follow_ups_for_todo"):
        assert not hasattr(todo_completion, name), name
    for name in (
        "create_work_project", "update_work_project", "create_work_todo",
        "update_work_todo", "create_work_update",
        "create_work_todo_dingtalk_link", "update_work_todo_dingtalk_link",
        "enqueue_task_todo_sync_outbox", "claim_task_todo_sync_outbox",
        "finish_task_todo_sync_outbox", "retry_task_todo_sync_outbox",
        "reconcile_unknown_task_todo_sync_outbox",
        "complete_unknown_task_todo_sync_outbox_from_receipt",
    ):
        assert not hasattr(AutoReplyStore, name), name


def test_recorded_active_legacy_todo_collision_stays_readable_and_cannot_dispatch(tmp_path):
    store = AutoReplyStore(tmp_path / "history.sqlite3")
    project_id = seed_legacy_work_project(store, title="Historical delivery", status="active")
    legacy_id = seed_legacy_work_todo(store,
        project_id=project_id, title="Original assignment", owner_user_id="old-owner",
        status="open", deadline_at="2026-07-01 18:00:00",
    )
    current_id = store.create_business_task(
        title="Unrelated current task", stage="formal", formal_basis="explicit_assignment",
        owner_user_id="current-owner", commitment_status="accepted",
    )
    assert legacy_id == current_id  # Shared public integers are not an identity mapping.
    provider_json = '{"id":"legacy-provider-id","done":false,"original":"receipt"}'
    link_id = seed_legacy_todo_link(
        store, work_todo_id=legacy_id, dingtalk_task_id="legacy-provider-id",
        executor_user_id="old-owner", title_snapshot="Original provider title",
        status="active", last_dingtalk_done=False,
        last_dingtalk_payload_json=provider_json, last_push_at="2026-07-01 10:00:00",
    )
    outbox_id = seed_legacy_todo_outbox(
        store, operation_key="legacy-source:create", work_todo_id=legacy_id,
        operation="create", status="completed", attempt_count=1,
        evidence_json='{"source":"original-source"}',
        receipt_json=json.dumps({"link_id": link_id, "dingtalk_task_id": "legacy-provider-id"}),
    )
    with store._connect() as db:
        actions_before = [tuple(row) for row in db.execute("select * from actions order by id")]
        business_before = [tuple(row) for row in db.execute("select * from business order by id")]

    class NoProviderCalls:
        def __getattr__(self, name):
            pytest.fail(f"historical Todo caused provider call: {name}")

    assert todo_sync.scan_completed_dingtalk_todos(
        store, NoProviderCalls(), now="2026-10-20 10:00:00"
    ) == 0
    assert todo_sync.reconcile_unknown_business_task_todo_creates(store, NoProviderCalls()) == 0
    assert store.get_business_task(current_id).status.value == "open"
    assert store.list_business_task_dingtalk_links() == []
    assert store.list_business_task_todo_sync_outbox() == []

    recorded = next((link for link in store.list_work_todo_dingtalk_links_for_todos([legacy_id]).get(legacy_id, []) if link.id == link_id), None)
    assert recorded.work_todo_id == legacy_id
    assert recorded.status == "active"
    assert recorded.last_dingtalk_payload_json == provider_json
    assert sorted(store.list_work_todo_dingtalk_links_for_todos([legacy_id]).get(legacy_id, []), key=lambda link: link.id) == [recorded]
    assert store.list_work_todo_dingtalk_links_for_todos([legacy_id]) == {legacy_id: [recorded]}
    [operation] = store.list_task_todo_sync_outbox()
    assert dict(store.get_task_todo_sync_outbox(outbox_id)) == dict(operation)
    assert operation["evidence_json"] == '{"source":"original-source"}'
    assert json.loads(operation["receipt_json"])["dingtalk_task_id"] == "legacy-provider-id"
    [sent] = store.list_sent_todo_records()
    payload = sent_todo_payload(sent)
    assert payload["status"] == "active"
    assert payload["external_id"] == "legacy-provider-id"
    assert payload["original_text"] == "Original provider title"
    assert payload["detail_url"] == f"/tasks/legacy-project/{project_id}#todo-{legacy_id}"
    with store._connect() as db:
        assert [tuple(row) for row in db.execute("select * from actions order by id")] == actions_before
        assert [tuple(row) for row in db.execute("select * from business order by id")] == business_before
