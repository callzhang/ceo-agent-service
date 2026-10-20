import json
from pathlib import Path

from app.action_followup_sync_definitions import SYNC_ACTION_PROJECTIONS
from app.dws_client import DwsError
from app.store import AutoReplyStore
from tests.support.legacy_todo_facts import seed_legacy_todo_link
from app.todo_sync import (
    dispatch_claimed_business_task_todo_sync_outbox,
    maybe_create_dingtalk_todo,
    reconcile_unknown_business_task_todo_creates,
    scan_completed_dingtalk_todos,
    sync_completed_task_to_dingtalk,
)


class FakeTodoDws:
    def __init__(self):
        self.created = []
        self.create_payload = {"todoTaskId": "dt-task-1"}
        self.create_error = None
        self.get_calls = []
        self.get_payloads = {}
        self.get_errors = {}
        self.done_calls = []
        self.done_error = None
        self.completed_task_ids = []
        self.list_calls = []

    def create_todo_task(
        self,
        *,
        title,
        executor_user_id,
        due,
        priority,
        description="",
        tags=None,
        participants=None,
        files=None,
    ):
        self.created.append(
            {
                "title": title,
                "executor_user_id": executor_user_id,
                "due": due,
                "priority": priority,
                "description": description,
                "tags": tags or [],
                "participants": participants or [],
                "files": files or [],
            }
        )
        if self.create_error is not None:
            raise self.create_error
        return self.create_payload

    def get_todo_task(self, task_id):
        self.get_calls.append(task_id)
        error = self.get_errors.get(task_id)
        if error is not None:
            raise error
        return self.get_payloads.get(task_id, {"id": task_id, "done": False})

    def list_completed_todo_tasks(self, *, page=1, size=20):
        self.list_calls.append({"page": page, "size": size})
        start = (page - 1) * size
        cards = [
            {"taskId": task_id, "subject": f"todo {task_id}"}
            for task_id in self.completed_task_ids[start:start + size]
        ]
        return {
            "result": {"hasMore": start + size < len(self.completed_task_ids), "todoCards": cards},
            "success": True,
        }

    def mark_todo_task_done(self, task_id, *, done=True):
        if self.done_error is not None:
            raise self.done_error
        self.done_calls.append({"task_id": task_id, "done": done})
        return {"id": task_id, "done": done}


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def _project_and_todo(store: AutoReplyStore, **todo_values):
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        tags_json=json.dumps(["客户交付"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        background="客户交付项目需要持续确认验收安排。",
        related_people_json=json.dumps(
            [{"user_id": "owner-1", "name": "Alex", "role": "owner"}],
            ensure_ascii=False,
        ),
    )
    defaults = {
        "project_id": project_id,
        "title": "给客户同步验收 ETA",
        "owner_user_id": "owner-1",
        "owner_name": "Alex",
        "status": "open",
        "priority": "P1",
        "deadline_at": "2026-07-01 18:00:00",
    }
    defaults.update(todo_values)
    return project_id, store.create_work_todo(**defaults)


def _formal_business_task(store: AutoReplyStore, **values) -> int:
    defaults = {
        "title": "给客户同步验收 ETA",
        "description": "来源：客户要求本周确认验收安排。",
        "stage": "formal",
        "formal_basis": "explicit_assignment",
        "commitment_status": "accepted",
        "owner_user_id": "owner-1",
        "owner_name": "Alex",
        "deadline_at": "2026-07-01 18:00:00",
    }
    defaults.update(values)
    task_id = store.create_business_task(**defaults)
    with store._immediate_write_transaction() as db:
        signal_id = store.create_business_task_signal_in_transaction(
            source_type="dingtalk_message", source_ref=f"task:{task_id}:commitment",
            evidence_text="Alex confirmed the committed deadline.",
            dedupe_key=f"task:{task_id}:commitment", _db=db,
        )
        store.create_business_task_date_evidence_in_transaction(
            task_id=task_id, source_signal_id=signal_id,
            date_type="committed_deadline_at", value_at="2026-07-01 18:00:00",
            raw_phrase="2026-07-01 18:00", actor_kind="human",
            actor_user_id="owner-1", actor_name="Alex", _db=db,
        )
    return task_id


def test_maybe_create_dingtalk_todo_mirrors_eligible_standalone_business_task(tmp_path):
    store = _store(tmp_path)
    business_task_id = _formal_business_task(store)
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        business_task_id=business_task_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    assert link["business_task_id"] == business_task_id
    assert link["dingtalk_task_id"] == "dt-task-1"
    assert len(dws.created) == 1


def test_maybe_create_dingtalk_todo_retains_unaccepted_task_without_mirror(tmp_path):
    store = _store(tmp_path)
    business_task_id = _formal_business_task(
        store, commitment_status="assigned_unaccepted"
    )

    link = maybe_create_dingtalk_todo(
        store,
        FakeTodoDws(),
        business_task_id=business_task_id,
        now="2026-06-27 10:00:00",
    )

    assert link is None
    assert store.get_business_task(business_task_id) is not None


def test_business_task_allows_only_one_active_external_todo_link(tmp_path):
    store = _store(tmp_path)
    task_id = _formal_business_task(store)

    first = store.create_business_task_dingtalk_link(
        business_task_id=task_id, status="creating"
    )
    second = store.create_business_task_dingtalk_link(
        business_task_id=task_id, status="creating"
    )

    assert second == first
    with store._connect() as db:
        assert db.execute(
            f"select count(*) from ({SYNC_ACTION_PROJECTIONS['business_task_dingtalk_links']}) "
            "where business_task_id=? "
            "and status in ('creating','active')", (task_id,)
        ).fetchone()[0] == 1


def test_business_task_create_readback_failure_preserves_external_id_without_duplicate(tmp_path):
    store = _store(tmp_path)
    task_id = _formal_business_task(store)
    dws = FakeTodoDws()
    dws.get_errors["dt-task-1"] = DwsError("readback unavailable")

    first = maybe_create_dingtalk_todo(
        store, dws, business_task_id=task_id, now="2026-06-27 10:00:00"
    )
    assert first["dingtalk_task_id"] == "dt-task-1"
    assert first["status"] == "creating"
    dws.get_errors.clear()
    second = maybe_create_dingtalk_todo(
        store, dws, business_task_id=task_id, now="2026-06-27 10:05:00"
    )

    assert second["status"] == "active"
    assert len(dws.created) == 1


def test_unknown_business_task_create_with_known_id_settles_after_provider_readback(tmp_path):
    store = _store(tmp_path)
    task_id = _formal_business_task(store)
    store.enqueue_business_task_todo_sync_outbox(
        operation_key=f"business-task:{task_id}:create",
        business_task_id=task_id, operation="create",
    )
    claimed = store.claim_business_task_todo_sync_outbox(
        owner="worker-a", now="2026-06-27 10:00:00"
    )
    assert claimed is not None
    dws = FakeTodoDws()
    dws.get_errors["dt-task-1"] = DwsError("readback unavailable")

    assert dispatch_claimed_business_task_todo_sync_outbox(
        store, dws, item=store.get_business_task_todo_sync_outbox(claimed["id"]),
        owner="worker-a", now="2026-06-27 10:00:00",
    ) == "unknown"
    assert store.get_business_task_todo_sync_outbox(claimed["id"])["status"] == "unknown"
    dws.get_errors.clear()

    assert reconcile_unknown_business_task_todo_creates(store, dws) == 1

    link = store.get_active_business_task_dingtalk_link(task_id)
    assert link is not None and link["status"] == "active"
    settled = store.get_business_task_todo_sync_outbox(claimed["id"])
    assert settled["status"] == "completed"
    assert json.loads(settled["receipt_json"])["dingtalk_task_id"] == "dt-task-1"
    assert len(dws.created) == 1


def test_unknown_business_task_create_without_receipt_blocks_new_create_operation(tmp_path):
    store = _store(tmp_path)
    task_id = _formal_business_task(store)
    store.enqueue_business_task_todo_sync_outbox(
        operation_key=f"task-agent:1:business-task:{task_id}:create",
        business_task_id=task_id, operation="create",
    )
    claimed = store.claim_business_task_todo_sync_outbox(
        owner="worker-a", now="2026-06-27 10:00:00"
    )
    assert claimed is not None
    dws = FakeTodoDws()
    dws.create_error = DwsError("provider result unknown")
    assert dispatch_claimed_business_task_todo_sync_outbox(
        store, dws, item=store.get_business_task_todo_sync_outbox(claimed["id"]),
        owner="worker-a", now="2026-06-27 10:00:00",
    ) == "unknown"

    store.enqueue_business_task_todo_sync_outbox(
        operation_key=f"task-agent:2:business-task:{task_id}:create",
        business_task_id=task_id, operation="create",
    )

    rows = store.list_business_task_todo_sync_outbox()
    assert len(rows) == 1
    assert rows[0]["status"] == "unknown"
    assert len(dws.created) == 1


def test_business_task_outbox_idempotency_key_is_task_keyed(tmp_path):
    store = _store(tmp_path)
    business_task_id = _formal_business_task(store)

    store.enqueue_business_task_todo_sync_outbox(
        operation_key=f"task-agent:1:business-task:{business_task_id}:create",
        business_task_id=business_task_id,
        operation="create",
    )
    store.enqueue_business_task_todo_sync_outbox(
        operation_key=f"task-agent:1:business-task:{business_task_id}:create",
        business_task_id=business_task_id,
        operation="create",
    )

    with store._connect() as db:
        from app.action_followup_sync_definitions import SYNC_ACTION_PROJECTIONS

        rows = db.execute(
            f"select * from ({SYNC_ACTION_PROJECTIONS['business_task_todo_sync_outbox']})"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["business_task_id"] == business_task_id


def test_business_task_outbox_expired_claim_becomes_unknown_without_replay(tmp_path):
    store = _store(tmp_path)
    business_task_id = _formal_business_task(store)
    store.enqueue_business_task_todo_sync_outbox(
        operation_key=f"business-task:{business_task_id}:create",
        business_task_id=business_task_id,
        operation="create",
    )
    assert store.claim_business_task_todo_sync_outbox(
        owner="worker-a", now="2026-06-27 10:00:00", lease_seconds=1
    ) is not None

    assert store.claim_business_task_todo_sync_outbox(
        owner="worker-b", now="2026-06-27 10:01:00"
    ) is None
    [unknown] = store.list_business_task_todo_sync_outbox(statuses=("unknown",))
    assert unknown["error"] == "receipt_reconciliation_required"


def test_business_task_outbox_dispatch_creates_receipted_mirror(tmp_path):
    store = _store(tmp_path)
    task_id = _formal_business_task(store)
    store.enqueue_business_task_todo_sync_outbox(
        operation_key=f"business-task:{task_id}:create",
        business_task_id=task_id,
        operation="create",
    )
    item = store.claim_business_task_todo_sync_outbox(
        owner="worker-a", now="2026-06-27 10:00:00"
    )
    assert item is not None
    item = store.get_business_task_todo_sync_outbox(item["id"])
    assert item is not None
    dws = FakeTodoDws()

    result = dispatch_claimed_business_task_todo_sync_outbox(
        store, dws, item=item, owner="worker-a", now="2026-06-27 10:00:00"
    )

    assert result == "completed"
    saved = store.get_business_task_todo_sync_outbox(item["id"])
    assert saved["status"] == "completed"
    assert json.loads(saved["receipt_json"])["dingtalk_task_id"] == "dt-task-1"
    assert len(dws.created) == 1


def test_completed_business_task_sync_marks_linked_external_todo_done(tmp_path):
    store = _store(tmp_path)
    task_id = _formal_business_task(store)
    dws = FakeTodoDws()
    maybe_create_dingtalk_todo(
        store, dws, business_task_id=task_id, now="2026-06-27 10:00:00"
    )

    result = sync_completed_task_to_dingtalk(
        store, dws, business_task_id=task_id,
        evidence={"source": "message:1", "reason": "owner confirmed completion"},
        now="2026-06-28 10:00:00",
    )

    assert result == "completed"
    assert dws.done_calls == [{"task_id": "dt-task-1", "done": True}]
    link = store.get_active_business_task_dingtalk_link(task_id)
    assert link is None


def test_completion_scan_closes_only_the_listed_business_task(tmp_path):
    store = _store(tmp_path)
    first = _formal_business_task(store)
    sibling = _formal_business_task(store, title="Separate quote")
    store.create_business_task_dingtalk_link(
        business_task_id=first, dingtalk_task_id="dt-task-1", status="active"
    )
    dws = FakeTodoDws()
    dws.completed_task_ids = ["dt-unrelated", "dt-task-1"]

    closed = scan_completed_dingtalk_todos(
        store, dws, now="2026-06-29 01:00:00"
    )

    assert closed == 1
    assert store.get_business_task(first).status.value == "done"
    assert store.get_business_task(sibling).status.value == "open"
    assert store.list_business_task_dingtalk_links(business_task_id=first)[0]["status"] == "done"


def test_completion_scan_keeps_legacy_rows_history_only(tmp_path):
    store = _store(tmp_path)
    _, legacy_todo_id = _project_and_todo(store)
    legacy_link_id = seed_legacy_todo_link(store,
        work_todo_id=legacy_todo_id,
        dingtalk_task_id="legacy-dt-task",
        executor_user_id="owner-1",
        status="active",
    )
    business_task_id = _formal_business_task(store)
    store.create_business_task_dingtalk_link(
        business_task_id=business_task_id,
        dingtalk_task_id="business-dt-task",
        status="active",
    )
    with store._connect() as db:
        legacy_before = {
            "business_legacy": [
                tuple(row)
                for row in db.execute(
                    "select * from business where record_type in ('work_project','work_todo') "
                    "order by id"
                )
            ],
            "work_updates": [
                tuple(row) for row in db.execute(
                    f"select * from ({SYNC_ACTION_PROJECTIONS['work_updates']}) order by id"
                )
            ],
            "work_todo_dingtalk_links": [
                tuple(row)
                for row in db.execute(
                    f"select * from ({SYNC_ACTION_PROJECTIONS['work_todo_dingtalk_links']}) order by id"
                )
            ],
        }
    dws = FakeTodoDws()
    dws.completed_task_ids = ["legacy-dt-task", "business-dt-task"]

    assert scan_completed_dingtalk_todos(store, dws, now="2026-06-29 01:00:00") == 1

    with store._connect() as db:
        legacy_after = {
            "business_legacy": [
                tuple(row)
                for row in db.execute(
                    "select * from business where record_type in ('work_project','work_todo') "
                    "order by id"
                )
            ],
            "work_updates": [
                tuple(row) for row in db.execute(
                    f"select * from ({SYNC_ACTION_PROJECTIONS['work_updates']}) order by id"
                )
            ],
            "work_todo_dingtalk_links": [
                tuple(row)
                for row in db.execute(
                    f"select * from ({SYNC_ACTION_PROJECTIONS['work_todo_dingtalk_links']}) order by id"
                )
            ],
        }
    assert legacy_after == legacy_before
    assert store.get_work_todo(legacy_todo_id).status.value == "open"
    assert store.get_work_todo_dingtalk_link(legacy_link_id).status == "active"
    assert store.get_business_task(business_task_id).status.value == "done"
    assert dws.list_calls == [{"page": 1, "size": 20}]


def test_completion_scan_keeps_legacy_todo_history_only(tmp_path):
    store = _store(tmp_path)
    project_id, todo_id = _project_and_todo(store)
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请确认验收 ETA。",
        scheduled_at="2026-06-27 09:00:00",
        status="sent",
        sent_at="2026-06-27 09:05:00",
    )
    link_id = seed_legacy_todo_link(store,
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.completed_task_ids = ["dt-task-1"]

    updated = scan_completed_dingtalk_todos(
        store,
        dws,
        now="2026-06-27 11:00:00",
    )

    assert updated == 0
    assert dws.get_calls == []
    todo = store.get_work_todo(todo_id)
    assert todo.status == "open"
    assert todo.completion_evidence_json == "{}"
    assert store.get_work_todo_dingtalk_link(link_id).status == "active"
    follow_up = store.get_follow_up_draft(follow_up_id)
    assert follow_up is not None
    assert follow_up.status == "sent"
    assert follow_up.evidence_check_json == "{}"


def test_completion_scan_does_not_read_legacy_links(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = seed_legacy_todo_link(store,
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.completed_task_ids = ["dt-task-1"]

    updated = scan_completed_dingtalk_todos(
        store,
        dws,
        now="2026-06-27 11:00:00",
    )

    assert updated == 0
    assert store.get_work_todo(todo_id).status == "open"
    assert store.get_work_todo_dingtalk_link(link_id).status == "active"
    assert dws.list_calls == []


def test_completion_scan_leaves_open_todo_that_dingtalk_does_not_list(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = seed_legacy_todo_link(store,
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.completed_task_ids = ["dt-other-1"]

    assert scan_completed_dingtalk_todos(store, dws, now="2026-06-27 11:00:00") == 0

    assert store.get_work_todo(todo_id).status != "done"
    assert store.get_work_todo_dingtalk_link(link_id).status == "active"
    assert dws.list_calls == []


def test_completion_scan_skips_cancelled_backfill_link(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(
        store,
        status="cancelled",
        blocker="routine HR offer-flow step",
    )
    link_id = seed_legacy_todo_link(store,
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="cancelled",
        last_error=(
            "Internal TODO cancelled as routine process; external "
            "cancellation is not part of this change."
        ),
    )
    dws = FakeTodoDws()
    dws.completed_task_ids = ["dt-task-1"]

    updated = scan_completed_dingtalk_todos(
        store,
        dws,
        now="2026-06-27 11:00:00",
    )

    todo = store.get_work_todo(todo_id)
    link = store.get_work_todo_dingtalk_link(link_id)

    assert updated == 0
    assert dws.list_calls == []
    assert todo.status == "cancelled"
    assert todo.blocker == "routine HR offer-flow step"
    assert link.status == "cancelled"
    assert "external cancellation is not part of this change" in link.last_error
