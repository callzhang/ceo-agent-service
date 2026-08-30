# Stale WeChat Delivery Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guarded, no-send CLI operation that closes an exhausted stale `target_open_failed` WeChat delivery as `skipped` with accurate audit evidence.

**Architecture:** `AutoReplyStore` owns one atomic transition with all state, generation, age, failure-code, and retry-count guards. `app.cli` exposes only the exact-ID operator command and delegates to the Store; no sender or UI path participates.

**Tech Stack:** Python 3.12, SQLite, Pydantic CLI settings, argparse, pytest.

---

### Task 1: Guarded Store transition

**Files:**
- Modify: `tests/wechat/test_store.py`
- Modify: `app/store.py`

- [ ] **Step 1: Write the failing success-path test**

Add `test_exhausted_stale_pre_action_delivery_can_be_skipped_without_send`.
Seed one WeChat reply task, a matching reply attempt with `retry_count=2`, and
a delivery whose persisted state is `failed`, `error='target_open_failed'`,
`pre_action_failure=1`, and `action_started_at='2026-08-30 17:07:23'`.
Call:

```python
store.skip_exhausted_stale_wechat_delivery(
    delivery_id,
    expected_execution_generation=delivery.execution_generation,
    reason="stale_after_exhausted_pre_action_retries",
    inactive_before="2026-08-30 18:00:00",
    max_retries=2,
)
```

Assert the delivery and reply attempt both become `skipped`, both retain the
factual reason, and `pre_action_failure` becomes false.

- [ ] **Step 2: Run the success-path test and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_store.py::test_exhausted_stale_pre_action_delivery_can_be_skipped_without_send -q
```

Expected: FAIL because `AutoReplyStore` has no
`skip_exhausted_stale_wechat_delivery` method.

- [ ] **Step 3: Add guard tests before implementation**

Add independent rejection cases for retry count below the limit, wrong failure
code, `pre_action_failure=0`, action time newer than the cutoff, status other
than `failed`, and execution-generation mismatch. Each case must assert
`AgentRunLeaseLostError` and unchanged delivery/attempt state. Add validation
cases for blank reason, blank generation, blank cutoff, and `max_retries < 1`
raising `ValueError` before mutation.

- [ ] **Step 4: Implement the minimal atomic Store method**

Add this public method to `AutoReplyStore` near the existing WeChat recovery
methods:

```python
def skip_exhausted_stale_wechat_delivery(
    self,
    delivery_id: int,
    *,
    expected_execution_generation: str,
    reason: str,
    inactive_before: str,
    max_retries: int = 2,
) -> None:
    ...
```

Validate scalar inputs first, then use `_immediate_write_transaction()` for one
guarded `UPDATE ... RETURNING`. The SQL must guard current reply-task generation,
`failed`, `pre_action_failure=1`, `error='target_open_failed'`, non-empty old
`action_started_at`, and the latest matching WeChat reply-attempt retry count.
Set `status='skipped'`, `error=?`, `pre_action_failure=0`, and the updated
timestamp. If no row is returned, raise `AgentRunLeaseLostError`. Call
`_sync_wechat_delivery_reply_attempt()` inside the same transaction with
`delivery_status='skipped'` and the same reason.

- [ ] **Step 5: Run Store tests and verify GREEN**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_store.py -q
```

Expected: all Store tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add app/store.py tests/wechat/test_store.py
git commit -m "fix: close exhausted stale wechat deliveries"
```

### Task 2: Exact-ID CLI operation

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `app/cli.py`

- [ ] **Step 1: Write the failing CLI test**

Add `test_skip_stale_wechat_delivery_command_closes_exact_eligible_delivery`.
Seed the same eligible delivery in a temporary database. Invoke the command
function with exact delivery ID, cutoff, reason, and retry limit. Assert stdout
is exactly `wechat-delivery skipped=<id>\n` and Store readback is `skipped`.

- [ ] **Step 2: Run the CLI test and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_cli.py::test_skip_stale_wechat_delivery_command_closes_exact_eligible_delivery -q
```

Expected: FAIL because the command function and parser entry do not exist.

- [ ] **Step 3: Implement parser and command function**

Register `skip-stale-wechat-delivery` with positive integer delivery ID and
retry count, plus required `inactive-before` and `reason` strings. The command
function loads the exact delivery, rejects a missing record, reads its current
execution generation, calls the Store method, and prints the stable receipt.
It must not construct a sender, reader, DWS client, or runtime Agent.

- [ ] **Step 4: Run CLI and focused WeChat tests**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_cli.py -k skip_stale_wechat_delivery -q
/Users/derek/miniforge3/bin/python -m pytest tests/wechat/test_store.py tests/wechat/test_service.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add app/cli.py tests/test_cli.py
git commit -m "feat: expose stale wechat delivery closure"
```

### Task 3: Broad verification and integration evidence

**Files:**
- Verify only; modify files only to fix failures introduced by Tasks 1–2.

- [ ] **Step 1: Run relevant broad tests**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/wechat tests/test_feedback_processing.py tests/test_feedback_processing_e2e.py tests/test_cli.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run static checks**

```bash
/Users/derek/miniforge3/bin/python -m ruff check app/store.py app/cli.py tests/wechat/test_store.py tests/test_cli.py
git diff --check main...HEAD
git status --short
```

Expected: Ruff and diff check exit zero; only intended committed files appear
between `main` and `HEAD`; the worktree is clean.

- [ ] **Step 3: Record exact final commit and hand off for two-stage review**

```bash
git rev-parse main
git rev-parse HEAD
git log --oneline main..HEAD
```

Provide the base SHA, head SHA, exact test output, and the design/plan paths to
the spec reviewer, then to the code-quality reviewer. Fix and re-review every
Critical or Important issue before integration.
