# Task Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local task summary system that turns processed conversations, new AI minutes, new `CEO_WORKSPACE` files, and targeted memory recall into durable company projects, project TODOs, follow-up drafts, and `/tasks` audit UI.

**Architecture:** Keep the existing CEO reply worker focused on message handling. Add a separate Work Item queue and task agent pipeline that uses BM25 project retrieval, optional DWS/memory context recovery, and validated JSON decisions to update projects and TODOs. Add daily scanners and follow-up sending as separate orchestration commands so they can be tested and deployed without changing reply delivery semantics.

**Tech Stack:** Python 3.11, SQLite through `app.store.AutoReplyStore`, Pydantic v2, Codex CLI runner pattern from `app.codex_decision`, DWS CLI wrapper from `app.dws_client`, FastAPI audit UI, pytest.

---

## Scope Check

The design spans several subsystems: data model, task-agent execution, conversation Work Items, AI minutes scanning, workspace-file scanning, follow-up delivery, setup checks, and UI. Implement them as incremental slices. The first four tasks produce usable local project persistence and task-agent decisions; later tasks add scanners, proactive follow-up, and UI.

Do not start execution on the existing dirty worktree without isolating implementation work. Existing `outputs/dingteam-okr-2026q2` changes are unrelated and must remain untouched.

## File Structure

- Create `app/task_models.py`: Pydantic models and enums for Work Items, projects, TODOs, task-agent decisions, follow-up drafts, and scan states.
- Modify `app/store.py`: SQLite schema and store methods for work projects, TODOs, updates, Work Item queue, agent runs, follow-up drafts, and scan state.
- Create `app/task_retrieval.py`: BM25 tokenization, project document rendering, and top-N project candidate retrieval.
- Create `app/task_agent.py`: task-agent prompt construction, Codex execution, decision parsing, and applying task decisions to store.
- Modify `app/worker.py`: enqueue a conversation Work Item after successful reply attempt handling without affecting reply status.
- Create `app/task_scanners.py`: daily AI minutes and local `CEO_WORKSPACE` file scanners that create Work Items.
- Create `app/follow_up.py`: due follow-up discovery, target selection, low-risk checks, delivery, and status updates.
- Create `app/memory_setup.py`: Codex/Claude memory-connector config doctor and setup helpers.
- Modify `app/cli.py`: commands for task-agent processing, daily scan, follow-up processing, and memory setup.
- Modify `app/audit_web.py`: `/tasks` pages for project list, project detail, review queue, and follow-up actions.
- Add tests under `tests/test_task_*.py` plus focused updates to existing worker, CLI, and audit web tests.

## Task 1: Task Models

**Files:**
- Create: `app/task_models.py`
- Test: `tests/test_task_models.py`

- [ ] **Step 1: Write model tests**

Create `tests/test_task_models.py`:

```python
import pytest
from pydantic import ValidationError

from app.task_models import (
    FollowUpDraftStatus,
    ProjectCategory,
    ProjectPriority,
    ProjectStatus,
    TaskAgentDecision,
    TodoStatus,
    WorkItem,
)


def test_work_item_keeps_input_small():
    item = WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "42",
                "title": "项目进展",
                "conversation_id": "cid-1",
                "conversation_title": "项目群",
                "created_at": "2026-06-07 09:00:00",
            },
            "summary": "客户交付项目今天确认 P0 风险，需要 owner 给 ETA。",
            "project_name": "客户交付项目",
            "context": {
                "sender": "Mina",
                "participants": ["Mina", "Derek"],
                "source_conversation_kind": "group",
                "source_conversation_title": "项目群",
            },
        }
    )

    payload = item.model_dump()
    assert "project_candidates" not in payload
    assert "todo_candidates" not in payload
    assert "facts" not in payload
    assert item.project_name == "客户交付项目"


def test_project_category_is_fixed_enum():
    assert ProjectCategory.MANAGEMENT.value == "management"
    assert ProjectCategory.HR.value == "HR"
    assert ProjectCategory.OTHER.value == "other"

    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate(
            {
                "action": "create_project",
                "project": {
                    "title": "x",
                    "category": "random",
                    "status": "active",
                },
                "todo_changes": [],
                "follow_up_drafts": [],
                "update_summary": "x",
                "merge_reason": "",
                "memory_recall_used": False,
                "confidence": 0.8,
            }
        )


def test_task_agent_decision_accepts_project_todo_and_follow_up():
    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "project": {
                "id": 7,
                "title": "售前知识库建设",
                "category": "sales",
                "tags": ["售前", "知识库"],
                "status": "active",
                "priority": "P1",
                "risk_level": "medium",
                "needs_derek_attention": True,
                "owner_user_id": "owner-1",
                "owner_name": "Alex",
                "related_people": [],
                "goal": "沉淀可复用售前材料",
                "background": "这是销售支持项目。",
                "facts": [
                    {
                        "description": "已确认放在 business/售前知识库。",
                        "source": "memory_recall",
                        "created": "2026-06-05",
                        "updated": "2026-06-07",
                    }
                ],
                "current_state": "正在整理来源材料。",
                "blocker": "",
                "next_step": "确认可复用摘要边界。",
                "next_follow_up_at": "2026-06-10 09:00:00",
                "follow_up_mode": "draft",
                "source_conversations": [],
            },
            "todo_changes": [
                {
                    "action": "create",
                    "todo_id": None,
                    "title": "补齐售前材料来源链接",
                    "owner_user_id": "owner-1",
                    "owner_name": "Alex",
                    "status": "open",
                    "priority": "P1",
                    "deadline_at": "2026-06-10 18:00:00",
                    "next_follow_up_at": "2026-06-10 09:00:00",
                    "follow_up_question": "现在来源链接补齐到哪一步了？",
                    "completion_evidence": None,
                    "blocker": "",
                }
            ],
            "follow_up_drafts": [
                {
                    "todo_id": None,
                    "owner_user_id": "owner-1",
                    "owner_name": "Alex",
                    "target_conversation_id": "cid-1",
                    "target_kind": "group",
                    "question_text": "售前材料来源链接现在补齐到哪一步了？",
                    "scheduled_at": "2026-06-10 09:00:00",
                    "risk_check": {"owner_in_group": True, "sensitive": False},
                }
            ],
            "update_summary": "新增 P1 跟进项。",
            "merge_reason": "项目名称、owner 和售前知识库事实一致。",
            "memory_recall_used": True,
            "confidence": 0.86,
        }
    )

    assert decision.project.category == ProjectCategory.SALES
    assert decision.project.priority == ProjectPriority.P1
    assert decision.project.status == ProjectStatus.ACTIVE
    assert decision.todo_changes[0].status == TodoStatus.OPEN
    assert decision.follow_up_drafts[0].status == FollowUpDraftStatus.DRAFT
```

- [ ] **Step 2: Run model tests and verify failure**

Run: `.venv/bin/pytest tests/test_task_models.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.task_models'`.

- [ ] **Step 3: Implement models**

Create `app/task_models.py`:

```python
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkItemSourceType(StrEnum):
    REPLY_ATTEMPT = "reply_attempt"
    AI_MINUTES = "ai_minutes"
    LOCAL_FILE = "local_file"
    MEMORY_RECALL = "memory_recall"


class WorkItemSourceKind(StrEnum):
    GROUP = "group"
    DIRECT = "direct"
    FILE = "file"
    MINUTES = "minutes"
    MEMORY = "memory"


class ProjectCategory(StrEnum):
    MANAGEMENT = "management"
    STRATEGY = "strategy"
    PROJECTS = "projects"
    MARKETING = "marketing"
    RESEARCH = "research"
    DEV = "dev"
    PRODUCT = "product"
    RECRUITING = "recruiting"
    SALES = "sales"
    FINANCE = "finance"
    ADMIN = "admin"
    HR = "HR"
    OTHER = "other"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    WAITING = "waiting"
    DONE = "done"
    ARCHIVED = "archived"


class ProjectPriority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    NONE = "none"


class RiskLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FollowUpMode(StrEnum):
    AUTO = "auto"
    DRAFT = "draft"
    NONE = "none"


class TodoStatus(StrEnum):
    OPEN = "open"
    WAITING_OWNER = "waiting_owner"
    DONE = "done"
    CANCELLED = "cancelled"


class FollowUpDraftStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkSummaryStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    DISCARDED = "discarded"


class WorkItemSource(BaseModel):
    type: WorkItemSourceType
    ref: str = ""
    title: str = ""
    conversation_id: str = ""
    conversation_title: str = ""
    created_at: str = ""


class WorkItemContext(BaseModel):
    sender: str = ""
    participants: list[str] = Field(default_factory=list)
    source_conversation_kind: WorkItemSourceKind
    source_conversation_title: str = ""


class WorkItem(BaseModel):
    source: WorkItemSource
    summary: str
    project_name: str = ""
    context: WorkItemContext


class ProjectFact(BaseModel):
    description: str
    source: str
    created: str = ""
    updated: str = ""


class TaskProjectPatch(BaseModel):
    id: int | None = None
    title: str = ""
    category: ProjectCategory = ProjectCategory.OTHER
    tags: list[str] = Field(default_factory=list)
    status: ProjectStatus = ProjectStatus.ACTIVE
    priority: ProjectPriority = ProjectPriority.NONE
    risk_level: RiskLevel = RiskLevel.NONE
    needs_derek_attention: bool = False
    owner_user_id: str = ""
    owner_name: str = ""
    related_people: list[dict[str, str]] = Field(default_factory=list)
    goal: str = ""
    background: str = ""
    facts: list[ProjectFact] = Field(default_factory=list)
    current_state: str = ""
    blocker: str = ""
    next_step: str = ""
    next_follow_up_at: str = ""
    follow_up_mode: FollowUpMode = FollowUpMode.NONE
    source_conversations: list[dict[str, Any]] = Field(default_factory=list)


class TodoChange(BaseModel):
    action: Literal["create", "update", "close", "cancel"]
    todo_id: int | None = None
    title: str = ""
    owner_user_id: str = ""
    owner_name: str = ""
    status: TodoStatus = TodoStatus.OPEN
    priority: ProjectPriority = ProjectPriority.NONE
    deadline_at: str = ""
    next_follow_up_at: str = ""
    follow_up_question: str = ""
    completion_evidence: dict[str, Any] | None = None
    blocker: str = ""


class FollowUpDraftDecision(BaseModel):
    todo_id: int | None = None
    owner_user_id: str = ""
    owner_name: str = ""
    target_conversation_id: str = ""
    target_kind: Literal["group", "direct"]
    question_text: str
    scheduled_at: str = ""
    risk_check: dict[str, Any] = Field(default_factory=dict)
    status: FollowUpDraftStatus = FollowUpDraftStatus.DRAFT


class TaskAgentDecision(BaseModel):
    action: Literal["discard", "create_project", "update_project"]
    discard_reason: str = ""
    project: TaskProjectPatch | None = None
    todo_changes: list[TodoChange] = Field(default_factory=list)
    follow_up_drafts: list[FollowUpDraftDecision] = Field(default_factory=list)
    update_summary: str = ""
    merge_reason: str = ""
    memory_recall_used: bool = False
    confidence: float = 0.0


class WorkProject(BaseModel):
    id: int
    title: str
    category: ProjectCategory
    tags_json: str = "[]"
    status: ProjectStatus
    priority: ProjectPriority
    risk_level: RiskLevel
    needs_derek_attention: bool = False
    owner_user_id: str = ""
    owner_name: str = ""
    related_people_json: str = "[]"
    goal: str = ""
    background: str = ""
    facts_json: str = "[]"
    current_state: str = ""
    blocker: str = ""
    next_step: str = ""
    next_follow_up_at: str = ""
    follow_up_mode: FollowUpMode = FollowUpMode.NONE
    source_conversations_json: str = "[]"
    memory_context_json: str = "{}"
    created_at: str
    updated_at: str
    last_activity_at: str = ""


class WorkTodo(BaseModel):
    id: int
    project_id: int
    title: str
    owner_user_id: str = ""
    owner_name: str = ""
    status: TodoStatus
    priority: ProjectPriority
    deadline_at: str = ""
    next_follow_up_at: str = ""
    follow_up_question: str = ""
    blocker: str = ""
    completion_evidence_json: str = "{}"
    created_from_update_id: int = 0
    created_at: str
    updated_at: str
    completed_at: str = ""


class WorkUpdate(BaseModel):
    id: int
    project_id: int
    source_type: str
    source_ref: str
    summary: str
    changes_json: str = "{}"
    merge_reason: str = ""
    confidence: float = 0.0
    created_at: str


class WorkSummaryInput(BaseModel):
    id: int
    source_type: WorkItemSourceType
    source_ref: str
    payload_json: str
    status: WorkSummaryStatus
    attempts: int = 0
    error: str = ""
    created_at: str
    updated_at: str


class TaskAgentRun(BaseModel):
    id: int
    summary_input_id: int
    codex_session_id: str = ""
    decision_json: str = "{}"
    audit_summary: str = ""
    memory_recall_used: bool = False
    created_at: str


class FollowUpDraft(BaseModel):
    id: int
    project_id: int
    todo_id: int = 0
    owner_user_id: str = ""
    owner_name: str = ""
    target_conversation_id: str = ""
    target_kind: str = ""
    question_text: str = ""
    risk_check_json: str = "{}"
    status: FollowUpDraftStatus
    send_result_json: str = "{}"
    scheduled_at: str = ""
    sent_at: str = ""
    created_at: str
```

- [ ] **Step 4: Run model tests and verify pass**

Run: `.venv/bin/pytest tests/test_task_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add app/task_models.py tests/test_task_models.py
git commit -m "Add task summary models"
```

## Task 2: SQLite Schema and Store Methods

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_task_store.py`

- [ ] **Step 1: Write store tests**

Create `tests/test_task_store.py`:

```python
import json
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
                "title": "项目消息",
                "conversation_id": "cid-1",
                "conversation_title": "项目群",
                "created_at": "2026-06-07 09:00:00",
            },
            "summary": "P1 项目需要三天内确认进展。",
            "project_name": "P1 项目",
            "context": {
                "sender": "Alex",
                "participants": ["Alex"],
                "source_conversation_kind": "group",
                "source_conversation_title": "项目群",
            },
        }
    )


def test_enqueue_and_claim_work_summary_input(tmp_path):
    store = _store(tmp_path)
    item = _work_item()

    inserted_id = store.enqueue_work_summary_input(
        source_type=item.source.type.value,
        source_ref=item.source.ref,
        payload_json=item.model_dump_json(),
    )
    duplicate_id = store.enqueue_work_summary_input(
        source_type=item.source.type.value,
        source_ref=item.source.ref,
        payload_json=item.model_dump_json(),
    )

    assert inserted_id > 0
    assert duplicate_id == inserted_id

    claimed = store.claim_work_summary_inputs(limit=1)
    second_claim = store.claim_work_summary_inputs(limit=1)

    assert len(claimed) == 1
    assert claimed[0].status == "processing"
    assert second_claim == []


def test_create_project_todo_update_and_follow_up(tmp_path):
    store = _store(tmp_path)

    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json=json.dumps(["售前"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        needs_derek_attention=True,
        owner_user_id="owner-1",
        owner_name="Alex",
        goal="沉淀售前材料",
        background="销售支持项目。",
        facts_json=json.dumps(
            [
                {
                    "description": "正式本地知识导入位置是 business/售前知识库。",
                    "source": "memory_recall",
                    "created": "2026-06-05",
                    "updated": "2026-06-07",
                }
            ],
            ensure_ascii=False,
        ),
        current_state="开始整理",
        next_step="补齐来源链接",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐来源链接",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P1",
        next_follow_up_at="2026-06-10 09:00:00",
    )
    update_id = store.create_work_update(
        project_id=project_id,
        source_type="reply_attempt",
        source_ref="1",
        summary="创建项目和行动项",
        changes_json=json.dumps({"created_todo_id": todo_id}),
        merge_reason="新项目信息明确",
        confidence=0.91,
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="来源链接补齐到哪一步了？",
        risk_check_json=json.dumps({"owner_in_group": True}),
        scheduled_at="2026-06-10 09:00:00",
    )

    project = store.get_work_project(project_id)
    assert project is not None
    assert project.title == "售前知识库建设"
    assert project.category == "sales"
    assert project.needs_derek_attention is True
    assert json.loads(project.facts_json)[0]["description"].startswith("正式本地")

    todos = store.list_work_todos(project_id=project_id)
    assert [todo.id for todo in todos] == [todo_id]

    updates = store.list_work_updates(project_id=project_id)
    assert [update.id for update in updates] == [update_id]

    drafts = store.list_follow_up_drafts(statuses=("draft",))
    assert [draft.id for draft in drafts] == [draft_id]


def test_scan_state_round_trip(tmp_path):
    store = _store(tmp_path)

    store.set_daily_scan_state(
        "local_files",
        last_success_at="2026-06-07T10:00:00+00:00",
        cursor_json='{"mtime": 123}',
        last_error="",
    )

    state = store.get_daily_scan_state("local_files")
    assert state is not None
    assert state["last_success_at"] == "2026-06-07T10:00:00+00:00"
    assert state["cursor_json"] == '{"mtime": 123}'
```

- [ ] **Step 2: Run store tests and verify failure**

Run: `.venv/bin/pytest tests/test_task_store.py -q`

Expected: FAIL because `enqueue_work_summary_input` is missing.

- [ ] **Step 3: Add imports and schema**

Modify the top of `app/store.py`:

```python
from app.task_models import (
    FollowUpDraft,
    TaskAgentRun,
    WorkProject,
    WorkSummaryInput,
    WorkTodo,
    WorkUpdate,
)
```

Add these tables inside `_initialize()` after `service_state`:

```sql
create table if not exists work_projects (
    id integer primary key autoincrement,
    title text not null,
    category text not null default 'other',
    tags_json text not null default '[]',
    status text not null default 'active',
    priority text not null default 'none',
    risk_level text not null default 'none',
    needs_derek_attention integer not null default 0,
    owner_user_id text not null default '',
    owner_name text not null default '',
    related_people_json text not null default '[]',
    goal text not null default '',
    background text not null default '',
    facts_json text not null default '[]',
    current_state text not null default '',
    blocker text not null default '',
    next_step text not null default '',
    next_follow_up_at text not null default '',
    follow_up_mode text not null default 'none',
    source_conversations_json text not null default '[]',
    memory_context_json text not null default '{}',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    last_activity_at text not null default current_timestamp
);
create index if not exists idx_work_projects_status_priority
    on work_projects(status, priority, updated_at);
create table if not exists work_todos (
    id integer primary key autoincrement,
    project_id integer not null,
    title text not null,
    owner_user_id text not null default '',
    owner_name text not null default '',
    status text not null default 'open',
    priority text not null default 'none',
    deadline_at text not null default '',
    next_follow_up_at text not null default '',
    follow_up_question text not null default '',
    blocker text not null default '',
    completion_evidence_json text not null default '{}',
    created_from_update_id integer not null default 0,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    completed_at text not null default ''
);
create index if not exists idx_work_todos_project_status
    on work_todos(project_id, status);
create index if not exists idx_work_todos_follow_up
    on work_todos(status, next_follow_up_at);
create table if not exists work_updates (
    id integer primary key autoincrement,
    project_id integer not null,
    source_type text not null,
    source_ref text not null,
    summary text not null,
    changes_json text not null default '{}',
    merge_reason text not null default '',
    confidence real not null default 0,
    created_at text not null default current_timestamp
);
create index if not exists idx_work_updates_project
    on work_updates(project_id, id);
create table if not exists work_summary_inputs (
    id integer primary key autoincrement,
    source_type text not null,
    source_ref text not null,
    payload_json text not null,
    status text not null default 'pending',
    attempts integer not null default 0,
    error text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    unique(source_type, source_ref)
);
create index if not exists idx_work_summary_inputs_status
    on work_summary_inputs(status, id);
create table if not exists task_agent_runs (
    id integer primary key autoincrement,
    summary_input_id integer not null,
    codex_session_id text not null default '',
    decision_json text not null default '{}',
    audit_summary text not null default '',
    memory_recall_used integer not null default 0,
    created_at text not null default current_timestamp
);
create index if not exists idx_task_agent_runs_input
    on task_agent_runs(summary_input_id, id);
create table if not exists follow_up_drafts (
    id integer primary key autoincrement,
    project_id integer not null,
    todo_id integer not null default 0,
    owner_user_id text not null default '',
    owner_name text not null default '',
    target_conversation_id text not null default '',
    target_kind text not null default '',
    question_text text not null default '',
    risk_check_json text not null default '{}',
    status text not null default 'draft',
    send_result_json text not null default '{}',
    scheduled_at text not null default '',
    sent_at text not null default '',
    created_at text not null default current_timestamp
);
create index if not exists idx_follow_up_drafts_status
    on follow_up_drafts(status, scheduled_at, id);
create table if not exists daily_scan_state (
    scanner_name text primary key,
    last_success_at text not null default '',
    cursor_json text not null default '{}',
    last_error text not null default '',
    updated_at text not null default current_timestamp
);
```

- [ ] **Step 4: Add store methods**

Append methods to `AutoReplyStore` before `record_error`:

```python
    def enqueue_work_summary_input(
        self,
        *,
        source_type: str,
        source_ref: str,
        payload_json: str,
    ) -> int:
        with self._connect() as db:
            db.execute(
                """
                insert into work_summary_inputs (source_type, source_ref, payload_json)
                values (?, ?, ?)
                on conflict(source_type, source_ref) do update set
                    payload_json=excluded.payload_json,
                    updated_at=current_timestamp
                """,
                (source_type, source_ref, payload_json),
            )
            row = db.execute(
                """
                select id from work_summary_inputs
                where source_type=? and source_ref=?
                """,
                (source_type, source_ref),
            ).fetchone()
            return int(row["id"])

    def claim_work_summary_inputs(self, limit: int) -> list[WorkSummaryInput]:
        if limit <= 0:
            return []
        with self._connect() as db:
            db.execute("begin immediate")
            rows = db.execute(
                """
                select *
                from work_summary_inputs
                where status='pending'
                order by id
                limit ?
                """,
                (limit,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            db.execute(
                f"""
                update work_summary_inputs
                set status='processing',
                    attempts=attempts + 1,
                    error='',
                    updated_at=current_timestamp
                where id in ({placeholders})
                """,
                ids,
            )
            claimed = db.execute(
                f"""
                select *
                from work_summary_inputs
                where id in ({placeholders})
                order by id
                """,
                ids,
            ).fetchall()
            return [WorkSummaryInput.model_validate(dict(row)) for row in claimed]

    def mark_work_summary_input_done(self, input_id: int) -> None:
        with self._connect() as db:
            db.execute(
                """
                update work_summary_inputs
                set status='done', error='', updated_at=current_timestamp
                where id=?
                """,
                (input_id,),
            )

    def mark_work_summary_input_discarded(self, input_id: int, reason: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update work_summary_inputs
                set status='discarded', error=?, updated_at=current_timestamp
                where id=?
                """,
                (reason, input_id),
            )

    def mark_work_summary_input_failed(self, input_id: int, error: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                update work_summary_inputs
                set status='failed', error=?, updated_at=current_timestamp
                where id=?
                """,
                (error, input_id),
            )

    def create_work_project(self, **values) -> int:
        keys = list(values.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._connect() as db:
            cursor = db.execute(
                f"insert into work_projects ({columns}) values ({placeholders})",
                [values[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def update_work_project(self, project_id: int, **values) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._connect() as db:
            db.execute(
                f"""
                update work_projects
                set {assignments},
                    updated_at=current_timestamp,
                    last_activity_at=current_timestamp
                where id=?
                """,
                [*values.values(), project_id],
            )

    def get_work_project(self, project_id: int) -> WorkProject | None:
        with self._connect() as db:
            row = db.execute(
                "select * from work_projects where id=?",
                (project_id,),
            ).fetchone()
            return None if row is None else WorkProject.model_validate(dict(row))

    def list_work_projects(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[WorkProject]:
        query = "select * from work_projects"
        args: list[str | int] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query = f"{query} where status in ({placeholders})"
            args.extend(statuses)
        query = f"{query} order by last_activity_at desc, id desc"
        if limit is not None:
            query = f"{query} limit ?"
            args.append(limit)
        with self._connect() as db:
            return [WorkProject.model_validate(dict(row)) for row in db.execute(query, args)]

    def create_work_todo(self, **values) -> int:
        keys = list(values.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._connect() as db:
            cursor = db.execute(
                f"insert into work_todos ({columns}) values ({placeholders})",
                [values[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def update_work_todo(self, todo_id: int, **values) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._connect() as db:
            db.execute(
                f"""
                update work_todos
                set {assignments}, updated_at=current_timestamp
                where id=?
                """,
                [*values.values(), todo_id],
            )

    def list_work_todos(
        self,
        *,
        project_id: int | None = None,
        statuses: tuple[str, ...] | None = None,
        due_before: str | None = None,
    ) -> list[WorkTodo]:
        query = "select * from work_todos"
        clauses: list[str] = []
        args: list[str | int] = []
        if project_id is not None:
            clauses.append("project_id=?")
            args.append(project_id)
        if statuses:
            clauses.append(f"status in ({','.join('?' for _ in statuses)})")
            args.extend(statuses)
        if due_before is not None:
            clauses.append("next_follow_up_at != '' and next_follow_up_at <= ?")
            args.append(due_before)
        if clauses:
            query = f"{query} where {' and '.join(clauses)}"
        query = f"{query} order by id"
        with self._connect() as db:
            return [WorkTodo.model_validate(dict(row)) for row in db.execute(query, args)]

    def create_work_update(self, **values) -> int:
        keys = list(values.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._connect() as db:
            cursor = db.execute(
                f"insert into work_updates ({columns}) values ({placeholders})",
                [values[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def list_work_updates(self, *, project_id: int, limit: int = 50) -> list[WorkUpdate]:
        with self._connect() as db:
            rows = db.execute(
                """
                select *
                from work_updates
                where project_id=?
                order by id desc
                limit ?
                """,
                (project_id, limit),
            ).fetchall()
            return [WorkUpdate.model_validate(dict(row)) for row in rows]

    def record_task_agent_run(
        self,
        *,
        summary_input_id: int,
        codex_session_id: str = "",
        decision_json: str = "{}",
        audit_summary: str = "",
        memory_recall_used: bool = False,
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into task_agent_runs (
                    summary_input_id,
                    codex_session_id,
                    decision_json,
                    audit_summary,
                    memory_recall_used
                )
                values (?, ?, ?, ?, ?)
                """,
                (
                    summary_input_id,
                    codex_session_id,
                    decision_json,
                    audit_summary,
                    int(memory_recall_used),
                ),
            )
            return int(cursor.lastrowid)

    def create_follow_up_draft(self, **values) -> int:
        keys = list(values.keys())
        columns = ", ".join(keys)
        placeholders = ", ".join("?" for _ in keys)
        with self._connect() as db:
            cursor = db.execute(
                f"insert into follow_up_drafts ({columns}) values ({placeholders})",
                [values[key] for key in keys],
            )
            return int(cursor.lastrowid)

    def update_follow_up_draft(self, draft_id: int, **values) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._connect() as db:
            db.execute(
                f"update follow_up_drafts set {assignments} where id=?",
                [*values.values(), draft_id],
            )

    def list_follow_up_drafts(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        due_before: str | None = None,
        limit: int = 200,
    ) -> list[FollowUpDraft]:
        query = "select * from follow_up_drafts"
        clauses: list[str] = []
        args: list[str | int] = []
        if statuses:
            clauses.append(f"status in ({','.join('?' for _ in statuses)})")
            args.extend(statuses)
        if due_before is not None:
            clauses.append("scheduled_at != '' and scheduled_at <= ?")
            args.append(due_before)
        if clauses:
            query = f"{query} where {' and '.join(clauses)}"
        query = f"{query} order by scheduled_at, id limit ?"
        args.append(limit)
        with self._connect() as db:
            return [FollowUpDraft.model_validate(dict(row)) for row in db.execute(query, args)]

    def set_daily_scan_state(
        self,
        scanner_name: str,
        *,
        last_success_at: str,
        cursor_json: str = "{}",
        last_error: str = "",
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into daily_scan_state (
                    scanner_name,
                    last_success_at,
                    cursor_json,
                    last_error
                )
                values (?, ?, ?, ?)
                on conflict(scanner_name) do update set
                    last_success_at=excluded.last_success_at,
                    cursor_json=excluded.cursor_json,
                    last_error=excluded.last_error,
                    updated_at=current_timestamp
                """,
                (scanner_name, last_success_at, cursor_json, last_error),
            )

    def get_daily_scan_state(self, scanner_name: str) -> dict[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                """
                select scanner_name, last_success_at, cursor_json, last_error
                from daily_scan_state
                where scanner_name=?
                """,
                (scanner_name,),
            ).fetchone()
            return None if row is None else dict(row)
```

- [ ] **Step 5: Run store tests and verify pass**

Run: `.venv/bin/pytest tests/test_task_store.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/store.py tests/test_task_store.py
git commit -m "Add task summary storage"
```

## Task 3: BM25 Project Retrieval

**Files:**
- Create: `app/task_retrieval.py`
- Test: `tests/test_task_retrieval.py`

- [ ] **Step 1: Write retrieval tests**

Create `tests/test_task_retrieval.py`:

```python
import json

from app.store import AutoReplyStore
from app.task_retrieval import retrieve_project_candidates


def test_retrieve_project_candidates_uses_summary_and_project_name(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    sales_project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        tags_json=json.dumps(["售前", "知识库"], ensure_ascii=False),
        status="active",
        priority="P1",
        risk_level="medium",
        background="复用售前材料和来源链接。",
        facts_json=json.dumps(
            [{"description": "材料放在 business/售前知识库", "source": "memory"}],
            ensure_ascii=False,
        ),
        current_state="正在整理",
    )
    store.create_work_project(
        title="招聘复盘",
        category="recruiting",
        tags_json=json.dumps(["招聘"], ensure_ascii=False),
        status="active",
        priority="P2",
        risk_level="low",
        background="候选人流程复盘。",
    )

    candidates = retrieve_project_candidates(
        store,
        summary="售前材料来源链接需要 owner 补齐",
        project_name="售前知识库",
        limit=3,
    )

    assert candidates[0].project.id == sales_project_id
    assert "business/售前知识库" in candidates[0].document
    assert candidates[0].score > 0
```

- [ ] **Step 2: Run retrieval tests and verify failure**

Run: `.venv/bin/pytest tests/test_task_retrieval.py -q`

Expected: FAIL with missing `app.task_retrieval`.

- [ ] **Step 3: Implement retrieval**

Create `app/task_retrieval.py`:

```python
import json
import math
import re
from dataclasses import dataclass

from app.store import AutoReplyStore
from app.task_models import WorkProject

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


@dataclass(frozen=True)
class ProjectCandidate:
    project: WorkProject
    score: float
    document: str


def tokenize(text: str) -> list[str]:
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(text)]


def project_document(project: WorkProject) -> str:
    parts = [
        project.title,
        project.category,
        project.tags_json,
        project.owner_name,
        project.goal,
        project.background,
        project.facts_json,
        project.current_state,
        project.blocker,
        project.next_step,
        project.source_conversations_json,
    ]
    return "\n".join(part for part in parts if part)


def retrieve_project_candidates(
    store: AutoReplyStore,
    *,
    summary: str,
    project_name: str = "",
    limit: int = 5,
) -> list[ProjectCandidate]:
    projects = store.list_work_projects(statuses=("active", "waiting"), limit=500)
    if not projects:
        return []
    query_terms = tokenize(f"{project_name}\n{summary}")
    if not query_terms:
        return []
    documents = [(project, project_document(project)) for project in projects]
    tokenized_docs = [(project, document, tokenize(document)) for project, document in documents]
    doc_count = len(tokenized_docs)
    doc_freq: dict[str, int] = {}
    for _, _, tokens in tokenized_docs:
        for token in set(tokens):
            doc_freq[token] = doc_freq.get(token, 0) + 1
    avg_len = sum(len(tokens) for _, _, tokens in tokenized_docs) / max(doc_count, 1)
    results: list[ProjectCandidate] = []
    for project, document, tokens in tokenized_docs:
        if not tokens:
            continue
        token_counts: dict[str, int] = {}
        for token in tokens:
            token_counts[token] = token_counts.get(token, 0) + 1
        score = 0.0
        for term in query_terms:
            tf = token_counts.get(term, 0)
            if tf == 0:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            length_norm = 1 - 0.75 + 0.75 * (len(tokens) / max(avg_len, 1))
            score += idf * ((tf * 2.2) / (tf + 1.2 * length_norm))
        if score > 0:
            results.append(ProjectCandidate(project=project, score=score, document=document))
    return sorted(results, key=lambda candidate: candidate.score, reverse=True)[:limit]


def render_candidate_prompt(candidates: list[ProjectCandidate]) -> str:
    payload = [
        {
            "id": candidate.project.id,
            "score": round(candidate.score, 4),
            "title": candidate.project.title,
            "category": candidate.project.category,
            "tags": json.loads(candidate.project.tags_json or "[]"),
            "owner_name": candidate.project.owner_name,
            "goal": candidate.project.goal,
            "background": candidate.project.background,
            "facts": json.loads(candidate.project.facts_json or "[]"),
            "current_state": candidate.project.current_state,
            "blocker": candidate.project.blocker,
            "next_step": candidate.project.next_step,
            "source_conversations": json.loads(
                candidate.project.source_conversations_json or "[]"
            ),
        }
        for candidate in candidates
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run retrieval tests and verify pass**

Run: `.venv/bin/pytest tests/test_task_retrieval.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add app/task_retrieval.py tests/test_task_retrieval.py
git commit -m "Add task project retrieval"
```

## Task 4: Task Agent Runner and Decision Application

**Files:**
- Create: `app/task_agent.py`
- Test: `tests/test_task_agent.py`

- [ ] **Step 1: Write task-agent tests**

Create `tests/test_task_agent.py`:

```python
import json

from app.store import AutoReplyStore
from app.task_agent import TaskAgentRunner, apply_task_agent_decision, process_work_item
from app.task_models import TaskAgentDecision, WorkItem


class FakeCodex:
    last_session_id = "task-session-1"
    last_transcript_start_line = 1
    last_transcript_end_line = 10

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def decide(self, *, prompt, session_id=None):
        self.prompts.append(prompt)
        return TaskAgentDecision.model_validate(self.payload)


def _work_item(project_name="售前知识库"):
    return WorkItem.model_validate(
        {
            "source": {
                "type": "reply_attempt",
                "ref": "1",
                "title": "售前推进",
                "conversation_id": "cid-1",
                "conversation_title": "售前群",
                "created_at": "2026-06-07 09:00:00",
            },
            "summary": "售前知识库需要补齐来源链接，owner 是 Alex。",
            "project_name": project_name,
            "context": {
                "sender": "Mina",
                "participants": ["Alex"],
                "source_conversation_kind": "group",
                "source_conversation_title": "售前群",
            },
        }
    )


def test_process_work_item_creates_project_todo_update_and_run(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    item = _work_item()
    input_id = store.enqueue_work_summary_input(
        source_type=item.source.type.value,
        source_ref=item.source.ref,
        payload_json=item.model_dump_json(),
    )
    work_input = store.claim_work_summary_inputs(limit=1)[0]
    codex = FakeCodex(
        {
            "action": "create_project",
            "project": {
                "title": "售前知识库建设",
                "category": "sales",
                "tags": ["售前"],
                "status": "active",
                "priority": "P1",
                "risk_level": "medium",
                "needs_derek_attention": False,
                "owner_user_id": "owner-1",
                "owner_name": "Alex",
                "related_people": [],
                "goal": "沉淀售前材料",
                "background": "售前知识库项目。",
                "facts": [
                    {
                        "description": "需要补齐来源链接。",
                        "source": "reply_attempt:1",
                        "created": "2026-06-07",
                        "updated": "2026-06-07",
                    }
                ],
                "current_state": "已识别来源链接缺口。",
                "blocker": "",
                "next_step": "Alex 补齐来源链接。",
                "next_follow_up_at": "2026-06-10 09:00:00",
                "follow_up_mode": "draft",
                "source_conversations": [{"conversation_id": "cid-1", "title": "售前群"}],
            },
            "todo_changes": [
                {
                    "action": "create",
                    "title": "补齐来源链接",
                    "owner_user_id": "owner-1",
                    "owner_name": "Alex",
                    "status": "open",
                    "priority": "P1",
                    "next_follow_up_at": "2026-06-10 09:00:00",
                    "follow_up_question": "来源链接现在补齐到哪一步了？",
                    "completion_evidence": None,
                    "blocker": "",
                }
            ],
            "follow_up_drafts": [],
            "update_summary": "创建售前知识库项目。",
            "merge_reason": "无现有项目匹配，且事项名称稳定。",
            "memory_recall_used": True,
            "confidence": 0.9,
        }
    )

    process_work_item(store, TaskAgentRunner(codex), work_input)

    projects = store.list_work_projects()
    assert len(projects) == 1
    assert projects[0].title == "售前知识库建设"
    assert store.list_work_todos(project_id=projects[0].id)[0].title == "补齐来源链接"
    assert store.list_work_updates(project_id=projects[0].id)[0].summary == "创建售前知识库项目。"
    assert store.claim_work_summary_inputs(limit=1) == []
    assert "memory_recall" in codex.prompts[0]
    assert "候选项目" in codex.prompts[0]


def test_apply_decision_closes_todo_with_completion_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给出交付 ETA",
        status="open",
        priority="P0",
    )
    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "project": {"id": project_id, "title": "客户交付", "category": "projects"},
            "todo_changes": [
                {
                    "action": "close",
                    "todo_id": todo_id,
                    "title": "给出交付 ETA",
                    "status": "done",
                    "completion_evidence": {
                        "source": "ai_minutes:minutes-1",
                        "summary": "会议纪要明确 ETA 已发送客户。",
                        "confidence": 0.93,
                    },
                }
            ],
            "follow_up_drafts": [],
            "update_summary": "关闭 ETA 待办。",
            "merge_reason": "同一客户交付项目。",
            "memory_recall_used": False,
            "confidence": 0.93,
        }
    )

    apply_task_agent_decision(
        store,
        summary_input_id=0,
        work_item=_work_item("客户交付"),
        decision=decision,
        codex_session_id="session-1",
    )

    todo = store.list_work_todos(project_id=project_id)[0]
    assert todo.status == "done"
    assert "ETA 已发送客户" in todo.completion_evidence_json
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_task_agent.py -q`

Expected: FAIL with missing `app.task_agent`.

- [ ] **Step 3: Implement task agent runner**

Create `app/task_agent.py`:

```python
import json
from typing import Protocol

from app.store import AutoReplyStore
from app.task_models import (
    FollowUpDraftDecision,
    TaskAgentDecision,
    TodoChange,
    WorkItem,
    WorkSummaryInput,
)
from app.task_retrieval import render_candidate_prompt, retrieve_project_candidates


class TaskCodex(Protocol):
    last_session_id: str | None
    last_transcript_start_line: int
    last_transcript_end_line: int

    def decide(self, *, prompt: str, session_id: str | None = None) -> TaskAgentDecision:
        ...


class TaskAgentRunner:
    def __init__(self, codex: TaskCodex):
        self.codex = codex

    def decide(
        self,
        *,
        work_item: WorkItem,
        candidate_prompt: str,
    ) -> TaskAgentDecision:
        return self.codex.decide(
            prompt=build_task_agent_prompt(
                work_item=work_item,
                candidate_prompt=candidate_prompt,
            ),
            session_id=None,
        )


def build_task_agent_prompt(*, work_item: WorkItem, candidate_prompt: str) -> str:
    return f"""
你是 CEO Agent 的 task agent。你只负责公司管理事项、业务项目和重要事项的项目/TODO 更新，不负责回复当前消息。

规则:
- Work Item 只是输入片段，不是预先抽好的项目更新。
- 候选项目来自 BM25，只是初始线索，不是唯一依据。
- 如果候选为空或你判断候选不匹配，可以自主使用 dws 补充来源对话、引用消息、相关群上下文、AI 听记详情。
- 如果历史背景不清楚，可以用 memory_recall 检索项目名、owner、标签、关键词或相似历史事项。
- 新增项目前，在 memory_connector 可用时必须使用 memory_recall 回忆历史背景；调用 memory 工具时不要传 user_id。
- 如果上下文仍不足以稳定命名项目，但事项重要，生成 follow_up_draft 询问项目/目标/owner，不要新建模糊项目。
- 根据明确完成证据自动关闭 TODO，并写 completion_evidence。
- P0 追问结果、阻塞、ETA；P1 追问进展、风险、下一步；P2 轻量确认。

Work Item:
{work_item.model_dump_json(indent=2)}

候选项目:
{candidate_prompt}

只输出符合 TaskAgentDecision JSON schema 的 JSON。
""".strip()


def process_work_item(
    store: AutoReplyStore,
    runner: TaskAgentRunner,
    work_input: WorkSummaryInput,
) -> None:
    work_item = WorkItem.model_validate_json(work_input.payload_json)
    candidates = retrieve_project_candidates(
        store,
        summary=work_item.summary,
        project_name=work_item.project_name,
    )
    decision = runner.decide(
        work_item=work_item,
        candidate_prompt=render_candidate_prompt(candidates),
    )
    session_id = getattr(runner.codex, "last_session_id", "") or ""
    apply_task_agent_decision(
        store,
        summary_input_id=work_input.id,
        work_item=work_item,
        decision=decision,
        codex_session_id=session_id,
    )
    if decision.action == "discard":
        store.mark_work_summary_input_discarded(
            work_input.id,
            decision.discard_reason or decision.update_summary,
        )
    else:
        store.mark_work_summary_input_done(work_input.id)


def apply_task_agent_decision(
    store: AutoReplyStore,
    *,
    summary_input_id: int,
    work_item: WorkItem,
    decision: TaskAgentDecision,
    codex_session_id: str = "",
) -> int | None:
    store.record_task_agent_run(
        summary_input_id=summary_input_id,
        codex_session_id=codex_session_id,
        decision_json=decision.model_dump_json(),
        audit_summary=decision.update_summary,
        memory_recall_used=decision.memory_recall_used,
    )
    if decision.action == "discard":
        return None
    if decision.project is None:
        raise ValueError("task agent decision requires project for create/update")
    project_id = _apply_project(store, decision)
    update_id = store.create_work_update(
        project_id=project_id,
        source_type=work_item.source.type.value,
        source_ref=work_item.source.ref,
        summary=decision.update_summary,
        changes_json=decision.model_dump_json(),
        merge_reason=decision.merge_reason,
        confidence=decision.confidence,
    )
    for change in decision.todo_changes:
        _apply_todo_change(store, project_id, update_id, change)
    for draft in decision.follow_up_drafts:
        _create_follow_up_draft(store, project_id, draft)
    return project_id


def _project_values(decision: TaskAgentDecision) -> dict[str, object]:
    project = decision.project
    assert project is not None
    return {
        "title": project.title,
        "category": project.category.value,
        "tags_json": json.dumps(project.tags, ensure_ascii=False),
        "status": project.status.value,
        "priority": project.priority.value,
        "risk_level": project.risk_level.value,
        "needs_derek_attention": int(project.needs_derek_attention),
        "owner_user_id": project.owner_user_id,
        "owner_name": project.owner_name,
        "related_people_json": json.dumps(project.related_people, ensure_ascii=False),
        "goal": project.goal,
        "background": project.background,
        "facts_json": json.dumps(
            [fact.model_dump() for fact in project.facts],
            ensure_ascii=False,
        ),
        "current_state": project.current_state,
        "blocker": project.blocker,
        "next_step": project.next_step,
        "next_follow_up_at": project.next_follow_up_at,
        "follow_up_mode": project.follow_up_mode.value,
        "source_conversations_json": json.dumps(
            project.source_conversations,
            ensure_ascii=False,
        ),
    }


def _apply_project(store: AutoReplyStore, decision: TaskAgentDecision) -> int:
    project = decision.project
    assert project is not None
    values = _project_values(decision)
    if decision.action == "create_project":
        return store.create_work_project(**values)
    if project.id is None:
        raise ValueError("update_project requires project.id")
    store.update_work_project(project.id, **values)
    return project.id


def _apply_todo_change(
    store: AutoReplyStore,
    project_id: int,
    update_id: int,
    change: TodoChange,
) -> None:
    values = {
        "title": change.title,
        "owner_user_id": change.owner_user_id,
        "owner_name": change.owner_name,
        "status": change.status.value,
        "priority": change.priority.value,
        "deadline_at": change.deadline_at,
        "next_follow_up_at": change.next_follow_up_at,
        "follow_up_question": change.follow_up_question,
        "blocker": change.blocker,
        "completion_evidence_json": json.dumps(
            change.completion_evidence or {},
            ensure_ascii=False,
        ),
    }
    if change.action == "create":
        store.create_work_todo(
            project_id=project_id,
            created_from_update_id=update_id,
            **values,
        )
        return
    if change.todo_id is None:
        raise ValueError(f"{change.action} requires todo_id")
    if change.action == "close":
        values["status"] = "done"
    if change.action == "cancel":
        values["status"] = "cancelled"
    store.update_work_todo(change.todo_id, **values)


def _create_follow_up_draft(
    store: AutoReplyStore,
    project_id: int,
    draft: FollowUpDraftDecision,
) -> None:
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=draft.todo_id or 0,
        owner_user_id=draft.owner_user_id,
        owner_name=draft.owner_name,
        target_conversation_id=draft.target_conversation_id,
        target_kind=draft.target_kind,
        question_text=draft.question_text,
        risk_check_json=json.dumps(draft.risk_check, ensure_ascii=False),
        status=draft.status.value,
        scheduled_at=draft.scheduled_at,
    )
```

- [ ] **Step 4: Run task-agent tests and verify pass**

Run: `.venv/bin/pytest tests/test_task_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add app/task_agent.py tests/test_task_agent.py
git commit -m "Add task agent decision pipeline"
```

## Task 5: CLI Command to Process Work Items

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add CLI parser test**

Append to `tests/test_cli.py`:

```python
def test_parser_supports_process_work_items():
    from app.cli import build_parser

    args = build_parser().parse_args(["process-work-items", "--max-batches", "3"])

    assert args.command == "process-work-items"
    assert args.max_batches == 3
```

- [ ] **Step 2: Run parser test and verify failure**

Run: `.venv/bin/pytest tests/test_cli.py::test_parser_supports_process_work_items -q`

Expected: FAIL because `process-work-items` is not a known command.

- [ ] **Step 3: Add command to parser and dispatcher**

In `app/cli.py`, add `"process-work-items"` to the shared command tuple in `build_parser()`.

Add imports near existing imports:

```python
from app.task_agent import TaskAgentCodexRunner, TaskAgentRunner, process_work_item
```

In `app/task_agent.py`, add a dedicated Codex runner below `TaskAgentRunner`:

```python
class TaskAgentCodexRunner:
    def __init__(
        self,
        workspace: Path,
        codex_bin: str = "codex",
        executor=None,
        timeout_seconds: int = 420,
        idle_timeout_seconds: int = 180,
    ):
        from app.codex_decision import (
            extract_codex_audit_events,
            extract_codex_session_id,
            _subprocess_failure_reason,
        )
        from app.codex_runner import CodexRunner
        from app.process_runner import run_process_with_idle_timeout

        self.workspace = workspace
        self.runner = CodexRunner(workspace=workspace, codex_bin=codex_bin)
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self._run_process_with_idle_timeout = run_process_with_idle_timeout
        self._extract_codex_session_id = extract_codex_session_id
        self._extract_codex_audit_events = extract_codex_audit_events
        self._subprocess_failure_reason = _subprocess_failure_reason
        self.last_session_id: str | None = None
        self.last_audit_tool_events: list[dict[str, str]] = []
        self.last_transcript_start_line = 0
        self.last_transcript_end_line = 0

    def decide(self, *, prompt: str, session_id: str | None = None) -> TaskAgentDecision:
        raw = self._execute(prompt=prompt, session_id=session_id)
        self.last_session_id = self._extract_codex_session_id(raw) or session_id
        self.last_audit_tool_events = self._extract_codex_audit_events(raw)
        return _parse_task_agent_decision(raw)

    def _execute(self, *, prompt: str, session_id: str | None) -> str:
        command = self.runner.build_command(prompt, session_id, image_paths=None)
        if self.executor is not None:
            return self.executor(command, prompt)
        completed = self._run_process_with_idle_timeout(
            command,
            input_text=prompt,
            timeout_seconds=self.timeout_seconds,
            idle_timeout_seconds=self.idle_timeout_seconds,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                self._subprocess_failure_reason(completed.stderr, completed.stdout)
            )
        return completed.stdout


def _task_decision_text_candidates(payload):
    candidates = []
    if isinstance(payload, dict):
        for key in ("message", "last_agent_message", "content"):
            value = payload.get(key)
            if isinstance(value, str):
                candidates.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        candidates.append(item["text"])
        item = payload.get("item")
        if isinstance(item, dict):
            candidates.extend(_task_decision_text_candidates(item))
        nested = payload.get("payload")
        if isinstance(nested, dict):
            candidates.extend(_task_decision_text_candidates(nested))
    return candidates


def _parse_task_agent_decision(raw: str) -> TaskAgentDecision:
    try:
        return TaskAgentDecision.model_validate_json(raw.strip())
    except ValueError:
        pass
    payloads = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    for payload in reversed(payloads):
        try:
            return TaskAgentDecision.model_validate(payload)
        except ValueError:
            pass
        for text in _task_decision_text_candidates(payload):
            try:
                return TaskAgentDecision.model_validate_json(text)
            except ValueError:
                continue
    raise ValueError("No TaskAgentDecision JSON found")
```

Also add imports at the top of `app/task_agent.py`:

```python
from pathlib import Path
import json
```

Add this test to `tests/test_task_agent.py`:

```python
def test_task_agent_codex_runner_parses_jsonl_payload(tmp_path):
    from app.task_agent import TaskAgentCodexRunner

    def executor(command, prompt):
        return (
            '{"type":"session_meta","payload":{"id":"session-task-1"}}\n'
            '{"item":{"type":"agent_message","text":"'
            '{\\"action\\":\\"discard\\",'
            '\\"discard_reason\\":\\"没有状态变化\\",'
            '\\"todo_changes\\":[],'
            '\\"follow_up_drafts\\":[],'
            '\\"update_summary\\":\\"无变化\\",'
            '\\"merge_reason\\":\\"\\",'
            '\\"memory_recall_used\\":false,'
            '\\"confidence\\":0.7}'
            '"}}\n'
        )

    runner = TaskAgentCodexRunner(workspace=tmp_path, executor=executor)
    decision = runner.decide(prompt="x")

    assert decision.action == "discard"
    assert runner.last_session_id == "session-task-1"
```

Add command function:

```python
def process_work_items_command(settings: WorkerSettings) -> int:
    store = AutoReplyStore(settings.db_path)
    limit = settings.max_batches or 20
    runner = TaskAgentRunner(
        TaskAgentCodexRunner(
            workspace=settings.workspace,
            timeout_seconds=settings.codex_timeout_seconds,
            idle_timeout_seconds=settings.codex_idle_timeout_seconds,
        )
    )
    processed = 0
    for work_input in store.claim_work_summary_inputs(limit=limit):
        try:
            process_work_item(store, runner, work_input)
            processed += 1
        except Exception as exc:
            store.mark_work_summary_input_failed(work_input.id, str(exc))
            store.record_error(None, None, "task_agent", str(exc))
    print(f"process-work-items processed={processed}", flush=True)
    return processed
```

In `main()` dispatch, add:

```python
    elif args.command == "process-work-items":
        process_work_items_command(settings)
```

- [ ] **Step 4: Run parser test**

Run: `.venv/bin/pytest tests/test_cli.py::test_parser_supports_process_work_items -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add app/cli.py tests/test_cli.py
git commit -m "Add work item processing command"
```

## Task 6: Conversation Work Item Enqueue

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Add worker test**

Append to `tests/test_worker.py` using existing fake worker fixtures. If the file already has a `make_worker` helper, reuse it; otherwise create the minimal setup shown here:

```python
def test_sent_reply_enqueues_conversation_work_item(tmp_path):
    from app.codex_decision import append_signature
    from app.dingtalk_models import DingTalkConversation, DingTalkMessage
    from app.store import AutoReplyStore
    from app.worker import DingTalkAutoReplyWorker

    class FakeDws:
        def read_recent_messages(self, conversation):
            return []

        def read_unread_messages(self, conversation):
            return []

        def send_reply_to_trigger(self, conversation, trigger, text):
            return {"ok": True}

    class FakeCodex:
        last_session_id = "session-1"
        last_transcript_start_line = 1
        last_transcript_end_line = 2
        last_audit_tool_events = []

        def decide(self, **kwargs):
            from app.dingtalk_models import CodexDecision

            return CodexDecision.model_validate(
                {
                    "action": "send_reply",
                    "reply_text": "请 Alex 三天内给进展。",
                    "reason": "形成项目推进要求。",
                    "sensitivity_kind": "general",
                    "audit_documents": [],
                    "audit_summary": "上下文形成 P1 项目进展要求。",
                }
            )

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    worker = DingTalkAutoReplyWorker(store=store, dws=FakeDws(), codex=FakeCodex())
    conversation = DingTalkConversation(
        open_conversation_id="cid-1",
        title="项目群",
        single_chat=False,
        unread_point=1,
    )
    message = DingTalkMessage(
        open_conversation_id="cid-1",
        open_message_id="mid-1",
        conversation_title="项目群",
        single_chat=False,
        sender_name="Mina",
        sender_user_id="user-1",
        create_time="2026-06-07 09:00:00",
        content="@Derek 这个项目需要 Alex 三天内给进展",
    )

    worker._process_batch(conversation, [message], [], ignore_existing_attempt=True)

    claimed = store.claim_work_summary_inputs(limit=1)
    assert len(claimed) == 1
    assert claimed[0].source_type == "reply_attempt"
    assert "Alex 三天内给进展" in claimed[0].payload_json
    assert append_signature("请 Alex 三天内给进展。") in store.list_reply_attempts()[0].final_reply_text
```

- [ ] **Step 2: Run worker test and verify failure**

Run: `.venv/bin/pytest tests/test_worker.py::test_sent_reply_enqueues_conversation_work_item -q`

Expected: FAIL because no Work Item is enqueued.

- [ ] **Step 3: Add Work Item helper**

In `app/worker.py`, import:

```python
from app.task_models import WorkItem
```

Add method to `DingTalkAutoReplyWorker`:

```python
    def _enqueue_conversation_work_item(
        self,
        *,
        attempt_id: int,
        conversation: DingTalkConversation,
        trigger: DingTalkMessage,
        action: str,
        audit_summary: str,
        final_reply_text: str,
    ) -> None:
        summary_parts = [
            trigger.content.strip(),
            audit_summary.strip(),
            final_reply_text.strip(),
        ]
        summary = "\n".join(part for part in summary_parts if part)
        if not summary.strip():
            return
        project_name = conversation.title.strip() if not conversation.single_chat else ""
        item = WorkItem.model_validate(
            {
                "source": {
                    "type": "reply_attempt",
                    "ref": str(attempt_id),
                    "title": conversation.title,
                    "conversation_id": conversation.open_conversation_id,
                    "conversation_title": conversation.title,
                    "created_at": trigger.create_time,
                },
                "summary": summary,
                "project_name": project_name,
                "context": {
                    "sender": trigger.sender_name,
                    "participants": [trigger.sender_name],
                    "source_conversation_kind": "direct" if conversation.single_chat else "group",
                    "source_conversation_title": conversation.title,
                },
            }
        )
        self.store.enqueue_work_summary_input(
            source_type=item.source.type.value,
            source_ref=item.source.ref,
            payload_json=item.model_dump_json(),
        )
```

Call `_enqueue_conversation_work_item()` after successful send, handoff, calendar commentary, and skipped no-reply where `audit_summary` indicates a business state change. For the first implementation, add it after `_send_reply()` succeeds in `_deliver_final_reply()` because it covers normal sent replies.

Inside `_deliver_final_reply()` after `record_sent_reply(...)`, add:

```python
        attempt = self.store.get_reply_attempt(attempt_id)
        if attempt is not None:
            self._enqueue_conversation_work_item(
                attempt_id=attempt_id,
                conversation=conversation,
                trigger=trigger,
                action=attempt.action,
                audit_summary=attempt.audit_summary,
                final_reply_text=attempt.final_reply_text,
            )
```

- [ ] **Step 4: Run worker test**

Run: `.venv/bin/pytest tests/test_worker.py::test_sent_reply_enqueues_conversation_work_item -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add app/worker.py tests/test_worker.py
git commit -m "Enqueue task work items from replies"
```

## Task 7: Daily Scanners for AI Minutes and Local Files

**Files:**
- Create: `app/task_scanners.py`
- Modify: `app/cli.py`
- Test: `tests/test_task_scanners.py`, `tests/test_cli.py`

- [ ] **Step 1: Write scanner tests**

Create `tests/test_task_scanners.py`:

```python
from pathlib import Path

from app.store import AutoReplyStore
from app.task_scanners import scan_local_workspace_files


def test_scan_local_files_only_under_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inside = workspace / "management.md"
    inside.write_text("P1 项目需要三天内确认进展", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("不应该扫描", encoding="utf-8")
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    count = scan_local_workspace_files(
        store,
        workspace=workspace,
        include_globs=("*.md",),
        exclude_globs=(),
    )

    assert count == 1
    claimed = store.claim_work_summary_inputs(limit=10)
    assert len(claimed) == 1
    assert str(inside) in claimed[0].source_ref
    assert str(outside) not in claimed[0].payload_json


def test_scan_local_files_rejects_workspace_outside_root(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    missing = tmp_path / "missing"

    count = scan_local_workspace_files(store, workspace=missing)

    assert count == 0
    assert store.get_daily_scan_state("local_files")["last_error"].startswith("workspace missing")
```

- [ ] **Step 2: Run scanner tests and verify failure**

Run: `.venv/bin/pytest tests/test_task_scanners.py -q`

Expected: FAIL because `app.task_scanners` is missing.

- [ ] **Step 3: Implement scanners**

Create `app/task_scanners.py`:

```python
import fnmatch
import json
from datetime import datetime, timezone
from pathlib import Path

from app.store import AutoReplyStore
from app.task_models import WorkItem

LOCAL_FILE_SCANNER = "local_files"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _matches_any(path: Path, patterns: tuple[str, ...]) -> bool:
    text = str(path)
    name = path.name
    return any(fnmatch.fnmatch(text, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _read_text_excerpt(path: Path, limit: int = 6000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""
    return text[:limit]


def scan_local_workspace_files(
    store: AutoReplyStore,
    *,
    workspace: Path,
    include_globs: tuple[str, ...] = ("*.md", "*.txt"),
    exclude_globs: tuple[str, ...] = (),
) -> int:
    workspace = workspace.expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        store.set_daily_scan_state(
            LOCAL_FILE_SCANNER,
            last_success_at="",
            cursor_json="{}",
            last_error=f"workspace missing: {workspace}",
        )
        return 0
    state = store.get_daily_scan_state(LOCAL_FILE_SCANNER) or {}
    cursor = json.loads(state.get("cursor_json") or "{}")
    previous_mtime = float(cursor.get("max_mtime", 0))
    max_mtime = previous_mtime
    count = 0
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not str(resolved).startswith(str(workspace)):
            continue
        if exclude_globs and _matches_any(resolved, exclude_globs):
            continue
        if include_globs and not _matches_any(resolved, include_globs):
            continue
        mtime = resolved.stat().st_mtime
        max_mtime = max(max_mtime, mtime)
        if mtime <= previous_mtime:
            continue
        excerpt = _read_text_excerpt(resolved)
        if not excerpt.strip():
            continue
        item = WorkItem.model_validate(
            {
                "source": {
                    "type": "local_file",
                    "ref": str(resolved),
                    "title": resolved.name,
                    "created_at": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
                },
                "summary": excerpt,
                "project_name": resolved.stem,
                "context": {
                    "sender": "",
                    "participants": [],
                    "source_conversation_kind": "file",
                    "source_conversation_title": resolved.name,
                },
            }
        )
        store.enqueue_work_summary_input(
            source_type=item.source.type.value,
            source_ref=item.source.ref,
            payload_json=item.model_dump_json(),
        )
        count += 1
    store.set_daily_scan_state(
        LOCAL_FILE_SCANNER,
        last_success_at=_utc_now(),
        cursor_json=json.dumps({"max_mtime": max_mtime}, sort_keys=True),
        last_error="",
    )
    return count
```

Implement AI minutes scanner in the same file after local files. Use DWS methods if available and keep it adapter-based:

```python
def scan_ai_minutes(store: AutoReplyStore, dws) -> int:
    list_minutes = getattr(dws, "list_minutes", None)
    if list_minutes is None:
        store.set_daily_scan_state(
            "ai_minutes",
            last_success_at="",
            cursor_json="{}",
            last_error="dws list_minutes unavailable",
        )
        return 0
    count = 0
    for minutes in list_minutes():
        minutes_id = str(minutes.get("taskUuid") or minutes.get("minutesId") or "")
        if not minutes_id:
            continue
        title = str(minutes.get("title") or f"AI minutes {minutes_id}")
        summary = json.dumps(minutes, ensure_ascii=False)
        item = WorkItem.model_validate(
            {
                "source": {
                    "type": "ai_minutes",
                    "ref": minutes_id,
                    "title": title,
                    "created_at": str(minutes.get("createdAt") or ""),
                },
                "summary": summary,
                "project_name": title,
                "context": {
                    "sender": "",
                    "participants": [],
                    "source_conversation_kind": "minutes",
                    "source_conversation_title": title,
                },
            }
        )
        store.enqueue_work_summary_input(
            source_type=item.source.type.value,
            source_ref=item.source.ref,
            payload_json=item.model_dump_json(),
        )
        count += 1
    store.set_daily_scan_state("ai_minutes", last_success_at=_utc_now(), cursor_json="{}", last_error="")
    return count
```

- [ ] **Step 4: Add CLI command**

In `app/cli.py`, add `"scan-task-sources"` to the command tuple.

Add command:

```python
def scan_task_sources_command(settings: WorkerSettings) -> int:
    from app.task_scanners import scan_ai_minutes, scan_local_workspace_files

    store = AutoReplyStore(settings.db_path)
    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    local_count = scan_local_workspace_files(store, workspace=settings.workspace)
    minutes_count = scan_ai_minutes(store, dws)
    total = local_count + minutes_count
    print(
        f"scan-task-sources local_files={local_count} ai_minutes={minutes_count} total={total}",
        flush=True,
    )
    return total
```

In `main()` dispatch:

```python
    elif args.command == "scan-task-sources":
        scan_task_sources_command(settings)
```

- [ ] **Step 5: Run scanner tests**

Run: `.venv/bin/pytest tests/test_task_scanners.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 7**

```bash
git add app/task_scanners.py app/cli.py tests/test_task_scanners.py tests/test_cli.py
git commit -m "Add task source scanners"
```

## Task 8: Follow-Up Draft Processing and Sending

**Files:**
- Create: `app/follow_up.py`
- Modify: `app/cli.py`
- Test: `tests/test_follow_up.py`

- [ ] **Step 1: Write follow-up tests**

Create `tests/test_follow_up.py`:

```python
import json

from app.follow_up import process_due_follow_ups
from app.store import AutoReplyStore


class FakeDws:
    def __init__(self):
        self.sent = []

    def send_message(self, conversation_id, text, at_users=None, title=None, user_id=None, open_dingtalk_id=None):
        self.sent.append(
            {
                "conversation_id": conversation_id,
                "text": text,
                "at_users": at_users or [],
                "user_id": user_id,
            }
        )
        return {"ok": True}


def test_due_low_risk_follow_up_sends_group_message(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="客户交付",
        category="projects",
        status="active",
        priority="P0",
        risk_level="high",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="给客户交付 ETA",
        owner_user_id="owner-1",
        owner_name="Alex",
        status="open",
        priority="P0",
        next_follow_up_at="2026-06-07 09:00:00",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="这个 P0 事项现在结果、阻塞和 ETA 分别是什么？",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": False}),
        scheduled_at="2026-06-07 09:00:00",
    )
    dws = FakeDws()

    sent = process_due_follow_ups(
        store,
        dws,
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 1
    assert dws.sent[0]["conversation_id"] == "cid-1"
    assert "结果、阻塞和 ETA" in dws.sent[0]["text"]
    assert store.list_follow_up_drafts(statuses=("sent",))[0].id == draft_id


def test_non_low_risk_follow_up_stays_draft(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="人事敏感事项",
        category="HR",
        status="active",
        priority="P1",
        risk_level="medium",
    )
    draft_id = store.create_follow_up_draft(
        project_id=project_id,
        owner_user_id="owner-1",
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="请同步进展",
        risk_check_json=json.dumps({"owner_in_group": True, "sensitive": True}),
        scheduled_at="2026-06-07 09:00:00",
    )

    sent = process_due_follow_ups(
        store,
        FakeDws(),
        now="2026-06-07 10:00:00",
        auto_send=True,
    )

    assert sent == 0
    assert store.list_follow_up_drafts(statuses=("draft",))[0].id == draft_id
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_follow_up.py -q`

Expected: FAIL with missing `app.follow_up`.

- [ ] **Step 3: Implement follow-up processing**

Create `app/follow_up.py`:

```python
import json

from app.store import AutoReplyStore


def _is_low_risk(risk_check_json: str) -> bool:
    try:
        risk = json.loads(risk_check_json or "{}")
    except json.JSONDecodeError:
        return False
    if risk.get("sensitive") is True:
        return False
    if risk.get("owner_in_group") is False:
        return False
    return True


def process_due_follow_ups(
    store: AutoReplyStore,
    dws,
    *,
    now: str,
    auto_send: bool,
    limit: int = 50,
) -> int:
    sent = 0
    drafts = store.list_follow_up_drafts(
        statuses=("draft", "approved"),
        due_before=now,
        limit=limit,
    )
    for draft in drafts:
        should_send = draft.status == "approved" or (auto_send and _is_low_risk(draft.risk_check_json))
        if not should_send:
            continue
        try:
            if draft.target_kind == "direct":
                result = dws.send_message(
                    None,
                    draft.question_text,
                    user_id=draft.owner_user_id or None,
                )
            else:
                result = dws.send_message(
                    draft.target_conversation_id,
                    draft.question_text,
                    at_users=[draft.owner_user_id] if draft.owner_user_id else [],
                )
        except Exception as exc:
            store.update_follow_up_draft(
                draft.id,
                status="failed",
                send_result_json=json.dumps({"error": str(exc)}, ensure_ascii=False),
            )
            store.record_error(draft.target_conversation_id, None, "follow_up", str(exc))
            continue
        store.update_follow_up_draft(
            draft.id,
            status="sent",
            send_result_json=json.dumps(result or {}, ensure_ascii=False),
            sent_at=now,
        )
        sent += 1
    return sent
```

- [ ] **Step 4: Add CLI command**

In `app/cli.py`, add `"process-follow-ups"` to the command tuple.

Add function:

```python
def process_follow_ups_command(settings: WorkerSettings) -> int:
    from app.follow_up import process_due_follow_ups

    dws = DwsClient(
        ding_robot_code=settings.ding_robot_code,
        ding_robot_name=settings.ding_robot_name,
        ding_receiver_user_id=settings.ding_receiver_user_id,
    )
    sent = process_due_follow_ups(
        AutoReplyStore(settings.db_path),
        dws,
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        auto_send=not settings.dry_run,
    )
    print(f"process-follow-ups sent={sent}", flush=True)
    return sent
```

In `main()` dispatch:

```python
    elif args.command == "process-follow-ups":
        process_follow_ups_command(settings)
```

- [ ] **Step 5: Run follow-up tests**

Run: `.venv/bin/pytest tests/test_follow_up.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 8**

```bash
git add app/follow_up.py app/cli.py tests/test_follow_up.py
git commit -m "Add task follow-up processing"
```

## Task 9: Memory Connector Doctor and Setup

**Files:**
- Create: `app/memory_setup.py`
- Modify: `app/cli.py`
- Test: `tests/test_memory_setup.py`

- [ ] **Step 1: Write setup tests**

Create `tests/test_memory_setup.py`:

```python
from pathlib import Path

from app.memory_setup import (
    codex_config_has_memory_connector,
    ensure_codex_memory_connector_config,
)


def test_codex_config_detection_and_update(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.other]\nurl = "https://other"\n', encoding="utf-8")

    assert codex_config_has_memory_connector(config) is False

    backup_path = ensure_codex_memory_connector_config(
        config,
        url="https://memory.example/mcp/",
        bearer_token_env_var="CONNECTOR_API_KEY",
    )

    content = config.read_text(encoding="utf-8")
    assert "[mcp_servers.memory_connector]" in content
    assert 'url = "https://memory.example/mcp/"' in content
    assert 'bearer_token_env_var = "CONNECTOR_API_KEY"' in content
    assert backup_path.exists()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_memory_setup.py -q`

Expected: FAIL with missing `app.memory_setup`.

- [ ] **Step 3: Implement setup helpers**

Create `app/memory_setup.py`:

```python
from datetime import datetime
from pathlib import Path


def codex_config_has_memory_connector(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    return "[mcp_servers.memory_connector]" in config_path.read_text(encoding="utf-8")


def ensure_codex_memory_connector_config(
    config_path: Path,
    *,
    url: str,
    bearer_token_env_var: str = "CONNECTOR_API_KEY",
) -> Path:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = config_path.with_suffix(config_path.suffix + f".{timestamp}.bak")
    backup_path.write_text(existing, encoding="utf-8")
    if "[mcp_servers.memory_connector]" in existing:
        return backup_path
    block = f'''

[mcp_servers.memory_connector]
url = "{url}"
bearer_token_env_var = "{bearer_token_env_var}"
'''
    config_path.write_text(existing.rstrip() + block, encoding="utf-8")
    return backup_path


def claude_config_has_memory_connector(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    return '"memory_connector"' in config_path.read_text(encoding="utf-8")
```

- [ ] **Step 4: Add CLI command**

In `app/cli.py`, add `"setup-memory-connector"` to the command tuple.

Add command-specific parser arguments:

```python
        if command == "setup-memory-connector":
            subparser.add_argument(
                "--memory-url",
                default=os.getenv("MEMORY_CONNECTOR_URL", ""),
                help="memory connector MCP URL",
            )
            subparser.add_argument(
                "--codex-config",
                default=str(Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"),
            )
```

Add function:

```python
def setup_memory_connector_command(memory_url: str, codex_config: str) -> None:
    from app.memory_setup import ensure_codex_memory_connector_config

    if not memory_url.strip():
        raise SystemExit("setup-memory-connector requires --memory-url or MEMORY_CONNECTOR_URL")
    backup = ensure_codex_memory_connector_config(
        Path(codex_config).expanduser(),
        url=memory_url.strip(),
    )
    print(f"setup-memory-connector codex_backup={backup}", flush=True)
```

In `main()` dispatch:

```python
    elif args.command == "setup-memory-connector":
        setup_memory_connector_command(args.memory_url, args.codex_config)
```

- [ ] **Step 5: Run setup tests**

Run: `.venv/bin/pytest tests/test_memory_setup.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 9**

```bash
git add app/memory_setup.py app/cli.py tests/test_memory_setup.py
git commit -m "Add memory connector setup checks"
```

## Task 10: Task Audit Web UI

**Files:**
- Modify: `app/audit_web.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Add UI render test**

Append to `tests/test_audit_web.py`:

```python
def test_tasks_page_renders_projects_todos_and_drafts(tmp_path):
    from app.audit_web import render_tasks_page
    from app.store import AutoReplyStore

    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="售前知识库建设",
        category="sales",
        status="active",
        priority="P1",
        risk_level="medium",
        background="销售支持项目。",
        current_state="整理中",
        next_step="补齐来源链接",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="补齐来源链接",
        status="open",
        priority="P1",
    )
    store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_name="Alex",
        target_conversation_id="cid-1",
        target_kind="group",
        question_text="来源链接补齐到哪一步了？",
        status="draft",
    )

    html = render_tasks_page(store)

    assert "售前知识库建设" in html
    assert "补齐来源链接" in html
    assert "来源链接补齐到哪一步了" in html
    assert "/tasks" in html
```

- [ ] **Step 2: Run UI test and verify failure**

Run: `.venv/bin/pytest tests/test_audit_web.py::test_tasks_page_renders_projects_todos_and_drafts -q`

Expected: FAIL because `render_tasks_page` is missing.

- [ ] **Step 3: Add render function and route**

In `app/audit_web.py`, import task models only if needed. Add:

```python
def render_tasks_page(store: AutoReplyStore) -> str:
    projects = store.list_work_projects(limit=100)
    draft_count = len(store.list_follow_up_drafts(statuses=("draft",), limit=200))
    rows = []
    for project in projects:
        todos = store.list_work_todos(project_id=project.id, statuses=("open", "waiting_owner"))
        rows.append(
            "<tr>"
            f"<td><a href=\"/tasks/{project.id}\">{escape(project.title)}</a></td>"
            f"<td><span class=\"pill\">{escape(project.category)}</span></td>"
            f"<td><span class=\"pill\">{escape(project.priority)}</span></td>"
            f"<td><span class=\"pill\">{escape(project.risk_level)}</span></td>"
            f"<td>{escape(project.owner_name)}</td>"
            f"<td>{escape(project.current_state)}</td>"
            f"<td>{escape(project.next_step)}</td>"
            f"<td>{len(todos)}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>Project</th><th>Category</th><th>Priority</th><th>Risk</th>"
        "<th>Owner</th><th>State</th><th>Next</th><th>Open TODOs</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    drafts = store.list_follow_up_drafts(statuses=("draft",), limit=50)
    draft_items = "".join(
        "<div class=\"attempt-item\">"
        f"<div class=\"attempt-main\">{escape(draft.owner_name)}</div>"
        f"<div class=\"attempt-copy\">{escape(draft.question_text)}</div>"
        "</div>"
        for draft in drafts
    )
    body = (
        "<section class=\"card\"><div class=\"card-head\">"
        "<h2>Tasks</h2>"
        f"<span class=\"pill\">Draft follow-ups {draft_count}</span>"
        "</div>"
        f"{table}</section>"
        "<section class=\"card\"><h2>Pending follow-ups</h2>"
        f"{draft_items or '<p class=\"muted\">No pending follow-ups.</p>'}"
        "</section>"
    )
    return render_page("Tasks", body, active_nav="tasks")
```

Add to `_top_nav()` a Tasks nav item:

```python
        ("tasks", "/tasks", "Tasks", None),
```

Add route in `create_audit_app()`:

```python
    @app.get("/tasks", response_class=HTMLResponse)
    def tasks_page() -> str:
        return render_tasks_page(AutoReplyStore(db_path))
```

- [ ] **Step 4: Run UI test**

Run: `.venv/bin/pytest tests/test_audit_web.py::test_tasks_page_renders_projects_todos_and_drafts -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 10**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "Add task audit page"
```

## Task 11: Integration and Regression Suite

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_worker.py`
- Test: multiple existing test files

- [ ] **Step 1: Add CLI command coverage for all new commands**

Append to `tests/test_cli.py`:

```python
def test_parser_supports_task_summary_commands():
    from app.cli import build_parser

    parser = build_parser()

    assert parser.parse_args(["process-work-items"]).command == "process-work-items"
    assert parser.parse_args(["scan-task-sources"]).command == "scan-task-sources"
    assert parser.parse_args(["process-follow-ups"]).command == "process-follow-ups"
    assert parser.parse_args(
        ["setup-memory-connector", "--memory-url", "https://memory.example/mcp/"]
    ).command == "setup-memory-connector"
```

- [ ] **Step 2: Run focused new test suite**

Run:

```bash
.venv/bin/pytest \
  tests/test_task_models.py \
  tests/test_task_store.py \
  tests/test_task_retrieval.py \
  tests/test_task_agent.py \
  tests/test_task_scanners.py \
  tests/test_follow_up.py \
  tests/test_memory_setup.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run affected existing tests**

Run:

```bash
.venv/bin/pytest \
  tests/test_store.py \
  tests/test_worker.py \
  tests/test_cli.py \
  tests/test_audit_web.py \
  tests/test_codex_runner.py \
  -q
```

Expected: PASS.

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/pytest -q`

Expected: PASS, except existing tests explicitly marked live/skipped by project defaults.

- [ ] **Step 5: Commit Task 11**

```bash
git add tests/test_cli.py tests/test_worker.py
git commit -m "Cover task summary integration paths"
```

## Task 12: Documentation and Launchd Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/product-logic.md`
- Modify: `docs/reply-worker-reliability.md`

- [ ] **Step 1: Add README section**

Add to `README.md` after the audit web section:

```markdown
## Task Summary

The task summary system tracks company management matters, business projects,
and important work as local projects with TODOs. It is separate from
`reply_tasks`: reply tasks are message-processing queue items, while task
summary projects are durable business objects.

Useful commands:

```bash
.venv/bin/ceo-agent scan-task-sources
.venv/bin/ceo-agent process-work-items
.venv/bin/ceo-agent process-follow-ups
.venv/bin/ceo-agent setup-memory-connector --memory-url "$MEMORY_CONNECTOR_URL"
```

Local file scanning is limited to `CEO_WORKSPACE`. The scanner does not scan the
whole machine and does not copy full sensitive file bodies into task tables.

Open the local audit UI and visit `/tasks` to review projects, TODOs, pending
follow-up drafts, low-confidence items, and memory setup errors.
```

- [ ] **Step 2: Add product logic note**

Add to `docs/product-logic.md` after the Audit section:

```markdown
## Task Summary

After reply processing, the service may enqueue a compact Work Item for the task
agent. The Work Item records source, summary, project_name, and basic context.
It does not pre-extract candidate projects, TODOs, or facts. The task agent sees
BM25 project candidates, can optionally recover context through DWS or
memory_recall, and then decides whether to discard, create, or update a project.

Task summary data is local SQLite audit data. Owner replies still use the normal
CEO reply path; the task system only updates project state after that path
finishes.
```

- [ ] **Step 3: Run documentation-adjacent tests**

Run:

```bash
.venv/bin/pytest tests/test_repo_layout.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit docs**

```bash
git add README.md docs/product-logic.md docs/reply-worker-reliability.md
git commit -m "Document task summary workflow"
```

- [ ] **Step 5: Restart service after runtime implementation**

After all runtime-code commits are complete, run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: launchd shows a fresh running process.

- [ ] **Step 6: Verify no unresolved backlog**

Run a SQLite check using the configured DB:

```bash
sqlite3 "${CEO_WORKER_DB:-data/auto-reply.sqlite3}" \
  "select status, count(*) from reply_tasks where status in ('failed','processing') group by status;"
sqlite3 "${CEO_WORKER_DB:-data/auto-reply.sqlite3}" \
  "select status, count(*) from work_summary_inputs where status in ('failed','processing') group by status;"
```

Expected: no unresolved `failed` or stale `processing` rows unless each row is already understood and documented.

## Self-Review Notes

Spec coverage:

- Separate task agent: Tasks 4 and 5.
- Project/TODO/update/follow-up data model: Tasks 1 and 2.
- Small Work Item without candidates/TODOs/facts: Tasks 1, 4, and 6.
- BM25 candidate retrieval from `summary + project_name`: Task 3.
- Optional DWS/memory recovery when BM25 misses or mismatches: Task 4 prompt.
- Conversation Work Items after processing: Task 6.
- Daily AI minutes and `CEO_WORKSPACE` local files: Task 7.
- Follow-up drafts, low-risk auto-send, P0/P1/P2 wording: Tasks 4 and 8.
- Memory connector setup checks without `user_id`: Tasks 4 and 9.
- `/tasks` UI: Task 10.
- Regression coverage and docs: Tasks 11 and 12.

Execution order is intentional. Do not implement scanners, follow-up sending, or
UI before the model/store/task-agent core is passing tests.
