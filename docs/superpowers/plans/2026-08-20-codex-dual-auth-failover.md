# Codex Dual-Auth Failover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every Codex-backed CEO Agent workload running when local Codex OAuth fails by safely retrying the same Agent run through native `codex exec` with an isolated OpenAI API credential.

**Architecture:** Add provider-neutral runtime contracts, append-only runtime attempts, route-scoped health pauses, a Codex CLI adapter with isolated OAuth/API environments, and one router shared by all Codex callers. The existing Consumer/Audit stream parser remains the authority for effect events; the router may change routes only when persisted evidence proves no effectful call started.

**Tech Stack:** Python 3.11+, Pydantic, SQLite, native Codex CLI JSONL, pytest, FastAPI audit UI, macOS launchd.

**Depends on:** `docs/superpowers/specs/2026-08-20-agent-runtime-failover-design.md`

---

## File map

- Create `app/agent_runtime_contracts.py`: provider-neutral route, attempt, failure, capability, and request types.
- Create `app/agent_runtime_config.py`: validated route order and secret-safe environment configuration.
- Create `app/codex_runtime_adapter.py`: Codex command/environment construction and Codex-specific failure normalization.
- Create `app/agent_runtime_router.py`: route eligibility, route pauses, and bounded failover decisions.
- Modify `app/store.py`: runtime-attempt, route-health, and per-route conversation-session persistence.
- Modify `app/agent_turn_runner.py`: route-aware Consumer/Audit execution while preserving effect accounting and reconciliation.
- Modify `app/codex_runner.py`: accept explicit model/provider options without reading fallback secrets itself.
- Modify the remaining Codex callers in `app/codex_decision.py`, `app/structured_agent.py`, `app/task_agent.py`, `app/task_memory_backfill.py`, `app/meeting_alignment_agent.py`, `app/weekly_okr_report.py`, `app/codex_memory_write.py`, and `app/wechat/`: use the shared router.
- Modify `app/cli.py`, `app/setup_wizard.py`, `app/audit_web.py`, `app/quality_gate.py`, `.env.example`, and `README.md`: configuration, probes, visibility, and operations.
- Create focused tests in `tests/test_agent_runtime_contracts.py`, `tests/test_agent_runtime_config.py`, `tests/test_codex_runtime_adapter.py`, and `tests/test_agent_runtime_router.py`; extend existing Store, runner, UI, setup, and recovery tests.

### Task 1: Define provider-neutral runtime contracts

**Files:**
- Create: `app/agent_runtime_contracts.py`
- Create: `tests/test_agent_runtime_contracts.py`

- [ ] **Step 1: Write failing tests for strict route, failure, and safety types**

```python
from app.agent_runtime_contracts import (
    CredentialMode,
    RuntimeFailure,
    RuntimeFailureClass,
    RuntimeKind,
    RuntimeRoute,
)


def test_runtime_route_contains_no_secret_material():
    route = RuntimeRoute(
        name="codex_api",
        runtime_kind=RuntimeKind.CODEX_CLI,
        credential_mode=CredentialMode.SERVICE_API,
        model="gpt-5.5",
    )
    assert route.name == "codex_api"
    assert "key" not in route.model_dump()


def test_unclassified_failure_is_fail_closed():
    failure = RuntimeFailure(
        failure_class=RuntimeFailureClass.UNCLASSIFIED,
        code="runtime_unclassified",
        detail="safe detail",
    )
    assert failure.retryable_on_same_route is False
    assert failure.failover_permitted is False
    assert failure.route_pause_required is False
```

- [ ] **Step 2: Run the new test and verify the module is missing**

Run: `.venv/bin/pytest tests/test_agent_runtime_contracts.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: app.agent_runtime_contracts`.

- [ ] **Step 3: Implement the complete contract module**

```python
from __future__ import annotations

from enum import StrEnum
from typing import FrozenSet

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RuntimeKind(StrEnum):
    CODEX_CLI = "codex_cli"
    CLAUDE_CLI = "claude_cli"


class CredentialMode(StrEnum):
    LOCAL_OAUTH = "local_oauth"
    SERVICE_API = "service_api"


class RuntimeFailureClass(StrEnum):
    AUTHENTICATION = "authentication"
    CAPACITY = "capacity"
    TRANSPORT = "transport"
    CAPABILITY = "capability"
    SESSION = "session"
    RESULT = "result"
    PROCESS = "process"
    UNCLASSIFIED = "unclassified"


class RuntimeRoute(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    runtime_kind: RuntimeKind
    credential_mode: CredentialMode
    model: str

    @field_validator("name", "model")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-empty")
        return value


class RuntimeFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    failure_class: RuntimeFailureClass
    code: str
    detail: str
    retryable_on_same_route: bool = False
    failover_permitted: bool = False
    route_pause_required: bool = False


class RuntimeCapabilitySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    route_name: str
    capabilities: FrozenSet[str] = Field(default_factory=frozenset)
    healthy: bool
    checked_at: str
    expires_at: str
    failure: RuntimeFailure | None = None


class RuntimeSelectionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    required_capabilities: FrozenSet[str] = Field(default_factory=frozenset)
    side_effect_state: str = "none"
    effect_started_count: int = 0
    has_confirmed_receipt: bool = False
    recovery_phase: str = ""
```

- [ ] **Step 4: Run contract tests**

Run: `.venv/bin/pytest tests/test_agent_runtime_contracts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the contracts**

```bash
git add app/agent_runtime_contracts.py tests/test_agent_runtime_contracts.py
git commit -m "feat: define agent runtime failover contracts"
```

### Task 2: Load route configuration without leaking fallback credentials

**Files:**
- Create: `app/agent_runtime_config.py`
- Create: `tests/test_agent_runtime_config.py`
- Modify: `app/config.py:187-202`
- Create: `tests/test_config.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing configuration and redaction tests**

```python
from app.agent_runtime_config import load_runtime_config


def test_default_runtime_uses_only_codex_oauth():
    config = load_runtime_config({})
    assert [route.name for route in config.routes] == ["codex_oauth"]


def test_dual_auth_requires_a_private_api_key():
    config = load_runtime_config(
        {
            "CEO_AGENT_RUNTIME_ROUTES": "codex_oauth,codex_api",
            "CEO_CODEX_API_MODEL": "gpt-5.5",
            "CEO_CODEX_API_KEY": "secret-value",
        }
    )
    assert config.routes[1].name == "codex_api"
    assert "secret-value" not in repr(config)
    assert config.secret_for("codex_api").get_secret_value() == "secret-value"
```

- [ ] **Step 2: Verify the tests fail**

Run: `.venv/bin/pytest tests/test_agent_runtime_config.py -q`

Expected: FAIL because `load_runtime_config` does not exist.

- [ ] **Step 3: Implement validated route configuration**

```python
from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, SecretStr

from app.agent_runtime_contracts import CredentialMode, RuntimeKind, RuntimeRoute
from app.config import parse_duration_value


class AgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    routes: tuple[RuntimeRoute, ...]
    secrets: dict[str, SecretStr]
    probe_interval: timedelta
    retry_delay: timedelta

    def secret_for(self, route_name: str) -> SecretStr | None:
        return self.secrets.get(route_name)


def load_runtime_config(env: Mapping[str, str]) -> AgentRuntimeConfig:
    names = tuple(
        item.strip()
        for item in env.get("CEO_AGENT_RUNTIME_ROUTES", "codex_oauth").split(",")
        if item.strip()
    )
    if not names or len(names) != len(set(names)):
        raise ValueError("CEO_AGENT_RUNTIME_ROUTES must contain unique routes")
    supported = {"codex_oauth", "codex_api"}
    unknown = set(names) - supported
    if unknown:
        raise ValueError(f"unsupported runtime routes: {sorted(unknown)}")
    model = env.get("CEO_CODEX_MODEL", "gpt-5.5").strip()
    api_model = env.get("CEO_CODEX_API_MODEL", model).strip()
    routes = []
    secrets: dict[str, SecretStr] = {}
    for name in names:
        if name == "codex_oauth":
            routes.append(RuntimeRoute(
                name=name,
                runtime_kind=RuntimeKind.CODEX_CLI,
                credential_mode=CredentialMode.LOCAL_OAUTH,
                model=model,
            ))
        else:
            raw_secret = env.get("CEO_CODEX_API_KEY", "").strip()
            if not raw_secret:
                raise ValueError("codex_api requires CEO_CODEX_API_KEY")
            routes.append(RuntimeRoute(
                name=name,
                runtime_kind=RuntimeKind.CODEX_CLI,
                credential_mode=CredentialMode.SERVICE_API,
                model=api_model,
            ))
            secrets[name] = SecretStr(raw_secret)
    return AgentRuntimeConfig(
        routes=tuple(routes),
        secrets=secrets,
        probe_interval=parse_duration_value(
            "CEO_RUNTIME_PROBE_INTERVAL",
            env.get("CEO_RUNTIME_PROBE_INTERVAL"),
            timedelta(minutes=5),
        ),
        retry_delay=parse_duration_value(
            "CEO_RUNTIME_ROUTE_RETRY_DELAY",
            env.get("CEO_RUNTIME_ROUTE_RETRY_DELAY"),
            timedelta(minutes=30),
        ),
    )
```

Refactor the existing duration parser without changing its behavior:

```python
def parse_duration_value(name: str, value: str | None,
                         default: timedelta) -> timedelta:
    if value is None:
        return default
    text = value.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = text[-1:]
    if unit not in units:
        raise ValueError(f"{name} must end with one of: s, m, h, d")
    amount_text = text[:-1]
    if not amount_text.isdigit():
        raise ValueError(f"{name} must use an integer duration like 30m or 1h")
    return timedelta(seconds=int(amount_text) * units[unit])


def env_duration(name: str, default: timedelta) -> timedelta:
    return parse_duration_value(name, os.getenv(name), default)
```

- [ ] **Step 4: Document disabled-by-default configuration**

Add exactly these commented entries to `.env.example` below the Codex model settings:

```dotenv
# Ordered Agent runtime routes. Keep codex_api disabled until its live probe passes.
# CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api
# CEO_CODEX_API_MODEL=gpt-5.5
# CEO_CODEX_API_KEY=
# CEO_RUNTIME_PROBE_INTERVAL=5m
# CEO_RUNTIME_ROUTE_RETRY_DELAY=30m
```

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_agent_runtime_config.py tests/test_config.py -q`

Expected: PASS.

```bash
git add app/agent_runtime_config.py app/config.py tests/test_agent_runtime_config.py tests/test_config.py .env.example
git commit -m "feat: configure isolated Codex fallback route"
```

### Task 3: Persist runtime attempts, route sessions, and route pauses

**Files:**
- Modify: `app/store.py:332-381`
- Modify: `app/store.py:941-1010`
- Modify: `app/store.py:3259-3635`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write failing Store tests**

```python
def test_runtime_attempt_claim_is_ordered_and_idempotent(store, claimed_agent_run):
    first = store.claim_agent_runtime_attempt(
        claimed_agent_run.id,
        route_name="codex_oauth",
        runtime_kind="codex_cli",
        credential_mode="local_oauth",
        model="gpt-5.5",
    )
    repeated = store.claim_agent_runtime_attempt(
        claimed_agent_run.id,
        route_name="codex_oauth",
        runtime_kind="codex_cli",
        credential_mode="local_oauth",
        model="gpt-5.5",
    )
    assert first.attempt_number == 1
    assert repeated.id == first.id


def test_route_sessions_do_not_overwrite_other_routes(store):
    store.upsert_conversation_runtime_session("cid", "codex_oauth", "oauth-session")
    store.upsert_conversation_runtime_session("cid", "codex_api", "api-session")
    assert store.get_conversation_runtime_session("cid", "codex_oauth") == "oauth-session"
    assert store.get_conversation_runtime_session("cid", "codex_api") == "api-session"


def test_route_pause_is_independent_and_expires(store):
    store.open_runtime_route_pause(
        "codex_oauth", "codex_login_required", retry_at="2026-08-20 10:30:00"
    )
    assert store.active_runtime_route_pause("codex_api", now="2026-08-20 10:00:00") is None
    assert store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:31:00") is None
```

- [ ] **Step 2: Verify Store tests fail**

Run: `.venv/bin/pytest tests/test_store.py -q -k 'runtime_attempt or route_session or route_pause'`

Expected: FAIL with missing Store methods.

- [ ] **Step 3: Add immutable models and tables**

Add `AgentRuntimeAttempt` and `RuntimeRoutePause` Pydantic models beside `AgentRun`, then create:

```sql
create table if not exists agent_runtime_attempts (
    id integer primary key autoincrement,
    agent_run_id integer,
    workload_kind text not null,
    workload_key text not null,
    attempt_number integer not null check(attempt_number > 0),
    route_name text not null,
    runtime_kind text not null,
    credential_mode text not null,
    model text not null,
    session_mode text not null default 'fresh'
        check(session_mode in ('fresh', 'resume')),
    source_session_id text not null default '',
    session_id text not null default '',
    status text not null check(status in ('starting','running','completed','failed','superseded')),
    failure_class text not null default '',
    failure_code text not null default '',
    failover_permitted integer not null default 0,
    transcript_reference text not null default '',
    transcript_start integer not null default 0,
    transcript_end integer not null default 0,
    first_effect_started_at text not null default '',
    started_at text not null default current_timestamp,
    finished_at text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    check(
        (workload_kind='agent_run' and agent_run_id is not null
         and workload_key=cast(agent_run_id as text))
        or
        (workload_kind<>'agent_run' and agent_run_id is null)
    ),
    check(
        (session_mode='fresh' and source_session_id='')
        or (session_mode='resume' and trim(source_session_id)<>'')
    ),
    unique(workload_kind, workload_key, attempt_number),
    foreign key(agent_run_id) references agent_runs(id)
);
create unique index if not exists idx_runtime_attempt_active_route
    on agent_runtime_attempts(workload_kind, workload_key, route_name)
    where status in ('starting','running');

create table if not exists conversation_runtime_sessions (
    conversation_id text not null,
    route_name text not null,
    session_id text not null,
    updated_at text not null default current_timestamp,
    primary key(conversation_id, route_name)
);

create table if not exists runtime_route_pauses (
    route_name text primary key,
    failure_code text not null,
    retry_at text not null,
    opened_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);
```

- [ ] **Step 4: Add transactional claim/transition APIs**

Implement these exact Store methods with `BEGIN IMMEDIATE` through the existing
write-transaction helper. `claim_agent_runtime_attempt` accepts the real Agent
run ID and derives `workload_kind='agent_run'` plus its decimal key.
`claim_runtime_operation_attempt` accepts one of the enumerated non-reply kinds
and a stable domain identifier that its caller has already persisted. Both
select the next number
with `coalesce(max(attempt_number), 0) + 1`, reuses an existing active row for
the same route, and inserts `starting`. Transition methods use `where status in
('starting','running')` guards and raise on conflicting terminal state. Session upsert uses
`on conflict(conversation_id, route_name) do update`; pause reads delete an
expired row before returning `None`.

Claims default to `session_mode='fresh'` with an empty `source_session_id`.
Resume claims require a trimmed, nonempty source session, and active-claim idempotency
compares both fields. Upgrades add the defaulted fields and database triggers so
invalid fresh/resume combinations cannot enter the attempt ledger directly.

```python
claim_agent_runtime_attempt(agent_run_id, route_name, runtime_kind,
                            credential_mode, model) -> AgentRuntimeAttempt
claim_runtime_operation_attempt(workload_kind, workload_key, route_name,
                                runtime_kind, credential_mode,
                                model) -> AgentRuntimeAttempt
mark_agent_runtime_attempt_running(attempt_id) -> AgentRuntimeAttempt
complete_agent_runtime_attempt(attempt_id, session_id, transcript_reference,
                               transcript_start, transcript_end) -> AgentRuntimeAttempt
fail_agent_runtime_attempt(attempt_id, failure_class, failure_code,
                           failover_permitted) -> AgentRuntimeAttempt
mark_agent_runtime_attempt_superseded(attempt_id) -> AgentRuntimeAttempt
list_agent_runtime_attempts(agent_run_id) -> list[AgentRuntimeAttempt]
note_runtime_attempt_effect_started(attempt_id, at=None) -> AgentRuntimeAttempt
upsert_conversation_runtime_session(conversation_id, route_name, session_id) -> None
get_conversation_runtime_session(conversation_id, route_name) -> str | None
open_runtime_route_pause(route_name, failure_code, retry_at) -> bool
active_runtime_route_pause(route_name, now=None) -> str | None
close_runtime_route_pause(route_name) -> bool
```

Reject conflicting terminal rewrites, never mutate a completed attempt, and redact failure detail before persistence by storing only the typed code.

- [ ] **Step 5: Backfill Codex conversation sessions without deleting legacy fields**

During initialization, insert existing non-empty `conversations.codex_session_id` values as `codex_oauth` rows with `insert or ignore`. Keep the old column readable until all callers migrate.

- [ ] **Step 6: Run Store tests and commit**

Run: `.venv/bin/pytest tests/test_store.py -q`

Expected: PASS, including all existing unknown-effect and restart tests.

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: persist agent runtime attempts and route state"
```

### Task 4: Implement the isolated Codex CLI adapter

**Files:**
- Create: `app/codex_runtime_adapter.py`
- Create: `tests/test_codex_runtime_adapter.py`
- Modify: `app/codex_runner.py:117-141`
- Modify: `tests/test_codex_runner.py`

- [ ] **Step 1: Write failing environment-isolation and classification tests**

```python
def test_oauth_route_removes_provider_keys(adapter, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient")
    monkeypatch.setenv("CODEX_API_KEY", "ambient-codex")
    monkeypatch.setenv("CEO_CODEX_API_KEY", "fallback")
    env = adapter.build_env(route("codex_oauth"))
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_API_KEY" not in env
    assert "CEO_CODEX_API_KEY" not in env


def test_api_route_injects_only_selected_secret(adapter):
    env = adapter.build_env(route("codex_api"), api_key="fallback")
    assert env["OPENAI_API_KEY"] == "fallback"
    assert "CEO_CODEX_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_local_oauth_expiration_allows_failover(adapter):
    failure = adapter.classify_failure(
        stderr="failed to refresh token: session has ended",
        stdout="",
        returncode=1,
    )
    assert failure.code == "codex_login_required"
    assert failure.failover_permitted is True
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/pytest tests/test_codex_runtime_adapter.py -q`

Expected: FAIL because the adapter is missing.

- [ ] **Step 3: Make Codex model options explicit**

Change `codex_model_config_options()` to accept `model`, `provider`, and `reasoning_effort` keyword arguments. Preserve the existing no-argument behavior for callers not yet migrated:

```python
def codex_model_config_options(*, model: str | None = None,
                               provider: str | None = None,
                               reasoning_effort: str | None = None) -> list[str]:
    selected_model = selected_codex_model() if model is None else model.strip()
    selected_provider = (
        os.environ.get(CODEX_MODEL_PROVIDER_ENV, "").strip()
        if provider is None else provider.strip()
    )
    selected_effort = (
        selected_codex_reasoning_effort()
        if reasoning_effort is None else reasoning_effort.strip()
    )
    # Build the existing -m/-c arguments from these explicit values.
```

- [ ] **Step 4: Implement `CodexRuntimeAdapter`**

The adapter must expose the following public surface. The constructor stores a
`CodexRunner`; `build_command` delegates to it with the route's explicit model
and provider; `build_env` performs the isolation algorithm below; and
`classify_failure` maps the already enumerated Codex errors into
`RuntimeFailure`.

```python
CodexRuntimeAdapter(workspace, config, codex_bin="codex")
build_command(route, prompt, session_id, image_paths, output_schema_path,
              use_output_schema, approval_policy, developer_instructions,
              use_approval_bypass) -> list[str]
build_env(route) -> dict[str, str]
classify_failure(stdout, stderr, returncode) -> RuntimeFailure
```

Start from `CodexRunner.build_env()`, then rebuild an explicit safe child
environment from macOS/launchd essentials, reviewed CA-bundle variables, and
an explicitly set `CODEX_HOME`. Do not pass proxy URLs, `SSH_AUTH_SOCK`, or
unreviewed inherited values. Both routes configure
`shell_environment_policy.inherit="core"` and
`shell_environment_policy.ignore_default_excludes=false`. `codex_api` alone
adds `OPENAI_API_KEY` and explicitly selects a custom `ceo_openai_api` provider
using `model_providers.ceo_openai_api` with the OpenAI `/v1` base URL,
`env_key="OPENAI_API_KEY"`, and `wire_api="responses"`; it must not use the
built-in `openai` provider or set `requires_openai_auth`.

Failure classification has a success hard boundary: a zero return code or a
terminal success cannot authorize retry, failover, or pause. For nonzero
results, classify only stderr plus provider-owned messages from JSONL `error`
and `turn.failed` events, never arbitrary stdout, model text, or tool output.
Recognize typed login/auth/capacity/transport errors; empty nonzero output is
`codex_process_failed`; unknown output stays fail-closed as
`runtime_unclassified`.

Keep `codex_api` disabled for business routing until Task 8 runs its
launchd-like, structured live probe successfully. Command construction and
environment injection are not authentication proof, and this task does not
run a real login or provider request.

- [ ] **Step 5: Run focused tests and commit**

Run: `.venv/bin/pytest tests/test_codex_runtime_adapter.py tests/test_codex_runner.py tests/test_codex_capacity.py -q`

Expected: PASS.

```bash
git add app/codex_runtime_adapter.py app/codex_runner.py tests/test_codex_runtime_adapter.py tests/test_codex_runner.py
git commit -m "feat: isolate Codex OAuth and API child environments"
```

### Task 5: Implement bounded route selection and failover safety

**Files:**
- Create: `app/agent_runtime_router.py`
- Create: `tests/test_agent_runtime_router.py`

- [ ] **Step 1: Write failing safety and ordering tests**

```python
def test_effect_start_blocks_failover(router, store, running_attempt):
    store.note_runtime_attempt_effect_started(running_attempt.id)
    decision = router.next_route(
        run=store.get_agent_run(running_attempt.agent_run_id),
        failed_attempt=store.get_agent_runtime_attempt(running_attempt.id),
        failure=failover_failure(),
        required_capabilities=frozenset({"structured_output"}),
        recovery_phase="",
    )
    assert decision.route is None
    assert decision.reason == "effect_started"


def test_oauth_failure_selects_api_once(router, store, running_attempt):
    decision = router.next_route(
        run=store.get_agent_run(running_attempt.agent_run_id),
        failed_attempt=running_attempt,
        failure=failover_failure(),
        required_capabilities=frozenset({"structured_output"}),
        recovery_phase="",
    )
    assert decision.route.name == "codex_api"
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/pytest tests/test_agent_runtime_router.py -q`

Expected: FAIL because the router is missing.

- [ ] **Step 3: Implement the deterministic safety predicate**

```python
def failover_is_safe(*, run: AgentRun, attempt: AgentRuntimeAttempt,
                     failure: RuntimeFailure, has_confirmed_receipt: bool,
                     recovery_phase: str) -> tuple[bool, str]:
    if recovery_phase:
        return False, "recovery_pinned"
    if has_confirmed_receipt:
        return False, "confirmed_receipt"
    if run.side_effect_state != "none":
        return False, "side_effect_state"
    if run.effect_started_count or attempt.first_effect_started_at:
        return False, "effect_started"
    if not failure.failover_permitted:
        return False, "failure_not_eligible"
    return True, "safe"
```

- [ ] **Step 4: Implement route eligibility and bounded selection**

`AgentRuntimeRouter.next_route()` must exclude attempted routes, active pauses, unhealthy/expired snapshots, and missing capabilities. Permit exactly one repeated `codex_api` route only for `session_route_incompatible`, marked as a fresh-session attempt. The failed `codex_api` attempt must persist `session_mode='resume'` and a nonempty `source_session_id`, and no earlier fresh `codex_api` attempt may exist for the same Agent run. Fresh and repeated attempts remain excluded, so the route cannot loop. Return a typed decision with the selected route, `fresh_session`, and safe display reason.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_agent_runtime_router.py tests/test_store.py -q -k 'runtime or route or effect'`

Expected: PASS.

```bash
git add app/agent_runtime_router.py tests/test_agent_runtime_router.py
git commit -m "feat: route Codex failover with effect-safe gating"
```

### Task 6: Route Consumer and Audit turns without changing recovery semantics

**Files:**
- Modify: `app/agent_turn_runner.py:45-132`
- Modify: `app/agent_turn_runner.py:140-520`
- Modify: `app/consumer_agent.py:220-385`
- Modify: `app/audit_agent.py:330-390`
- Test: `tests/test_agent_turn_store.py`
- Test: `tests/test_agent_orchestrator.py`

- [ ] **Step 1: Write failing Consumer/Audit failover regressions**

Add tests proving:

For `test_consumer_read_events_can_fail_over_within_same_run`, use the existing
fake process executor to emit a completed read event followed by
`codex_login_required` for `codex_oauth`, then a valid `ConsumerAgentResult` for
`codex_api`. Assert the returned run ID equals the claimed run, attempt routes
are exactly `codex_oauth,codex_api`, and the task generation is unchanged.

For `test_audit_effect_start_blocks_api_fallback`, emit the existing normalized
effectful `item.started` fixture followed by a transport failure. Assert the
call raises, only the OAuth attempt exists, and the persisted run has
`side_effect_state == "unknown"`.

- [ ] **Step 2: Verify the focused tests fail**

Run: `.venv/bin/pytest tests/test_agent_turn_store.py tests/test_agent_orchestrator.py -q -k 'failover or effect_start'`

Expected: FAIL because `AgentTurnProcess` still owns one fixed Codex invocation.

- [ ] **Step 3: Inject the adapter and router into `AgentTurnProcess`**

Add constructor parameters with production defaults:

```python
runtime_config: AgentRuntimeConfig | None = None,
runtime_router: AgentRuntimeRouter | None = None,
codex_adapter: CodexRuntimeAdapter | None = None,
```

Replace the single command/process block with a bounded route loop. Claim the attempt before process start, pass the existing `persist_line` callback unchanged, call `note_runtime_attempt_effect_started()` inside the existing normalized effect `item.started` branch, and complete/fail the attempt before choosing another route.

On every nonzero exit or timeout, stabilize the failed route's observed/resumed
local session and replay its completed controlled calls through the same
receipt/effect validator before consulting `next_route()`. A replayed or
ambiguous effect pins the run to unknown/reconciliation; Consumer may fail over
only after its read-only contract and replay prove no effect. Persist the failed
attempt's route-local session and transcript bounds. Each successor starts its
own transcript range, excluding the predecessor's evidence.

When classified failure evidence has `route_pause_required=true`, open the
failed route's persisted pause with the configured runtime retry delay before
successor selection. Pause opening is transactional/idempotent; Task 8 owns
successful probe-based closure.

Initial selection must use current injected capability snapshots and persisted
route pauses, not configured route order alone. Configuration does not prove API
health. Until Task 8 installs probe refresh, only the pre-existing local OAuth
path may use the explicitly scoped trusted-legacy bootstrap when it has no
snapshot; the bootstrap never applies to API failover.

Consumer/Audit orchestration passes an explicit capability set for the actual
turn into `AgentTurnProcess.execute(required_capabilities=...)`. It includes the
task channel, reviewed MCP/native-CLI surfaces (DWS or Lark and Memory),
`image_input` when images are attached, and exact name+sha256 capability names
for reviewed Skill receipts. The process adds its role/recovery invariants; it
does not infer tool needs from prompt text. A configured route missing any
required concrete capability is ineligible.

Consumer receives any required reviewed Skills as explicit
`required_reviewed_skills` metadata on `AgentTaskContext`. An empty tuple adds no
Skill requirement. Each supplied receipt adds exactly
`reviewed_skill:{name}:{sha256}`; there is no generic "named Skill" capability
and prompts are never parsed to infer one.

If initial selection has no eligible route, persist the AgentRun as a typed,
retryable `runtime_route_unavailable` failure with a display-safe eligibility
reason before returning a deferred orchestration result. No runtime attempt or
child process may be started. The existing worker retry mapping requeues the
ReplyTask with its normal backoff.

- [ ] **Step 4: Preserve unknown and recovery behavior exactly**

Before consulting the router, run `_recovery_execution_result_from_receipts()`. If an effect started or a receipt exists, execute the existing `_defer_unknown()`/`mark_agent_run_unknown()` path and do not ask for another route. Reconciliation and authorized recovery executions pass `recovery_phase` to the router and remain pinned.

- [ ] **Step 5: Persist route-specific sessions**

When a turn yields a session ID, update the runtime attempt and call:

```python
store.upsert_conversation_runtime_session(
    task.conversation_id, route.name, session_id
)
```

Continue writing the legacy Codex conversation field for `codex_oauth` during migration. For `codex_api`, do not overwrite `codex_oauth`.

Only Consumer turns read or update conversation route sessions. Audit and Audit
recovery turns are fresh route sessions and persist their session identifiers
only on their runtime attempts/run evidence.

Each `conversation_runtime_sessions` row also owns its `contract_hash`.
Consumer resume lookup requires the selected route row to match the current
wire contract, and a yielded Consumer session atomically updates that route's
session ID and hash. Migrated rows default to an empty hash and therefore fail
closed. The legacy OAuth column/hash may mirror only the `codex_oauth` route;
refreshing an API session must not make an older OAuth session appear current.

Consumer invalidation (forced decision, wire-contract mismatch, missing local
session, or retry without evidence progress) clears only the matching route
slot. For `codex_oauth`, clear the matching legacy compatibility value in the
same Store transaction; API invalidation never clears the OAuth slot. Runtime
attempt and Audit evidence remain immutable.

- [ ] **Step 6: Run complete role-agent tests and commit**

Run: `.venv/bin/pytest tests/test_agent_turn_store.py tests/test_agent_orchestrator.py tests/test_consumer_agent.py tests/test_audit_agent.py -q`

Expected: PASS.

```bash
git add app/agent_turn_runner.py app/consumer_agent.py app/audit_agent.py tests/test_agent_turn_store.py tests/test_agent_orchestrator.py
git commit -m "feat: fail over Consumer and Audit Codex turns safely"
```

### Task 7: Migrate every remaining native Codex caller to the shared router

**Files:**
- Modify: `app/codex_decision.py`
- Modify: `app/structured_agent.py`
- Modify: `app/task_agent.py`
- Modify: `app/task_memory_backfill.py`
- Modify: `app/meeting_alignment_agent.py`
- Modify: `app/weekly_okr_report.py`
- Modify: `app/codex_memory_write.py`
- Modify: `app/wechat/memory_import.py`
- Modify: `app/wechat/memory_writer.py`
- Create: `tests/test_codex_memory_write.py`
- Test: `tests/test_codex_decision.py`
- Test: `tests/test_structured_agent.py`
- Test: `tests/test_task_agent.py`
- Test: `tests/test_task_memory_backfill.py`
- Test: `tests/test_meeting_alignment_agent.py`
- Test: `tests/test_weekly_okr_report.py`
- Test: `tests/wechat/test_memory.py`

- [ ] **Step 1: Add a failing migration guard**

Create `tests/test_runtime_route_coverage.py`:

```python
import ast
from pathlib import Path


class CodexRunnerConstructorVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.lines: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        target = node.func
        if (
            isinstance(target, ast.Name) and target.id == "CodexRunner"
        ) or (
            isinstance(target, ast.Attribute) and target.attr == "CodexRunner"
        ):
            self.lines.append(node.lineno)
        self.generic_visit(node)


def test_production_modules_do_not_construct_codex_runner_directly():
    allowed = {"app/codex_runtime_adapter.py"}
    offenders = []
    for path in Path("app").rglob("*.py"):
        visitor = CodexRunnerConstructorVisitor()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        if visitor.lines and str(path) not in allowed:
            offenders.append((str(path), visitor.lines))
    assert offenders == []
```

- [ ] **Step 2: Verify the guard lists all remaining callers**

Run: `.venv/bin/pytest tests/test_runtime_route_coverage.py -q`

Expected: FAIL and list every direct caller named in this task.

- [ ] **Step 3: Add a reusable no-effect routed executor**

Create `RoutedCodexExecution` in `app/agent_runtime_router.py` for callers that
do not own `agent_runs`. It accepts an enumerated workload kind, the caller's
already-persisted stable domain ID, prompt, command factory, parser, and optional
conversation ID. It claims the generalized `agent_runtime_attempts` row through
`claim_runtime_operation_attempt`. It may fail over only when the caller is
runner-enforced read-only. Do not create synthetic reply tasks.

- [ ] **Step 4: Migrate callers one family at a time**

For each listed caller, inject `AgentRuntimeRouter`/`CodexRuntimeAdapter`, replace direct `CodexRunner` construction, preserve its existing timeout/output parser, and persist its domain result only after the routed execution returns. Conversation-bound structured requests may use their route-scoped session lookup. Meeting analysis always starts fresh and never reads or updates a Consumer session. Weekly OKR, project-memory backfill, and WeChat import extraction also use fresh sessions.

Use these explicit workload identities:

- reply decision: `agent_run:<agent_run_id>`;
- structured OA/OKR request: `structured:<request_id>`;
- task agent: insert the existing `task_agent_runs` row as running before the
  model call and use `task:<task_agent_run_id>`;
- task-memory backfill: `task:<project_id>:memory_backfill`;
- meeting alignment: insert the existing `meeting_alignment_runs` row as
  running before the model call and use `meeting:<meeting_alignment_run_id>`;
- weekly OKR analysis: insert or reuse the exact natural-key
  `weekly_okr_analysis_jobs` row before the model call and use
  `weekly_okr:<week_end>:<manager_user_id>:<source_digest>`;
- Memory outbox write: `memory:memory_write_event:<event_id>`;
- WeChat Memory extraction: insert a `wechat_memory_import_jobs` row for every
  invocation before the model call and use
  `memory:wechat_memory_import_job:<job_id>`;
- WeChat approved-candidate write:
  `memory:wechat_memory_candidate:<candidate_id>`.

If a caller lacks the named persisted row, add that row before starting the
runtime attempt in the caller's existing domain table. Do not use random UUIDs,
prompt text, or a synthetic reply task as workload identity.

The Memory outbox and approved-candidate writers are effectful. Migrate them to
the attempt ledger for durable route evidence, but do not permit automatic
provider failover; ambiguous completion remains owned by their existing
write/reconciliation lifecycle.

The weekly job claim must distinguish `claimed`, `in_progress`, and `cache_hit`;
concurrent callers must not both execute the active analysis. It atomically
reopens a failed row but never returns a completed row as runnable. Canonicalize
the manager ID before constructing its natural key. Meeting run completion uses
the centralized production terminal-status set. Before adding its one-active
index, migration preserves duplicate legacy running rows, keeps the newest by
`created_at` then `id`, and closes the older rows with an auditable migration
failure reason.

After `cache_hit`, the weekly caller must validate the local analysis artifact.
If it is missing or corrupt, call the explicit cache-miss reclaim API with the
job ID and complete expected natural key; never reopen completed work from the
normal begin path. Reclaim uses a completed-to-running CAS and exposes
`claimed` versus `in_progress` to concurrent callers.

- [ ] **Step 5: Run each affected suite**

Run:

```bash
.venv/bin/pytest \
  tests/test_codex_decision.py tests/test_structured_agent.py \
  tests/test_task_agent.py tests/test_task_memory_backfill.py \
  tests/test_meeting_alignment_agent.py tests/test_weekly_okr_report.py \
  tests/test_codex_memory_write.py tests/wechat/test_memory.py \
  tests/test_runtime_route_coverage.py -q
```

Expected: PASS and the coverage guard finds no production direct constructor.

- [ ] **Step 6: Commit caller migration**

```bash
git add app/codex_decision.py app/structured_agent.py app/task_agent.py \
  app/task_memory_backfill.py app/meeting_alignment_agent.py \
  app/weekly_okr_report.py app/codex_memory_write.py \
  app/wechat/memory_import.py app/wechat/memory_writer.py \
  tests/test_runtime_route_coverage.py tests/test_codex_decision.py \
  tests/test_structured_agent.py tests/test_task_agent.py \
  tests/test_task_memory_backfill.py tests/test_meeting_alignment_agent.py \
  tests/test_weekly_okr_report.py tests/test_codex_memory_write.py \
  tests/wechat/test_memory.py
git commit -m "refactor: route all Codex workloads through runtime failover"
```

### Task 8: Add route probes, route-scoped pauses, and operational commands

**Files:**
- Create: `app/agent_runtime_probe.py`
- Create: `tests/test_agent_runtime_probe.py`
- Modify: `app/cli.py`
- Modify: `app/worker.py`
- Modify: `app/setup_wizard.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_worker.py`
- Test: `tests/test_setup_wizard.py`

- [ ] **Step 1: Write failing probe and pause tests**

```python
def test_probe_requires_structured_completion(fake_codex, probe):
    fake_codex.stdout = '{"type":"turn.started"}\n'
    snapshot = probe.run(route_name="codex_api")
    assert snapshot.healthy is False
    assert snapshot.failure.code == "runtime_probe_incomplete"


def test_oauth_pause_does_not_pause_api_route(worker, store):
    store.open_runtime_route_pause("codex_oauth", "codex_login_required", retry_at=future())
    assert worker.runtime_router.first_eligible_route(required_capabilities=frozenset()).name == "codex_api"
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/pytest tests/test_agent_runtime_probe.py tests/test_worker.py -q -k 'probe or route_pause'`

Expected: FAIL.

- [ ] **Step 3: Implement a synthetic, non-business probe**

The probe creates a temporary directory, sends a minimal schema-constrained prompt, disables effectful tools, requires `turn.started`, one valid final result, and `turn.completed`, and returns a snapshot containing only capability names and typed failure state. It must not load the production DB or call DWS/Lark/Memory.

- [ ] **Step 4: Add `probe-agent-runtimes` and worker refresh**

Register a CLI command that prints JSON containing route name, `healthy`, capabilities, checked/expiry times, and safe failure code. Worker startup and the configured interval refresh expired snapshots and close only the recovered route's pause.

- [ ] **Step 5: Add setup validation without rendering secrets**

The setup wizard reports each route as `disabled`, `missing_secret`, `probe_failed`, or `ready`. It accepts new secret values but renders only whether they are configured.

- [ ] **Step 6: Run tests and commit**

Run: `.venv/bin/pytest tests/test_agent_runtime_probe.py tests/test_cli.py tests/test_worker.py tests/test_setup_wizard.py -q`

Expected: PASS.

```bash
git add app/agent_runtime_probe.py app/cli.py app/worker.py app/setup_wizard.py \
  tests/test_agent_runtime_probe.py tests/test_cli.py tests/test_worker.py tests/test_setup_wizard.py
git commit -m "feat: probe and pause agent runtime routes independently"
```

### Task 9: Expose failover evidence and protect quality gates

**Files:**
- Modify: `app/audit_web.py`
- Modify: `app/quality_gate.py`
- Modify: `app/history.py`
- Test: `tests/test_audit_web.py`
- Test: `tests/test_quality_gate.py`

- [ ] **Step 1: Write failing History and quality tests**

```python
def test_history_shows_runtime_attempts_without_secret_values(client, store, runtime_attempts):
    html = client.get("/history").text
    assert "codex_oauth" in html
    assert "codex_api" in html
    assert "codex_login_required" in html
    assert "fallback-secret" not in html


def test_quality_gate_rejects_effect_started_cross_route_attempt(store, quality_report):
    seed_effect_started_then_second_route(store)
    report = quality_report(store)
    assert any(v.code == "unsafe_runtime_failover" for v in report.violations)
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/pytest tests/test_audit_web.py tests/test_quality_gate.py -q -k 'runtime_attempt or unsafe_runtime_failover'`

Expected: FAIL.

- [ ] **Step 3: Render attempt evidence**

Show route, runtime kind, credential mode, model, session link, status, typed failure code, failover decision, transcript bounds, and effect-start marker. Pass every rendered field through existing credential/local-runtime leak checks. Do not render child environments or raw stderr.

- [ ] **Step 4: Add quality invariants**

Flag:

```text
unsafe_runtime_failover
runtime_attempt_without_parent
multiple_active_runtime_attempts
completed_runtime_attempt_without_final_run
unknown_effect_with_fallback_attempt
runtime_secret_leak
```

Provider route pauses remain operational warnings rather than failed-work red bars when another healthy route can process the task.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/pytest tests/test_audit_web.py tests/test_quality_gate.py tests/test_history.py -q`

Expected: PASS.

```bash
git add app/audit_web.py app/quality_gate.py app/history.py \
  tests/test_audit_web.py tests/test_quality_gate.py tests/test_history.py
git commit -m "feat: audit Codex runtime failover attempts"
```

### Task 10: Document, verify, and stage the dual-auth rollout

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/agent-installation-runbook.md`
- Create: `tests/e2e/test_runtime_failover_live.py`

- [ ] **Step 1: Add opt-in live tests**

Implement three opt-in cases guarded by `CEO_LIVE_RUNTIME_FAILOVER_E2E=1`:

- `test_oauth_route_probe_from_service_environment` asserts a complete,
  schema-valid OAuth probe.
- `test_api_route_probe_does_not_expose_secret` asserts a complete API probe
  and scans every captured/persisted surface for the configured secret.
- `test_read_only_turn_fails_over_under_same_agent_run` injects a typed OAuth
  failure and asserts the API attempt completes under the original run ID and
  execution generation.

Use synthetic content and no business channel tools. Scan stdout, stderr, SQLite, and rendered History for the configured secret and fail if any occurrence exists.

- [ ] **Step 2: Update operator documentation**

Document route order, secret isolation, `probe-agent-runtimes`, per-route pauses, safe failure classes, rollback by removing `codex_api`, and the rule that unknown writes reconcile instead of switching providers.

- [ ] **Step 3: Run focused and full automated verification**

Run:

```bash
.venv/bin/pytest tests/test_agent_runtime_contracts.py tests/test_agent_runtime_config.py \
  tests/test_codex_runtime_adapter.py tests/test_agent_runtime_router.py \
  tests/test_agent_runtime_probe.py tests/test_agent_turn_store.py \
  tests/test_agent_orchestrator.py tests/test_store.py tests/test_quality_gate.py -q
.venv/bin/pytest -q
```

Expected: all tests PASS; live tests SKIP without the explicit environment flag.

- [ ] **Step 4: Run probe-only Stage 1 verification**

With `codex_api` configured but business routing still disabled:

```bash
.venv/bin/ceo-agent probe-agent-runtimes --db "$CEO_WORKER_DB" --workspace "$CEO_WORKSPACE"
```

Expected: both routes return `healthy=true`; output contains no credential material.

- [ ] **Step 5: Commit documentation and live tests**

```bash
git add README.md CHANGELOG.md docs/agent-installation-runbook.md tests/e2e/test_runtime_failover_live.py
git commit -m "docs: add Codex dual-auth failover runbook"
```

- [ ] **Step 6: Release and verify Stage 2 before enabling Audit fallback**

After merging and deploying the committed release, run:

```bash
launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
.venv/bin/ceo-agent quality-check --db "$CEO_WORKER_DB" --workspace "$CEO_WORKSPACE"
```

Expected: launchd shows a new running PID from the committed release root; both probes are healthy; one synthetic/read-only forced OAuth failure completes through `codex_api`; there is no new failed, processing, or unknown backlog.

- [ ] **Step 7: Enable Stage 3 only after Stage 2 evidence passes**

Enable Audit fallback, run the dedicated test-target write/readback scenario, terminate the primary process once before effect start and once after effect start, and verify respectively: safe API failover with one write; no fallback write plus read-only reconciliation. Record commands, attempt rows, receipts, and external readback in the release evidence.
