# Consumer/Audit Work Profile Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure ordinary Consumer and Audit turns both use the current configured work profile, so a safe receipt acknowledgment is not automatically treated as a complete response to substantive input.

**Architecture:** Keep `app.prompt.work_profile_instruction()` as the only profile renderer and append its current output to both authoritative role instruction builders. Include the rendered profile in the Consumer conversation-contract hash so profile edits rotate stale persistent sessions. Clarify the existing message-triage Skill semantically; do not add a classifier, template, domain route, or new lifecycle state.

**Tech Stack:** Python 3.11+, Pydantic Agent wire contracts, pytest, Markdown repository Skills, launchd local runtime, Console feedback API.

---

## File map

- Modify `app/consumer_agent.py`: read the configured work profile at prompt construction time, include it in the Consumer contract hash, and inject it into Consumer and Audit developer instructions.
- Modify `skills/ceo-message-triage/SKILL.md`: distinguish an incoming acknowledgment from a receipt-only outgoing response to substantive input at the semantic level.
- Modify `tests/test_consumer_agent.py`: prove profile injection, profile-driven contract rotation, and the attempt-8308-shaped composed Audit contract.
- Modify `tests/test_message_triage_skill.py`: prove the Skill carries the semantic completeness rule without forbidden routing or templates.
- Modify `CHANGELOG.md`: document the runtime bug fix after tests pass.

### Task 1: Add failing work-profile prompt regressions

**Files:**
- Modify: `tests/test_consumer_agent.py`
- Test: `tests/test_consumer_agent.py`

- [ ] **Step 1: Write a failing Consumer and Audit profile injection test**

Add a helper that writes a temporary profile and points `CEO_WORK_PROFILE_PATH` to it, then assert both instruction builders contain the same marker and the existing hard boundaries:

```python
def test_consumer_and_audit_instructions_include_current_work_profile(
    tmp_path, monkeypatch
):
    profile = tmp_path / "work_profile.md"
    profile.write_text(
        "# Runtime Profile\n\nPROFILE-CONTEXT-SENTINEL",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))

    consumer = consumer_developer_instructions("Verify supported facts.")
    audit = audit_developer_instructions("Verify supported facts.")

    for instructions in (consumer, audit):
        assert "PROFILE-CONTEXT-SENTINEL" in instructions
        assert "判断顺序、追问方式和回复边界" in instructions
        assert "profile 不能覆盖既有硬规则" in instructions
```

- [ ] **Step 2: Write a failing dynamic profile contract test**

Prove the Consumer conversation contract changes when the configured profile changes, which prevents a persistent session from carrying stale persona context:

```python
def test_consumer_contract_hash_changes_with_work_profile(
    tmp_path, monkeypatch
):
    profile = tmp_path / "work_profile.md"
    monkeypatch.setenv("CEO_WORK_PROFILE_PATH", str(profile))
    profile.write_text("# Profile\n\nfirst judgment", encoding="utf-8")
    first = consumer_wire_contract_hash()

    profile.write_text("# Profile\n\nsecond judgment", encoding="utf-8")
    second = consumer_wire_contract_hash()

    assert first != second
```

- [ ] **Step 3: Write a failing attempt-8308-shaped Audit contract test**

Build an `AgentTaskContext` whose trigger supplies analysis and report materials and an `AuditTurnContext` whose candidate action only confirms receipt. Combine that rendered business context with `audit_developer_instructions(...)` and assert the composed Audit input contains the substantive trigger, receipt-only candidate, current profile, and the canonical instruction to request `feedback_provided` when the candidate does not genuinely respond. This tests the model contract without adding a service-side text classifier.

- [ ] **Step 4: Run the new tests and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest \
  tests/test_consumer_agent.py::test_consumer_and_audit_instructions_include_current_work_profile \
  tests/test_consumer_agent.py::test_consumer_contract_hash_changes_with_work_profile \
  tests/test_consumer_agent.py::test_audit_contract_requires_profile_guided_response_to_substantive_input -q
```

Expected: all three fail because the current Consumer/Audit builders and contract hash omit `work_profile_instruction()`.

### Task 2: Add failing message-triage semantic regression

**Files:**
- Modify: `tests/test_message_triage_skill.py`
- Test: `tests/test_message_triage_skill.py`

- [ ] **Step 1: Write the failing Skill contract test**

```python
def test_message_triage_skill_does_not_treat_receipt_as_substantive_completion():
    text = _skill_text()

    for required in (
        "smallest response that genuinely satisfies the message",
        "incoming acknowledgment",
        "outgoing receipt confirmation",
        "current work profile",
        "substantive material",
    ):
        assert required in text

    for forbidden in (
        "financial material",
        "three questions",
        "minimum reply length",
        "re.compile",
    ):
        assert forbidden not in text
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest \
  tests/test_message_triage_skill.py::test_message_triage_skill_does_not_treat_receipt_as_substantive_completion -q
```

Expected: FAIL because the Skill does not yet distinguish receipt-only output from a completed substantive response.

### Task 3: Implement the minimal prompt and Skill fix

**Files:**
- Modify: `app/consumer_agent.py`
- Modify: `skills/ceo-message-triage/SKILL.md`
- Test: `tests/test_consumer_agent.py`
- Test: `tests/test_message_triage_skill.py`

- [ ] **Step 1: Import the existing profile renderer**

In `app/consumer_agent.py`, add:

```python
from app.prompt import work_profile_instruction
```

- [ ] **Step 2: Include the rendered profile in the Consumer contract hash**

Add one key to `consumer_wire_contract_hash()`:

```python
"work_profile_instruction": work_profile_instruction(),
```

This uses the existing normalized JSON hashing and causes profile edits to rotate old Consumer sessions.

- [ ] **Step 3: Inject the current profile into Consumer instructions**

Append the rendered profile after the existing Consumer rules and Skill protocol:

```python
return "\n\n".join(
    part
    for part in (
        instructions,
        _CONSUMER_AGENT_RULES,
        skill_protocol,
        work_profile_instruction(),
    )
    if part
)
```

- [ ] **Step 4: Inject the current profile and completeness review rule into Audit instructions**

Keep the existing Audit role boundary and append the profile plus this semantic review instruction:

```text
Use the current work profile, complete conversation context, and inspected
materials to judge whether the candidate genuinely responds in the principal's
role. A receipt acknowledgment may be an opening or interim state, but receipt
alone does not complete a response to substantive input that calls for the
principal's engagement. Return feedback_provided when the candidate must be
regenerated; do not rewrite it yourself.
```

The instruction is domain-neutral and does not inspect reply keywords in application code.

- [ ] **Step 5: Clarify the message-triage Skill**

After workflow step 3, add prose that:

```text
Choose the smallest response that genuinely satisfies the message in the
principal's role. Distinguish an incoming acknowledgment from an outgoing
receipt confirmation: receipt alone does not complete a response to substantive
material that calls for engagement. Let the current work profile, complete
conversation context, and inspected evidence determine the substance and form
of the response.
```

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest \
  tests/test_consumer_agent.py \
  tests/test_message_triage_skill.py -q
```

Expected: all tests pass, with only existing marked skips.

### Task 4: Verify related runtime behavior and document the fix

**Files:**
- Modify: `CHANGELOG.md`
- Test: `tests/test_audit_agent.py`
- Test: `tests/test_agent_context.py`
- Test: `tests/test_prompt.py`
- Test: `tests/test_worker.py`

- [ ] **Step 1: Run the related broad Agent suite**

Run:

```bash
/Users/derek/miniforge3/bin/python -m pytest \
  tests/test_consumer_agent.py \
  tests/test_audit_agent.py \
  tests/test_agent_context.py \
  tests/test_prompt.py \
  tests/test_message_triage_skill.py \
  tests/test_worker.py -q
```

Expected: exit code `0`, no failures.

- [ ] **Step 2: Run lint and diff checks**

Run:

```bash
/Users/derek/miniforge3/bin/python -m ruff check \
  app/consumer_agent.py \
  tests/test_consumer_agent.py \
  tests/test_message_triage_skill.py
git diff --check
```

Expected: both commands exit `0` without errors.

- [ ] **Step 3: Add a concise changelog entry**

Under the current unreleased/fix section, state that ordinary Consumer and Audit turns now use the configured work profile and that Audit requests revision when a safe receipt-only candidate does not engage with substantive input. Do not copy the full feedback body.

- [ ] **Step 4: Re-run focused tests after documentation**

Run the focused command from Task 3 again and expect exit code `0`.

### Task 5: Commit, deploy, and persist feedback evidence

**Files:**
- Commit only: `app/consumer_agent.py`, `skills/ceo-message-triage/SKILL.md`, `tests/test_consumer_agent.py`, `tests/test_message_triage_skill.py`, `CHANGELOG.md`, and this plan.
- Do not stage unrelated files in the main worktree.

- [ ] **Step 1: Commit the isolated implementation**

```bash
git add \
  app/consumer_agent.py \
  skills/ceo-message-triage/SKILL.md \
  tests/test_consumer_agent.py \
  tests/test_message_triage_skill.py \
  CHANGELOG.md \
  docs/superpowers/plans/2026-08-29-consumer-audit-work-profile-context.md
git diff --cached --check
git commit -m "fix: apply work profile to message review"
git rev-parse HEAD
```

Expected: one related commit and a clean isolated worktree.

- [ ] **Step 2: Integrate only the related commit into the main worktree**

From the main repository, verify status and cherry-pick the isolated commit. Confirm all pre-existing unrelated staged and unstaged paths retain their prior status. Record the resulting main-repository commit SHA and `git rev-parse HEAD`.

- [ ] **Step 3: Re-run focused verification from the main repository**

Run the Task 3 focused pytest command and Ruff/diff checks from the main repository. Record exact command, wall time, exit code, and test counts.

- [ ] **Step 4: Restart and verify the local service**

Read the current launchd PID, run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
```

Then verify a different PID using `launchctl print`, poll `http://127.0.0.1:8765/healthz`, and run the repository quality/backlog read to prove no failed or processing runtime backlog. Preserve exact receipts.

- [ ] **Step 5: Patch and read back feedback evidence**

Use only the local Console feedback API. First read batch `feedback-import:manual:8308` and item `manual:8308`. Patch the existing batch association with its Workbench task and turn if required. Patch the item with exact `attempt_id=8308`, `agent_run_id=5903`, the main implementation commit SHA, focused and broad test receipts, restart label and before/after PIDs, health response, and a concise note.

- [ ] **Step 6: Resolve and verify the batch**

Read the item and batch back. Only if all associations and evidence are complete and consistent, call `POST /api/console/feedback/batches/feedback-import%3Amanual%3A8308/resolve`. Read both endpoints again and verify the item and batch report `resolved` with the evidence intact.
