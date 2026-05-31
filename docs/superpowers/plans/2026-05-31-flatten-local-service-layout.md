# Flatten Local Service Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `apps/local-service/ceo_agent_service/` with a direct root-level `app/` package and remove the redundant `apps/local-service/` layer.

**Architecture:** This is a single-application repository, so the repository name carries the product identity and the Python package can be the conventional `app`. All runtime imports, monkeypatch targets, prompt `<code: ...>` tags, setuptools metadata, launchd commands, and docs must move from `ceo_agent_service.*` to `app.*`. Do not keep compatibility wrappers for `ceo_agent_service`; one import path is easier to audit.

**Tech Stack:** Python 3.11, setuptools editable install, pytest, FastAPI/uvicorn, launchd, shell scripts.

---

## Target Layout

```text
.
├── app/
│   ├── __init__.py
│   ├── cli.py
│   ├── worker.py
│   ├── ...
│   └── logo.png
├── tests/
├── docs/
├── launchd/
├── prompts/
├── scripts/
├── pyproject.toml
├── package.json
└── README.md
```

The runtime entrypoints become:

```bash
.venv/bin/python -m app.cli service --host 127.0.0.1 --port 8765
.venv/bin/ceo-agent service --host 127.0.0.1 --port 8765
```

Prompt code tags become:

```text
<code: app.prompt:work_profile_instruction()>
<code: app.user_prompt_blocks:current_message_block()>
```

## File Structure Map

- Move: `apps/local-service/ceo_agent_service/` -> `app/`
  Runtime package. All imports and monkeypatch strings must use `app.*`.
- Move: `apps/local-service/tests/` -> `tests/`
  Root-level tests.
- Move: `apps/local-service/docs/*.md` -> `docs/*.md`
  Operational docs join existing repo docs.
- Move: `apps/local-service/logo.png` -> `app/logo.png`
  Notification icon lives with the application package.
- Move: `apps/local-service/pyproject.toml` -> `pyproject.toml`
  Root-level Python project metadata.
- Delete: `apps/local-service/setup.py`
  `pyproject.toml` becomes the only packaging entry.
- Modify: `app/config.py`
  `repo_root()` must resolve from `app/` to the repository root.
- Modify: `app/cli.py`
  `_repo_root()` must resolve from `app/` to the repository root.
- Modify: `app/developer_prompt.py`
  Code-tag validation must allow `app.*`, not `ceo_agent_service.*`.
- Modify: `app/notification.py`
  Default icon path must use `app/logo.png`.
- Modify: `prompts/developer_prompt.md`, `prompts/user_prompt.md`
  Code tags must use `app.*`.
- Modify: `scripts/run-local-service.sh`, `launchd/com.ceo-agent-service.main.plist`, `package.json`
  Runtime commands must run from repository root.
- Modify: `README.md`, `CONTRIBUTING.md`, `docs/work-profile-distillation-tutorial.md`
  Commands and project tree must match the root layout.
- Create: `tests/test_repo_layout.py`
  Locks in the simplified layout.

Historical implementation plans under `docs/superpowers/plans/` are not operational runbooks. Do not rewrite every historical path inside those files unless a later task explicitly turns them into active documentation.

---

### Task 1: Add A Layout Guard Test

**Files:**
- Create: `tests/test_repo_layout.py`

- [ ] **Step 1: Write the failing layout test**

Create `tests/test_repo_layout.py` with:

```python
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_uses_direct_app_package_layout():
    assert (REPO_ROOT / "app" / "__init__.py").is_file()
    assert (REPO_ROOT / "app" / "cli.py").is_file()
    assert (REPO_ROOT / "app" / "logo.png").is_file()
    assert (REPO_ROOT / "tests").is_dir()
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert not (REPO_ROOT / "apps" / "local-service").exists()
    assert not (REPO_ROOT / "apps" / "local-service" / "ceo_agent_service").exists()


def test_repo_root_helpers_resolve_repository_root():
    from app.config import repo_root
    from app import cli

    assert repo_root() == REPO_ROOT
    assert cli._repo_root() == REPO_ROOT
```

- [ ] **Step 2: Run the test to verify it fails**

Run from repo root:

```bash
python3 -m pytest tests/test_repo_layout.py -q
```

Expected: FAIL because `tests/test_repo_layout.py` or `app/` does not exist yet.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_repo_layout.py
git commit -m "test: lock direct app layout"
```

---

### Task 2: Move Files To The Direct App Layout

**Files:**
- Move: `apps/local-service/ceo_agent_service/` -> `app/`
- Move: `apps/local-service/tests/*` -> `tests/`
- Move: `apps/local-service/docs/*.md` -> `docs/`
- Move: `apps/local-service/logo.png` -> `app/logo.png`
- Move: `apps/local-service/pyproject.toml` -> `pyproject.toml`
- Delete: `apps/local-service/setup.py`

- [ ] **Step 1: Move tracked files**

Run from repo root:

```bash
git mv apps/local-service/ceo_agent_service app
mkdir -p tests
git mv apps/local-service/tests/* tests/
git mv apps/local-service/docs/codex-session-history.md docs/codex-session-history.md
git mv apps/local-service/docs/message-response-window.md docs/message-response-window.md
git mv apps/local-service/docs/reply-worker-reliability.md docs/reply-worker-reliability.md
git mv apps/local-service/logo.png app/logo.png
git mv apps/local-service/pyproject.toml pyproject.toml
git rm apps/local-service/setup.py
```

Expected: `git status --short` shows renames for source, tests, docs, icon, and `pyproject.toml`, plus deletion of `setup.py`.

- [ ] **Step 2: Remove old generated local-service files**

Run:

```bash
find apps/local-service -maxdepth 2 -mindepth 1 -print
rm -rf apps/local-service/.venv apps/local-service/.pytest_cache apps/local-service/.ruff_cache apps/local-service/ceo_agent_local_service.egg-info
rmdir apps/local-service
rmdir apps
```

Expected: `test ! -e apps/local-service` exits 0. If `rmdir apps` fails because `apps/.DS_Store` exists, remove that generated file with `rm apps/.DS_Store` and rerun `rmdir apps`.

- [ ] **Step 3: Run the layout test again**

```bash
python3 -m pytest tests/test_repo_layout.py -q
```

Expected: FAIL because imports and root helpers still reference the old package name and parent depth.

- [ ] **Step 4: Commit the mechanical move**

```bash
git add app tests docs pyproject.toml
git add -u apps
git commit -m "refactor: move service package to app"
```

---

### Task 3: Rename Python Imports And Prompt Code Tags

**Files:**
- Modify: `app/**/*.py`
- Modify: `tests/**/*.py`
- Modify: `prompts/developer_prompt.md`
- Modify: `prompts/user_prompt.md`
- Modify: `docs/*.md`
- Modify: `README.md`

- [ ] **Step 1: Rewrite import references**

Run from repo root:

```bash
rg --files app tests prompts docs README.md CONTRIBUTING.md package.json launchd scripts pyproject.toml \
  | xargs perl -pi -e 's/ceo_agent_service/app/g; s/ceo-agent-local-service/ceo-agent-service/g'
```

Expected: `rg -n "ceo_agent_service|ceo-agent-local-service" app tests prompts docs README.md CONTRIBUTING.md package.json launchd scripts pyproject.toml` prints no output except historical plan files if included by the `docs` search. If historical plan files show matches, do not edit them just for history.

- [ ] **Step 2: Update code-tag validation text**

In `app/developer_prompt.py`, ensure the user-facing validation messages say:

```python
"code tag must look like <code: app.module:function()> "
```

and:

```python
"module code tags are restricted to app.* modules"
```

- [ ] **Step 3: Verify active prompt templates use `app.*`**

Run:

```bash
rg -n "<code: app\\." prompts tests app
rg -n "<code: ceo_agent_service\\." prompts tests app
```

Expected: first command prints prompt/test references; second command prints no output.

- [ ] **Step 4: Run focused import tests**

```bash
python3 -m pytest tests/test_prompt.py tests/test_codex_runner.py tests/test_audit_web.py::test_prompt_config_page_renders_dynamic_function_catalog -q
```

Expected: import failures may remain until `pyproject.toml` and root helper changes are done in Task 4. Record the exact failures and continue.

- [ ] **Step 5: Commit import and prompt rename**

```bash
git add app tests prompts docs README.md CONTRIBUTING.md package.json launchd scripts pyproject.toml
git commit -m "refactor: rename runtime package to app"
```

---

### Task 4: Update Packaging, Root Helpers, And Icon Path

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/config.py`
- Modify: `app/cli.py`
- Modify: `app/notification.py`
- Modify: `tests/test_hourly_dry_run_launchd.py`

- [ ] **Step 1: Replace `pyproject.toml`**

Replace `pyproject.toml` with:

```toml
[project]
name = "ceo-agent-service"
version = "0.1.0"
description = "Local-first DingTalk executive auto-reply service"
requires-python = ">=3.11"
license = "MIT"
dependencies = [
  "fastapi>=0.115",
  "pypdf>=5.0",
  "pydantic>=2.10",
  "uvicorn>=0.34"
]

[project.optional-dependencies]
dev = [
  "httpx>=0.28",
  "pytest>=8.3"
]

[project.scripts]
ceo-agent = "app.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
markers = [
  "live: explicit opt-in tests that may call real dws, Codex, or DingTalk"
]

[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]
```

- [ ] **Step 2: Update root helpers**

In `app/config.py`, set:

```python
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
```

In `app/cli.py`, set:

```python
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
```

- [ ] **Step 3: Update notification icon path**

In `app/notification.py`, set:

```python
DEFAULT_NOTIFICATION_ICON_PATH = Path(__file__).resolve().parent / "logo.png"
```

- [ ] **Step 4: Update launchd test root**

In `tests/test_hourly_dry_run_launchd.py`, set:

```python
REPO_ROOT = Path(__file__).resolve().parents[1]
```

- [ ] **Step 5: Create root virtualenv and install package**

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Expected: `.venv/bin/ceo-agent` exists and imports `app.cli:main`.

- [ ] **Step 6: Run focused tests**

```bash
.venv/bin/python -m pytest tests/test_repo_layout.py tests/test_prompt.py::test_work_profile_path_default_is_not_user_specific tests/test_cli.py::test_default_paths_are_repo_local tests/test_notification.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit packaging and path changes**

```bash
git add pyproject.toml app/config.py app/cli.py app/notification.py tests/test_hourly_dry_run_launchd.py tests/test_repo_layout.py
git commit -m "refactor: package service as app"
```

---

### Task 5: Update Runtime Scripts And Launchd

**Files:**
- Modify: `scripts/run-local-service.sh`
- Modify: `launchd/com.ceo-agent-service.main.plist`
- Modify: `package.json`
- Test: `tests/test_hourly_dry_run_launchd.py`

- [ ] **Step 1: Update `scripts/run-local-service.sh` working directory**

Replace:

```bash
cd "${repo_root}/apps/local-service"
```

with:

```bash
cd "${repo_root}"
```

Set the fallback command to:

```bash
ceo_agent_cmd=(.venv/bin/python -c 'from app.cli import main; main()')
```

- [ ] **Step 2: Update launchd command**

In `launchd/com.ceo-agent-service.main.plist`, replace the command CDATA with:

```xml
<string><![CDATA[service_root="${CEO_SERVICE_ROOT:-${HOME:?HOME must be set}/Documents/Projects/ceo-agent-service}"; cd "${service_root}" && export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}" && export HOME="${CEO_SERVICE_HOME:-${HOME}}" && export PYTHONPATH="${PYTHONPATH:-.}" && export CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}" && export CEO_WORKSPACE="${CEO_WORKSPACE:-${HOME}/Documents/memory}" && export CEO_WORKER_DB="${CEO_WORKER_DB:-${service_root}/data/auto-reply.sqlite3}" && export CEO_CORPUS_DIR="${CEO_CORPUS_DIR:-${service_root}/corpus}" && export DWS_DISABLE_KEYCHAIN="${DWS_DISABLE_KEYCHAIN:-1}" && export DWS_KEYCHAIN_DIR="${DWS_KEYCHAIN_DIR:-${CEO_WORKSPACE}/Library/Application Support/dws-cli}" && export CEO_NOT_SEND_MESSAGE=0 && export CEO_LIVE_SEND_BLOCKERS_ACCEPTED=1 && exec .venv/bin/python -m app.cli service --host "${CEO_AUDIT_WEB_HOST:-127.0.0.1}" --port "${CEO_AUDIT_WEB_PORT:-8765}" --producer-interval-seconds "${CEO_PRODUCER_INTERVAL_SECONDS:-60}" --consumer-poll-interval-seconds "${CEO_CONSUMER_POLL_INTERVAL_SECONDS:-10}" --db "${CEO_WORKER_DB}" --workspace "${CEO_WORKSPACE}" --corpus-dir "${CEO_CORPUS_DIR}"]]></string>
```

Keep the plist `EnvironmentVariables` `PYTHONPATH` value as:

```xml
<string>.</string>
```

- [ ] **Step 3: Update `package.json` scripts**

Replace:

```json
"scripts": {
  "dev:local": "cd apps/local-service && ceo-agent run",
  "test:local": "cd apps/local-service && pytest"
}
```

with:

```json
"scripts": {
  "dev:local": "ceo-agent run",
  "test:local": "pytest"
}
```

- [ ] **Step 4: Run script tests**

```bash
.venv/bin/python -m pytest tests/test_hourly_dry_run_launchd.py -q
```

Expected: PASS and no assertion references `apps/local-service`.

- [ ] **Step 5: Smoke-test root script**

```bash
CEO_NOT_SEND_MESSAGE=1 CEO_MAX_BATCHES=0 scripts/run-local-service.sh
```

Expected: exits successfully without `ModuleNotFoundError`.

- [ ] **Step 6: Commit runtime entrypoint changes**

```bash
git add scripts/run-local-service.sh launchd/com.ceo-agent-service.main.plist package.json tests/test_hourly_dry_run_launchd.py
git commit -m "refactor: run app package from repo root"
```

---

### Task 6: Update Active Documentation

**Files:**
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/work-profile-distillation-tutorial.md`

- [ ] **Step 1: Update installation commands**

In `README.md` and `CONTRIBUTING.md`, replace:

```bash
python3 -m venv apps/local-service/.venv
apps/local-service/.venv/bin/pip install -e 'apps/local-service[dev]'
```

with:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

- [ ] **Step 2: Update command working directories**

In `README.md`, `CONTRIBUTING.md`, and `docs/work-profile-distillation-tutorial.md`, replace operational examples using:

```bash
cd apps/local-service
```

with:

```bash
cd /path/to/ceo-agent-service
```

Replace `python -m ceo_agent_service.cli` examples with `python -m app.cli`.

- [ ] **Step 3: Update README project structure**

Replace:

```text
├── apps/local-service/          # Python 服务、CLI、worker、测试
```

with:

```text
├── app/                         # Python 应用包、CLI、worker 和资源
├── tests/                       # Python 测试
```

- [ ] **Step 4: Check active docs do not mention old paths**

```bash
rg -n "apps/local-service|ceo_agent_service|python -m ceo_agent_service" README.md CONTRIBUTING.md docs/work-profile-distillation-tutorial.md prompts scripts launchd package.json pyproject.toml tests app
```

Expected: no output.

- [ ] **Step 5: Commit documentation changes**

```bash
git add README.md CONTRIBUTING.md docs/work-profile-distillation-tutorial.md prompts
git commit -m "docs: document direct app layout"
```

---

### Task 7: Full Verification, Launchd Reinstall, And Push

**Files:**
- No source edits expected unless verification exposes a missed reference.

- [ ] **Step 1: Run full tests**

```bash
.venv/bin/python -m pytest -q
```

Expected: all non-live tests pass.

- [ ] **Step 2: Run final scans**

```bash
git diff --check
rg -n "apps/local-service|ceo_agent_service|ceo-agent-local-service" app tests prompts README.md CONTRIBUTING.md docs/work-profile-distillation-tutorial.md scripts launchd package.json pyproject.toml
test ! -e apps/local-service
```

Expected: `git diff --check` prints no output, `rg` prints no output, and `test` exits 0.

- [ ] **Step 3: Reinstall launchd plist**

```bash
scripts/install-auto-reply-agents.sh
```

Expected:

```text
installed /Users/<user>/Library/LaunchAgents/com.ceo-agent-service.main.plist
```

- [ ] **Step 4: Verify service status**

```bash
pgrep -fl "app.cli service"
lsof -nP -iTCP:8765 -sTCP:LISTEN
curl -sS -o /tmp/ceo-config-check.html -w '%{http_code}\n' 'http://127.0.0.1:8765/config?tab=info'
```

Expected:

```text
<pid> .venv/bin/python -m app.cli service ...
python... TCP 127.0.0.1:8765 (LISTEN)
200
```

- [ ] **Step 5: Commit verification fixes if needed**

If verification required fixes:

```bash
git add app tests prompts docs README.md CONTRIBUTING.md scripts launchd package.json pyproject.toml
git commit -m "fix: complete direct app layout migration"
```

If no files changed, skip this commit.

- [ ] **Step 6: Push**

```bash
git status --short --branch
git push
git status --short --branch
```

Expected: push succeeds and branch returns to clean `main...origin/main`.

---

## Self-Review

**Spec coverage:** The plan changes `apps/local-service/ceo_agent_service/` into direct `app/`, removes `apps/local-service/`, moves tests and packaging to root, changes imports/code tags to `app.*`, updates scripts/launchd/docs, verifies service health, and pushes.

**Placeholder scan:** No task relies on placeholder markers or vague implementation instructions. Each code or command change includes exact snippets or exact commands.

**Type consistency:** The plan consistently uses `app` as the Python package, `app.cli:main` as the script entrypoint, `python -m app.cli` as the module command, root `.venv`, root `pyproject.toml`, and root-level `tests/`.

