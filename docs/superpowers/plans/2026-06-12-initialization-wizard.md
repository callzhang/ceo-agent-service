# Initialization Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stateful `/tutorial` initialization wizard that automatically checks and safely configures CEO Agent Service setup steps, then marks each step complete only from verified evidence.

**Architecture:** Add a focused setup wizard subsystem with typed models, deterministic step checkers/actions, and SQLite-backed event history. Keep the audit web page thin: it renders status returned by the subsystem and exposes small JSON/action endpoints. Existing CLI/helper code remains the source of truth for setup operations such as Memory Connector config and dry-run execution.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite via `AutoReplyStore`, existing `app.cli` commands, existing `app.memory_setup` helpers, pytest.

---

## File Structure

- Create `app/setup_wizard_models.py`
  - Pydantic models and string constants for step status, action status, evidence, and responses.

- Create `app/setup_wizard.py`
  - Static step definitions.
  - Read-only checkers.
  - Safe action runner for `.env`, directories, Memory Connector config, corpus/profile commands, dry-run, and gated launchd/live-send evidence.
  - Gating rules and status aggregation.

- Modify `app/store.py`
  - Add `setup_wizard_steps` and `setup_wizard_events` tables.
  - Add CRUD methods for wizard step state and event history.

- Modify `app/audit_web.py`
  - Replace static tutorial content with a wizard rendering.
  - Add `/tutorial/status`, `/tutorial/check/{step_id}`, `/tutorial/run/{action_id}`, and `/tutorial/confirm/{step_id}` endpoints.
  - Keep current `/tutorial` route and top-nav item.

- Test `tests/test_setup_wizard.py`
  - Unit tests for models, step definitions, checkers, actions, gating, redaction, and Computer Use fallback state.

- Modify `tests/test_store.py`
  - Persistence tests for wizard steps/events.

- Modify `tests/test_audit_web.py`
  - Route/render/API tests for wizard UI and endpoint behavior.

- Update `docs/agent-installation-runbook.md`
  - Point first-time installs to the interactive wizard while preserving the runbook as the agent contract.

---

### Task 1: Models And Static Step Definitions

**Files:**
- Create: `app/setup_wizard_models.py`
- Create: `app/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 1: Write failing tests for step models and order**

Add this new file:

```python
# tests/test_setup_wizard.py
from app.setup_wizard import SETUP_WIZARD_STEPS, get_step_definition
from app.setup_wizard_models import SetupStepStatus, SetupWizardStatus


def test_setup_wizard_steps_are_ordered_and_gated():
    assert [step.id for step in SETUP_WIZARD_STEPS] == [
        "preflight",
        "cli_components",
        "mcp",
        "service_config",
        "data_corpus",
        "work_profile",
        "dry_run",
        "launchd",
        "live_send",
    ]
    assert get_step_definition("mcp").depends_on == ["cli_components"]
    assert get_step_definition("launchd").depends_on == ["dry_run"]
    assert get_step_definition("live_send").depends_on == ["dry_run"]


def test_setup_step_status_defaults_to_not_started():
    status = SetupStepStatus(step_id="mcp", title="MCP")

    assert status.status == "not_started"
    assert status.summary == ""
    assert status.available_actions == []
    assert status.manual_confirmation_allowed is False


def test_setup_wizard_status_serializes_steps():
    status = SetupWizardStatus(
        steps=[
            SetupStepStatus(
                step_id="preflight",
                title="Preflight",
                status="done",
                summary="Python is available",
            )
        ]
    )

    payload = status.model_dump()

    assert payload["steps"][0]["step_id"] == "preflight"
    assert payload["steps"][0]["status"] == "done"
    assert payload["steps"][0]["summary"] == "Python is available"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py -q
```

Expected: FAIL during import because `app.setup_wizard` and `app.setup_wizard_models` do not exist.

- [ ] **Step 3: Add setup wizard models**

Create `app/setup_wizard_models.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field


SetupStatus = Literal[
    "not_started",
    "checking",
    "needs_action",
    "running",
    "done",
    "failed",
    "blocked",
]
SetupActionStatus = Literal["not_started", "running", "done", "failed"]


class SetupAction(BaseModel):
    id: str
    label: str
    step_id: str
    kind: Literal["check", "run", "confirm"]
    destructive: bool = False
    external_side_effect: bool = False


class SetupStepDefinition(BaseModel):
    id: str
    title: str
    phase: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    actions: list[SetupAction] = Field(default_factory=list)


class SetupStepStatus(BaseModel):
    step_id: str
    title: str
    status: SetupStatus = "not_started"
    summary: str = ""
    evidence: dict[str, str | int | bool] = Field(default_factory=dict)
    available_actions: list[SetupAction] = Field(default_factory=list)
    manual_confirmation_allowed: bool = False
    updated_at: str = ""


class SetupWizardEvent(BaseModel):
    id: int = 0
    step_id: str
    action_id: str
    status: SetupActionStatus
    summary: str = ""
    evidence: dict[str, str | int | bool] = Field(default_factory=dict)
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    started_at: str = ""
    finished_at: str = ""


class SetupWizardStatus(BaseModel):
    steps: list[SetupStepStatus]
```

- [ ] **Step 4: Add static step definitions**

Create `app/setup_wizard.py`:

```python
from app.setup_wizard_models import SetupAction, SetupStepDefinition


SETUP_WIZARD_STEPS: tuple[SetupStepDefinition, ...] = (
    SetupStepDefinition(
        id="preflight",
        title="Preflight",
        phase="Phase 1",
        description="Verify local checkout, Python, Node, and package environment.",
        actions=[
            SetupAction(id="check_preflight", label="Check", step_id="preflight", kind="check"),
        ],
    ),
    SetupStepDefinition(
        id="cli_components",
        title="CLI Components",
        phase="Phase 2",
        description="Verify dws, Codex CLI, and Nvwa skill availability.",
        depends_on=["preflight"],
        actions=[
            SetupAction(id="check_cli_components", label="Check", step_id="cli_components", kind="check"),
        ],
    ),
    SetupStepDefinition(
        id="mcp",
        title="Memory Connector MCP",
        phase="Phase 2",
        description="Verify or configure the memory_connector MCP entry.",
        depends_on=["cli_components"],
        actions=[
            SetupAction(id="check_mcp", label="Check", step_id="mcp", kind="check"),
            SetupAction(id="setup_mcp", label="Fix automatically", step_id="mcp", kind="run"),
        ],
    ),
    SetupStepDefinition(
        id="service_config",
        title="Service Config",
        phase="Phase 3",
        description="Create and validate .env, runtime paths, and dry-run defaults.",
        depends_on=["mcp"],
        actions=[
            SetupAction(id="check_service_config", label="Check", step_id="service_config", kind="check"),
            SetupAction(id="setup_service_config", label="Fix automatically", step_id="service_config", kind="run"),
        ],
    ),
    SetupStepDefinition(
        id="data_corpus",
        title="Data Corpus",
        phase="Phase 4",
        description="Build local style corpus from workspace and DingTalk samples.",
        depends_on=["service_config"],
        actions=[
            SetupAction(id="check_data_corpus", label="Check", step_id="data_corpus", kind="check"),
            SetupAction(id="build_data_corpus", label="Run", step_id="data_corpus", kind="run"),
        ],
    ),
    SetupStepDefinition(
        id="work_profile",
        title="Work Profile Distillation",
        phase="Phase 5",
        description="Generate and verify profiles/work_profile.md and evidence index.",
        depends_on=["data_corpus"],
        actions=[
            SetupAction(id="check_work_profile", label="Check", step_id="work_profile", kind="check"),
            SetupAction(id="build_work_profile", label="Run", step_id="work_profile", kind="run"),
        ],
    ),
    SetupStepDefinition(
        id="dry_run",
        title="Dry-Run Validation",
        phase="Phase 7",
        description="Run dry-run processing and verify audit state has no unresolved backlog.",
        depends_on=["work_profile"],
        actions=[
            SetupAction(id="check_dry_run", label="Check", step_id="dry_run", kind="check"),
            SetupAction(id="run_dry_run", label="Run", step_id="dry_run", kind="run"),
        ],
    ),
    SetupStepDefinition(
        id="launchd",
        title="Launchd Service",
        phase="Phase 8",
        description="Install or restart launchd only after dry-run is verified.",
        depends_on=["dry_run"],
        actions=[
            SetupAction(id="check_launchd", label="Check", step_id="launchd", kind="check"),
            SetupAction(
                id="install_launchd",
                label="Run",
                step_id="launchd",
                kind="run",
                external_side_effect=True,
            ),
        ],
    ),
    SetupStepDefinition(
        id="live_send",
        title="Live Send Verification",
        phase="Phase 9",
        description="Verify a reviewed DingTalk send from structured state, Computer Use, or manual fallback.",
        depends_on=["dry_run"],
        actions=[
            SetupAction(id="check_live_send", label="Check", step_id="live_send", kind="check"),
            SetupAction(
                id="verify_live_send",
                label="Run",
                step_id="live_send",
                kind="run",
                external_side_effect=True,
            ),
            SetupAction(id="confirm_live_send", label="Confirm after page inspection", step_id="live_send", kind="confirm"),
        ],
    ),
)


def get_step_definition(step_id: str) -> SetupStepDefinition:
    for step in SETUP_WIZARD_STEPS:
        if step.id == step_id:
            return step
    raise KeyError(step_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add app/setup_wizard_models.py app/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: add setup wizard step models"
```

---

### Task 2: SQLite Persistence For Wizard State

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing store tests**

Append to `tests/test_store.py`:

```python
def test_setup_wizard_step_state_round_trips(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.upsert_setup_wizard_step(
        step_id="mcp",
        status="done",
        summary="Codex config contains memory_connector",
        manual_confirmed_by="",
    )
    row = store.get_setup_wizard_step("mcp")

    assert row["step_id"] == "mcp"
    assert row["status"] == "done"
    assert row["summary"] == "Codex config contains memory_connector"
    assert row["manual_confirmed_by"] == ""
    assert row["updated_at"]


def test_setup_wizard_event_history_round_trips(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    event_id = store.record_setup_wizard_event(
        step_id="mcp",
        action_id="setup_mcp",
        status="done",
        summary="wrote config",
        evidence_json='{"codex_config": "/tmp/config.toml"}',
        stdout_excerpt="setup-memory-connector codex_config=/tmp/config.toml",
        stderr_excerpt="",
    )
    events = store.list_setup_wizard_events("mcp")

    assert event_id > 0
    assert len(events) == 1
    assert events[0]["step_id"] == "mcp"
    assert events[0]["action_id"] == "setup_mcp"
    assert events[0]["evidence_json"] == '{"codex_config": "/tmp/config.toml"}'
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_store.py::test_setup_wizard_step_state_round_trips tests/test_store.py::test_setup_wizard_event_history_round_trips -q
```

Expected: FAIL with missing `AutoReplyStore` methods.

- [ ] **Step 3: Add tables to store initialization**

In `app/store.py`, inside the existing `_initialize()` `executescript` block after `service_state`, add:

```python
                create table if not exists setup_wizard_steps (
                    step_id text primary key,
                    status text not null,
                    summary text not null default '',
                    manual_confirmed_at text not null default '',
                    manual_confirmed_by text not null default '',
                    updated_at text not null default current_timestamp
                );
                create table if not exists setup_wizard_events (
                    id integer primary key autoincrement,
                    step_id text not null,
                    action_id text not null,
                    status text not null,
                    summary text not null default '',
                    evidence_json text not null default '{}',
                    stdout_excerpt text not null default '',
                    stderr_excerpt text not null default '',
                    started_at text not null default current_timestamp,
                    finished_at text not null default current_timestamp
                );
                create index if not exists idx_setup_wizard_events_step
                    on setup_wizard_events(step_id, id);
```

- [ ] **Step 4: Add store methods**

In `app/store.py`, near the `service_state` helpers or before `record_error`, add:

```python
    def upsert_setup_wizard_step(
        self,
        *,
        step_id: str,
        status: str,
        summary: str,
        manual_confirmed_by: str = "",
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                insert into setup_wizard_steps (
                    step_id,
                    status,
                    summary,
                    manual_confirmed_at,
                    manual_confirmed_by
                )
                values (?, ?, ?, case when ? != '' then current_timestamp else '' end, ?)
                on conflict(step_id) do update set
                    status=excluded.status,
                    summary=excluded.summary,
                    manual_confirmed_at=case
                        when excluded.manual_confirmed_by != '' then current_timestamp
                        else setup_wizard_steps.manual_confirmed_at
                    end,
                    manual_confirmed_by=case
                        when excluded.manual_confirmed_by != '' then excluded.manual_confirmed_by
                        else setup_wizard_steps.manual_confirmed_by
                    end,
                    updated_at=current_timestamp
                """,
                (
                    step_id,
                    status,
                    summary,
                    manual_confirmed_by,
                    manual_confirmed_by,
                ),
            )

    def get_setup_wizard_step(self, step_id: str) -> dict[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                """
                select step_id, status, summary, manual_confirmed_at,
                       manual_confirmed_by, updated_at
                from setup_wizard_steps
                where step_id=?
                """,
                (step_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_setup_wizard_steps(self) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                """
                select step_id, status, summary, manual_confirmed_at,
                       manual_confirmed_by, updated_at
                from setup_wizard_steps
                order by updated_at desc
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def record_setup_wizard_event(
        self,
        *,
        step_id: str,
        action_id: str,
        status: str,
        summary: str = "",
        evidence_json: str = "{}",
        stdout_excerpt: str = "",
        stderr_excerpt: str = "",
    ) -> int:
        with self._connect() as db:
            cursor = db.execute(
                """
                insert into setup_wizard_events (
                    step_id,
                    action_id,
                    status,
                    summary,
                    evidence_json,
                    stdout_excerpt,
                    stderr_excerpt
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    action_id,
                    status,
                    summary,
                    evidence_json,
                    stdout_excerpt,
                    stderr_excerpt,
                ),
            )
            return int(cursor.lastrowid)

    def list_setup_wizard_events(
        self,
        step_id: str | None = None,
        *,
        limit: int = 20,
    ) -> list[dict[str, str | int]]:
        with self._connect() as db:
            args: list[str | int] = []
            where = ""
            if step_id is not None:
                where = "where step_id=?"
                args.append(step_id)
            args.append(limit)
            rows = db.execute(
                f"""
                select id, step_id, action_id, status, summary, evidence_json,
                       stdout_excerpt, stderr_excerpt, started_at, finished_at
                from setup_wizard_events
                {where}
                order by id desc
                limit ?
                """,
                args,
            ).fetchall()
            return [dict(row) for row in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_store.py::test_setup_wizard_step_state_round_trips tests/test_store.py::test_setup_wizard_event_history_round_trips -q
```

Expected: PASS.

- [ ] **Step 6: Run full store tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_store.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: persist setup wizard state"
```

---

### Task 3: Status Aggregation, Gating, And Redaction

**Files:**
- Modify: `app/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 1: Write failing tests for status aggregation and gating**

Append to `tests/test_setup_wizard.py`:

```python
from pathlib import Path

from app.store import AutoReplyStore
from app.setup_wizard import build_wizard_status, redact_setup_output


def test_build_wizard_status_blocks_dependent_steps(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    status = build_wizard_status(store)
    steps = {step.step_id: step for step in status.steps}

    assert steps["preflight"].status == "not_started"
    assert steps["mcp"].status == "blocked"
    assert steps["mcp"].summary == "Blocked until CLI Components is complete."
    assert steps["mcp"].available_actions == []


def test_build_wizard_status_allows_next_step_after_dependency_done(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(
        step_id="preflight",
        status="done",
        summary="ok",
    )

    status = build_wizard_status(store)
    steps = {step.step_id: step for step in status.steps}

    assert steps["cli_components"].status == "not_started"
    assert [action.id for action in steps["cli_components"].available_actions] == [
        "check_cli_components"
    ]


def test_redact_setup_output_removes_secrets_and_session_ids():
    text = (
        "Authorization: Bearer abc.def token=secret123 "
        "session_id=019eb3e7-dfc2 path=/Users/derek/Documents/private.md"
    )

    redacted = redact_setup_output(text)

    assert "abc.def" not in redacted
    assert "secret123" not in redacted
    assert "019eb3e7-dfc2" not in redacted
    assert "/Users/derek/Documents/private.md" not in redacted
    assert "[REDACTED_BEARER]" in redacted
    assert "[REDACTED_TOKEN]" in redacted
    assert "[REDACTED_SESSION]" in redacted
    assert "[REDACTED_PATH]" in redacted
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py::test_build_wizard_status_blocks_dependent_steps tests/test_setup_wizard.py::test_build_wizard_status_allows_next_step_after_dependency_done tests/test_setup_wizard.py::test_redact_setup_output_removes_secrets_and_session_ids -q
```

Expected: FAIL because `build_wizard_status` and `redact_setup_output` do not exist.

- [ ] **Step 3: Implement status aggregation and redaction**

Add to `app/setup_wizard.py`:

```python
import re

from app.setup_wizard_models import SetupStepStatus, SetupWizardStatus
from app.store import AutoReplyStore


BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+")
TOKEN_RE = re.compile(r"(?i)(token|api[_-]?key|secret)=\S+")
SESSION_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4,}(?:-[0-9a-f]{4,})+\b")
LOCAL_PATH_RE = re.compile(r"/Users/[^\s'\"<>]+")


def redact_setup_output(text: str) -> str:
    redacted = BEARER_RE.sub("Bearer [REDACTED_BEARER]", text)
    redacted = TOKEN_RE.sub(lambda match: f"{match.group(1)}=[REDACTED_TOKEN]", redacted)
    redacted = SESSION_RE.sub("[REDACTED_SESSION]", redacted)
    redacted = LOCAL_PATH_RE.sub("[REDACTED_PATH]", redacted)
    return redacted


def build_wizard_status(store: AutoReplyStore) -> SetupWizardStatus:
    persisted = {
        row["step_id"]: row
        for row in store.list_setup_wizard_steps()
    }
    complete = {
        step_id
        for step_id, row in persisted.items()
        if row["status"] == "done"
    }
    statuses: list[SetupStepStatus] = []
    for definition in SETUP_WIZARD_STEPS:
        missing_dependency = next(
            (
                dependency
                for dependency in definition.depends_on
                if dependency not in complete
            ),
            "",
        )
        row = persisted.get(definition.id)
        if missing_dependency:
            dependency_title = get_step_definition(missing_dependency).title
            statuses.append(
                SetupStepStatus(
                    step_id=definition.id,
                    title=definition.title,
                    status="blocked",
                    summary=f"Blocked until {dependency_title} is complete.",
                    updated_at=row["updated_at"] if row else "",
                )
            )
            continue
        statuses.append(
            SetupStepStatus(
                step_id=definition.id,
                title=definition.title,
                status=row["status"] if row else "not_started",
                summary=row["summary"] if row else "",
                available_actions=definition.actions,
                updated_at=row["updated_at"] if row else "",
            )
        )
    return SetupWizardStatus(steps=statuses)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: aggregate setup wizard status"
```

---

### Task 4: Read-Only Checkers For Local Environment And Artifacts

**Files:**
- Modify: `app/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 1: Write failing checker tests**

Append to `tests/test_setup_wizard.py`:

```python
from app.setup_wizard import (
    check_data_corpus,
    check_service_config,
    check_work_profile,
)


def test_check_service_config_detects_missing_env(tmp_path: Path):
    result = check_service_config(repo_root=tmp_path)

    assert result.status == "needs_action"
    assert result.summary == ".env is missing."
    assert result.evidence["env_exists"] is False


def test_check_service_config_accepts_env_and_directories(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "CEO_WORKSPACE=workspace\nCEO_WORKER_DB=data/auto-reply.sqlite3\nCEO_CORPUS_DIR=corpus\nCEO_NOT_SEND_MESSAGE=1\n",
        encoding="utf-8",
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "corpus").mkdir()

    result = check_service_config(repo_root=tmp_path)

    assert result.status == "done"
    assert result.summary == "Service config and runtime directories are ready."
    assert result.evidence["dry_run_enabled"] is True


def test_check_data_corpus_requires_style_corpus(tmp_path: Path):
    result = check_data_corpus(repo_root=tmp_path)

    assert result.status == "needs_action"
    assert result.summary == "corpus/style_corpus.csv is missing."


def test_check_work_profile_requires_profile_and_evidence(tmp_path: Path):
    result = check_work_profile(repo_root=tmp_path)

    assert result.status == "needs_action"
    assert result.summary == "profiles/work_profile.md is missing."


def test_check_work_profile_flags_leaked_local_path(tmp_path: Path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "data" / "profile-evidence").mkdir(parents=True)
    (tmp_path / "corpus").mkdir()
    (tmp_path / "profiles" / "work_profile.md").write_text(
        "Evidence from /Users/derek/Documents/private.md",
        encoding="utf-8",
    )
    (tmp_path / "data" / "profile-evidence" / "evidence_index.jsonl").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (tmp_path / "corpus" / "style_corpus.csv").write_text(
        "source,text\n",
        encoding="utf-8",
    )

    result = check_work_profile(repo_root=tmp_path)

    assert result.status == "failed"
    assert result.summary == "profiles/work_profile.md contains sensitive local evidence."
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py::test_check_service_config_detects_missing_env tests/test_setup_wizard.py::test_check_service_config_accepts_env_and_directories tests/test_setup_wizard.py::test_check_data_corpus_requires_style_corpus tests/test_setup_wizard.py::test_check_work_profile_requires_profile_and_evidence tests/test_setup_wizard.py::test_check_work_profile_flags_leaked_local_path -q
```

Expected: FAIL because checker functions do not exist.

- [ ] **Step 3: Add checker result helper and file checkers**

Add to `app/setup_wizard.py`:

```python
from pathlib import Path


def _status(
    step_id: str,
    *,
    title: str,
    status: str,
    summary: str,
    evidence: dict[str, str | int | bool] | None = None,
) -> SetupStepStatus:
    return SetupStepStatus(
        step_id=step_id,
        title=title,
        status=status,
        summary=summary,
        evidence=evidence or {},
    )


def _env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_repo_path(repo_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return repo_root / path


def check_service_config(*, repo_root: Path) -> SetupStepStatus:
    env_path = repo_root / ".env"
    if not env_path.exists():
        return _status(
            "service_config",
            title="Service Config",
            status="needs_action",
            summary=".env is missing.",
            evidence={"env_exists": False},
        )
    values = _env_values(env_path)
    workspace = _resolve_repo_path(repo_root, values.get("CEO_WORKSPACE", ""))
    db_path = _resolve_repo_path(repo_root, values.get("CEO_WORKER_DB", ""))
    corpus_dir = _resolve_repo_path(repo_root, values.get("CEO_CORPUS_DIR", ""))
    dry_run_enabled = (
        values.get("CEO_NOT_SEND_MESSAGE") == "1"
        or values.get("CEO_DRY_RUN") == "1"
    )
    missing = [
        label
        for label, path in (
            ("CEO_WORKSPACE", workspace),
            ("CEO_WORKER_DB parent", db_path.parent),
            ("CEO_CORPUS_DIR", corpus_dir),
        )
        if not str(path) or not path.exists()
    ]
    if missing:
        return _status(
            "service_config",
            title="Service Config",
            status="needs_action",
            summary="Missing runtime paths: " + ", ".join(missing),
            evidence={"env_exists": True, "dry_run_enabled": dry_run_enabled},
        )
    if not dry_run_enabled:
        return _status(
            "service_config",
            title="Service Config",
            status="needs_action",
            summary="Dry-run is not enabled.",
            evidence={"env_exists": True, "dry_run_enabled": False},
        )
    return _status(
        "service_config",
        title="Service Config",
        status="done",
        summary="Service config and runtime directories are ready.",
        evidence={"env_exists": True, "dry_run_enabled": True},
    )


def check_data_corpus(*, repo_root: Path) -> SetupStepStatus:
    style_corpus = repo_root / "corpus" / "style_corpus.csv"
    if not style_corpus.exists():
        return _status(
            "data_corpus",
            title="Data Corpus",
            status="needs_action",
            summary="corpus/style_corpus.csv is missing.",
            evidence={"style_corpus_exists": False},
        )
    return _status(
        "data_corpus",
        title="Data Corpus",
        status="done",
        summary="Style corpus exists.",
        evidence={"style_corpus_exists": True},
    )


def check_work_profile(*, repo_root: Path) -> SetupStepStatus:
    profile = repo_root / "profiles" / "work_profile.md"
    evidence = repo_root / "data" / "profile-evidence" / "evidence_index.jsonl"
    style_corpus = repo_root / "corpus" / "style_corpus.csv"
    if not profile.exists():
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="needs_action",
            summary="profiles/work_profile.md is missing.",
            evidence={"profile_exists": False},
        )
    if not evidence.exists():
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="needs_action",
            summary="data/profile-evidence/evidence_index.jsonl is missing.",
        )
    if not style_corpus.exists():
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="needs_action",
            summary="corpus/style_corpus.csv is missing.",
        )
    profile_text = profile.read_text(encoding="utf-8")
    if "/Users/" in profile_text or "Bearer " in profile_text or "session_id=" in profile_text:
        return _status(
            "work_profile",
            title="Work Profile Distillation",
            status="failed",
            summary="profiles/work_profile.md contains sensitive local evidence.",
        )
    return _status(
        "work_profile",
        title="Work Profile Distillation",
        status="done",
        summary="Work profile artifacts are ready.",
    )
```

- [ ] **Step 4: Run checker tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: add setup wizard file checkers"
```

---

### Task 5: MCP And Service Config Safe Actions

**Files:**
- Modify: `app/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 1: Write failing action tests**

Append to `tests/test_setup_wizard.py`:

```python
from app.setup_wizard import run_setup_action


def test_run_setup_service_config_creates_env_and_directories(tmp_path: Path):
    (tmp_path / ".env.example").write_text(
        "CEO_WORKSPACE=\nCEO_WORKER_DB=\nCEO_CORPUS_DIR=\nCEO_NOT_SEND_MESSAGE=\n",
        encoding="utf-8",
    )
    event = run_setup_action(
        "setup_service_config",
        repo_root=tmp_path,
        env={
            "CEO_WORKSPACE": "workspace",
            "CEO_WORKER_DB": "data/auto-reply.sqlite3",
            "CEO_CORPUS_DIR": "corpus",
            "CEO_NOT_SEND_MESSAGE": "1",
        },
    )

    assert event.status == "done"
    assert (tmp_path / ".env").exists()
    assert (tmp_path / "workspace").is_dir()
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "corpus").is_dir()
    assert "CEO_NOT_SEND_MESSAGE=1" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_run_setup_mcp_writes_codex_config(tmp_path: Path):
    codex_config = tmp_path / "config.toml"

    event = run_setup_action(
        "setup_mcp",
        repo_root=tmp_path,
        env={
            "MEMORY_CONNECTOR_URL": "https://memory.example/mcp/",
            "CODEX_CONFIG_PATH": str(codex_config),
            "CLAUDE_CONFIG_PATH": str(tmp_path / "claude.json"),
        },
    )

    assert event.status == "done"
    assert "memory_connector" in codex_config.read_text(encoding="utf-8")
    assert event.evidence["codex_config"] == str(codex_config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py::test_run_setup_service_config_creates_env_and_directories tests/test_setup_wizard.py::test_run_setup_mcp_writes_codex_config -q
```

Expected: FAIL because `run_setup_action` does not exist.

- [ ] **Step 3: Implement safe actions**

Add to `app/setup_wizard.py`:

```python
import json
import os

from app.cli import setup_memory_connector_command
from app.setup_wizard_models import SetupWizardEvent


def run_setup_action(
    action_id: str,
    *,
    repo_root: Path,
    env: dict[str, str] | None = None,
) -> SetupWizardEvent:
    env_values = env or {}
    if action_id == "setup_service_config":
        return _setup_service_config(repo_root=repo_root, env=env_values)
    if action_id == "setup_mcp":
        return _setup_mcp(repo_root=repo_root, env=env_values)
    return SetupWizardEvent(
        step_id="unknown",
        action_id=action_id,
        status="failed",
        summary=f"Unknown setup action: {action_id}",
    )


def _setup_service_config(
    *,
    repo_root: Path,
    env: dict[str, str],
) -> SetupWizardEvent:
    env_path = repo_root / ".env"
    example_path = repo_root / ".env.example"
    if not env_path.exists():
        env_path.write_text(
            example_path.read_text(encoding="utf-8") if example_path.exists() else "",
            encoding="utf-8",
        )
    existing = _env_values(env_path)
    updates = {
        "CEO_WORKSPACE": env.get("CEO_WORKSPACE", existing.get("CEO_WORKSPACE", "workspace")),
        "CEO_WORKER_DB": env.get("CEO_WORKER_DB", existing.get("CEO_WORKER_DB", "data/auto-reply.sqlite3")),
        "CEO_CORPUS_DIR": env.get("CEO_CORPUS_DIR", existing.get("CEO_CORPUS_DIR", "corpus")),
        "CEO_NOT_SEND_MESSAGE": env.get("CEO_NOT_SEND_MESSAGE", existing.get("CEO_NOT_SEND_MESSAGE", "1")),
    }
    merged = {**existing, **updates}
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(merged.items())) + "\n",
        encoding="utf-8",
    )
    workspace = _resolve_repo_path(repo_root, merged["CEO_WORKSPACE"])
    db_path = _resolve_repo_path(repo_root, merged["CEO_WORKER_DB"])
    corpus_dir = _resolve_repo_path(repo_root, merged["CEO_CORPUS_DIR"])
    workspace.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    return SetupWizardEvent(
        step_id="service_config",
        action_id="setup_service_config",
        status="done",
        summary="Created .env and runtime directories.",
        evidence={
            "env_path": str(env_path),
            "workspace": str(workspace),
            "db_parent": str(db_path.parent),
            "corpus_dir": str(corpus_dir),
        },
    )


def _setup_mcp(
    *,
    repo_root: Path,
    env: dict[str, str],
) -> SetupWizardEvent:
    del repo_root
    memory_url = env.get("MEMORY_CONNECTOR_URL", os.getenv("MEMORY_CONNECTOR_URL", ""))
    codex_config = env.get(
        "CODEX_CONFIG_PATH",
        str(Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"),
    )
    claude_config = env.get(
        "CLAUDE_CONFIG_PATH",
        str(Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"),
    )
    if not memory_url:
        return SetupWizardEvent(
            step_id="mcp",
            action_id="setup_mcp",
            status="failed",
            summary="MEMORY_CONNECTOR_URL is missing.",
        )
    result = setup_memory_connector_command(
        memory_url=memory_url,
        codex_config=codex_config,
        claude_config=claude_config,
    )
    return SetupWizardEvent(
        step_id="mcp",
        action_id="setup_mcp",
        status="done",
        summary="Memory Connector MCP config checked.",
        evidence={
            "codex_config": result["codex_config"],
            "claude_status": result["claude_status"],
        },
        stdout_excerpt=redact_setup_output(json.dumps(result, ensure_ascii=False)),
    )
```

- [ ] **Step 4: Run action tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: add setup wizard safe actions"
```

---

### Task 6: Audit Web Wizard Rendering And API

**Files:**
- Modify: `app/audit_web.py`
- Test: `tests/test_audit_web.py`

- [ ] **Step 1: Write failing audit web tests**

Append near the current tutorial tests in `tests/test_audit_web.py`:

```python
def test_render_tutorial_page_shows_wizard_status(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(
        step_id="preflight",
        status="done",
        summary="Python is available",
    )

    html = render_tutorial_page(store=store)

    assert "Initialization Wizard" in html
    assert "Python is available" in html
    assert 'class="setup-step-status setup-status-done"' in html
    assert 'data-action-id="check_cli_components"' in html
    assert "安装检查流程" not in html


def test_tutorial_status_route_returns_json(tmp_path: Path):
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.get("/tutorial/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["steps"][0]["step_id"] == "preflight"
    assert payload["steps"][0]["title"] == "Preflight"


def test_tutorial_check_route_records_step_status(monkeypatch, tmp_path: Path):
    def fake_check(step_id, *, repo_root, store):
        del repo_root, store
        assert step_id == "service_config"
        return SetupStepStatus(
            step_id="service_config",
            title="Service Config",
            status="done",
            summary="ready",
        )

    monkeypatch.setattr(audit_web_module, "check_setup_step", fake_check)
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post("/tutorial/check/service_config")

    assert response.status_code == 200
    assert response.json()["status"] == "done"


def test_tutorial_run_route_records_action_event(monkeypatch, tmp_path: Path):
    def fake_run(action_id, *, repo_root, env):
        del repo_root, env
        assert action_id == "setup_service_config"
        return SetupWizardEvent(
            step_id="service_config",
            action_id="setup_service_config",
            status="done",
            summary="created",
        )

    monkeypatch.setattr(audit_web_module, "run_setup_action", fake_run)
    client = TestClient(create_audit_app(tmp_path / "worker.sqlite3"))

    response = client.post("/tutorial/run/setup_service_config")

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    events = AutoReplyStore(tmp_path / "worker.sqlite3").list_setup_wizard_events("service_config")
    assert events[0]["action_id"] == "setup_service_config"
```

Also add imports at the top of `tests/test_audit_web.py`:

```python
from app.setup_wizard_models import SetupStepStatus, SetupWizardEvent
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_render_tutorial_page_shows_wizard_status tests/test_audit_web.py::test_tutorial_status_route_returns_json tests/test_audit_web.py::test_tutorial_check_route_records_step_status tests/test_audit_web.py::test_tutorial_run_route_records_action_event -q
```

Expected: FAIL because `render_tutorial_page` currently does not accept `store` and the new routes are missing.

- [ ] **Step 3: Wire setup wizard imports**

In `app/audit_web.py`, add imports near existing app imports:

```python
from app.setup_wizard import (
    build_wizard_status,
    check_setup_step,
    confirm_setup_step,
    run_setup_action,
)
from app.setup_wizard_models import SetupStepStatus, SetupWizardEvent
```

- [ ] **Step 4: Replace tutorial renderer with wizard status rendering**

Change the signature and body of `render_tutorial_page` in `app/audit_web.py`:

```python
def render_tutorial_page(*, store: AutoReplyStore | None = None) -> str:
    if store is None:
        configured_db_path = os.environ.get("CEO_WORKER_DB")
        store = AutoReplyStore(Path(configured_db_path or "data/auto-reply.sqlite3"))
    status = build_wizard_status(store)
    steps_html = "".join(_setup_wizard_step_html(step) for step in status.steps)
    body = (
        "<section class=\"card tutorial-intro\">"
        "<h2>Initialization Wizard</h2>"
        "<p class=\"muted\">"
        "This wizard checks and configures the local CEO Agent Service setup. "
        "A step is checked only after the system verifies it."
        "</p>"
        "</section>"
        "<section class=\"card\">"
        "<div class=\"card-head\">"
        "<h2>Setup steps</h2>"
        "<div class=\"tutorial-links\">"
        "<a class=\"tutorial-link\" href=\"/config?tab=system\">系统参数</a>"
        "<a class=\"tutorial-link\" href=\"/tasks\">Tasks</a>"
        "<a class=\"tutorial-link\" href=\"/logs\">Logs</a>"
        "</div>"
        "</div>"
        f"<ol class=\"tutorial-steps setup-wizard-steps\">{steps_html}</ol>"
        "</section>"
    )
    return render_page("Tutorial", body, active_nav="tutorial")
```

- [ ] **Step 5: Add wizard step HTML helper**

Add below the tutorial renderer:

```python
def _setup_wizard_step_html(step: SetupStepStatus) -> str:
    action_html = "".join(
        "<form method=\"post\" action=\"/tutorial/"
        f"{'check' if action.kind == 'check' else 'run' if action.kind == 'run' else 'confirm'}"
        f"/{escape(action.id if action.kind == 'run' else step.step_id)}\">"
        f"<button type=\"submit\" data-action-id=\"{escape(action.id)}\">"
        f"{escape(action.label)}</button>"
        "</form>"
        for action in step.available_actions
        if action.kind != "confirm" or step.manual_confirmation_allowed
    )
    evidence_html = "".join(
        "<li>"
        f"<code>{escape(str(key))}</code>: {escape(str(value))}"
        "</li>"
        for key, value in step.evidence.items()
    )
    return (
        "<li class=\"tutorial-step setup-wizard-step\">"
        "<div class=\"tutorial-step-number\" aria-hidden=\"true\"></div>"
        "<div class=\"tutorial-step-body\">"
        "<div class=\"tutorial-step-head\">"
        f"<h3>{escape(step.title)}</h3>"
        f"<span class=\"setup-step-status setup-status-{escape(step.status)}\">"
        f"{escape(step.status)}</span>"
        "</div>"
        f"<p>{escape(step.summary or 'Not checked yet.')}</p>"
        f"<ul class=\"tutorial-list\">{evidence_html}</ul>"
        f"<div class=\"tutorial-links\">{action_html}</div>"
        "</div>"
        "</li>"
    )
```

- [ ] **Step 6: Add CSS for wizard status pills**

In `CSS` near tutorial styles, add:

```css
.setup-step-status{display:inline-flex;align-items:center;height:24px;padding:0 8px;border:1px solid var(--hairline);border-radius:999px;background:var(--surface-soft);font-family:"Geist Mono","SF Mono",Menlo,Consolas,monospace;font-size:11px;font-weight:800;line-height:1;white-space:nowrap}
.setup-status-done{background:#ddfff6;border-color:rgba(0,180,138,.46);color:#005b49}
.setup-status-running,.setup-status-checking{background:rgba(55,114,207,.10);border-color:rgba(55,114,207,.24);color:#245aa5}
.setup-status-needs_action{background:rgba(195,125,13,.12);border-color:rgba(195,125,13,.24);color:#8a5a08}
.setup-status-failed,.setup-status-blocked{background:rgba(212,86,86,.12);border-color:rgba(212,86,86,.24);color:#9a2f2f}
.setup-wizard-step form{margin:0}
```

- [ ] **Step 7: Add status and action routes**

In `create_audit_app`, change tutorial route and add new routes:

```python
    @app.get("/tutorial", response_class=HTMLResponse)
    def tutorial_page() -> str:
        return render_tutorial_page(store=AutoReplyStore(db_path))

    @app.get("/tutorial/status")
    def tutorial_status() -> JSONResponse:
        return JSONResponse(build_wizard_status(AutoReplyStore(db_path)).model_dump())

    @app.post("/tutorial/check/{step_id}")
    def tutorial_check(step_id: str) -> JSONResponse:
        store = AutoReplyStore(db_path)
        status = check_setup_step(step_id, repo_root=_repo_root(), store=store)
        store.upsert_setup_wizard_step(
            step_id=status.step_id,
            status=status.status,
            summary=status.summary,
        )
        return JSONResponse(status.model_dump())

    @app.post("/tutorial/run/{action_id}")
    def tutorial_run(action_id: str) -> JSONResponse:
        store = AutoReplyStore(db_path)
        event = run_setup_action(action_id, repo_root=_repo_root(), env=dict(os.environ))
        store.record_setup_wizard_event(
            step_id=event.step_id,
            action_id=event.action_id,
            status=event.status,
            summary=event.summary,
            evidence_json=json.dumps(event.evidence, ensure_ascii=False),
            stdout_excerpt=event.stdout_excerpt,
            stderr_excerpt=event.stderr_excerpt,
        )
        if event.status == "done":
            store.upsert_setup_wizard_step(
                step_id=event.step_id,
                status="done",
                summary=event.summary,
            )
        return JSONResponse(event.model_dump())

    @app.post("/tutorial/confirm/{step_id}")
    async def tutorial_confirm(step_id: str, request: Request) -> JSONResponse:
        payload = await request.json()
        store = AutoReplyStore(db_path)
        event = confirm_setup_step(
            step_id,
            store=store,
            confirmed_by=str(payload.get("confirmed_by") or "local-user"),
            evidence={
                key: str(value)
                for key, value in (payload.get("evidence") or {}).items()
            },
        )
        store.record_setup_wizard_event(
            step_id=event.step_id,
            action_id=event.action_id,
            status=event.status,
            summary=event.summary,
            evidence_json=json.dumps(event.evidence, ensure_ascii=False),
            stdout_excerpt=event.stdout_excerpt,
            stderr_excerpt=event.stderr_excerpt,
        )
        return JSONResponse(event.model_dump())
```

Add this helper in `app/audit_web.py` near other private helpers:

```python
def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent
```

- [ ] **Step 8: Run targeted audit web tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_render_tutorial_page_shows_wizard_status tests/test_audit_web.py::test_tutorial_status_route_returns_json tests/test_audit_web.py::test_tutorial_check_route_records_step_status tests/test_audit_web.py::test_tutorial_run_route_records_action_event -q
```

Expected: PASS.

- [ ] **Step 9: Run existing tutorial/nav tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_audit_web.py::test_top_nav_highlights_current_page_and_disables_current_link tests/test_audit_web.py::test_tutorial_route_renders_first_time_setup -q
```

Expected: PASS after updating old text assertions from static tutorial content to wizard content.

- [ ] **Step 10: Commit**

Run:

```bash
git add app/audit_web.py tests/test_audit_web.py
git commit -m "feat: render setup wizard in audit web"
```

---

### Task 7: Check Dispatch, Dry-Run Gating, And Backlog Verification

**Files:**
- Modify: `app/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 1: Write failing tests for checker dispatch and dry-run backlog**

Append to `tests/test_setup_wizard.py`:

```python
from app.setup_wizard import check_setup_step


def test_check_setup_step_dispatches_service_config(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "CEO_WORKSPACE=workspace\nCEO_WORKER_DB=data/auto-reply.sqlite3\nCEO_CORPUS_DIR=corpus\nCEO_NOT_SEND_MESSAGE=1\n",
        encoding="utf-8",
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "corpus").mkdir()
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    result = check_setup_step("service_config", repo_root=tmp_path, store=store)

    assert result.status == "done"


def test_check_dry_run_fails_when_reply_task_backlog_exists(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="群",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-06-12 09:00:00",
        trigger_sender="Mina",
        trigger_text="@Derek ping",
    )

    result = check_setup_step("dry_run", repo_root=tmp_path, store=store)

    assert result.status == "needs_action"
    assert result.summary == "Unresolved reply task backlog exists."
    assert result.evidence["pending_reply_tasks"] == 1


def test_check_dry_run_passes_without_failed_or_processing_backlog(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    result = check_setup_step("dry_run", repo_root=tmp_path, store=store)

    assert result.status == "done"
    assert result.summary == "Dry-run audit state has no unresolved backlog."
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py::test_check_setup_step_dispatches_service_config tests/test_setup_wizard.py::test_check_dry_run_fails_when_reply_task_backlog_exists tests/test_setup_wizard.py::test_check_dry_run_passes_without_failed_or_processing_backlog -q
```

Expected: FAIL because `check_setup_step` and dry-run checker are incomplete.

- [ ] **Step 3: Implement checker dispatch and dry-run checker**

Add to `app/setup_wizard.py`:

```python
def check_setup_step(
    step_id: str,
    *,
    repo_root: Path,
    store: AutoReplyStore,
) -> SetupStepStatus:
    if step_id == "service_config":
        return check_service_config(repo_root=repo_root)
    if step_id == "data_corpus":
        return check_data_corpus(repo_root=repo_root)
    if step_id == "work_profile":
        return check_work_profile(repo_root=repo_root)
    if step_id == "dry_run":
        return check_dry_run(store=store)
    definition = get_step_definition(step_id)
    return SetupStepStatus(
        step_id=definition.id,
        title=definition.title,
        status="needs_action",
        summary=f"No automated checker is implemented for {definition.title}.",
        manual_confirmation_allowed=False,
    )


def check_dry_run(*, store: AutoReplyStore) -> SetupStepStatus:
    pending = store.count_reply_tasks("pending")
    processing = store.count_reply_tasks("processing")
    failed = store.count_reply_tasks("failed")
    if pending or processing or failed:
        return SetupStepStatus(
            step_id="dry_run",
            title="Dry-Run Validation",
            status="needs_action",
            summary="Unresolved reply task backlog exists.",
            evidence={
                "pending_reply_tasks": pending,
                "processing_reply_tasks": processing,
                "failed_reply_tasks": failed,
            },
        )
    return SetupStepStatus(
        step_id="dry_run",
        title="Dry-Run Validation",
        status="done",
        summary="Dry-run audit state has no unresolved backlog.",
        evidence={
            "pending_reply_tasks": pending,
            "processing_reply_tasks": processing,
            "failed_reply_tasks": failed,
        },
    )
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: check setup wizard dry-run state"
```

---

### Task 8: Command Actions For Corpus, Profile, And Dry-Run

**Files:**
- Modify: `app/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 1: Write failing command action tests**

Append to `tests/test_setup_wizard.py`:

```python
def test_run_command_action_records_success(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(args, cwd, text, capture_output, timeout):
        calls.append((args, cwd, text, capture_output, timeout))
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("app.setup_wizard.subprocess.run", fake_run)

    event = run_setup_action("build_data_corpus", repo_root=tmp_path, env={})

    assert event.status == "done"
    assert event.step_id == "data_corpus"
    assert calls[0][0][:3] == [".venv/bin/ceo-agent", "build-corpus", "--workspace"]
    assert event.stdout_excerpt == "ok\n"


def test_run_command_action_redacts_failure_output(monkeypatch, tmp_path: Path):
    def fake_run(args, cwd, text, capture_output, timeout):
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="token=secret path=/Users/derek/private.md",
        )

    monkeypatch.setattr("app.setup_wizard.subprocess.run", fake_run)

    event = run_setup_action("run_dry_run", repo_root=tmp_path, env={})

    assert event.status == "failed"
    assert "secret" not in event.stderr_excerpt
    assert "/Users/derek/private.md" not in event.stderr_excerpt
```

Add import:

```python
import subprocess
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py::test_run_command_action_records_success tests/test_setup_wizard.py::test_run_command_action_redacts_failure_output -q
```

Expected: FAIL because command actions are not implemented.

- [ ] **Step 3: Add subprocess action dispatch**

Modify `run_setup_action` in `app/setup_wizard.py`:

```python
    if action_id == "build_data_corpus":
        return _run_command_action(
            step_id="data_corpus",
            action_id=action_id,
            repo_root=repo_root,
            args=[
                ".venv/bin/ceo-agent",
                "build-corpus",
                "--workspace",
                env_values.get("CEO_WORKSPACE", "workspace"),
                "--corpus-dir",
                env_values.get("CEO_CORPUS_DIR", "corpus"),
            ],
        )
    if action_id == "build_work_profile":
        return _run_command_action(
            step_id="work_profile",
            action_id=action_id,
            repo_root=repo_root,
            args=[
                ".venv/bin/ceo-agent",
                "build-work-profile",
                "--workspace",
                env_values.get("CEO_WORKSPACE", "workspace"),
                "--corpus-dir",
                env_values.get("CEO_CORPUS_DIR", "corpus"),
            ],
        )
    if action_id == "run_dry_run":
        return _run_command_action(
            step_id="dry_run",
            action_id=action_id,
            repo_root=repo_root,
            args=[
                ".venv/bin/ceo-agent",
                "run-once",
                "--not-send-message",
            ],
        )
```

Add helper:

```python
import subprocess


def _run_command_action(
    *,
    step_id: str,
    action_id: str,
    repo_root: Path,
    args: list[str],
    timeout_seconds: int = 900,
) -> SetupWizardEvent:
    result = subprocess.run(
        args,
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    status = "done" if result.returncode == 0 else "failed"
    return SetupWizardEvent(
        step_id=step_id,
        action_id=action_id,
        status=status,
        summary=(
            f"{action_id} completed."
            if status == "done"
            else f"{action_id} failed with exit code {result.returncode}."
        ),
        evidence={"returncode": result.returncode},
        stdout_excerpt=redact_setup_output(result.stdout[-4000:]),
        stderr_excerpt=redact_setup_output(result.stderr[-4000:]),
    )
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add app/setup_wizard.py tests/test_setup_wizard.py
git commit -m "feat: run setup wizard command actions"
```

---

### Task 9: Live Send Verification And Computer Use Evidence Bridge

**Files:**
- Modify: `app/store.py`
- Modify: `app/setup_wizard.py`
- Test: `tests/test_setup_wizard.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing live-send verifier tests**

Append to `tests/test_setup_wizard.py`:

```python
from app.setup_wizard import confirm_setup_step, record_computer_use_send_evidence


def test_check_live_send_passes_from_sent_reply(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "测试消息",
        send_result_json='{"processQueryKey":"ok"}',
    )

    result = check_setup_step("live_send", repo_root=tmp_path, store=store)

    assert result.status == "done"
    assert result.summary == "A DingTalk send has structured success evidence."


def test_check_live_send_requests_computer_use_when_no_structured_evidence(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    result = check_setup_step("live_send", repo_root=tmp_path, store=store)

    assert result.status == "needs_action"
    assert result.summary == "No structured DingTalk send success evidence found; inspect DingTalk with Computer Use."
    assert result.evidence["computer_use_recommended"] is True
    assert result.manual_confirmation_allowed is False


def test_computer_use_success_evidence_marks_live_send_done(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    event = record_computer_use_send_evidence(
        store=store,
        success=True,
        page_title="DingTalk",
        page_signal="发送成功",
        inspected_by="computer-use",
    )
    result = check_setup_step("live_send", repo_root=tmp_path, store=store)

    assert event.status == "done"
    assert event.summary == "Computer Use verified DingTalk send success."
    assert result.status == "done"
    assert result.summary == "Computer Use verified DingTalk send success."


def test_computer_use_inconclusive_allows_manual_confirmation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    event = record_computer_use_send_evidence(
        store=store,
        success=False,
        page_title="DingTalk",
        page_signal="无法确认",
        inspected_by="computer-use",
    )
    result = check_setup_step("live_send", repo_root=tmp_path, store=store)

    assert event.status == "failed"
    assert result.status == "needs_action"
    assert result.manual_confirmation_allowed is True


def test_manual_confirmation_is_rejected_for_non_fallback_step(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    event = confirm_setup_step(
        "service_config",
        store=store,
        confirmed_by="Derek",
        evidence={"page_signal": "ready"},
    )

    assert event.status == "failed"
    assert event.summary == "Manual confirmation is not allowed for service_config."


def test_manual_confirmation_is_allowed_after_inconclusive_computer_use(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    record_computer_use_send_evidence(
        store=store,
        success=False,
        page_title="DingTalk",
        page_signal="无法确认",
        inspected_by="computer-use",
    )

    event = confirm_setup_step(
        "live_send",
        store=store,
        confirmed_by="Derek",
        evidence={"page_signal": "DingTalk page showed success"},
    )

    assert event.status == "done"
    assert event.summary == "Manually confirmed by Derek."
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py::test_check_live_send_passes_from_sent_reply tests/test_setup_wizard.py::test_check_live_send_requests_computer_use_when_no_structured_evidence tests/test_setup_wizard.py::test_computer_use_success_evidence_marks_live_send_done tests/test_setup_wizard.py::test_computer_use_inconclusive_allows_manual_confirmation tests/test_setup_wizard.py::test_manual_confirmation_is_rejected_for_non_fallback_step tests/test_setup_wizard.py::test_manual_confirmation_is_allowed_after_inconclusive_computer_use -q
```

Expected: FAIL because live-send checking, Computer Use evidence, and confirmation are not implemented.

- [ ] **Step 3: Add sent reply count and latest Computer Use evidence store helpers**

Append tests to `tests/test_store.py`:

```python
def test_count_sent_replies_counts_live_send_evidence(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.count_sent_replies() == 0

    store.record_sent_reply("cid-1", "msg-1", "hello")

    assert store.count_sent_replies() == 1


def test_latest_setup_wizard_event_returns_latest_for_action(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_setup_wizard_event(
        step_id="live_send",
        action_id="computer_use_send_evidence",
        status="failed",
        summary="old",
    )
    store.record_setup_wizard_event(
        step_id="live_send",
        action_id="computer_use_send_evidence",
        status="done",
        summary="new",
        evidence_json='{"page_signal": "发送成功"}',
    )

    event = store.get_latest_setup_wizard_event(
        step_id="live_send",
        action_id="computer_use_send_evidence",
    )

    assert event["status"] == "done"
    assert event["summary"] == "new"
    assert event["evidence_json"] == '{"page_signal": "发送成功"}'
```

Add to `app/store.py`:

```python
    def count_sent_replies(self) -> int:
        with self._connect() as db:
            row = db.execute("select count(*) as count from sent_replies").fetchone()
            return int(row["count"])

    def get_latest_setup_wizard_event(
        self,
        *,
        step_id: str,
        action_id: str,
    ) -> dict[str, str | int] | None:
        with self._connect() as db:
            row = db.execute(
                """
                select id, step_id, action_id, status, summary, evidence_json,
                       stdout_excerpt, stderr_excerpt, started_at, finished_at
                from setup_wizard_events
                where step_id=? and action_id=?
                order by id desc
                limit 1
                """,
                (step_id, action_id),
            ).fetchone()
            return dict(row) if row is not None else None
```

Run:

```bash
.venv/bin/python -m pytest tests/test_store.py::test_count_sent_replies_counts_live_send_evidence tests/test_store.py::test_latest_setup_wizard_event_returns_latest_for_action -q
```

Expected: PASS after adding both store methods.

- [ ] **Step 4: Add live-send checker, Computer Use evidence recording, and confirmation**

Add to `app/setup_wizard.py`:

```python
import json


def check_live_send(*, store: AutoReplyStore) -> SetupStepStatus:
    sent_count = store.count_sent_replies()
    if sent_count > 0:
        return SetupStepStatus(
            step_id="live_send",
            title="Live Send Verification",
            status="done",
            summary="A DingTalk send has structured success evidence.",
            evidence={"sent_replies": sent_count},
        )
    computer_use_event = store.get_latest_setup_wizard_event(
        step_id="live_send",
        action_id="computer_use_send_evidence",
    )
    if computer_use_event and computer_use_event["status"] == "done":
        evidence = json.loads(str(computer_use_event["evidence_json"] or "{}"))
        return SetupStepStatus(
            step_id="live_send",
            title="Live Send Verification",
            status="done",
            summary="Computer Use verified DingTalk send success.",
            evidence={
                "sent_replies": 0,
                "computer_use_verified": True,
                "page_signal": str(evidence.get("page_signal") or ""),
            },
        )
    if computer_use_event and computer_use_event["status"] == "failed":
        evidence = json.loads(str(computer_use_event["evidence_json"] or "{}"))
        return SetupStepStatus(
            step_id="live_send",
            title="Live Send Verification",
            status="needs_action",
            summary="Computer Use could not verify DingTalk send success.",
            evidence={
                "sent_replies": 0,
                "computer_use_verified": False,
                "page_signal": str(evidence.get("page_signal") or ""),
            },
            manual_confirmation_allowed=True,
        )
    return SetupStepStatus(
        step_id="live_send",
        title="Live Send Verification",
        status="needs_action",
        summary="No structured DingTalk send success evidence found; inspect DingTalk with Computer Use.",
        evidence={"sent_replies": 0, "computer_use_recommended": True},
        manual_confirmation_allowed=False,
    )


def record_computer_use_send_evidence(
    *,
    store: AutoReplyStore,
    success: bool,
    page_title: str,
    page_signal: str,
    inspected_by: str,
) -> SetupWizardEvent:
    status = "done" if success else "failed"
    summary = (
        "Computer Use verified DingTalk send success."
        if success
        else "Computer Use could not verify DingTalk send success."
    )
    evidence = {
        "page_title": page_title,
        "page_signal": page_signal,
        "inspected_by": inspected_by,
    }
    store.record_setup_wizard_event(
        step_id="live_send",
        action_id="computer_use_send_evidence",
        status=status,
        summary=summary,
        evidence_json=json.dumps(evidence, ensure_ascii=False),
    )
    if success:
        store.upsert_setup_wizard_step(
            step_id="live_send",
            status="done",
            summary=summary,
        )
    return SetupWizardEvent(
        step_id="live_send",
        action_id="computer_use_send_evidence",
        status=status,
        summary=summary,
        evidence=evidence,
    )


def confirm_setup_step(
    step_id: str,
    *,
    store: AutoReplyStore,
    confirmed_by: str,
    evidence: dict[str, str],
) -> SetupWizardEvent:
    if step_id != "live_send":
        return SetupWizardEvent(
            step_id=step_id,
            action_id=f"confirm_{step_id}",
            status="failed",
            summary=f"Manual confirmation is not allowed for {step_id}.",
        )
    status = check_live_send(store=store)
    if not status.manual_confirmation_allowed:
        return SetupWizardEvent(
            step_id=step_id,
            action_id="confirm_live_send",
            status="failed",
            summary="Manual confirmation is not currently available.",
        )
    store.upsert_setup_wizard_step(
        step_id=step_id,
        status="done",
        summary=f"Manually confirmed by {confirmed_by}.",
        manual_confirmed_by=confirmed_by,
    )
    return SetupWizardEvent(
        step_id=step_id,
        action_id="confirm_live_send",
        status="done",
        summary=f"Manually confirmed by {confirmed_by}.",
        evidence=evidence,
    )
```

Update `check_setup_step`:

```python
    if step_id == "live_send":
        return check_live_send(store=store)
```

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py tests/test_store.py::test_count_sent_replies_counts_live_send_evidence tests/test_store.py::test_latest_setup_wizard_event_returns_latest_for_action -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add app/setup_wizard.py app/store.py tests/test_setup_wizard.py tests/test_store.py
git commit -m "feat: verify live send setup state"
```

---

### Task 10: Documentation And Verification

**Files:**
- Modify: `docs/agent-installation-runbook.md`
- Modify: `README.md`
- Test: existing test suite

- [ ] **Step 1: Update runbook to point to wizard**

In `docs/agent-installation-runbook.md`, after the opening paragraph, add:

```markdown
For local first-time setup, prefer the audit web initialization wizard at
`/tutorial`. The wizard follows this runbook, runs safe checks/actions itself,
and records local evidence before checking off each step. Use the runbook below
as the operational contract and fallback detail when a wizard step reports a
blocker.
```

- [ ] **Step 2: Update README Agent 安装入口**

In `README.md`, under `## Agent 安装入口`, add:

```markdown
如果审计 Web UI 已可启动，优先打开 `/tutorial` 使用初始化向导。向导会按
runbook 自动检测和配置可安全自动化的部分，包括 MCP、`.env`、本地目录、
corpus/profile 产物和 dry-run 审计状态；只有外部可见动作或无法自动证明的状态才会要求人工确认。
```

- [ ] **Step 3: Run targeted wizard tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py tests/test_store.py::test_setup_wizard_step_state_round_trips tests/test_store.py::test_setup_wizard_event_history_round_trips tests/test_audit_web.py::test_render_tutorial_page_shows_wizard_status tests/test_audit_web.py::test_tutorial_status_route_returns_json tests/test_audit_web.py::test_tutorial_check_route_records_step_status tests/test_audit_web.py::test_tutorial_run_route_records_action_event -q
```

Expected: PASS.

- [ ] **Step 4: Run broader affected tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_setup_wizard.py tests/test_store.py tests/test_audit_web.py -q
```

Expected: PASS or only pre-existing unrelated failures. If failures occur, inspect each failure and fix wizard-related regressions before continuing.

- [ ] **Step 5: Verify web route manually**

Start a temporary audit web server on a free port:

```bash
.venv/bin/python -m app.cli audit-web --host 127.0.0.1 --port 8766
```

Open:

```text
http://127.0.0.1:8766/tutorial
```

Expected:

- Page title is `Tutorial`.
- It shows `Initialization Wizard`.
- Preflight is not blocked.
- Later dependent steps are blocked until dependencies are done.
- No horizontal overflow at desktop or mobile width.

Stop the temporary server with `Ctrl-C`.

- [ ] **Step 6: Commit**

Run:

```bash
git add README.md docs/agent-installation-runbook.md tests/test_setup_wizard.py tests/test_store.py tests/test_audit_web.py app/setup_wizard.py app/setup_wizard_models.py app/store.py app/audit_web.py
git commit -m "docs: document initialization wizard"
```

---

## Self-Review

Spec coverage:

- Stateful wizard: Tasks 1, 2, 3, 6.
- Verified checkmarks: Tasks 3, 4, 6, 7.
- Backend checker/action endpoints: Task 6.
- Local persistence: Task 2.
- Safe automated setup, including MCP: Task 5.
- Current-state inspection before questions: Tasks 4, 7, 9.
- Structured DingTalk evidence, Computer Use evidence, and manual fallback: Task 9.
- Dry-run default and launchd/live-send gating: Tasks 3, 7, 9.
- Tests: every task starts with failing tests and targeted pass commands.

Computer Use note:

- Computer Use itself is an agent capability, not a normal app runtime import.
  Task 9 implements the app-side evidence bridge: after an agent inspects the
  DingTalk page with Computer Use, it records the page title/signal into the
  wizard, and the wizard uses that evidence before allowing manual fallback.
