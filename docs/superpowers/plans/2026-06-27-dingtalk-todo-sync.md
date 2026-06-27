# DingTalk Todo Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link high-confidence internal `work_todos` to DingTalk Todo, pull DingTalk completion state, push internal completion evidence back to DingTalk, and suppress duplicate follow-up reminders.

**Architecture:** Keep `work_todos` as the source of truth and add `work_todo_dingtalk_links` as an external execution link table. Add focused DWS Todo wrappers and a new `app/todo_sync.py` module for all DingTalk Todo side effects. Integrate the sync layer after task-agent persistence, before follow-up sends, and inside task maintenance without making the task agent itself call DWS.

**Tech Stack:** Python 3.11, SQLite through `app.store.AutoReplyStore`, Pydantic v2 models in `app.task_models`, DWS CLI wrapper in `app.dws_client`, local audit web in `app.audit_web`, pytest.

---

## Scope Check

This plan implements one cohesive subsystem: DingTalk Todo synchronization for existing task-summary TODOs. It does not implement title/deadline edit sync, automatic deletion, Derek as executor, or DingTalk Todo as the project-management source of truth. Those items remain out of scope.

## File Structure

- Modify `app/task_models.py`: add `WorkTodoDingTalkLink` Pydantic model and status enum.
- Modify `app/store.py`: add `work_todo_dingtalk_links` schema, indexes, and store methods.
- Modify `app/dws_client.py`: add DingTalk Todo command builders and JSON wrappers.
- Create `app/todo_sync.py`: pure sync policy, create/pull/push operations, and follow-up guard helper.
- Modify `app/task_agent.py`: call sync creation after TODO persistence.
- Modify `app/follow_up.py`: check linked DingTalk Todo before sending a follow-up.
- Modify `app/cli.py`: run pull sync inside task maintenance. Do not add a new manual command in this implementation.
- Modify `app/audit_web.py`: show DingTalk Todo link state on task detail and operation logs.
- Add or modify tests in `tests/test_task_store.py`, `tests/test_dws_client.py`, `tests/test_todo_sync.py`, `tests/test_task_agent.py`, `tests/test_follow_up.py`, `tests/test_cli.py`, and `tests/test_audit_web.py`.

## Task 1: Add Link Schema And Store Methods

**Files:**
- Modify: `app/task_models.py`
- Modify: `app/store.py`
- Test: `tests/test_task_store.py`

- [ ] **Step 1: Write failing store tests**

Add these tests to `tests/test_task_store.py`:

```python
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
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_task_store.py::test_dingtalk_todo_link_create_get_update_and_active_lookup tests/test_task_store.py::test_dingtalk_todo_link_prevents_duplicate_active_links -q`

Expected: FAIL with `AttributeError: 'AutoReplyStore' object has no attribute 'create_work_todo_dingtalk_link'`.

- [ ] **Step 3: Add model types**

In `app/task_models.py`, add:

```python
class DingTalkTodoLinkStatus(StrEnum):
    CREATING = "creating"
    ACTIVE = "active"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


class WorkTodoDingTalkLink(BaseModel):
    id: int
    work_todo_id: int
    dingtalk_task_id: str = ""
    executor_user_id: str = ""
    executor_name: str = ""
    title_snapshot: str = ""
    deadline_at_snapshot: str = ""
    priority_snapshot: str = ""
    status: DingTalkTodoLinkStatus
    last_dingtalk_done: bool | None = None
    last_dingtalk_payload_json: str = "{}"
    last_pull_at: str = ""
    last_push_at: str = ""
    last_error: str = ""
    created_at: str
    updated_at: str
```

Update the `app.store` import list to import `WorkTodoDingTalkLink`.

- [ ] **Step 4: Add schema and store methods**

In `AutoReplyStore._initialize`, add this table after `work_todos`:

```sql
create table if not exists work_todo_dingtalk_links (
    id integer primary key autoincrement,
    work_todo_id integer not null,
    dingtalk_task_id text not null default '',
    executor_user_id text not null default '',
    executor_name text not null default '',
    title_snapshot text not null default '',
    deadline_at_snapshot text not null default '',
    priority_snapshot text not null default '',
    status text not null default 'creating',
    last_dingtalk_done integer,
    last_dingtalk_payload_json text not null default '{}',
    last_pull_at text not null default '',
    last_push_at text not null default '',
    last_error text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);
create index if not exists idx_work_todo_dingtalk_links_todo
    on work_todo_dingtalk_links(work_todo_id, status, id);
create unique index if not exists idx_work_todo_dingtalk_links_task_id
    on work_todo_dingtalk_links(dingtalk_task_id)
    where dingtalk_task_id != '';
create unique index if not exists idx_work_todo_dingtalk_links_active_todo
    on work_todo_dingtalk_links(work_todo_id)
    where status in ('creating', 'active');
```

Add migration column checks only if the table may exist in older DBs before this change. Since this table is new, no `alter table` migration is needed.

Add these methods near the existing `work_todos` methods:

```python
def create_work_todo_dingtalk_link(self, **values) -> int:
    allowed_columns = {
        "work_todo_id",
        "dingtalk_task_id",
        "executor_user_id",
        "executor_name",
        "title_snapshot",
        "deadline_at_snapshot",
        "priority_snapshot",
        "status",
        "last_dingtalk_done",
        "last_dingtalk_payload_json",
        "last_pull_at",
        "last_push_at",
        "last_error",
    }
    filtered = self._filter_allowed_values(values, allowed_columns)
    work_todo_id = int(filtered["work_todo_id"])
    with self._connect() as db:
        existing = db.execute(
            """
            select id
            from work_todo_dingtalk_links
            where work_todo_id=? and status in ('creating', 'active')
            order by id desc
            limit 1
            """,
            (work_todo_id,),
        ).fetchone()
        if existing is not None:
            return int(existing["id"])
        keys = list(filtered.keys())
        cursor = db.execute(
            f"insert into work_todo_dingtalk_links ({', '.join(keys)}) "
            f"values ({', '.join('?' for _ in keys)})",
            [filtered[key] for key in keys],
        )
        return int(cursor.lastrowid)


def update_work_todo_dingtalk_link(self, link_id: int, **values) -> None:
    if not values:
        return
    allowed_columns = {
        "dingtalk_task_id",
        "executor_user_id",
        "executor_name",
        "title_snapshot",
        "deadline_at_snapshot",
        "priority_snapshot",
        "status",
        "last_dingtalk_done",
        "last_dingtalk_payload_json",
        "last_pull_at",
        "last_push_at",
        "last_error",
    }
    filtered = self._filter_allowed_values(values, allowed_columns)
    assignments = ", ".join(f"{key}=?" for key in filtered)
    with self._connect() as db:
        db.execute(
            f"""
            update work_todo_dingtalk_links
            set {assignments},
                updated_at=current_timestamp
            where id=?
            """,
            [*filtered.values(), link_id],
        )


def get_work_todo_dingtalk_link(self, link_id: int) -> WorkTodoDingTalkLink | None:
    with self._connect() as db:
        row = db.execute(
            "select * from work_todo_dingtalk_links where id=?",
            (link_id,),
        ).fetchone()
    return None if row is None else WorkTodoDingTalkLink.model_validate(dict(row))


def get_active_work_todo_dingtalk_link(
    self,
    work_todo_id: int,
) -> WorkTodoDingTalkLink | None:
    with self._connect() as db:
        row = db.execute(
            """
            select *
            from work_todo_dingtalk_links
            where work_todo_id=? and status in ('creating', 'active')
            order by id desc
            limit 1
            """,
            (work_todo_id,),
        ).fetchone()
    return None if row is None else WorkTodoDingTalkLink.model_validate(dict(row))


def list_work_todo_dingtalk_links(
    self,
    *,
    statuses: tuple[str, ...] | None = None,
    limit: int = 200,
) -> list[WorkTodoDingTalkLink]:
    query = "select * from work_todo_dingtalk_links"
    args: list[object] = []
    if statuses:
        query += f" where status in ({','.join('?' for _ in statuses)})"
        args.extend(statuses)
    query += " order by updated_at asc, id asc limit ?"
    args.append(limit)
    with self._connect() as db:
        rows = db.execute(query, args).fetchall()
    return [WorkTodoDingTalkLink.model_validate(dict(row)) for row in rows]
```

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_task_store.py::test_dingtalk_todo_link_create_get_update_and_active_lookup tests/test_task_store.py::test_dingtalk_todo_link_prevents_duplicate_active_links -q`

Expected: PASS.

Commit:

```bash
git add app/task_models.py app/store.py tests/test_task_store.py
git commit -m "feat: store DingTalk todo links"
```

## Task 2: Add DWS Todo Client Wrappers

**Files:**
- Modify: `app/dws_client.py`
- Test: `tests/test_dws_client.py`

- [ ] **Step 1: Write failing DWS client tests**

Add to `tests/test_dws_client.py`:

```python
def test_build_todo_create_command():
    client = DwsClient(dws_bin="dws")

    assert client.build_todo_create_command(
        title="给客户同步验收 ETA",
        executor_user_id="owner-1",
        due="2026-07-01T18:00:00+08:00",
        priority=30,
    ) == [
        "dws",
        "todo",
        "task",
        "create",
        "--title",
        "给客户同步验收 ETA",
        "--executors",
        "owner-1",
        "--due",
        "2026-07-01T18:00:00+08:00",
        "--priority",
        "30",
        "--format",
        "json",
    ]


def test_build_todo_get_and_done_commands():
    client = DwsClient(dws_bin="dws")

    assert client.build_todo_get_command("dt-task-1") == [
        "dws",
        "todo",
        "task",
        "get",
        "--task-id",
        "dt-task-1",
        "--format",
        "json",
    ]
    assert client.build_todo_done_command("dt-task-1", done=True) == [
        "dws",
        "todo",
        "task",
        "done",
        "--task-id",
        "dt-task-1",
        "--status",
        "true",
        "--format",
        "json",
    ]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_dws_client.py::test_build_todo_create_command tests/test_dws_client.py::test_build_todo_get_and_done_commands -q`

Expected: FAIL with missing methods.

- [ ] **Step 3: Implement command builders and wrappers**

In `app/dws_client.py`, add command builders near the minutes command builders:

```python
def build_todo_create_command(
    self,
    *,
    title: str,
    executor_user_id: str,
    due: str,
    priority: int,
) -> list[str]:
    if not title.strip():
        raise ValueError("DingTalk todo title is required")
    if not executor_user_id.strip():
        raise ValueError("DingTalk todo executor_user_id is required")
    if not due.strip():
        raise ValueError("DingTalk todo due is required")
    if priority not in {10, 20, 30, 40}:
        raise ValueError("DingTalk todo priority must be one of 10, 20, 30, 40")
    return [
        self.dws_bin,
        "todo",
        "task",
        "create",
        "--title",
        title,
        "--executors",
        executor_user_id,
        "--due",
        due,
        "--priority",
        str(priority),
        "--format",
        "json",
    ]


def build_todo_get_command(self, task_id: str) -> list[str]:
    if not task_id.strip():
        raise ValueError("DingTalk todo task_id is required")
    return [
        self.dws_bin,
        "todo",
        "task",
        "get",
        "--task-id",
        task_id,
        "--format",
        "json",
    ]


def build_todo_done_command(self, task_id: str, *, done: bool) -> list[str]:
    if not task_id.strip():
        raise ValueError("DingTalk todo task_id is required")
    return [
        self.dws_bin,
        "todo",
        "task",
        "done",
        "--task-id",
        task_id,
        "--status",
        "true" if done else "false",
        "--format",
        "json",
    ]
```

Add JSON wrappers near `get_minutes_todos`:

```python
def create_todo_task(
    self,
    *,
    title: str,
    executor_user_id: str,
    due: str,
    priority: int,
) -> dict[str, Any]:
    payload = self.run_json(
        self.build_todo_create_command(
            title=title,
            executor_user_id=executor_user_id,
            due=due,
            priority=priority,
        )
    )
    if not isinstance(payload, dict):
        raise DwsError("invalid todo create response")
    return payload


def get_todo_task(self, task_id: str) -> dict[str, Any]:
    payload = self.run_json(self.build_todo_get_command(task_id))
    if not isinstance(payload, dict):
        raise DwsError("invalid todo get response")
    return payload


def mark_todo_task_done(self, task_id: str, *, done: bool = True) -> dict[str, Any]:
    payload = self.run_json(self.build_todo_done_command(task_id, done=done))
    if not isinstance(payload, dict):
        raise DwsError("invalid todo done response")
    return payload
```

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest tests/test_dws_client.py::test_build_todo_create_command tests/test_dws_client.py::test_build_todo_get_and_done_commands -q`

Expected: PASS.

Commit:

```bash
git add app/dws_client.py tests/test_dws_client.py
git commit -m "feat: wrap DingTalk todo commands"
```

## Task 3: Add Todo Sync Core

**Files:**
- Create: `app/todo_sync.py`
- Test: `tests/test_todo_sync.py`

- [ ] **Step 1: Write sync tests**

Create `tests/test_todo_sync.py`:

```python
import json
from pathlib import Path

from app.store import AutoReplyStore
from app.todo_sync import (
    maybe_create_dingtalk_todo,
    pull_dingtalk_todo_statuses,
    sync_completed_todo_to_dingtalk,
)


class FakeTodoDws:
    def __init__(self):
        self.created = []
        self.get_payloads = {}
        self.done_calls = []

    def create_todo_task(self, *, title, executor_user_id, due, priority):
        self.created.append(
            {
                "title": title,
                "executor_user_id": executor_user_id,
                "due": due,
                "priority": priority,
            }
        )
        return {"id": "dt-task-1", "taskId": "dt-task-1"}

    def get_todo_task(self, task_id):
        return self.get_payloads.get(task_id, {"id": task_id, "done": False})

    def mark_todo_task_done(self, task_id, *, done=True):
        self.done_calls.append({"task_id": task_id, "done": done})
        return {"id": task_id, "done": done}


def _store(tmp_path: Path) -> AutoReplyStore:
    return AutoReplyStore(tmp_path / "task.sqlite3")


def _project_and_todo(store: AutoReplyStore, **todo_values):
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P1",
        risk_level="medium",
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
            "title": "给客户同步验收 ETA",
            "executor_user_id": "owner-1",
            "due": "2026-07-01T18:00:00+08:00",
            "priority": 30,
        }
    ]
    stored = store.get_work_todo_dingtalk_link(link.id)
    assert stored.dingtalk_task_id == "dt-task-1"
    assert stored.status == "active"


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


def test_pull_done_dingtalk_todo_closes_internal_todo(tmp_path):
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
    dws.get_payloads["dt-task-1"] = {"id": "dt-task-1", "done": True}

    updated = pull_dingtalk_todo_statuses(
        store,
        dws,
        now="2026-06-27 11:00:00",
    )

    assert updated == 1
    todo = store.get_work_todo(todo_id)
    assert todo.status == "done"
    assert "dingtalk_todo:dt-task-1" in todo.completion_evidence_json
    assert store.get_work_todo_dingtalk_link(link_id).status == "done"


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

    assert synced is True
    assert dws.done_calls == [{"task_id": "dt-task-1", "done": True}]
    assert store.get_active_work_todo_dingtalk_link(todo_id) is None
    links = store.list_work_todo_dingtalk_links(statuses=("done",))
    assert links[0].last_push_at == "2026-06-27 12:00:00"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_todo_sync.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.todo_sync'`.

- [ ] **Step 3: Implement `app/todo_sync.py`**

Create `app/todo_sync.py`:

```python
import json
from datetime import datetime
from typing import Any

from app.dws_client import DwsError
from app.store import AutoReplyStore
from app.task_models import ProjectCategory, ProjectPriority, TodoStatus


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _deadline_to_iso(value: str) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return ""
    if parsed.tzinfo is not None:
        return parsed.isoformat()
    return parsed.isoformat() + "+08:00"


def _priority_to_dingtalk(priority: str) -> int:
    mapping = {
        ProjectPriority.P0.value: 40,
        ProjectPriority.P1.value: 30,
        ProjectPriority.P2.value: 20,
        ProjectPriority.NONE.value: 20,
    }
    return mapping.get(str(priority), 20)


def _payload_task_id(payload: dict[str, Any]) -> str:
    for key in ("taskId", "task_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    result = payload.get("result")
    if isinstance(result, dict):
        return _payload_task_id(result)
    return ""


def _payload_done(payload: dict[str, Any]) -> bool:
    for key in ("done", "isDone", "completed", "isCompleted"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
    result = payload.get("result")
    if isinstance(result, dict):
        return _payload_done(result)
    return False


def _has_completion_evidence(value: str) -> bool:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return bool(value.strip())
    return bool(parsed)


def _is_actionable_title(title: str) -> bool:
    compact = "".join(title.split())
    if len(compact) < 6:
        return False
    weak_titles = {"跟进一下", "同步进展", "确认进展", "问一下", "推进一下"}
    return compact not in weak_titles


def _is_project_sensitive(store: AutoReplyStore, project_id: int) -> bool:
    project = store.get_work_project(project_id)
    if project is None:
        return False
    return str(project.category) == ProjectCategory.HR.value


def _todo_is_eligible(store: AutoReplyStore, todo) -> bool:
    if str(todo.status) not in {TodoStatus.OPEN.value, TodoStatus.WAITING_OWNER.value}:
        return False
    if not todo.owner_user_id.strip():
        return False
    if not todo.deadline_at.strip() or not _deadline_to_iso(todo.deadline_at):
        return False
    if not _is_actionable_title(todo.title):
        return False
    if _has_completion_evidence(todo.completion_evidence_json):
        return False
    if _is_project_sensitive(store, todo.project_id):
        return False
    return store.get_active_work_todo_dingtalk_link(todo.id) is None


def maybe_create_dingtalk_todo(
    store: AutoReplyStore,
    dws,
    *,
    work_todo_id: int,
    now: str,
):
    todo = store.get_work_todo(work_todo_id)
    if todo is None or not _todo_is_eligible(store, todo):
        return None
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo.id,
        executor_user_id=todo.owner_user_id,
        executor_name=todo.owner_name,
        title_snapshot=todo.title,
        deadline_at_snapshot=todo.deadline_at,
        priority_snapshot=str(todo.priority),
        status="creating",
    )
    try:
        create_payload = dws.create_todo_task(
            title=todo.title,
            executor_user_id=todo.owner_user_id,
            due=_deadline_to_iso(todo.deadline_at),
            priority=_priority_to_dingtalk(str(todo.priority)),
        )
        task_id = _payload_task_id(create_payload)
        if not task_id:
            raise DwsError("DingTalk todo create response did not include task id")
        get_payload = dws.get_todo_task(task_id)
        store.update_work_todo_dingtalk_link(
            link_id,
            dingtalk_task_id=task_id,
            status="active",
            last_dingtalk_done=_payload_done(get_payload),
            last_dingtalk_payload_json=json.dumps(get_payload, ensure_ascii=False),
            last_push_at=now,
            last_pull_at=now,
            last_error="",
        )
    except Exception as exc:
        store.update_work_todo_dingtalk_link(
            link_id,
            status="failed",
            last_error=str(exc),
        )
    return store.get_work_todo_dingtalk_link(link_id)


def pull_dingtalk_todo_statuses(
    store: AutoReplyStore,
    dws,
    *,
    now: str,
    limit: int = 100,
) -> int:
    closed = 0
    for link in store.list_work_todo_dingtalk_links(statuses=("active",), limit=limit):
        try:
            payload = dws.get_todo_task(link.dingtalk_task_id)
            done = _payload_done(payload)
            store.update_work_todo_dingtalk_link(
                link.id,
                last_dingtalk_done=done,
                last_dingtalk_payload_json=json.dumps(payload, ensure_ascii=False),
                last_pull_at=now,
                last_error="",
            )
            if done:
                _close_internal_todo_from_dingtalk(store, link, payload, now=now)
                closed += 1
        except Exception as exc:
            store.update_work_todo_dingtalk_link(
                link.id,
                last_error=str(exc),
            )
    return closed


def _close_internal_todo_from_dingtalk(
    store: AutoReplyStore,
    link,
    payload: dict[str, Any],
    *,
    now: str,
) -> None:
    evidence = {
        "source": f"dingtalk_todo:{link.dingtalk_task_id}",
        "summary": "DingTalk Todo is marked done.",
        "checked_at": now,
    }
    store.update_work_todo(
        link.work_todo_id,
        status=TodoStatus.DONE.value,
        completion_evidence_json=json.dumps(evidence, ensure_ascii=False),
    )
    store.update_work_todo_dingtalk_link(
        link.id,
        status="done",
        last_dingtalk_done=True,
        last_dingtalk_payload_json=json.dumps(payload, ensure_ascii=False),
        last_pull_at=now,
        last_error="",
    )
    todo = store.get_work_todo(link.work_todo_id)
    if todo is not None:
        store.create_work_update(
            project_id=todo.project_id,
            source_type="dingtalk_todo",
            source_ref=link.dingtalk_task_id,
            summary=f"DingTalk Todo completed: {todo.title}",
            changes_json=json.dumps({"todo_id": todo.id, "status": "done"}, ensure_ascii=False),
            merge_reason="DingTalk Todo completion synced to internal task.",
            confidence=1.0,
        )


def sync_completed_todo_to_dingtalk(
    store: AutoReplyStore,
    dws,
    *,
    work_todo_id: int,
    evidence: dict[str, Any],
    now: str,
) -> bool:
    link = store.get_active_work_todo_dingtalk_link(work_todo_id)
    if link is None or not link.dingtalk_task_id.strip():
        return False
    try:
        payload = dws.mark_todo_task_done(link.dingtalk_task_id, done=True)
        store.update_work_todo_dingtalk_link(
            link.id,
            status="done",
            last_dingtalk_done=True,
            last_dingtalk_payload_json=json.dumps(payload, ensure_ascii=False),
            last_push_at=now,
            last_error="",
        )
        return True
    except Exception as exc:
        store.update_work_todo_dingtalk_link(
            link.id,
            last_error=str(exc),
        )
        return False
```

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest tests/test_todo_sync.py -q`

Expected: PASS.

Commit:

```bash
git add app/todo_sync.py tests/test_todo_sync.py
git commit -m "feat: sync internal todos with DingTalk"
```

## Task 4: Create DingTalk Todo After Task-Agent Persistence

**Files:**
- Modify: `app/task_agent.py`
- Test: `tests/test_task_agent.py`

- [ ] **Step 1: Write failing task-agent integration test**

Add to `tests/test_task_agent.py`:

```python
def test_apply_decision_creates_dingtalk_todo_for_high_confidence_todo(
    tmp_path,
    monkeypatch,
):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    calls = []

    def fake_create(store_arg, dws_arg, *, work_todo_id, now):
        calls.append((store_arg, dws_arg, work_todo_id, now))
        return None

    monkeypatch.setattr("app.task_agent.maybe_create_dingtalk_todo", fake_create)

    decision = TaskAgentDecision.model_validate(
        {
            "action": "create_project",
            "project": {
                "title": "客户交付",
                "category": "projects",
                "status": "active",
                "priority": "P1",
                "risk_level": "medium",
                "memory_context": _memory_context(),
            },
            "todo_changes": [
                {
                    "action": "create",
                    "todo_ref": "eta",
                    "title": "给客户同步验收 ETA",
                    "owner_user_id": "owner-1",
                    "owner_name": "Alex",
                    "status": "open",
                    "priority": "P1",
                    "deadline_at": "2026-07-01 18:00:00",
                }
            ],
            "follow_up_drafts": [],
            "update_summary": "新增交付 ETA task item。",
            "merge_reason": "新项目。",
            "memory_recall_used": True,
            "confidence": 0.9,
        }
    )

    apply_task_agent_decision(
        store,
        summary_input_id=0,
        work_item=_work_item("客户交付"),
        decision=decision,
        dws=object(),
        now="2026-06-27 10:00:00",
    )

    todo_id = store.list_work_todos()[0].id
    assert calls == [(store, calls[0][1], todo_id, "2026-06-27 10:00:00")]
```

- [ ] **Step 2: Run test and verify failure**

Run: `.venv/bin/pytest tests/test_task_agent.py::test_apply_decision_creates_dingtalk_todo_for_high_confidence_todo -q`

Expected: FAIL because `apply_task_agent_decision` does not accept `dws` and `now`, or because it does not call `maybe_create_dingtalk_todo`.

- [ ] **Step 3: Wire sync after TODO persistence**

In `app/task_agent.py`, import:

```python
from app.todo_sync import maybe_create_dingtalk_todo
```

Update `apply_task_agent_decision` signature to accept optional side-effect dependencies:

```python
def apply_task_agent_decision(
    store: AutoReplyStore,
    *,
    summary_input_id: int,
    work_item: WorkItem,
    decision: TaskAgentDecision,
    memory_recall_attempted: bool = False,
    audit_tool_events: object | None = None,
    dws=None,
    now: str = "",
) -> None:
```

Track changed TODO ids inside the function:

```python
changed_todo_ids: list[int] = []
```

Append every created or updated TODO id to `changed_todo_ids`. After follow-up draft creation and work update persistence, add:

```python
if dws is not None:
    sync_now = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for todo_id in changed_todo_ids:
        maybe_create_dingtalk_todo(
            store,
            dws,
            work_todo_id=todo_id,
            now=sync_now,
        )
```

Make sure existing callers still work because `dws` is optional.

- [ ] **Step 4: Pass DWS from `process_work_item`**

If `process_work_item` already has access to a DWS client through the runner or CLI settings, thread it through. If not, add an optional parameter:

```python
def process_work_item(
    store: AutoReplyStore,
    runner: TaskAgentRunner,
    work_input: WorkSummaryInput,
    *,
    dws=None,
    now: str = "",
) -> None:
```

Then call:

```python
apply_task_agent_decision(
    store,
    summary_input_id=work_input.id,
    work_item=work_item,
    decision=decision,
    memory_recall_attempted=memory_recall_attempted,
    audit_tool_events=audit_tool_events,
    dws=dws,
    now=now,
)
```

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_task_agent.py::test_apply_decision_creates_dingtalk_todo_for_high_confidence_todo tests/test_task_agent.py -q`

Expected: PASS.

Commit:

```bash
git add app/task_agent.py tests/test_task_agent.py
git commit -m "feat: create DingTalk todos from task agent output"
```

## Task 5: Push Internal Completion To DingTalk

**Files:**
- Modify: `app/task_agent.py`
- Modify: `app/follow_up.py`
- Test: `tests/test_task_agent.py`
- Test: `tests/test_follow_up.py`

- [ ] **Step 1: Add task-agent close test**

Add to `tests/test_task_agent.py`:

```python
def test_apply_decision_pushes_completed_todo_to_dingtalk(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户同步验收 ETA",
        owner_user_id="owner-1",
        status="open",
        deadline_at="2026-07-01 18:00:00",
    )
    calls = []

    def fake_push(store_arg, dws_arg, *, work_todo_id, evidence, now):
        calls.append((work_todo_id, evidence, now))
        return True

    monkeypatch.setattr("app.task_agent.sync_completed_todo_to_dingtalk", fake_push)

    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "project": {
                "id": project_id,
                "title": "客户交付",
                "category": "projects",
                "status": "active",
                "memory_context": _memory_context(),
            },
            "todo_changes": [
                {
                    "action": "close",
                    "todo_id": todo_id,
                    "completion_evidence": {
                        "source": "reply_attempt:1",
                        "summary": "已发客户",
                    },
                }
            ],
            "follow_up_drafts": [],
            "update_summary": "关闭 task item。",
            "merge_reason": "明确完成。",
            "memory_recall_used": True,
            "confidence": 1.0,
        }
    )

    apply_task_agent_decision(
        store,
        summary_input_id=0,
        work_item=_work_item("客户交付"),
        decision=decision,
        dws=object(),
        now="2026-06-27 12:00:00",
    )

    assert calls == [
        (
            todo_id,
            {"source": "reply_attempt:1", "summary": "已发客户"},
            "2026-06-27 12:00:00",
        )
    ]
```

- [ ] **Step 2: Add follow-up reaction close test**

Extend `tests/test_follow_up.py` with:

```python
def test_completion_reaction_pushes_dingtalk_todo_done(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
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
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步验收 ETA。",
        scheduled_at="2026-06-27 09:00:00",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="客户交付群",
        trigger_message_id="msg-complete",
        trigger_sender="Alex",
        trigger_text="完成了，这块已经结束了。",
        action="no_reply",
        sensitivity_kind="general",
    )
    with store._connect() as db:
        db.execute(
            """
            update reply_attempts
            set created_at='2026-06-27 09:30:00',
                updated_at='2026-06-27 09:30:00'
            where id=?
            """,
            (attempt_id,),
        )
    pushed = []

    def fake_push(store_arg, dws_arg, *, work_todo_id, evidence, now):
        pushed.append({"work_todo_id": work_todo_id, "now": now})
        return True

    monkeypatch.setattr("app.follow_up.sync_completed_todo_to_dingtalk", fake_push)
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-27 10:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert pushed == [{"work_todo_id": todo_id, "now": "2026-06-27 10:00:00"}]
```

- [ ] **Step 3: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_task_agent.py::test_apply_decision_pushes_completed_todo_to_dingtalk tests/test_follow_up.py -q`

Expected: FAIL because push completion is not wired.

- [ ] **Step 4: Wire push helper**

In `app/task_agent.py`, import:

```python
from app.todo_sync import maybe_create_dingtalk_todo, sync_completed_todo_to_dingtalk
```

When applying a `close` action with `completion_evidence`, after `store.update_work_todo`, call:

```python
if dws is not None:
    sync_completed_todo_to_dingtalk(
        store,
        dws,
        work_todo_id=todo_id,
        evidence=todo_change.completion_evidence or {},
        now=sync_now,
    )
```

In `app/follow_up.py`, import `sync_completed_todo_to_dingtalk`. In the branch that closes TODOs from reaction completion, call the helper after `store.update_work_todo` when `dws` is available.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_task_agent.py tests/test_follow_up.py tests/test_todo_sync.py -q`

Expected: PASS.

Commit:

```bash
git add app/task_agent.py app/follow_up.py tests/test_task_agent.py tests/test_follow_up.py
git commit -m "feat: push internal todo completion to DingTalk"
```

## Task 6: Guard Follow-Up Sends With DingTalk Todo State

**Files:**
- Modify: `app/todo_sync.py`
- Modify: `app/follow_up.py`
- Test: `tests/test_follow_up.py`
- Test: `tests/test_todo_sync.py`

- [ ] **Step 1: Add follow-up guard tests**

Add helper in `app/todo_sync.py` through tests first. In `tests/test_todo_sync.py`, add:

```python
def test_refresh_link_before_follow_up_closes_when_dingtalk_done(tmp_path):
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
    dws.get_payloads["dt-task-1"] = {"id": "dt-task-1", "done": True}

    from app.todo_sync import refresh_dingtalk_todo_before_follow_up

    completed, reason = refresh_dingtalk_todo_before_follow_up(
        store,
        dws,
        work_todo_id=todo_id,
        now="2026-06-27 10:00:00",
    )

    assert completed is True
    assert reason == "dingtalk_todo_done"
    assert store.get_work_todo(todo_id).status == "done"
```

Add to `tests/test_follow_up.py`:

```python
def test_due_follow_up_skips_when_linked_dingtalk_todo_is_done(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
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
    store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        dingtalk_task_id="dt-task-1",
        executor_user_id="owner-1",
        title_snapshot="给客户同步验收 ETA",
        deadline_at_snapshot="2026-07-01 18:00:00",
        priority_snapshot="P1",
        status="active",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_kind="direct",
        question_text="请同步验收 ETA。",
        scheduled_at="2026-06-27 09:00:00",
    )
    dws = FakeDws()
    dws.get_todo_task = lambda task_id: {"id": task_id, "done": True}

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-27 10:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert dws.sent == []
    skipped = store.list_follow_up_drafts(statuses=("skipped",))[0]
    assert "dingtalk_todo_done" in skipped.send_result_json
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_todo_sync.py::test_refresh_link_before_follow_up_closes_when_dingtalk_done tests/test_follow_up.py::test_due_follow_up_skips_when_linked_dingtalk_todo_is_done -q`

Expected: FAIL because `refresh_dingtalk_todo_before_follow_up` is missing and follow-up does not call it.

- [ ] **Step 3: Implement guard helper**

Add to `app/todo_sync.py`:

```python
def refresh_dingtalk_todo_before_follow_up(
    store: AutoReplyStore,
    dws,
    *,
    work_todo_id: int,
    now: str,
) -> tuple[bool, str]:
    link = store.get_active_work_todo_dingtalk_link(work_todo_id)
    if link is None or not link.dingtalk_task_id.strip():
        return False, ""
    try:
        payload = dws.get_todo_task(link.dingtalk_task_id)
        done = _payload_done(payload)
        store.update_work_todo_dingtalk_link(
            link.id,
            last_dingtalk_done=done,
            last_dingtalk_payload_json=json.dumps(payload, ensure_ascii=False),
            last_pull_at=now,
            last_error="",
        )
        if done:
            _close_internal_todo_from_dingtalk(store, link, payload, now=now)
            return True, "dingtalk_todo_done"
    except Exception as exc:
        store.update_work_todo_dingtalk_link(link.id, last_error=str(exc))
    return False, ""
```

- [ ] **Step 4: Call guard before existing follow-up completion checks**

In `app/follow_up.py`, import:

```python
from app.todo_sync import refresh_dingtalk_todo_before_follow_up
```

In `process_due_follow_ups`, before `_completion_supported_by_current_evidence`, add:

```python
if draft.todo_id > 0:
    dingtalk_done, dingtalk_reason = refresh_dingtalk_todo_before_follow_up(
        store,
        dws,
        work_todo_id=draft.todo_id,
        now=now,
    )
    if dingtalk_done:
        _skip_completed_follow_up(
            store,
            draft,
            now=now,
            reason=dingtalk_reason,
        )
        continue
```

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_todo_sync.py tests/test_follow_up.py -q`

Expected: PASS.

Commit:

```bash
git add app/todo_sync.py app/follow_up.py tests/test_todo_sync.py tests/test_follow_up.py
git commit -m "feat: check DingTalk todos before follow-ups"
```

## Task 7: Add Maintenance, UI, And Operation Logs

**Files:**
- Modify: `app/cli.py`
- Modify: `app/audit_web.py`
- Modify: `app/store.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_audit_web.py`
- Test: `tests/test_task_store.py`

- [ ] **Step 1: Add CLI maintenance test**

In `tests/test_cli.py`, add a focused test that monkeypatches `app.todo_sync.pull_dingtalk_todo_statuses` and asserts `daily_task_maintenance_command` calls it once with the service store and DWS client.

Use assertion:

```python
assert calls == [{"now": "2026-06-27 10:00:00"}]
```

- [ ] **Step 2: Add operation log test**

In `tests/test_task_store.py`, create a failed link and assert `store.list_operation_logs(query="dt-task-1")` includes category `DingTalk Todo`.

Expected row fields:

```python
assert log.category == "DingTalk Todo"
assert log.status == "failed"
assert "dt-task-1" in log.context
```

- [ ] **Step 3: Add task detail rendering test**

In `tests/test_audit_web.py`, create a project, task item, and active link, render project detail, and assert the page contains:

```python
assert "DingTalk Todo" in html
assert "dt-task-1" in html
assert "active" in html
assert "last pull" in html or "Last pull" in html
```

- [ ] **Step 4: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_cli.py::test_daily_task_maintenance_pulls_dingtalk_todos tests/test_task_store.py::test_operation_logs_include_dingtalk_todo_links tests/test_audit_web.py::test_task_project_detail_renders_dingtalk_todo_link -q`

Expected: FAIL because maintenance, operation logs, and UI rendering are not wired.

- [ ] **Step 5: Wire maintenance**

In `app/cli.py`, import and call:

```python
from app.todo_sync import pull_dingtalk_todo_statuses
```

Inside the daily task maintenance command after processing work items and before or after follow-ups:

```python
dingtalk_todo_closed = pull_dingtalk_todo_statuses(
    store,
    dws,
    now=_utc_now_string(),
)
```

Include the count in the printed maintenance summary:

```python
f"dingtalk_todos_closed={dingtalk_todo_closed}"
```

- [ ] **Step 6: Add operation log union**

In `AutoReplyStore._operation_logs_base_query`, add a `union all` branch:

```sql
select
    'dingtalk-todo:' || id as id,
    'work_todo_dingtalk_links' as source_table,
    id as source_id,
    updated_at as occurred_at,
    'DingTalk Todo' as category,
    dingtalk_task_id as action,
    status as status,
    'work_todo #' || work_todo_id || ' dingtalk #' || dingtalk_task_id as context,
    title_snapshot as summary,
    last_error as detail,
    '' as conversation_id,
    '' as message_id
from work_todo_dingtalk_links
```

- [ ] **Step 7: Render link state in task detail**

Add a store method:

```python
def list_work_todo_dingtalk_links_for_todos(
    self,
    todo_ids: list[int],
) -> dict[int, list[WorkTodoDingTalkLink]]:
    if not todo_ids:
        return {}
    placeholders = ",".join("?" for _ in todo_ids)
    with self._connect() as db:
        rows = db.execute(
            f"""
            select *
            from work_todo_dingtalk_links
            where work_todo_id in ({placeholders})
            order by id desc
            """,
            todo_ids,
        ).fetchall()
    result: dict[int, list[WorkTodoDingTalkLink]] = {}
    for row in rows:
        link = WorkTodoDingTalkLink.model_validate(dict(row))
        result.setdefault(link.work_todo_id, []).append(link)
    return result
```

In `app/audit_web.py`, fetch links for project task items and render a compact block under each task item:

```html
<div class="todo-dingtalk-link">
  <span class="pill">DingTalk Todo</span>
  <span>dt-task-1</span>
  <span>active</span>
  <span>Last pull: 2026-06-27 10:00:00</span>
</div>
```

Use existing pill styles where possible.

- [ ] **Step 8: Run tests and commit**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_task_store.py tests/test_audit_web.py -q`

Expected: PASS or only unrelated pre-existing failures. Investigate any failure touching modified code.

Commit:

```bash
git add app/cli.py app/audit_web.py app/store.py tests/test_cli.py tests/test_audit_web.py tests/test_task_store.py
git commit -m "feat: surface DingTalk todo sync status"
```

## Task 8: Final Verification And Runtime Restart

**Files:**
- Modify: `README.md`
- Test: focused and integration tests listed below

- [ ] **Step 1: Document the behavior**

Add a short subsection to README task summary docs:

```markdown
### DingTalk Todo sync

High-confidence internal `work_todos` can create DingTalk Todo tasks for the
owner. Internal task state remains the source of truth. The service periodically
pulls DingTalk Todo completion status, pushes strong internal completion
evidence back to DingTalk Todo, and checks linked DingTalk Todo state before
sending follow-up reminders.
```

- [ ] **Step 2: Run focused test suite**

Run:

```bash
.venv/bin/pytest \
  tests/test_task_store.py \
  tests/test_dws_client.py \
  tests/test_todo_sync.py \
  tests/test_task_agent.py \
  tests/test_follow_up.py \
  tests/test_cli.py \
  tests/test_audit_web.py \
  -q
```

Expected: PASS. If unrelated legacy tests fail, record exact failing tests and rerun the focused tests touched by this plan.

- [ ] **Step 3: Run compile check**

Run:

```bash
python -m compileall app/task_models.py app/store.py app/dws_client.py app/todo_sync.py app/task_agent.py app/follow_up.py app/cli.py app/audit_web.py
```

Expected: command exits 0.

- [ ] **Step 4: Commit docs and any final test fixes**

Commit:

```bash
git add README.md
git commit -m "docs: describe DingTalk todo sync"
```

If README was already committed with another task, skip this commit and mention that in the final implementation summary.

- [ ] **Step 5: Restart launchd service after runtime code is committed**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: service prints a current running process. If the command requires approval in Codex, request it because this is a service-affecting operation.

- [ ] **Step 6: Verify local audit web and backlog**

Run:

```bash
curl -sS http://127.0.0.1:8765/ | head -5
sqlite3 data/auto-reply.sqlite3 "
select status, count(*)
from work_summary_inputs
where status in ('failed', 'processing')
group by status;
"
```

Expected: the web endpoint returns HTML and there is no unresolved processing backlog caused by this change.

## Self-Review

Spec coverage:

- External link table: Task 1.
- DWS Todo create/get/done wrappers: Task 2.
- Create, pull, and push-completion sync: Task 3 and Task 5.
- Immediate creation after task-agent persistence: Task 4.
- Follow-up guard before reminder send: Task 6.
- Maintenance loop, UI, and operation logs: Task 7.
- Tests, docs, compile check, service restart: Task 8.

Placeholder scan:

- The plan intentionally uses the product term `Todo` and database term `work_todos`.
- No unfinished placeholders, unspecified validations, or deferred implementation steps are left.

Type consistency:

- The link model is named `WorkTodoDingTalkLink`.
- The table is named `work_todo_dingtalk_links`.
- Sync functions are consistently named `maybe_create_dingtalk_todo`, `pull_dingtalk_todo_statuses`, `sync_completed_todo_to_dingtalk`, and `refresh_dingtalk_todo_before_follow_up`.
