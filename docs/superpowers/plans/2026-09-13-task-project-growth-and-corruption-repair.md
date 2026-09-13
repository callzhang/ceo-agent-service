# Task Project Growth and Corruption Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop autonomous historical-file project creation and empty-default project corruption, restore traceable lost fields, and archive only deterministically historical-material-only projects.

**Architecture:** Enforce source-aware mutation policy before task-agent writes, preserve existing values when a patch contains empty schema defaults, score every active/waiting project before selecting five prompt candidates, and repair production through an idempotent manifest-driven CLI. Preserve audit records and make every production mutation recoverable from a verified SQLite backup.

**Tech Stack:** Python 3.12, Pydantic, SQLite, pytest, FastAPI, React/Vite, launchd.

---

## File map

- Modify `app/task_agent.py` and `app/task_models.py`: validate sparse project mutations and source authority.
- Modify `app/task_retrieval.py`: score the complete active/waiting project set.
- Modify `app/web_api/tasks.py`: expose unresolved missing titles as integrity failures.
- Create `app/task_project_repair.py`: plan and apply restoration/archival manifests.
- Modify `app/cli.py`: expose repair plan and apply commands.
- Modify focused tests for every behavior.
- Modify `README.md` and `CHANGELOG.md` after tests pass.

### Task 1: Preserve project metadata on updates

**Files:**
- Modify: `app/task_models.py:286-308`
- Modify: `app/task_agent.py:1010-1080,1355-1433`
- Test: `tests/test_task_agent.py`
- Test: `tests/test_task_models.py`

- [ ] **Step 1: Write a failing preserved-field test**

Create a project with non-empty title, goal, background, owner, facts, and source conversations. Apply an update decision that explicitly supplies empty defaults plus a valid memory context. Assert a repairable validation error and byte-for-byte unchanged stored fields.

```python
def test_update_project_rejects_empty_defaults_that_would_erase_metadata(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    project_id = store.create_work_project(
        title="融资 Demo 交付",
        goal="完成可复跑演示",
        background="已确认背景",
        owner_name="ET",
        facts_json='[{"description":"fact","source":"source"}]',
        source_conversations_json='[{"conversation_id":"cid"}]',
    )
    decision = TaskAgentDecision.model_validate({
        "action": "update_project",
        "project": {
            "id": project_id,
            "title": "",
            "goal": "",
            "background": "",
            "owner_name": "",
            "facts": [],
            "source_conversations": [],
            "memory_context": {"query": "q", "summary": "no new evidence"},
        },
        "memory_recall_used": True,
    })
    with pytest.raises(RepairableTaskDecisionValidationError, match="erase"):
        apply_task_agent_decision(
            store,
            summary_input_id=1,
            work_item=_work_item(),
            decision=decision,
        )
    restored = store.get_work_project(project_id)
    assert restored.title == "融资 Demo 交付"
    assert restored.goal == "完成可复跑演示"
```

- [ ] **Step 2: Run the test and verify RED**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_task_agent.py::test_update_project_rejects_empty_defaults_that_would_erase_metadata -q
```

Expected: FAIL because the current update writes empty fields.

- [ ] **Step 3: Implement validated sparse updates**

Add `_validated_project_update_fields(project, current_project)`. It derives fields from `model_fields_set`, rejects a currently non-empty protected field becoming empty, and returns only safe mutation fields. Protected fields are title, category, tags, status, priority, risk, owner identity, related people, goal, background, facts, and source conversations. Creation requires a trimmed non-empty title. Store serialization uses only validated fields.

- [ ] **Step 4: Add tests for omitted fields and valid sparse changes**

Verify an omitted field stays absent, whitespace-only create title is rejected, and updating only `current_state` preserves every other value.

- [ ] **Step 5: Run focused tests and verify GREEN**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_task_agent.py tests/test_task_models.py -q
```

- [ ] **Step 6: Commit**

```bash
git add app/task_models.py app/task_agent.py tests/test_task_agent.py tests/test_task_models.py
git commit -m "fix(tasks): preserve project metadata on sparse updates"
```

### Task 2: Enforce source-specific mutation authority

**Files:**
- Modify: `app/task_agent.py`
- Modify: `app/todo_completion.py`
- Test: `tests/test_task_agent.py`
- Test: `tests/test_todo_completion.py`

- [ ] **Step 1: Write a failing local-file creation test**

Apply a valid `create_project` decision to a `local_file` WorkItem. Assert rejection before any task-agent run, project, update, TODO, or follow-up write.

- [ ] **Step 2: Write a failing completion no-op test**

Apply a `todo_completion_check` update without a TODO/follow-up transition or meaningful projection change. Assert no project mutation and no `last_activity_at` change.

- [ ] **Step 3: Run both tests and verify RED**

```bash
/Users/derek/miniforge3/bin/python -m pytest   tests/test_task_agent.py::test_local_file_cannot_create_project   tests/test_todo_completion.py::test_no_evidence_completion_check_does_not_touch_project -q
```

- [ ] **Step 4: Implement deterministic source policy**

Call `_validate_work_item_mutation_policy(work_item, decision, current_project)` before recording a run or writing. Automatic `local_file` inputs can update a stable project but cannot create one. Completion-check inputs can change TODO/follow-up lifecycle and current-state projection fields, but cannot change title, owner, goal, background, category, tags, risk, facts, or source conversations. A no-op completion decision must be `skip`.

- [ ] **Step 5: Align task and validation-repair prompts**

Tell the model the same boundary, while keeping deterministic validation as the authority.

- [ ] **Step 6: Run focused tests and verify GREEN**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_task_agent.py tests/test_todo_completion.py -q
```

- [ ] **Step 7: Commit**

```bash
git add app/task_agent.py app/todo_completion.py tests/test_task_agent.py tests/test_todo_completion.py
git commit -m "fix(tasks): restrict autonomous project mutation sources"
```

### Task 3: Remove the 500-project blind spot

**Files:**
- Modify: `app/task_retrieval.py:106-180,216-301`
- Test: `tests/test_task_retrieval.py`

- [ ] **Step 1: Write a failing beyond-500 retrieval test**

Create the uniquely relevant project first, then 501 more recent irrelevant projects. Assert retrieval still returns the oldest relevant ID.

- [ ] **Step 2: Run and verify RED**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_task_retrieval.py::test_retrieve_project_candidates_scores_projects_beyond_old_recency_window -q
```

Expected: empty candidate list under the current 500-row cap.

- [ ] **Step 3: Score all active/waiting projects**

Use `limit=None` for the internal project read while preserving the final prompt-facing `limit`. Apply the same correction to task detail retrieval.

- [ ] **Step 4: Run retrieval tests and verify GREEN**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_task_retrieval.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/task_retrieval.py tests/test_task_retrieval.py
git commit -m "fix(tasks): search all active projects before merging"
```

### Task 4: Surface unresolved title corruption

**Files:**
- Modify: `app/web_api/tasks.py:532`
- Test: `tests/test_web_api_task_sort.py`
- Test: `tests/test_web_api_contracts.py`

- [ ] **Step 1: Change the title-fallback test first**

Expect `标题缺失（Project <id>）` and a machine-readable `missing_title` integrity issue.

- [ ] **Step 2: Run and verify RED**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_web_api_task_sort.py::test_task_list_sorts_before_pagination -q
```

- [ ] **Step 3: Implement the explicit integrity label**

Add `integrity_issues` to the task summary response. Titled projects keep an empty issue list.

- [ ] **Step 4: Run API tests and verify GREEN**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_web_api_task_sort.py tests/test_web_api_contracts.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/web_api/tasks.py tests/test_web_api_task_sort.py tests/test_web_api_contracts.py
git commit -m "fix(console): surface missing task project titles"
```

### Task 5: Build the manifest-driven repair tool

**Files:**
- Create: `app/task_project_repair.py`
- Modify: `app/cli.py`
- Create: `tests/test_task_project_repair.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing restoration tests**

Use current and historical fixture databases. Assert the planner fills only current blanks, selects the latest traceable non-empty task-agent value before an older snapshot, records evidence, and leaves absent or ambiguous evidence unresolved.

- [ ] **Step 2: Write failing idempotency test**

Apply a manifest twice. The first run changes expected fields and creates one repair update per project; the second changes zero fields and creates no duplicate update.

- [ ] **Step 3: Write failing archival eligibility tests**

Cover every exclusion: TODO, DingTalk link, follow-up, non-local update, explicit user source, source current at ingestion, and later current commitment. Only the fully historical local-file-only fixture is eligible.

- [ ] **Step 4: Run and verify RED**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_task_project_repair.py -q
```

Expected: import failure because the module does not exist.

- [ ] **Step 5: Implement typed manifest planning**

Create immutable `FieldRestoration` and `ArchiveDecision` records plus `build_repair_manifest`, `write_manifest`, and `apply_manifest`. Apply under `BEGIN IMMEDIATE`, verify each expected old value, write changes and auditable `work_updates`, and abort atomically on mismatch.

- [ ] **Step 6: Add CLI plan/apply commands**

```bash
python -m app.cli repair-task-projects-plan   --db /absolute/current.sqlite3   --historical-db /absolute/historical.sqlite3   --manifest /absolute/manifest.json

python -m app.cli repair-task-projects-apply   --db /absolute/current.sqlite3   --manifest /absolute/manifest.json   --archive-limit 10
```

The plan command is read-only. Apply rejects a database fingerprint mismatch and reports changed, skipped, unresolved, and archived counts.

- [ ] **Step 7: Run repair and CLI tests and verify GREEN**

```bash
/Users/derek/miniforge3/bin/python -m pytest tests/test_task_project_repair.py tests/test_cli.py -q
```

- [ ] **Step 8: Commit**

```bash
git add app/task_project_repair.py app/cli.py tests/test_task_project_repair.py tests/test_cli.py
git commit -m "feat(tasks): add auditable project repair workflow"
```

### Task 6: Documentation and complete local verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Document project authority and recovery**

Document local-file update-only behavior, required project titles/current execution signals, sparse updates, and backup/manifest-gated repair.

- [ ] **Step 2: Add a dated CHANGELOG entry**

Describe prevention, full-project matching, integrity labels, and the recoverable repair workflow without claiming production execution.

- [ ] **Step 3: Run complete verification**

```bash
/Users/derek/miniforge3/bin/python -m pytest -q
npm test -- --run
npm run build
git diff --check
```

Read every exit code and failure count. Fix task-owned failures and classify unrelated baseline failures exactly.

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs(tasks): document project mutation and recovery rules"
```

### Task 7: Integrate, repair production, and verify live behavior

**Files and state:**
- Runtime DB: `/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`
- Backup directory: `/Users/derek/Library/Application Support/ceo-agent-service/backups/`
- Evidence directory: `/Users/derek/Library/Application Support/ceo-agent-service/repairs/`

- [ ] **Step 1: Integrate task-owned commits**

Inspect the branch diff and preserve the main worktree's existing user-owned files. Integrate only task-owned commits.

- [ ] **Step 2: Verify resumability before restart**

Query reply tasks, work-summary inputs, meeting jobs, leases, external receipts, and reconciliation state. Stop if an unknown external effect has no safe recovery path.

- [ ] **Step 3: Create and verify an online SQLite backup**

Use an explicit timestamped filename, run `PRAGMA integrity_check`, compute SHA-256, and record row counts.

- [ ] **Step 4: Generate and inspect the dry-run manifest**

Abort if any current non-empty field would change. Review field sources, unresolved cases, archive candidates, and exclusions.

- [ ] **Step 5: Apply and verify ten archive candidates**

Apply safe restorations and at most ten archives. Verify database counts, exact detail APIs, and unchanged TODO/follow-up/link state.

- [ ] **Step 6: Apply remaining eligible archives and prove idempotency**

Apply the remaining reviewed manifest, rerun it, and require zero additional mutations.

- [ ] **Step 7: Restart `com.ceo-agent-service.main`**

Verify new supervisor, service, audit-web, and email-worker processes, recovered queues, external-action reconciliation, and absence of new failed/stuck work.

- [ ] **Step 8: Verify APIs and headless Tasks UI**

Read `/api/console/tasks`, project 401, restored samples, and archived samples. Confirm real titles, expected active count, working filters, no normal-looking `Project <id>` fallback, and accessible detail pages.

- [ ] **Step 9: Run safe recurrence probes on a temporary DB**

Verify empty-default updates and historical local-file create attempts are rejected without row or field changes.

- [ ] **Step 10: Record final evidence**

Write a non-sensitive report under `docs/operations/` with backup/manifest basenames, before/after counts, commands, API/UI checks, and rollback procedure. Commit only the report.

