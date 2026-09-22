# OA Authorization Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a high-risk OA `needs_human` result explain exactly why a person is needed, what evidence supports it, and the one concrete action an authorization may permit.

**Architecture:** Typed Consumer and Audit results carry a structured human-decision explanation and, only for a genuine domain authorization boundary, a bounded authorization plan. The Store validates the final typed run before projecting it to the current Attempt; the API renders that same validated payload and the React card submits a selected bounded instruction into the existing same-business-object revision path. Technical failures remain History evidence and are invalid as human-decision inputs.

**Tech Stack:** Python 3.12, Pydantic v2, JSON Schema snapshots, SQLite `reply_attempts`/`agent_runs`, FastAPI-style console API, React + TypeScript + Vitest, pytest.

---

## File structure and ownership

- `app/agent_contracts.py` owns typed Consumer/Audit result models and their JSON-schema shape.
- `app/agent_wire_contracts.py` owns raw agent JSON parsing and conversion to those models.
- `app/decision_quality.py` owns projection eligibility; it must not infer eligibility from error-code strings or option-key prefixes.
- `app/consumer_agent.py` owns the exact Consumer/Audit prompt rules and generated Consumer schema snapshot.
- `app/store.py` owns projection reconciliation; it uses the final run, never writes a substitute reason into history.
- `app/audit_web.py` owns legacy HTML, the human-decision POST, and read helpers shared by the Console API.
- `app/web_api/attempts.py` maps the validated decision payload to the Console DTO.
- `frontend/src/api/console.ts` owns the Attempt Detail TypeScript shape; `frontend/src/pages/AttemptDetailPage.tsx` renders it.
- Tests remain beside their owning layer: contracts/wire in `tests/test_agent_contracts.py`, snapshot parity in `tests/test_schema_snapshots.py`, projection in `tests/test_decision_quality.py` and `tests/test_store.py`, API/POST in `tests/test_audit_web.py` and `tests/test_console_attempt_detail_api.py`, UI in `frontend/src/pages/AttemptDetailPage.test.tsx`.

Do not make an automated migration that writes a generic reason or plan into historical runs. Existing invalid human projections—including 9733's generic “re-run” result—must reconcile to `failed`; only a new same-session revision with a typed plan may reopen a decision.

### Task 1: Add the strict, explainable human-decision result contract

**Files:**

- Modify: `app/agent_contracts.py:DecisionOption through AuditAgentResult`
- Modify: `app/agent_wire_contracts.py:_WireBase through AuditAgentWireResult`
- Modify: `app/consumer_agent.py:DECISION_QUALITY_GATE_INSTRUCTIONS`, `AGENT_CAPABILITY_INSTRUCTIONS`, `AUDIT_ROLE_BOUNDARY`
- Modify: `app/schemas/consumer_agent_result.schema.json`
- Modify: `app/schemas/audit_agent_result.schema.json`
- Test: `tests/test_agent_contracts.py`
- Test: `tests/test_schema_snapshots.py`

- [ ] **Step 1: Write failing Consumer and Audit contract tests.**

  Add helpers to `tests/test_agent_contracts.py` that form a valid explicit decision:

  ```python
  def _decision_basis() -> dict[str, object]:
      return {
          "verified_facts": [{"assertion": "当前 OA 任务仍为 RUNNING。", "references": ["oa:task:103917272718"]}],
          "rule_evidence": [{"assertion": "高风险 OA 需要本条具体授权。", "references": ["skill:dingtalk-oa-approval#风险和确信度"]}],
          "quality_explanation": "材料和规则均已完整读取；授权边界不是材料缺失。",
          "no_external_action_evidence": [{"assertion": "未找到当前实例的 provider receipt。", "references": ["attempt:9733"]}],
          "conclusion": "只有本次具体 OA 动作需要授权。",
      }

  def _authorization_plan() -> dict[str, object]:
      return {
          "summary": "将当前 OA 退回至当前主管节点，并通知申请人。",
          "primary_action": _proposal()["actions"][0],
          "follow_up_actions": [],
          "will_not_do": ["不会同意或拒绝该审批。"],
          "readback": ["读取 OA 流水确认退回结果。"],
      }
  ```

  Add parametrized assertions for both `ConsumerAgentResult` and `AuditAgentResult`:

  - valid `needs_human` needs `needs_human_reason` and `decision_basis`;
  - a missing reason, missing basis, empty fact/rule/no-action evidence, or empty conclusion raises `ValidationError`;
  - `authorization_plan` is allowed only when the result has a domain authorization boundary, has one typed primary action, and has a non-empty “will not do” and readback list;
  - a generic summary such as `重新判断并执行` is rejected as a plan summary by a structural validator that requires the summary to equal the primary action description, rather than by keyword matching;
  - a non-authorization `needs_human` keeps `authorization_plan=None` and still requires reason/basis;
  - non-`needs_human` outcomes reject all three new fields.

- [ ] **Step 2: Run the new tests and verify red.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_agent_contracts.py -k 'human_decision or authorization_plan'
  ```

  Expected: FAIL because `ConsumerAgentResult` and `AuditAgentResult` have no `needs_human_reason`, `decision_basis`, or `authorization_plan` fields.

- [ ] **Step 3: Define the reusable Pydantic value objects and outcome invariants.**

  In `app/agent_contracts.py`, add these models directly after `DecisionOption`:

  ```python
  class DecisionBasis(BaseModel):
      model_config = ConfigDict(extra="forbid", strict=True)

      verified_facts: tuple[ProposalFact, ...] = Field(min_length=1)
      rule_evidence: tuple[ProposalFact, ...] = Field(min_length=1)
      quality_explanation: str = Field(min_length=1)
      no_external_action_evidence: tuple[ProposalFact, ...] = Field(min_length=1)
      conclusion: str = Field(min_length=1)

      @field_validator("verified_facts", "rule_evidence", "no_external_action_evidence", mode="before")
      @classmethod
      def accept_json_arrays(cls, value: object) -> object:
          return tuple(value) if isinstance(value, list) else value


  class AuthorizationPlan(BaseModel):
      model_config = ConfigDict(extra="forbid", strict=True)

      summary: str = Field(min_length=1)
      primary_action: ProposedAction
      follow_up_actions: tuple[ProposedAction, ...] = ()
      will_not_do: tuple[str, ...] = Field(min_length=1)
      readback: tuple[str, ...] = Field(min_length=1)

      @field_validator("follow_up_actions", "will_not_do", "readback", mode="before")
      @classmethod
      def accept_json_arrays(cls, value: object) -> object:
          return tuple(value) if isinstance(value, list) else value

      @model_validator(mode="after")
      def summary_names_primary_action(self) -> "AuthorizationPlan":
          if self.primary_action.description != self.summary:
              raise ValueError("authorization plan summary must name the primary action")
          return self
  ```

  Add nullable `needs_human_reason`, `decision_basis`, and `authorization_plan` fields to both result models. In each existing `model_validator`, require reason+basis for `NEEDS_HUMAN`; require `authorization_plan` exactly when `error.authorization_required` is true; reject it otherwise; reject all three fields on every other outcome. Preserve the existing quality-gate check for ordinary rule gaps, but let the narrowly typed authorization plan be the explicit domain-authorization branch rather than forcing `confidence` down to pass the generic gate.

  Add matching fields to `_WireBase`; add `authorization_plan` as required only to `_ConsumerNeedsHumanWire` and `_AuditNeedsHumanWire` when their `error_authorization_required` flag is true, and pass all fields through `to_result`. Keep error meaning service-owned: do not branch on `error_code` text or option-key prefixes.

- [ ] **Step 4: Update generated schemas and prompts.**

  Regenerate the two snapshots from their Pydantic models with the repository's existing schema snapshot command or a short checked-in model-schema script; do not hand-edit JSON. Update the Consumer/Audit instructions so that:

  ```text
  A needs_human result must include needs_human_reason and decision_basis.
  A domain authorization boundary additionally requires authorization_plan.
  confidence describes evidence, not whether a person has granted permission.
  A technical failure never supplies the reason, evidence, or plan.
  ```

  State that the plan's `summary` must equal `primary_action.description`, which prevents generic re-run authorization without an application-maintained keyword list.

- [ ] **Step 5: Run focused contract and snapshot tests.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_agent_contracts.py tests/test_schema_snapshots.py
  env RUFF_CACHE_DIR=/private/tmp/ceo-agent-service-ruff-cache /Users/derek/miniforge3/bin/python -m ruff check app/agent_contracts.py app/agent_wire_contracts.py app/consumer_agent.py tests/test_agent_contracts.py tests/test_schema_snapshots.py
  ```

  Expected: all selected tests pass; Ruff returns `All checks passed!`.

- [ ] **Step 6: Commit the contract boundary.**

  ```bash
  git add app/agent_contracts.py app/agent_wire_contracts.py app/consumer_agent.py app/schemas/consumer_agent_result.schema.json app/schemas/audit_agent_result.schema.json tests/test_agent_contracts.py tests/test_schema_snapshots.py
  git commit -m "feat: require explainable human decisions"
  ```

### Task 2: Make current Attempt projection reject generic or technical authorization cards

**Files:**

- Modify: `app/decision_quality.py:classify_stored_needs_human_projection`
- Modify: `app/store.py:reconcile_valid_needs_human_projections`, `reconcile_invalid_needs_human_projections`
- Modify: `app/agent_orchestrator.py:_consumer_state`, `_audit_state`, `_deferred_result`, `_failed_audit_result`
- Test: `tests/test_decision_quality.py`
- Test: `tests/test_store.py`
- Test: `tests/test_agent_orchestrator.py`

- [ ] **Step 1: Write failing stored-projection tests.**

  Add cases that persist final-result JSON and prove:

  ```python
  assert classify_stored_needs_human_projection(generic_oa_retry) is StoredNeedsHumanProjection.INVALID
  assert classify_stored_needs_human_projection(technical_failure_with_options) is StoredNeedsHumanProjection.INVALID
  assert classify_stored_needs_human_projection(explicit_authorization) is StoredNeedsHumanProjection.NEEDS_HUMAN
  ```

  Use an `explicit_authorization` object containing the exact `DecisionBasis` and `AuthorizationPlan` shape from Task 1. Its primary action target must equal the current Attempt OA identifiers in the store integration test. Add an idempotent migration test where a 9733-shaped generic authorization record is currently `needs_human`; reconciliation sets its Attempt and task to `failed`, clears `human_decision_options_json`, and leaves the final run JSON unchanged.

  In `tests/test_agent_orchestrator.py`, add a failing regression test that passes a failed Consumer or Audit run with `authorization_required=True` and verifies the terminal synthetic result is `FAILED`, contains the source error as History evidence, and has no decision options. The test must also verify that `_failed_audit_result` no longer accepts `AuditOutcome.NEEDS_HUMAN`; a model/runtime fault cannot manufacture a human card.

- [ ] **Step 2: Run the narrow tests and verify red.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_decision_quality.py tests/test_store.py tests/test_agent_orchestrator.py -k 'authorization or needs_human_projection or synthetic_audit_failure'
  ```

  Expected: FAIL because the current classifier accepts authorization through an error-code and option-key naming convention without structured reason, basis, or plan.

- [ ] **Step 3: Replace string-pattern authorization acceptance with typed validation.**

  In `classify_stored_needs_human_projection`, parse with `ConsumerAgentResult.model_validate` first. Return invalid on Pydantic validation failure. Determine authorization eligibility exclusively from the parsed `error.authorization_required` and `authorization_plan`; never inspect an error-code literal, labels, instructions, or option keys. Preserve the fixed information/risk/coverage classifier for ordinary rule gaps.

  In `app/agent_orchestrator.py`, remove the `_Deferred.authorization_required` transport and the special Consumer/Audit retry branches that turn an AgentError authorization flag into a later human decision. `_failed_audit_result` always builds `AuditOutcome.FAILED` with an empty option tuple; callers pass `FAILED` only. An authorization mapping, provider confirmation, session failure, or any other runtime error remains a normal failed/retryable orchestration result. A genuine human authorization card can originate only from a successfully parsed, typed Consumer or Audit `needs_human` result with the Task 1 plan.

  In `AutoReplyStore.reconcile_valid_needs_human_projections`, serialize options only after the typed parse succeeds. Require the plan primary action's target to contain exact values already held by the Attempt (`oa_process_instance_id` and `oa_task_id` when those fields are populated); mismatches are invalid. In invalid reconciliation, retain the original final JSON and use the existing projection-failure-code logic, clear only the current projection's decision options, and never delete agent runs.

- [ ] **Step 4: Run focused Store and decision-quality tests.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_decision_quality.py tests/test_store.py tests/test_agent_orchestrator.py -k 'authorization or needs_human_projection or synthetic_audit_failure'
  env RUFF_CACHE_DIR=/private/tmp/ceo-agent-service-ruff-cache /Users/derek/miniforge3/bin/python -m ruff check app/decision_quality.py app/store.py app/agent_orchestrator.py tests/test_decision_quality.py tests/test_store.py tests/test_agent_orchestrator.py
  ```

  Expected: all selected tests pass and no literal `authorize_`, `leave_`, or specific authorization error-code matching remains in the eligibility logic.

- [ ] **Step 5: Commit the projection boundary.**

  ```bash
  git add app/decision_quality.py app/store.py app/agent_orchestrator.py tests/test_decision_quality.py tests/test_store.py tests/test_agent_orchestrator.py
  git commit -m "fix: reject unexplained authorization projections"
  ```

### Task 3: Expose reason, basis, scope, and no-action evidence through the Attempt API and UI

**Files:**

- Modify: `app/audit_web.py:_needs_human_decision_options` and a new typed decision-detail helper beside it
- Modify: `app/web_api/attempts.py:build_attempt_detail`
- Modify: `frontend/src/api/console.ts:AttemptDetail`
- Modify: `frontend/src/pages/AttemptDetailPage.tsx`
- Test: `tests/test_console_attempt_detail_api.py`
- Test: `tests/test_audit_web.py`
- Test: `frontend/src/pages/AttemptDetailPage.test.tsx`

- [ ] **Step 1: Write failing API and UI tests.**

  In the API test, seed an explicit valid authorization result and assert an exact DTO fragment:

  ```python
  assert body["item"]["human_decision"] == {
      "reason": "高风险动作缺少本条具体授权。",
      "basis": {
          "verified_facts": [...],
          "rule_evidence": [...],
          "quality_explanation": "材料和规则均已完整读取；授权边界不是材料缺失。",
          "no_external_action_evidence": [...],
          "conclusion": "只有本次具体 OA 动作需要授权。",
      },
      "plan": {
          "summary": "退回至当前主管节点。",
          "will_not_do": ["不会同意或拒绝该审批。"],
          "readback": ["读取 OA 流水确认退回结果。"],
      },
  }
  ```

  Also assert a generic historic authorization result returns `human_decision: null` and no decision options. In the React test, supply this DTO and assert visible headings `为什么需要你判断`、`判断依据`、`本次授权范围`, the specific action, the excluded action, and the readback. Assert that neither a raw session error nor `重新处理` appears in the card.

- [ ] **Step 2: Run the focused API/UI tests and verify red.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_console_attempt_detail_api.py tests/test_audit_web.py -k 'human_decision or authorization'
  npm test -- --run frontend/src/pages/AttemptDetailPage.test.tsx
  ```

  Expected: API fails because `human_decision` is absent; UI fails because it only renders the generic option list.

- [ ] **Step 3: Build one validated DTO source and render it directly.**

  Add `_needs_human_decision_detail(attempt, agent_runs)` in `app/audit_web.py`. It must:

  1. locate only `attempt.agent_run_id`;
  2. use the Task 2 typed parser;
  3. return `None` on invalid/technical/generic results;
  4. return a JSON-safe object with `reason`, all `DecisionBasis` fields, and the plan's display-safe summary, action description, target, `will_not_do`, and `readback`.

  `build_attempt_detail` calls that helper once, returns it as `human_decision`, and derives authorization decision options only from the same valid detail. Do not reparse raw JSON separately in the API and HTML paths. Update the TypeScript `AttemptDetail` interface with an optional `human_decision` object. Replace the one-line decision card with semantic sections in this order: reason, basis, scope, options, one-time/Skill-update feedback controls. The card must render nothing when `human_decision` is null even if a stale Attempt row still says `needs_human`.

- [ ] **Step 4: Run focused API/UI tests.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_console_attempt_detail_api.py tests/test_audit_web.py -k 'human_decision or authorization'
  npm test -- --run frontend/src/pages/AttemptDetailPage.test.tsx
  ```

  Expected: selected Python and Vitest tests pass. Check the rendered DOM has no generic “授权本审批重跑” text for the 9733-shaped fixture.

- [ ] **Step 5: Commit the explainable read model.**

  ```bash
  git add app/audit_web.py app/web_api/attempts.py frontend/src/api/console.ts frontend/src/pages/AttemptDetailPage.tsx tests/test_console_attempt_detail_api.py tests/test_audit_web.py frontend/src/pages/AttemptDetailPage.test.tsx
  git commit -m "feat: explain human authorization decisions"
  ```

### Task 4: Bind the selected authorization to one same-session revision

**Files:**

- Modify: `app/audit_web.py:handle_needs_human_decision_post`
- Modify: `app/consumer_agent.py` (feedback instruction rendering only)
- Modify: `app/store.py` (existing reviewed-message reply/revision handoff only if it lacks a typed feedback field)
- Test: `tests/test_audit_web.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing submission tests.**

  Seed a valid decision with a plan and submit `instruction` equal to its displayed one-time authorization. Assert:

  ```python
  assert requeued.execution_generation != original.execution_generation
  assert requeued.conversation_id == original.conversation_id
  assert requeued.trigger_message_id == original.trigger_message_id
  assert requeued.codex_session_id == original.codex_session_id
  assert "authorization_plan" in requeued.reviewer_feedback
  ```

  Add negative tests for an instruction that does not match the stored decision option, a stale/generic plan, and an already-selected authorization. Each must return `409`, create neither a new Attempt nor a new session, and preserve the original current projection. Add a positive test for selecting the Skill update checkbox with the one-time authorization; it records its existing Skill receipt while retaining the exact authorization plan in the revision feedback.

- [ ] **Step 2: Run the submission tests and verify red.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_audit_web.py tests/test_store.py -k 'human_decision and authorization'
  ```

  Expected: FAIL because the endpoint currently accepts arbitrary text and records only an unstructured `Human decision` paragraph.

- [ ] **Step 3: Persist a canonical feedback envelope and reject scope expansion.**

  In `handle_needs_human_decision_post`, obtain the single validated decision detail before parsing user input. Accept an `option_key`, not a free-form plan rewrite, for the primary buttons. Generate a canonical feedback envelope containing source Attempt/run IDs, selected option key, `authorization_plan`, `needs_human_reason`, and `decision_basis`; serialize it into the existing reviewer-feedback path that `handle_reviewed_message_reply` already sends to the next revision. Keep the custom textarea as additional feedback only; it must not replace or expand the plan.

  In the Consumer feedback instruction, require the next revision to re-read the current OA task and execute only a proposal whose primary action has the same action identity and target as the envelope. If the OA is no longer RUNNING, target ownership changed, facts/rules changed, or the action no longer matches, it returns a new ordinary result rather than executing the old authorization. The existing task/revision helper must reuse the compatible session; do not create a new Attempt or session.

- [ ] **Step 4: Run focused handoff tests.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_audit_web.py tests/test_store.py -k 'human_decision and authorization'
  env RUFF_CACHE_DIR=/private/tmp/ceo-agent-service-ruff-cache /Users/derek/miniforge3/bin/python -m ruff check app/audit_web.py app/consumer_agent.py app/store.py tests/test_audit_web.py tests/test_store.py
  ```

  Expected: all selected tests pass; no submission can turn “one-time authorization” into an unconstrained rerun.

- [ ] **Step 5: Commit the bounded revision handoff.**

  ```bash
  git add app/audit_web.py app/consumer_agent.py app/store.py tests/test_audit_web.py tests/test_store.py
  git commit -m "fix: bind OA authorization to one revision"
  ```

### Task 5: Document, verify production reconciliation, and deploy safely

**Files:**

- Modify: `docs/architecture.md:History and decision-result contract section`
- Modify: `docs/runtime-mechanism.md:startup reconciliation and revision section`
- Modify: `CHANGELOG.md`
- Test: relevant focused suites from Tasks 1-4

- [ ] **Step 1: Write documentation assertions into the existing behavior tests.**

  Extend the 9733-shaped Store/API fixture to assert that an old generic authorization result becomes `failed` after reconciliation but retains its run and `what_happened.stopped_because` entry. This is the regression proof for the documented migration behavior.

- [ ] **Step 2: Run the focused end-to-end unit slice.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_agent_contracts.py tests/test_schema_snapshots.py tests/test_decision_quality.py tests/test_store.py tests/test_audit_web.py tests/test_console_attempt_detail_api.py
  npm test -- --run frontend/src/pages/AttemptDetailPage.test.tsx
  ```

  Expected: all selected tests pass.

- [ ] **Step 3: Update runtime documentation and changelog.**

  Document these non-negotiable rules:

  - `needs_human` has a user-facing reason and auditable basis;
  - authorization is a bounded plan, not a rerun grant;
  - technical failures stay History facts and cannot be its basis;
  - generic historical cards are reconciled to `failed` without deleting history;
  - feedback starts a same-session, new-revision verification before any external action.

- [ ] **Step 4: Run broad checks and inspect staged scope.**

  Run:

  ```bash
  /Users/derek/miniforge3/bin/python -m pytest -q tests/test_agent_contracts.py tests/test_schema_snapshots.py tests/test_decision_quality.py tests/test_store.py tests/test_audit_web.py tests/test_console_attempt_detail_api.py
  npm run lint
  npm test -- --run frontend/src/pages/AttemptDetailPage.test.tsx
  git diff --check
  git diff --cached --name-only
  ```

  Expected: all checks pass and staged files are only the files named in this plan plus the two runtime documents and changelog.

- [ ] **Step 5: Commit the documented migration behavior.**

  ```bash
  git add docs/architecture.md docs/runtime-mechanism.md CHANGELOG.md tests/test_store.py tests/test_console_attempt_detail_api.py
  git commit -m "docs: define explainable authorization decisions"
  ```

- [ ] **Step 6: Request the designated heartbeat session to deploy and read back.**

  Send it the full commit list and request exactly these checks after it verifies the shared tree imports and the queue is idle:

  ```text
  launchctl print gui/$(id -u)/com.ceo-agent-service.main
  /healthz
  GET /api/console/history/9733
  current reply_attempt 9733 projection
  queue states and Attention count
  ```

  Expected post-deploy result: 9733 is `failed` with no decision card unless a later same-session revision provides a complete reason, basis, and bounded plan. No OA action or message is sent by the migration.
