# Consumer Attempt Result Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the current Attempt-linked Consumer result in the Attempt detail grid, including percentages, risk, unavailable-result reasons, and a separate status for a later pending or running Consumer run.

**Architecture:** Keep all state read-only. `render_attempt_detail` already resolves the Attempt terminal run, its generation runs, and the current ReplyTask; extend it to load the current task generation when necessary. A focused projection helper in `app/audit_web.py` follows the terminal run's exact Consumer/Audit parent relationship, parses only the linked Consumer result, and returns display fields for the existing detail grid.

**Tech Stack:** Python 3, Pydantic `ConsumerAgentResult`, SQLite-backed `AutoReplyStore`, server-rendered FastAPI HTML, pytest.

---

## File structure

| File | Responsibility |
| --- | --- |
| `app/audit_web.py` | Select the exact linked Consumer run, build safe display fields, identify an active newer Consumer run, and append those fields to the existing Attempt detail grid. |
| `tests/test_audit_web.py` | Persist realistic Consumer/Audit run chains and verify the complete rendered HTML for normal, unavailable, historical, and active-run cases. |
| `docs/superpowers/specs/2026-09-14-consumer-attempt-result-design.md` | Approved behavior contract; no functional change after this plan is written. |

Before implementation, reread `docs/agent-claims.md`. `app/audit_web.py` is currently claimed by `attention-reconciliation`; coordinate or wait for that owner, then claim `app/audit_web.py` and `tests/test_audit_web.py` before changing either file. Do not alter unrelated dirty files.

### Task 1: Add failing Attempt-detail regression coverage

**Files:**
- Modify: `tests/test_audit_web.py` near `test_orchestrated_attempt_detail_links_consumer_and_execution_sessions`
- Modify: `docs/agent-claims.md` only to claim the test file before editing

- [ ] **Step 1: Claim the test file and inspect the existing attempt seed helpers**

Add a claim row that names only `tests/test_audit_web.py` and describes Consumer result detail coverage. Then review the existing `store.claim_agent_run`, `store.complete_agent_run`, `store.fail_agent_run`, and `store.finalize_orchestrated_reply_task` test patterns; each test must persist its own task/run chain in its own temporary SQLite database.

- [ ] **Step 2: Write a small, strict-valid Consumer result payload helper**

Add this helper next to the existing attempt test helpers so every new test persists the same valid result shape:

```python
def _consumer_result_payload(
    *,
    confidence: float = 0.82,
    information_completeness: float = 0.75,
    rule_coverage: float = 1.0,
    risk: str = "medium",
) -> dict[str, object]:
    return {
        "outcome": "proposal",
        "summary": "Publish the reviewed update.",
        "proposal": {
            "objective": "Publish the reviewed update.",
            "actions": [
                {
                    "description": "Publish the update.",
                    "action_identity": "publish-reviewed-update",
                    "capability": "dingtalk-chat",
                    "operation": "send_to_group",
                    "target": {"conversation_id": "cid-consumer-result"},
                    "payload": {"text": "Reviewed update"},
                }
            ],
            "sourced_facts": [],
            "authored_judgment": "The update is ready to publish.",
        },
        "decision_options": [],
        "error": {
            "code": "",
            "retryable": False,
            "authorization_required": False,
        },
        "risk": risk,
        "confidence": confidence,
        "rule_coverage": rule_coverage,
        "information_completeness": information_completeness,
    }
```

- [ ] **Step 3: Write the normal linked-Consumer test before implementing rendering**

Create one Consumer run with `_consumer_result_payload()`, create a completed Audit run whose `parent_agent_run_id` is that Consumer's ID, finalize the Attempt with that Audit run, then assert the detail HTML contains the labels and values below:

```python
assert "Consumer 执行结果" in html
assert "confidence" in html and "82%" in html
assert "information_completeness" in html and "75%" in html
assert "rule_coverage" in html and "100%" in html
assert "risk" in html and "medium" in html
```

Run:

```sh
pytest tests/test_audit_web.py::test_attempt_detail_renders_linked_consumer_result -q
```

Expected: FAIL because the current Attempt detail grid does not render Consumer result fields.

- [ ] **Step 4: Add the exact-link and Consumer-terminal failure tests**

Persist two revisions in one generation: first Consumer/Audit with `confidence=0.10`, then second Consumer/Audit with `confidence=0.82`; finalize the Attempt with the first Audit run. Assert `10%` is present and `82%` is absent. Add a separate Attempt finalized directly from a Consumer run and assert it shows that Consumer's `82%` / `medium` values. Both tests must name the current terminal run explicitly rather than select a run by list position.

Run:

```sh
pytest tests/test_audit_web.py -q -k 'linked_consumer_result or consumer_terminal_result or exact_link'
```

Expected: FAIL until the implementation follows `parent_agent_run_id` and handles a Consumer terminal run.

- [ ] **Step 5: Add unavailable-result and active-run failure tests**

Write three tests with these exact assertions:

```python
assert html.count(">—<") >= 4
assert "Consumer 结果不符合当前契约" in html
assert "新 Consumer run" in html
assert "等待中" in html
```

For the first test, persist a completed Consumer run with `final_result_json` containing `{"outcome":"proposal"}` through a direct SQLite update after it is completed; this intentionally preserves an old malformed stored record and must not add compatibility parsing. For the second, call `store.fail_agent_run` with `{"code":"consumer_source_failed", "detail":"Source read failed"}` and assert its safe detail is shown while `final_result_json` content is absent. Build the current-generation cases with this exact sequence:

```python
status, _, _ = handle_rerun_attempt_post(
    store,
    attempt_id,
    worker_factory=lambda settings: (_ for _ in ()).throw(AssertionError()),
)
assert status == 303
pending_html = render_attempt_detail(store, attempt_id)[1]
assert "新 Consumer run" in pending_html
assert "等待中" in pending_html

current_task = store.get_reply_task_for_message("cid-consumer-result", "msg-consumer-result")
assert current_task is not None and current_task.status == "pending"
claimed_task = store.claim_reply_task(current_task.id)
assert claimed_task is not None
running_consumer = store.claim_agent_run(
    claimed_task.id,
    claimed_task.execution_generation,
    role=AgentRole.CONSUMER,
    proposal_revision=0,
    turn_attempt=0,
    parent_agent_run_id=None,
    operation_id="",
    owner="running-consumer",
).run
assert running_consumer.status == "running"
running_html = render_attempt_detail(store, attempt_id)[1]
assert "新 Consumer run" in running_html
assert "运行中" in running_html
assert "82%" in running_html
```

This proves that the current task generation is read separately from the old Attempt's linked result and that the old four field values remain unchanged.

Run:

```sh
pytest tests/test_audit_web.py -q -k 'consumer_result_unavailable or consumer_result_failure or new_consumer_run'
```

Expected: FAIL because the existing renderer neither exposes the unavailable state nor reads the current task generation for a new run.

- [ ] **Step 6: Commit the failing tests**

```sh
git add tests/test_audit_web.py docs/agent-claims.md
git commit -m "test(console): cover consumer result on attempt detail"
```

Expected: the commit contains only this task's test hunks and claim row; it does not stage existing work by other owners.

### Task 2: Implement the read-only Consumer result projection

**Files:**
- Modify: `app/audit_web.py` near `render_attempt_detail` and `_attempt_detail_body`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Add the Consumer-result fields helper**

Place these helpers immediately before `_attempt_detail_body`. They use the already-imported `ConsumerAgentResult`, `AgentRole`, `json`, and `safe_observability_error` and return only labels/values consumed by `_attempt_detail_grid`:

```python
def _linked_consumer_run(
    terminal_run: AgentRun | None,
    agent_runs: list[AgentRun],
) -> AgentRun | None:
    if terminal_run is None:
        return None
    if terminal_run.role is AgentRole.CONSUMER:
        return terminal_run
    if terminal_run.role is not AgentRole.AUDIT or terminal_run.parent_agent_run_id is None:
        return None
    return next(
        (run for run in agent_runs if run.id == terminal_run.parent_agent_run_id),
        None,
    )


def _consumer_result_error(run: AgentRun | None) -> str:
    if run is None:
        return "未找到当前 Attempt 关联的 Consumer run"
    if run.status == "failed":
        try:
            error = json.loads(run.structured_error_json or "{}")
        except json.JSONDecodeError:
            error = {}
        if isinstance(error, dict):
            detail = str(error.get("detail") or error.get("code") or "").strip()
            if detail:
                return safe_observability_error(detail, limit=180)
        return "Consumer 运行失败"
    if not run.final_result_json.strip():
        return "Consumer 未保存最终结果"
    return "Consumer 结果不符合当前契约"


def _consumer_result_fields(
    terminal_run: AgentRun | None,
    agent_runs: list[AgentRun],
    current_agent_runs: list[AgentRun],
    reply_task: ReplyTask | None,
) -> list[tuple[str, str]]:
    consumer = _linked_consumer_run(terminal_run, agent_runs)
    labels = ("confidence", "information_completeness", "rule_coverage", "risk")
    fields = [("Consumer 执行结果", f"run #{consumer.id}" if consumer else "—")]
    try:
        result = ConsumerAgentResult.model_validate_json(consumer.final_result_json) if consumer else None
    except ValueError:
        result = None
    if result is None:
        fields.extend((label, "—") for label in labels)
        fields.append(("Consumer error", _consumer_result_error(consumer)))
    else:
        fields.extend(
            (
                ("confidence", f"{result.confidence:.0%}"),
                ("information_completeness", f"{result.information_completeness:.0%}"),
                ("rule_coverage", f"{result.rule_coverage:.0%}"),
                ("risk", result.risk.value),
            )
        )
    active = next(
        (
            run for run in reversed(current_agent_runs)
            if run.role is AgentRole.CONSUMER
            and run.id != (consumer.id if consumer else 0)
            and run.status in {"pending", "running"}
        ),
        None,
    )
    if active is not None:
        state = "等待中" if active.status == "pending" else "运行中"
        fields.append(("新 Consumer run", state))
    elif reply_task is not None and reply_task.status == "pending":
        fields.append(("新 Consumer run", "等待中"))
    return fields
```

- [ ] **Step 2: Extend `render_attempt_detail` to load current-generation runs**

After resolving `reply_task`, build `current_agent_runs` from the existing generation list unless the task has a different current generation. Use this exact branch, which keeps reads bounded and does not mutate Store state:

```python
current_agent_runs = agent_runs
if (
    reply_task is not None
    and reply_task.execution_generation
    and (
        terminal_run is None
        or reply_task.execution_generation != terminal_run.execution_generation
    )
):
    current_agent_runs = store.list_agent_runs_for_task_generation(
        reply_task.id,
        reply_task.execution_generation,
    )
```

Pass `terminal_run` and `current_agent_runs` to `_attempt_detail_body` as named keyword arguments. Do not replace the existing `agent_runs` argument: that list remains the historical generation used by links, revisions, attention, and runtime-attempt evidence.

- [ ] **Step 3: Append the projected fields in `_attempt_detail_body`**

After adding the existing `revisions` row, append the helper output exactly once:

```python
fields.extend(
    _consumer_result_fields(
        terminal_run,
        agent_runs,
        current_agent_runs or [],
        reply_task,
    )
)
```

Add optional `terminal_run: AgentRun | None = None` and `current_agent_runs: list[AgentRun] | None = None` keyword parameters to `_attempt_detail_body`, then provide them only from `render_attempt_detail`. Existing direct unit callers continue to receive the unavailable-result rendering rather than a crash.

- [ ] **Step 4: Run the focused regression suite**

```sh
pytest tests/test_audit_web.py -q -k 'consumer_result or orchestrated_attempt_detail'
```

Expected: PASS. Confirm the test assertions cover `82%`, `75%`, `100%`, `medium`, four `—` values plus an error, exact terminal linkage, direct Consumer terminal linkage, `pending`, and `running` without any Store writes during `render_attempt_detail`.

- [ ] **Step 5: Commit the renderer and focused tests**

```sh
git add -p app/audit_web.py tests/test_audit_web.py docs/agent-claims.md
git commit -m "feat(console): show consumer result on attempts"
```

Expected: staged hunks are limited to the read-only projection and tests. Resolve any overlap with the current `attention-reconciliation` owner before committing; never overwrite its hunk.

### Task 3: Verify the delivered server behavior

**Files:**
- Verify: `app/audit_web.py`
- Verify: `tests/test_audit_web.py`
- Verify: live `/attempts/{id}` HTML using a locally persisted fixture or a non-sensitive existing Attempt

- [ ] **Step 1: Run the complete relevant test module**

```sh
pytest tests/test_audit_web.py -q
```

Expected: PASS. If unrelated pre-existing failures occur, record their exact test names and failure output separately; do not weaken or skip the new Consumer-result assertions.

- [ ] **Step 2: Verify Python imports before restart**

Because restarting deploys every dirty file in the shared tree, first notify the current `app/audit_web.py` owner and verify the main service imports:

```sh
python -c "import app.cli, app.worker, app.email_worker, app.service_supervisor"
```

Expected: exit status 0. Do not restart until the owner has been informed and the shared working tree is known importable.

- [ ] **Step 3: Restart the non-hot-reloading service and verify a new process**

```sh
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: the job is running with a newly started process and without a Python import failure.

- [ ] **Step 4: Perform live page and queue readback**

Use an Attempt that has a persisted linked Consumer result, fetch `/attempts/{attempt_id}`, and verify all four labels and values in rendered HTML. Then query the service's active task/attempt state using the existing Store read APIs or console status endpoint and confirm no unresolved `processing` or new `failed` backlog was introduced by the restart. Record the Attempt ID, the four rendered values, process identifier, and backlog result in the implementation handoff.

- [ ] **Step 5: Release the claim after the implementation commit and verification**

Remove only the implementation task's row from `docs/agent-claims.md`, then commit that release separately:

```sh
git add docs/agent-claims.md
git commit -m "chore: release consumer attempt result claim"
```

Expected: the claim board no longer names the completed Consumer-result work; unrelated owners and their rows remain intact.

## Plan self-review

- **Spec coverage:** Task 1 covers valid metrics, direct Consumer terminal linkage, exact Audit parent linkage, malformed/missing/failed results, and active `pending`/`running` state. Task 2 implements each condition through a single read-only projection. Task 3 covers module tests, service reload, live HTML, and backlog verification.
- **No placeholders:** Every task lists exact files, test construction, commands, expected outcomes, and the required implementation helper logic.
- **Type consistency:** The plan uses existing `AgentRun`, `AgentRole`, `ReplyTask`, `ConsumerAgentResult`, `final_result_json`, `structured_error_json`, `parent_agent_run_id`, `execution_generation`, and `list_agent_runs_for_task_generation` names consistently.
