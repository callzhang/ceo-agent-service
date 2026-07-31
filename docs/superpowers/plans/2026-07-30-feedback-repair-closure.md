# Service Problem Triage And Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Analyze every reported service problem as an existing-behavior bug or a new feature request, automatically repair reproducible bugs on isolated branches with TDD and a complete audit trail, and place feature requests on a new approval page without implementing them before Derek approves.

**Architecture:** Keep business and code judgment in the existing Codex automation that already inherits the installed Codex MCP configuration. The service only persists issue, repair, and approval state and exposes safe transitions; it does not add another provider client or a self-modifying background Agent. Bugs run in isolated Git worktrees through an explicit red-test, fix, green-test, merge, resumability-check, restart, and live-verification sequence. Feature requests stop at a durable approval record.

**Tech Stack:** Python 3.12, SQLite, Pydantic v2, FastAPI audit UI, native Codex CLI, Git worktrees, pytest, launchd.

---

## Product Decisions

This revision incorporates Derek's 2026-07-31 feedback:

1. Confirmed-fact behavior is a prompt-only change. It is implemented by `docs/superpowers/plans/2026-07-30-agent-confirmed-fact-grounding.md` and must not add another response schema or service-side fact structure.
2. Every service problem, whether caused by this repository or an external dependency, is analyzed before development starts.
3. A problem is a **bug** only when current behavior violates an existing contract in code, prompt, configuration, documentation, test, or external-integration expectations.
4. A request is a **feature** when it asks for behavior that is not currently promised by an existing contract.
5. A transient dependency incident that the existing retry and recovery contract handles correctly is neither a bug nor a feature. It is recorded as a recovered incident and closed without code changes.
6. Every bug fix uses a new branch and worktree, adds a regression test first, proves the test fails before the fix, proves it passes after the fix, merges to `main`, and records the evidence.
7. Every feature request is saved to a new `需求审批` page. Approval changes only the request status; implementation requires a separate approved execution plan.

## Safety And Ownership Boundary

- The service must not switch the launchd checkout away from `main`. Bug branches live in `.worktrees/service-repair-<candidate-id>`.
- Branch names use `codex/service-repair-<candidate-id>-<slug>`.
- The repair automation reuses the installed `codex` executable and configured MCP inheritance. Do not introduce `MemoryConnectorClient`, provider wrappers, direct Responses API calls, or another Agent client.
- The repair automation may inspect code, tests, logs, live database state, and external dependency health. It must not expose secrets or persist raw tool output.
- No bug is merged unless the regression test demonstrably fails on the pre-fix branch state and passes after the fix.
- If a bug cannot be reproduced automatically, mark it `needs_review`; do not write an untested patch.
- If `main` advances after the repair worktree is created, update the worktree from current `main`, rerun all required tests, and merge only when conflict-free. Never force-push or rewrite history.
- Before restarting runtime code, verify claimed reply tasks, work-summary inputs, meeting jobs, and persisted external actions are resumable and idempotent.
- Feature approval never calls Codex, edits code, creates a branch, or restarts the service inside the HTTP request.

## State Model

### Service issue candidate

Extend `service_bugfix_candidates` with these states and fields instead of creating a second intake queue:

- `status`: `pending_analysis`, `repair_queued`, `repairing`, `needs_review`, `feature_recorded`, `resolved`, or `failed`.
- `problem_kind`: empty, `bug`, `feature`, or `recovered_incident`.
- `analysis_summary`: concise explanation of the existing contract, observed behavior, and classification.
- `evidence_json`: redacted identifiers for linked attempt, task, run, error, test, document, or dependency-health evidence.
- `feature_request_id`: linked request when `problem_kind=feature`.
- `repair_run_id`: linked repair when `problem_kind=bug`.
- `resolved_at`: terminal timestamp.

### Service repair run

Create `service_repair_runs` with:

- identity: `id`, `candidate_id`, `status`;
- Git evidence: `base_sha`, `branch_name`, `worktree_path`, `test_commit_sha`, `fix_commit_sha`, `merge_commit_sha`;
- TDD evidence: `regression_test_paths_json`, `red_command`, `red_exit_code`, `green_command`, `green_exit_code`, `full_test_command`, `full_test_exit_code`;
- deployment evidence: `resumability_summary`, `restart_required`, `old_pid`, `new_pid`, `live_verification_summary`;
- failure evidence: `error`, `created_at`, `updated_at`, `completed_at`.

Repair statuses are `queued`, `reproducing`, `fixing`, `verifying`, `merging`, `restarting`, `resolved`, `needs_review`, or `failed`.

### Feature request

Create `feature_requests` with:

- `id`, `candidate_id`, `status` (`pending`, `approved`, `rejected`);
- `title`, `problem_statement`, `expected_behavior`, `acceptance_plan`, `risk_summary`;
- `source_summary`, `created_at`, `updated_at`, `reviewed_at`, `reviewer_note`.

The table must not store feedback tokens, raw feedback JSON, credentials, signed URLs, or full private messages.

## File Map

- Modify: `app/store.py`
  - Add issue fields, repair-run and feature-request tables, migrations, models, and atomic transitions.
- Replace: `app/feedback_bugfix.py`
  - Remove all text marker lists. Keep only evidence assembly and state validation for the Codex automation.
- Create: `app/service_issue_workflow.py`
  - Define pure transition validation and evidence-redaction helpers; do not invoke Codex or Git.
- Modify: `app/cli.py`
  - Add explicit commands used by the existing automation to record analysis and TDD/deployment evidence.
- Modify: `app/audit_web.py`
  - Replace the old pending-only service-fix page with service issue/repair history and add the `需求审批` page.
- Modify: `tests/test_store.py`
  - Cover migrations, idempotency, and state transitions.
- Create: `tests/test_service_issue_workflow.py`
  - Cover bug/feature/incident transition rules and redaction.
- Modify: `tests/test_cli.py`
  - Cover recording analysis, red/green evidence, merge, and restart results.
- Modify: `tests/test_audit_web.py`
  - Cover service repair history and feature approval/rejection.
- Modify: `docs/reply-worker-reliability.md`
  - Document operational ownership, TDD evidence, merge, restart, and recovery.
- Modify: `docs/architecture.md`
  - Document the issue, repair, and feature approval records.

### Task 1: Persist Issue Classification And Audit Records

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing migration and model tests**

Add tests that open a pre-change database, preserve current candidates, create one repair run and one feature request, and reject duplicate links:

```python
def test_service_issue_schema_preserves_candidates_and_links_one_outcome(tmp_path):
    store = _open_pre_service_issue_schema(tmp_path)
    candidate = store.list_service_bugfix_candidates(status="pending", limit=1)[0]

    analyzed = store.record_service_issue_analysis(
        candidate.id,
        problem_kind="bug",
        analysis_summary="Existing retry contract was not applied.",
        evidence={"attempt_id": candidate.attempt_id},
    )
    repair = store.create_service_repair_run(candidate.id, base_sha="a" * 40)

    assert analyzed.problem_kind == "bug"
    assert repair.candidate_id == candidate.id
    assert store.create_service_repair_run(candidate.id, base_sha="b" * 40).id == repair.id
    assert store.create_feature_request_from_candidate(candidate.id, **_feature_fields()) is None
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_store.py -k 'service_issue_schema'
```

Expected: FAIL because the new fields, tables, and methods do not exist.

- [ ] **Step 3: Add models, migrations, and indexes**

Add Pydantic models `ServiceRepairRun` and `FeatureRequest`. Extend `ServiceBugfixCandidate` with the state-model fields above. Add idempotent SQLite migrations and indexes on:

- `service_bugfix_candidates(status, id)`;
- `service_repair_runs(status, id)` and unique `candidate_id`;
- `feature_requests(status, id)` and unique `candidate_id`.

Existing `pending` rows migrate to `pending_analysis` without losing their title, reason, feedback comment, attempt link, or timestamps.

- [ ] **Step 4: Add atomic store transitions**

Implement:

```python
record_service_issue_analysis(candidate_id, *, problem_kind, analysis_summary, evidence)
create_service_repair_run(candidate_id, *, base_sha)
update_service_repair_run(repair_run_id, *, expected_status, status, **evidence)
create_feature_request_from_candidate(candidate_id, **request_fields)
review_feature_request(request_id, *, decision, reviewer_note)
resolve_recovered_incident(candidate_id, *, summary)
```

Use `begin immediate` for analysis, link creation, and review decisions. Reject bug-to-feature and feature-to-bug changes after a repair/request link exists.

- [ ] **Step 5: Run store tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_store.py -k 'service_issue or service_repair or feature_request'
```

Expected: PASS.

- [ ] **Step 6: Commit persistence**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: persist service issue decisions"
```

### Task 2: Remove Keyword Routing And Enforce Evidence-Based Analysis

**Files:**
- Replace: `app/feedback_bugfix.py`
- Create: `app/service_issue_workflow.py`
- Test: `tests/test_service_issue_workflow.py`

- [ ] **Step 1: Write failing tests for the classification boundary**

```python
def test_failed_existing_operation_requires_bug_analysis():
    evidence = issue_evidence(
        existing_contract="External reads retry transient failures.",
        observed_behavior="The first transient failure terminated the task.",
        execution_status="failed",
    )
    assert next_issue_step(evidence) == "analyze_bug"


def test_requested_unpromised_behavior_requires_feature_record():
    evidence = issue_evidence(
        existing_contract="",
        observed_behavior="A new approval dashboard was requested.",
        execution_status="completed",
    )
    assert next_issue_step(evidence) == "record_feature"


def test_successful_existing_retry_closes_recovered_incident():
    evidence = issue_evidence(
        existing_contract="External reads retry transient failures.",
        observed_behavior="The retry succeeded with no duplicate effect.",
        execution_status="completed",
    )
    assert next_issue_step(evidence) == "close_incident"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_service_issue_workflow.py
```

Expected: FAIL because the workflow module does not exist and the current classifier uses marker lists.

- [ ] **Step 3: Delete text-marker classification**

Remove `SERVICE_BUGFIX_REQUEST_MARKERS`, `SERVICE_BUGFIX_PROBLEM_MARKERS`, `ARBITRARY_DEVELOPMENT_MARKERS`, `NEW_FEATURE_REQUEST_MARKERS`, and `_candidate_title` from `app/feedback_bugfix.py`.

The replacement module may assemble linked task/run/attempt/dependency evidence and redact secrets. It must not classify from substrings, regular expressions, names, languages, or a static list of product terms.

- [ ] **Step 4: Implement pure transition validation**

`app/service_issue_workflow.py` validates the automation's recorded analysis:

- `bug` requires a named existing contract and an observed violation;
- `feature` requires a problem statement and expected behavior, and must not link a repair run;
- `recovered_incident` requires a successful retry/reconciliation result and no remaining failed effect;
- ambiguous or incomplete analysis remains `needs_review`.

This module does not call a model. The existing Codex automation performs repository and live-service analysis and records the conclusion through the CLI in Task 3.

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_service_issue_workflow.py tests/test_worker.py -k 'service_issue or feedback_bugfix'
```

Expected: PASS and no marker list remains in `app/feedback_bugfix.py`.

- [ ] **Step 6: Commit the boundary**

```bash
git add app/feedback_bugfix.py app/service_issue_workflow.py \
  tests/test_service_issue_workflow.py tests/test_worker.py
git commit -m "refactor: analyze service issues from evidence"
```

### Task 3: Add Traceable Automation Commands

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Add tests for:

```text
service-issue classify --id <id> --kind bug --analysis-file <path> --evidence-file <path>
service-repair start --candidate-id <id> --base-sha <sha>
service-repair record-red --id <id> --commit <sha> --tests-file <path> --command <command> --exit-code <nonzero>
service-repair record-green --id <id> --commit <sha> --command <command> --exit-code 0
service-repair record-merge --id <id> --merge-commit <sha>
service-repair record-restart --id <id> --old-pid <pid> --new-pid <pid> --verification-file <path>
feature-request create --candidate-id <id> --request-file <path>
```

Tests must assert that `record-red` rejects exit code 0, `record-green` rejects a missing red record, and `record-merge` rejects a non-green repair.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_cli.py -k 'service_issue or service_repair or feature_request'
```

Expected: FAIL because the commands do not exist.

- [ ] **Step 3: Implement thin CLI adapters**

Each command parses files, validates paths and SHA formats, calls one store method, and prints a redacted status summary. It must not run Codex, Git, tests, launchctl, or external writes itself. This keeps execution ownership in the existing automation and persistence ownership in the service.

- [ ] **Step 4: Run CLI tests**

```bash
.venv/bin/python -m pytest -q tests/test_cli.py -k 'service_issue or service_repair or feature_request'
```

Expected: PASS.

- [ ] **Step 5: Commit CLI support**

```bash
git add app/cli.py tests/test_cli.py
git commit -m "feat: record service repair evidence"
```

### Task 4: Add The Feature Request Approval Page

**Files:**
- Modify: `app/audit_web.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing page and review tests**

Add tests asserting:

- the primary navigation contains `需求审批` and its pending count;
- `/feature-requests` lists pending, approved, and rejected requests;
- each pending row shows problem, expected behavior, acceptance plan, risk, and source summary;
- POST approve changes only `status`, `reviewed_at`, and `reviewer_note`;
- POST reject changes only the same review fields;
- neither route creates a branch, repair run, reply task, Agent run, external action, or service restart;
- tokens, raw JSON, signed URLs, and private message bodies are absent.

- [ ] **Step 2: Run the tests and verify they fail**

```bash
.venv/bin/python -m pytest -q tests/test_audit_web.py -k 'feature_request'
```

Expected: FAIL because the page and routes do not exist.

- [ ] **Step 3: Implement the page and routes**

Add:

```text
GET  /feature-requests
POST /feature-requests/{request_id}/approve
POST /feature-requests/{request_id}/reject
```

Use the existing page shell, compact table style, local POST protection, and safe redirect behavior. Approval and rejection call only `review_feature_request`.

- [ ] **Step 4: Replace the pending-only service repair page**

Replace `/service-bugfix-candidates` with `/service-issues`. Show classification, current repair state, branch, red/green test evidence, merge commit, restart status, and terminal error. Do not retain an alias route; update all internal navigation and tests to the new path.

- [ ] **Step 5: Run audit UI tests**

```bash
.venv/bin/python -m pytest -q tests/test_audit_web.py -k 'service_issue or service_repair or feature_request'
```

Expected: PASS.

- [ ] **Step 6: Commit the pages**

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: add service issue and feature approval pages"
```

### Task 5: Execute One Bug Through Branch-Based TDD

**Files:**
- Runtime evidence only; the exact source and test files depend on the selected reproducible bug.

- [ ] **Step 1: Select one analyzed bug with no active repair**

The candidate must name the existing contract, observed violation, reproduction evidence, and affected module. Record `problem_kind=bug` before creating development work.

- [ ] **Step 2: Create the isolated branch and worktree**

Run with the recorded candidate ID and slug:

```bash
git fetch origin main
git worktree add -b codex/service-repair-<candidate-id>-<slug> \
  .worktrees/service-repair-<candidate-id> origin/main
```

Record the exact `origin/main` SHA as `base_sha`. The launchd checkout remains on `main`.

- [ ] **Step 3: Add only the regression test**

In the worktree, use the installed Codex runtime with a natural-language instruction containing the problem evidence and the rule: add the narrowest regression test, do not modify production code, run the test, and commit only the test.

Verify independently:

```bash
git diff <base-sha>..HEAD --name-only
.venv/bin/python -m pytest -q <changed-test-files>
```

Expected: only test files and test documentation changed; the focused test fails. Record the test commit, paths, command, and nonzero exit code through `service-repair record-red`.

- [ ] **Step 4: Implement the minimal fix**

Resume the same Codex task in the same worktree. Instruct it to fix the root cause, keep the regression test unchanged unless its expectation is proven wrong, update documentation after tests pass, and commit the fix by feature.

- [ ] **Step 5: Prove green behavior**

Run:

```bash
.venv/bin/python -m pytest -q <changed-test-files>
.venv/bin/python -m pytest -q <affected-focused-suites>
.venv/bin/python -m pytest -q
```

Expected: all commands pass. Record the fix commit and test results. If the full suite has a pre-existing baseline failure, record its prior commit evidence and require all changed/affected tests to pass; do not silently ignore a new failure.

- [ ] **Step 6: Merge only from a current base**

Fetch `origin/main`. If it differs from `base_sha`, merge current `origin/main` into the repair branch and rerun all three green commands. If Git reports a conflict, stop without attempting an automatic resolution and mark the repair `needs_review`. If any regression remains, also mark it `needs_review`.

With a current green branch:

```bash
git switch main
git merge --no-ff codex/service-repair-<candidate-id>-<slug>
git push origin main
git fetch origin main
git rev-parse HEAD origin/main
```

Expected: local `HEAD` and `origin/main` match. Record the merge commit.

- [ ] **Step 7: Verify resumability, restart, and live behavior**

Before restart, verify active task/run state and persisted external effects can resume or reconcile. Then restart `com.ceo-agent-service.main`, verify a new PID and port 8765, and run the original reproduction without duplicating an external action. Record the resumability summary, old/new PID, live verification, and final backlog.

- [ ] **Step 8: Finish the repair record**

Mark the repair and candidate `resolved` only when red, green, merge, restart, and live evidence are complete. Otherwise use `needs_review` or `failed` with the exact missing gate.

### Task 6: Record One Feature Without Implementing It

**Files:**
- Runtime evidence only.

- [ ] **Step 1: Select one analyzed feature request**

The analysis must state that no existing contract promises the requested behavior. It must include the user problem, expected behavior, acceptance plan, and risk.

- [ ] **Step 2: Create the feature request**

Use `feature-request create` and verify one pending row is linked to the candidate. A repeated command for the same candidate returns the existing request.

- [ ] **Step 3: Verify the approval page**

Open `/feature-requests` on the live audit service and verify the pending request is readable and contains no secret or raw payload.

- [ ] **Step 4: Verify no development side effect**

Confirm no branch, worktree, repair run, reply task, Agent run, commit, push, or restart was created by feature intake.

### Task 7: Document, Test, Deploy, And Verify

**Files:**
- Modify: `docs/reply-worker-reliability.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Document the three outcomes**

Document bug, feature, and recovered-incident definitions; the Bug TDD gates; feature approval semantics; branch/worktree naming; audit fields; restart preflight; and crash recovery.

- [ ] **Step 2: Run focused suites**

```bash
.venv/bin/python -m pytest -q \
  tests/test_store.py \
  tests/test_service_issue_workflow.py \
  tests/test_cli.py \
  tests/test_audit_web.py \
  tests/test_worker.py \
  -k 'service_issue or service_repair or feature_request or feedback_bugfix'
```

Expected: PASS.

- [ ] **Step 3: Run the full suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS, or only explicitly recorded pre-existing baseline failures with no new failures.

- [ ] **Step 4: Commit documentation**

```bash
git add docs/reply-worker-reliability.md docs/architecture.md
git commit -m "docs: define service issue repair workflow"
```

- [ ] **Step 5: Merge, push, and restart**

Complete the same current-base merge verification and resumability preflight defined in Task 5. Restart launchd only after tests pass, then verify the new process, audit pages, database migrations, queue recovery, and absence of new failed or stuck work.

## Acceptance Criteria

- Confirmed-fact behavior changes only the Direct Agent prompt and adds no new output schema.
- No feedback keyword list, regular expression, or language-specific classifier remains.
- Every service issue records an evidence-backed `bug`, `feature`, or `recovered_incident` conclusion.
- Every bug has a unique worktree/branch, failing regression-test evidence, passing post-fix evidence, commits, merge SHA, restart evidence, and live verification.
- A bug without a reproducible failing test is not automatically patched.
- Feature requests appear on `/feature-requests` and cannot start development before review.
- Feature approval changes only approval state; implementation remains a separate approved plan.
- No new provider, memory, MCP, or Responses API client is introduced.
- Runtime restart occurs only after resumability/idempotency checks and ends with a new healthy process.
- Issue, repair, and feature records remain traceable after process termination and restart.
