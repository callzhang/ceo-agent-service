# Agent Runtime Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Universal planner/validator/executor stack with one native Codex Agent run that directly reads evidence and executes configured CLI/MCP tools, while retaining only channel readiness, exact run ownership, unknown-side-effect reconciliation, audit redaction, and the agreed business rules.

**Architecture:** DingTalk and Lark readiness are checked by typed status-plus-live-probe gates before producer or consumer work. Each `reply_task` execution generation owns one `agent_runs` row and one native `codex exec` session; the Agent receives raw context and material read commands, performs its own reads and writes, and returns a minimal terminal result. Process events are persisted while Codex is running so a crashed write can be reconciled read-only instead of blindly replayed.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, native Codex CLI JSONL, DWS CLI, Lark CLI, configured MCP servers, FastAPI History UI, pytest, launchd.

---

## Scope And File Map

This is one subsystem: queued CEO tasks from channel discovery through Agent execution and History. Existing standalone OKR, meeting-alignment, task-summary, and WeChat ingestion jobs remain intact unless they currently enter the Universal queued-task path.

**Create:**

- `app/channel_gate.py`: typed status-plus-live-probe gates and one-login coordination.
- `app/agent_result.py`: minimal Agent result types and local JSONL result parsing.
- `app/agent_context.py`: raw trigger/context/material-reference rendering with no service-side body resolution.
- `app/agent_runner.py`: one native Codex run, incremental event capture, session resume, and result return.
- `app/schemas/agent_result.schema.json`: native Codex output schema for the minimal result.
- `tests/test_channel_gate.py`: gate classification and login suppression.
- `tests/test_agent_result.py`: strict result parsing and local normalization.
- `tests/test_agent_context.py`: prompt boundaries and material command rendering.
- `tests/test_agent_runner.py`: native command, event persistence, resume, and failure semantics.
- `tests/test_agent_runtime_worker.py`: worker cutover, final-state mapping, and reconciliation.

**Modify:**

- `app/process_runner.py`: optional stdout-line callback for durable Codex event capture.
- `app/store.py`: `AgentRun` model, `agent_runs` schema, claim/update/reconciliation APIs, and removal of Universal persistence.
- `app/worker.py`: gate producer/consumer passes, invoke the direct Agent, persist final attempts, reconcile unknown runs, and remove service-owned business execution/material reading.
- `app/codex_runner.py`: preserve the user's native CLI/MCP configuration and expose the shared direct-exec command builder without `--ignore-user-config`.
- `app/history.py`, `app/audit_web.py`, `app/setup_wizard.py`: render Agent outcomes and gate state; remove Universal internals.
- `app/cli.py`: channel doctor uses the same gate implementation as the service.
- `tests/test_process_runner.py`, `tests/test_store.py`, `tests/test_worker.py`, `tests/test_audit_web.py`, `tests/test_cli.py`, `tests/test_setup_wizard.py`: focused regressions for the new path.
- `docs/architecture.md`, `docs/reply-worker-reliability.md`, `README.md`: document the authoritative runtime and remove auth-archive guidance.

**Delete after cutover:**

- `app/universal_consumer.py`
- `app/universal_context.py`
- `app/universal_executor.py`
- `app/universal_plan.py`
- `app/universal_planner.py`
- `app/universal_validator.py`
- `tests/test_universal_consumer.py`
- `tests/test_universal_context.py`
- `tests/test_universal_context_enrichment.py`
- `tests/test_universal_executor.py`
- `tests/test_universal_memory.py`
- `tests/test_universal_okr.py`
- `tests/test_universal_parity.py`
- `tests/test_universal_plan.py`
- `tests/test_universal_planner.py`
- `tests/test_universal_validator.py`
- `tests/test_universal_worker.py`
- `tests/test_universal_worker_wiring.py`

**Delete with the gate replacement in Task 1:**

- `app/channels/__init__.py`
- `app/channels/models.py`
- `app/channels/dingtalk.py`
- `app/channels/feishu.py`
- `app/channels/enqueue.py`
- `tests/test_channels.py`

### Task 1: Typed DWS And Lark Gates

**Files:**
- Create: `app/channel_gate.py`
- Create: `tests/test_channel_gate.py`
- Modify: `app/cli.py`
- Modify: `app/audit_web.py`
- Delete: `app/channels/`
- Delete: `tests/test_channels.py`

- [ ] **Step 1: Write failing tests for status plus live-probe readiness**

```python
def test_dws_gate_requires_status_and_authenticated_probe():
    runner = ScriptedRunner([
        completed(0, '{"authenticated":true,"token_valid":true,"refresh_token_valid":true}'),
        completed(4, '', '{"code":"invalidParameter.authCode.notFound"}'),
    ])
    result = DwsChannelGate(binary="dws", runner=runner).check()
    assert result.state is ChannelGateState.NEEDS_LOGIN
    assert runner.commands == [
        ["dws", "auth", "status", "--format", "json", "--timeout", "5"],
        ["dws", "contact", "user", "get-self", "--format", "json"],
    ]


def test_lark_gate_requires_verified_status_and_authenticated_probe():
    runner = ScriptedRunner([
        completed(0, '{"authenticated":true}'),
        completed(0, '{"data":{"user_id":"u1"}}'),
    ])
    result = LarkChannelGate(binary="lark-cli", runner=runner).check()
    assert result.state is ChannelGateState.READY
    assert runner.commands[0] == ["lark-cli", "auth", "status", "--json", "--verify"]
    assert runner.commands[1] == [
        "lark-cli", "contact", "+get-user", "--as", "user", "--json",
    ]
```

- [ ] **Step 2: Run the focused tests and verify the new API is missing**

Run: `pytest tests/test_channel_gate.py -q`

Expected: FAIL during import because `app.channel_gate` does not exist.

- [ ] **Step 3: Implement gate-only types and command classification**

```python
class ChannelGateState(StrEnum):
    READY = "ready"
    NEEDS_LOGIN = "needs_login"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


class ChannelGateResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    channel: str
    state: ChannelGateState
    reason_code: str
    detail: str = ""
    commands: tuple[tuple[str, ...], ...] = ()


class ChannelGate(Protocol):
    channel_name: str
    def check(self) -> ChannelGateResult: ...
```

Implement `DwsChannelGate.check()` with the two exact commands from Step 1. Treat a structurally valid status as ready only when `authenticated`, `token_valid`, and `refresh_token_valid` are all `true`; the live probe must also exit zero with a JSON object. Implement `LarkChannelGate.check()` with its two exact commands. Classify missing executable/configuration as `blocked`, auth/refresh failures as `needs_login`, and timeout/network/provider failures as `unavailable`. Keep safe stderr in `detail`, but classify from return code plus parsed JSON fields rather than free-form substring routing.

Delete the `app/channels` package, including `ChannelMessage`, `ChannelSendResult`, `ChannelAdapter`, generic enqueue, list, and send methods. Channel-specific producers continue to enqueue `reply_tasks` directly. Update CLI and Audit imports to use `app.channel_gate` directly; do not add a compatibility re-export.

- [ ] **Step 4: Run gate and old channel tests**

Run: `pytest tests/test_channel_gate.py tests/test_cli.py tests/test_audit_web.py -q -k 'channel or gate'`

Expected: PASS; no test imports the removed generic adapter or enqueue helper.

- [ ] **Step 5: Commit the gate boundary**

```bash
git add -A app/channel_gate.py app/channels tests/test_channel_gate.py tests/test_channels.py tests/test_cli.py tests/test_audit_web.py
git commit -m "refactor: add typed channel readiness gates"
```

### Task 2: Persistent One-Login Coordination And Pass-Level Gating

**Files:**
- Modify: `app/channel_gate.py`
- Modify: `app/worker.py`
- Modify: `app/cli.py`
- Modify: `app/audit_web.py`
- Test: `tests/test_channel_gate.py`
- Test: `tests/test_worker.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for one-hour login suppression**

```python
def test_login_coordinator_starts_one_process_and_suppresses_repeats(tmp_path):
    store = AutoReplyStore(tmp_path / "db.sqlite3")
    launches = []
    coordinator = LoginCoordinator(
        store=store,
        launchers={"dingtalk": lambda: launches.append("dws") or FakeProcess(41)},
        now=lambda: datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
    )
    gate = ChannelGateResult(
        channel="dingtalk",
        state=ChannelGateState.NEEDS_LOGIN,
        reason_code="live_probe_auth_failed",
    )
    assert coordinator.handle(gate).launched is True
    assert coordinator.handle(gate).suppressed is True
    assert launches == ["dws"]


def test_producer_does_not_call_dws_when_gate_is_not_ready(worker):
    worker.channel_gates["dingtalk"] = FixedGate(ChannelGateState.NEEDS_LOGIN)
    assert worker.produce_once() == 0
    assert worker.dws.calls == []


def test_consumer_does_not_claim_task_when_required_gate_is_not_ready(worker):
    task_id = enqueue_dingtalk_task(worker.store)
    worker.channel_gates["dingtalk"] = FixedGate(ChannelGateState.UNAVAILABLE)
    assert worker.consume_once() == 0
    assert worker.store.get_reply_task(task_id).status == "pending"
```

- [ ] **Step 2: Verify the tests fail before integration**

Run: `pytest tests/test_channel_gate.py tests/test_worker.py -q -k 'login_coordinator or gate_is_not_ready'`

Expected: FAIL because `LoginCoordinator` and worker `channel_gates` do not exist.

- [ ] **Step 3: Implement persisted coordination and gate the whole pass**

```python
@dataclass(frozen=True)
class LoginHandlingResult:
    launched: bool = False
    suppressed: bool = False
    pid: int | None = None


class LoginCoordinator:
    SUPPRESSION = timedelta(hours=1)

    def handle(self, result: ChannelGateResult) -> LoginHandlingResult:
        if result.state is not ChannelGateState.NEEDS_LOGIN:
            return LoginHandlingResult()
        state = self._load(result.channel)
        if self._pid_alive(state.get("pid")) or self._recent(state.get("started_at")):
            return LoginHandlingResult(suppressed=True, pid=state.get("pid"))
        process = self.launchers[result.channel]()
        self._save(result.channel, process.pid, result.reason_code)
        return LoginHandlingResult(launched=True, pid=process.pid)
```

Use service-state key `channel_login_request:{channel}`. Configure launchers as `dws auth login` and `lark-cli auth login`. Check gates before `produce_once()` starts discovery and before `consume_once()` claims a task. A non-ready DWS gate stops the DingTalk pass; a non-ready Lark gate only stops Lark tasks. Do not let Agent prompts contain auth-login commands.

Implement `required_channels_for_task(task)` as a deterministic capability check, not a business router: include `task.channel`, add `dingtalk` for DingTalk/OA/Alidocs references, and add `lark` for Feishu/Lark document or message references in `trigger_message_json`. Check these requirements before claiming the task. The function does not choose an action or recipient.

Change `channel_doctor_command()` and the config page to use the exact same gate objects, not separate doctor logic.

- [ ] **Step 4: Run the gate integration tests**

Run: `pytest tests/test_channel_gate.py tests/test_worker.py tests/test_cli.py tests/test_audit_web.py -q -k 'gate or login or channel_doctor'`

Expected: PASS with one login launch, subsequent suppression, and no task claim while blocked.

- [ ] **Step 5: Commit pass-level gating**

```bash
git add app/channel_gate.py app/worker.py app/cli.py app/audit_web.py tests/test_channel_gate.py tests/test_worker.py tests/test_cli.py tests/test_audit_web.py
git commit -m "fix: gate channel work before task execution"
```

### Task 3: Minimal Agent Result And Incremental Codex Event Capture

**Files:**
- Create: `app/agent_result.py`
- Create: `app/schemas/agent_result.schema.json`
- Create: `tests/test_agent_result.py`
- Modify: `app/process_runner.py`
- Test: `tests/test_process_runner.py`

- [ ] **Step 1: Write failing strict-result and line-callback tests**

```python
def test_parse_agent_result_from_last_agent_message():
    raw = json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": json.dumps({
            "outcome": "completed",
            "summary": "已回复并确认发送成功。",
            "error": {
                "code": "",
                "retryable": False,
                "authorization_required": False,
                "side_effect_state": "confirmed",
            },
        }, ensure_ascii=False)},
    }, ensure_ascii=False)
    assert parse_agent_result(raw).outcome is AgentOutcome.COMPLETED


def test_process_runner_emits_complete_stdout_lines(tmp_path):
    lines = []
    result = run_process_with_idle_timeout(
        [sys.executable, "-c", "print('one'); print('two')"],
        prompt="",
        env=None,
        total_timeout_seconds=5,
        idle_timeout_seconds=5,
        on_stdout_line=lines.append,
    )
    assert result.returncode == 0
    assert lines == ["one", "two"]
```

- [ ] **Step 2: Run and verify both APIs are absent**

Run: `pytest tests/test_agent_result.py tests/test_process_runner.py -q`

Expected: FAIL because the result module and `on_stdout_line` argument are missing.

- [ ] **Step 3: Implement the strict result and local-only normalization**

```python
class AgentOutcome(StrEnum):
    COMPLETED = "completed"
    NO_ACTION = "no_action"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class SideEffectState(StrEnum):
    NONE = "none"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class AgentError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = ""
    retryable: bool = False
    authorization_required: bool = False
    side_effect_state: SideEffectState = SideEffectState.NONE


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: AgentOutcome
    summary: str = Field(min_length=1)
    error: AgentError = Field(default_factory=AgentError)
```

`parse_agent_result(raw)` must inspect parsed JSONL payloads in reverse, then parse `item.text`, `message`, or `last_agent_message`. Permit exactly one local normalization that strips a Markdown JSON fence and extracts the first balanced JSON object. Do not call Codex again and do not create a repair prompt.

Create the matching JSON Schema with `additionalProperties: false` at every object level.

- [ ] **Step 4: Add incremental line delivery without changing existing callers**

Add `on_stdout_line: Callable[[str], None] | None = None` to `run_process_with_idle_timeout()`. Use `codecs.getincrementaldecoder("utf-8")(errors="replace")` so multibyte characters split across reads remain valid. Maintain a text buffer; call the callback only for newline-terminated lines, flush the final non-empty line after process exit, and still return the full original stdout.

- [ ] **Step 5: Run parser and process tests**

Run: `pytest tests/test_agent_result.py tests/test_process_runner.py -q`

Expected: PASS, including malformed JSON rejection without a second Agent call.

- [ ] **Step 6: Commit the result contract**

```bash
git add app/agent_result.py app/schemas/agent_result.schema.json app/process_runner.py tests/test_agent_result.py tests/test_process_runner.py
git commit -m "feat: add minimal agent run result"
```

### Task 4: Single Agent Run Persistence

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing Store tests for one row per generation**

```python
def test_agent_run_is_unique_per_task_generation(store, reply_task):
    first = store.claim_agent_run(
        reply_task.id, reply_task.execution_generation, owner="worker-1"
    )
    second = store.claim_agent_run(
        reply_task.id, reply_task.execution_generation, owner="worker-2"
    )
    assert first.claimed is True
    assert second.claimed is False
    assert second.run.id == first.run.id


def test_running_agent_events_are_persisted_incrementally(store, reply_task):
    run = store.claim_agent_run(
        reply_task.id, reply_task.execution_generation, owner="worker-1"
    ).run
    store.append_agent_run_event(run.id, {"type": "item.started", "call_id": "c1"})
    store.append_agent_run_event(run.id, {"type": "item.completed", "call_id": "c1"})
    loaded = store.get_agent_run(run.id)
    assert [event["type"] for event in loaded.tool_events] == [
        "item.started", "item.completed",
    ]
```

- [ ] **Step 2: Verify Store lacks `agent_runs`**

Run: `pytest tests/test_store.py -q -k agent_run`

Expected: FAIL because `claim_agent_run()` does not exist.

- [ ] **Step 3: Add the model, table, and transactional APIs**

```sql
create table if not exists agent_runs (
    id integer primary key autoincrement,
    reply_task_id integer not null,
    execution_generation text not null,
    status text not null default 'pending',
    codex_session_id text not null default '',
    transcript_start_line integer not null default 0,
    transcript_end_line integer not null default 0,
    final_result_json text not null default '',
    structured_error_json text not null default '',
    tool_events_json text not null default '[]',
    side_effect_state text not null default 'none',
    lease_owner text not null default '',
    lease_expires_at text not null default '',
    started_at text not null default '',
    completed_at text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    unique(reply_task_id, execution_generation),
    foreign key(reply_task_id) references reply_tasks(id)
);
create index if not exists idx_agent_runs_status on agent_runs(status, updated_at);
```

Add `AgentRun`, `AgentRunClaim`, `claim_agent_run(..., owner, lease_seconds=1800)`, `renew_agent_run_lease()`, `set_agent_run_session()`, `append_agent_run_event()`, `complete_agent_run()`, `fail_agent_run()`, `mark_agent_run_unknown()`, `get_agent_run_for_task_generation()`, and `list_unknown_agent_runs()`. Use `begin immediate` for claims. A fresh running lease cannot be stolen; an expired running lease may be reclaimed only for same-generation session recovery. Reject transitions out of `completed`; allow `running -> failed|unknown|completed` and `unknown -> completed|failed` only.

- [ ] **Step 4: Run Store tests and database integrity checks**

Run: `pytest tests/test_store.py tests/test_task_store.py -q`

Expected: PASS and `pragma foreign_key_check` returns no rows.

- [ ] **Step 5: Commit Agent run persistence**

```bash
git add app/store.py tests/test_store.py tests/test_task_store.py
git commit -m "feat: persist one agent run per task generation"
```

### Task 5: Raw Agent Context And Native Direct Runner

**Files:**
- Create: `app/agent_context.py`
- Create: `app/agent_runner.py`
- Create: `tests/test_agent_context.py`
- Create: `tests/test_agent_runner.py`
- Modify: `app/codex_runner.py`

- [ ] **Step 1: Write failing context tests that forbid service-read bodies**

```python
def test_context_renders_reference_and_command_without_resolved_body():
    context = AgentTaskContext(
        task_id=7,
        channel="dingtalk",
        conversation_id="cid",
        conversation_title="产品群",
        single_chat=False,
        trigger_message_id="mid",
        trigger_sender="ET",
        trigger_text="请审核这个文档",
        trigger_create_time="2026-07-28 12:00:00",
        messages=(),
        materials=(MaterialReference(
            kind="dingtalk_doc",
            reference="https://alidocs.dingtalk.com/i/nodes/abc",
            source_message_id="mid",
            read_commands=("dws doc info --node https://alidocs.dingtalk.com/i/nodes/abc --format json",),
        ),),
        prior_receipts=(),
    )
    rendered = context.render()
    assert "dws doc info" in rendered
    assert "resolved_content" not in rendered
    assert "Trusted" not in rendered


def test_context_contains_only_the_agreed_business_rules():
    rendered = minimal_context().render()
    assert "current OA task owner" in rendered
    assert "internal_personnel" in rendered
    assert "HR conversation may skip counterpart identity matching" in rendered
    assert "Never expose credentials" in rendered
    assert "confidence" not in rendered
    assert "trusted target" not in rendered.casefold()
```

- [ ] **Step 2: Write failing runner tests for native config and incremental events**

```python
def test_direct_runner_uses_native_codex_and_never_ignores_user_config(tmp_path):
    executor = RecordingExecutor(agent_result_jsonl())
    runner = DirectAgentRunner(store=store, workspace=tmp_path, executor=executor)
    runner.run(task, context)
    command = executor.commands[0]
    assert command[:2] == ["codex", "exec"]
    assert "--ignore-user-config" not in command
    assert str(AGENT_RESULT_SCHEMA_PATH) in command


def test_direct_runner_persists_each_jsonl_event_before_final_parse(tmp_path):
    runner = DirectAgentRunner(store=store, workspace=tmp_path, executor=streaming_executor)
    result = runner.run(task, context)
    persisted = store.get_agent_run(result.run_id)
    assert persisted.codex_session_id == "session-1"
    assert len(persisted.tool_events) == 2
```

- [ ] **Step 3: Verify context and runner modules are missing**

Run: `pytest tests/test_agent_context.py tests/test_agent_runner.py -q`

Expected: FAIL during import.

- [ ] **Step 4: Implement the raw context contract**

```python
@dataclass(frozen=True)
class MaterialReference:
    kind: str
    reference: str
    source_message_id: str
    read_commands: tuple[str, ...]


@dataclass(frozen=True)
class AgentTaskContext:
    task_id: int
    channel: str
    conversation_id: str
    conversation_title: str
    single_chat: bool
    trigger_message_id: str
    trigger_sender: str
    trigger_text: str
    trigger_create_time: str
    messages: tuple[AgentContextMessage, ...]
    materials: tuple[MaterialReference, ...]
    prior_receipts: tuple[PriorReceipt, ...]
```

Render original trigger, recent messages, raw IDs, material links/file IDs, exact read commands, and safe prior receipt summaries. The prompt must state that the Agent owns evidence reading, target choice, business judgment, and execution; it must query live state before repeating a prior side effect; it must never run auth login/reset/logout; and it must follow OA/personnel/secret rules. Do not include `resolved_content`, trusted-target fields, hashes, dependencies, confidence, or action schemas.

- [ ] **Step 5: Implement one direct native Codex run**

`DirectAgentRunner.run(task, context, *, read_only=False)` must:

1. claim the existing `agent_runs` row;
2. build a fresh `codex exec` command or `codex exec resume` using only that row's session ID;
3. preserve user config, Lark CLI environment, DWS local auth environment, Memory MCP, Exa MCP, and configured passthrough MCPs;
4. use a 1200-second total timeout and 900-second idle timeout;
5. persist every JSONL line through `append_agent_run_event()`;
6. store the session ID as soon as a session event arrives;
7. parse `AgentResult` locally after process exit;
8. return the result plus safe transcript boundaries and events.

For `read_only=True`, use `approval_policy="never"` and a developer instruction that forbids all external writes. For normal runs, retain native `codex exec` with the existing auto-review policy; do not add `--ignore-user-config`, agentcode-specific auth, or a second provider.

- [ ] **Step 6: Run context, runner, and Codex configuration tests**

Run: `pytest tests/test_agent_context.py tests/test_agent_runner.py tests/test_codex_runner.py -q`

Expected: PASS; command assertions show native `codex exec`, output schema, and preserved MCP configuration.

- [ ] **Step 7: Commit the direct Agent runner**

```bash
git add app/agent_context.py app/agent_runner.py app/codex_runner.py tests/test_agent_context.py tests/test_agent_runner.py tests/test_codex_runner.py
git commit -m "feat: run queued tasks through native codex agent"
```

### Task 6: Cut The Worker Over To The Single Agent Path

**Files:**
- Create: `tests/test_agent_runtime_worker.py`
- Modify: `app/worker.py`
- Modify: `app/store.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Write failing end-to-end worker tests**

```python
def test_queued_task_runs_agent_once_and_records_completed_attempt(worker, direct_runner):
    task = enqueue_task(worker.store, generation="g1")
    direct_runner.result = completed_result("已回复并确认发送成功。")
    assert worker.consume_once(max_tasks=1) == 1
    assert direct_runner.calls == [(task.id, "g1")]
    assert worker.store.get_reply_task(task.id).status == "completed"
    attempt = worker.store.get_latest_reply_attempt_for_trigger(
        task.conversation_id, task.trigger_message_id,
    )
    assert attempt.action == "agent_run"
    assert attempt.send_status == "completed"
    assert attempt.audit_summary == "已回复并确认发送成功。"


def test_manual_rerun_uses_new_generation_even_when_content_changes(worker):
    first = enqueue_task(worker.store, generation="g1")
    worker.consume_once(max_tasks=1)
    rerun = worker.store.enqueue_manual_rerun_reply_task(**manual_rerun_args(first))
    worker.consume_once(max_tasks=1)
    assert rerun.execution_generation != "g1"
    assert worker.direct_agent_runner.generations == ["g1", rerun.execution_generation]


def test_failed_result_is_terminal_only_when_not_retryable(worker):
    task = enqueue_task(worker.store, generation="g1")
    worker.direct_agent_runner.result = failed_result("material_missing", retryable=False)
    worker.consume_once(max_tasks=1)
    assert worker.store.get_reply_task(task.id).status == "failed"


def test_stale_processing_task_resumes_same_generation_and_session(worker):
    task = seed_stale_processing_task(worker.store, generation="g1")
    seed_running_agent_run(
        worker.store,
        task=task,
        session_id="session-1",
        expired_lease=True,
        events=[tool_completed("read-1")],
    )
    worker.consume_once(max_tasks=1)
    assert worker.direct_agent_runner.resume_session_ids == ["session-1"]
    assert worker.direct_agent_runner.generations == ["g1"]
```

- [ ] **Step 2: Run the tests and confirm the Universal path is still called**

Run: `pytest tests/test_agent_runtime_worker.py -q`

Expected: FAIL because the worker has no `direct_agent_runner` path.

- [ ] **Step 3: Replace `_process_universal_queued_task()` with `_process_agent_queued_task()`**

```python
def _process_agent_queued_task(self, conversation, task, trigger, context_messages):
    context = self._build_agent_task_context(
        conversation=conversation,
        task=task,
        trigger=trigger,
        context_messages=context_messages,
    )
    run = self.direct_agent_runner.run(task, context)
    return self._apply_agent_result(task, run)
```

Construct material references only from trigger/context metadata and provide CLI commands; do not invoke document, folder, minutes, file-download, sheet, mail-body, or OA-attachment readers. OA trigger metadata may include process/task IDs and the approval detail command, but the Agent must run the command and decide what to do.

Map terminal results as follows:

| Agent result | `agent_runs` | `reply_tasks` | `reply_attempts.send_status` |
|---|---|---|---|
| `completed` + confirmed/none | completed | completed | completed |
| `no_action` | completed | completed | skipped |
| `needs_human` | completed | completed | blocked with explicit reason |
| `failed`, retryable | failed | pending with backoff | failed |
| `failed`, not retryable | failed | failed | failed |
| any result + unknown | unknown | failed, owned by reconciliation scan | blocked |

Use `action="agent_run"`, `codex_reason=summary`, `audit_summary=summary`, the run's Codex session/transcript fields, and safe tool events. Do not infer or rewrite the Agent outcome from target, confidence, dependency, or older attempt status.

- [ ] **Step 4: Remove automatic generation-mismatch replanning and trigger-only suppression**

Delete the `execution generation mismatch` exception branch, Universal dependency exceptions, terminal-attempt precheck, and sent-reply precheck from `consume_once()`. Keep only the unique `(task, generation)` Agent run claim. Manual rerun already rotates generation and must not be blocked by the previous attempt.

Replace blanket stale-processing reset with lease-aware recovery: inspect the same generation's persisted events first. Reclaim and resume its stored Codex session when the lease expired and no effectful call is incomplete; mark it `unknown` and hand it to Task 7 reconciliation when an effectful call started without completion. Never rotate generation merely because the worker process restarted.

- [ ] **Step 5: Run worker cutover tests**

Run: `pytest tests/test_agent_runtime_worker.py tests/test_worker.py tests/test_store.py -q`

Expected: PASS for Agent result mapping, retry backoff, changed manual reruns, and one run per generation.

- [ ] **Step 6: Commit the authoritative worker path**

```bash
git add app/worker.py app/store.py tests/test_agent_runtime_worker.py tests/test_worker.py
git commit -m "refactor: make direct agent the queued task runtime"
```

### Task 7: Unknown Side-Effect Reconciliation

**Files:**
- Modify: `app/agent_runner.py`
- Modify: `app/worker.py`
- Modify: `app/store.py`
- Test: `tests/test_agent_runner.py`
- Test: `tests/test_agent_runtime_worker.py`

- [ ] **Step 1: Write failing incomplete-call and reconciliation tests**

```python
def test_started_effectful_call_without_completion_marks_run_unknown(store, runner):
    runner.executor = stream_then_fail([
        session_event("s1"),
        tool_started("c1", "exec_command", {"cmd": "dws chat message send --conversation cid --text hi"}),
    ])
    with pytest.raises(AgentRunUnknownError):
        runner.run(task, context)
    assert store.get_agent_run_for_task_generation(task.id, "g1").status == "unknown"


def test_reconciliation_is_read_only_and_confirms_existing_effect(worker):
    unknown = seed_unknown_run(worker.store, effectful_call_id="c1")
    worker.reconciliation_runner.result = completed_result("外部消息已存在。")
    worker.reconcile_unknown_agent_runs()
    assert worker.reconciliation_runner.read_only is True
    assert worker.store.get_agent_run(unknown.id).status == "completed"
    assert worker.store.get_reply_task(unknown.reply_task_id).status == "completed"


def test_reconciliation_rotates_generation_only_after_confirmed_no_effect(worker):
    unknown = seed_unknown_run(worker.store, effectful_call_id="c1")
    worker.reconciliation_runner.result = no_action_result("已核对，外部动作未发生。")
    worker.reconcile_unknown_agent_runs()
    task = worker.store.get_reply_task(unknown.reply_task_id)
    assert task.status == "pending"
    assert task.execution_generation != unknown.execution_generation
```

- [ ] **Step 2: Verify unknown calls are currently replayable**

Run: `pytest tests/test_agent_runner.py tests/test_agent_runtime_worker.py -q -k 'unknown or reconciliation'`

Expected: FAIL because no incomplete-call classifier or reconciliation path exists.

- [ ] **Step 3: Classify started/completed tool events by call ID**

Parse structured event type, tool name, call ID, and command arguments. A call is incomplete when a start/call event exists without a matching completion/output event. Mark it effectful only when parsed argv identifies a write command (`send`, `reply`, `create`, `update`, `delete`, `comment`, `reaction`, `approve`, `reject`, `revert`, or document edit) or a mutating MCP tool annotation. This classifier protects replay only; it must not validate target, content, or business permission and must not inspect human error prose.

If the process fails with no incomplete effectful call, use normal retry semantics. If an incomplete effectful call exists, persist `unknown` before raising `AgentRunUnknownError`.

- [ ] **Step 4: Implement a read-only reconciliation invocation**

Add `DirectAgentRunner.reconcile(existing_run, context)`. Build its prompt from the original trigger, safe call arguments, prior output, and live-state query instructions. Run it with `read_only=True`, append its audit events to the existing row without claiming or creating another row, and accept only:

- `completed`: the effect is confirmed; finish the original task/run;
- `no_action`: external state confirms no effect; fail the original run, rotate generation, and requeue;
- `needs_human` or `failed`: retain unknown with the explicit reason and do not replay.

The reconciliation invocation does not create a new `agent_runs` row or execution generation.

- [ ] **Step 5: Run unknown-state tests**

Run: `pytest tests/test_agent_runner.py tests/test_agent_runtime_worker.py -q -k 'unknown or reconciliation'`

Expected: PASS with no write-capable reconciliation command and no replay before confirmed absence.

- [ ] **Step 6: Commit unknown-state recovery**

```bash
git add app/agent_runner.py app/worker.py app/store.py tests/test_agent_runner.py tests/test_agent_runtime_worker.py
git commit -m "fix: reconcile uncertain agent side effects before retry"
```

### Task 8: Delete Universal Execution, Service-Owned Business Work, And Auth Archives

**Files:**
- Delete: all Universal files and tests listed in the File Map
- Modify: `app/worker.py`
- Modify: `app/store.py`
- Modify: `app/dws_client.py`
- Modify: `app/org_cache.py`
- Modify: `app/setup_wizard.py`
- Modify: `tests/test_worker.py`
- Modify: `tests/test_store.py`
- Modify: `tests/test_setup_wizard.py`

- [ ] **Step 1: Add a failing absence test**

```python
def test_removed_runtime_tables_and_modules_are_absent(store):
    tables = {row[0] for row in store._connect().execute(
        "select name from sqlite_master where type='table'"
    )}
    assert "agent_runs" in tables
    assert "universal_plan_executions" not in tables
    assert "universal_action_executions" not in tables
    assert not hasattr(store, "create_universal_plan_execution")
    assert not hasattr(DingTalkAutoReplyWorker, "execute_universal_send_reply")
```

- [ ] **Step 2: Run the absence test and record remaining references**

Run: `pytest tests/test_store.py tests/test_worker.py -q -k removed_runtime`

Expected: FAIL because old tables and methods still exist.

Run: `rg -n 'UniversalPlanner|UniversalPlan|PlannedAction|UniversalConsumer|UniversalValidator|UniversalActionExecutor|universal_plan_executions|universal_action_executions|context_hash|action_hash|dws_auth_backup|export_auth_archive|import_auth_archive' app tests`

Expected: matches identify every deletion in this task.

- [ ] **Step 3: Remove service-owned Agent work from `worker.py`**

Delete Universal planning/execution methods and all automatic business material readers used only to prepare Agent decisions, including linked document/folder/minutes/file body expansion, OA attachment fallback parsing/search, target normalization, permission/recipient matching, confidence/dependency validation, and action-kind dispatch. Keep channel discovery, recent-message retrieval, trigger construction, queue lifecycle, channel gate, run persistence, result mapping, and unknown reconciliation.

Keep the explicit pre-Agent business rules already confirmed: current OA task ownership/SOP instructions, internal-personnel subject identity with HR exception, and secret redaction. Supply business rules in the Agent prompt instead of duplicating their judgment in service branches.

- [ ] **Step 4: Drop old tables and remove auth archive APIs**

In Store initialization, migrate historical visible data into existing `reply_attempts` if an attempt reference is missing, then execute:

```sql
drop table if exists universal_action_executions;
drop table if exists universal_plan_executions;
delete from service_state where key = 'dws_auth_backup';
```

Remove Universal Store models/APIs and remove `export_auth_archive()`, `import_auth_archive()`, archive command builders, worker backup/restore constants, rotation, restore retry, and archive status rendering. Preserve normal local CLI auth and the one-login coordinator from Task 2.

- [ ] **Step 5: Delete obsolete modules/tests and update remaining imports**

Delete the files listed under **Delete after cutover**. Move any still-valid trigger metadata extraction into `agent_context.py`; do not preserve a compatibility import or fallback module.

- [ ] **Step 6: Verify the old architecture is physically absent**

Run: `rg -n 'UniversalPlanner|UniversalPlan|PlannedAction|UniversalConsumer|UniversalValidator|UniversalActionExecutor|context_hash|action_hash|export_auth_archive|import_auth_archive' app tests`

Expected: no matches.

Run: `rg -n 'universal_plan_executions|universal_action_executions|dws_auth_backup' app tests`

Expected: matches are limited to the one-way Store cleanup SQL and tests asserting the old tables/state are absent; there are no models, reads, writes, counters, or UI renderers for them.

Run: `pytest tests/test_store.py tests/test_worker.py tests/test_setup_wizard.py tests/test_dws_client.py -q`

Expected: PASS.

- [ ] **Step 7: Commit physical deletion**

```bash
git add -A app tests
git commit -m "refactor: remove universal runtime and auth archives"
```

### Task 9: Simplify History, Documentation, And Configuration

**Files:**
- Modify: `app/history.py`
- Modify: `app/audit_web.py`
- Modify: `app/setup_wizard.py`
- Modify: `docs/architecture.md`
- Modify: `docs/reply-worker-reliability.md`
- Modify: `README.md`
- Test: `tests/test_history.py`
- Test: `tests/test_audit_web.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 1: Write failing History tests for user-facing Agent outcomes**

```python
def test_history_hides_runtime_internals_and_shows_outcome(store):
    attempt = seed_agent_attempt(
        store,
        status="completed",
        summary="已在群内回复并确认发送成功。",
    )
    html = render_history(store)
    assert "已在群内回复并确认发送成功。" in html
    assert "Universal" not in html
    assert "planner" not in html.casefold()
    assert "action index" not in html.casefold()
    assert f'/attempts/{attempt.id}' in html


def test_config_page_shows_live_probe_and_login_suppression_state(store):
    html = render_config(store, gate_state="needs_login", login_state="suppressed")
    assert "需要登录" in html
    assert "已避免重复弹出授权页" in html
    assert "auth archive" not in html.casefold()
```

- [ ] **Step 2: Run and verify old Universal cards still render**

Run: `pytest tests/test_history.py tests/test_audit_web.py tests/test_setup_wizard.py -q -k 'runtime_internals or login_suppression'`

Expected: FAIL against current History/config rendering.

- [ ] **Step 3: Replace Universal observability with Agent run observability**

History list/detail shows channel icon, conversation, sender, trigger, terminal outcome, summary, safe generated/sent text when present in a receipt, safe receipt summary, and timestamps. Remove Universal execution cards, action pills, planner labels, confidence, dependencies, action indexes, target normalization errors, and unresolved Universal counters.

Config shows each gate's state, last status command, last live probe, last success time, and whether a login request is active or suppressed. Do not expose PID, session ID, token fields, credential paths, or raw signed URLs.

- [ ] **Step 4: Rewrite architecture and reliability documentation**

Document the authoritative flow:

```text
channel gate -> reply task -> one agent run -> direct CLI/MCP work
             -> incremental safe audit -> terminal result
             -> read-only reconciliation only for unknown writes
```

State explicitly that credentials stay in normal local CLI storage, the service never exports/restores auth archives, and Agents never call auth login commands.

- [ ] **Step 5: Run UI/documentation tests**

Run: `pytest tests/test_history.py tests/test_audit_web.py tests/test_setup_wizard.py tests/test_cli.py -q`

Expected: PASS with no Universal internals in rendered pages.

- [ ] **Step 6: Commit UI and docs**

```bash
git add app/history.py app/audit_web.py app/setup_wizard.py docs/architecture.md docs/reply-worker-reliability.md README.md tests/test_history.py tests/test_audit_web.py tests/test_setup_wizard.py tests/test_cli.py
git commit -m "docs: expose simplified agent runtime"
```

### Task 10: Full Verification, Live Rollout, And Backlog Recovery

**Files:**
- Modify only if verification exposes a regression in files changed by Tasks 1-9.

- [ ] **Step 1: Run formatting/static checks and the full suite**

Run: `git diff --check main...HEAD`

Expected: no output.

Run: `pytest -q`

Expected: all tests PASS; no Universal test modules remain collected.

- [ ] **Step 2: Verify fresh and migrated databases**

Run a temporary fresh Store initialization and a copy of the current database through initialization. For both, run:

```sql
pragma integrity_check;
pragma foreign_key_check;
select name from sqlite_master
where type='table' and name in (
  'agent_runs', 'universal_plan_executions', 'universal_action_executions'
)
order by name;
```

Expected: `integrity_check=ok`, no foreign-key rows, and only `agent_runs` is returned.

- [ ] **Step 3: Run live gate checks before restarting**

Run: `.venv/bin/python -m ceo_agent_service.cli channel-doctor`

Expected: configured DWS and Lark channels each report `ready` only after status and live probe succeed. If a channel reports `needs_login`, verify exactly one authorization page is launched and a second check reports suppression instead of opening another page.

- [ ] **Step 4: Run one read-only Agent smoke test**

Use a synthetic local task whose prompt requires `dws contact user get-self --format json` and `lark-cli contact +get-user --as user --json` only, with external writes forbidden.

Expected: one `agent_runs` row reaches `completed`, tool events are visible, native user config/MCPs are available, and no auth-login command is executed.

- [ ] **Step 5: Commit any verification-only fixes**

If Steps 1-4 required changes, run their focused tests and commit only those fixes:

```bash
git add -u app tests docs README.md
git commit -m "fix: complete direct agent rollout verification"
```

If no files changed, do not create an empty commit.

- [ ] **Step 6: Restart launchd and verify the new process**

Run: `launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main`

Run: `launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'`

Expected: service state is running with a new PID and no immediate crash loop.

- [ ] **Step 7: Recover the backlog through the authoritative path**

List all `reply_tasks.status in ('failed','processing')`, all `agent_runs.status in ('failed','unknown','running')`, and unresolved blocked/failed attempts. Requeue definite retryable failures through Store APIs; reconcile every unknown run read-only before any generation rotation. Do not hand-edit work-summary inputs or mark messages complete without a real terminal result.

Expected: each recoverable item reaches `completed`, `skipped`, explicit unrecoverable `blocked/failed`, or remains `unknown` with a concrete reconciliation reason.

- [ ] **Step 8: Execute one authorized end-to-end task and verify delivery**

Use a current, non-sensitive trigger authorized for a normal reply. Verify the Agent reads required evidence itself, calls the direct CLI, receives a successful external receipt, writes one completed Agent run, writes one user-visible attempt, and does not create a duplicate on the next service pass.

- [ ] **Step 9: Push the implementation branch**

Run: `git status --short`

Expected: clean worktree.

Run: `git push -u origin codex/agent-runtime-simplification`

Expected: branch push succeeds after all live verification and backlog work completes.

## Final Acceptance Checklist

- [ ] DWS and Lark require status plus real authenticated probes.
- [ ] `needs_login` launches at most one login process per hour.
- [ ] Agents never execute auth login/reset/logout.
- [ ] One task generation owns exactly one Agent run.
- [ ] Native `codex exec` preserves user CLI and MCP configuration.
- [ ] The Agent reads materials and executes CLI/MCP work directly.
- [ ] The service does not bind targets, pre-read business bodies, or dispatch action arrays.
- [ ] Corrected reruns may change content, target, and operation.
- [ ] Incomplete writes become `unknown` and reconcile read-only before replay.
- [ ] OA/personnel rules and secret redaction remain.
- [ ] Universal modules, tables, hashes, auth archives, and UI internals are absent.
- [ ] Full tests, database checks, launchd restart, live gates, one read-only smoke, one authorized end-to-end task, and backlog recovery are complete.
