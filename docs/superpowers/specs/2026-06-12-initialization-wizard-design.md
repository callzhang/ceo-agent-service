# Initialization Wizard Design

Date: 2026-06-12

## Goal

Turn the current static `/tutorial` page into an initialization wizard that
guides a first-time user from a fresh checkout to a verified dry-run CEO Agent
Service installation.

The wizard should not ask the user to copy terminal commands. It should run
safe checks and safe setup actions itself, mark each step complete only after
verification, and explain the exact blocker when the system cannot proceed.

Manual confirmation is only a fallback when automated detection cannot prove
the state. For DingTalk send verification, the system should prefer command,
database, and page-state evidence, then use Computer Use to inspect the visible
DingTalk success page when needed, and only then ask for manual confirmation.

## Current Context

The service already has:

- A local FastAPI audit web UI in `app/audit_web.py`.
- A static `/tutorial` page that summarizes the installation runbook.
- CLI commands for setup and verification:
  - `setup-memory-connector`
  - `probe-dws`
  - `build-corpus`
  - `collect-corpus`
  - `build-work-profile`
  - `run-once`
  - `audit-web`
- Existing Memory Connector config helpers in `app/memory_setup.py`.
- Runtime configuration through `.env`, `.env.example`, and `app.config`.
- SQLite audit state in `AutoReplyStore`.
- `docs/agent-installation-runbook.md`, which defines the install contract and
  phase order.

The current `/tutorial` page is useful as documentation, but it does not store
progress, execute setup, verify outputs, or automatically check off completed
steps.

## Scope

In scope:

- Replace the static tutorial behavior with a stateful initialization wizard.
- Show setup steps with checkmarks driven by verified system state.
- Add backend checker/action endpoints for wizard steps.
- Persist wizard progress locally.
- Automatically run safe setup actions, including MCP configuration.
- Automatically inspect current local state before asking the user anything.
- Detect DingTalk send success from structured evidence first, Computer Use
  second, and manual confirmation last.
- Keep dry-run as the default path.
- Block launchd and live-send steps until prior verification passes.
- Add tests for wizard status, actions, gating, and failure states.

Out of scope for the first implementation:

- A full one-click live-send production rollout.
- Installing `dws` from an unknown external source.
- Scanning arbitrary local folders outside explicit configured workspace paths.
- Silent live DingTalk sends without a reviewed attempt and visible scope.
- Replacing the existing CLI commands.
- Replacing the existing `/config`, `/tasks`, `/errors`, and history pages.

## User Experience

The `/tutorial` page becomes a wizard inside the audit console.

Each step shows:

- Status: `not_started`, `checking`, `needs_action`, `running`, `done`, or
  `failed`.
- A short explanation of the current evidence.
- Buttons for available actions: `Check`, `Fix automatically`, `Run`, or
  `Retry`.
- A concise log of what was checked or executed.
- Links to relevant audit pages such as `/config?tab=system`, `/errors`,
  `/tasks`, and history.

A step is checked only when its checker returns `done`. Clicking a checkbox does
not complete the step by itself. If automated proof is unavailable, the step can
enter `needs_action` with a precise explanation and an explicit fallback action
such as `Confirm after page inspection`.

## Wizard Steps

The first implementation should follow the runbook phases:

1. **Preflight**
   - Check canonical rules were read by the agent.
   - Check repository path and git status.
   - Check Python, Node, and package environment.

2. **CLI Components**
   - Check `dws` exists, version is readable, auth status is valid, and
     `dws doctor` passes.
   - Check Codex CLI exists and can report a version.
   - Check Nvwa skill exists at the approved local skill path.

3. **MCP**
   - Check whether Codex MCP config includes `memory_connector`.
   - If missing and a memory URL is available, run the existing
     Memory Connector setup logic.
   - Re-check config after writing.
   - Do not invent or ask for a separate memory `user_id`; MCP identity comes
     from the installed Authorization header.

4. **Service Config**
   - Ensure `.env` exists, creating it from `.env.example` when safe.
   - Fill or validate safe defaults:
     - `CEO_WORKSPACE`
     - `CEO_WORKER_DB`
     - `CEO_CORPUS_DIR`
     - dry-run setting
   - Validate required directories exist.
   - Surface unresolved values, but prefer detected defaults.

5. **Data Corpus**
   - Check workspace shape under `CEO_WORKSPACE`.
   - Run `build-corpus` when local material exists.
   - Run `collect-corpus` when `dws` auth is valid.
   - Verify expected corpus outputs.

6. **Work Profile Distillation**
   - Run `build-work-profile`.
   - Verify:
     - `data/work-profile/work_profile.md`
     - `data/profile-evidence/evidence_index.jsonl`
     - `data/corpus/style_corpus.csv`
   - Check generated profile for obvious path, token, session id, or raw cache
     leakage.
   - Verify runtime consumption tests for `work_profile_instruction()`.

7. **Dry-Run Validation**
   - Ensure audit web is reachable.
   - Run `CEO_NOT_SEND_MESSAGE=1 .venv/bin/ceo-agent run-once
     --not-send-message`.
   - Check DB state for unresolved `failed` or `processing` backlog.
   - Check `/errors` and recent attempts for unexpected live-send status.

8. **Launchd**
   - Gate this step behind successful dry-run validation.
   - Inspect launchd config before install.
   - Install or restart only through an explicit wizard action.
   - Verify a running `com.ceo-agent-service.main` process and audit web health.

9. **Live Send Verification**
   - Gate this step behind reviewed dry-run evidence.
   - Show the exact reviewed attempt or test message scope before sending.
   - Trigger send only through an explicit action.
   - Verify send success in this order:
     1. CLI return value and process result.
     2. SQLite `sent_replies` / attempt status.
     3. DingTalk page state inspected through Computer Use.
     4. Manual confirmation fallback when automated inspection is inconclusive.

## Architecture

Add a small setup subsystem instead of embedding setup logic inside HTML.

Proposed modules:

- `app/setup_wizard.py`
  - Step definitions.
  - Checker functions.
  - Action functions.
  - Gating rules.
  - Status aggregation.

- `app/setup_wizard_models.py`
  - Typed data models for step status, actions, evidence, logs, and blockers.

- `app/setup_wizard_store.py`
  - Local persistence for wizard state.
  - Prefer SQLite tables in the existing worker DB so the audit UI has one
    local state source.

- `app/audit_web.py`
  - Render the wizard page.
  - Expose API endpoints that call the setup subsystem.
  - Keep UI rendering thin.

The existing CLI commands remain the source of truth for operational actions
where possible. The wizard should call the same code paths or invoke the CLI
with controlled arguments, then verify outputs.

## API Shape

Add audit-web endpoints:

- `GET /tutorial`
  - Render the wizard shell and current step state.

- `GET /tutorial/status`
  - Return all step states and available actions as JSON.

- `POST /tutorial/check/{step_id}`
  - Run the checker for one step and persist the result.

- `POST /tutorial/run/{action_id}`
  - Run one setup action, persist logs/evidence, then re-check the affected
    step.

- `POST /tutorial/confirm/{step_id}`
  - Allow manual fallback confirmation only for steps whose checker explicitly
    returns `manual_confirmation_allowed`.

Every action response should include:

- action id
- step id
- started/finished timestamps
- exit status
- summarized stdout/stderr or structured evidence
- next status
- next available actions

## Persistence

Store wizard state locally, not in committed source files.

Suggested tables:

- `setup_wizard_steps`
  - `step_id`
  - `status`
  - `updated_at`
  - `summary`
  - `manual_confirmed_at`
  - `manual_confirmed_by`

- `setup_wizard_events`
  - `id`
  - `step_id`
  - `action_id`
  - `status`
  - `started_at`
  - `finished_at`
  - `summary`
  - `evidence_json`
  - `stdout_excerpt`
  - `stderr_excerpt`

The checker should still recompute real state when asked. Persisted state is for
history and UI continuity, not a substitute for verification.

## Automation Rules

Safe automatic actions:

- Create `.env` from `.env.example` if `.env` is absent.
- Create local ignored runtime directories such as `data`, `corpus`, and the
  configured workspace directory.
- Write Codex Memory Connector config through existing helper logic.
- Run read-only checks.
- Run dry-run service commands.
- Build corpus/profile artifacts in ignored runtime paths.

Actions that require explicit wizard action:

- Package installation.
- `dws auth login`.
- launchd install/restart.
- Any live DingTalk send.
- Any action that changes an external system.

The wizard can automate these after the user clicks the action and sees the
scope. It should not run them silently in the background.

## Computer Use Verification

Computer Use is a verification fallback for UI-only DingTalk success evidence.

The send verifier should:

1. First check structured state from CLI and DB.
2. If structured state is insufficient, open or inspect the DingTalk success
   page through Computer Use.
3. Extract only the success/failure signal needed for the wizard.
4. Store the result as evidence in `setup_wizard_events`.
5. If the page cannot be inspected reliably, set the step to `needs_action`
   with manual confirmation available.

Computer Use must not replace the existing send safety checks. It only proves
whether an already-scoped send action appears to have succeeded.

## Error Handling

Each checker/action should fail closed:

- Unknown state becomes `needs_action` or `failed`, not `done`.
- Missing credentials should name the missing credential or auth surface.
- Network/download failures should include the failed command and error excerpt.
- Permission failures should say which macOS, DingTalk, or tool permission is
  missing.
- A step cannot be checked if its required prior steps are not complete.

The UI should show exact evidence, but must not expose tokens, cookies, local
secrets, signed URLs, or full private document excerpts.

## Testing

Add tests for:

- Wizard route renders step statuses and available actions.
- `/tutorial/status` returns all defined steps in order.
- Step checkers return `done`, `needs_action`, and `failed` correctly.
- MCP setup action uses existing Memory Connector helper and re-checks config.
- `.env` creation and directory creation are idempotent.
- Dry-run checker blocks launchd when backlog exists.
- Live-send action is gated until dry-run is complete.
- Computer Use verifier failure falls back to manual confirmation instead of
  marking success.
- Manual confirmation is rejected unless the checker allows it.
- Sensitive output redaction removes tokens, paths where required, and session
  ids from UI excerpts.

## Rollout

Implement in phases:

1. Add data models, static step definitions, and read-only status endpoint.
2. Add UI rendering for step status and checkmarks.
3. Add safe checkers for local environment, CLI presence, MCP config, `.env`,
   workspace, and artifact presence.
4. Add safe setup actions for `.env`, directories, and MCP config.
5. Add corpus/profile actions and verification.
6. Add dry-run and backlog verification.
7. Add gated launchd actions.
8. Add live-send verification and Computer Use fallback.

This order keeps the page useful early while limiting risk from externally
visible actions until the verification model is already in place.
