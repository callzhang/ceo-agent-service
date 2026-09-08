# Email Classifier Luna Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route only cold-start Email classification through `gpt-5.6-luna` while preserving every other Agent model and existing failover behavior.

**Architecture:** Add an optional, validated Codex OAuth model override at the production routed-execution construction boundary. Email classification reads `CEO_EMAIL_CLASSIFIER_MODEL` and passes it only to its dedicated routed execution; the shared runtime config and description optimizer remain unchanged.

**Tech Stack:** Python 3.12, Pydantic runtime contracts, pytest, launchd, SQLite runtime-attempt evidence.

---

### Task 1: Add a scoped routed-execution model override

**Files:**
- Modify: `app/agent_runtime_production.py`
- Test: `tests/test_agent_runtime_production.py`

- [x] **Step 1: Write the failing tests**

Add tests proving that `build_production_routed_codex_execution(..., codex_oauth_model="gpt-5.6-luna")` replaces only the `codex_oauth` route model, preserves all other route models, and rejects an unsupported model.

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
pytest -q tests/test_agent_runtime_production.py -k codex_oauth_model
```

Expected: failure because the builder does not accept `codex_oauth_model`.

- [x] **Step 3: Implement the minimal override**

Validate the optional value against `SUPPORTED_CODEX_RUNTIME_MODELS`, copy only the named `codex_oauth` route with the requested model, and construct the router/adapter from that scoped config. Empty input means no override.

- [x] **Step 4: Run the focused tests**

Run the same pytest command and expect all selected tests to pass.

### Task 2: Wire and deploy the Email-only setting

**Files:**
- Modify: `app/email_worker.py`
- Modify: `.env`
- Modify: `.env.example`
- Test: `tests/test_email_worker.py`

- [x] **Step 1: Write the failing Email wiring test**

Add a builder test that sets `CEO_EMAIL_CLASSIFIER_MODEL=gpt-5.6-luna`, captures routed-execution builder arguments, and proves the value is supplied only for `EmailClassifierRoutedBackend`; description optimization receives no override.

- [x] **Step 2: Run the test to verify RED**

Run:

```bash
pytest -q tests/test_email_worker.py -k classifier_model_override
```

Expected: failure because Email does not yet read or pass the setting.

- [x] **Step 3: Implement and configure**

Read `CEO_EMAIL_CLASSIFIER_MODEL` while building the Email classification backend and pass it as `codex_oauth_model`. Add the documented optional variable to `.env.example` and configure `.env` as:

```text
CEO_EMAIL_CLASSIFIER_MODEL=gpt-5.6-luna
```

- [x] **Step 4: Verify and commit**

Run focused tests, `pytest -q tests/test_email*.py`, and Ruff for changed Python files. Commit only task-owned source, tests, documentation, and `.env.example`; `.env` remains local configuration.

- [x] **Step 5: Restart and prove the live model**

Before restart, confirm active Email operations are resumable and have no external effect in progress. Restart `com.ceo-agent-service.main`, verify a new PID and `/healthz`, then wait for a newly created `email_classification` runtime attempt and query SQLite for `route_name=codex_oauth` and `model=gpt-5.6-luna`. Confirm no failed/stuck Email classifications or provider actions and no Email reply task.
