# Agent Runtime Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a dedicated Settings tab that safely configures the Codex OAuth default and optional API fallback, and make the selected fallback Base URL drive the real Codex runtime.

**Architecture:** Keep general system parameters in the System tab. Add an Agent Runtime tab that presents controlled selections for the OAuth default model and thinking effort, plus an API fallback enable switch, Base URL, fallback model, and write-only token input. Persist the values through `.env`; derive an immutable API Base URL in `AgentRuntimeConfig`, then pass it to Codex's provider settings only for the `codex_api` route.

**Tech Stack:** Python 3, FastAPI, Pydantic, pytest, HTML rendered by `app.audit_web`.

---

### Task 1: Make API Base URL part of the runtime configuration

**Files:**
- Modify: `app/agent_runtime_config.py`
- Modify: `app/codex_runtime_adapter.py`
- Test: `tests/test_agent_runtime_config.py`
- Test: `tests/test_codex_runtime_adapter.py`

- [ ] **Step 1: Write failing runtime-configuration tests**

Add tests that load `codex_api` with `CEO_CODEX_API_BASE_URL=https://gateway.example/v1/` and assert the immutable config value is `https://gateway.example/v1`; add parameterized invalid URLs for a relative URL, credentials in a URL, a query, and a fragment, each expecting `ValueError`.

- [ ] **Step 2: Run the new configuration tests and verify failure**

Run: `pytest tests/test_agent_runtime_config.py -q`

Expected: FAIL because `AgentRuntimeConfig` has no Base URL field or validation.

- [ ] **Step 3: Implement Base URL loading and validation**

Add `codex_api_base_url: str` to `AgentRuntimeConfig`. Load `CEO_CODEX_API_BASE_URL` with default `https://api.openai.com/v1`; validate with `urllib.parse.urlsplit` that the URL is absolute HTTP(S), has a hostname, and has no username, password, query, or fragment. Remove a trailing slash from the accepted value.

- [ ] **Step 4: Write a failing adapter command test**

Extend the API-route command test to deserialize the `model_providers` payload from the generated command and assert its provider `base_url` equals the configured normalized Base URL.

- [ ] **Step 5: Implement Base URL propagation**

Replace the mutable global provider-settings use in `CodexRuntimeAdapter.build_command` with an instance method that copies the static provider metadata and sets `base_url` from `self.config.codex_api_base_url` for `CredentialMode.SERVICE_API` only.

- [ ] **Step 6: Run focused runtime tests and commit**

Run: `pytest tests/test_agent_runtime_config.py tests/test_codex_runtime_adapter.py -q`

Expected: PASS.

Commit: `git add app/agent_runtime_config.py app/codex_runtime_adapter.py tests/test_agent_runtime_config.py tests/test_codex_runtime_adapter.py && git commit -m "feat: configure codex api fallback base url"`

### Task 2: Add the Agent Runtime settings tab and safe persistence

**Files:**
- Modify: `app/audit_web.py`
- Modify: `tests/test_audit_web.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing settings-page tests**

Add a render test for `render_config_page(active_tab="agent-runtime")` that asserts the tab contains `<select>` fields named `codex_model` and `codex_reasoning_effort`, API fallback fields named `codex_api_enabled`, `codex_api_base_url`, `codex_api_model`, and `codex_api_token`, and only reports `已配置` for an existing `CEO_CODEX_API_KEY`. Assert that the secret itself does not appear in the HTML. Update the System-tab test to assert it no longer displays `CEO_CODEX_MODEL` or `CEO_CODEX_MODEL_REASONING_EFFORT`.

- [ ] **Step 2: Run the new page tests and verify failure**

Run: `pytest tests/test_audit_web.py -q -k 'agent_runtime or system_config'`

Expected: FAIL because the tab and handler do not exist.

- [ ] **Step 3: Implement the dedicated form**

Add an `agent-runtime` branch to `_render_config_body` and a tab link in `_config_tabs`. Render curated model and thinking-effort options with `<select>`, an API fallback checkbox, API Base URL text input, fallback model select, and password input with no `value` attribute. Read the persisted token only to show `已配置`/`未配置`; never include it in HTML. Remove the two OAuth controls from `_system_config_rows` and `_editable_system_config_keys`.

- [ ] **Step 4: Write failing save-handler tests**

Add tests for `handle_agent_runtime_config_post`: a valid OAuth-only save writes `CEO_CODEX_MODEL`, `CEO_CODEX_MODEL_REASONING_EFFORT`, and route order `codex_oauth`; an enabled fallback writes `CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api`, normalized Base URL, fallback model, and a submitted token. A blank submitted token preserves the existing token. An enabled fallback with neither a submitted nor existing token returns HTTP 400 and leaves the file unchanged.

- [ ] **Step 5: Implement validation and save handler**

Parse the form in a dedicated `handle_agent_runtime_config_post`. Accept only application-owned model and effort choices. Require a valid Base URL and token when enabling fallback, retain a nonempty existing token when the token input is blank, and write only the configured keys through `write_env_values`. Return a 303 to `/config?tab=agent-runtime&saved=1` after success, or a 400 page with the validation message.

- [ ] **Step 6: Register the HTTP route and document the variable**

Register `POST /config/agent-runtime` in `create_app`. Add a commented `CEO_CODEX_API_BASE_URL=https://api.openai.com/v1` entry to `.env.example`, with no real key or token example.

- [ ] **Step 7: Run focused settings tests and commit**

Run: `pytest tests/test_audit_web.py -q -k 'agent_runtime or system_config'`

Expected: PASS.

Commit: `git add app/audit_web.py tests/test_audit_web.py .env.example && git commit -m "feat: add agent runtime settings"`

### Task 3: Complete verification and deploy

**Files:**
- Verify only: repository worktree, service status, SQLite queue state, running settings endpoint

- [ ] **Step 1: Run the targeted feature suite**

Run: `pytest tests/test_config.py tests/test_agent_runtime_config.py tests/test_codex_runtime_adapter.py tests/test_audit_web.py -q`

Expected: PASS.

- [ ] **Step 2: Run the complete test suite**

Run: `pytest -q`

Expected: PASS except intentionally skipped tests; record the exact result.

- [ ] **Step 3: Review and commit documentation/plan state**

Review `git diff --check`, `git status --short`, and `git log --oneline -3`. Commit any remaining task-owned documentation change before deployment; do not touch unrelated work.

- [ ] **Step 4: Deploy the committed service**

Run: `launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main`

Then run: `launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'`

Expected: running service with a new process identifier.

- [ ] **Step 5: Verify deployed behavior with readback**

Fetch `/settings?tab=config&config_tab=agent-runtime`; confirm the Agent Runtime tab/form is present, no API token is reflected, and `CEO_CODEX_API_BASE_URL` is read by the active process configuration. Query the SQLite queue for `failed` or `processing` items and distinguish pre-existing historical records from newly created ones.

- [ ] **Step 6: Push and report**

Run: `git push origin main` and `git status --short --branch`.

Report the deployed commit, test evidence, service process readback, safe token behavior, the settings URL, and any pre-existing nonterminal queue work.
