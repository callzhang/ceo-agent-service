# Agent Confirmed-Fact Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Direct Agent acknowledge facts already supplied by the user and ask only for genuinely missing or contradictory information.

**Architecture:** Keep semantic judgment inside the single Direct Agent introduced by the runtime-simplification work. Change only the natural-language Direct Agent prompt and prompt-presence regression tests; do not add a production or test-only output schema, fact extractor, keyword router, reply rewriter, or compatibility path.

**Tech Stack:** Python 3.12, native Codex CLI, existing AgentResult contract, pytest.

---

## Prerequisite And Boundary

This plan starts only after `codex/agent-runtime-simplification` is merged and its focused suites pass on the target branch. That branch already establishes these contracts:

- `app/agent_context.py` renders the original trigger, recent conversation context, raw material references, completed receipts, and manual-rerun feedback.
- `app/agent_runner.py` runs one native Codex Agent with the installed CLI/MCP configuration.
- `app/agent_result.py` and `app/worker.py` reject diagnosis-only completion when an effectful request has no completion evidence.

This plan does not create another Agent client, planner, validator, fact database, or service-side business fallback. It addresses response grounding only. Feedback-to-rerun lifecycle work is in `docs/superpowers/plans/2026-07-30-feedback-repair-closure.md`.

## Required Behavior

For every requested item, the Agent must internally distinguish:

- **Confirmed:** one uncontradicted value or completion fact is present in the trigger, context, a successful material read, or a completed receipt.
- **Missing:** the requested item has no usable evidence.
- **Contradictory:** two usable sources disagree and neither source is authoritative enough to resolve the disagreement.

The user-visible behavior is:

- all requested items confirmed: acknowledge them and continue; ask no follow-up question;
- some confirmed and some missing: acknowledge the confirmed subset and ask only for the missing subset;
- contradictory evidence: state the exact conflict and ask only for the value needed to resolve it;
- a failed live read: identify the failed source and ask only when no other evidence resolves the item.

## File Map

- Modify: `app/agent_context.py`
  - Express the confirmed/missing/contradictory decision rule in the one Direct Agent instruction block.
- Modify: `tests/test_agent_context.py`
  - Verify the natural-language prompt covers complete, partial, contradictory, and failed-read evidence.
- Modify: `docs/reply-worker-reliability.md`
  - Document the evidence-reuse acceptance rule and manual live verification.

### Task 1: Establish The Post-Simplification Baseline

**Files:**
- Verify: `app/agent_context.py`
- Verify: `app/agent_runner.py`
- Verify: `app/agent_result.py`
- Verify: `tests/test_agent_context.py`
- Verify: `tests/test_agent_runtime_worker.py`

- [ ] **Step 1: Confirm the simplification branch is present in the implementation branch**

Run:

```bash
git merge-base --is-ancestor 87b5ee6 HEAD
```

Expected: exit code 0. If the simplification branch has advanced, replace `87b5ee6` with its reviewed merge commit before running this check.

- [ ] **Step 2: Run the existing grounding and completion-evidence regressions**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_agent_context.py \
  tests/test_agent_result.py \
  tests/test_agent_runtime_worker.py \
  -k 'confirmed_fact or diagnosis_only or completion_evidence'
```

Expected: PASS. This verifies the baseline already rejects diagnosis-only completion and carries raw confirmed facts into the Agent context.

- [ ] **Step 3: Record the baseline in the plan execution notes**

Record the tested commit and pass count in the implementation task or pull-request description. Do not edit production files in this step.

### Task 2: Strengthen The Single-Agent Grounding Rule

**Files:**
- Modify: `app/agent_context.py`
- Test: `tests/test_agent_context.py`

- [ ] **Step 1: Write failing unit tests for the four evidence states**

Add these tests to `tests/test_agent_context.py`:

```python
def test_context_requires_no_question_when_all_requested_facts_are_confirmed():
    rendered = _context(
        trigger_text="请确认当前结果。",
    ).render()

    assert "When every requested item is confirmed" in rendered
    assert "ask no follow-up question" in rendered


def test_context_requires_only_the_missing_delta_for_partial_completion():
    rendered = _context().render()

    assert "acknowledge the confirmed subset" in rendered
    assert "ask only for the missing subset" in rendered


def test_context_requires_exact_conflict_explanation():
    rendered = _context().render()

    assert "identify the exact conflicting sources" in rendered
    assert "ask only for the value needed to resolve the conflict" in rendered


def test_context_does_not_treat_failed_read_as_missing_when_other_evidence_resolves_it():
    rendered = _context().render()

    assert "A failed read does not erase usable evidence from another source" in rendered
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_agent_context.py \
  -k 'all_requested_facts or partial_completion or exact_conflict or failed_read'
```

Expected: FAIL because the more precise decision rules are not yet in `_AGENT_RULES`.

- [ ] **Step 3: Replace the broad fact-reuse sentence with an explicit decision rule**

In `app/agent_context.py`, replace the current single confirmed-fact bullet with these bullets inside `_AGENT_RULES`:

```text
- Before any reply or write, classify each requested item from the original trigger, recent context, successful material reads, and completed receipts as confirmed, missing, or contradictory.
- Confirmed means one usable value or completion fact is present and no usable source contradicts it. Reuse and acknowledge confirmed facts; never ask the user to provide or decompose them again.
- When every requested item is confirmed, continue from those facts and ask no follow-up question.
- When only part is confirmed, acknowledge the confirmed subset and ask only for the missing subset.
- When usable sources disagree, identify the exact conflicting sources and ask only for the value needed to resolve the conflict.
- A failed read does not erase usable evidence from another source. Ask about that item only when the failed read leaves it genuinely unresolved.
```

Do not add domain values, names, percentages, keyword lists, regular expressions, or service-side response rewriting.

- [ ] **Step 4: Run the focused unit tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_agent_context.py
```

Expected: PASS.

- [ ] **Step 5: Commit the grounding rule**

```bash
git add app/agent_context.py tests/test_agent_context.py
git commit -m "fix: ground follow-ups in confirmed facts"
```

### Task 3: Verify Prompt Behavior Without A New Output Contract

**Files:**
- Verify: `app/agent_context.py`
- Verify: `tests/test_agent_context.py`

- [ ] **Step 1: Scan the implementation diff for structural additions**

Run:

```bash
git diff -- app/agent_context.py tests/test_agent_context.py
```

Expected: the production change is limited to natural-language prompt text. No schema, response field, parser, fact model, keyword list, regular expression, or response rewriting is added.

- [ ] **Step 2: Run the existing AgentResult contract tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_agent_context.py \
  tests/test_agent_result.py \
  tests/test_agent_runner.py
```

Expected: PASS using the existing `AgentResult` contract only.

- [ ] **Step 3: Run one isolated live conversation without adding an eval schema**

Use a new test conversation containing one confirmed result and one completed validation. Let the normal Direct Agent runtime produce its normal reply, then verify in History that it acknowledges both facts and asks no question for information already present. Do not add an eval-only schema or replay an already-sent production trigger.

- [ ] **Step 4: Record the live evidence**

Record the test conversation title, attempt ID, observed reply summary, and verification time in the implementation task or pull-request description. Do not persist message IDs, tokens, signed URLs, or raw transcripts in the repository.

### Task 4: Document And Verify The User-Facing Contract

**Files:**
- Modify: `docs/reply-worker-reliability.md`

- [ ] **Step 1: Document the three evidence states and acceptance command**

Add a section named `Confirmed fact grounding` that defines confirmed, missing, and contradictory exactly as this plan does. Include the focused unit command and state that live verification uses the normal Direct Agent output contract without an additional schema.

- [ ] **Step 2: Run focused and broad tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_agent_context.py \
  tests/test_agent_result.py \
  tests/test_agent_runner.py \
  tests/test_agent_runtime_worker.py
```

Expected: PASS.

- [ ] **Step 3: Commit the documentation**

```bash
git add docs/reply-worker-reliability.md
git commit -m "docs: define confirmed fact grounding contract"
```

- [ ] **Step 4: Restart and verify only after all tests pass**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
```

Expected: a new running process and no immediate startup failure.

- [ ] **Step 5: Verify the real regression scenario without sending duplicates**

Create one new test conversation or a manual rerun generation whose context contains a confirmed result and completed validation. Verify in History that the Agent acknowledges both facts and asks no question for already supplied detail. Do not replay an already-sent production trigger.

## Acceptance Criteria

- The production prompt has one explicit confirmed/missing/contradictory rule.
- No production or test-only output schema is added for this behavior.
- No keyword list, regular expression, fact extractor, or response rewriter is added.
- Complete, partial, contradictory, and failed-read unit cases pass.
- The real test conversation uses the normal AgentResult contract and does not repeat a request for already confirmed information.
- The service is restarted on a new process and the reply-task failed/processing backlog remains empty.
