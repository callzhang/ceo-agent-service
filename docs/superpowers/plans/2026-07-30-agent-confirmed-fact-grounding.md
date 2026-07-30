# Agent Confirmed-Fact Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Direct Agent acknowledge facts already supplied by the user and ask only for genuinely missing or contradictory information.

**Architecture:** Keep semantic judgment inside the single Direct Agent introduced by the runtime-simplification work. Strengthen the shared Agent instructions, preserve raw trigger/context/receipt evidence, and add unit plus live semantic regressions; do not add a service-side fact extractor, keyword router, reply rewriter, or compatibility path.

**Tech Stack:** Python 3.12, native Codex CLI, Pydantic v2, pytest, JSON fixtures.

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
  - Verify complete, partial, contradictory, and failed-read context rendering.
- Create: `tests/fixtures/agent_fact_reuse_cases.json`
  - Store generic semantic regression cases without production keyword logic.
- Create: `tests/schemas/agent_fact_reuse_eval.schema.json`
  - Define the test-only structured judgment returned by the live semantic evaluation.
- Create: `tests/test_agent_fact_reuse_eval.py`
  - Run the exact rendered production rules through native Codex in read-only evaluation mode.
- Modify: `docs/reply-worker-reliability.md`
  - Document the evidence-reuse acceptance rule and live-eval command.

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

### Task 3: Add Generic Live Semantic Regressions

**Files:**
- Create: `tests/fixtures/agent_fact_reuse_cases.json`
- Create: `tests/schemas/agent_fact_reuse_eval.schema.json`
- Create: `tests/test_agent_fact_reuse_eval.py`

- [ ] **Step 1: Add complete, partial, and contradictory fixtures**

Create `tests/fixtures/agent_fact_reuse_cases.json`:

```json
[
  {
    "id": "all_confirmed",
    "trigger": "请确认本轮交付状态。",
    "messages": [
      "本轮性能提升已经确认达到 15%。",
      "回归验证已经完成。"
    ],
    "expected_action": "acknowledge",
    "expected_question_count": 0,
    "expected_confirmed_count": 2,
    "expected_conflict_count": 0
  },
  {
    "id": "partial_completion",
    "trigger": "请确认训练和验收是否都完成。",
    "messages": [
      "训练已经完成，验收时间还没有提供。"
    ],
    "expected_action": "ask_missing",
    "expected_question_count": 1,
    "expected_confirmed_count": 1,
    "expected_conflict_count": 0
  },
  {
    "id": "contradictory_values",
    "trigger": "请确认最终提升结果。",
    "messages": [
      "第一份记录写的是 12%。",
      "后续记录写的是 15%，但没有说明是否替代前值。"
    ],
    "expected_action": "ask_conflict",
    "expected_question_count": 1,
    "expected_confirmed_count": 0,
    "expected_conflict_count": 1
  }
]
```

- [ ] **Step 2: Define the test-only evaluation schema**

Create `tests/schemas/agent_fact_reuse_eval.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["action", "confirmed", "conflicts", "questions"],
  "properties": {
    "action": {
      "type": "string",
      "enum": ["acknowledge", "ask_missing", "ask_conflict"]
    },
    "confirmed": {"type": "array", "items": {"type": "string"}},
    "conflicts": {"type": "array", "items": {"type": "string"}},
    "questions": {"type": "array", "items": {"type": "string"}}
  }
}
```

- [ ] **Step 3: Add a live evaluation that uses the production-rendered rules**

Create `tests/test_agent_fact_reuse_eval.py`. The test must:

1. build `AgentTaskContext` from each fixture;
2. call `context.render()` so the production `_AGENT_RULES` are evaluated;
3. invoke native Codex with `approval_policy="never"`, `use_approval_bypass=False`, and the test-only output schema;
4. prohibit tool use in the test developer instruction;
5. parse the last Agent JSON object;
6. assert action and list counts against the fixture.

Use this assertion body:

```python
@pytest.mark.live
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_direct_agent_reuses_confirmed_facts(case: dict) -> None:
    result = run_fact_reuse_eval(_context_from_case(case))

    assert result["action"] == case["expected_action"]
    assert len(result["questions"]) == case["expected_question_count"]
    assert len(result["confirmed"]) == case["expected_confirmed_count"]
    assert len(result["conflicts"]) == case["expected_conflict_count"]
```

The helper must call `CodexRunner.build_command` and `run_process_with_idle_timeout` from the production runtime; it must not introduce a new production client.

- [ ] **Step 4: Run the live evaluation three times**

Run:

```bash
for run in 1 2 3; do
  .venv/bin/python -m pytest -q -m live tests/test_agent_fact_reuse_eval.py || exit 1
done
```

Expected: all three runs PASS. Requiring three clean runs catches unstable prompt behavior before deployment.

- [ ] **Step 5: Commit the semantic regressions**

```bash
git add tests/fixtures/agent_fact_reuse_cases.json \
  tests/schemas/agent_fact_reuse_eval.schema.json \
  tests/test_agent_fact_reuse_eval.py
git commit -m "test: cover confirmed fact reuse semantics"
```

### Task 4: Document And Verify The User-Facing Contract

**Files:**
- Modify: `docs/reply-worker-reliability.md`

- [ ] **Step 1: Document the three evidence states and acceptance command**

Add a section named `Confirmed fact grounding` that defines confirmed, missing, and contradictory exactly as this plan does. Include the focused unit command and the three-run live-eval command.

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
- No production keyword list, regular expression, fact extractor, or response rewriter is added.
- Complete, partial, contradictory, and failed-read unit cases pass.
- The three semantic cases pass three consecutive native Codex runs.
- The real test conversation does not repeat a request for already confirmed information.
- The service is restarted on a new process and the reply-task failed/processing backlog remains empty.
