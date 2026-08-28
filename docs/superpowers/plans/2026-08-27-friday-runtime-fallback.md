# Friday Runtime Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Friday Runtime as a first-class CEO Agent runtime route so provider-specific APIs such as MiniMax Chat Completions are handled by the company runtime while preserving one Agent run and existing fallback behavior.

**Architecture:** Add a `friday_runtime` route backed by a small HTTP client that calls Friday's thread/message/run API and consumes the final Artifact (SSE is an optional second phase, not a new execution model). Keep provider credentials and model resolution inside Friday Runtime; CEO Agent receives a normalized structured result, records one runtime attempt, and lets the existing router continue to Audit or the next route. Do not modify Codex Responses or Claude adapters.

**Tech Stack:** Python 3.12, Pydantic, urllib/httpx as already used by the repository, SQLite-backed `AutoReplyStore`, pytest, Friday Runtime HTTP/JSON API and its existing `friday` CLI.

**Spec:** `docs/architecture.md`, `docs/runtime-mechanism.md`, `/Users/derek/Documents/Projects/friday-agent/docs/Friday CLI 调试入口与 SSE 流式交互技术设计.md` (validated against Friday Runtime `939232c`)

## Global Constraints

- Preserve one Consumer/Audit Agent run across route changes; never create a second run for fallback.
- Do not add audit, review, authorization, confirmation, safety-gate, or effect-reconciliation policy as part of this adapter.
- Do not add a `discard` action or `discarded` status; use the existing runtime failure and retry contracts.
- Provider-specific credentials remain in Friday Runtime; CEO Agent must never log or persist Friday provider tokens.
- A successful route must return a schema-validated result before entering the existing Audit path.
- Every bug fix and new behavior must have a regression test before implementation is considered complete.
- Existing unrelated worktree changes must remain untouched and unstaged.

---

### Task 1: Freeze the Friday Runtime HTTP contract

**Files:**
- Create: `docs/superpowers/specs/2026-08-27-friday-runtime-fallback.md`
- Create: `app/friday_runtime_contract.py`
- Read: `/Users/derek/Documents/Projects/friday-agent/friday-runtime/src/friday_runtime/api/main.py`
- Read: `/Users/derek/Documents/Projects/friday-agent/friday-runtime/src/friday_runtime/services/runtime_service.py`
- Read: `/Users/derek/Documents/Projects/friday-agent/friday-runtime/src/friday_runtime/services/thread_service.py`
- Test: `tests/test_friday_runtime_adapter.py`

**Interfaces:**
- Consumes: Friday endpoints and payload models discovered from the current `939232c` checkout; the canonical CLI non-interactive entry is `friday exec` and the HTTP API exposes `/v1/threads` resources.
- Produces: `FridayRuntimeContract` with exact paths, status transitions, final Artifact shape, and error mapping used by Tasks 2–6.

- [ ] **Step 1: Write the failing contract test**

```python
def test_contract_requires_thread_message_and_final_artifact():
    contract = FridayRuntimeContract.from_documented_api()
    assert contract.create_thread_path == "/v1/threads"
    assert contract.send_message_path("thread-1") == "/v1/threads/thread-1/turns"
    assert contract.final_artifact_field == "artifact"
```

- [ ] **Step 2: Run `pytest tests/test_friday_runtime_adapter.py::test_contract_requires_thread_message_and_final_artifact -v` and verify it fails because the contract is not defined.**
- [ ] **Step 3: Inspect the Friday API implementation and implement `FridayRuntimeContract.from_documented_api()` with exact request/response examples, including `friday exec`/Thread execution and synchronous/asynchronous behavior; write the spec from those verified shapes.**
- [ ] **Step 4: Run the contract test and confirm it passes against the frozen contract.**
- [ ] **Step 5: Commit only the spec and contract test: `git add docs/superpowers/specs/2026-08-27-friday-runtime-fallback.md tests/test_friday_runtime_adapter.py && git commit -m "docs: freeze Friday runtime fallback contract"`.**

### Task 2: Add typed Friday route configuration

**Files:**
- Modify: `app/agent_runtime_contracts.py`
- Modify: `app/agent_runtime_config.py`
- Modify: `tests/test_agent_runtime_config.py`
- Modify: `tests/test_agent_runtime_router.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `RuntimeRoute`, `load_runtime_config`, and the Task 1 contract.
- Produces: `friday_runtime` route with `RuntimeKind.FRIDAY_RUNTIME`, `CEO_FRIDAY_RUNTIME_BASE_URL`, and `CEO_FRIDAY_RUNTIME_MODEL`; no Friday provider token field in CEO Agent config.

- [ ] **Step 1: Add failing tests for `friday_runtime` route parsing and URL normalization.**

```python
def test_load_runtime_config_accepts_friday_runtime():
    config = load_runtime_config({
        "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,friday_runtime",
        "CEO_FRIDAY_RUNTIME_BASE_URL": "http://127.0.0.1:8080/",
        "CEO_FRIDAY_RUNTIME_MODEL": "MiniMax-M3",
    })
    route = next(item for item in config.routes if item.name == "friday_runtime")
    assert route.model == "MiniMax-M3"
    assert config.friday_runtime_base_url == "http://127.0.0.1:8080"
```

- [ ] **Step 2: Run the focused config tests and verify failure for the missing enum/config fields.**
- [ ] **Step 3: Implement the route kind and configuration fields with the existing URL validation helper.**
- [ ] **Step 4: Add router tests proving a healthy `friday_runtime` is selected after an unavailable Codex route and that capability requirements are respected.**
- [ ] **Step 5: Run `pytest tests/test_agent_runtime_config.py tests/test_agent_runtime_router.py -q` and commit the configuration feature.**

### Task 3: Implement the Friday Runtime HTTP client

**Files:**
- Create: `app/friday_runtime_adapter.py`
- Test: `tests/test_friday_runtime_adapter.py`

**Interfaces:**
- Consumes: `AgentRuntimeConfig.friday_runtime_base_url`, `RuntimeRoute`, Task 1 contract.
- Produces: `FridayRuntimeAdapter.execute(prompt, *, conversation_id, model, timeout_seconds) -> FridayExecutionResult`; `FridayRuntimeError(code, detail, retryable)` with stable codes `friday_runtime_unreachable`, `friday_runtime_auth_failed`, `friday_runtime_result_invalid`, and `friday_runtime_failed`.

- [ ] **Step 1: Write failing tests using a fake transport.**

```python
def test_execute_creates_thread_sends_message_and_returns_artifact():
    transport = FakeTransport([
        (201, {"thread_id": "friday-thread-1"}),
        (200, {"status": "completed", "artifact": {"text": "{\\"ok\\":true}"}}),
    ])
    result = FridayRuntimeAdapter(config, transport=transport).execute(
        '{"ok":true}', conversation_id="ceo-conversation", model="MiniMax-M3", timeout_seconds=10
    )
    assert result.text == '{"ok":true}'
    assert [call.path for call in transport.calls] == ["/v1/threads", "/v1/threads/friday-thread-1/turns"]
```

- [ ] **Step 2: Add tests for HTTP timeout, 401/403, malformed JSON, missing Artifact, and non-completed run status; verify each maps to the specified error code.**
- [ ] **Step 3: Implement the client with bounded timeout, JSON decoding, and secret-free exception details.**
- [ ] **Step 4: Run `pytest tests/test_friday_runtime_adapter.py -q` and confirm all adapter tests pass.**
- [ ] **Step 5: Commit `git add app/friday_runtime_adapter.py tests/test_friday_runtime_adapter.py && git commit -m "feat: add Friday runtime HTTP adapter"`.**

### Task 4: Connect Friday execution to route attempts and fallback

**Files:**
- Modify: `app/agent_runtime_production.py`
- Modify: `app/agent_runtime_router.py`
- Modify: `app/agent_turn_runner.py`
- Test: `tests/test_agent_runtime_production.py`
- Test: `tests/test_routed_codex_execution.py`

**Interfaces:**
- Consumes: `FridayRuntimeAdapter.execute`, `FridayRuntimeError`, existing `RoutedCodexExecution` route-attempt lifecycle.
- Produces: route-neutral execution dispatch that records `friday_runtime` attempts and preserves the original `agent_run_id`, `execution_generation`, proposal revision, and operation ID.

- [ ] **Step 1: Add a failing integration test where OAuth and Codex API fail, Friday returns a valid structured result, and exactly one Agent run has three ordered route attempts.**
- [ ] **Step 2: Add a failing test where Friday returns `friday_runtime_unreachable`; assert the router records the precise code and proceeds to Claude when configured.**
- [ ] **Step 3: Implement the smallest dispatch seam: inject a route executor map keyed by `RuntimeKind`, leaving Codex and Claude paths unchanged.**
- [ ] **Step 4: Reuse existing result codec/schema validation; do not add a second Audit or Consumer run.**
- [ ] **Step 5: Run `pytest tests/test_agent_runtime_production.py tests/test_routed_codex_execution.py -q` and commit the integration.**

### Task 5: Add Friday runtime health probe and capability snapshot

**Files:**
- Modify: `app/agent_runtime_probe.py`
- Modify: `app/agent_runtime_production.py`
- Test: `tests/test_agent_runtime_probe.py`
- Test: `tests/test_agent_runtime_worker.py`

**Interfaces:**
- Consumes: `FridayRuntimeAdapter`, `PROBE_VERIFIED_RUNTIME_CAPABILITIES`.
- Produces: `AgentRuntimeProbe.run(route_name="friday_runtime")` returning a healthy snapshot only after a synthetic structured result is validated; failures use the precise `friday_runtime_*` code.

- [ ] **Step 1: Write failing probe tests for healthy structured output, unreachable runtime, and invalid artifact.**
- [ ] **Step 2: Run `pytest tests/test_agent_runtime_probe.py -q` and verify the new tests fail.**
- [ ] **Step 3: Implement the Friday branch using the same synthetic prompt/schema and bounded timeout as other providers.**
- [ ] **Step 4: Add a worker refresh test proving the route becomes eligible after a successful probe and ineligible after an expired snapshot.**
- [ ] **Step 5: Run the focused probe/worker tests and commit.**

### Task 6: Add default and local integration E2E coverage

**Files:**
- Modify: `tests/e2e/test_runtime_failover_live.py`
- Create: `tests/e2e/test_friday_runtime_fallback.py`
- Modify: `docs/agent-installation-runbook.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 2–5 route, adapter, and probe interfaces.
- Produces: a default no-external-side-effect E2E and an opt-in local Friday Runtime E2E; the latter validates MiniMax through Friday without exposing a provider token to CEO Agent.

- [ ] **Step 1: Add a default test with a fake Friday HTTP server that runs OAuth failure → Friday success in one Agent run and asserts ordered attempts.**
- [ ] **Step 2: Run `pytest tests/e2e/test_friday_runtime_fallback.py -q` and verify it fails before the route integration.**
- [ ] **Step 3: Add an opt-in test guarded by `CEO_LIVE_FRIDAY_RUNTIME_E2E=1` and `FRIDAY_RUNTIME_BASE_URL`, using a synthetic prompt only.**
- [ ] **Step 4: Document the exact command, required Friday Runtime endpoint, expected MiniMax model configuration, and failure codes.**
- [ ] **Step 5: Run default E2E plus all focused runtime tests and commit.**

### Task 7: Deploy and verify without external business writes

**Files:**
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: all prior tasks and the project launchd contract.
- Produces: deployed runtime with read-back evidence for route configuration, process identity, probe snapshots, and no unresolved new failed/processing work.

- [ ] **Step 1: Run the focused suite, default E2E, Ruff, and diff checks.**
- [ ] **Step 2: Review the diff to ensure no unrelated worktree files are staged.**
- [ ] **Step 3: Commit architecture and changelog documentation with a feature-specific message.**
- [ ] **Step 4: Restart `com.ceo-agent-service.main` using `launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main`.**
- [ ] **Step 5: Verify a new PID, loaded `friday_runtime` configuration, route probe snapshot, and backlog classification using read-only queries; do not rerun unknown external writes.**
- [ ] **Step 6: Run the opt-in local Friday E2E only when the user has supplied a working Friday Runtime endpoint; report provider availability separately from code/test status.**

## Self-Review Checklist

- [ ] The plan keeps MiniMax protocol handling inside Friday Runtime rather than adding provider-specific branches to Codex CLI.
- [ ] The plan covers configuration, execution, fallback, probe, default E2E, live E2E, documentation, and deployment verification.
- [ ] No task introduces a new audit/safety policy or a second Agent run.
- [ ] All named interfaces and test commands are concrete; no placeholder steps remain.
- [ ] The Friday remote fetch failure is recorded as an environment limitation, not treated as a code regression.
