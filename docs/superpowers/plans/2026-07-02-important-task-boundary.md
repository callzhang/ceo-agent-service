# Important Task Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CEO Agent track only important work items while ignoring routine process steps and cleaning up noisy TODOs exposed by Mina's feedback.

**Architecture:** Keep judgment inside the task agent prompt instead of adding a deterministic keyword filter. Use the existing `todo_changes.cancel` and `follow_up_changes.suppress` paths for noisy-TODO feedback. Add a conservative manual backfill command that cancels explicitly reviewed TODO IDs and suppresses their follow-ups with an audit trail.

**Tech Stack:** Python, Pydantic models, SQLite-backed `AutoReplyStore`, pytest, existing CLI command parser in `app/cli.py`.

---

## File Structure

- Modify `app/task_agent.py`
  - Responsibility: render task-agent prompt and apply task-agent decisions.
  - Change: add the important-vs-routine-process boundary to the prompt. No pre-agent keyword filter.

- Modify `tests/test_task_agent.py`
  - Responsibility: task-agent prompt and decision-application tests.
  - Change: assert prompt includes the new boundary and verify a Mina-style feedback decision cancels a TODO and suppresses its follow-up.

- Create `app/task_noise_backfill.py`
  - Responsibility: deterministic, manually scoped cleanup for already-reviewed noisy TODO IDs.
  - Interface: `backfill_routine_process_todos(store, todo_ids, reason, dry_run=True, now="") -> RoutineProcessBackfillResult`.
  - Boundary: does not classify text automatically; only acts on explicit TODO IDs supplied by the operator.

- Modify `app/cli.py`
  - Responsibility: expose backfill command and print auditable dry-run/apply summaries.
  - Change: add `backfill-routine-process-todos`.

- Modify `tests/test_cli.py`
  - Responsibility: parser and CLI command behavior tests.
  - Change: test dry-run and apply mode for the backfill command.

- `tests/test_task_store.py` is not expected to change.
  - Existing methods are enough: `get_work_todo`, `update_work_todo`, `list_follow_up_drafts`, `update_follow_up_draft`, `list_work_todo_dingtalk_links`, `update_work_todo_dingtalk_link`.

---

### Task 1: Protect The Prompt Boundary

**Files:**
- Modify: `tests/test_task_agent.py`
- Modify: `app/task_agent.py`

- [ ] **Step 1: Add a failing prompt test**

Add this test near the existing prompt tests in `tests/test_task_agent.py`, after `test_task_agent_prompt_names_required_memory_recall_tool`.

```python
def test_task_agent_prompt_defines_important_vs_routine_process_boundary():
    work_item = _work_item()
    work_item.summary = "Mina: 这种事情没必要创建待办，我不办这人也没法发 offer。"
    prompt = build_task_agent_prompt(
        work_item,
        candidate_prompt="候选上下文为空。",
    )

    assert "只跟踪重要事项" in prompt
    assert "流程性内容默认忽略" in prompt
    assert "不要创建 project、TODO、follow_up_draft 或 DingTalk Todo" in prompt
    assert "如果 Work Item 是对误建 TODO 或过细 follow-up 的反馈" in prompt
    assert "cancel" in prompt
    assert "suppress" in prompt
    assert "不要用关键词或固定业务词表做决定" in prompt
```

- [ ] **Step 2: Run the focused failing test**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py::test_task_agent_prompt_defines_important_vs_routine_process_boundary -q
```

Expected: FAIL because the prompt does not yet include the exact new boundary language.

- [ ] **Step 3: Update the task-agent prompt**

In `app/task_agent.py`, inside `build_task_agent_prompt`, replace the current bullet:

```python
- Task 只记录需要持续管理的公司事项；一次性工具、账号、权限、订阅或行政操作默认不创建 task，也不生成 follow-up，除非它明确影响已有项目、关键交付、成本风险或管理决策。
```

with this block:

```python
- Task 只记录需要持续管理的公司重要事项；只跟踪重要事项，不跟踪普通流程步骤。
- 重要事项是指失败会实质影响公司目标、关键项目、收入、客户承诺、组织决策、关键招聘、合规、财务风险或 Derek 级决策的事项。
- 流程性内容默认忽略：招聘、offer、面试、审批、报销、日程、行政等已知流程里的常规步骤，如果只是流程本来必须做的动作，不要创建 project、TODO、follow_up_draft 或 DingTalk Todo。
- 流程性内容只有在暴露真实风险、系统故障、跨 owner 阻塞、明确 deadline 风险、关键岗位决策或 Derek 需要拍板时，才把其中的风险或决策抽成 task；不要跟踪流程步骤本身。
- 如果 Work Item 是对误建 TODO 或过细 follow-up 的反馈，例如“没必要创建待办”“不要催这种流程动作”“这类事情不办流程也走不下去”，不要简单 discard；应在能匹配已有 TODO/follow_up 时使用 todo_changes.cancel 和 follow_up_changes.suppress 清理噪声，并在 update_summary 写明原因。
- 不要用关键词或固定业务词表做决定；结合 Work Item、候选项目、已有 TODO/follow-up、上下文和 failure_risk 判断是否重要。
- 一次性工具、账号、权限、订阅或行政操作默认不创建 task，也不生成 follow-up，除非它明确影响已有项目、关键交付、成本风险或管理决策。
```

Keep the rest of the prompt unchanged.

- [ ] **Step 4: Run the focused prompt test**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py::test_task_agent_prompt_defines_important_vs_routine_process_boundary -q
```

Expected: PASS.

- [ ] **Step 5: Run adjacent task-agent prompt tests**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/task_agent.py tests/test_task_agent.py
git commit -m "Clarify important task boundary"
```

---

### Task 2: Verify Noisy Feedback Can Cancel TODO And Suppress Follow-Up

**Files:**
- Modify: `tests/test_task_agent.py`
- Modify: `app/task_agent.py` for verification only; no behavior change is expected in this task.

- [ ] **Step 1: Add a failing behavior test**

Add this test near the existing `follow_up_change` tests in `tests/test_task_agent.py`.

```python
def test_mina_style_feedback_cancels_noisy_todo_and_suppresses_follow_up(tmp_path):
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    project_id = store.create_work_project(
        title="【招聘】Marketing L4-L5",
        category="recruiting",
        status="active",
        memory_context_json=json.dumps(
            _memory_context().model_dump(mode="json"),
            ensure_ascii=False,
        ),
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="将唐华 L5/对外总监 title 的 offer 和试用目标压实成一页纸",
        owner_user_id="mina-user-1",
        owner_name="邹婧玮(Mina 邹)",
        status="open",
        priority="P1",
        deadline_at="2026-07-03 18:00:00",
        next_follow_up_at="2026-07-02 10:00:00",
        follow_up_question="唐华 offer 和试用目标一页纸完成了吗？",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="mina-user-1",
        owner_name="邹婧玮(Mina 邹)",
        target_conversation_id="cid-mina",
        target_kind="direct",
        question_text="基于唐华 offer 推进事项，这个一页纸完成了吗？",
        status="draft",
        scheduled_at="2026-07-02 10:00:00",
    )
    decision = TaskAgentDecision.model_validate(
        {
            "action": "update_project",
            "project": {
                "id": project_id,
                "title": "【招聘】Marketing L4-L5",
                "category": "recruiting",
                "status": "active",
                "memory_context": _memory_context().model_dump(mode="json"),
                "facts": [
                    {
                        "description": "Mina clarified that routine HR offer-flow steps should not become separate reminders.",
                        "source": "reply_attempt:2163",
                        "created": "2026-07-01 10:50:17",
                        "updated": "2026-07-01 10:50:17",
                    }
                ],
            },
            "todo_changes": [
                {
                    "action": "cancel",
                    "todo_id": todo_id,
                    "title": "将唐华 L5/对外总监 title 的 offer 和试用目标压实成一页纸",
                    "status": "cancelled",
                    "blocker": "Routine HR offer-flow step; not an important task to track.",
                }
            ],
            "follow_up_drafts": [],
            "follow_up_changes": [
                {
                    "follow_up_id": follow_up_id,
                    "todo_id": todo_id,
                    "action": "suppress",
                    "reason": "Routine HR offer-flow step should not be followed up separately.",
                    "evidence_check": {
                        "source": "reply_attempt:2163",
                        "supports_suppression": True,
                    },
                }
            ],
            "update_summary": "Canceled noisy routine-process TODO after Mina feedback.",
            "merge_reason": "matched existing Marketing recruiting project and TODO",
            "memory_recall_used": True,
            "confidence": 0.86,
            "failure_risk": "If left open, the agent will keep interrupting HR about a routine process step.",
            "failure_risk_score": 0.2,
        }
    )

    work_item = _work_item()
    work_item.summary = (
        "磊哥分身，就类似这种事情，没必要创建待办，"
        "我这些事儿不办，这人也没法发offer啊。"
    )
    apply_task_agent_decision(
        store,
        summary_input_id=1,
        work_item=work_item,
        decision=decision,
        memory_recall_attempted=True,
    )

    todo = store.get_work_todo(todo_id)
    follow_up = store.get_follow_up_draft(follow_up_id)
    updates = store.list_work_updates(project_id=project_id)

    assert todo is not None
    assert todo.status == "cancelled"
    assert todo.blocker == "Routine HR offer-flow step; not an important task to track."
    assert follow_up is not None
    assert follow_up.status == "skipped"
    assert follow_up.suppressed_reason == "Routine HR offer-flow step should not be followed up separately."
    assert "Canceled noisy routine-process TODO" in updates[-1].summary
```

- [ ] **Step 2: Run the focused behavior test**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py::test_mina_style_feedback_cancels_noisy_todo_and_suppresses_follow_up -q
```

Expected: PASS if existing `todo_changes.cancel` and `follow_up_changes.suppress` behavior already works. If it fails, the failure should point to an existing application gap.

- [ ] **Step 3: Verify existing implementation branches**

Confirm `_todo_values` in `app/task_agent.py` includes `blocker` in its field list:

```python
    fields = [
        "title",
        "owner_user_id",
        "owner_name",
        "status",
        "priority",
        "deadline_at",
        "next_follow_up_at",
        "follow_up_question",
        "blocker",
    ]
```

Confirm `_apply_follow_up_change` in `app/task_agent.py` has this `suppress` branch:

```python
if change.action == "suppress":
    values["status"] = "skipped"
    values["suppressed_reason"] = change.reason or "task_agent_suppressed"
```

- [ ] **Step 4: Run the focused behavior test again**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py::test_mina_style_feedback_cancels_noisy_todo_and_suppresses_follow_up -q
```

Expected: PASS.

- [ ] **Step 5: Run full task-agent tests**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/task_agent.py tests/test_task_agent.py
git commit -m "Handle noisy task feedback"
```

---

### Task 3: Add Manual Routine-Process TODO Backfill

**Files:**
- Create: `app/task_noise_backfill.py`
- Modify: `tests/test_cli.py`
- Modify: `app/cli.py`

- [ ] **Step 1: Add failing parser test**

In `tests/test_cli.py`, add this parser test near the other parser tests.

```python
def test_parser_supports_backfill_routine_process_todos():
    args = build_parser().parse_args(
        [
            "backfill-routine-process-todos",
            "--todo-id",
            "2622",
            "--todo-id",
            "2623",
            "--reason",
            "routine HR offer-flow step",
            "--apply",
        ]
    )

    assert args.command == "backfill-routine-process-todos"
    assert args.todo_id == [2622, 2623]
    assert args.reason == "routine HR offer-flow step"
    assert args.apply is True
```

- [ ] **Step 2: Add failing command tests**

In `tests/test_cli.py`, add these tests near task CLI tests.

```python
def test_backfill_routine_process_todos_dry_run_reports_without_writing(tmp_path, capsys):
    from app.cli import backfill_routine_process_todos_command

    db_path = tmp_path / "task.sqlite3"
    store = AutoReplyStore(db_path)
    project_id = store.create_work_project(
        title="【招聘】Marketing L4-L5",
        category="recruiting",
        status="active",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="将唐华 offer 和试用目标压实成一页纸",
        owner_user_id="mina-user-1",
        owner_name="Mina",
        status="open",
        priority="P1",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="mina-user-1",
        owner_name="Mina",
        target_conversation_id="cid-mina",
        target_kind="direct",
        question_text="这个一页纸完成了吗？",
        status="draft",
    )

    result = backfill_routine_process_todos_command(
        WorkerSettings(db_path=db_path),
        todo_ids=[todo_id],
        reason="routine HR offer-flow step",
        apply=False,
        now="2026-07-02 12:00:00",
    )

    captured = capsys.readouterr()
    todo = store.get_work_todo(todo_id)
    follow_up = store.get_follow_up_draft(follow_up_id)

    assert result.changed == 0
    assert result.planned == 1
    assert todo is not None
    assert todo.status == "open"
    assert follow_up is not None
    assert follow_up.status == "draft"
    assert "dry_run=True planned=1 changed=0" in captured.out
    assert str(todo_id) in captured.out
```

```python
def test_backfill_routine_process_todos_apply_cancels_todo_and_suppresses_followup(tmp_path, capsys):
    from app.cli import backfill_routine_process_todos_command

    db_path = tmp_path / "task.sqlite3"
    store = AutoReplyStore(db_path)
    project_id = store.create_work_project(
        title="【招聘】Marketing L4-L5",
        category="recruiting",
        status="active",
    )
    todo_id = store.create_work_todo(
        project_id=project_id,
        title="将唐华 offer 和试用目标压实成一页纸",
        owner_user_id="mina-user-1",
        owner_name="Mina",
        status="open",
        priority="P1",
    )
    follow_up_id = store.create_follow_up_draft(
        project_id=project_id,
        todo_id=todo_id,
        owner_user_id="mina-user-1",
        owner_name="Mina",
        target_conversation_id="cid-mina",
        target_kind="direct",
        question_text="这个一页纸完成了吗？",
        status="draft",
    )
    link_id = store.create_work_todo_dingtalk_link(
        work_todo_id=todo_id,
        executor_user_id="mina-user-1",
        executor_name="Mina",
        title_snapshot="将唐华 offer 和试用目标压实成一页纸",
        deadline_at_snapshot="2026-07-03 18:00:00",
        priority_snapshot="P1",
        status="active",
        dingtalk_task_id="dt-task-1",
    )

    result = backfill_routine_process_todos_command(
        WorkerSettings(db_path=db_path),
        todo_ids=[todo_id],
        reason="routine HR offer-flow step",
        apply=True,
        now="2026-07-02 12:00:00",
    )

    captured = capsys.readouterr()
    todo = store.get_work_todo(todo_id)
    follow_up = store.get_follow_up_draft(follow_up_id)
    link = store.get_work_todo_dingtalk_link(link_id)
    updates = store.list_work_updates(project_id=project_id)

    assert result.changed == 1
    assert result.planned == 1
    assert todo is not None
    assert todo.status == "cancelled"
    assert todo.blocker == "routine HR offer-flow step"
    assert follow_up is not None
    assert follow_up.status == "skipped"
    assert follow_up.suppressed_reason == "routine HR offer-flow step"
    assert link is not None
    assert "external cancellation is not part of this change" in link.last_error
    assert updates[-1].source_type == "routine_process_backfill"
    assert "routine HR offer-flow step" in updates[-1].summary
    assert "dry_run=False planned=1 changed=1" in captured.out
```

- [ ] **Step 3: Run failing CLI tests**

Run:

```bash
.venv/bin/pytest tests/test_cli.py::test_parser_supports_backfill_routine_process_todos tests/test_cli.py::test_backfill_routine_process_todos_dry_run_reports_without_writing tests/test_cli.py::test_backfill_routine_process_todos_apply_cancels_todo_and_suppresses_followup -q
```

Expected: FAIL because command/module do not exist.

- [ ] **Step 4: Create `app/task_noise_backfill.py`**

Create this file:

```python
import json
from dataclasses import dataclass, field
from datetime import datetime

from app.store import AutoReplyStore


@dataclass(frozen=True)
class RoutineProcessBackfillItem:
    todo_id: int
    project_id: int
    title: str
    before_status: str
    after_status: str
    suppressed_follow_up_ids: list[int] = field(default_factory=list)
    dingtalk_link_ids: list[int] = field(default_factory=list)
    reason: str = ""
    skipped_reason: str = ""


@dataclass(frozen=True)
class RoutineProcessBackfillResult:
    dry_run: bool
    planned: int
    changed: int
    items: list[RoutineProcessBackfillItem]


def _default_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def backfill_routine_process_todos(
    store: AutoReplyStore,
    *,
    todo_ids: list[int],
    reason: str,
    dry_run: bool = True,
    now: str = "",
) -> RoutineProcessBackfillResult:
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("reason is required")
    timestamp = now.strip() or _default_now()
    seen: set[int] = set()
    items: list[RoutineProcessBackfillItem] = []
    changed = 0

    for todo_id in todo_ids:
        if todo_id in seen:
            continue
        seen.add(todo_id)
        todo = store.get_work_todo(todo_id)
        if todo is None:
            items.append(
                RoutineProcessBackfillItem(
                    todo_id=todo_id,
                    project_id=0,
                    title="",
                    before_status="missing",
                    after_status="missing",
                    reason=clean_reason,
                    skipped_reason="todo not found",
                )
            )
            continue
        if str(todo.status) in {"done", "cancelled"}:
            items.append(
                RoutineProcessBackfillItem(
                    todo_id=todo.id,
                    project_id=todo.project_id,
                    title=todo.title,
                    before_status=str(todo.status),
                    after_status=str(todo.status),
                    reason=clean_reason,
                    skipped_reason=f"todo already {todo.status}",
                )
            )
            continue

        follow_ups = [
            draft
            for draft in store.list_follow_up_drafts(
                statuses=("draft", "approved"),
                limit=500,
            )
            if draft.todo_id == todo.id
        ]
        links = store.list_work_todo_dingtalk_links(
            work_todo_id=todo.id,
            statuses=("active", "creating", "failed"),
            limit=100,
        )
        item = RoutineProcessBackfillItem(
            todo_id=todo.id,
            project_id=todo.project_id,
            title=todo.title,
            before_status=str(todo.status),
            after_status="cancelled",
            suppressed_follow_up_ids=[draft.id for draft in follow_ups],
            dingtalk_link_ids=[link.id for link in links],
            reason=clean_reason,
        )
        items.append(item)

        if dry_run:
            continue

        store.update_work_todo(
            todo.id,
            status="cancelled",
            blocker=clean_reason,
        )
        for draft in follow_ups:
            store.update_follow_up_draft(
                draft.id,
                status="skipped",
                suppressed_reason=clean_reason,
                evidence_check_json=json.dumps(
                    {
                        "source": "routine_process_backfill",
                        "reason": clean_reason,
                        "todo_id": todo.id,
                    },
                    ensure_ascii=False,
                ),
            )
        for link in links:
            store.update_work_todo_dingtalk_link(
                link.id,
                last_error=(
                    "Internal TODO cancelled as routine process; external "
                    "cancellation is not part of this change."
                ),
            )
        store.create_work_update(
            project_id=todo.project_id,
            source_type="routine_process_backfill",
            source_ref=str(todo.id),
            summary=(
                f"Cancelled routine-process TODO #{todo.id}: {todo.title}. "
                f"Reason: {clean_reason}"
            ),
            changes_json=(
                json.dumps(
                    {
                        "action": "cancel_routine_process_todo",
                        "todo_id": todo.id,
                        "suppressed_follow_up_ids": item.suppressed_follow_up_ids,
                        "dingtalk_link_ids": item.dingtalk_link_ids,
                        "at": timestamp,
                    },
                    ensure_ascii=False,
                )
            ),
            merge_reason="routine_process_backfill",
            confidence=1.0,
        )
        changed += 1

    return RoutineProcessBackfillResult(
        dry_run=dry_run,
        planned=sum(1 for item in items if not item.skipped_reason),
        changed=changed,
        items=items,
    )
```

- [ ] **Step 5: Wire CLI imports and command function**

In `app/cli.py`, add this import near other task imports:

```python
from app.task_noise_backfill import (
    RoutineProcessBackfillResult,
    backfill_routine_process_todos,
)
```

Add this function near `backfill_task_memory_context_command` or other task commands:

```python
def _print_routine_process_backfill_result(
    result: RoutineProcessBackfillResult,
) -> None:
    print(
        "backfill-routine-process-todos "
        f"dry_run={result.dry_run} planned={result.planned} changed={result.changed}"
    )
    for item in result.items:
        status = "skip" if item.skipped_reason else "plan"
        print(
            f"- {status} todo_id={item.todo_id} project_id={item.project_id} "
            f"before={item.before_status} after={item.after_status} "
            f"follow_ups={item.suppressed_follow_up_ids} "
            f"dingtalk_links={item.dingtalk_link_ids} "
            f"reason={item.reason or item.skipped_reason} title={item.title}"
        )


def backfill_routine_process_todos_command(
    settings: WorkerSettings,
    *,
    todo_ids: list[int],
    reason: str,
    apply: bool = False,
    now: str = "",
) -> RoutineProcessBackfillResult:
    store = AutoReplyStore(settings.db_path)
    result = backfill_routine_process_todos(
        store,
        todo_ids=todo_ids,
        reason=reason,
        dry_run=not apply,
        now=now,
    )
    _print_routine_process_backfill_result(result)
    return result
```

- [ ] **Step 6: Wire parser**

In `build_parser()` in `app/cli.py`, add:

```python
    routine_backfill = subparsers.add_parser(
        "backfill-routine-process-todos",
        help="Cancel manually reviewed routine-process TODOs and suppress their follow-ups.",
    )
    routine_backfill.add_argument(
        "--todo-id",
        action="append",
        type=int,
        required=True,
        help="Work TODO id to cancel. Repeat for multiple reviewed IDs.",
    )
    routine_backfill.add_argument(
        "--reason",
        required=True,
        help="Audit reason explaining why these TODOs are routine process noise.",
    )
    routine_backfill.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Omit for dry-run.",
    )
```

In `main()` command dispatch, add:

```python
    if args.command == "backfill-routine-process-todos":
        backfill_routine_process_todos_command(
            settings,
            todo_ids=args.todo_id,
            reason=args.reason,
            apply=args.apply,
        )
        return 0
```

- [ ] **Step 7: Run focused CLI tests**

Run:

```bash
.venv/bin/pytest tests/test_cli.py::test_parser_supports_backfill_routine_process_todos tests/test_cli.py::test_backfill_routine_process_todos_dry_run_reports_without_writing tests/test_cli.py::test_backfill_routine_process_todos_apply_cancels_todo_and_suppresses_followup -q
```

Expected: PASS.

- [ ] **Step 8: Run adjacent tests**

Run:

```bash
.venv/bin/pytest tests/test_cli.py tests/test_task_store.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/task_noise_backfill.py app/cli.py tests/test_cli.py tests/test_task_store.py
git commit -m "Add routine process TODO backfill"
```

---

### Task 4: Run Mina Backfill Dry-Run And Apply Reviewed IDs

**Files:**
- No code files expected.
- Runtime DB: `data/auto-reply.sqlite3`

- [ ] **Step 1: Inspect candidate Mina TODOs**

Run:

```bash
sqlite3 -header -csv data/auto-reply.sqlite3 "select t.id, t.project_id, p.title as project_title, t.title, t.owner_name, t.status, t.priority, t.deadline_at, t.next_follow_up_at, t.follow_up_question from work_todos t join work_projects p on p.id=t.project_id where t.status in ('open','waiting_owner') and (t.owner_name like '%Mina%' or t.owner_name like '%邹婧玮%' or p.related_people_json like '%Mina%' or p.related_people_json like '%邹婧玮%') order by t.updated_at desc limit 80;"
```

Expected: output includes candidate noisy TODOs such as the 唐华 offer/probation one-pager. Do not use this query as automatic classification; it is only an inspection list.

- [ ] **Step 2: Manually select routine-process TODO IDs**

Use these decision rules:

- Select TODOs that are only routine process steps inside HR/recruiting flow.
- Do not select TODOs for critical hiring decisions, offer cash/equity boundaries, system faults, owner correction, deadline risk, or Derek decisions.
- At minimum, review these known likely candidates from Mina's feedback window:
  - `2622` if it is still open and still titled like "将唐华 L5/对外总监 title 的 offer 和试用目标压实成一页纸".
  - Any duplicate TODO with the same routine offer one-pager meaning.

- [ ] **Step 3: Run dry-run with reviewed IDs**

Replace `2622` with the reviewed list from Step 2.

```bash
.venv/bin/python -m app.cli backfill-routine-process-todos --todo-id 2622 --reason "Mina feedback: routine HR offer-flow step should not be tracked as a separate TODO"
```

Expected output contains these exact substrings:

```text
backfill-routine-process-todos dry_run=True planned=1 changed=0
todo_id=2622
before=open after=cancelled
reason=Mina feedback: routine HR offer-flow step should not be tracked as a separate TODO
```

- [ ] **Step 4: Verify dry-run made no DB changes**

Run:

```bash
sqlite3 -header -csv data/auto-reply.sqlite3 "select id, status, blocker from work_todos where id in (2622);"
```

Expected: selected TODOs are still `open` or their original pre-apply status.

- [ ] **Step 5: Apply reviewed backfill**

Only after Step 3 output matches the reviewed list:

```bash
.venv/bin/python -m app.cli backfill-routine-process-todos --todo-id 2622 --reason "Mina feedback: routine HR offer-flow step should not be tracked as a separate TODO" --apply
```

Expected output contains these exact substrings:

```text
backfill-routine-process-todos dry_run=False planned=1 changed=1
todo_id=2622
before=open after=cancelled
reason=Mina feedback: routine HR offer-flow step should not be tracked as a separate TODO
```

- [ ] **Step 6: Verify DB state**

Run:

```bash
sqlite3 -header -csv data/auto-reply.sqlite3 "select id, status, blocker from work_todos where id in (2622);"
```

Expected: selected TODOs are `cancelled` and `blocker` contains the Mina feedback reason.

Run:

```bash
sqlite3 -header -csv data/auto-reply.sqlite3 "select id, todo_id, status, suppressed_reason from follow_up_drafts where todo_id in (2622) order by id;"
```

Expected: draft or approved follow-ups tied to selected TODOs are `skipped` with the Mina feedback reason. Already sent follow-ups are not retroactively unsent.

- [ ] **Step 7: Commit if the DB is intentionally tracked**

Check:

```bash
git status --short data/auto-reply.sqlite3
```

Expected:

- If no output, runtime DB is not tracked; no DB commit is needed.
- If `data/auto-reply.sqlite3` appears and the project expects DB changes to be versioned, commit it:

```bash
git add data/auto-reply.sqlite3
git commit -m "Backfill routine process task noise"
```

---

### Task 5: Full Verification

**Files:**
- No new files expected.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
.venv/bin/pytest tests/test_task_agent.py tests/test_cli.py tests/test_task_store.py -q
```

Expected: PASS.

- [ ] **Step 2: Run compile check**

Run:

```bash
.venv/bin/python -m compileall app tests
```

Expected: no compile errors.

- [ ] **Step 3: Check worktree**

Run:

```bash
git status --short
```

Expected: only intentional files are modified. No unrelated files.

- [ ] **Step 4: Restart service after runtime code changes**

After all code commits are complete, restart launchd because this project does not hot-reload Python code.

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
```

Expected: command exits successfully.

- [ ] **Step 5: Verify service process**

Run:

```bash
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: service has a current `pid` and no unresolved launch failure.

- [ ] **Step 6: Verify no active processing backlog**

Run:

```bash
sqlite3 -header -csv data/auto-reply.sqlite3 "select 'reply_tasks' as table_name, status, count(*) as count from reply_tasks where status in ('pending','processing','failed') group by status union all select 'work_summary_inputs' as table_name, status, count(*) as count from work_summary_inputs where status in ('pending','processing','failed') group by status order by table_name, status;"
```

Expected: no `processing` rows. Existing historical `failed` rows may remain if unrelated to this change.

---

## Self-Review Notes

- Spec coverage:
  - Prompt boundary implements routine-process ignore behavior.
  - Existing decision application is tested for canceling noisy TODOs and suppressing follow-ups.
  - Manual backfill covers Mina's existing noisy TODO cleanup without keyword filtering.
  - Important exceptions remain task-agent decisions and are preserved by prompt language and tests.

- Placeholder scan:
  - No placeholder markers or incomplete task remains.
  - The only operator-supplied values are explicit reviewed TODO IDs during runtime backfill.

- Type consistency:
  - Uses existing `TodoChange.action="cancel"` and `FollowUpDraftChange.action="suppress"`.
  - Uses existing store methods and CLI `WorkerSettings`.
  - Backfill result types are new and local to `app/task_noise_backfill.py`.
