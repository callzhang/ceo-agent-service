# Autonomous needs-human boundary and reply risk controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (recommended for inline execution) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bounded external proposals carry explicit reply-level risk controls and make Audit reject a marked proposal when the exact outbound message body does not preserve those controls, while keeping technical runtime failures separate from `needs_human`.

**Architecture:** Extend the existing strict `ProposedAction` contract with an optional `external_boundary` object. Consumer instructions require the object for meaningful autonomous external boundaries. A pure validator extracts the actual DingTalk message text from the reviewed argv and checks that all four exact control statements are present. Audit runs this validator before claiming an external write; missing controls produce `revision_required` with no side effect. Existing proposals default to no boundary so historical fixtures and ordinary messages remain compatible.

**Tech Stack:** Python 3, Pydantic v2, pytest, existing `native_cli_metadata.dingtalk_message_text`, existing Consumer/Audit wire contracts and SQLite-backed run persistence.

---

### Task 1: Add the typed external-boundary contract

**Files:**
- Modify: `app/agent_contracts.py:90-125`
- Test: `tests/test_agent_contracts.py`

- [ ] **Step 1: Write the failing contract tests**

Add tests that construct a `ProposedAction` with an `external_boundary` object
containing non-empty `allowed_now`, `concrete_risk`, `do_not`, and
`decision_boundary` strings. Assert the four strings round-trip through
`model_dump(mode="json")`, and assert an incomplete object raises Pydantic
validation.

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
pytest -q tests/test_agent_contracts.py -k external_boundary
```

Expected: FAIL because `ProposedAction` currently rejects the new field and no
boundary model exists.

- [ ] **Step 3: Implement the minimal typed model**

In `app/agent_contracts.py`, add a strict `ExternalBoundary` model with four
non-empty string fields: `allowed_now`, `concrete_risk`, `do_not`, and
`decision_boundary`. Add `external_boundary: ExternalBoundary | None = None` to
`ProposedAction`. Keep `extra="forbid"` and do not add defaults to the four
fields.

- [ ] **Step 4: Run the focused tests and verify they pass**

```bash
pytest -q tests/test_agent_contracts.py -k external_boundary
```

Expected: PASS.

- [ ] **Step 5: Commit the contract change**

```bash
git add app/agent_contracts.py tests/test_agent_contracts.py
git commit -m "feat: add typed external reply boundary"
```

### Task 2: Validate the exact outbound message body

**Files:**
- Create: `app/reply_risk_controls.py`
- Test: `tests/test_reply_risk_controls.py`

- [ ] **Step 1: Write failing pure-function tests**

Create tests for `missing_external_boundary_controls(message_body, boundary)`:

```python
boundary = ExternalBoundary(
    allowed_now="询问字段和价格",
    concrete_risk="对方可能误解为采购意向",
    do_not="不要报价承诺或购买",
    decision_boundary="预算和采购仍由 Derek 决定",
)
assert missing_external_boundary_controls(
    "询问字段和价格；对方可能误解为采购意向；不要报价承诺或购买；预算和采购仍由 Derek 决定",
    boundary,
) == ()
assert missing_external_boundary_controls("询问字段和价格；对方可能误解为采购意向；不要报价承诺或购买", boundary) == ("decision_boundary",)
```

Also test that `dingtalk_message_text` extraction from a reviewed send argv is
used by the validator, and that a non-message argv returns a stable
`message_body_missing` diagnostic when validation is requested.

- [ ] **Step 2: Run the tests and verify failure**

```bash
pytest -q tests/test_reply_risk_controls.py
```

Expected: FAIL because the module and function do not exist.

- [ ] **Step 3: Implement the pure validator**

Implement `missing_external_boundary_controls(message_body, boundary) ->
tuple[str, ...]`. Compare the four supplied strings literally after trimming
whitespace and return deterministic field names in model order. Do not use a
keyword dictionary, fuzzy matching, or a generic risk disclaimer. Reuse
`native_cli_metadata.dingtalk_message_text` for argv extraction rather than
duplicating CLI parsing.

- [ ] **Step 4: Run the tests and verify they pass**

```bash
pytest -q tests/test_reply_risk_controls.py
```

Expected: PASS.

- [ ] **Step 5: Commit the validator**

```bash
git add app/reply_risk_controls.py tests/test_reply_risk_controls.py
git commit -m "feat: validate exact outbound risk controls"
```

### Task 3: Make Consumer and Audit prompts use the typed boundary

**Files:**
- Modify: `app/consumer_agent.py:250-315`
- Test: `tests/test_consumer_agent.py`
- Test: `tests/test_audit_agent.py`

- [ ] **Step 1: Add prompt contract assertions**

Assert that Consumer instructions name `external_boundary` and require the
four exact fields whenever an autonomous external action has a meaningful
boundary. Assert that Audit instructions require preserving those same fields
in the exact outbound body and returning `revision_required` if a field is
missing.

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
pytest -q tests/test_consumer_agent.py -k 'external_boundary or risk_controls' tests/test_audit_agent.py -k 'external_boundary or risk_controls'
```

Expected: FAIL because the prompt does not yet name the typed field or the
revision code.

- [ ] **Step 3: Update the prompt text without changing existing authority**

Tell Consumer to set `external_boundary` only when the action is
`autonomous_bounded_external`, and to copy each field's exact sentence into
the message body. Tell Audit to compare the exact body with the four fields and
return `revision_required` rather than execute when the comparison fails.

- [ ] **Step 4: Run the focused tests and verify they pass**

```bash
pytest -q tests/test_consumer_agent.py -k 'external_boundary or risk_controls' tests/test_audit_agent.py -k 'external_boundary or risk_controls'
```

Expected: PASS.

### Task 4: Fail closed in Audit before any external effect

**Files:**
- Modify: `app/audit_agent.py:70-105, 500-570`
- Test: `tests/test_audit_agent.py`

- [ ] **Step 1: Write the failing Audit integration tests**

Add one proposal fixture with `external_boundary` and a DWS send argv whose
`--text` contains all four exact strings; assert the normal scripted Audit
execution completes. Add a second fixture with one missing string; assert the
Audit result is `revision_required`, `side_effect_state == "none"`, no
effectful tool event exists, and feedback contains `external_boundary` plus the
missing field name.

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
pytest -q tests/test_audit_agent.py -k external_boundary
```

Expected: FAIL because Audit currently executes the candidate without a body
comparison.

- [ ] **Step 3: Add the pre-effect validation**

After the existing recipient and operation-contract checks, inspect each action
with a non-null `external_boundary`. Extract its reviewed message text with
`native_command_argv` and `dingtalk_message_text`, call the pure validator, and
return the existing `revision_required` result shape when any field is absent.
Do this before the write turn can start, or terminalize a claimed run with
`side_effect_state="none"` and no effect event. Do not apply the check to
actions whose boundary is `None`.

- [ ] **Step 4: Run the focused tests and verify they pass**

```bash
pytest -q tests/test_audit_agent.py -k external_boundary
```

Expected: PASS.

- [ ] **Step 5: Commit the Audit gate**

```bash
git add app/audit_agent.py tests/test_audit_agent.py app/reply_risk_controls.py app/agent_contracts.py
git commit -m "feat: fail closed when reply risk controls are missing"
```

### Task 5: Regression, runtime verification, and release handoff

**Files:**
- Modify: `CHANGELOG.md`
- Test: existing focused suites and full regression suite

- [ ] **Step 1: Run the focused contract and orchestration suites**

```bash
pytest -q tests/test_agent_contracts.py tests/test_reply_risk_controls.py tests/test_consumer_agent.py tests/test_audit_agent.py tests/test_agent_orchestrator.py
```

Expected: PASS with no new failures. Existing proposals without an explicit
boundary must continue to behave exactly as before.

- [ ] **Step 2: Add a changelog entry**

Record that only explicitly bounded autonomous external actions require the
four exact reply controls, and that missing controls produce an Audit revision
instead of a send.

- [ ] **Step 3: Run the full relevant regression suite**

```bash
pytest -q
```

Expected: PASS, or any pre-existing unrelated failures are recorded with their
exact test names and outputs before release.

- [ ] **Step 4: Commit documentation and test updates**

```bash
git add CHANGELOG.md
git commit -m "docs: document autonomous reply safety gate"
```

- [ ] **Step 5: Restart and verify the production service**

After runtime commits, run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
curl -fsS http://127.0.0.1:8765/attempts/7166 >/dev/null
```

Then read the live SQLite queue and verify the new process, the route probe,
and that no new failed or stuck task was introduced. Do not report completion
until the readback is complete.

## Self-review

- Typed boundary, exact-body validator, prompt contract, Audit pre-effect gate,
  tests, and operational verification cover the approved design's required
  behavior.
- No task uses `TODO`, `TBD`, or an unspecified fallback.
- Existing proposals remain compatible because `external_boundary` defaults to
  `None`; only an explicitly marked bounded action activates the strict body
  check.
- Runtime recovery remains separate: this change never maps a route failure to
  `needs_human`.

