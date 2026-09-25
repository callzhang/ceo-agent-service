import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import app.todo_sync as todo_sync
from app.dispatcher.models import ClaimGuard
from app.dws_client import DwsError
from app.store import AutoReplyStore
from app.todo_sync import (
    dispatch_claimed_business_task_todo_sync_outbox,
    dispatch_task_todo_sync_outbox,
    maybe_create_dingtalk_todo,
    reconcile_unknown_business_task_todo_creates,
    scan_completed_dingtalk_todos,
    retry_failed_dingtalk_todo_links,
    sync_completed_todo_to_dingtalk,
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
            "select count(*) from business_task_dingtalk_links where business_task_id=? "
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
        rows = db.execute("select * from business_task_todo_sync_outbox").fetchall()
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


def test_task_todo_outbox_expired_delivery_becomes_unknown_without_replay(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    store.enqueue_task_todo_sync_outbox(
        operation_key="task-agent:1:todo:1:create",
        work_todo_id=todo_id,
        operation="create",
    )
    claimed = store.claim_task_todo_sync_outbox(
        owner="worker-a", now="2026-06-27 10:00:00", lease_seconds=1
    )
    assert claimed is not None

    assert store.claim_task_todo_sync_outbox(
        owner="worker-b", now="2026-06-27 10:01:00"
    ) is None
    unknown = store.list_task_todo_sync_outbox(statuses=("unknown",))
    assert len(unknown) == 1
    assert unknown[0]["error"] == "receipt_reconciliation_required"


def test_task_todo_outbox_claim_allows_one_sender(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    store.enqueue_task_todo_sync_outbox(
        operation_key="task-agent:1:todo:1:create",
        work_todo_id=todo_id,
        operation="create",
    )

    first = store.claim_task_todo_sync_outbox(owner="worker-a", now="2026-06-27 10:00:00")
    second = store.claim_task_todo_sync_outbox(owner="worker-b", now="2026-06-27 10:00:00")

    assert first is not None
    assert second is None


def test_claimed_task_todo_outbox_delivery_does_not_scan_or_reclaim(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    store.enqueue_task_todo_sync_outbox(
        operation_key="task-agent:1:todo:1:create",
        work_todo_id=todo_id,
        operation="create",
    )
    now = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)
    adapter_type = getattr(
        __import__("app.dispatcher.adapters", fromlist=["TaskTodoSyncOutboxQueueAdapter"]),
        "TaskTodoSyncOutboxQueueAdapter",
    )
    adapter = adapter_type(store)
    envelope = adapter.claim(
        now,
        owner="dispatcher-a",
        owner_pid=101,
        lease=timedelta(minutes=5),
    )
    assert envelope is not None
    item = store.list_task_todo_sync_outbox(statuses=("running",))[0]
    monkeypatch.setattr(
        store,
        "claim_task_todo_sync_outbox",
        lambda **_kwargs: pytest.fail("claimed delivery must not scan the queue"),
    )
    guard = ClaimGuard(adapter=adapter, envelope=envelope, owner="dispatcher-a")
    dws = FakeTodoDws()

    status = todo_sync.dispatch_claimed_task_todo_sync_outbox(
        store,
        dws,
        item=item,
        owner="dispatcher-a",
        now="2026-06-27 10:00:00",
        claim_guard=guard,
    )

    assert status == "completed"
    assert guard.resolved is True
    assert len(dws.created) == 1
    assert len(store.list_task_todo_sync_outbox(statuses=("completed",))) == 1


def test_claimed_task_todo_outbox_terminal_write_is_fenced_by_generation(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    store.enqueue_task_todo_sync_outbox(
        operation_key="task-agent:1:todo:1:create",
        work_todo_id=todo_id,
        operation="create",
    )
    now = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)
    adapter_type = getattr(
        __import__("app.dispatcher.adapters", fromlist=["TaskTodoSyncOutboxQueueAdapter"]),
        "TaskTodoSyncOutboxQueueAdapter",
    )
    adapter = adapter_type(store)
    envelope = adapter.claim(
        now,
        owner="dispatcher-a",
        owner_pid=101,
        lease=timedelta(minutes=5),
    )
    assert envelope is not None
    item = store.get_task_todo_sync_outbox(int(envelope.source_id))
    assert item is not None

    class ClaimLosingDws(FakeTodoDws):
        def create_todo_task(self, **kwargs):
            result = super().create_todo_task(**kwargs)
            with store._connect() as db:
                db.execute(
                    "update dispatcher_claim_leases set generation=generation+1 "
                    "where adapter_name=? and source_id=?",
                    (envelope.adapter_name, envelope.source_id),
                )
            return result

    guard = ClaimGuard(adapter=adapter, envelope=envelope, owner="dispatcher-a")
    with pytest.raises(ValueError, match="no longer owned"):
        todo_sync.dispatch_claimed_task_todo_sync_outbox(
            store,
            ClaimLosingDws(),
            item=item,
            owner="dispatcher-a",
            now="2026-06-27 10:00:00",
            claim_guard=guard,
        )

    persisted = store.get_task_todo_sync_outbox(int(envelope.source_id))
    assert persisted is not None
    assert persisted["status"] == "running"
    assert persisted["lease_owner"] == "dispatcher-a"


def test_task_todo_outbox_receipt_write_failure_never_blindly_replays(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    store.enqueue_task_todo_sync_outbox(
        operation_key="task-agent:1:todo:1:create",
        work_todo_id=todo_id,
        operation="create",
    )
    dws = FakeTodoDws()
    original_finish = store.finish_task_todo_sync_outbox

    def fail_receipt(**_kwargs):
        raise RuntimeError("receipt database unavailable")

    monkeypatch.setattr(store, "finish_task_todo_sync_outbox", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt database unavailable"):
        dispatch_task_todo_sync_outbox(
            store, dws, owner="worker-a", now="2026-06-27 10:00:00"
        )
    assert len(dws.created) == 1

    monkeypatch.setattr(store, "finish_task_todo_sync_outbox", original_finish)
    assert dispatch_task_todo_sync_outbox(
        store, dws, owner="worker-b", now="2026-06-27 10:10:00"
    ) == 0
    assert len(dws.created) == 1
    assert store.list_task_todo_sync_outbox(statuses=("unknown",))[0]["error"] == (
        "receipt_reconciliation_required"
    )


def test_legacy_task_todo_outbox_migrates_retry_cap_without_status_violation(tmp_path):
    store = _store(tmp_path)
    with store._connect() as db:
        db.execute("drop table task_todo_sync_outbox")
        db.execute(
            """
            create table task_todo_sync_outbox (
                id integer primary key autoincrement,
                operation_key text not null unique,
                work_todo_id integer not null,
                operation text not null check(operation in ('create', 'complete')),
                evidence_json text not null default '{}',
                status text not null default 'queued'
                    check(status in ('queued', 'running', 'completed', 'failed', 'unknown')),
                lease_owner text not null default '', lease_expires_at text not null default '',
                receipt_json text not null default '{}', error text not null default '',
                created_at text not null default current_timestamp, updated_at text not null default current_timestamp,
                completed_at text not null default ''
            )
            """
        )
    store._initialize()
    _, todo_id = _project_and_todo(store)
    store.enqueue_task_todo_sync_outbox(operation_key="legacy", work_todo_id=todo_id, operation="create")
    for now in ("2026-06-27 10:00:00", "2026-06-27 10:02:00", "2026-06-27 10:05:00"):
        item = store.claim_task_todo_sync_outbox(owner="worker", now=now)
        assert item is not None
        store.retry_task_todo_sync_outbox(outbox_id=item["id"], owner="worker", error="no_effect", now=now)
    exhausted = store.list_task_todo_sync_outbox(statuses=("failed",))[0]
    assert exhausted["attempt_count"] == 3 and exhausted["next_attempt_at"] == ""
    store.enqueue_task_todo_sync_outbox(operation_key="later", work_todo_id=todo_id, operation="create")
    assert store.claim_task_todo_sync_outbox(owner="later", now="2026-06-27 10:10:00")["operation_key"] == "later"


def test_maybe_create_dingtalk_todo_creates_high_confidence_link(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    assert dws.created == [
        {
            "title": "给客户同步验收 ETA：项目：客户交付",
            "executor_user_id": "owner-1",
            "due": "2026-07-01T18:00:00+08:00",
            "priority": 30,
            "description": (
                "项目：客户交付\n"
                "项目背景：客户交付项目需要持续确认验收安排。\n"
                "待办：给客户同步验收 ETA\n"
                "截止时间：2026-07-01 18:00:00\n"
                "负责人：Alex\n"
                "优先级：P1"
            ),
            "tags": ["客户交付", "projects", "risk:medium", "P1"],
            "participants": [
                {"user_id": "owner-1", "name": "Alex", "role": "owner"}
            ],
            "files": [],
        }
    ]
    stored = store.get_work_todo_dingtalk_link(link.id)
    assert stored.dingtalk_task_id == "dt-task-1"
    assert stored.status == "active"


def test_maybe_create_dingtalk_todo_uses_project_context_when_description_has_no_source(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(
        store,
        description="确认交付验收时间和阻塞。",
    )
    dws = FakeTodoDws()

    maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert dws.created[0]["title"] == (
        "给客户同步验收 ETA：项目：客户交付；确认交付验收时间和阻塞"
    )


def test_maybe_create_dingtalk_todo_pushes_context_in_external_title(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(
        store,
        title="准备宝马专家邀请材料",
        description=(
            "来源：宝马项目周末攻坚与客户Demo推进；交付：补齐专家背景、"
            "邀请理由、Demo议程和客户确认口径。完成标准：可直接发给客户。"
        ),
    )
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    created_title = dws.created[0]["title"]
    assert created_title.startswith("准备宝马专家邀请材料：来源：宝马项目周末攻坚")
    assert len(created_title) <= 80
    stored = store.get_work_todo_dingtalk_link(link.id)
    assert stored.title_snapshot == created_title


def test_maybe_create_dingtalk_todo_accepts_create_todo_task_id(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()
    dws.create_payload = {"todoTaskId": "dt-task-from-create"}

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    assert link.dingtalk_task_id == "dt-task-from-create"
    assert link.status == "active"
    assert dws.get_calls == ["dt-task-from-create"]


def test_maybe_create_dingtalk_todo_skips_missing_deadline(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store, deadline_at="")
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is None
    assert dws.created == []


@pytest.mark.parametrize(
    "todo_values",
    [
        {"owner_user_id": ""},
        {"completion_evidence_json": json.dumps({"source": "reply_attempt:1"})},
        {"status": "done"},
    ],
)
def test_maybe_create_dingtalk_todo_skips_ineligible_todos(
    tmp_path,
    todo_values,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store, **todo_values)
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is None
    assert dws.created == []


def test_maybe_create_dingtalk_todo_does_not_rejudge_agent_business_category(tmp_path):
    store = _store(tmp_path)
    project_id = store.create_work_project(
        title="HR 事项",
        category="HR",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="确认候选人面试安排",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        deadline_at="2026-07-01 18:00:00",
    )
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    assert len(dws.created) == 1
    assert dws.created[0]["executor_user_id"] == "owner-1"


def test_maybe_create_dingtalk_todo_does_not_rejudge_agent_title_semantics(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store, title="跟进一下")
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    assert len(dws.created) == 1


def test_maybe_create_dingtalk_todo_skips_existing_active_link(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-existing",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link.id == link_id
    assert link.dingtalk_task_id == "dt-task-existing"
    assert dws.created == []


def test_maybe_create_dingtalk_todo_keeps_task_id_when_get_fails(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()
    dws.get_errors["dt-task-1"] = DwsError("todo get failed")

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert link is not None
    assert link.status == "failed"
    assert link.dingtalk_task_id == "dt-task-1"
    assert "todo get failed" in link.last_error


def test_maybe_create_dingtalk_todo_recovers_failed_link_without_duplicate_create(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()
    dws.get_errors["dt-task-1"] = DwsError("first get failed")

    first_link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )
    dws.get_errors = {}
    dws.get_payloads["dt-task-1"] = {"id": "dt-task-1", "done": False}

    second_link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:05:00",
    )

    assert len(dws.created) == 1
    assert second_link.id == first_link.id
    assert second_link.dingtalk_task_id == "dt-task-1"
    assert second_link.status == "active"
    stored = store.get_work_todo_dingtalk_link(first_link.id)
    assert stored.dingtalk_task_id == "dt-task-1"
    assert stored.status == "active"
    assert stored.last_error == ""


def test_maybe_create_dingtalk_todo_retries_token_failed_create_link(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="",
        status="failed",
        last_error="code=TOKEN_VERIFIED_FAILED",
    )
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:05:00",
    )

    assert link.id == link_id
    assert len(dws.created) == 1
    assert link.dingtalk_task_id == "dt-task-1"
    assert link.status == "active"
    assert link.last_error == ""
    assert link.retry_count == 1


def test_maybe_create_dingtalk_todo_retries_security_check_failure_once(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="",
        status="failed",
        last_error="code=SECURITY_CHECK_INVOKE_FAILED",
    )
    dws = FakeTodoDws()

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:05:00",
    )

    assert link.id == link_id
    assert link.status == "active"
    assert link.retry_count == 1
    assert len(dws.created) == 1

    retry = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:10:00",
    )

    assert retry.id == link_id
    assert len(dws.created) == 1


def test_retry_failed_dingtalk_todo_links_terminalizes_exhausted_security_failure(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="",
        status="failed",
        last_error="code=SECURITY_CHECK_INVOKE_FAILED",
    )
    dws = FakeTodoDws()
    dws.create_error = DwsError("code=SECURITY_CHECK_INVOKE_FAILED")

    first = retry_failed_dingtalk_todo_links(
        store,
        dws,
        now="2026-06-27 10:05:00",
    )
    second = retry_failed_dingtalk_todo_links(
        store,
        dws,
        now="2026-06-27 10:10:00",
    )

    link = store.get_work_todo_dingtalk_link(link_id)
    assert first == 0
    assert second == 0
    assert link.status == "cancelled"
    assert link.retry_count == 1
    assert link.last_error == "dingtalk_todo_create_retry_exhausted_no_external_task"
    assert len(dws.created) == 1


def test_retry_failed_dingtalk_todo_links_recovers_token_failures(tmp_path):
    store = _store(tmp_path)
    _, existing_todo_id = _project_and_todo(store, title="确认验收 ETA")
    existing_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=existing_todo_id,
        dingtalk_task_id="dt-existing",
        status="failed",
        last_error="code=TOKEN_VERIFIED_FAILED",
    )
    _, create_todo_id = _project_and_todo(store, title="归档验收材料")
    create_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=create_todo_id,
        dingtalk_task_id="",
        status="failed",
        last_error="code=TOKEN_VERIFIED_FAILED",
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-existing"] = {"id": "dt-existing", "done": False}

    recovered = retry_failed_dingtalk_todo_links(
        store,
        dws,
        now="2026-06-27 10:10:00",
    )

    assert recovered == 2
    existing_link = store.get_work_todo_dingtalk_link(existing_link_id)
    create_link = store.get_work_todo_dingtalk_link(create_link_id)
    assert existing_link.status == "active"
    assert existing_link.last_error == ""
    assert create_link.status == "active"
    assert create_link.dingtalk_task_id == "dt-task-1"
    assert create_link.retry_count == 1
    assert len(dws.created) == 1


def test_retry_failed_dingtalk_todo_links_cancels_completed_internal_todo(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(
        store,
        completion_evidence_json=json.dumps({"source": "work_update:1"}),
    )
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="",
        status="failed",
        last_error="code=SECURITY_CHECK_INVOKE_FAILED",
    )
    dws = FakeTodoDws()

    recovered = retry_failed_dingtalk_todo_links(
        store,
        dws,
        now="2026-06-27 10:10:00",
    )

    assert recovered == 0
    link = store.get_work_todo_dingtalk_link(link_id)
    assert link.status == "cancelled"
    assert link.last_error == "internal_todo_completed_before_dingtalk_delivery"
    assert dws.created == []


def test_retry_failed_dingtalk_todo_links_cancels_expired_external_create(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store, deadline_at="2026-06-26 18:00:00")
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="",
        status="failed",
        last_error="code=TOKEN_VERIFIED_FAILED",
    )
    dws = FakeTodoDws()

    recovered = retry_failed_dingtalk_todo_links(
        store,
        dws,
        now="2026-06-27 10:10:00",
    )

    assert recovered == 0
    link = store.get_work_todo_dingtalk_link(link_id)
    assert link.status == "cancelled"
    assert link.last_error == "dingtalk_todo_create_expired_before_external_delivery"
    assert dws.created == []


def test_maybe_create_dingtalk_todo_keeps_failed_link_when_recovery_get_fails(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()
    dws.get_errors["dt-task-1"] = DwsError("first get failed")

    first_link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )
    dws.get_errors["dt-task-1"] = DwsError("second get failed")

    second_link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:05:00",
    )

    assert len(dws.created) == 1
    assert second_link.id == first_link.id
    assert second_link.status == "failed"
    assert second_link.dingtalk_task_id == "dt-task-1"
    assert "second get failed" in second_link.last_error


def test_maybe_create_dingtalk_todo_prefers_active_link_over_failed_recovery(
    tmp_path,
):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    failed_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-failed",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="failed",
    )
    active_link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-active",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.get_payloads["dt-task-failed"] = {"id": "dt-task-failed", "done": False}

    link = maybe_create_dingtalk_todo(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:10:00",
    )

    assert dws.created == []
    assert link.id == active_link_id
    assert link.dingtalk_task_id == "dt-task-active"
    assert store.get_work_todo_dingtalk_link(failed_link_id).status == "failed"
    assert store.get_work_todo_dingtalk_link(active_link_id).status == "active"


def test_completion_scan_closes_internal_todo(tmp_path):
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
    link_id = store.create_work_todo_dingtalk_link(
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

    assert updated == 1
    assert dws.get_calls == []
    todo = store.get_work_todo(todo_id)
    assert todo.status == "done"
    assert "dingtalk_todo:dt-task-1" in todo.completion_evidence_json
    assert store.get_work_todo_dingtalk_link(link_id).status == "done"
    follow_up = store.get_follow_up_draft(follow_up_id)
    assert follow_up is not None
    assert follow_up.status == "completed"
    check = json.loads(follow_up.evidence_check_json)
    assert check["source"] == "dingtalk_todo:dt-task-1"
    assert check["reason"] == "DingTalk Todo marked done by owner"


def test_completion_scan_pages_until_the_linked_todo_is_found(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.completed_task_ids = [f"dt-other-{index}" for index in range(25)] + [
        "dt-task-1"
    ] + [f"dt-later-{index}" for index in range(40)]

    updated = scan_completed_dingtalk_todos(
        store,
        dws,
        now="2026-06-27 11:00:00",
    )

    assert updated == 1
    assert store.get_work_todo(todo_id).status == "done"
    assert store.get_work_todo_dingtalk_link(link_id).status == "done"
    # Stops once no active link is left to match, instead of reading every page.
    assert [call["page"] for call in dws.list_calls] == [1, 2]


def test_completion_scan_leaves_open_todo_that_dingtalk_does_not_list(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
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
    assert [call["page"] for call in dws.list_calls] == [1]


def test_completion_scan_skips_cancelled_backfill_link(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(
        store,
        status="cancelled",
        blocker="routine HR offer-flow step",
    )
    link_id = store.create_work_todo_dingtalk_link(
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


def test_internal_completion_marks_dingtalk_done(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()

    synced = sync_completed_todo_to_dingtalk(
        store,
        dws,
        work_todo_id=todo_id,
        evidence={"source": "reply_attempt:1", "summary": "已发客户"},
        now="2026-06-27 12:00:00",
    )

    assert synced == "completed"
    assert dws.done_calls == [{"task_id": "dt-task-1", "done": True}]
    assert store.get_active_work_todo_dingtalk_link(todo_id) is None
    links = store.list_work_todo_dingtalk_links(statuses=("done",))
    assert links[0].last_push_at == "2026-06-27 12:00:00"


def test_internal_completion_without_active_link_is_skipped(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    dws = FakeTodoDws()

    synced = sync_completed_todo_to_dingtalk(
        store,
        dws,
        work_todo_id=todo_id,
        evidence={"source": "reply_attempt:1", "summary": "已发客户"},
        now="2026-06-27 12:00:00",
    )

    assert synced == "skipped"
    assert dws.done_calls == []


def test_internal_completion_with_blank_task_id_sets_last_error(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()

    synced = sync_completed_todo_to_dingtalk(
        store,
        dws,
        work_todo_id=todo_id,
        evidence={"source": "reply_attempt:1", "summary": "已发客户"},
        now="2026-06-27 12:00:00",
    )

    assert synced == "failed"
    assert dws.done_calls == []
    stored = store.get_work_todo_dingtalk_link(link_id)
    assert "no task id" in stored.last_error


def test_internal_completion_dws_failure_records_last_error(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    dws = FakeTodoDws()
    dws.done_error = DwsError("todo done failed")

    synced = sync_completed_todo_to_dingtalk(
        store,
        dws,
        work_todo_id=todo_id,
        evidence={"source": "reply_attempt:1", "summary": "已发客户"},
        now="2026-06-27 12:00:00",
    )

    assert synced == "failed"
    stored = store.get_work_todo_dingtalk_link(link_id)
    assert stored.status == "active"
    assert "todo done failed" in stored.last_error


def test_outbox_create_for_ineligible_todo_is_skipped_not_failed(tmp_path):
    """A Todo that does not qualify for a mirror never reached DingTalk."""
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store, deadline_at="")
    store.enqueue_task_todo_sync_outbox(
        operation_key="task-agent:1:todo:1:create",
        work_todo_id=todo_id,
        operation="create",
    )
    dws = FakeTodoDws()

    delivered = dispatch_task_todo_sync_outbox(
        store, dws, owner="worker-1", now="2026-06-27 10:00:00"
    )

    assert delivered == 0
    assert dws.created == []
    assert store.list_task_todo_sync_outbox(statuses=("failed",)) == []
    skipped = store.list_task_todo_sync_outbox(statuses=("skipped",))
    assert len(skipped) == 1
    assert skipped[0]["error"] == "dingtalk_todo_not_eligible_for_mirror"
    assert skipped[0]["attempt_count"] == 1


def test_outbox_complete_without_mirror_is_skipped_not_failed(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    store.enqueue_task_todo_sync_outbox(
        operation_key="task-agent:1:todo:1:complete",
        work_todo_id=todo_id,
        operation="complete",
        evidence_json=json.dumps({"source": "reply_attempt:1"}, ensure_ascii=False),
    )
    dws = FakeTodoDws()

    delivered = dispatch_task_todo_sync_outbox(
        store, dws, owner="worker-1", now="2026-06-27 10:00:00"
    )

    assert delivered == 0
    assert dws.done_calls == []
    assert store.list_task_todo_sync_outbox(statuses=("failed",)) == []
    skipped = store.list_task_todo_sync_outbox(statuses=("skipped",))
    assert len(skipped) == 1
    assert skipped[0]["error"] == "dingtalk_todo_not_mirrored"


def test_outbox_create_still_fails_when_dingtalk_rejects_the_create(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    store.enqueue_task_todo_sync_outbox(
        operation_key="task-agent:1:todo:1:create",
        work_todo_id=todo_id,
        operation="create",
    )
    dws = FakeTodoDws()
    dws.create_error = DwsError("todo create failed")

    delivered = dispatch_task_todo_sync_outbox(
        store, dws, owner="worker-1", now="2026-06-27 10:00:00"
    )

    assert delivered == 0
    assert store.list_task_todo_sync_outbox(statuses=("skipped",)) == []
    failed = store.list_task_todo_sync_outbox(statuses=("failed",))
    assert len(failed) == 1
    assert failed[0]["error"] == "dingtalk_todo_effect_not_delivered"


def test_legacy_task_todo_outbox_gains_skipped_status_without_losing_rows(tmp_path):
    store = _store(tmp_path)
    with store._connect() as db:
        db.execute("drop table task_todo_sync_outbox")
        db.execute(
            """
            create table task_todo_sync_outbox (
                id integer primary key autoincrement,
                operation_key text not null unique,
                work_todo_id integer not null,
                operation text not null check(operation in ('create', 'complete')),
                evidence_json text not null default '{}',
                status text not null default 'queued'
                    check(status in ('queued', 'running', 'completed', 'failed', 'unknown')),
                lease_owner text not null default '', lease_expires_at text not null default '',
                receipt_json text not null default '{}', error text not null default '',
                attempt_count integer not null default 0, next_attempt_at text not null default '',
                created_at text not null default current_timestamp,
                updated_at text not null default current_timestamp,
                completed_at text not null default ''
            )
            """
        )
        db.execute(
            "insert into task_todo_sync_outbox (operation_key, work_todo_id, operation, status) "
            "values ('legacy-failed', 7, 'create', 'failed')"
        )

    store._initialize()
    store._initialize()

    rows = store.list_task_todo_sync_outbox()
    assert [row["operation_key"] for row in rows] == ["legacy-failed"]
    _, todo_id = _project_and_todo(store, deadline_at="")
    store.enqueue_task_todo_sync_outbox(
        operation_key="new-ineligible", work_todo_id=todo_id, operation="create"
    )
    dispatch_task_todo_sync_outbox(
        store, FakeTodoDws(), owner="worker-1", now="2026-06-27 10:00:00"
    )
    skipped = {
        row["operation_key"]
        for row in store.list_task_todo_sync_outbox(statuses=("skipped",))
    }
    assert "new-ineligible" in skipped


def _unknown_create(store, todo_id):
    store.enqueue_task_todo_sync_outbox(
        operation_key=f"deadline-backfill:{todo_id}:create",
        work_todo_id=todo_id,
        operation="create",
    )
    store.claim_task_todo_sync_outbox(owner="worker-a", now="2026-06-27 10:00:00", lease_seconds=1)
    store.claim_task_todo_sync_outbox(owner="worker-b", now="2026-06-27 10:01:00")
    [row] = store.list_task_todo_sync_outbox(statuses=("unknown",))
    return int(row["id"])


def test_an_unknown_create_proven_absent_is_requeued_and_its_half_link_closed(tmp_path):
    """Seen live: TODO 1238's link stayed `creating`, which counts as the
    active link, so no later create would ever have reached DingTalk."""
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    outbox_id = _unknown_create(store, todo_id)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id, executor_user_id="owner-1", executor_name="Alex",
        title_snapshot="给客户同步验收 ETA", deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1", status="creating",
    )

    assert store.reconcile_unknown_task_todo_sync_outbox(
        outbox_id=outbox_id,
        provider_absent_evidence="dws todo +search complete=true count=0",
    )

    row = store.get_task_todo_sync_outbox(outbox_id)
    assert row["status"] == "queued" and row["error"] == ""
    assert "count=0" in json.loads(row["evidence_json"])["provider_absent_reconciliation"]
    # Cancelled, not failed: the requeued create replaces it, so it must not
    # remain a History failure once that create lands.
    assert store.get_work_todo_dingtalk_link(link_id).status == "cancelled"
    assert store.get_active_work_todo_dingtalk_link(todo_id) is None


def test_reconciliation_needs_evidence_and_only_touches_an_unknown_create(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    outbox_id = _unknown_create(store, todo_id)

    with pytest.raises(ValueError, match="evidence"):
        store.reconcile_unknown_task_todo_sync_outbox(outbox_id=outbox_id, provider_absent_evidence=" ")
    assert store.reconcile_unknown_task_todo_sync_outbox(outbox_id=outbox_id, provider_absent_evidence="read")
    # Already queued: a second reconciliation changes nothing.
    assert not store.reconcile_unknown_task_todo_sync_outbox(outbox_id=outbox_id, provider_absent_evidence="read")


def test_an_unknown_create_that_got_its_task_id_is_completed_not_resent(tmp_path):
    """Seen live: a restart landed between DingTalk returning the task id and
    the read-back, leaving TODO 1238 unknown although the task existed."""
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    outbox_id = _unknown_create(store, todo_id)
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id, executor_user_id="owner-1", executor_name="Alex",
        title_snapshot="给客户同步验收 ETA", deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1", status="creating", dingtalk_task_id="57249450733",
    )

    assert store.complete_unknown_task_todo_sync_outbox_from_receipt(
        outbox_id=outbox_id,
        provider_readback_json='{"taskId": "57249450733", "done": false}',
    )

    row = store.get_task_todo_sync_outbox(outbox_id)
    assert row["status"] == "completed"
    assert json.loads(row["receipt_json"]) == {"link_id": link_id, "dingtalk_task_id": "57249450733"}
    assert store.get_work_todo_dingtalk_link(link_id).status == "active"


def test_an_unknown_create_without_a_task_id_cannot_be_completed_from_a_receipt(tmp_path):
    store = _store(tmp_path)
    _, todo_id = _project_and_todo(store)
    outbox_id = _unknown_create(store, todo_id)
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id, executor_user_id="owner-1", executor_name="Alex",
        title_snapshot="t", deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1", status="creating",
    )

    assert not store.complete_unknown_task_todo_sync_outbox_from_receipt(
        outbox_id=outbox_id, provider_readback_json='{"taskId": "x"}'
    )
    assert store.get_task_todo_sync_outbox(outbox_id)["status"] == "unknown"
