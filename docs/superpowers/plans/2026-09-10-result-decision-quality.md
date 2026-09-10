# Result Decision Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Consumer/Audit result distinguish missing facts, low decision confidence, and missing Skill coverage so only the approved cases become `needs_human`.

**Architecture:** Keep `ask_back` as an ordinary proposal rather than a new persisted status. Add a pure decision-quality classifier with information completeness as the first branch, then enforce `(risk == high and confidence < 0.5) or rule_coverage < 0.5` for `needs_human`. Wire contracts remain strict for new runtime results, while stored historical results receive an explicit compatibility normalization only when they lack the new fields.

**Tech Stack:** Python 3, Pydantic v2, SQLite current-projection queries, pytest, JSON Schema snapshots, launchd-managed local service.

---

## File map

- Create `app/decision_quality.py`: pure thresholds, route enum, and classification helper; no database or provider access.
- Create `tests/test_decision_quality.py`: red/green unit tests for precedence and boundary values.
- Modify `app/agent_contracts.py`: add `rule_coverage` and `information_completeness`, expose the common thresholds, and validate `needs_human` options against the classifier.
- Modify `app/agent_wire_contracts.py`: require both new numeric fields on every Consumer/Audit wire branch and pass them into typed results.
- Modify `app/schemas/consumer_agent_result.schema.json` and `app/schemas/audit_agent_result.schema.json`: regenerate from the typed models and keep the schema snapshot exact.
- Modify `app/consumer_agent.py` and `app/audit_agent.py`: define the four fields, the precedence rule, and the `ask_back` proposal contract in both prompts.
- Modify `app/agent_turn_runner.py`: include all four fields in the durable runtime envelope projection and validate them on decode.
- Modify `app/agent_orchestrator.py` and `app/worker.py`: give synthetic failures explicit quality values, preserve `ask_back` as proposal, and prevent invalid human escalation from reaching the task projection.
- Modify `app/quality_gate.py` and `app/audit_web.py`: inspect the current structured result when counting/reporting `needs_human`, while continuing to exclude historical business-object projections.
- Modify `tests/test_agent_contracts.py`, `tests/test_consumer_agent.py`, `tests/test_audit_agent.py`, `tests/test_agent_turn_runner.py`, `tests/test_worker.py`, `tests/test_quality_gate.py`, and `tests/test_audit_web.py`: cover the contract, prompts, projection, persistence, and current Attention behavior.
- Modify `docs/architecture.md`, `docs/runtime-mechanism.md`, and `CHANGELOG.md`: document the final precedence and migration behavior.

## Task 1: Add the pure decision-quality classifier

**Files:**
- Create: `app/decision_quality.py`
- Test: `tests/test_decision_quality.py`

- [ ] **Step 1: Write failing tests for the exact precedence.**

```python
def test_information_incompleteness_wins_over_rule_gap():
    assert classify_decision_quality(
        risk="high",
        confidence=0.1,
        rule_coverage=0.1,
        information_completeness=0.2,
    ) is DecisionQualityRoute.ASK_BACK


def test_high_risk_low_confidence_needs_human_when_facts_are_complete():
    assert classify_decision_quality(
        risk="high", confidence=0.49, rule_coverage=1.0,
        information_completeness=0.5,
    ) is DecisionQualityRoute.NEEDS_HUMAN


def test_low_rule_coverage_needs_human_even_with_high_confidence():
    assert classify_decision_quality(
        risk="low", confidence=1.0, rule_coverage=0.49,
        information_completeness=1.0,
    ) is DecisionQualityRoute.NEEDS_HUMAN


def test_boundary_values_are_not_low():
    assert classify_decision_quality(
        risk="high", confidence=0.5, rule_coverage=0.5,
        information_completeness=0.5,
    ) is DecisionQualityRoute.AUTONOMOUS
```

- [ ] **Step 2: Run the focused test and verify it fails because the classifier does not exist.**

Run: `pytest -q tests/test_decision_quality.py`  
Expected: collection/import failure naming `app.decision_quality`.

- [ ] **Step 3: Implement the minimal pure classifier.**

Use `Decimal`-independent float comparisons with a shared `0.5` threshold, validate every numeric input is between `0.0` and `1.0`, and branch in this order: `information_completeness < 0.5`, then `(risk == high and confidence < 0.5) or rule_coverage < 0.5`, then autonomous.

- [ ] **Step 4: Run the focused test and the module lint.**

Run: `pytest -q tests/test_decision_quality.py && ruff check app/decision_quality.py tests/test_decision_quality.py`  
Expected: all classifier tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit the isolated policy helper.**

```bash
git add app/decision_quality.py tests/test_decision_quality.py
git commit -m "feat: add decision quality routing policy"
```

## Task 2: Extend the strict Consumer/Audit contracts

**Files:**
- Modify: `app/agent_contracts.py`
- Modify: `app/agent_wire_contracts.py`
- Modify: `app/schemas/consumer_agent_result.schema.json`
- Modify: `app/schemas/audit_agent_result.schema.json`
- Test: `tests/test_agent_contracts.py`

- [ ] **Step 1: Add failing contract tests.**

Add tests that reject a wire payload missing `rule_coverage` or `information_completeness`, reject values below `0` or above `1`, accept the four fields on every outcome, and assert that `needs_human` is valid for either high-risk/low-confidence or low rule coverage when information is complete, but invalid when information completeness is below `0.5`.

- [ ] **Step 2: Run only the new contract tests and verify the expected failures.**

Run: `pytest -q tests/test_agent_contracts.py -k 'rule_coverage or information_completeness or needs_human'`  
Expected: failures show missing fields and the old confidence-only validator.

- [ ] **Step 3: Implement the contract changes.**

Add strict `float` fields with `ge=0.0, le=1.0` to `_WireBase`, pass them through both `to_result()` methods, and add them to both model JSON-schema `required` lists. Keep `ConsumerAgentResult` and `AuditAgentResult` programmatic defaults only for legacy stored-result hydration; every new wire result must provide all four fields. Make the model validator call `classify_decision_quality`: an `needs_human` result is valid only for the `NEEDS_HUMAN` route and still requires 2–4 unique options; an `ask_back` route must not be represented as `needs_human`.

- [ ] **Step 4: Regenerate the committed schemas from the models.**

Run this exact script from the repository root; it writes both snapshots with stable formatting and never hand-edits generated branches:

```bash
python - <<'PY'
import json
from pathlib import Path
from app.agent_contracts import AuditAgentResult, ConsumerAgentResult

for filename, model in (
    ("consumer_agent_result.schema.json", ConsumerAgentResult),
    ("audit_agent_result.schema.json", AuditAgentResult),
):
    Path("app/schemas", filename).write_text(
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
PY
```

- [ ] **Step 5: Run the contract and schema tests.**

Run: `pytest -q tests/test_agent_contracts.py`  
Expected: all contract tests pass, including exact committed-schema equality.

- [ ] **Step 6: Commit the contract boundary.**

```bash
git add app/agent_contracts.py app/agent_wire_contracts.py app/schemas/consumer_agent_result.schema.json app/schemas/audit_agent_result.schema.json tests/test_agent_contracts.py
git commit -m "feat: add structured decision quality fields"
```

## Task 3: Update Consumer/Audit prompts and result corrections

**Files:**
- Modify: `app/consumer_agent.py`
- Modify: `app/audit_agent.py`
- Test: `tests/test_consumer_agent.py`
- Test: `tests/test_audit_agent.py`

- [ ] **Step 1: Add failing prompt assertions.**

Assert that both role prompts name all four fields, use the exact precedence, state that information incompleteness produces a concrete clarification proposal, and state that technical/provider/schema/Audit failures remain `failed`.

- [ ] **Step 2: Run the prompt tests and verify they fail on the missing wording.**

Run: `pytest -q tests/test_consumer_agent.py tests/test_audit_agent.py -k 'prompt or instruction or schema'`  
Expected: the new assertions fail before prompt changes.

- [ ] **Step 3: Update both prompts and correction text.**

Consumer instructions must tell A to return a single concrete `ask_back` message when facts are incomplete, and to use `needs_human` only for the approved rule. Audit instructions must independently recompute the four fields, preserve the `ask_back` proposal path, and reject a human escalation that fails either the information-first rule or the human-option contract.

- [ ] **Step 4: Run focused prompt tests and lint.**

Run: `pytest -q tests/test_consumer_agent.py tests/test_audit_agent.py -k 'prompt or instruction or schema' && ruff check app/consumer_agent.py app/audit_agent.py`  
Expected: pass with no lint errors.

- [ ] **Step 5: Commit the prompt contract.**

```bash
git add app/consumer_agent.py app/audit_agent.py tests/test_consumer_agent.py tests/test_audit_agent.py
git commit -m "feat: teach agents decision quality routing"
```

## Task 4: Preserve the fields through runtime envelopes and orchestration

**Files:**
- Modify: `app/agent_turn_runner.py`
- Modify: `app/agent_orchestrator.py`
- Modify: `app/worker.py`
- Test: `tests/test_agent_turn_runner.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Add failing persistence tests.**

Cover runtime encode/decode round trips for all four fields, synthetic Consumer/Audit failures carrying explicit safe quality values, and a proposal with low information completeness remaining a proposal rather than being mapped to `needs_human`.

- [ ] **Step 2: Run the focused tests and verify the expected failures.**

Run: `pytest -q tests/test_agent_turn_runner.py tests/test_worker.py -k 'runtime_result or quality or needs_human or orchestration'`  
Expected: failures show dropped fields or old synthetic-result construction.

- [ ] **Step 3: Update runtime projection and orchestration constructors.**

Add `risk`, `confidence`, `rule_coverage`, and `information_completeness` to `_project_runtime_domain_result()` and its decode comparison. Set explicit `risk=low`, `confidence=1.0`, `rule_coverage=1.0`, and `information_completeness=1.0` on service-generated technical failures. Keep proposal handling unchanged so a clarification is audited and sent through the normal path.

- [ ] **Step 4: Add legacy stored-result normalization.**

When `_consumer_result()` or `_audit_result()` reads an old `final_result_json` missing the two new fields, normalize the stored object to safe historical defaults before model validation and mark it as legacy for display/recovery. Do not reinterpret a legacy `needs_human` row as a new valid human escalation; current projection repair in Task 6 decides its terminal state from the persisted evidence.

- [ ] **Step 5: Run the focused tests and full contract-adjacent suites.**

Run: `pytest -q tests/test_agent_turn_runner.py tests/test_worker.py tests/test_agent_contracts.py`  
Expected: all pass; no runtime envelope leak or status-mapping regression remains.

- [ ] **Step 6: Commit the runtime propagation changes.**

```bash
git add app/agent_turn_runner.py app/agent_orchestrator.py app/worker.py tests/test_agent_turn_runner.py tests/test_worker.py
git commit -m "feat: preserve decision quality through execution"
```

## Task 5: Implement and test current-projection quality scanning

**Files:**
- Modify: `app/quality_gate.py`
- Modify: `app/audit_web.py`
- Test: `tests/test_quality_gate.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Add failing current-projection tests.**

Add fixtures for: a current high-risk/low-confidence result, a current low-rule-coverage result, a current low-information result represented as a clarification proposal, and a historical false-positive `needs_human` row. Assert the quality gate counts only the first two and the History/Workers projections keep the historical row out.

- [ ] **Step 2: Run the tests and verify they fail against status-only counting.**

Run: `pytest -q tests/test_quality_gate.py tests/test_audit_web.py -k 'needs_human or attention or current_projection'`  
Expected: status-only counting reports the low-information or malformed rows incorrectly.

- [ ] **Step 3: Implement structured current-result inspection.**

Reuse the stable business-object/current-task SQL projection already used by the quality gate. For each current unresolved `needs_human`, load the referenced latest Consumer/Audit final result, validate the four fields, apply information-first classification, and count/report only a valid `NEEDS_HUMAN` result. Treat malformed, missing, technical, or low-information results as repair candidates rather than human attention.

- [ ] **Step 4: Add a guarded repair helper for false-positive projections.**

Expose a store/quality-gate operation that updates only an exact current `reply_attempt` and its mapped `reply_task`, requires no sent provider receipt, records the original error code, and changes the terminal state to `failed` or requeues an `ask_back` proposal. It must refuse historical rows, changed status, changed business-object mapping, or any row with an external send record.

- [ ] **Step 5: Run the quality-gate and UI suites.**

Run: `pytest -q tests/test_quality_gate.py tests/test_audit_web.py`  
Expected: current valid human decisions remain visible; false positives disappear from Attention and quality counts; historical facts remain queryable.

- [ ] **Step 6: Commit current-projection handling.**

```bash
git add app/quality_gate.py app/audit_web.py tests/test_quality_gate.py tests/test_audit_web.py
git commit -m "fix: classify current human escalation projections"
```

## Task 6: Update docs and schema evidence

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add documentation assertions or a text review checklist.**

Verify the docs state the four fields, the information-first precedence, the exact OR expression for `needs_human`, same-session clarification revision behavior, and the rule that technical failures remain `failed`.

- [ ] **Step 2: Update the documents without rewriting unrelated user changes.**

Use a targeted patch and stage only the new hunks. Keep the existing clarification that domain-specific authorization flags are not the generic authorization boundary.

- [ ] **Step 3: Run documentation checks.**

Run: `git diff --check -- docs/architecture.md docs/runtime-mechanism.md CHANGELOG.md` and a repository search for stale confidence-only instructions: `rg -n "needs_human.*confidence|confidence.*needs_human" app docs tests`.

- [ ] **Step 4: Commit documentation separately.**

```bash
git add -p docs/architecture.md docs/runtime-mechanism.md CHANGELOG.md
git commit -m "docs: document information-first human escalation"
```

## Task 7: Production migration and end-to-end verification

**Files:**
- Test/verification: `tests/test_decision_quality.py`, `tests/test_agent_contracts.py`, `tests/test_quality_gate.py`, `tests/test_audit_web.py`, `tests/test_email_worker.py`
- Runtime: production SQLite selected from launchd `CEO_WORKER_DB`; no broad database path assumptions.

- [ ] **Step 1: Run the complete focused regression set.**

Run: `pytest -q tests/test_decision_quality.py tests/test_agent_contracts.py tests/test_agent_turn_runner.py tests/test_consumer_agent.py tests/test_audit_agent.py tests/test_quality_gate.py tests/test_audit_web.py tests/test_email_worker.py`  
Expected: zero failures.

- [ ] **Step 2: Run lint and schema consistency checks.**

Run: `ruff check app/decision_quality.py app/agent_contracts.py app/agent_wire_contracts.py app/agent_turn_runner.py app/agent_orchestrator.py app/worker.py app/quality_gate.py app/audit_web.py` and `git diff --check`. Expected: no errors.

- [ ] **Step 3: Inspect the real launchd database before migration.**

Read `launchctl print gui/$(id -u)/com.ceo-agent-service.main`, resolve the actual `CEO_WORKER_DB`, list current business-object mappings, latest attempts, latest agent runs, and sent-reply receipts. Do not infer production state from `ceo_agent.db` in the repository.

- [ ] **Step 4: Repair only exact false-positive current projections.**

Use the guarded operation from Task 5. For each repair, record attempt/task IDs, original reason, structured quality values, no-send evidence, resulting terminal status, and the transaction row counts. Leave valid `needs_human` decisions unchanged.

- [ ] **Step 5: Restart and read back the service.**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
curl -fsS http://127.0.0.1:8765/healthz
```

Expected: a new PID, `state = running`, exit code 0, and `healthz` reports `{"ok":true,"status":"ok"}`.

- [ ] **Step 6: Verify current projections and one example of each route.**

Read the quality report and Workers API. Confirm: valid `needs_human` count equals only structured qualifying results; low-information results are proposals awaiting clarification; no historical false positive contributes to Attention; no new failed/processing backlog was created by migration. Save the exact IDs and counts in the final report.

- [ ] **Step 7: Commit only implementation-owned files and report unrelated worktree changes.**

Run `git status --short`, confirm no implementation-owned file is left unstaged, and list pre-existing unrelated modifications separately. Do not stage or reset them.

## Plan self-review

- **Spec coverage:** fields and thresholds are Task 1–2; prompts are Task 3; same-session clarification and runtime persistence are Task 4; current Attention and repair are Task 5; docs are Task 6; live migration and service evidence are Task 7.
- **Precedence check:** every task uses `information_completeness < 0.5` before `(risk == high and confidence < 0.5) or rule_coverage < 0.5`.
- **Status check:** the plan never adds `ask_back` as a persisted lifecycle status; it remains a proposal.
- **Failure check:** technical/provider/schema/Audit failures remain `failed` independent of numeric fields.
- **Compatibility check:** new wire results are strict; old stored JSON is normalized only for historical parsing and never promoted to a new human decision.
- **Placeholder scan:** no unresolved placeholder marker, vague file reference, or unspecified task remains.
