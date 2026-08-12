# Agent Workbench Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the History-first root page with a durable, streaming, three-column Agent workbench that runs Codex through a provider-neutral runtime contract and safely confirms reviewed external actions.

**Architecture:** FastAPI and SQLite remain authoritative. A focused `app/workbench/` package owns task persistence, normalized runtime events, execution recovery, confirmations, API routes, and SSE; a React/Vite application consumes only those normalized resources. `CodexRuntime` is the first production adapter, while shared contract fixtures prove that UI and persistence do not depend on Codex-native fields.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Pydantic 2, Codex CLI JSONL, Server-Sent Events, React 19, TypeScript, Vite, Vitest, Testing Library, React Virtuoso, react-markdown.

---

## File map

**Backend domain and persistence**

- Create `app/workbench/__init__.py`: public workbench package exports.
- Create `app/workbench/models.py`: task, turn, event, input attachment, artifact, confirmation, runtime capability, and API models.
- Create `app/workbench/store.py`: workbench-only persistence operations over the shared SQLite database.
- Modify `app/store.py`: create workbench tables and advance the global schema version.
- Create `tests/test_workbench_store.py`: lifecycle, idempotency, attachment, ordering, isolation, and recovery tests.

**Runtime and execution**

- Create `app/workbench/runtime.py`: provider-neutral protocol, event types, capability contract, and registry.
- Create `app/workbench/codex_runtime.py`: Codex command construction, JSONL normalization, stop, and session resume.
- Create `app/workbench/confirmation_mcp.py`: safe MCP tool that describes a reviewed action without executing it.
- Create `app/workbench/executor.py`: bounded turn claiming, streaming persistence, stop, confirmation, and restart recovery.
- Modify `app/agent_cli.py`: accept a typed, exact one-use write authorization without a process-global environment mutation.
- Create `tests/fixtures/workbench_runtime/codex.jsonl`, `claude.jsonl`, and `pi.jsonl`: provider-shaped inputs for the common contract.
- Create `tests/test_workbench_runtime.py`, `tests/test_workbench_codex_runtime.py`, and `tests/test_workbench_executor.py`.

**HTTP and SSE**

- Create `app/workbench/api.py`: JSON resources, mutation validation, SSE replay, and live subscription.
- Modify `app/audit_web.py`: register workbench routes, serve the React entry point at `/`, and move History to `/history`.
- Create `tests/test_workbench_api.py` and modify `tests/test_audit_web.py`.

**React workbench**

- Create `frontend/package.json`, `frontend/package-lock.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, and `frontend/index.html`.
- Create `frontend/src/api.ts`, `types.ts`, `events.ts`, `app.tsx`, `main.tsx`, and `styles.css`.
- Create focused components under `frontend/src/components/`: `TaskList.tsx`, `ConversationTimeline.tsx`, `ExecutionStep.tsx`, `ConfirmationCard.tsx`, `ArtifactList.tsx`, `TurnInspector.tsx`, and `Composer.tsx`.
- Create matching `*.test.tsx` files plus `frontend/src/test/setup.ts`.
- Build to `app/static/workbench/`; do not commit generated assets because installation builds them locally.

**Installation and documentation**

- Modify `package.json`: root commands delegate to the frontend build and tests while preserving existing API dependencies.
- Modify `docs/agent-installation-runbook.md`: install and build the frontend before service start.
- Modify `README.md`: document the workbench, runtime boundary, streaming, and local build.
- Modify `scripts/install-auto-reply-agents.sh`: fail before launchd installation when compiled workbench assets are absent.

### Task 1: Persist workbench tasks, turns, events, artifacts, and confirmations

**Files:**
- Create: `app/workbench/__init__.py`
- Create: `app/workbench/models.py`
- Create: `app/workbench/store.py`
- Modify: `app/store.py:45-52`
- Modify: `app/store.py` inside `_initialize()` schema script
- Test: `tests/test_workbench_store.py`

- [ ] **Step 1: Write the failing creation and idempotency tests**

```python
from pathlib import Path

import pytest

from app.workbench.models import TurnStatus
from app.workbench.store import WorkbenchStore


def test_create_task_and_idempotent_turn(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="Analyse sales", runtime_kind="codex")

    first = store.create_turn(
        task_id=task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )
    second = store.create_turn(
        task_id=task.id,
        user_text="Compare regions",
        client_request_id="request-1",
    )

    assert first == second
    assert first.status is TurnStatus.QUEUED
    assert store.get_task(task.id) == task


def test_one_non_terminal_turn_per_task(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "worker.sqlite3")
    task = store.create_task(title="One at a time", runtime_kind="codex")
    store.create_turn(task_id=task.id, user_text="first", client_request_id="one")

    with pytest.raises(ValueError, match="task already has an active turn"):
        store.create_turn(task_id=task.id, user_text="second", client_request_id="two")
```

- [ ] **Step 2: Run the focused tests and verify the missing package failure**

Run: `pytest tests/test_workbench_store.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'app.workbench'`.

- [ ] **Step 3: Add strict workbench models**

Implement `app/workbench/models.py` with these exact public types:

```python
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class WorkbenchTask(StrictModel):
    id: str
    title: str
    runtime_kind: str
    provider_session_ref: str = ""
    archived_at: str = ""
    created_at: str
    updated_at: str


class WorkbenchTurn(StrictModel):
    id: str
    task_id: str
    client_request_id: str
    user_text: str
    status: TurnStatus
    stop_requested: bool = False
    final_text: str = ""
    error_code: str = ""
    error_detail: str = ""
    started_at: str = ""
    completed_at: str = ""
    created_at: str
    updated_at: str


class WorkbenchEvent(StrictModel):
    id: int
    turn_id: str
    sequence: int
    event_type: Literal[
        "text_delta", "thinking_summary", "tool_started", "tool_completed",
        "file_changed", "artifact_created", "confirmation_required",
        "status_changed", "turn_completed", "turn_failed",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class WorkbenchArtifact(StrictModel):
    id: str
    turn_id: str
    label: str
    path: str
    media_type: str
    created_at: str


class WorkbenchAttachment(StrictModel):
    id: str
    task_id: str
    filename: str
    media_type: str
    size_bytes: int
    created_at: str


class ConfirmationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXECUTED = "executed"
    FAILED = "failed"


class WorkbenchConfirmation(StrictModel):
    id: str
    turn_id: str
    action_kind: str
    target: str
    summary: str
    risk: str
    arguments_json: str
    status: ConfirmationStatus
    result_json: str = ""
    created_at: str
    decided_at: str = ""
```

Export these models from `app/workbench/__init__.py`.

- [ ] **Step 4: Add the schema in the existing serialized migration path**

Advance `STORE_SCHEMA_VERSION` to `2026-08-13.1`, add all six workbench tables to `STORE_SCHEMA_REQUIRED_TABLES`, and add the following constraints in `_initialize()`:

```sql
create table if not exists workbench_tasks (
    id text primary key,
    title text not null,
    runtime_kind text not null,
    provider_session_ref text not null default '',
    archived_at text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);
create table if not exists workbench_turns (
    id text primary key,
    task_id text not null references workbench_tasks(id),
    client_request_id text not null unique,
    user_text text not null,
    status text not null check(status in (
        'queued','running','waiting_confirmation','completed','stopped','failed'
    )),
    stop_requested integer not null default 0 check(stop_requested in (0,1)),
    final_text text not null default '',
    error_code text not null default '',
    error_detail text not null default '',
    lease_owner text not null default '',
    lease_expires_at text not null default '',
    started_at text not null default '',
    completed_at text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);
create unique index if not exists idx_workbench_one_active_turn
on workbench_turns(task_id)
where status in ('queued','running','waiting_confirmation');
create table if not exists workbench_events (
    id integer primary key autoincrement,
    turn_id text not null references workbench_turns(id),
    sequence integer not null,
    event_type text not null,
    payload_json text not null,
    created_at text not null default current_timestamp,
    unique(turn_id, sequence)
);
create table if not exists workbench_attachments (
    id text primary key,
    task_id text not null references workbench_tasks(id),
    filename text not null,
    media_type text not null,
    size_bytes integer not null check(size_bytes >= 0),
    storage_path text not null,
    created_at text not null default current_timestamp
);
create table if not exists workbench_artifacts (
    id text primary key,
    turn_id text not null references workbench_turns(id),
    label text not null,
    path text not null,
    media_type text not null,
    created_at text not null default current_timestamp
);
create table if not exists workbench_confirmations (
    id text primary key,
    turn_id text not null references workbench_turns(id),
    action_kind text not null,
    target text not null,
    summary text not null,
    risk text not null,
    arguments_json text not null,
    status text not null check(status in ('pending','confirmed','cancelled','executed','failed')),
    result_json text not null default '',
    created_at text not null default current_timestamp,
    decided_at text not null default ''
);
```

- [ ] **Step 5: Implement `WorkbenchStore` with explicit transitions**

Subclass `AutoReplyStore` and implement `create_task`, `get_task`, `list_tasks`, `rename_task`, `archive_task`, `save_attachment`, `list_attachments`, `create_turn`, `claim_next_turn`, `renew_turn_lease`, `request_stop`, `append_event`, `events_after`, `set_provider_session`, `create_confirmation`, `decide_confirmation`, and `complete_turn`. Use `uuid4()` IDs, `begin immediate` for claims and decisions, JSON serialization with `sort_keys=True`, and this transition table:

```python
ALLOWED_TURN_TRANSITIONS = {
    TurnStatus.QUEUED: {TurnStatus.RUNNING, TurnStatus.STOPPED},
    TurnStatus.RUNNING: {
        TurnStatus.WAITING_CONFIRMATION,
        TurnStatus.COMPLETED,
        TurnStatus.STOPPED,
        TurnStatus.FAILED,
    },
    TurnStatus.WAITING_CONFIRMATION: {
        TurnStatus.QUEUED,
        TurnStatus.STOPPED,
        TurnStatus.FAILED,
    },
    TurnStatus.COMPLETED: set(),
    TurnStatus.STOPPED: set(),
    TurnStatus.FAILED: set(),
}
```

Reject blank titles, blank user messages, malformed payload JSON, stale leases, cross-turn confirmation decisions, and invalid transitions. Redact confirmation `arguments_json` from list responses; expose it only to the executor. `save_attachment` accepts decoded bytes only after API validation, generates its own storage filename, and writes below `<db-parent>/workbench/attachments/<task-id>/`; a supplied filename never participates in the filesystem path.

- [ ] **Step 6: Add ordering, isolation, and recovery tests**

Add tests that append sequences 1 and 2, reject another sequence 2, replay only events after ID 1, prevent a confirmation from being decided through another task, reclaim an expired `running` turn as `queued`, leave `waiting_confirmation` unchanged on recovery, and prove an attachment named `../../secret.txt` is stored under the generated task directory without path traversal.

Run: `pytest tests/test_workbench_store.py tests/test_config_runtime_paths.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit the persistence slice**

```bash
git add app/store.py app/workbench/__init__.py app/workbench/models.py app/workbench/store.py tests/test_workbench_store.py
git commit -m "feat: persist workbench tasks and turns"
```

### Task 2: Define and contract-test provider-neutral runtimes

**Files:**
- Create: `app/workbench/runtime.py`
- Create: `tests/test_workbench_runtime.py`
- Create: `tests/fixtures/workbench_runtime/codex.jsonl`
- Create: `tests/fixtures/workbench_runtime/claude.jsonl`
- Create: `tests/fixtures/workbench_runtime/pi.jsonl`

- [ ] **Step 1: Write failing capability and adapter contract tests**

```python
import pytest

from app.workbench.runtime import (
    AgentRuntime,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeRegistry,
)


class FixtureRuntime:
    kind = "fixture"

    def capabilities(self):
        return RuntimeCapabilities(
            session_resume=True,
            streamed_text=True,
            structured_tools=True,
            image_input=False,
            model_selection=False,
            mcp_configuration=False,
            stoppable=True,
            recoverable=True,
        )


def test_registry_resolves_only_registered_runtime():
    registry = RuntimeRegistry([FixtureRuntime()])
    assert registry.get("fixture").kind == "fixture"
    with pytest.raises(KeyError, match="unsupported runtime"):
        registry.get("unknown")


def test_runtime_event_rejects_provider_native_event_name():
    with pytest.raises(ValueError):
        RuntimeEvent(event_type="item.completed", payload={})
```

- [ ] **Step 2: Run the tests and verify they fail on missing types**

Run: `pytest tests/test_workbench_runtime.py -q`

Expected: import fails because `app.workbench.runtime` does not exist.

- [ ] **Step 3: Implement the runtime protocol and registry**

Define immutable `RuntimeCapabilities`, `RuntimeRequest`, `RuntimeEvent`, `RuntimeResult`, and `RuntimeHandle` dataclasses. Define this protocol:

```python
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol


class AgentRuntime(Protocol):
    kind: str

    def capabilities(self) -> RuntimeCapabilities: ...

    def start(
        self,
        request: RuntimeRequest,
        *,
        on_event: Callable[[RuntimeEvent], None],
    ) -> RuntimeHandle: ...

    def wait(self, handle: RuntimeHandle) -> RuntimeResult: ...

    def stop(self, handle: RuntimeHandle) -> None: ...
```

`RuntimeRequest` contains `turn_id`, `workspace: Path`, `prompt`, `provider_session_ref`, `model`, `attachment_paths`, and `image_paths`. `RuntimeEvent.event_type` uses the exact vocabulary in the design. `RuntimeHandle` contains only `run_id` and a private process owner reference; it is never serialized to the browser.

- [ ] **Step 4: Add one shared contract function and three provider-shaped fixtures**

Fixtures must include native text deltas, tool start/result, session identifiers, and terminal output in each provider's distinct shape. The test helper feeds every fixture through its fixture normalizer and asserts the same normalized sequence:

```python
EXPECTED = [
    "text_delta",
    "tool_started",
    "tool_completed",
    "text_delta",
    "turn_completed",
]


@pytest.mark.parametrize("provider", ["codex", "claude", "pi"])
def test_provider_fixture_contract(provider, runtime_fixture):
    events = runtime_fixture(provider)
    assert [event.event_type for event in events] == EXPECTED
    assert all("session_id" not in event.payload for event in events)
```

- [ ] **Step 5: Run and commit the runtime contract**

Run: `pytest tests/test_workbench_runtime.py -q`

Expected: all tests pass.

```bash
git add app/workbench/runtime.py tests/test_workbench_runtime.py tests/fixtures/workbench_runtime
git commit -m "feat: define provider neutral workbench runtime"
```

### Task 3: Adapt Codex JSONL into normalized streaming events

**Files:**
- Create: `app/workbench/codex_runtime.py`
- Create: `app/workbench/confirmation_mcp.py`
- Test: `tests/test_workbench_codex_runtime.py`
- Modify: `tests/test_codex_runner.py`

- [ ] **Step 1: Write failing Codex normalization and resume tests**

```python
from pathlib import Path

from app.workbench.codex_runtime import CodexRuntime
from app.workbench.runtime import RuntimeRequest


def test_codex_runtime_streams_text_tools_and_session(fake_process_executor, tmp_path):
    events = []
    runtime = CodexRuntime(workspace=tmp_path, executor=fake_process_executor)
    handle = runtime.start(
        RuntimeRequest(
            turn_id="turn-1",
            workspace=tmp_path,
            prompt="inspect the repo",
            provider_session_ref="",
            model="",
            attachment_paths=(),
            image_paths=(),
        ),
        on_event=events.append,
    )
    result = runtime.wait(handle)

    assert [event.event_type for event in events] == [
        "status_changed", "tool_started", "tool_completed", "text_delta"
    ]
    assert result.provider_session_ref == "session-1"
    assert result.final_text == "Done"


def test_codex_resume_command_keeps_provider_reference_private(tmp_path):
    runtime = CodexRuntime(workspace=tmp_path)
    command = runtime.build_command(prompt="continue", provider_session_ref="secret-session")
    assert command[:3] == ["codex", "exec", "resume"]
    assert "secret-session" in command
```

- [ ] **Step 2: Run and verify the adapter is missing**

Run: `pytest tests/test_workbench_codex_runtime.py -q`

Expected: import fails for `app.workbench.codex_runtime`.

- [ ] **Step 3: Implement the safe confirmation-request MCP tool**

Create a FastMCP server named `workbench_confirmation` with one read-only tool:

```python
@server.tool(name="request_reviewed_action", annotations=ToolAnnotations(readOnlyHint=True))
def request_reviewed_action(
    argv: list[str],
    target: str,
    summary: str,
    risk: str,
) -> dict[str, object]:
    if not argv or not target.strip() or not summary.strip() or not risk.strip():
        raise ValueError("complete reviewed action details are required")
    return {
        "kind": "reviewed_cli",
        "argv": argv,
        "target": target.strip(),
        "summary": summary.strip(),
        "risk": risk.strip(),
        "executed": False,
    }
```

The tool never imports `subprocess`, `app.agent_cli`, DWS, or an external connector. Add a `main()` that runs stdio transport. Test that the returned object says `executed=False` and rejects sensitive argument names using `is_sensitive_field_name`.

- [ ] **Step 4: Implement `CodexRuntime` by reusing `CodexRunner.build_command`**

Construct Codex with `approval_policy="untrusted"`, `use_approval_bypass=False`, `use_output_schema=False`, and a developer instruction requiring all reviewed external writes to call `workbench_confirmation.request_reviewed_action` rather than execute directly. Inject only the confirmation server overlay into the command; preserve the user's authenticated Codex configuration and skills.

Use `run_process_with_idle_timeout(..., on_stdout_line=...)`. Normalize:

- `thread.started` → capture provider session reference, emit `status_changed` without the reference.
- `item.started` command/MCP calls → `tool_started` with redacted name and summary.
- `item.completed` command/MCP calls → `tool_completed`.
- assistant message deltas or completed messages → non-duplicated `text_delta`.
- completed `request_reviewed_action` → `confirmation_required` with its safe returned fields.
- `turn.completed` → final result; do not emit terminal event here because the executor owns the persisted terminal state.

Reject malformed JSON after the first valid JSONL event, outputs larger than existing process limits, credential-bearing payloads, and multiple conflicting session references.

- [ ] **Step 5: Test stop and no-duplicate text behavior**

Add a fake long-running process test where `stop()` terminates the owned process group and returns status `stopped`. Add a fixture containing both delta and completed assistant text; assert the final text appears once.

Run: `pytest tests/test_workbench_codex_runtime.py tests/test_codex_runner.py tests/test_process_runner.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the Codex adapter**

```bash
git add app/workbench/codex_runtime.py app/workbench/confirmation_mcp.py tests/test_workbench_codex_runtime.py tests/test_codex_runner.py
git commit -m "feat: stream Codex workbench events"
```

### Task 4: Execute, stop, recover, and confirm turns

**Files:**
- Create: `app/workbench/executor.py`
- Modify: `app/agent_cli.py`
- Test: `tests/test_workbench_executor.py`
- Test: `tests/test_agent_cli.py`

- [ ] **Step 1: Write failing executor lifecycle tests**

```python
def test_executor_persists_stream_and_terminal_result(store, fake_runtime):
    task = store.create_task(title="Run", runtime_kind="fixture")
    turn = store.create_turn(task_id=task.id, user_text="do it", client_request_id="r1")
    executor = WorkbenchExecutor(store, RuntimeRegistry([fake_runtime]), workspace=Path("/workspace"))

    executor.run_once()

    assert store.get_turn(turn.id).status is TurnStatus.COMPLETED
    assert [e.event_type for e in store.events_after(turn.id, 0)][-1] == "turn_completed"


def test_restart_requeues_expired_running_but_not_confirmation(store):
    running = seed_expired_running_turn(store)
    waiting = seed_waiting_confirmation_turn(store)

    recovered = WorkbenchExecutor(store, RuntimeRegistry([]), workspace=Path("/workspace")).recover()

    assert running.id in recovered
    assert store.get_turn(running.id).status is TurnStatus.QUEUED
    assert store.get_turn(waiting.id).status is TurnStatus.WAITING_CONFIRMATION
```

- [ ] **Step 2: Run and verify `WorkbenchExecutor` is missing**

Run: `pytest tests/test_workbench_executor.py -q`

Expected: import fails for `app.workbench.executor`.

- [ ] **Step 3: Replace process-global recovery authorization with an exact object**

In `app/agent_cli.py`, add:

```python
@dataclass(frozen=True)
class ReviewedWriteAuthorization:
    authorization_id: str
    action_index: int
    capability: str
    operation: str
    operation_digest: str
    target_identifiers: tuple[str, ...]
    arguments_digest: str
```

Add `review_write_authorization(argv, authorization_id, action_index, classifier=None)` to classify and freeze the exact action. Extend `execute_reviewed_write(..., authorization=None)` so an explicit authorization is checked against the actual classified command and argument digest. Keep the existing environment allowlist path only for current recovery callers; never set or mutate the environment from the workbench executor.

Test that changing one argument, target, operation, or authorization ID rejects execution before the process runner is called.

- [ ] **Step 4: Implement the bounded executor**

`WorkbenchExecutor` owns a `ThreadPoolExecutor(max_workers=2)`, a runtime registry, active handles keyed by turn ID, and a per-task lock. `run_once()` atomically claims queued turns, starts their selected runtime, persists every normalized event, renews leases, and completes terminal state.

When `confirmation_required` arrives:

1. Persist one `WorkbenchConfirmation` containing the exact reviewed argv and safe display fields.
2. Persist the event.
3. Mark the turn `waiting_confirmation`.
4. Stop the runtime handle after its current safe confirmation tool result.

`stop(turn_id)` sets `stop_requested`, stops an active handle if present, and idempotently transitions queued/running/waiting turns to `stopped` with one terminal event.

- [ ] **Step 5: Implement confirm and cancel without blind replay**

`confirm(confirmation_id)` must atomically change `pending` to `confirmed`, build `ReviewedWriteAuthorization` from the stored exact argv, call `execute_reviewed_write` once, store its sanitized receipt, then either:

- mark the confirmation `executed`, requeue the same turn, and resume the provider session with a prompt containing the sanitized receipt; or
- mark it `failed` and requeue the same turn with the failure code so the Agent reports the real blocker.

`cancel(confirmation_id)` changes `pending` to `cancelled`, requeues the turn, and resumes with an explicit cancellation statement. A repeated confirm/cancel returns the already persisted decision and never reruns the command.

- [ ] **Step 6: Test stop, confirmation, duplicate decisions, and recovery**

Use a fake runtime and fake reviewed write runner. Assert stop terminates once, confirm executes once, duplicate confirm executes zero additional times, cancel never calls the write runner, and a service restart does not execute a `confirmed` action whose result is absent—instead it moves the turn to failed reconciliation with a visible recovery condition.

Run: `pytest tests/test_workbench_executor.py tests/test_agent_cli.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit execution and confirmation**

```bash
git add app/workbench/executor.py app/agent_cli.py tests/test_workbench_executor.py tests/test_agent_cli.py
git commit -m "feat: execute and confirm workbench turns"
```

### Task 5: Expose protected JSON resources and replayable SSE

**Files:**
- Create: `app/workbench/api.py`
- Modify: `app/audit_web.py`
- Create: `tests/test_workbench_api.py`
- Modify: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing API lifecycle and replay tests**

```python
def test_task_turn_and_event_replay(client):
    task = client.post("/api/workbench/tasks", json={"title": "New task", "runtime_kind": "codex"}).json()
    turn = client.post(
        f"/api/workbench/tasks/{task['id']}/turns",
        json={"text": "Inspect the repo", "client_request_id": "request-1"},
    ).json()

    response = client.get(f"/api/workbench/turns/{turn['id']}/events?after=0")

    assert response.status_code == 200
    assert response.json()[0]["event_type"] == "status_changed"


def test_cross_origin_mutation_is_rejected(client):
    response = client.post(
        "/api/workbench/tasks",
        json={"title": "Blocked", "runtime_kind": "codex"},
        headers={"origin": "https://attacker.example"},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run and verify routes return 404**

Run: `pytest tests/test_workbench_api.py -q`

Expected: requests return 404 because routes are unregistered.

- [ ] **Step 3: Implement a route registrar with no global mutable app state**

Create `register_workbench_routes(app, store, executor, runtime_registry, asset_dir)`. Add endpoints for task list/create/read/rename/archive, bounded attachment upload/list, turn create/read/stop, timeline read, event replay, confirmation confirm/cancel, runtime capability list, global statistics, and artifact download.

Reuse `_require_trusted_json_mutation` for JSON writes. Attachment upload accepts `{filename, media_type, content_base64}` JSON, validates strict Base64 before decoding, permits at most 20 MiB decoded content, and returns metadata without a storage path. Validate task ownership on every nested route. Artifact downloads resolve the stored path, require it to be inside the configured workspace or a dedicated workbench output directory, and never return arbitrary local paths in JSON.

- [ ] **Step 4: Implement SSE with persisted replay first**

The endpoint `/api/workbench/turns/{turn_id}/events/stream` must:

```python
async def stream():
    cursor = request.headers.get("last-event-id") or request.query_params.get("after", "0")
    for event in store.events_after(turn_id, int(cursor)):
        yield encode_sse(event)
        cursor = str(event.id)
    async for event in broker.subscribe(turn_id, after_id=int(cursor)):
        yield encode_sse(event)
```

Emit `id`, `event`, and JSON `data`; send `: keepalive` at most every 15 seconds of inactivity. Set `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Subscription notifications are wakeups only; every delivered event is read from SQLite so a process-local queue is never the source of truth.

- [ ] **Step 5: Move History to `/history` and reserve `/` for the workbench**

Extract the existing root History handler into a helper used by `/history`. Preserve all query parameters, busy-page behavior, cached default rendering, and Tutorial redirect. Change brand/history links and tests accordingly. Until frontend assets exist, `/` returns a clear 503 page naming `npm run build:workbench`; after assets exist it serves `index.html`.

- [ ] **Step 6: Run focused API and existing audit tests**

Run: `pytest tests/test_workbench_api.py tests/test_audit_web.py -q`

Expected: all tests pass, `/history` renders the old page, `/` is the workbench entry, and no existing mutation protection regresses.

- [ ] **Step 7: Commit the API slice**

```bash
git add app/workbench/api.py app/audit_web.py tests/test_workbench_api.py tests/test_audit_web.py
git commit -m "feat: expose workbench API and event stream"
```

### Task 6: Scaffold the independently authored React workbench

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app.tsx`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/components/TaskList.tsx`
- Create: `frontend/src/components/TaskList.test.tsx`

- [ ] **Step 1: Write the failing task-list component test**

```tsx
import { render, screen } from "@testing-library/react";
import { TaskList } from "./TaskList";

it("shows truthful task states and starts a new task", async () => {
  render(
    <TaskList
      tasks={[{ id: "t1", title: "Sales", runtime_kind: "codex", state: "running", updated_at: "2026-08-13 10:00:00" }]}
      activeTaskId="t1"
      onSelect={() => undefined}
      onNewTask={() => undefined}
    />,
  );
  expect(screen.getByText("Sales")).toBeInTheDocument();
  expect(screen.getByText("执行中")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "新任务" })).toBeEnabled();
});
```

- [ ] **Step 2: Add the Vite/Vitest toolchain and verify the missing component failure**

Use React 19, Vite, TypeScript, Vitest, Testing Library, jsdom, React Virtuoso, `react-markdown`, `remark-gfm`, `lucide-react`, and `clsx`. Do not depend on `@multica/*` or copy Multica source.

Run: `npm install --prefix frontend && npm test --prefix frontend -- --run`

Expected: test fails because `TaskList.tsx` is missing.

- [ ] **Step 3: Define API types and a single fetch boundary**

`types.ts` mirrors public JSON models but excludes provider session references, attachment storage paths, and confirmation arguments. `api.ts` exports `listTasks`, `createTask`, `renameTask`, `archiveTask`, `uploadAttachment`, `createTurn`, `stopTurn`, `confirmAction`, `cancelAction`, `getTimeline`, `getTurn`, `getStats`, and `runtimeCapabilities`. Every mutation sends `Content-Type: application/json`; non-2xx responses become an `ApiError` with safe server detail.

- [ ] **Step 4: Implement the three-column shell and task list**

`App` owns selected task ID in the URL query parameter `task`, loads resources from the API, and renders semantic `<aside>`, `<section>`, and `<aside>` columns. `TaskList` groups by Today, Yesterday, and Earlier, supports search, new, rename, and archive, and maps only persisted states to Chinese labels.

Use repository-owned CSS variables and responsive rules. At widths below 900px, collapse the right inspector into a drawer; below 680px, show either list or conversation with an explicit back control.

- [ ] **Step 5: Run unit tests and production build**

Run: `npm test --prefix frontend -- --run && npm run build --prefix frontend`

Expected: tests pass and Vite writes `frontend/dist/index.html` plus hashed assets.

- [ ] **Step 6: Commit the frontend foundation**

```bash
git add frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts frontend/index.html frontend/src
git commit -m "feat: add React workbench shell"
```

### Task 7: Render streaming conversation, confirmations, artifacts, and statistics

**Files:**
- Create: `frontend/src/events.ts`
- Create: `frontend/src/components/ConversationTimeline.tsx`
- Create: `frontend/src/components/ExecutionStep.tsx`
- Create: `frontend/src/components/ConfirmationCard.tsx`
- Create: `frontend/src/components/ArtifactList.tsx`
- Create: `frontend/src/components/TurnInspector.tsx`
- Create: `frontend/src/components/Composer.tsx`
- Create: corresponding `frontend/src/components/*.test.tsx`
- Modify: `frontend/src/app.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing reconnect and no-duplicate delta tests**

```tsx
it("appends streamed text once and reconnects after the last event", async () => {
  const source = new FakeEventSource();
  render(<ConversationTimeline initialItems={[]} eventSourceFactory={() => source} turnId="turn-1" />);

  source.emit({ id: "10", event: "text_delta", data: { text: "Hello " } });
  source.emit({ id: "11", event: "text_delta", data: { text: "world" } });
  source.reconnect();
  source.emit({ id: "11", event: "text_delta", data: { text: "world" } });

  expect(await screen.findByText("Hello world")).toBeInTheDocument();
  expect(source.lastUrl).toContain("after=11");
});
```

- [ ] **Step 2: Run and verify missing streaming components**

Run: `npm test --prefix frontend -- --run`

Expected: imports fail for the new components.

- [ ] **Step 3: Implement the persisted-event reducer and SSE client**

`events.ts` validates event names, drops IDs less than or equal to the last applied ID, coalesces adjacent `text_delta` events for the same assistant turn, and preserves tool/confirmation/artifact order. The EventSource URL includes `after=<lastId>`; native automatic reconnect is allowed, and a terminal event closes the source. On parse failure, keep prior content and show a recoverable connection error without inventing task failure.

- [ ] **Step 4: Implement timeline and stable streaming Markdown**

Use React Virtuoso with stable keys `turn:<turnId>:assistant`, `event:<eventId>`, and `confirmation:<id>`. Completed paragraphs are memoized; only the active Markdown block rerenders during text streaming. Sanitize raw HTML, render fenced code and GFM, and route artifact links through the protected artifact endpoint.

`ExecutionStep` shows tool name, understandable summary, running/completed/failed state, and a collapsed detail. It never renders raw environment variables, provider session references, command output containing credentials, or local absolute paths.

- [ ] **Step 5: Implement confirmation, artifacts, inspector, and composer**

`ConfirmationCard` displays action, exact human-readable target, effect, and risk, then disables both buttons after the first decision request. `ArtifactList` opens protected downloads. `TurnInspector` shows persisted duration, tool/file/artifact/error counts and checklist. `Composer` supports text, Enter-to-send, Shift+Enter, bounded file upload before turn creation, Stop while active, and disabled send while another turn is non-terminal. A failed upload remains visible and prevents send until removed or retried.

- [ ] **Step 6: Test all visible states and responsive controls**

Cover queued, running, waiting confirmation, completed, stopped, and failed; task switching while another task runs; confirmed/cancelled button state; stop idempotency; missing runtime capability explanations; and mobile drawer/back controls.

Run: `npm test --prefix frontend -- --run && npm run build --prefix frontend`

Expected: all tests and production build pass.

- [ ] **Step 7: Commit the interactive UI**

```bash
git add frontend/src
git commit -m "feat: add streaming workbench conversation"
```

### Task 8: Integrate the frontend build and service lifecycle

**Files:**
- Modify: `frontend/vite.config.ts`
- Modify: `package.json`
- Modify: `app/audit_web.py`
- Modify: `scripts/install-auto-reply-agents.sh`
- Modify: `docs/agent-installation-runbook.md`
- Modify: `README.md`
- Test: `tests/test_audit_web.py`
- Test: `tests/test_service_supervisor.py`

- [ ] **Step 1: Write failing asset and History navigation tests**

```python
def test_root_serves_built_workbench_and_history_remains_available(tmp_path, monkeypatch):
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    (asset_dir / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    monkeypatch.setattr(audit_web_module, "_workbench_asset_dir", lambda: asset_dir)
    complete_setup_wizard(AutoReplyStore(tmp_path / "worker.sqlite3"))
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    assert client.get("/").text == '<div id="root"></div>'
    assert "最近 24 小时事件" in client.get("/history").text
```

- [ ] **Step 2: Run and verify the frontend path is not integrated**

Run: `pytest tests/test_audit_web.py::test_root_serves_built_workbench_and_history_remains_available -q`

Expected: failure because `_workbench_asset_dir` and root asset serving are absent.

- [ ] **Step 3: Build directly into the FastAPI static directory**

Set Vite `base` to `/workbench-assets/` and `build.outDir` to `../app/static/workbench`. Add root scripts:

```json
{
  "scripts": {
    "build:workbench": "npm run build --prefix frontend",
    "test:workbench": "npm test --prefix frontend -- --run",
    "test": "pytest && npm run test:workbench"
  }
}
```

Mount hashed assets at `/workbench-assets`, serve `app/static/workbench/index.html` at `/`, and keep all API paths outside the SPA. Add `app/static/workbench/` to `.gitignore`; generated files must be built on each installed checkout.

- [ ] **Step 4: Make installation verify compiled assets**

Before installing the plist, `scripts/install-auto-reply-agents.sh` checks `app/static/workbench/index.html`. If absent, it exits with: `workbench assets missing; run npm install --prefix frontend && npm run build:workbench`. Do not silently run npm during a service-control operation.

Document the build commands in the installation runbook and README. Explain that existing audit pages remain server-rendered at `/history`, `/tasks`, `/workers`, and `/settings`.

- [ ] **Step 5: Run backend, frontend, and build checks**

Run:

```bash
npm install --prefix frontend
npm run test:workbench
npm run build:workbench
pytest tests/test_workbench_api.py tests/test_audit_web.py tests/test_service_supervisor.py -q
```

Expected: all tests pass, `app/static/workbench/index.html` exists, and asset URLs begin with `/workbench-assets/`.

- [ ] **Step 6: Commit integration and documentation**

```bash
git add .gitignore package.json frontend/vite.config.ts app/audit_web.py scripts/install-auto-reply-agents.sh docs/agent-installation-runbook.md README.md tests/test_audit_web.py tests/test_service_supervisor.py
git commit -m "feat: serve the built Agent workbench"
```

### Task 9: Verify end to end, restart launchd, and prove live recovery

**Files:**
- Create: `tests/e2e/test_workbench_live.py`
- Modify: `docs/user-guide.md`

- [ ] **Step 1: Add a deterministic local end-to-end test**

Use a fixture runtime, temporary SQLite database, and TestClient. The test creates a task, streams two deltas and one tool event, refreshes the timeline from the last event ID, stops a second turn, and confirms a fake reviewed action. Assert no duplicate event IDs, accurate terminal states, and one execution of the confirmed action.

Run: `pytest tests/e2e/test_workbench_live.py -q`

Expected: pass without network, real Codex, or external writes.

- [ ] **Step 2: Run all scoped verification**

```bash
pytest tests/test_workbench_store.py tests/test_workbench_runtime.py tests/test_workbench_codex_runtime.py tests/test_workbench_executor.py tests/test_workbench_api.py tests/test_audit_web.py tests/test_agent_cli.py tests/e2e/test_workbench_live.py -q
npm run test:workbench
npm run build:workbench
git diff --check
```

Expected: all tests pass, production frontend build succeeds, and `git diff --check` is silent.

- [ ] **Step 3: Run the full repository suite and report unrelated failures separately**

Run: `pytest -q`

Expected: all tests pass. If a pre-existing unrelated failure remains, record its exact test and evidence; do not describe the full suite as passing.

- [ ] **Step 4: Document user-visible workbench behavior**

Update `docs/user-guide.md` with creating and continuing tasks, choosing a runtime, streamed progress, stopping, confirming/cancelling, artifacts, state meanings, and History diagnostics. State that first-release production execution is Codex; Claude/Pi are contract-compatible but not yet production adapters.

- [ ] **Step 5: Commit tests and user documentation**

```bash
git add tests/e2e/test_workbench_live.py docs/user-guide.md
git commit -m "test: verify Agent workbench workflow"
```

- [ ] **Step 6: Verify resumable state before restarting the service**

Using the configured runtime database, read workbench turns in `queued`, `running`, and `waiting_confirmation`. Confirm that running turns have persisted events and that no confirmation is in an ambiguous `confirmed` state without a receipt. Do not alter production rows during this check.

- [ ] **Step 7: Restart the launchd service and verify a new process**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: launchd reports `state = running` with a new PID. This restart is required because Python runtime, routing, and service behavior changed.

- [ ] **Step 8: Perform live readback without unauthorized effects**

Open `http://127.0.0.1:8765/`, create a Codex task that reads one repository file and summarizes it, refresh while it runs, and verify streamed events resume without duplication. Trigger a reviewed test action that stops at confirmation, cancel it, and verify no external action occurred.

Read back `/history`, `/workers`, and the SQLite workbench terminal state. Confirm there is no new unresolved `failed` or `processing` backlog and no workbench turn remains stuck in `running` with an expired lease.
