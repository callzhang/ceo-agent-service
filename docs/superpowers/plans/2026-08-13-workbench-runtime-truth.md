# Workbench Runtime Truth and Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent harmless provider pagination metadata from failing new Workbench turns and make terminal tool, status, title, and timestamp presentation accurate and readable.

**Architecture:** `CodexRuntime` will project recognized native records into minimal provider-neutral events before credential validation, so raw MCP results never cross the persistence boundary. `WorkbenchStore.create_turn()` will derive the first title in its existing transaction, while pure frontend presentation helpers and the timeline reducer will localize display values and derive aborted tools from authoritative terminal turn state.

**Tech Stack:** Python 3.12, SQLite, FastAPI, Pydantic 2, Codex CLI JSONL, React 19, TypeScript, Vitest, Testing Library, Vite.

---

## File map

**Runtime safety projection**

- Modify `app/workbench/codex_runtime.py`: remove whole-record credential rejection, validate normalized payloads, and emit bounded Chinese tool labels and summaries.
- Modify `tests/test_workbench_codex_runtime.py`: reproduce the pagination cursor false positive, prove ignored native data never persists, and retain public credential fail-closed coverage.

**Atomic first-message title**

- Modify `app/workbench/store.py`: derive and conditionally persist the first-message title inside `create_turn()`'s `BEGIN IMMEDIATE` transaction.
- Modify `tests/test_workbench_store.py`: cover default-title replacement, idempotent replay, renamed tasks, and later turns.
- Modify `tests/test_workbench_api.py`: prove the authoritative timeline returns the derived title after creating the first turn.

**Frontend presentation boundary**

- Create `frontend/src/presentation.ts`: strict UTC timestamp parsing, local formatting, machine-status labels, and safe legacy tool-name/summary labels.
- Create `frontend/src/presentation.test.ts`: timezone, invalid timestamp, status, and tool-label unit tests.
- Modify `frontend/src/components/TaskList.tsx`: reuse the shared parser and task-state labels.
- Modify `frontend/src/components/TurnInspector.tsx`: render local time and Chinese task/turn state.
- Modify `frontend/src/components/ExecutionStep.tsx`: render localized safe names, summaries, and an explicit aborted state.
- Modify `frontend/src/styles.css`: give aborted tools a distinct non-running visual state.
- Modify `frontend/src/components/ConversationTimeline.test.tsx`: cover inspector and execution-card presentation.
- Modify `frontend/src/components/TaskList.test.tsx`: keep lifecycle-label expectations aligned with the shared labels.

**Terminal tool truth**

- Modify `frontend/src/events.ts`: accept authoritative turn status when building blocks and derive unmatched active tools as aborted only for terminal turns.
- Modify `frontend/src/events.test.ts`: cover running versus terminal unmatched tools and preserve correlated completion ordering.
- Modify `frontend/src/components/ConversationTimeline.tsx`: pass each turn's authoritative status into block derivation.
- Modify `frontend/src/components/ConversationTimeline.test.tsx`: verify the visible aborted explanation on a failed historical turn.

**Verification and release**

- Modify `docs/user-guide.md`: describe automatic first-message titles, safe tool summaries, and aborted tool meaning.
- Verify all focused and broad tests, build the static bundle, integrate the clean commits into the production checkout, restart launchd, and perform a new harmless live query. Generated `app/static/workbench/` assets remain untracked.

### Task 1: Normalize provider records before public credential validation

**Files:**
- Modify: `app/workbench/codex_runtime.py:263-452`
- Test: `tests/test_workbench_codex_runtime.py:45-110, 820-1015, 1270-1380`

- [ ] **Step 1: Write the pagination and ignored-native-data regressions**

Add these tests beside the existing MCP business-content tests:

```python
def test_mcp_pagination_cursor_is_private_and_does_not_fail_turn(tmp_path: Path):
    cursor = "opaque-pagination-value"
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {
                "id": "calendar-1",
                "type": "mcp_tool_call",
                "server": "codex_apps",
                "tool": "google_calendar.search_events",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "calendar-1",
                "type": "mcp_tool_call",
                "server": "codex_apps",
                "tool": "google_calendar.search_events",
                "result": {
                    "Ok": {
                        "structuredContent": {
                            "events": [],
                            "next_page_token": cursor,
                        }
                    }
                },
            },
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "完成"}},
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    [completed] = [event for event in events if event.event_type == "tool_completed"]
    assert completed.payload == {
        "tool": "Google 日历查询",
        "summary": "已完成",
        "status": "completed",
        "tool_call_id": "tool-call-1",
    }
    assert cursor not in repr(events)


def test_ignored_native_metadata_is_not_public_or_rejected(tmp_path: Path):
    credential = "sk-proj-ignorednativecredential1234"
    records = [
        {"type": "thread.started", "thread_id": SESSION_ID},
        {
            "type": "item.started",
            "item": {"id": "command-1", "type": "command_execution"},
        },
        {
            "type": "item.completed",
            "item": {
                "id": "command-1",
                "type": "command_execution",
                "metadata": {"api_token": credential},
                "aggregated_output": credential,
            },
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "完成"}},
        {"type": "turn.completed"},
    ]
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=FakeProcessExecutor(records))

    result = runtime.wait(runtime.start(request(tmp_path), on_event=events.append))

    assert result.status == "completed"
    assert credential not in repr(events)
```

Replace `test_credential_in_any_nested_provider_string_is_rejected_before_emission` with the second boundary test. Keep `test_credential_bearing_assistant_text_never_enters_events_or_errors` and `test_confirmation_argv_credential_value_is_rejected_without_leak` unchanged.

- [ ] **Step 2: Run the new regressions and verify RED**

Run:

```bash
./.venv/bin/python -m pytest -q \
  tests/test_workbench_codex_runtime.py::test_mcp_pagination_cursor_is_private_and_does_not_fail_turn \
  tests/test_workbench_codex_runtime.py::test_ignored_native_metadata_is_not_public_or_rejected
```

Expected: both fail because `accept_line()` calls `assert_no_credentials(record)` and returns `sensitive_provider_output`.

- [ ] **Step 3: Add the minimal public projection boundary**

In `app/workbench/codex_runtime.py`, remove the whole-record credential check from `accept_line()`. Add application-owned labels and validate only emitted payloads:

```python
_SAFE_MCP_TOOL_LABELS = {
    ("codex_apps", "google_calendar.search_events"): "Google 日历查询",
    ("codex_apps", "gmail.search_emails"): "邮件查询",
    (_CONFIRMATION_SERVER, _CONFIRMATION_TOOL): "操作确认",
}


def _safe_tool_name(item: Mapping[str, Any]) -> str:
    if item.get("type") == "command_execution":
        return "本地命令"
    server = item.get("server")
    tool = item.get("tool")
    if not isinstance(server, str) or not isinstance(tool, str):
        return "MCP 工具"
    return _SAFE_MCP_TOOL_LABELS.get((server.strip(), tool.strip()), "MCP 工具")
```

Update `_start_tool()` and `_complete_tool()` summaries:

```python
"summary": "执行中"
```

```python
"summary": "执行失败" if failed else "已完成"
```

Make `_emit()` the validation boundary:

```python
def _emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
    try:
        assert_no_credentials(payload)
    except ValueError as exc:
        raise _AdapterFailure(
            "sensitive_provider_output",
            "provider output contained sensitive public data",
        ) from exc
    self._on_event(RuntimeEvent(event_type, payload))
```

Do not change `app/leak_check.py`. `_reject_credential_bearing_text()`, `_extract_confirmation()`, and `_validate_argv()` remain the explicit checks for assistant and confirmation content.

- [ ] **Step 4: Run runtime safety tests and verify GREEN**

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_workbench_codex_runtime.py
./.venv/bin/python -m ruff check app/workbench/codex_runtime.py tests/test_workbench_codex_runtime.py
```

Expected: all Codex runtime tests pass; Ruff reports no errors. Confirm the assistant-text and confirmation-credential tests still pass.

- [ ] **Step 5: Commit the runtime boundary**

```bash
git add app/workbench/codex_runtime.py tests/test_workbench_codex_runtime.py
git commit -m "fix: normalize workbench provider events safely"
```

### Task 2: Derive the first task title atomically

**Files:**
- Modify: `app/workbench/store.py:134-150, 376-447`
- Test: `tests/test_workbench_store.py:920-980`
- Test: `tests/test_workbench_api.py:65-90`

- [ ] **Step 1: Write failing store tests for first-turn title rules**

Add:

```python
def test_first_turn_replaces_only_the_default_task_title(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="新任务", runtime_kind="codex")

    first = store.create_turn(
        task.id,
        user_text="  今天有哪些   值得我关注的事项？  ",
        client_request_id="title-request",
    )
    replay = store.create_turn(
        task.id,
        user_text="今天有哪些   值得我关注的事项？",
        client_request_id="title-request",
    )

    assert replay == first
    assert store.get_task(task.id).title == "今天有哪些 值得我关注的事项？"


def test_first_turn_does_not_overwrite_a_user_title(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="新任务", runtime_kind="codex")
    store.rename_task(task.id, title="每日关注")

    store.create_turn(task.id, user_text="今天有哪些事项", client_request_id="renamed")

    assert store.get_task(task.id).title == "每日关注"


def test_later_turn_does_not_replace_a_restored_default_title(tmp_path: Path):
    store = _store(tmp_path)
    task = store.create_task(title="第一轮", runtime_kind="codex")
    first = store.create_turn(task.id, user_text="第一轮", client_request_id="first")
    claimed = store.claim_next_turn(owner="worker", now="2026-08-13T00:00:00Z")
    assert claimed is not None and claimed.id == first.id
    store.complete_turn(
        first.id,
        status=TurnStatus.COMPLETED,
        owner="worker",
        now="2026-08-13T00:00:01Z",
    )
    store.rename_task(task.id, title="新任务")

    store.create_turn(task.id, user_text="第二轮不应改名", client_request_id="second")

    assert store.get_task(task.id).title == "新任务"
```

- [ ] **Step 2: Run the title tests and verify RED**

Run:

```bash
./.venv/bin/python -m pytest -q \
  tests/test_workbench_store.py::test_first_turn_replaces_only_the_default_task_title \
  tests/test_workbench_store.py::test_first_turn_does_not_overwrite_a_user_title \
  tests/test_workbench_store.py::test_later_turn_does_not_replace_a_restored_default_title
```

Expected: the first test fails because the task title remains `新任务`; the protection tests document the non-overwrite contract.

- [ ] **Step 3: Implement deterministic title derivation in the existing transaction**

Add near `WorkbenchStore`:

```python
_DEFAULT_WORKBENCH_TASK_TITLE = "新任务"
_DERIVED_TASK_TITLE_MAX_LENGTH = 32


def _derive_task_title(user_text: str) -> str:
    normalized = " ".join(user_text.split())
    if len(normalized) <= _DERIVED_TASK_TITLE_MAX_LENGTH:
        return normalized
    return normalized[: _DERIVED_TASK_TITLE_MAX_LENGTH - 1].rstrip() + "…"
```

Inside `create_turn()`, after the insert succeeds and before `_append_control_event()`, conditionally update the task:

```python
if task_sequence == 1 and task["title"] == _DEFAULT_WORKBENCH_TASK_TITLE:
    db.execute(
        """
        update workbench_tasks
        set title=?, updated_at=current_timestamp
        where id=? and title=?
          and not exists (
              select 1 from workbench_turns
              where task_id=? and id<>?
          )
        """,
        (
            _derive_task_title(user_text),
            task_id,
            _DEFAULT_WORKBENCH_TASK_TITLE,
            task_id,
            turn_id,
        ),
    )
```

Keep the idempotent existing-turn branch before title derivation so a retry does not mutate title state.

- [ ] **Step 4: Add an API readback regression**

Extend `test_task_turn_and_event_replay` or add:

```python
def test_first_turn_title_is_authoritative_in_timeline(tmp_path: Path):
    with _client(tmp_path) as client:
        task = client.post(
            "/api/workbench/tasks",
            json={"title": "新任务", "runtime_kind": "codex"},
        ).json()
        response = client.post(
            f"/api/workbench/tasks/{task['id']}/turns",
            json={"text": "检查今天的重要事项", "client_request_id": "title-api"},
        )
        timeline = client.get(f"/api/workbench/tasks/{task['id']}/timeline").json()

    assert response.status_code == 201
    assert timeline["task"]["title"] == "检查今天的重要事项"
```

- [ ] **Step 5: Run title/store/API tests and verify GREEN**

Run:

```bash
./.venv/bin/python -m pytest -q tests/test_workbench_store.py tests/test_workbench_api.py
./.venv/bin/python -m ruff check app/workbench/store.py tests/test_workbench_store.py tests/test_workbench_api.py
```

Expected: all store and API tests pass; no schema migration is introduced.

- [ ] **Step 6: Commit the title transaction**

```bash
git add app/workbench/store.py tests/test_workbench_store.py tests/test_workbench_api.py
git commit -m "feat: derive workbench title from first message"
```

### Task 3: Centralize safe localized presentation

**Files:**
- Create: `frontend/src/presentation.ts`
- Create: `frontend/src/presentation.test.ts`
- Modify: `frontend/src/components/TaskList.tsx:1-115, 215-225`
- Modify: `frontend/src/components/TurnInspector.tsx:1-60`
- Modify: `frontend/src/components/ExecutionStep.tsx`
- Test: `frontend/src/components/ConversationTimeline.test.tsx:250-265`

- [ ] **Step 1: Write failing pure presentation tests**

Create `frontend/src/presentation.test.ts`:

```typescript
import { describe, expect, it, vi } from "vitest";

import {
  executionName,
  executionStateLabel,
  formatWorkbenchDateTime,
  parseWorkbenchTimestamp,
  taskStateLabel,
} from "./presentation";

describe("workbench presentation", () => {
  it("parses storage timestamps as UTC and formats browser-local time", () => {
    vi.stubEnv("TZ", "Asia/Shanghai");
    try {
      expect(parseWorkbenchTimestamp("2026-08-13 15:14:36")?.toISOString())
        .toBe("2026-08-13T15:14:36.000Z");
      const formatted = formatWorkbenchDateTime("2026-08-13 15:14:36");
      expect(formatted?.dateTime).toBe("2026-08-13T15:14:36.000Z");
      expect(formatted?.label).toContain("23:14:36");
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("rejects impossible timestamps and localizes machine values", () => {
    expect(parseWorkbenchTimestamp("2026-02-30 12:00:00")).toBeNull();
    expect(formatWorkbenchDateTime("not-a-date")).toBeNull();
    expect(taskStateLabel("failed")).toBe("失败");
    expect(executionStateLabel("aborted")).toBe("已中止");
  });

  it("localizes bounded legacy tool identities without exposing unknown names", () => {
    expect(executionName("command")).toBe("本地命令");
    expect(executionName("google_calendar.search_events")).toBe("Google 日历查询");
    expect(executionName("gmail.search_emails")).toBe("邮件查询");
    expect(executionName("untrusted.provider.tool")).toBe("MCP 工具");
  });
});
```

- [ ] **Step 2: Run the new file and verify RED**

Run: `npm run test --prefix frontend -- --run src/presentation.test.ts`

Expected: FAIL because `frontend/src/presentation.ts` does not exist.

- [ ] **Step 3: Implement pure presentation helpers**

Create `frontend/src/presentation.ts` with strict rollover validation copied from the proven `TaskList` parser, then expose bounded labels:

```typescript
import type { TaskState, TurnStatus } from "./types";

const taskStateLabels: Record<TaskState | TurnStatus, string> = {
  idle: "空闲",
  queued: "排队中",
  running: "执行中",
  waiting_confirmation: "等待确认",
  completed: "已完成",
  stopped: "已停止",
  failed: "失败",
};

const legacyToolLabels: Record<string, string> = {
  command: "本地命令",
  mcp_tool: "MCP 工具",
  "google_calendar.search_events": "Google 日历查询",
  "gmail.search_emails": "邮件查询",
  request_reviewed_action: "操作确认",
};

export function taskStateLabel(value: TaskState | TurnStatus): string {
  return taskStateLabels[value] ?? "状态未知";
}

export function executionStateLabel(value: string): string {
  if (["completed", "success"].includes(value)) return "已完成";
  if (["failed", "error"].includes(value)) return "失败";
  if (value === "aborted") return "已中止";
  return "执行中";
}

export function executionName(value: unknown): string {
  if (typeof value !== "string") return "工具调用";
  const normalized = value.trim();
  if (Object.prototype.hasOwnProperty.call(legacyToolLabels, normalized)) {
    return legacyToolLabels[normalized];
  }
  if (["本地命令", "MCP 工具", "Google 日历查询", "邮件查询", "操作确认"].includes(normalized)) {
    return normalized;
  }
  return "MCP 工具";
}
```

Implement `parseWorkbenchTimestamp()` using the exact 19-character backend UTC validation currently in `TaskList.tsx`. Implement `formatWorkbenchDateTime()` as:

```typescript
export function formatWorkbenchDateTime(value: string) {
  const parsed = parseWorkbenchTimestamp(value);
  if (!parsed) return null;
  const label = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed).replace(/\s+/g, " ");
  return { dateTime: parsed.toISOString(), label };
}
```

- [ ] **Step 4: Reuse helpers in task list, inspector, and execution cards**

In `TaskList.tsx`, delete the private timestamp parser and state-label record, import `parseWorkbenchTimestamp` and `taskStateLabel`, and render `taskStateLabel(task.state)`.

Update the lifecycle-label expectation in `TaskList.test.tsx` from `等待中` to the approved `排队中`; keep all seven machine states covered.

In `TurnInspector.tsx`, compute:

```typescript
const updatedAt = formatWorkbenchDateTime(task.updated_at);
const visibleStatus = taskStateLabel(latest?.status ?? task.state);
```

Render time semantically:

```tsx
<div>
  <dt>最近更新</dt>
  <dd>{updatedAt ? <time dateTime={updatedAt.dateTime}>{updatedAt.label}</time> : "时间未知"}</dd>
</div>
<div><dt>状态</dt><dd>{visibleStatus}</dd></div>
```

In `ExecutionStep.tsx`, use `executionName(payload.tool)` and `executionStateLabel(status)`. Translate the three legacy summaries before `safeDisplayText`:

```typescript
const legacySummaries: Record<string, string> = {
  "Tool started": "执行中",
  "Tool completed": "已完成",
  "Tool failed": "执行失败",
};
const rawSummary = typeof payload.summary === "string"
  ? (legacySummaries[payload.summary] ?? payload.summary)
  : payload.change;
```

Keep `safeDisplayText()` as the final defensive rendering boundary.

- [ ] **Step 5: Add inspector assertions and run frontend tests**

Extend the existing `TurnInspector` test with an explicit failed, timestamped fixture:

```typescript
const failedTimeline: Timeline = {
  ...timeline,
  task: {
    ...timeline.task,
    state: "failed",
    updated_at: "2026-08-13 15:14:36",
  },
  turns: [{
    ...turn,
    status: "failed",
    completed_at: "2026-08-13 15:17:41",
  }],
};
render(<TurnInspector task={failedTimeline.task} timeline={failedTimeline} capabilities={[]} stats={null} />);
const inspector = screen.getByTestId("turn-inspector");
expect(within(inspector).getByText("失败")).toBeInTheDocument();
expect(within(inspector).queryByText("failed")).not.toBeInTheDocument();
expect(within(inspector).getByText(/2026\/08\/13/).tagName).toBe("TIME");
```

Run:

```bash
npm run test --prefix frontend -- --run \
  src/presentation.test.ts \
  src/components/TaskList.test.tsx \
  src/components/ConversationTimeline.test.tsx
npm run build --prefix frontend
```

Expected: focused tests pass and TypeScript/Vite build succeeds.

- [ ] **Step 6: Commit the presentation boundary**

```bash
git add frontend/src/presentation.ts frontend/src/presentation.test.ts \
  frontend/src/components/TaskList.tsx \
  frontend/src/components/TaskList.test.tsx \
  frontend/src/components/TurnInspector.tsx \
  frontend/src/components/ExecutionStep.tsx \
  frontend/src/components/ConversationTimeline.test.tsx
git commit -m "fix: localize workbench execution details"
```

### Task 4: Derive aborted tools from authoritative terminal state

**Files:**
- Modify: `frontend/src/events.ts:103-165`
- Modify: `frontend/src/events.test.ts:40-105`
- Modify: `frontend/src/components/ConversationTimeline.tsx:50-70`
- Modify: `frontend/src/components/ExecutionStep.tsx`
- Modify: `frontend/src/styles.css:264-275`
- Test: `frontend/src/components/ConversationTimeline.test.tsx`

- [ ] **Step 1: Write reducer tests for terminal and active turns**

Add to `frontend/src/events.test.ts`:

```typescript
it("marks unmatched tools aborted only when the authoritative turn is terminal", () => {
  const events = createEventState([
    event(1, "tool_started", {
      tool: "google_calendar.search_events",
      tool_call_id: "calendar-1",
      summary: "Tool started",
    }),
  ]).events;

  expect(timelineBlocks("turn-1", events, "running")[0].status).toBe("running");
  const failed = timelineBlocks("turn-1", events, "failed")[0];
  expect(failed.status).toBe("aborted");
  expect(failed.payload?.summary).toBe("任务已结束，未收到工具完成事件。");
});

it("keeps a persisted completion authoritative on terminal turns", () => {
  const events = createEventState([
    event(1, "tool_started", { tool: "read", tool_call_id: "read-1" }),
    event(2, "tool_completed", {
      tool: "read",
      tool_call_id: "read-1",
      status: "completed",
      summary: "Tool completed",
    }),
  ]).events;

  expect(timelineBlocks("turn-1", events, "failed")[0].status).toBe("completed");
});
```

- [ ] **Step 2: Run the reducer tests and verify RED**

Run: `npm run test --prefix frontend -- --run src/events.test.ts`

Expected: TypeScript or assertions fail because `timelineBlocks()` does not accept turn status and leaves the tool running.

- [ ] **Step 3: Implement terminal-only derivation**

Change the signature to accept `TurnStatus`:

```typescript
import type { EventType, TurnStatus, WorkbenchEvent } from "./types";

const terminalTurnStatuses = new Set<TurnStatus>(["completed", "stopped", "failed"]);

export function timelineBlocks(
  turnId: string,
  events: WorkbenchEvent[],
  turnStatus?: TurnStatus,
): TimelineBlock[] {
```

Before returning blocks, derive only still-running tool blocks:

```typescript
if (!turnStatus || !terminalTurnStatuses.has(turnStatus)) return blocks;
return blocks.map((block) => block.kind === "tool" && block.status === "running"
  ? {
      ...block,
      status: "aborted",
      payload: {
        ...block.payload,
        summary: "任务已结束，未收到工具完成事件。",
      },
    }
  : block);
```

In `ConversationTimeline.tsx`, pass the authoritative status and include it in the memo dependencies:

```typescript
const blocks = useMemo(
  () => timelineBlocks(turn.id, events, turn.status),
  [events, turn.id, turn.status],
);
```

In `ExecutionStep.tsx`, give `aborted` its own class and non-spinning icon:

```typescript
const aborted = status === "aborted";
const Icon = kind === "file"
  ? FilePenLine
  : failed
    ? XCircle
    : aborted
      ? CircleSlash2
      : completed
        ? CheckCircle2
        : CircleEllipsis;
const visualState = failed ? "failed" : aborted ? "aborted" : completed ? "completed" : "running";
```

Add a static aborted color rule beside the other execution states in `styles.css`:

```css
.execution-aborted summary > svg:first-child { color: var(--ink-soft); }
```

- [ ] **Step 4: Add the historical failed-turn rendering assertion**

In `ConversationTimeline.test.tsx`, render a failed turn containing only `tool_started` and assert:

```typescript
expect(screen.getByText("已中止")).toBeInTheDocument();
expect(screen.getByText("任务已结束，未收到工具完成事件。")).toBeInTheDocument();
expect(screen.queryByText("执行中")).not.toBeInTheDocument();
```

Also render the same event under a running turn and assert `执行中` remains visible.

- [ ] **Step 5: Run all frontend tests and build**

Run:

```bash
npm run test --prefix frontend -- --run
npm run build --prefix frontend
git diff --check
```

Expected: all frontend tests pass, build emits hashed JS/CSS under ignored `app/static/workbench/`, and diff check is silent.

- [ ] **Step 6: Commit terminal tool truth**

```bash
git add frontend/src/events.ts frontend/src/events.test.ts \
  frontend/src/components/ConversationTimeline.tsx \
  frontend/src/components/ConversationTimeline.test.tsx \
  frontend/src/components/ExecutionStep.tsx frontend/src/styles.css
git commit -m "fix: close terminal workbench tool displays"
```

### Task 5: Document, verify, integrate, and deploy

**Files:**
- Modify: `docs/user-guide.md`
- Verify: all files changed by Tasks 1-4
- Runtime: `/Users/derek/Documents/Projects/ceo-agent-service-release`

- [ ] **Step 1: Update the user guide with exact behavior**

Add a short Workbench note that says:

```markdown
- 首条消息提交成功后，仍使用默认名称的任务会自动采用消息开头作为标题；手动修改过的标题不会被覆盖。
- 工具卡只显示经过安全归一化的类别和状态，不展示原始命令、参数或服务返回。
- 任务结束时仍未收到完成事件的工具会标记为“已中止”；这表示执行记录不完整，不表示工具成功或失败。
```

Do not claim that historical failed turns are retried or repaired.

- [ ] **Step 2: Run focused privacy and behavior gates**

Run:

```bash
./.venv/bin/python -m pytest -q \
  tests/test_workbench_codex_runtime.py \
  tests/test_workbench_store.py \
  tests/test_workbench_api.py \
  tests/test_workbench_executor.py
npm run test --prefix frontend -- --run
npm run build --prefix frontend
./.venv/bin/python -m ruff check \
  app/workbench/codex_runtime.py \
  app/workbench/store.py \
  tests/test_workbench_codex_runtime.py \
  tests/test_workbench_store.py \
  tests/test_workbench_api.py
git diff --check
```

Expected: all focused tests and lint pass; the build succeeds; no cursor value, raw MCP result, command argument, credential, provider session identifier, or absolute path appears in public event assertions.

- [ ] **Step 3: Run the repository regression gate**

Run:

```bash
npm test
```

Expected: the documented Python and frontend suites pass. If macOS sandbox restrictions prevent Unix-socket tests, rerun only those exact tests outside the sandbox and report both results; do not classify sandbox denial as a product failure.

- [ ] **Step 4: Commit documentation and final verification state**

```bash
git add docs/user-guide.md
git commit -m "docs: explain truthful workbench tool states"
git status --short --branch
git log --oneline 62a7415..HEAD
```

Expected: only the intentionally retained local `.venv` symlink is untracked; implementation commits are contiguous on `codex/workbench-runtime-truth`.

- [ ] **Step 5: Integrate without overwriting concurrent production changes**

Before integration, inspect:

```bash
git -C /Users/derek/Documents/Projects/ceo-agent-service-release status --short --branch
git -C /Users/derek/Documents/Projects/ceo-agent-service-release log -5 --oneline
```

If tracked production changes remain, stop and reconcile ownership before merging. Once clean, merge the feature branch without rewriting history:

```bash
git -C /Users/derek/Documents/Projects/ceo-agent-service-release merge --no-ff codex/workbench-runtime-truth
npm run build:workbench --prefix /Users/derek/Documents/Projects/ceo-agent-service-release
```

Expected: merge and production build succeed; generated assets remain ignored.

- [ ] **Step 6: Verify resumability, restart launchd, and read back live state**

Record the current PID and Workbench state, then restart:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
curl -sS http://127.0.0.1:8765/api/workbench/stats
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
lsof -nP -iTCP:8765 -sTCP:LISTEN
curl -sS http://127.0.0.1:8765/
curl -sS http://127.0.0.1:8765/api/workbench/runtime-capabilities
curl -sS http://127.0.0.1:8765/api/workbench/stats
```

Expected: the post-restart PID differs, launchd reports `state = running`, root HTML references existing hashed assets, Codex capability is available, and no new unresolved processing backlog appears. Do not restart until queued/running work has been checked for safe resumability.

- [ ] **Step 7: Perform a new live acceptance turn**

In the browser, create a new task and ask a harmless read-only calendar or email question. Verify:

1. The task title changes from `新任务` to a bounded prefix of the first message.
2. Calendar/email tool cards use Chinese safe labels.
3. A response containing pagination metadata does not fail with `sensitive_provider_output`.
4. The task reaches a truthful terminal state.
5. No tool remains labeled `执行中` after terminal completion, failure, or stop.
6. Inspector status is Chinese and its local timestamp matches the task list.
7. Browser console and network panel contain no new errors and no raw provider result.

Leave the existing failed historical turn unchanged; only its unmatched tool display may now read `已中止`.
