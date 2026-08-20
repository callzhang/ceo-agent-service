# Claude CLI Disaster Failover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude CLI as an independently authenticated third runtime route after Codex OAuth and Codex API, while preserving the same local result validation, permissions, effect evidence, and no-replay guarantees.

**Architecture:** Extend the provider-neutral router produced by the Codex dual-auth plan with a Claude adapter, Claude-specific event normalization, route-scoped sessions, and live capability snapshots. Claude is enabled per task family only after its exact read/write tools and skills pass launchd-context probes.

**Tech Stack:** Python 3.11+, Pydantic, SQLite, Claude CLI structured stream JSON, pytest, FastAPI audit UI, macOS launchd.

**Depends on:** completion and production verification of `docs/superpowers/plans/2026-08-20-codex-dual-auth-failover.md`.

---

## File map

- Create `app/claude_runtime_adapter.py`: command construction, credential isolation, event normalization, sessions, and failure classification.
- Create `app/runtime_capabilities.py`: task capability requirements and snapshot eligibility.
- Extend `app/agent_runtime_config.py`, `app/agent_runtime_router.py`, `app/agent_runtime_probe.py`, and `app/store.py`: Claude route configuration, selection, probes, and sessions.
- Extend `app/agent_turn_runner.py`: normalized Claude events use existing effect accounting and reconciliation.
- Modify setup, History, quality checks, `.env.example`, README, changelog, and installation runbook.
- Add fake-CLI integration tests and opt-in live Claude tests.

### Task 1: Add Claude route configuration and strict credential isolation

**Files:**
- Modify: `app/agent_runtime_config.py`
- Create: `app/claude_runtime_adapter.py`
- Create: `tests/test_claude_runtime_adapter.py`
- Modify: `tests/test_agent_runtime_config.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing configuration and environment tests**

```python
def test_claude_route_requires_anthropic_secret():
    with pytest.raises(ValueError, match="CEO_CLAUDE_API_KEY"):
        load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,claude_api"})


def test_claude_child_receives_no_openai_or_codex_secret(adapter, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("CODEX_API_KEY", "codex-secret")
    env = adapter.build_env(route("claude_api"), api_key="anthropic-secret")
    assert env["ANTHROPIC_API_KEY"] == "anthropic-secret"
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_API_KEY" not in env
    assert "CEO_CLAUDE_API_KEY" not in env
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/pytest tests/test_agent_runtime_config.py tests/test_claude_runtime_adapter.py -q`

Expected: FAIL because `claude_api` is unsupported.

- [ ] **Step 3: Extend configuration**

Accept `claude_api`, build a `RuntimeRoute` with `RuntimeKind.CLAUDE_CLI` and `CredentialMode.SERVICE_API`, require `CEO_CLAUDE_API_KEY`, and use `CEO_CLAUDE_MODEL`. Keep the default route list unchanged.

- [ ] **Step 4: Implement the adapter shell and environment isolation**

```python
class ClaudeRuntimeAdapter:
    def __init__(self, *, workspace: Path, config: AgentRuntimeConfig,
                 claude_bin: str = "claude") -> None:
        self.workspace = workspace
        self.config = config
        self.claude_bin = claude_bin

    def build_command(self, *, route: RuntimeRoute,
                      session_id: str | None, max_turns: int) -> list[str]:
        command = [self.claude_bin, "-p", "--input-format", "text",
                   "--output-format", "stream-json",
                   "--model", route.model, "--max-turns", str(max_turns)]
        if session_id:
            command.extend(["--resume", session_id])
        return command

    def build_env(self, route: RuntimeRoute) -> dict[str, str]:
        env = os.environ.copy()
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CEO_CODEX_API_KEY",
                    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                    "CEO_CLAUDE_API_KEY"):
            env.pop(key, None)
        secret = self.config.secret_for(route.name)
        if secret is None:
            raise ValueError("claude_api credential is missing")
        env["ANTHROPIC_API_KEY"] = secret.get_secret_value()
        return env
```

Do not append the prompt to the command. Send it through the existing process
runner's stdin so business content is absent from process listings.

- [ ] **Step 5: Document disabled Claude settings and commit**

```dotenv
# CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api,claude_api
# CEO_CLAUDE_MODEL=
# CEO_CLAUDE_API_KEY=
```

Run: `.venv/bin/pytest tests/test_agent_runtime_config.py tests/test_claude_runtime_adapter.py -q`

Expected: PASS.

```bash
git add app/agent_runtime_config.py app/claude_runtime_adapter.py \
  tests/test_agent_runtime_config.py tests/test_claude_runtime_adapter.py .env.example
git commit -m "feat: configure isolated Claude CLI runtime"
```

### Task 2: Normalize Claude streams into the runtime event contract

**Files:**
- Modify: `app/claude_runtime_adapter.py`
- Modify: `app/agent_runtime_contracts.py`
- Test: `tests/test_claude_runtime_adapter.py`

- [ ] **Step 1: Capture sanitized fixtures and write failing parser tests**

Store minimal sanitized fixture dictionaries directly in the test for session start, assistant text, tool start, tool completion, tool failure, and final result. Assert normalized events use the existing `turn.started`, `item.started`, `item.completed`, `item.failed`, and `turn.completed` names.

```python
def test_effectful_tool_start_is_visible_before_completion(adapter):
    event = adapter.normalize_event(CLAUDE_TOOL_START)
    assert event["type"] == "item.started"
    assert event["item"]["metadata"]["effect"] == "effectful"
    assert event["item"]["id"] == "toolu_123"
```

- [ ] **Step 2: Verify parser tests fail**

Run: `.venv/bin/pytest tests/test_claude_runtime_adapter.py -q -k 'normalize or effectful'`

Expected: FAIL because normalization is missing.

- [ ] **Step 3: Implement strict normalization**

Map only documented and live-probed Claude event types. Pass tool names and arguments through the existing `McpToolEffectRegistry` and `NativeCliMetadataClassifier`; reject unknown write-capable tools before execution. Preserve raw event references but never persist raw credential-bearing headers or environments.

- [ ] **Step 4: Implement final-result parsing and failure classification**

Extract the final JSON payload, validate it with the caller's existing Pydantic parser, and return typed failures for Anthropic authentication, capacity/rate limit, transport, session, result, and unclassified errors. Only authentication, capacity, and bounded transport failures set `failover_permitted=True`; unclassified failures stay closed.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_claude_runtime_adapter.py tests/test_agent_runtime_contracts.py -q`

Expected: PASS.

```bash
git add app/claude_runtime_adapter.py app/agent_runtime_contracts.py tests/test_claude_runtime_adapter.py
git commit -m "feat: normalize Claude CLI runtime events"
```

### Task 3: Require per-task capability snapshots

**Files:**
- Create: `app/runtime_capabilities.py`
- Create: `tests/test_runtime_capabilities.py`
- Modify: `app/agent_runtime_probe.py`
- Modify: `tests/test_agent_runtime_probe.py`

- [ ] **Step 1: Write failing capability tests**

```python
def test_claude_is_ineligible_when_required_skill_is_unproven(registry):
    registry.put(snapshot("claude_api", {"structured_output", "dws.read"}))
    decision = registry.eligible(
        "claude_api", frozenset({"structured_output", "dws.read", "skill:dingtalk-chat"})
    )
    assert decision.eligible is False
    assert decision.missing == frozenset({"skill:dingtalk-chat"})


def test_expired_snapshot_is_not_eligible(registry):
    registry.put(expired_snapshot("claude_api", {"structured_output"}))
    assert registry.eligible("claude_api", frozenset()).reason == "snapshot_expired"
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/pytest tests/test_runtime_capabilities.py tests/test_agent_runtime_probe.py -q`

Expected: FAIL because the registry is missing.

- [ ] **Step 3: Implement task-derived requirements**

Create typed capability names from Agent spec declarations and channel gates, not message keywords. Include structured output, image input, read-only enforcement, effect event visibility, exact DWS/Lark/MCP operations, and `skill:<name>` requirements.

- [ ] **Step 4: Extend probes**

Claude probe must validate non-interactive launchd-context auth, structured completion, session ID, read-only enforcement, reviewed tool discovery, and effect-start visibility using only a dedicated test tool. Persist snapshot capabilities and expiry; never infer parity from installed config alone.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_runtime_capabilities.py tests/test_agent_runtime_probe.py -q`

Expected: PASS.

```bash
git add app/runtime_capabilities.py app/agent_runtime_probe.py \
  tests/test_runtime_capabilities.py tests/test_agent_runtime_probe.py
git commit -m "feat: gate Claude failover by proven capabilities"
```

### Task 4: Add route-specific Claude conversation sessions

**Files:**
- Modify: `app/store.py`
- Modify: `app/agent_runtime_router.py`
- Test: `tests/test_store.py`
- Test: `tests/test_agent_runtime_router.py`

- [ ] **Step 1: Write failing route-session tests**

```python
def test_claude_session_never_replaces_codex_session(store):
    store.upsert_conversation_runtime_session("cid", "codex_oauth", "codex-1")
    store.upsert_conversation_runtime_session("cid", "claude_api", "claude-1")
    assert store.get_conversation_runtime_session("cid", "codex_oauth") == "codex-1"
    assert store.get_conversation_runtime_session("cid", "claude_api") == "claude-1"


def test_first_claude_fallback_starts_fresh_session(router):
    decision = router.select_session("cid", route("claude_api"), source_route="codex_oauth")
    assert decision.session_id is None
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/pytest tests/test_store.py tests/test_agent_runtime_router.py -q -k 'claude_session'`

Expected: FAIL until Claude is recognized as a separate route.

- [ ] **Step 3: Implement route session selection**

For Claude, read only `conversation_runtime_sessions(conversation_id, 'claude_api')`. Never pass a Codex session ID to Claude. On first fallback, provide the complete current turn input and source-context package and persist the returned Claude session after strict result validation.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/pytest tests/test_store.py tests/test_agent_runtime_router.py -q -k 'runtime_session or claude_session'`

Expected: PASS.

```bash
git add app/store.py app/agent_runtime_router.py tests/test_store.py tests/test_agent_runtime_router.py
git commit -m "feat: preserve independent Claude conversation sessions"
```

### Task 5: Enable Claude for read-only Agent work

**Files:**
- Modify: `app/agent_runtime_router.py`
- Modify: `app/agent_turn_runner.py`
- Modify: read-only routed callers migrated in the prerequisite plan
- Test: `tests/test_agent_turn_store.py`
- Test: `tests/test_agent_orchestrator.py`
- Test: `tests/test_runtime_route_coverage.py`

- [ ] **Step 1: Write failing end-to-end read-only routing tests**

For `test_openai_failure_falls_back_to_claude_for_consumer`, configure the fake
Codex adapter to return typed failures for both Codex routes and the fake Claude
adapter to emit a valid Consumer result. Assert the original run ID is retained,
attempt routes are exactly `codex_oauth,codex_api,claude_api`, and a Claude
conversation session is persisted.

For `test_missing_claude_skill_defers_instead_of_running`, require
`skill:dingtalk-chat` while the snapshot omits it. Assert the result code is
`runtime_capability_missing` and the fake Claude executor has zero calls.

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/pytest tests/test_agent_turn_store.py tests/test_agent_orchestrator.py -q -k 'claude'`

Expected: FAIL because the router has no Claude execution path.

- [ ] **Step 3: Invoke the selected adapter through one interface**

Change the route loop to resolve an adapter by `RuntimeKind`; feed normalized Claude events into the same `persist_line`/effect-accounting path used by Codex. Consumer permission policy remains runner-enforced read-only regardless of Claude settings.

- [ ] **Step 4: Enable only proven read-only task families**

Start with Consumer and isolated analysis jobs whose snapshots prove every capability. Keep unsupported meeting, image, Memory-write, or channel-specific jobs on Codex or deferred. Do not add message-keyword routing.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_agent_turn_store.py tests/test_agent_orchestrator.py tests/test_runtime_route_coverage.py -q`

Expected: PASS.

```bash
git add app/agent_runtime_router.py app/agent_turn_runner.py tests/test_agent_turn_store.py \
  tests/test_agent_orchestrator.py tests/test_runtime_route_coverage.py
git commit -m "feat: fail over eligible read-only work to Claude"
```

### Task 6: Enable effect-safe Claude Audit and cross-route reconciliation reads

**Files:**
- Modify: `app/agent_turn_runner.py`
- Modify: `app/agent_orchestrator.py`
- Modify: `app/audit_agent.py`
- Test: `tests/test_agent_turn_store.py`
- Test: `tests/test_agent_orchestrator.py`

- [ ] **Step 1: Write failing Audit safety tests**

Add three tests using the existing fake executor and Store fixtures:

- `test_audit_can_select_claude_before_any_effect` makes both Codex routes fail
  before tool activity and asserts one confirmed test-target write on Claude.
- `test_claude_effect_start_prevents_any_further_route` emits a Claude
  effect-start event then disconnects; assert unknown state and no later attempt.
- `test_codex_can_reconcile_claude_unknown_read_only` makes Claude unavailable
  during reconciliation; assert Codex performs only the exact read capability
  and emits no effectful event.

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/pytest tests/test_agent_turn_store.py tests/test_agent_orchestrator.py -q -k 'claude and audit'`

Expected: FAIL.

- [ ] **Step 3: Enable Claude Audit only with effect visibility**

Require `effect_event_visibility` plus every reviewed write capability. Once Claude emits an effectful start, call the same Store method used for Codex and pin the run. A disconnect or malformed result after that point enters unknown reconciliation and never attempts another provider write.

- [ ] **Step 4: Allow cross-route read-only reconciliation**

The router may choose another provider for reconciliation only when the exact read capability is proven. Pass `recovery_phase='reconcile'`, enforce read-only tools, and retain the original operation/action identity. Do not create a new generation or repeat the original write.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_agent_turn_store.py tests/test_agent_orchestrator.py tests/test_audit_agent.py -q`

Expected: PASS.

```bash
git add app/agent_turn_runner.py app/agent_orchestrator.py app/audit_agent.py \
  tests/test_agent_turn_store.py tests/test_agent_orchestrator.py tests/test_audit_agent.py
git commit -m "feat: enforce effect-safe Claude Audit failover"
```

### Task 7: Add Claude visibility, setup, and security gates

**Files:**
- Modify: `app/setup_wizard.py`
- Modify: `app/audit_web.py`
- Modify: `app/quality_gate.py`
- Modify: `app/cli.py`
- Test: `tests/test_setup_wizard.py`
- Test: `tests/test_audit_web.py`
- Test: `tests/test_quality_gate.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing UI, setup, and leak tests**

Assert the setup UI shows only `configured/missing/rejected`, History labels Claude route/session/failure without raw stderr, and quality check detects any Anthropic secret in process metadata, SQLite, logs, or rendered HTML.

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/pytest tests/test_setup_wizard.py tests/test_audit_web.py tests/test_quality_gate.py tests/test_cli.py -q -k 'claude or anthropic'`

Expected: FAIL.

- [ ] **Step 3: Implement safe visibility**

Add Claude route state to setup, probe output, History attempts, notifications, and metrics. Display route/model/session/capabilities/failure code only. Use existing leak checks plus explicit Anthropic environment-key redaction.

- [ ] **Step 4: Add quality violations**

Flag Claude execution without a current capability snapshot, Claude Audit without effect visibility, a Codex session stored under Claude, cross-provider fallback after effect start, and any secret leak.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_setup_wizard.py tests/test_audit_web.py tests/test_quality_gate.py tests/test_cli.py -q`

Expected: PASS.

```bash
git add app/setup_wizard.py app/audit_web.py app/quality_gate.py app/cli.py \
  tests/test_setup_wizard.py tests/test_audit_web.py tests/test_quality_gate.py tests/test_cli.py
git commit -m "feat: expose and guard Claude runtime health"
```

### Task 8: Validate and stage Claude production rollout

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/agent-installation-runbook.md`
- Create: `tests/e2e/test_claude_runtime_live.py`

- [ ] **Step 1: Add opt-in live tests**

Guard with `CEO_LIVE_CLAUDE_RUNTIME_E2E=1` and implement four concrete cases:

- launchd-context probe completes with a Claude session and turn completion;
- a synthetic read-only result passes the same local schema as Codex;
- the dedicated test tool records effect start before its reversible write;
- the configured Claude secret is absent from captured streams, SQLite,
  History HTML, logs, and process command metadata.

Use only synthetic input and a dedicated reversible test target.

- [ ] **Step 2: Document capability-based enablement and rollback**

Explain why `claude -p` success is insufficient, how task capability snapshots work, how route/session evidence appears, and how removing `claude_api` rolls back without deleting attempts or sessions.

- [ ] **Step 3: Run all automated tests**

Run:

```bash
.venv/bin/pytest tests/test_claude_runtime_adapter.py tests/test_runtime_capabilities.py \
  tests/test_agent_runtime_probe.py tests/test_agent_runtime_router.py \
  tests/test_agent_turn_store.py tests/test_agent_orchestrator.py \
  tests/test_quality_gate.py -q
.venv/bin/pytest -q
```

Expected: PASS; live tests SKIP without explicit opt-in.

- [ ] **Step 4: Commit documentation and live tests**

```bash
git add README.md CHANGELOG.md docs/agent-installation-runbook.md tests/e2e/test_claude_runtime_live.py
git commit -m "docs: add Claude disaster failover runbook"
```

- [ ] **Step 5: Run Stage 4 probe-only deployment**

Configure Claude, keep it in probe-only mode, restart launchd, verify a new PID and successful structured/capability probes, then run full quality inspection. Do not promote a task family until every required capability is present.

- [ ] **Step 6: Promote read-only task families one at a time**

Force both Codex routes to return typed failures, verify one eligible read-only task completes through Claude under the same business run, and compare correctness, schema validity, latency, cost, tool evidence, and secret scans against the Codex baseline.

- [ ] **Step 7: Promote Audit only after test-target failure injection**

Prove: a failure before effect start safely changes routes and produces one write; a failure after Claude effect start produces no second write and enters read-only reconciliation; restart preserves the same decision. Record exact commands, process/attempt states, receipts, and external readback.

- [ ] **Step 8: Verify final live state**

Run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
.venv/bin/ceo-agent probe-agent-runtimes --db "$CEO_WORKER_DB" --workspace "$CEO_WORKSPACE"
.venv/bin/ceo-agent quality-check --db "$CEO_WORKER_DB" --workspace "$CEO_WORKSPACE"
```

Expected: a new process runs the committed release; all enabled routes and capabilities read back healthy; no secret appears; no unresolved failed, processing, or unknown backlog was introduced.
