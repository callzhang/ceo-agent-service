# Agent Tab and White-Box Tool Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Agent as the default homepage while exposing every original audit feature through shared tabs, and render each new command or MCP call with its exact provider-supplied action and result.

**Architecture:** Add symmetric navigation to the React Workbench and server-rendered audit console. Extend the Codex adapter's normalized tool events, pass those fields unchanged through the local public API, validate bounded recursive JSON in the frontend, and merge start/completion records into a detailed execution card.

**Tech Stack:** Python 3.12, FastAPI, SQLite, React 19, TypeScript, Vitest, Testing Library, Vite.

---

### Task 1: Shared Agent Navigation

**Files:**
- Create: `frontend/src/components/GlobalNav.tsx`
- Create: `frontend/src/components/GlobalNav.test.tsx`
- Modify: `frontend/src/app.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `app/audit_web.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing frontend and backend navigation tests**

Add a component test that renders `GlobalNav`, asserts the six exact links and routes, and asserts `Agent` has `aria-current="page"`. Add an audit test asserting `_top_nav("history")` begins with an Agent link and still marks History active.

```tsx
expect(screen.getByRole("link", { name: "Agent" })).toHaveAttribute("href", "/");
expect(screen.getByRole("link", { name: "History" })).toHaveAttribute("href", "/history");
expect(screen.getByText("Agent")).toHaveAttribute("aria-current", "page");
```

```python
html = _top_nav("history")
assert html.index('href="/"') < html.index('href="/history"')
assert 'aria-current="page">History' in html
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
npm run test --prefix frontend -- --run src/components/GlobalNav.test.tsx
.venv/bin/python -m pytest -q tests/test_audit_web.py::test_top_nav_exposes_agent_home
```

Expected: frontend import is missing and backend Agent link assertion fails.

- [ ] **Step 3: Implement shared navigation**

Create `GlobalNav` from a fixed array of the six confirmed application routes. Render it before `.workbench-shell` in `App`. Add `.global-nav` styles with horizontal overflow and non-wrapping links. Add `("agent", "Agent", "/")` as the first server navigation item.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the two commands from Step 2 plus:

```bash
npm run test --prefix frontend -- --run src/app.test.tsx
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/GlobalNav.tsx frontend/src/components/GlobalNav.test.tsx frontend/src/app.tsx frontend/src/styles.css app/audit_web.py tests/test_audit_web.py
git commit -m "feat: add shared agent navigation"
```

### Task 2: Normalize Exact Command and MCP Events

**Files:**
- Modify: `app/workbench/codex_runtime.py`
- Test: `tests/test_workbench_codex_runtime.py`

- [ ] **Step 1: Write failing adapter tests**

Add one command pair with `command`, `cwd`, `aggregated_output`, and `exit_code`; assert start and completion contain `kind`, `name`, `native_id`, exact action fields, and `provider_item`. Add one MCP pair with exact `server`, `tool`, `arguments`, and `result`; assert both events are self-describing and no generic label appears.

```python
assert started.payload["command"] == "rg --files frontend/src"
assert completed.payload["output"] == "frontend/src/app.tsx\n"
assert completed.payload["provider_item"]["exit_code"] == 0
assert mcp_completed.payload["name"] == "codex_apps.google_calendar.search_events"
assert mcp_completed.payload["arguments"] == {"time_min": "2026-08-14T00:00:00+08:00"}
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_workbench_codex_runtime.py -k 'white_box or exact_tool_identity'
```

Expected: payload fields are absent and names remain generic.

- [ ] **Step 3: Implement bounded tool snapshots**

Replace the correlation-only map with a map containing `tool_call_id` and the normalized start snapshot. Build tool payloads from JSON-compatible provider item values. Command names use the first executable token when a command is present; MCP names join exact server and tool. Completion merges the start snapshot with completion fields and repeats action identity.

The normalized mapping is:

```python
{
    "tool_call_id": correlation_id,
    "kind": "command" | "mcp",
    "name": exact_name,
    "native_id": native_id,
    "status": "running" | "completed" | "failed",
    "command": command,
    "cwd": cwd,
    "exit_code": exit_code,
    "output": aggregated_output,
    "server": server,
    "tool": tool,
    "arguments": arguments,
    "result": result,
    "provider_item": dict(item),
}
```

Omit fields not supplied by the provider. Do not copy process environment or unrelated records.

- [ ] **Step 4: Preserve correlation and confirmation behavior**

Keep duplicate start, uncorrelated completion, 128-open-call, malformed provider, failure classification, and confirmation extraction behavior unchanged. Update existing label tests to exact identity expectations.

- [ ] **Step 5: Run runtime tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_workbench_codex_runtime.py tests/test_workbench_runtime.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/workbench/codex_runtime.py tests/test_workbench_codex_runtime.py
git commit -m "feat: publish white-box tool events"
```

### Task 3: Preserve White-Box Fields Through API and SSE

**Files:**
- Modify: `app/workbench/api.py`
- Modify: `frontend/src/events.ts`
- Modify: `frontend/src/api.ts`
- Test: `tests/test_workbench_api.py`
- Test: `frontend/src/events.test.ts`
- Test: `frontend/src/api.test.ts`

- [ ] **Step 1: Write failing API projection tests**

Persist command and MCP events containing nested objects, arrays, exact paths, output, and numeric exit codes. Assert timeline, event-list, and SSE responses preserve the complete tool payload.

```python
assert response_event["payload"] == payload
assert response_event["payload"]["cwd"] == str(tmp_path)
assert response_event["payload"]["exit_code"] == 0
```

- [ ] **Step 2: Write failing frontend parser tests**

Assert initial API normalization and `parseStreamEvent` accept bounded nested tool JSON and reject functions, undefined values, excessive nesting, and unknown top-level fields.

```ts
expect(parseStreamEvent(JSON.stringify(toolEvent), "tool_completed")).toEqual(toolEvent);
expect(isPublicEventPayload("tool_completed", { ...payload, unexpected: true })).toBe(false);
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_workbench_api.py -k white_box
npm run test --prefix frontend -- --run src/events.test.ts src/api.test.ts
```

Expected: backend strips fields and frontend rejects non-string values.

- [ ] **Step 4: Implement exact tool API projection**

Define explicit allowed fields for command and MCP tool payloads. For `tool_started` and `tool_completed`, validate structure and return those fields without `_safe_public_value` path or content rewriting. Keep the existing projection for every other event type.

- [ ] **Step 5: Implement recursive frontend JSON validation**

Accept JSON primitives, arrays, and plain objects with a maximum nesting depth matching the backend boundary. Keep exact top-level field allowlists per event type. Update API normalization to retain nested values rather than coercing them to strings.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run commands from Step 3 plus the complete Workbench API suite.

- [ ] **Step 7: Commit**

```bash
git add app/workbench/api.py frontend/src/events.ts frontend/src/api.ts tests/test_workbench_api.py frontend/src/events.test.ts frontend/src/api.test.ts
git commit -m "feat: expose exact local tool details"
```

### Task 4: Render Detailed White-Box Execution Cards

**Files:**
- Modify: `frontend/src/events.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/ExecutionStep.tsx`
- Modify: `frontend/src/components/ConversationTimeline.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/events.test.ts`
- Test: `frontend/src/components/ExecutionStep.test.tsx`
- Test: `frontend/src/components/ConversationTimeline.test.tsx`

- [ ] **Step 1: Write failing reducer tests**

Assert completion merges with start details, standalone completion renders independently, event timestamps become start/completion timestamps, and aborted synthesis preserves every detail field.

```ts
expect(block.payload).toMatchObject({ command: "pwd", output: "/workspace", status: "completed" });
expect(block.startedAt).toBe("2026-08-14 00:00:00");
expect(block.completedAt).toBe("2026-08-14 00:00:02");
```

- [ ] **Step 2: Write failing component tests**

Assert the collapsed header contains the exact command or `server.tool`. Expand the card and assert command/cwd, formatted arguments, output/result, raw provider JSON, IDs, timestamps, and duration. Assert a legacy generic event says `历史事件未记录命令详情`.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
npm run test --prefix frontend -- --run src/events.test.ts src/components/ExecutionStep.test.tsx src/components/ConversationTimeline.test.tsx
```

Expected: reducer loses start data and component hides details.

- [ ] **Step 4: Implement reducer merge and timestamps**

Extend `TimelineBlock` with `startedAt` and `completedAt`. Merge completion payload over start payload rather than replacing it. For aborted synthesis, change only status and explanatory summary.

- [ ] **Step 5: Implement execution detail layout**

Remove content redaction from tool details. Render exact values in semantic definition lists and `<pre>` blocks. Use stable JSON pretty-printing for arguments, result, and provider item. Preserve control-character filtering required for valid DOM text, without semantic replacement.

- [ ] **Step 6: Run focused and full frontend tests**

Run:

```bash
npm run test --prefix frontend -- --run
npm run build:workbench
```

Expected: all frontend tests pass and the production build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/events.ts frontend/src/types.ts frontend/src/components/ExecutionStep.tsx frontend/src/components/ConversationTimeline.tsx frontend/src/styles.css frontend/src/events.test.ts frontend/src/components/ExecutionStep.test.tsx frontend/src/components/ConversationTimeline.test.tsx
git commit -m "feat: render transparent tool execution details"
```

### Task 5: Documentation, Verification, Integration, and Deployment

**Files:**
- Modify: `docs/user-guide.md`
- Modify: `docs/superpowers/plans/2026-08-14-workbench-tabs-white-box-tools.md`

- [ ] **Step 1: Update user documentation**

Document the Agent tab, routes to original functions, white-box command/MCP fields, historical-event limitation, and the fact that confirmation remains required for writes.

- [ ] **Step 2: Run focused backend verification**

```bash
.venv/bin/python -m pytest -q tests/test_workbench_codex_runtime.py tests/test_workbench_runtime.py tests/test_workbench_api.py tests/test_audit_web.py tests/test_workbench_browser.py
```

- [ ] **Step 3: Run frontend verification and build**

```bash
npm run test --prefix frontend -- --run
npm run build:workbench
```

- [ ] **Step 4: Run repository gates**

```bash
.venv/bin/python -m ruff check app/audit_web.py app/workbench/api.py app/workbench/codex_runtime.py tests/test_audit_web.py tests/test_workbench_api.py tests/test_workbench_codex_runtime.py
git diff --check
env -u CEO_MAX_BATCHES npm test
```

Run Unix-socket tests outside the filesystem/network sandbox if they are denied there. Expected: all repository tests pass.

- [ ] **Step 5: Commit documentation and plan completion**

```bash
git add docs/user-guide.md docs/superpowers/plans/2026-08-14-workbench-tabs-white-box-tools.md
git commit -m "docs: explain agent tabs and white-box tools"
```

- [ ] **Step 6: Merge into the production release checkout**

Confirm the release checkout contains only its known `.venv` symlink, merge `codex/workbench-tabs-transparency` into `codex/agent-workbench-production`, and rerun focused tests plus the frontend build from the merged tree.

- [ ] **Step 7: Restart and verify production**

Before restart, verify reply tasks, work-summary inputs, meeting jobs, and persisted external actions are resumable and idempotent. Then restart:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main
```

Verify a new supervisor PID and listener PID, Workbench runtime availability, zero queued/running/waiting turns, zero unresolved processing rows, and no new failed external actions.

- [ ] **Step 8: Perform live browser acceptance**

Use the in-app browser to verify all six tabs, bidirectional navigation, exact command details, exact MCP details, persistence after refresh, mobile navigation, and an error-free console.
