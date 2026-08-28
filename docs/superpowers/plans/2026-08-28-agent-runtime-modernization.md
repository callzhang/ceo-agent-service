# Agent Runtime Page Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Settings → Agent Runtime visually clear and give every sensitive credential a consistent show/hide input without changing runtime configuration semantics.

**Architecture:** Keep the existing `_render_agent_runtime_config()` and `handle_agent_runtime_config_post()` contract, but split the HTML into overview, route cards, and a shared password-field renderer. CSS remains in the existing audit web stylesheet string; behavior uses one delegated toggle script for all password fields.

**Tech Stack:** Python, FastAPI HTML rendering, inline CSS/JavaScript, pytest.

---

### Task 1: Add regression tests for the modern layout and credential controls

**Files:**
- Modify: `tests/test_audit_web.py`

- [ ] **Step 1: Add assertions for overview, cards, and four credential toggles**

Assert `/settings?tab=agent-runtime` contains `Runtime Overview`, `Codex OAuth`, `Codex API fallback`, `Friday Runtime`, four `password-field` wrappers, and one shared toggle script.

- [ ] **Step 2: Add secret non-leak assertions**

Set configured secret environment values, render the page, and assert their real values do not occur in HTML while configured-state labels do.

- [ ] **Step 3: Run the focused tests and verify they fail**

Run `pytest -q tests/test_audit_web.py -k agent_runtime`.

### Task 2: Implement the shared password-field component and modern layout

**Files:**
- Modify: `app/audit_web.py`

- [ ] **Step 1: Add CSS for runtime cards, overview, form grid, status badges, and password fields**

Use existing design tokens and responsive grid rules; keep the page readable below 960px.

- [ ] **Step 2: Add `_runtime_password_field()`**

Render an empty password input with configured-state metadata and a button using `aria-controls`, `aria-pressed`, and a visible “显示/隐藏” label. Never render the stored credential as `value` or placeholder text.

- [ ] **Step 3: Rewrite `_render_agent_runtime_config()`**

Preserve all existing input names and form action. Render overview, three cards, helper text, and one shared script that toggles every `.password-field` independently.

- [ ] **Step 4: Run focused tests and verify they pass**

Run `pytest -q tests/test_audit_web.py -k agent_runtime tests/test_settings_ia_refactor.py`.

### Task 3: Verify compatibility and live service behavior

**Files:**
- Modify: `README.md` only if the Agent Runtime UI description is stale.

- [ ] **Step 1: Run targeted regression suite**

Run `pytest -q tests/test_audit_web.py tests/test_agent_runtime_config.py tests/test_settings_ia_refactor.py`.

- [ ] **Step 2: Run `git diff --check` and commit**

Commit with `git commit -m "refactor: modernize agent runtime settings"`.

- [ ] **Step 3: Restart and verify launchd**

Run `launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main` and inspect `launchctl print ...` for `state = running` and a new PID.

- [ ] **Step 4: Run live smoke and confirm no credential leakage**

Request `http://127.0.0.1:8765/settings?tab=agent-runtime`, check HTTP 200, and grep only for configured-state text—not secret values.
