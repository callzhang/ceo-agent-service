# Email and CEO Agent Audited Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the executable Consumer-direct unsubscribe exception with one audited `channel=email` lifecycle while preserving direct, read-backed deterministic mailbox actions, disabled replies, rejected-model gates, and production-disabled rollout.

**Architecture:** The independent Email worker keeps scanner/classifier, direct-action executor, and email-task consumer as separate loops. Only an immutable ActionPlan containing `unsubscribe` creates a task; ordinary `ConsumerAgentRunner` proposes one operation, `AuditAgentRunner` alone receives a task/run-bound execution capability, and an Email-specific continuation driver starts another Consumer/Audit proposal revision without counting it as content feedback. Existing append-only unsubscribe effects, controls, receipts, dedicated browser profile, and provider readback remain the execution substrate.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, FastMCP, Codex/Claude Agent runtimes, Playwright Chromium, FastAPI, React 19, TypeScript, Vitest, pytest.

---

## File responsibility map

The implementation must preserve these ownership boundaries:

- `app/email_task_adapter.py`: create redacted `email_agent_action.v1` task payloads and convert one exact Consumer proposal into a validated unsubscribe effect.
- `app/task_lifecycle.py`: recognize audited-v2 Email task identity and reject legacy executable selection; it must not contain a second execution lifecycle.
- `app/email_unsubscribe_audit.py`: new task/run-bound Audit operation. It resolves the current provider entry, validates the accepted proposal, executes exactly one new operation, and returns redacted evidence.
- `app/email_unsubscribe.py`: browser/effect engine only; retain audited continuation and remove automatic Consumer-direct recursion.
- `app/email_store.py`: atomic ActionPlan/task/Audit-run/effect fences and append-only unsubscribe claims, continuations, steps, and receipts.
- `app/agent_cli.py`: expose the unsubscribe write only as a task-bound Audit capability.
- `app/audit_agent.py`: grant that capability only to an Audit run for an audited-v2 Email unsubscribe task.
- `app/agent_orchestrator.py`: support a narrowly injected domain continuation driver while keeping content-feedback accounting separate.
- `app/email_unsubscribe_continuation.py`: new Email-only continuation driver that reads durable `awaiting_audit` state and requests the next Consumer proposal revision.
- `app/email_worker.py`: route every Email task through the normal Consumer/Audit orchestrator and keep deterministic actions outside tasks.
- `app/web_api/email.py` and `app/email_store.py`: project safe task, Consumer run, Audit run, effect, step, and receipt observability.
- `frontend/src/api/console.ts` and `frontend/src/pages/EmailPage.tsx`: render audited lifecycle evidence without exposing private URLs or credentials.
- `skills/ceo-mail-review/SKILL.md`: state the proposal/execution boundary, attachment-metadata limit, no-reply rule, and one-operation continuation rule.
- `docs/architecture.md` and `docs/runtime-mechanism.md`: describe current audited runtime only after the matching code lands.
- Historical specs under `docs/superpowers/specs/`: remain historical; only add current-state pointers where needed.

Delete these Consumer-direct-only units after all callers are removed:

- `app/email_unsubscribe_consumer.py`
- `tests/test_email_unsubscribe_consumer.py`
- `tests/e2e/test_email_unsubscribe_consumer_direct.py`

Retain `app/email_unsubscribe_operation.py` only until the Audit operation has equivalent focused coverage; then replace it with `app/email_unsubscribe_audit.py` in the same routing commit so two executable operations never coexist in a committed state.

## Task 1: Change the documented lifecycle policy in an isolated commit

**Files:**
- Modify: `tests/test_documentation_contract.py`
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Modify: `docs/superpowers/specs/2026-08-29-email-classifier-design.md`
- Modify: `docs/superpowers/specs/2026-08-30-email-unsubscribe-branch-integration-design.md`
- Modify: `docs/superpowers/specs/2026-08-31-email-integration-main-progress.md`
- Verify: `docs/superpowers/specs/2026-09-02-email-ceo-agent-audited-fusion-design.md`

- [ ] **Step 1: Replace the Consumer-direct documentation assertions with audited-v2 assertions**

```python
def test_current_email_docs_describe_audited_unsubscribe_boundary() -> None:
    for path in ("docs/architecture.md", "docs/runtime-mechanism.md"):
        document = _read(path)
        assert "email_unsubscribe_audited_v2" in document
        assert "Consumer A" in document
        assert "Audit Agent B" in document
        assert "Consumer-direct" not in document
        assert "`label`、`mark_read`、`archive`、`move` 和 `trash`" in document
        assert "不创建" in document
        assert "`auto_reply`" in document and "禁用" in document


def test_audited_email_fusion_spec_is_approved() -> None:
    design = _read(
        "docs/superpowers/specs/2026-09-02-email-ceo-agent-audited-fusion-design.md"
    )
    assert "状态：书面规范已批准，已进入实施计划" in design
    assert "email_unsubscribe_audited_v2" in design
    assert "move-to-Trash" in design
```

- [ ] **Step 2: Run the documentation contract and confirm it fails on current-runtime wording**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_documentation_contract.py`

Expected: FAIL because `docs/architecture.md` and `docs/runtime-mechanism.md` still say `Consumer-direct`.

- [ ] **Step 3: Update current architecture and runtime wording**

Use this exact lifecycle text in both current-runtime documents, adapting only surrounding headings:

```markdown
`label`、`mark_read`、`archive`、`move` 和 `trash` 是 Email 子系统的确定性动作，
由独立 Email worker 直接执行并做 provider readback；它们不创建 CEO Agent task，
因此也不创建 Consumer 或 Audit run。`trash` 仅为 move-to-Trash，永久删除、
`EXPUNGE` 和清空废纸篓不可达。

只有 `unsubscribe` 从不可变 ActionPlan 创建 `channel=email` task，生命周期版本为
`email_unsubscribe_audited_v2`。Consumer A 只提出与 task/ActionPlan 绑定的单个操作；
Audit Agent B 检查身份、前缀和网络策略，并且是唯一能调用 task-bound 退订写能力的角色。
多步页面每轮只追加一个操作；`awaiting_audit` 是退订领域状态，不是顶层 task 状态。

Email `auto_reply`、SMTP 和 `mailto:` 发送全部禁用。正文可用于判断，附件仅提供 metadata；
任何 Email Agent 不下载、打开、OCR、解析或总结附件内容。
```

In the older Consumer-direct spec, preserve the historical body and add this banner immediately after its title:

```markdown
> Historical implementation record: this Consumer-direct lifecycle is superseded by the approved
> `2026-09-02-email-ceo-agent-audited-fusion-design.md`. It is not an executable target for new tasks.
```

- [ ] **Step 4: Run the focused documentation and Skill contracts**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_documentation_contract.py tests/test_mail_review_skill.py`

Expected: PASS.

- [ ] **Step 5: Commit only the policy contract**

```bash
git add tests/test_documentation_contract.py docs/architecture.md docs/runtime-mechanism.md docs/superpowers/specs/2026-08-29-email-classifier-design.md docs/superpowers/specs/2026-08-30-email-unsubscribe-branch-integration-design.md docs/superpowers/specs/2026-08-31-email-integration-main-progress.md
git commit -m "docs: restore audited email lifecycle"
```

This commit must contain no Python runtime or frontend change.

## Task 2: Bind new Email tasks to audited-v2 and fail legacy work closed

**Files:**
- Modify: `app/email_task_adapter.py`
- Modify: `app/task_lifecycle.py`
- Modify: `app/email_store.py`
- Modify: `tests/test_email_task_adapter.py`
- Modify: `tests/test_task_lifecycle.py`
- Modify: `tests/test_email_store.py`

- [ ] **Step 1: Write failing tests for audited-v2 task identity**

```python
AUDITED_LIFECYCLE_VERSION = "email_unsubscribe_audited_v2"
LEGACY_LIFECYCLE_VERSION = "email_unsubscribe_consumer_direct_v1"


def test_unsubscribe_task_payload_uses_audited_v2(adapter_fixture):
    route = adapter_fixture.ensure_unsubscribe_route()
    payload = json.loads(route.task.trigger_message_json)
    assert payload["lifecycle_version"] == AUDITED_LIFECYCLE_VERSION
    assert select_task_lifecycle(route.task, route.context) is TaskLifecycle.CONSUMER_AUDIT


def test_legacy_v1_is_never_selected_for_execution():
    payload = _payload(lifecycle_version=LEGACY_LIFECYCLE_VERSION)
    assert select_task_lifecycle(_task(payload), _context(payload)) is TaskLifecycle.CONSUMER_AUDIT
    assert validate_audited_email_task(_task(payload), _context(payload)) is False
```

Add a Store-level test that creates one terminal and one non-terminal v1 task and expects only the latter:

```python
def test_lists_only_nonterminal_legacy_unsubscribe_tasks(email_store, task_store):
    pending = _insert_email_task(task_store, lifecycle="email_unsubscribe_consumer_direct_v1", status="pending")
    _insert_email_task(task_store, lifecycle="email_unsubscribe_consumer_direct_v1", status="done")
    assert email_store.list_nonterminal_legacy_unsubscribe_task_ids() == (pending.id,)
```

- [ ] **Step 2: Run the three focused modules and confirm the lifecycle mismatch**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_email_task_adapter.py tests/test_task_lifecycle.py tests/test_email_store.py`

Expected: FAIL because the adapter still emits `email_unsubscribe_consumer_direct_v1`, the lifecycle enum exposes a direct branch, and the Store has no legacy inventory query.

- [ ] **Step 3: Collapse lifecycle selection to the ordinary audited path**

Replace the special enum and version with:

```python
class TaskLifecycle(StrEnum):
    CONSUMER_AUDIT = "consumer_audit"


EMAIL_UNSUBSCRIBE_AUDITED_LIFECYCLE_VERSION = "email_unsubscribe_audited_v2"


def validate_audited_email_task(task: ReplyTask, context: AgentTaskContext) -> bool:
    if task.channel != "email" or context.channel != "email":
        return False
    if context.task_id != task.id or context.trigger_message_id != task.trigger_message_id:
        return False
    try:
        payload = json.loads(task.trigger_message_json)
    except (json.JSONDecodeError, TypeError):
        return False
    return (
        isinstance(payload, dict)
        and payload == context.trigger_raw_payload
        and payload.get("schema") == "email_agent_action.v1"
        and payload.get("action_type") == EmailAction.UNSUBSCRIBE.value
        and payload.get("lifecycle_version")
        == EMAIL_UNSUBSCRIBE_AUDITED_LIFECYCLE_VERSION
        and _has_valid_unsubscribe_identity(payload, task)
    )


def select_task_lifecycle(task: ReplyTask, context: AgentTaskContext) -> TaskLifecycle:
    del task, context
    return TaskLifecycle.CONSUMER_AUDIT
```

Keep the existing strict identity validation inside `_has_valid_unsubscribe_identity`; do not weaken required fields, confidence bounds, action parameters, or action identity derivation.

Change the task payload version in `app/email_task_adapter.py`:

```python
_ACTION_LIFECYCLE_VERSIONS = {
    EmailAction.AUTO_REPLY: "consumer_audit_v1",
    EmailAction.UNSUBSCRIBE: "email_unsubscribe_audited_v2",
}
```

- [ ] **Step 4: Add the read-only legacy inventory query**

```python
def list_nonterminal_legacy_unsubscribe_task_ids(self) -> tuple[int, ...]:
    with self._connect() as db:
        rows = db.execute(
            """
            select id
            from reply_tasks
            where channel='email'
              and status not in ('done', 'failed', 'skipped')
              and json_extract(trigger_message_json, '$.schema')='email_agent_action.v1'
              and json_extract(trigger_message_json, '$.action_type')='unsubscribe'
              and json_extract(trigger_message_json, '$.lifecycle_version')
                    ='email_unsubscribe_consumer_direct_v1'
            order by id
            """
        ).fetchall()
    return tuple(int(row["id"]) for row in rows)
```

This is an inventory fence only. It must not update, replay, delete, or rewrite legacy rows.

- [ ] **Step 5: Run focused tests**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_email_task_adapter.py tests/test_task_lifecycle.py tests/test_email_store.py`

Expected: PASS.

- [ ] **Step 6: Commit task identity and legacy inventory**

```bash
git add app/email_task_adapter.py app/task_lifecycle.py app/email_store.py tests/test_email_task_adapter.py tests/test_task_lifecycle.py tests/test_email_store.py
git commit -m "feat: bind audited email task lifecycle"
```

- [ ] **Step 7: Perform the repository runtime reload contract without claiming deployment**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: a new launchd PID. Record the printed executable/working-directory path and current checkout SHA. If it does not point at this feature worktree, state that the restart verifies only the existing production checkout and does not make this commit live.

Then query the existing task/backlog status endpoint used by the service and verify there are no unresolved `processing` or `failed` tasks introduced by the restart.

## Task 3: Make the unsubscribe effect capability Audit-run-bound

**Files:**
- Create: `app/email_unsubscribe_audit.py`
- Modify: `app/agent_cli.py`
- Modify: `app/audit_agent.py`
- Modify: `app/wechat/codex_safety.py`
- Modify: `app/email_store.py`
- Modify: `tests/test_agent_cli.py`
- Modify: `tests/test_audit_agent.py`
- Create: `tests/test_email_unsubscribe_audit.py`

- [ ] **Step 1: Write failing Audit capability tests**

```python
def test_consumer_command_does_not_expose_unsubscribe_write():
    command = _base_codex_command()
    make_consumer_agent_command(command, controlled_cli=_controlled_cli())
    assert "execute_audited_email_unsubscribe" not in json.dumps(command)


def test_audited_email_command_exposes_task_bound_write():
    command = _base_codex_command()
    make_audit_agent_command(
        command,
        controlled_cli=_controlled_cli(),
        additional_agent_cli_tools=("execute_audited_email_unsubscribe",),
    )
    assert "execute_audited_email_unsubscribe" in json.dumps(command)


def test_operation_rejects_consumer_or_stale_audit_run(operation, task, consumer_run):
    result = operation.execute(
        task.id,
        task.execution_generation,
        audit_agent_run_id=consumer_run.id,
        accepted_action=_accepted_open_entry_action().model_dump(mode="json"),
    )
    assert result["status"] == "failed"
    assert result["error"]["code"] == "unsubscribe_audit_run_invalid"
```

Add positive coverage with a running Audit run whose parent is the current completed Consumer run. Assert that the claim row records the exact Audit run ID and that changing task, generation, action identity, plan, account, message, thread, entry, or origin is rejected before browser execution.

- [ ] **Step 2: Run focused tests and confirm the tool and operation do not exist**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_agent_cli.py tests/test_audit_agent.py tests/test_email_unsubscribe_audit.py`

Expected: FAIL because `execute_audited_email_unsubscribe`, `EmailUnsubscribeAuditOperation`, and the Audit-only command option are absent.

- [ ] **Step 3: Allow `make_audit_agent_command` to receive a narrow extra tool list**

```python
def make_audit_agent_command(
    command: list[str],
    *,
    controlled_cli: ControlledCliConfig,
    allow_write: bool = True,
    additional_agent_cli_tools: tuple[str, ...] = (),
) -> None:
    make_role_agent_command(
        command,
        controlled_cli=controlled_cli,
        allow_write=allow_write,
        additional_agent_cli_tools=additional_agent_cli_tools,
    )
```

- [ ] **Step 4: Add an atomic Audit-run fence to the unsubscribe claim**

Extend `claim_email_unsubscribe_write` with `audit_agent_run_id: int | None = None`. When supplied, join `agent_runs` and require all of:

```sql
and audit_runs.id=?
and audit_runs.reply_task_id=tasks.id
and audit_runs.execution_generation=tasks.execution_generation
and audit_runs.role='audit'
and audit_runs.status='running'
and audit_runs.operation_id<>''
```

Persist `audit_agent_run_id` on the claim/effect row through a forward-only SQLite migration. Existing rows retain `NULL`; no historical row is rewritten to imply an Audit run.

- [ ] **Step 5: Implement the Audit operation around the existing validated effect adapter**

The new public surface must be:

```python
AUDITED_LIFECYCLE_VERSION = "email_unsubscribe_audited_v2"


class EmailUnsubscribeAuditOperation:
    def execute(
        self,
        task_id: int,
        execution_generation: str,
        *,
        audit_agent_run_id: int,
        accepted_action: Mapping[str, object],
    ) -> dict[str, object]:
        task = self.task_store.get_reply_task(task_id)
        audit_run = self.task_store.get_agent_run(audit_agent_run_id)
        if not _is_current_running_audit(task, audit_run, execution_generation):
            return _failed("unsubscribe_audit_run_invalid")
        continuation = self.email_store.get_email_unsubscribe_continuation(
            task.trigger_message_id
        )
        action = ProposedAction.model_validate(accepted_action)
        effect = accepted_email_unsubscribe_effect(
            task,
            action,
            continuation=(
                None
                if continuation is None
                else _continuation_from_store(continuation)
            ),
        )
        return self._execute_bound_effect(
            task=task,
            audit_run=audit_run,
            effect=effect,
        )


def _continuation_from_store(
    value: Mapping[str, object],
) -> EmailUnsubscribeContinuation:
    return EmailUnsubscribeContinuation(
        action_identity=str(value["action_identity"]),
        action_plan_id=str(value["action_plan_id"]),
        action_plan_version=int(value["action_plan_version"]),
        classification_id=int(value["classification_id"]),
        account_id=str(value["account_id"]),
        stable_message_identity=str(value["stable_message_identity"]),
        thread_identity=str(value["thread_identity"]),
        entry_reference=str(value["entry_reference"]),
        effect_digest=str(value["effect_digest"]),
        previous_effect_digest=str(value["previous_effect_digest"]),
        executed_operations=tuple(
            UnsubscribeOperation.from_mapping(item)
            for item in value["operations"]
        ),
        controls=tuple(
            UnsubscribeDiscoveredControl(**item) for item in value["controls"]
        ),
        network_policy_reference=str(value["network_policy_reference"]),
        network_policy_origin_references=tuple(
            str(item) for item in value["network_policy_origin_references"]
        ),
    )
```

`_execute_bound_effect` must re-resolve the current provider entry from the task locator, call `claim_email_unsubscribe_write` with task ID, generation, lifecycle version, action type, and Audit run ID, then invoke `execute_unsubscribe_in_dedicated_profile(..., automatic=False)`. It may return terminal receipt evidence or an `awaiting_audit` continuation; it must never recurse into a second browser operation.

- [ ] **Step 6: Replace the FastMCP tool with the Audit-bound signature**

```python
@server.tool(name="execute_audited_email_unsubscribe", annotations=ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
))
def execute_audited_email_unsubscribe_tool(
    task_id: int,
    execution_generation: str,
    audit_agent_run_id: int,
    accepted_action: dict[str, object],
) -> dict[str, object]:
    from app.config import worker_db_path
    from app.email_worker import run_audited_email_unsubscribe

    return run_audited_email_unsubscribe(
        worker_db_path(),
        task_id,
        execution_generation,
        audit_agent_run_id=audit_agent_run_id,
        accepted_action=accepted_action,
    )
```

Remove the registered `execute_email_unsubscribe` tool in the same edit.

- [ ] **Step 7: Configure the capability only for audited-v2 Email Audit turns**

Add a helper in `AuditAgentRunner`:

```python
def _email_unsubscribe_tools(self, task: ReplyTask, run: AgentRun) -> tuple[str, ...]:
    try:
        payload = json.loads(task.trigger_message_json)
    except (json.JSONDecodeError, TypeError):
        return ()
    if (
        task.channel == "email"
        and payload.get("action_type") == "unsubscribe"
        and payload.get("lifecycle_version") == "email_unsubscribe_audited_v2"
    ):
        return ("execute_audited_email_unsubscribe",)
    return ()
```

For Codex Audit turns, configure `app.agent_cli` with `make_audit_agent_command`; put `task_id`, `execution_generation`, and `audit_agent_run_id=run.id` in the Audit prompt's task-bound capability section. No Consumer prompt or command receives the tool.

- [ ] **Step 8: Run focused capability and operation tests**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_agent_cli.py tests/test_audit_agent.py tests/test_email_unsubscribe_audit.py tests/test_email_task_adapter.py`

Expected: PASS.

- [ ] **Step 9: Commit the Audit-only capability**

```bash
git add app/email_unsubscribe_audit.py app/agent_cli.py app/audit_agent.py app/wechat/codex_safety.py app/email_store.py tests/test_agent_cli.py tests/test_audit_agent.py tests/test_email_unsubscribe_audit.py
git commit -m "feat: execute email unsubscribe through audit"
```

- [ ] **Step 10: Restart and verify the actual launchd target as in Task 2**

Run the same `launchctl kickstart`, `launchctl print`, health, and backlog checks. Expected: new PID, no newly unresolved backlog, and an explicit note if launchd still points outside the feature worktree.

## Task 4: Add audited multi-step continuation without spending content-feedback quota

**Files:**
- Create: `app/email_unsubscribe_continuation.py`
- Modify: `app/agent_orchestrator.py`
- Modify: `app/email_task_adapter.py`
- Modify: `tests/test_agent_orchestrator.py`
- Create: `tests/test_email_unsubscribe_continuation.py`

- [ ] **Step 1: Write failing tests for domain continuation**

```python
def test_executed_audit_with_awaiting_audit_starts_next_consumer_revision(fixture):
    first = fixture.complete_consumer_and_audit(
        audit_outcome="executed",
        persist_unsubscribe_continuation=True,
    )
    state = fixture.orchestrator._derive_state(fixture.task)
    assert state.proposal_revision == first.proposal_revision + 1
    assert state.parent_run_id == first.audit_run_id
    assert state.feedback is None


def test_domain_continuations_do_not_consume_content_feedback_cycles(fixture):
    fixture.complete_domain_continuation_rounds(3)
    assert fixture.orchestrator._feedback_cycles(fixture.task) == 0


def test_real_audit_feedback_still_stops_after_two_cycles(fixture):
    fixture.complete_feedback_cycles(2)
    result = fixture.orchestrator.process(
        fixture.task,
        fixture.context,
        refresh_context=fixture.refresh_context,
    )
    assert result.status == "failed_terminal"
    assert result.error.code == "audit_revision_exhausted"
```

- [ ] **Step 2: Run continuation and orchestrator tests**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_agent_orchestrator.py tests/test_email_unsubscribe_continuation.py`

Expected: FAIL because an executed Audit is currently always terminal and feedback cycles equal the highest proposal revision.

- [ ] **Step 3: Define the narrow continuation protocol**

```python
class DomainContinuationDriver(Protocol):
    def needs_next_proposal(
        self,
        task: ReplyTask,
        *,
        audit_run: AgentRun,
        audit_result: AuditAgentResult,
    ) -> bool: ...


class EmailUnsubscribeContinuationDriver:
    def __init__(self, email_store: EmailStore) -> None:
        self.email_store = email_store

    def needs_next_proposal(self, task, *, audit_run, audit_result) -> bool:
        if audit_result.outcome is not AuditOutcome.EXECUTED:
            return False
        claim = self.email_store.get_email_unsubscribe_claim(task.trigger_message_id)
        continuation = self.email_store.get_email_unsubscribe_continuation(
            task.trigger_message_id
        )
        return (
            task.channel == "email"
            and claim is not None
            and claim["status"] == "awaiting_audit"
            and continuation is not None
            and claim.get("audit_agent_run_id") == audit_run.id
        )
```

- [ ] **Step 4: Teach the orchestrator to distinguish proposal revisions from feedback cycles**

Add optional `domain_continuation: DomainContinuationDriver | None = None` to `AgentOrchestrator.__init__`. When an Audit result is `EXECUTED`, ask the driver before returning terminal:

```python
if audit_state.outcome is AuditOutcome.EXECUTED:
    if (
        self.domain_continuation is not None
        and self.domain_continuation.needs_next_proposal(
            task,
            audit_run=latest,
            audit_result=audit_state,
        )
    ):
        return _NextConsumer(revision + 1, latest.id, None)
    return _audit_terminal("executed", latest, audit_state, self._feedback_cycles(task))
```

Permit a revision whose parent Audit was `EXECUTED` only when the continuation driver still confirms the durable `awaiting_audit` state. Count content feedback from completed Audit results instead of maximum revision:

```python
def _feedback_cycles(self, task: ReplyTask) -> int:
    return sum(
        1
        for run in self.store.list_agent_runs_for_task_generation(
            task.id, task.execution_generation
        )
        if run.role is AgentRole.AUDIT
        and run.status == "completed"
        and _audit_result(run).outcome is AuditOutcome.FEEDBACK_PROVIDED
    )
```

Keep `MAX_TURNS_PER_PROCESS=32` as the total loop bound. Apply `MAX_CONTENT_FEEDBACK_CYCLES=2` only to this new count, not to proposal revision numbers.

- [ ] **Step 5: Include durable continuation evidence in refreshed Email context**

Add one safe prior receipt with opaque references only:

```python
{
    "receipt_id": f"email-unsubscribe-continuation:{continuation['effect_digest']}",
    "operation": "unsubscribe_continuation",
    "summary": "Audit accepted the durable prefix; propose exactly one next operation from the listed opaque controls.",
    "completed": False,
    "accepted_operations": continuation["operations"],
    "controls": continuation["controls"],
    "previous_effect_digest": continuation["effect_digest"],
}
```

The context must not include private URLs, cookies, local paths, credentials, or attachment bodies.

- [ ] **Step 6: Run focused continuation and generic lifecycle tests**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_agent_orchestrator.py tests/test_email_unsubscribe_continuation.py tests/test_email_task_adapter.py tests/test_agent_turn_store.py`

Expected: PASS, including existing non-Email feedback/retry cases.

- [ ] **Step 7: Commit continuation orchestration**

```bash
git add app/email_unsubscribe_continuation.py app/agent_orchestrator.py app/email_task_adapter.py tests/test_agent_orchestrator.py tests/test_email_unsubscribe_continuation.py tests/test_email_task_adapter.py
git commit -m "feat: audit each unsubscribe continuation"
```

- [ ] **Step 8: Restart and verify launchd PID, target path, health, and backlog**

Use the exact reload/readback procedure from Task 2. Do not state that the feature is live unless launchd's printed path resolves to this commit.

## Task 5: Remove the Consumer-direct worker route and automatic browser recursion

**Files:**
- Modify: `app/email_worker.py`
- Modify: `app/email_unsubscribe.py`
- Modify: `app/email_store.py`
- Delete: `app/email_unsubscribe_consumer.py`
- Delete: `app/email_unsubscribe_operation.py`
- Modify: `tests/test_email_worker.py`
- Modify: `tests/test_email_unsubscribe.py`
- Modify: `tests/browser/test_email_unsubscribe_browser.py`
- Delete: `tests/test_email_unsubscribe_consumer.py`

- [ ] **Step 1: Replace direct-route tests with ordinary orchestrator assertions**

```python
def test_unsubscribe_task_uses_consumer_audit_orchestrator(email_task_fixture):
    run_email_agent_task_loop(
        email_task_fixture.task_store,
        email_task_fixture.orchestrator,
        load_task_context=email_task_fixture.load_context,
        finalize_task=email_task_fixture.finalize,
        max_cycles=1,
    )
    email_task_fixture.orchestrator.process.assert_called_once()
    assert email_task_fixture.task_store.list_agent_runs(
        email_task_fixture.task.id
    )[0].role is AgentRole.CONSUMER


def test_direct_action_does_not_create_agent_runs(direct_action_fixture):
    run_direct_actions_once(
        direct_action_fixture.store,
        direct_action_fixture.executor_factory,
    )
    assert direct_action_fixture.task_store.list_agent_runs_for_message(
        direct_action_fixture.message_identity
    ) == ()
```

Add a browser test that calls `UnsubscribeExecutor.execute(..., automatic=False)`, discovers a valid next control, and asserts it returns `UnsubscribeContinuationResult` after one newly executed operation.

- [ ] **Step 2: Run worker and unsubscribe suites to verify direct routing still exists**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_email_worker.py tests/test_email_unsubscribe.py tests/browser/test_email_unsubscribe_browser.py`

Expected: FAIL because the worker still selects `EMAIL_UNSUBSCRIBE_CONSUMER_DIRECT` and the browser still supports automatic recursive continuation.

- [ ] **Step 3: Wire the ordinary orchestrator with the Email continuation driver**

```python
return AgentOrchestrator(
    store=store,
    consumer=ConsumerAgentRunner(**shared),
    audit=AuditAgentRunner(**shared, dry_run=bool(settings.dry_run)),
    domain_continuation=EmailUnsubscribeContinuationDriver(
        EmailStore(Path(settings.db_path))
    ),
)
```

In `run_email_agent_task_loop`, remove `unsubscribe_consumer` and all lifecycle branching:

```python
context = load_task_context(task)
if not validate_audited_email_task(task, context):
    raise RuntimeError("legacy_email_unsubscribe_lifecycle")
result = orchestrator.process(
    task,
    context,
    refresh_context=lambda task=task: load_task_context(task),
)
finalize_task(task, result)
```

At Email worker startup, call `list_nonterminal_legacy_unsubscribe_task_ids()`. If non-empty, record degraded health with `legacy_email_unsubscribe_lifecycle`, fail those exact task attempts without executing them, and continue scanner/direct-action operation.

- [ ] **Step 4: Build and run only the Audit operation**

Replace the old public helpers with:

```python
def build_audited_email_unsubscribe_operation(settings: object) -> object:
    return EmailUnsubscribeAuditOperation(
        task_store=AutoReplyStore(Path(settings.db_path)),
        email_store=EmailStore(Path(settings.db_path)),
        resolve_entries=_build_email_unsubscribe_entry_resolver(settings),
        execute_effect=_build_email_unsubscribe_effect_executor(settings),
    )


def run_audited_email_unsubscribe(
    db_path: str | Path,
    task_id: int,
    execution_generation: str,
    *,
    audit_agent_run_id: int,
    accepted_action: Mapping[str, object],
) -> dict[str, object]:
    operation = build_audited_email_unsubscribe_operation(
        SimpleNamespace(db_path=Path(db_path), workspace=Path(db_path).parent)
    )
    return operation.execute(
        task_id,
        execution_generation,
        audit_agent_run_id=audit_agent_run_id,
        accepted_action=accepted_action,
    )
```

- [ ] **Step 5: Remove automatic continuation from the browser engine and Store**

Delete the `automatic_continuation` constructor parameter, `automatic` and `_automatic_depth` execution parameters, the recursive branch, and `EmailStore.continue_email_unsubscribe_consumer_direct`. The only control-discovery path must call:

```python
self.store.persist_email_unsubscribe_continuation(
    **self._store_arguments(effect),
    controls=tuple(asdict(item) for item in observation.controls),
    observation_reference=observation.state_reference,
    final_step={
        "sequence": len(effect.operations),
        "operation": operation_step.operation,
        "state": operation_step.state,
        "reference": operation_step.reference,
    },
    owner=self.owner,
)
```

Then return `UnsubscribeContinuationResult`; do not execute the discovered control in the same call.

- [ ] **Step 6: Delete Consumer-direct modules and tests, then scan for executable remnants**

Run: `rg -n "EmailUnsubscribeConsumer|consumer_direct|Consumer-direct|execute_email_unsubscribe" app tests skills frontend/src`

Expected: no runtime or active test hit. Historical migration/document references may still contain `email_unsubscribe_consumer_direct_v1` for read-only compatibility.

- [ ] **Step 7: Run worker, Store, unsubscribe, and browser tests**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_email_worker.py tests/test_email_store.py tests/test_email_unsubscribe.py tests/test_email_unsubscribe_audit.py tests/browser/test_email_unsubscribe_browser.py`

Expected: PASS.

- [ ] **Step 8: Commit removal of the second executable lifecycle**

```bash
git add -A app/email_worker.py app/email_unsubscribe.py app/email_store.py app/email_unsubscribe_consumer.py app/email_unsubscribe_operation.py tests/test_email_worker.py tests/test_email_unsubscribe.py tests/test_email_unsubscribe_consumer.py tests/browser/test_email_unsubscribe_browser.py
git commit -m "refactor: remove direct unsubscribe consumer"
```

- [ ] **Step 9: Restart and verify launchd PID, target path, health, and backlog**

Use the exact reload/readback procedure from Task 2. Verify that no legacy v1 task was executed and no new `processing` task remains abandoned.

## Task 6: Keep classification, ActionPlan, direct actions, replies, and attachments inside their approved boundaries

**Files:**
- Modify: `tests/test_email_classifier_model.py`
- Modify: `tests/test_email_learning.py`
- Modify: `tests/test_email_feedback_actions.py`
- Modify: `tests/test_email_task_producer.py`
- Modify: `tests/test_email_provider_actions.py`
- Modify: `tests/test_email_context_source.py`
- Modify: `tests/test_mail_review_skill.py`
- Modify: `skills/ceo-mail-review/SKILL.md`

- [ ] **Step 1: Add one regression matrix covering every non-negotiable boundary**

Add focused tests with these exact assertions:

```python
assert rejected_model.auto_action_eligible is False
assert candidate_model.auto_action_eligible is False
assert model_only_prediction.status == "pending_feedback"
assert confirmed_plan.classification_source == "user"
assert confirmed_plan.model_id == classification.model_version
assert confirmed_plan.config_version == category_config.config_version
assert all(action.action_type != EmailAction.AUTO_REPLY for action in confirmed_plan.actions)
assert task_producer.ensure_action_plan_tasks(direct_only_plan, task_input) == ()
assert tuple(task.action_type for task in task_producer.ensure_action_plan_tasks(unsubscribe_plan, task_input)) == (EmailAction.UNSUBSCRIBE,)
assert trash_result.provider_operation == "move_to_trash"
assert "EXPUNGE" not in provider_executor.supported_operations
assert context.task.image_paths == ()
assert all(material.kind == "attachment_metadata" for material in attachment_materials)
```

Also verify each configured action is independently gated: a category eligible for label but not trash produces an ActionPlan with authorized label and ineligible trash; changing the threshold creates a new plan and never expands the old plan.

- [ ] **Step 2: Run the boundary matrix**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_email_classifier_model.py tests/test_email_learning.py tests/test_email_feedback_actions.py tests/test_email_task_producer.py tests/test_email_provider_actions.py tests/test_email_context_source.py tests/test_mail_review_skill.py`

Expected: PASS if existing boundaries remain intact; any failure is a regression to fix within the owning module before proceeding.

- [ ] **Step 3: Tighten the mail-review Skill wording and its exact-string tests**

Ensure the Skill includes these operation rows:

```markdown
| Classification confirmation | Save final category and feedback only; create no generic task. |
| Deterministic mailbox action | Email worker executes exact configured action and reads provider state back; no Agent run. |
| Unsubscribe proposal | Consumer proposes exactly one operation bound to the immutable ActionPlan; Consumer has no write capability. |
| Unsubscribe execution | Audit alone invokes the task/run-bound capability and verifies the external result. |
| Reply or mailto unsubscribe | Disabled; do not draft, send, or request SMTP capability. |
| Attachment | Metadata only; never download, open, OCR, parse, summarize, or infer body content. |
```

- [ ] **Step 4: Commit the cross-boundary regression contract**

```bash
git add tests/test_email_classifier_model.py tests/test_email_learning.py tests/test_email_feedback_actions.py tests/test_email_task_producer.py tests/test_email_provider_actions.py tests/test_email_context_source.py tests/test_mail_review_skill.py skills/ceo-mail-review/SKILL.md
git commit -m "test: lock email action boundaries"
```

This is test/Skill-only unless a focused regression exposes an existing mismatch. If runtime fixes are required, put them in a separate commit named for the corrected boundary and perform the runtime reload contract.

## Task 7: Project Audit evidence into the Email API and UI

**Files:**
- Modify: `app/email_store.py`
- Modify: `app/web_api/email.py`
- Modify: `tests/test_email_web_api.py`
- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/pages/EmailPage.tsx`
- Modify: `frontend/src/pages/EmailPage.test.tsx`

- [ ] **Step 1: Write failing API and UI tests for audited observability**

```python
def test_email_detail_projects_audited_unsubscribe_runs(client, audited_unsubscribe_fixture):
    response = client.get(
        f"/api/console/email/classifications/{audited_unsubscribe_fixture.classification_id}"
    )
    event = response.json()["observability"][0]
    assert event["kind"] == "unsubscribe"
    assert event["lifecycle_version"] == "email_unsubscribe_audited_v2"
    assert event["task_id"] == audited_unsubscribe_fixture.task.id
    assert event["consumer_run_ids"]
    assert event["audit_run_ids"]
    assert event["receipt_id"] == audited_unsubscribe_fixture.receipt_id
    assert "private_url" not in json.dumps(event)
```

```tsx
expect(await screen.findByText("Consumer → Audit")).toBeInTheDocument();
expect(screen.getByText("email_unsubscribe_audited_v2")).toBeInTheDocument();
expect(screen.getByText(/Audit run/)).toBeInTheDocument();
expect(screen.queryByText(/Consumer-direct/)).not.toBeInTheDocument();
```

- [ ] **Step 2: Run focused API and UI tests and confirm missing fields/copy**

Run:

```bash
/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_email_web_api.py
cd frontend && pnpm test -- --run src/pages/EmailPage.test.tsx
```

Expected: FAIL because current observability has receipt/steps but not task and run lineage, and the save message still says Consumer-direct.

- [ ] **Step 3: Add a redacted task/run projection to Store observability**

For each unsubscribe receipt, join by `action_identity = reply_tasks.trigger_message_id` and return only:

```python
{
    "kind": "unsubscribe",
    "operation": "unsubscribe",
    "lifecycle_version": lifecycle_version,
    "task_id": task_id,
    "task_status": task_status,
    "consumer_run_ids": consumer_run_ids,
    "audit_run_ids": audit_run_ids,
    "status": "done",
    "receipt_id": receipt_id,
    "result_text": result_text,
    "evidence": evidence,
    "observation_digest": observation_digest,
    "steps": steps,
}
```

Do not return `trigger_message_json`, provider locator, entry private URL, browser profile path, cookie, credential, query token, or raw tool transcript.

- [ ] **Step 4: Extend the TypeScript contract and render lifecycle lineage**

```typescript
export interface EmailObservabilityEvent {
  // existing fields remain
  lifecycle_version?: string;
  task_id?: number;
  task_status?: string;
  consumer_run_ids?: number[];
  audit_run_ids?: number[];
}
```

Use this copy after configuration save:

```typescript
setMessage("配置已保存：确定性动作由 Email worker 执行并回读；退订由 Consumer 提案、Audit 审核执行。邮件回复已全局禁用。");
```

In unsubscribe details render `Consumer → Audit`, lifecycle version, task ID/status, Consumer run IDs, Audit run IDs, receipt, final result text, digest, and steps. Render only fields returned by the safe projection.

- [ ] **Step 5: Run API, UI test, and TypeScript gates**

Run:

```bash
/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_email_web_api.py
cd frontend && pnpm test -- --run src/pages/EmailPage.test.tsx
cd frontend && pnpm typecheck
cd frontend && pnpm build
```

Expected: all commands PASS.

- [ ] **Step 6: Commit UI and observability**

```bash
git add app/email_store.py app/web_api/email.py tests/test_email_web_api.py frontend/src/api/console.ts frontend/src/pages/EmailPage.tsx frontend/src/pages/EmailPage.test.tsx
git commit -m "feat: show audited email outcomes"
```

- [ ] **Step 7: Restart and verify launchd PID, target path, HTTP health, Email worker health, and backlog**

Use the Task 2 reload contract. Also fetch the running console Email detail endpoint for a known local fixture only if the active runtime uses this checkout; otherwise retain browser/API verification as development evidence, not production evidence.

## Task 8: Replace the direct E2E with loopback audited execution

**Files:**
- Create: `tests/e2e/test_email_unsubscribe_audited.py`
- Delete: `tests/e2e/test_email_unsubscribe_consumer_direct.py`
- Modify: `tests/browser/test_email_unsubscribe_browser.py`
- Modify: `tests/test_email_worker.py`

- [ ] **Step 1: Build a loopback-only E2E fixture**

The test must create:

```python
account = create_enabled_imap_account(account_id="account-a")
message = ingest_text_email(
    account_id=account.account_id,
    subject="Weekly newsletter",
    body="Manage subscription",
    list_unsubscribe=loopback_https_url,
)
classification = confirm_category(message, "subscription")
plan = create_action_plan(classification, actions=(EmailAction.UNSUBSCRIBE,))
task = ensure_email_tasks(plan)[0]
```

Use fake Consumer and Audit Agent turn executors that still persist ordinary typed Agent runs. The Consumer proposes one `OPEN_ENTRY`; Audit invokes `execute_audited_email_unsubscribe` with its own run ID. For a two-page loopback flow, the first call must persist `awaiting_audit`; refreshed context must cause a second Consumer proposal containing the accepted prefix plus exactly one control; the second Audit executes only that control.

- [ ] **Step 2: Assert end-to-end lineage and negative boundaries**

```python
assert task.channel == "email"
assert json.loads(task.trigger_message_json)["lifecycle_version"] == "email_unsubscribe_audited_v2"
assert [run.role for run in runs].count(AgentRole.CONSUMER) == 2
assert [run.role for run in runs].count(AgentRole.AUDIT) == 2
assert store.list_email_unsubscribe_steps(plan.actions[0].action_identity) == [
    expected_open_step,
    expected_confirmation_step,
]
assert receipt.result_text == "You have been unsubscribed"
assert task_store.get_reply_task(task.id).status == "done"
assert loopback_server.requests == [expected_get, expected_confirm_post]
assert browser_launch.headless is True
assert browser_launch.profile_path == dedicated_email_profile
assert browser_launch.profile_path != main_chrome_profile
assert smtp_connections == []
assert attachment_reads == []
```

Add separate loopback cases for RFC one-click cookie-free exact POST, unapproved redirect, popup, download, private-network target, password, MFA/QR, CAPTCHA, payment, and redacted/16-KiB result text.

- [ ] **Step 3: Run the new E2E and browser suite**

Run: `WORKBENCH_BROWSER_TESTS=1 /Users/derek/miniforge3/bin/python3 -m pytest -q tests/e2e/test_email_unsubscribe_audited.py tests/browser/test_email_unsubscribe_browser.py`

Expected: PASS. The test must bind only a loopback server and must not connect to IMAP, SMTP, a real unsubscribe website, Chrome's main profile, or the Internet.

- [ ] **Step 4: Prove deterministic direct actions still create zero Agent/Audit runs**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q tests/test_email_worker.py -k 'direct_action and not unsubscribe'`

Expected: PASS with zero task, Consumer run, and Audit run assertions.

- [ ] **Step 5: Commit audited E2E coverage**

```bash
git add -A tests/e2e/test_email_unsubscribe_audited.py tests/e2e/test_email_unsubscribe_consumer_direct.py tests/browser/test_email_unsubscribe_browser.py tests/test_email_worker.py
git commit -m "test: cover audited email unsubscribe end to end"
```

## Task 9: Run full verification, update progress evidence, and keep production disabled

**Files:**
- Modify: `docs/superpowers/specs/2026-08-31-email-integration-main-progress.md`
- Modify: `docs/superpowers/specs/2026-09-02-email-ceo-agent-audited-fusion-design.md`
- Modify if generated by existing project workflow: `docs/superpowers/specs/email-ui-audit-report.md`
- Verify: all Email/backend/frontend/runtime files changed by Tasks 1-8

- [ ] **Step 1: Scan for forbidden executable paths and unsafe capabilities**

Run:

```bash
rg -n "EmailUnsubscribeConsumer|EMAIL_UNSUBSCRIBE_CONSUMER_DIRECT|continue_email_unsubscribe_consumer_direct|execute_email_unsubscribe|Consumer-direct" app tests skills frontend/src docs/architecture.md docs/runtime-mechanism.md
rg -n "auto_reply|smtplib|SMTP_SSL|EXPUNGE|empty.*Trash|attachment.*(open|download|OCR|parse|summar)" app/email_* skills/ceo-mail-review/SKILL.md
```

Expected: the first command has no active runtime/UI/Skill/current-doc hit; the legacy lifecycle string may remain only in migration inventory and historical documentation. The second command shows only explicit rejection/disabled assertions, historical compatibility readers, and test fixtures—no reachable send, permanent-delete, or attachment-content path.

- [ ] **Step 2: Run the complete focused Email backend matrix**

Run:

```bash
/Users/derek/miniforge3/bin/python3 -m pytest -q \
  tests/test_documentation_contract.py \
  tests/test_mail_review_skill.py \
  tests/test_email_connector_config.py \
  tests/test_email_imap_readonly.py \
  tests/test_email_classifier_model.py \
  tests/test_email_learning.py \
  tests/test_email_feedback_actions.py \
  tests/test_email_task_adapter.py \
  tests/test_email_task_producer.py \
  tests/test_task_lifecycle.py \
  tests/test_email_worker.py \
  tests/test_email_provider_actions.py \
  tests/test_email_context_source.py \
  tests/test_email_store.py \
  tests/test_email_unsubscribe.py \
  tests/test_email_unsubscribe_audit.py \
  tests/test_email_unsubscribe_continuation.py \
  tests/test_audit_agent.py \
  tests/test_agent_cli.py \
  tests/test_email_web_api.py \
  tests/browser/test_email_unsubscribe_browser.py \
  tests/e2e/test_email_unsubscribe_audited.py
```

Expected: PASS with zero failure.

- [ ] **Step 3: Run the full backend suite**

Run: `/Users/derek/miniforge3/bin/python3 -m pytest -q`

Expected: PASS with zero failure. Record the exact test count and elapsed time in the progress document.

- [ ] **Step 4: Run all frontend gates**

Run:

```bash
cd frontend && pnpm test -- --run
cd frontend && pnpm typecheck
cd frontend && pnpm build
```

Expected: all PASS. Record test count, typecheck result, and build artifact completion.

- [ ] **Step 5: Exercise the four Email tabs in a local browser**

Start the development console against a disposable local database containing only fixtures. Verify:

1. **已处理** shows final classification, full model ID, config version, ActionPlan version, direct-action readback, audited task lineage, Audit runs, and terminal result text.
2. **待反馈** shows model alternatives, confidence, margin, text preview, and attachment metadata; confirming a category creates no generic task and creates an unsubscribe task only when category configuration explicitly contains that action.
3. **邮件配置** exposes no `auto_reply`, does not require SMTP, saves versioned category/action configuration, and explains direct versus Consumer/Audit execution accurately.
4. **学习** shows active/candidate/rejected status, training time, sample count, source coverage, validation method, per-category metrics, latency, artifact digest, and promotion/rejection reason; rejected v3 remains ineligible.

Capture screenshots and update the existing UI audit report only through the repository's established screenshot workflow. Expected: no private URL, token, credential, attachment body, or local browser-profile path appears.

- [ ] **Step 6: Verify the production database migration fence read-only**

Run the repository's read-only status command against the configured service database and call `list_nonterminal_legacy_unsubscribe_task_ids()` without changing rows.

Expected: an empty tuple before audited-v2 activation. If non-empty, stop activation, record `legacy_email_unsubscribe_lifecycle`, and leave every row/effect untouched.

- [ ] **Step 7: Restart the real service only from its actual checkout and verify live state**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: new PID. Verify printed executable/working directory and the checkout's `git rev-parse HEAD`. If the active checkout does not contain the audited commits, report the feature as development-verified and not deployed.

For an active matching checkout, verify HTTP health, Email worker health, each enabled account's scan/cursor/error, active model status, pending feedback count, direct-action backlog, unsubscribe task/Audit backlog, and training status. Expected: no unresolved `processing` or newly failed backlog.

- [ ] **Step 8: Update evidence and design status without enabling real writes**

Append an evidence table containing exact commit SHAs, commands, pass counts, browser screenshots, launchd PID/path/SHA, and backlog readback. Change the design status only to:

```text
状态：实现完成并通过开发/loopback 验证；production-disabled，等待分阶段受控验收
```

Keep all model-only actions disabled because v3 remains rejected. Do not connect SMTP, reply, move/trash a real message, or unsubscribe from a real site in this task.

- [ ] **Step 9: Commit verification evidence**

```bash
git add docs/superpowers/specs/2026-08-31-email-integration-main-progress.md docs/superpowers/specs/2026-09-02-email-ceo-agent-audited-fusion-design.md docs/superpowers/specs/email-ui-audit-report.md
git commit -m "docs: record audited email verification"
```

If the UI audit report path was not generated or changed, omit it from `git add`; do not create an empty evidence file.

- [ ] **Step 10: Final branch integrity check**

Run:

```bash
git status --short
git log --oneline --decorate -10
git diff --check abf5e2afb6ca8f9be7756d87f1f931abf23c3c11..HEAD
```

Expected: clean worktree, the policy commit separate from runtime commits, no whitespace error, and no unrelated user-owned file included.

## Plan self-review record

- Spec coverage: Tasks 1-2 cover policy, audited identity, and legacy migration; Tasks 3-5 cover Consumer/Audit capability, one-operation continuation, Store/browser fences, and removal of the second lifecycle; Task 6 covers classification, immutable ActionPlan, direct actions, no reply, metadata-only attachments, and rejected-model eligibility; Task 7 covers Email/History observability; Task 8 covers browser and audited E2E; Task 9 covers full verification, four-tab UI review, production-disabled staging, live-path evidence, and backlog checks.
- Placeholder scan: the plan contains no deferred implementation marker or unspecified error-handling step; every code change step names the exact contract, command, and expected result.
- Type consistency: lifecycle is `email_unsubscribe_audited_v2`; the write tool is `execute_audited_email_unsubscribe`; task identity uses `task_id` plus `execution_generation`; execution identity additionally requires `audit_agent_run_id`; Consumer/Audit continuation uses proposal revisions while `_feedback_cycles` counts only `FEEDBACK_PROVIDED` Audit outcomes.
- Scope boundary: the plan performs no real mailbox write, SMTP connection, reply, real unsubscribe, model promotion, or production activation.
