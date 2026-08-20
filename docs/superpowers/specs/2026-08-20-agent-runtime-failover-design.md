# Agent Runtime Failover Design

## Goal

Keep CEO Agent Service processing work when the installing user's local Codex
OAuth session expires, without weakening the existing Consumer/Audit boundary,
losing tool-call evidence, or repeating an external action.

The runtime uses three ordered routes:

1. native `codex exec` with the installing user's local Codex OAuth session;
2. the same native `codex exec` binary with a service-owned OpenAI API
   credential;
3. Claude CLI with an independent non-interactive Anthropic credential.

The OpenAI API credential is the first fallback because it preserves the
existing Codex CLI runtime, session format, MCP/plugins/skills loading, and
JSONL audit behavior. Claude CLI is the second fallback because it isolates an
OpenAI-wide failure, but it has a different session and event protocol and must
pass an explicit capability gate before receiving work.

This design does not replace `codex exec` with a service-owned Responses API
tool loop. Direct Responses API orchestration would require the service to own
tool execution, approvals, MCP authentication, conversation continuity, and
audit persistence, and is outside this change.

## Scope

This design covers:

- route-specific credentials and child-process environments;
- typed runtime failure classification;
- safe route selection and automatic failover;
- Codex and Claude CLI adapters behind one runtime contract;
- route-specific health and capability checks;
- persistence and audit of every runtime attempt;
- restart behavior, unknown-effect reconciliation, rollout, and rollback;
- Consumer, Audit, structured, meeting, task, and weekly-analysis Agent runs
  that currently invoke native Codex CLI.

This design does not cover:

- failover for DWS, Lark, Memory Connector, or another business dependency;
- automatic login, OAuth refresh UI, browser authorization, or account repair;
- translating Codex plugins or skills into Claude-compatible implementations;
- changing the Consumer/Audit business contract;
- model-quality routing based on subjective answer quality;
- load balancing, lowest-price routing, or per-task model experimentation;
- direct OpenAI Responses API orchestration.

## Confirmed Decisions

- Local Codex OAuth remains the primary route.
- OpenAI API authentication continues to use native `codex exec`; the service
  does not implement an OpenAI tool loop.
- Claude CLI is an independent second-level route, not an automatic replacement
  for a missing DWS, Lark, MCP, plugin, or skill capability.
- A provider change is permitted only when no external effect may have started.
- Once an effectful tool call has started, the run stays on its original route
  and follows existing read-only reconciliation. It is never replayed through
  another provider.
- A route switch creates another runtime attempt under the same `agent_run`; it
  does not create a new reply-task generation or proposal revision.
- Consumer and read-only analysis runs may switch after partial read activity
  because they cannot produce an external effect.
- Audit runs may switch only before the first effectful `item.started` event.
- Provider authentication, capacity, transport, and capability failures remain
  distinct. The service does not treat every nonzero CLI exit as authorization
  to try another model.
- Provider credentials are injected only into the selected child process. A
  fallback API key must not shadow or alter the primary local OAuth route.
- Secrets are never written to commands, runtime-attempt rows, JSONL-derived
  audit events, logs, notifications, or the History UI.
- Claude is enabled for a task only when its current capability snapshot covers
  every capability required by that task.
- Rollout starts with health probes, then read-only Consumer work, and only then
  Audit work. An unknown write is never replayed through another provider.

## Terms

### Runtime route

A runtime route is one executable plus one credential mode and model/provider
configuration. The initial routes are:

- `codex_oauth`: native Codex CLI using local OAuth and local Codex config;
- `codex_api`: native Codex CLI using an isolated OpenAI API credential;
- `claude_api`: Claude CLI using an isolated non-interactive Anthropic
  credential.

The route name is a stable audit identifier. It is not a model name and must
not contain an account email, token fragment, or other credential detail.

### Runtime attempt

A runtime attempt is one child-process invocation for one Agent turn. A single
business `agent_run` can own more than one runtime attempt when a safe failover
occurs. Attempts are ordered and immutable after completion.

### Capability snapshot

A capability snapshot is a recent, non-secret record of what a route can
actually use: executable availability, model access, required MCP servers,
required native CLIs, reviewed skills, output mode, and read/write policy. It
is evidence from probes, not a declaration inferred from configuration.

During migration, the existing local `codex_oauth` route alone may use a
trusted-legacy bootstrap when no OAuth snapshot exists. This exception preserves
the pre-failover execution path without claiming that a probe succeeded. It is
never available to `codex_api` or another service-credential route, never
overrides an explicit unhealthy/expired snapshot, and is not used for failover.
An injected capability registry disables the exception and makes initial route
selection use the same pause, freshness, health, and capability gates as later
route selection.

## Existing Invariants That Must Remain

The failover layer extends the current runtime without changing these rules:

- Consumer Agent A is read-only.
- Audit Agent B is the only Agent allowed to perform an external write.
- One business conversation owns one continuing Consumer context at a time.
- Each candidate revision receives an independent Audit review.
- The local database owns task claiming, generation identity, session locking,
  receipts, restart recovery, and exact duplicate prevention.
- An effectful call with no confirmed completion becomes `unknown` and enters
  read-only reconciliation.
- Reconciliation does not create a new task generation and does not repeat the
  original command.
- A provider failure cannot convert `unknown` into a definite failure.
- A task that lacks a required business channel or tool remains pending or
  blocked; changing the language model cannot manufacture the missing evidence.
- Local Codex JSONL remains the audit source for Codex routes. Claude events are
  normalized but the raw Claude transcript reference remains available for
  drilldown.

## Architecture

```text
AgentOrchestrator / structured Agent caller
                  |
                  v
          AgentRuntimeRouter
          |       |       |
          |       |       +--> ClaudeCliAdapter + claude_api env
          |       +----------> CodexCliAdapter + codex_api env
          +------------------> CodexCliAdapter + codex_oauth env
                  |
                  v
        normalized runtime events
                  |
                  v
      agent_runtime_attempts + existing agent_runs
                  |
       +----------+-----------+
       |                      |
       v                      v
 final Agent result     unknown-effect reconciliation
```

### AgentRuntimeRouter

`AgentRuntimeRouter` owns route eligibility and attempt ordering. It receives:

- Agent role and task kind;
- the current `agent_run` and execution generation;
- prompt, output schema, and developer instructions;
- required capabilities;
- current route health and persisted pauses;
- whether the invocation is a normal turn, reconciliation read, or narrowly
  authorized recovery execution;
- prior attempt events and side-effect state.

It returns one final typed result or one typed terminal/deferred failure. It
does not interpret business content, modify a candidate, choose a recipient, or
execute a business action itself.

The router tries eligible routes in configured order. A later route may start
only when `FailoverSafety` returns `safe`. Route count is bounded by configured
routes. One route is started at most once per Agent turn, except that an
explicit `session_route_incompatible` result permits exactly one fresh-session
attempt on the same Codex API route only when the failed attempt persisted
`session_mode=resume` and a nonempty `source_session_id`. A prior fresh Codex
API attempt blocks another repeat. Existing same-route process retry remains
limited to transient failures that are safe to repeat.

### Runtime adapters

Both adapters implement one provider-neutral contract:

```python
class AgentRuntimeAdapter(Protocol):
    route_name: str

    def probe(self, request: RuntimeProbeRequest) -> RuntimeCapabilitySnapshot: ...
    def start(self, request: AgentTurnRequest) -> RuntimeProcess: ...
    def normalize_event(self, raw_event: object) -> RuntimeEvent: ...
    def parse_result(self, output: RuntimeOutput, schema: dict) -> AgentResult: ...
    def classify_failure(self, output: RuntimeOutput) -> RuntimeFailure: ...
```

The contract describes behavior; implementation may use dataclasses and the
existing process runner rather than these exact class names.

#### CodexCliAdapter

The Codex adapter reuses existing command construction, timeout handling,
session lookup, JSONL parsing, output validation, native CLI classification,
MCP effect classification, and receipt persistence.

For `codex_oauth` it:

- uses the installing user's real `CODEX_HOME`;
- removes provider API-key variables from the child environment;
- does not copy or rewrite OAuth files;
- retains current model and reasoning configuration;
- loads the user's Codex MCP, plugins, hooks, and skills.

For `codex_api` it:

- uses the same Codex binary and Codex home;
- explicitly selects a service-owned `ceo_openai_api` provider and the configured
  model. The command defines that provider with `base_url` set to the OpenAI
  `/v1` endpoint, `env_key="OPENAI_API_KEY"`, and `wire_api="responses"`; it
  does not use the built-in `openai` provider or set `requires_openai_auth`.
  This prevents a cached ChatGPT login from selecting the API route's provider;
- maps the service-private fallback secret into the single environment variable
  supported by the installed Codex version, only for that child process;
- never exposes the fallback secret to the OAuth child process;
- retains the same local MCP, plugins, hooks, skills, prompt, schema, sandbox,
  and approval configuration.

When `codex_api` receives an existing Codex session ID, it first tries native
`codex exec resume` with the API route. If the installed CLI explicitly rejects
cross-credential resume, the attempt is classified as
`session_route_incompatible`. A fresh Codex API session may then start only if
no effectful call has started; it receives the complete current turn input and
the existing source-context package. The rejected resume and fresh session are
recorded as separate attempts. The original session is never overwritten.

#### ClaudeCliAdapter

Claude CLI cannot resume a Codex session. It always creates or resumes a Claude
session associated with the same business conversation and route.

The adapter:

- runs non-interactively with structured streaming output;
- supplies the same role contract, audit rules, task prompt, and local output
  schema;
- validates the final result locally with the same Pydantic contract used by
  Codex runs;
- normalizes Claude tool start, completion, failure, session, and final-result
  events into the provider-neutral runtime event contract;
- applies the same Consumer read-only and Audit reviewed-write policies;
- uses only reviewed Claude MCP and native CLI capabilities proven by the
  current capability snapshot;
- stores the Claude session ID separately from Codex session IDs;
- never imports, copies, or translates Codex OAuth data.

Claude skills are not assumed to be equivalent to Codex skills. A task that
requires a named skill is eligible for Claude only after the installed Claude
environment proves that exact reviewed skill and its required tools are
available. Otherwise the route is `capability_missing` and the task stays on a
compatible route or remains deferred.

### FailoverSafety

`FailoverSafety` is a deterministic runtime check based on persisted events and
receipts. It does not ask the model whether switching is safe.

Failover is safe when all of the following are true:

1. no completed execution receipt exists for the current action;
2. no effectful tool event has reached `item.started`;
3. persisted `side_effect_state` is `none`;
4. the run is not in reconciliation or authorized recovery execution;
5. the next route satisfies every required capability;
6. the failure classification explicitly permits route failover.

Consumer A and other runner-enforced read-only jobs satisfy conditions 1–3 even
after read tools have run. Those reads may be repeated by a fallback route.

Failover is unsafe as soon as one effectful `item.started` event exists, even
when the command later reports failure. The run becomes or remains `unknown`
until the existing reconciliation path proves whether the external system
accepted the operation.

### CapabilityRegistry

`CapabilityRegistry` stores route probe results and answers whether a route is
eligible for a task. Required capabilities are derived from the existing Agent
spec, role policy, channel gate, reviewed MCP manifest, native CLI metadata, and
required skill declarations. They are not inferred from message keywords.

Examples include:

- structured streaming output;
- local schema validation support;
- `dws` read capability;
- `dws` reviewed write capability;
- Lark read or reviewed write capability;
- Memory Connector read or write capability;
- named reviewed skill availability;
- image input support when the task contains images;
- read-only enforcement for Consumer;
- effect event visibility for Audit.

Capability snapshots expire after the configured probe interval. An expired
snapshot makes the route temporarily ineligible until a probe refreshes it; it
does not imply that a tool or account is permanently missing.

## Route-Specific Credentials and Configuration

Configuration declares route order and secret sources without placing secret
values in commands or repository files.

Proposed configuration:

```dotenv
CEO_AGENT_RUNTIME_ROUTES=codex_oauth,codex_api,claude_api

CEO_CODEX_MODEL=gpt-5.5
CEO_CODEX_MODEL_REASONING_EFFORT=medium

CEO_CODEX_API_MODEL=gpt-5.5
CEO_CODEX_API_KEY=<service-private secret>

CEO_CLAUDE_MODEL=<reviewed production model>
CEO_CLAUDE_API_KEY=<service-private secret>

CEO_RUNTIME_PROBE_INTERVAL=5m
CEO_RUNTIME_ROUTE_RETRY_DELAY=30m
```

Exact model identifiers are configuration, not hard-coded routing rules. Setup
must validate that each configured model is callable before enabling its route.

`CEO_CODEX_API_KEY` and `CEO_CLAUDE_API_KEY` are service-private source
variables. Adapters map them to the environment variable required by the child
CLI. The source variables are removed from every child environment before the
selected credential is injected. The OAuth route removes all model-provider
API keys. The Codex API route receives no Anthropic credential, and the Claude
route receives no OpenAI or Codex credential.

The setup wizard may store secrets only through the project's existing ignored
local configuration or an approved operating-system secret mechanism. It must
never render an existing secret back to the browser or terminal. Audit output
shows only `configured`, `missing`, or `rejected`.

## Runtime Attempt Persistence

Add `agent_runtime_attempts` as an append-only runtime-attempt ledger. Consumer
and Audit attempts retain a real foreign key to `agent_runs`; Codex-backed
workloads that do not own an `agent_run` use their existing stable domain kind
and identifier. The service must not create synthetic reply tasks merely to
obtain an `agent_run` parent.

```text
id
agent_run_id              nullable only for non-reply workloads
workload_kind             agent_run | structured | meeting | task | weekly_okr | memory
workload_key              stable existing domain identifier
attempt_number
route_name
runtime_kind             codex_cli | claude_cli
credential_mode          local_oauth | service_api
model
session_mode             fresh | resume
source_session_id        required only when session_mode=resume
session_id
status                    starting | running | completed | failed | superseded
failure_class
failure_code
failover_permitted
transcript_reference
transcript_start
transcript_end
first_effect_started_at
started_at
finished_at
```

The unique key is `(workload_kind, workload_key, attempt_number)`. For
`workload_kind=agent_run`, `agent_run_id` is required and `workload_key` is the
decimal Agent run ID. For every other kind, `agent_run_id` is null and
`workload_key` must match an existing stable identifier already owned by that
workload; free-form or random identifiers are rejected. Attempt numbers are
claimed transactionally. A completed attempt cannot be superseded. A failed
attempt is `superseded` only after the next attempt is durably claimed. `fresh`
attempts persist an empty `source_session_id`; `resume` attempts persist the
trimmed, nonempty session supplied to the provider. New tables enforce this pairing with
checks; upgrades add the fields with `fresh` defaults and use database triggers
to reject invalid direct writes.

Non-reply workload identity is backed by an exact persisted parent, checked in
the same transaction that claims its runtime attempt:

- `structured:<okr_review_request_id>` requires a processing request;
- `task:<task_agent_run_id>` requires a pre-call running `task_agent_runs` row;
- `task:<project_id>:memory_backfill` names the existing `work_projects` row;
- `meeting:<meeting_alignment_run_id>` requires a pre-call running
  `meeting_alignment_runs` row;
- `weekly_okr:<week_end>:<manager_user_id>:<source_digest>` requires the exact
  running natural-key row in `weekly_okr_analysis_jobs`;
- `memory:memory_write_event:<id>`,
  `memory:wechat_memory_import_job:<id>`, and
  `memory:wechat_memory_candidate:<id>` are source-qualified so equal numeric
  IDs from different tables cannot collide.

Legacy `task_agent_runs` rows migrate as completed. New task and meeting runs
are inserted as running before provider launch and closed idempotently after
the result or failure is persisted. Meeting analysis always starts fresh: it
does not consume or update a Consumer conversation-session slot. Weekly OKR,
project-memory backfill, and WeChat import extraction also start fresh.

Memory outbox and approved-candidate writers are effectful. They use the
runtime-attempt ledger for evidence only and never receive automatic provider
failover; interrupted writes remain on their existing reconciliation path.

Do not store:

- API keys, OAuth tokens, cookies, authorization headers, or token fragments;
- complete child environments;
- raw prompts or business documents duplicated from their existing source;
- account email addresses inferred from credentials;
- unredacted CLI stderr that may contain secrets.

`agent_runs.codex_session_id` remains readable during migration. New code uses
route-specific attempt sessions as execution history and a provider-neutral
conversation-session mapping:

```text
conversation_runtime_sessions
conversation_id
route_name
session_id
updated_at
```

Only one session per conversation and route is current. Codex and Claude
session IDs never overwrite each other. Historical attempt rows remain the
source for prior session references.

Conversation runtime sessions belong only to continuing Consumer context.
Audit turns always start fresh, retain their observed session on their own
runtime attempt and run evidence, and never read or update a Consumer
`conversation_runtime_sessions` slot.

## Failure Classification and Routing

Every runtime failure has:

```text
class
code
retryable_on_same_route
failover_permitted
route_pause_required
detail_safe_for_display
```

The initial routing matrix is:

| Failure | Same-route retry | Next route | Result |
|---|---:|---:|---|
| Local Codex OAuth expired or invalidated | No | `codex_api` | Safe only before effect start |
| ChatGPT Codex backend rejects local session | No | `codex_api` | Safe only before effect start |
| Local Codex subscription capacity exhausted | No immediate retry | `codex_api` | Pause OAuth route |
| OpenAI API key missing or rejected | No | `claude_api` | Pause API route; alert once |
| OpenAI API capacity exhausted | Delayed | `claude_api` | Pause API route |
| OpenAI transport failure before effect start | Bounded | `claude_api` | Switch after retry budget |
| Claude credential missing or rejected | No | None | Defer and alert once |
| Required MCP, CLI, or skill missing | No | Compatible route only | Never treat as model failure |
| Invalid final result with no effect start | One schema-repair turn | Next eligible route | Preserve validation detail |
| Effectful call started; completion missing | No | None | Mark unknown and reconcile |
| Effect confirmed by receipt | No | None | Complete; never fail over |
| Business result is `needs_human` or `no_action` | No | None | Final business outcome |

No routing decision is based on a generic substring such as `error`, `failed`,
or `timeout`. CLI-specific text is parsed inside its adapter into the typed
failure contract. Unclassified failures default to `failover_permitted=false`.
`returncode == 0` or an observed terminal success is a hard boundary: it cannot
authorize retry, failover, or a route pause even if its stdout happens to quote
an error. On a nonzero result, the adapter combines stderr with only
provider-owned JSONL error fields from `error` and `turn.failed` events; it
does not scan tool output, model text, or arbitrary stdout. Idle/total timeout
and the recognized stream-disconnect condition are typed transport failures;
an empty nonzero process result is `codex_process_failed` and cannot fail over.

## Execution Flow

### Normal Consumer turn

```text
claim agent_run
  -> calculate required read capabilities
  -> select first healthy eligible route
  -> claim runtime attempt
  -> run read-only Agent turn
  -> success: persist result and conversation route session
  -> typed provider failure:
       -> verify FailoverSafety
       -> mark attempt failed
       -> select next eligible route
       -> claim next attempt under same agent_run
  -> no eligible route: defer task with actionable route state
```

Read events from a failed Consumer attempt remain audit evidence. They do not
grant the next model authority to write.

### Normal Audit turn

```text
claim Audit agent_run
  -> calculate required read and reviewed-write capabilities
  -> select route and claim attempt
  -> stream normalized tool events
  -> provider failure before effectful item.started:
       -> FailoverSafety may allow next route
  -> effectful item.started:
       -> pin run to current route
       -> provider/process failure without confirmed receipt:
            mark unknown
            enter existing read-only reconciliation
  -> confirmed execution:
       persist receipt and complete
```

The router cannot switch routes between effect start and external-state
readback. This remains true after service restart.

### Reconciliation and recovery

Reconciliation is pinned to a capability-compatible runtime but not permitted
to repeat the original write. It receives the original operation identity,
controlled command metadata, receipts, and read-only recovery prompt.

If the original route is unavailable, another route may perform reconciliation
only when it has the exact required read capability and the runner enforces
read-only mode. This is not a replay: it may query external state but cannot
execute the missing action.

If reconciliation proves the action absent, the existing recovery protocol may
authorize one new effectful execution with a stable authorization identity. A
provider route is selected before that new execution starts. Once started, it
is pinned by the same rule.

### Restart recovery

At service startup:

1. recover expired runtime-attempt leases;
2. inspect persisted normalized events and receipts;
3. mark attempts with a started but unconfirmed effect as unknown;
4. resume or recreate only read-only/no-effect attempts;
5. re-evaluate current route health instead of trusting pre-restart health;
6. never rerun a completed or unknown effectful attempt;
7. continue reconciliation before accepting a new generation for the task.

## Health Checks and Route Pauses

Each route has a bounded, non-business probe that verifies:

- executable exists and reports a supported version;
- configured credential can reach the configured model;
- structured streaming can produce and locally validate a minimal result;
- required MCP/tool discovery works without performing a business write;
- event normalization captures session, turn start, and turn completion;
- Consumer read-only enforcement and Audit effect visibility are available.

The probe uses an isolated temporary workspace and synthetic content. It does
not read DingTalk, Lark, Memory, email, calendar, documents, or the production
SQLite database. Business-channel health remains owned by existing channel
gates.

A failing route opens a persisted route pause for the configured delay. A
credential rejection remains paused until configuration changes or an explicit
successful probe. Capacity and transport pauses expire and are re-probed. A
route pause never pauses a healthy independent route.

The service emits one deduplicated notification per route and failure code,
then closes it after a successful probe. An accepted process launch is not a
health success; the structured probe must complete.

## Capability Parity for Claude

Claude is not enabled globally merely because `claude -p` returns text. Before
the route can receive production tasks, live probes must prove:

- non-interactive authentication survives launchd execution;
- structured streaming and session IDs are stable;
- the service can enforce Consumer read-only behavior;
- every effectful Audit tool produces a start event before the external call
  can become unknown;
- DWS and Lark commands use the same installed-user identities and channel
  gates as Codex;
- required MCP servers authenticate without copying Codex OAuth material;
- required reviewed skills exist and have equivalent instructions;
- images and attachments used by eligible tasks are available;
- local result validation rejects malformed output;
- transcript references can be shown without leaking credentials.

Capability parity is evaluated per task, not as a one-time claim that Claude
and Codex are interchangeable. A missing optional capability removes only the
affected task type from Claude eligibility.

## History, Metrics, and Notifications

History displays, for each Agent run:

- selected route, runtime, credential mode, and model;
- each attempt in order;
- session/transcript reference;
- safe failure classification and fallback reason;
- whether failover was permitted or blocked by an effect start;
- route health at selection time;
- final result, effect state, receipts, and reconciliation status.

It never displays API keys, OAuth details, full environments, or credential
owner identifiers.

Metrics include:

- attempts and successful turns by route and task type;
- primary-route success rate;
- failover count and reason;
- time added by failover;
- route pause duration;
- capability-gate rejection count;
- unknown-effect count by route;
- duplicate-prevention and reconciliation outcomes;
- result-schema failure rate by route;
- cost/token usage when the CLI exposes trustworthy non-secret usage fields.

Provider cost is reported separately. ChatGPT subscription usage, OpenAI API
usage, and Anthropic API usage must not be combined into one unlabeled total.

## Security Rules

- Route secrets are loaded at runtime and injected only into the selected child
  process.
- The primary OAuth route receives no provider API key.
- Child environments are rebuilt from an explicit macOS/launchd allowlist:
  process basics (`HOME`, `PATH`, `SHELL`, temporary-directory, locale, user,
  terminal, and time-zone variables), reviewed CA-bundle variables, and an
  explicitly set `CODEX_HOME`. Proxy URLs and `SSH_AUTH_SOCK` are excluded.
  No arbitrary inherited variable or later caller-provided credential may
  expand that allowlist.
- Both Codex routes configure `shell_environment_policy.inherit="core"` and
  `shell_environment_policy.ignore_default_excludes=false`, so the API key is
  available only to the Codex provider process and not model-launched shells.
- Consumer remains unable to invoke effectful MCP or native CLI operations on
  every route.
- Claude receives no Codex OAuth files or token export.
- Codex receives no Anthropic credential.
- Commands, process metadata, error rendering, and test fixtures use redacted
  placeholders rather than real secrets.
- A failure classifier must not include raw stderr in user-visible detail until
  it passes the existing credential leak check.
- Setup and doctor commands report credential state, not credential content.
- Provider switching never expands filesystem, shell, MCP, channel, or approval
  permissions.

## Testing Strategy

### Unit tests

- route-specific environment construction removes unrelated credentials;
- OAuth child environment cannot inherit the fallback OpenAI API key;
- API and Claude secrets never enter command arguments or persisted records;
- the API route uses the exact custom `env_key` provider configuration and no
  key appears in argv, exceptions, representations, or dumps;
- success-shaped output and tool/model text that merely quote provider errors
  cannot influence failure classification;
- failure adapters produce the correct typed classification;
- unclassified errors cannot trigger failover;
- route pauses are independent and expire according to configuration;
- capability snapshots expire and block stale eligibility;
- route ordering is deterministic and each route is attempted at most once,
  except for the single specified fresh-session attempt after
  `session_route_incompatible`;
- a fallback attempt remains under the same `agent_run` and generation;
- Codex and Claude session IDs remain route-specific;
- strict output validation is identical across adapters.

### Safety regression tests

- Consumer provider failure after read events may switch routes;
- Audit provider failure before an effect event may switch routes;
- Audit failure after effectful `item.started` never starts another provider;
- effect start plus missing completion persists `unknown` across restart;
- confirmed receipt prevents fallback and replay;
- reconciliation on another route remains read-only;
- recovery execution selects one route before starting and stays pinned;
- missing DWS/Lark/MCP/skill capability cannot be bypassed by switching models;
- malformed output after a confirmed effect enters reconciliation instead of
  rerunning the business action;
- duplicate sends, approvals, comments, edits, and Memory writes remain
  impossible across route changes.

### Adapter integration tests

Use fake Codex and Claude executables that emit realistic streaming events to
verify:

- command and environment isolation;
- session creation and resume behavior;
- transcript ranges and raw-reference persistence;
- event normalization;
- schema repair;
- timeout and process termination handling;
- provider auth, capacity, and transport failures;
- service restart between effect start and completion.

### Opt-in live tests

Live tests are disabled by default and use dedicated test credentials and
synthetic content. They verify:

- `codex_oauth` probe from the launchd-like environment;
- `codex_api` probe with the fallback API credential;
- Codex session resume across OAuth and API routes, including the specified
  fresh-session behavior when the installed CLI rejects it;
- Claude non-interactive authentication and structured streaming;
- read-only DWS/Lark/MCP capability on both providers;
- one reviewed test-only effect whose external state can be read back and
  cleaned up safely;
- no secret appears in stdout, stderr, SQLite, History, or logs.

Live effect tests must use a dedicated test target. They never send to a real
employee, modify a real approval, or write a production document.

## Rollout

### Stage 0: Persistence and adapters disabled

- Add the provider-neutral contracts and runtime-attempt persistence.
- Keep `codex_oauth` as the only route.
- Prove existing behavior and audit output are unchanged.

### Stage 1: Probe-only dual Codex routes

- Configure the fallback OpenAI API credential.
- Run `codex_oauth` and `codex_api` probes without routing business work to the
  API route.
- Verify model access, event shape, session behavior, MCP/skills loading,
  redaction, and independent route pauses.

### Stage 2: Read-only Codex API failover

- Enable `codex_api` for Consumer and other strictly read-only jobs.
- Inject OAuth expiration, capacity, transport, and malformed-result failures.
- Verify the same Agent run completes through a second attempt without a new
  generation or duplicate notification.

### Stage 3: Audit Codex API failover

- Enable fallback for Audit only before effect start.
- Run test-target write/readback and restart scenarios.
- Require zero cross-route replays and correct unknown reconciliation before
  promotion.

### Stage 4: Claude probe and read-only eligibility

- Add Claude adapter and credential isolation.
- Probe capabilities individually.
- Enable only Consumer/task kinds whose full required capability set passes.
- Compare result correctness, schema validity, latency, cost, and tool evidence
  against the Codex routes on representative fixtures.

### Stage 5: Claude Audit eligibility

- Enable only reviewed write capabilities with visible effect-start events and
  live readback coverage.
- Promote one task family at a time.
- Keep unsupported task families on Codex routes or deferred.

Every stage requires a committed change, focused tests, launchd restart, a new
verified process, route readback, and confirmation that no failed, processing,
or unknown backlog was introduced.

## Rollback

Rollback is configuration-first:

1. remove `claude_api` from `CEO_AGENT_RUNTIME_ROUTES`;
2. remove `codex_api` if necessary;
3. retain `codex_oauth` as the only route;
4. restart and verify the launchd service;
5. preserve runtime-attempt history and route-specific sessions;
6. reconcile every existing unknown run before reprocessing its task.

Disabling a route never deletes its sessions, attempts, receipts, or health
history. Database migrations remain forward-compatible so rollback does not
require destructive schema reversal.

## Acceptance Criteria

The design is complete when all of the following are demonstrated:

1. Local Codex OAuth can be made invalid in a test environment and a read-only
   task completes through `codex_api` under the same `agent_run`.
2. The primary OAuth child process contains no OpenAI fallback API key.
3. OpenAI route failure can move an eligible read-only task to Claude without
   copying Codex credentials or sessions.
4. Claude is rejected for a task when any required capability is unproven.
5. An Audit failure before effect start can switch routes and execute at most
   once.
6. An Audit failure after effect start never starts a fallback write and enters
   reconciliation.
7. Restart recovery preserves the same safety decision from persisted events
   and receipts.
8. Codex and Claude final results pass the same local schema validation.
9. History shows every attempt, route, model, session, failure class, failover
   decision, and final effect state without exposing credentials.
10. Route pauses and notifications are independent and close after verified
    recovery.
11. Existing Consumer/Audit authorization, duplicate prevention, delivery
    receipts, and unknown-effect tests continue to pass.
12. Launchd runs the committed release on a new process and no unresolved
    failed, processing, or unknown backlog is introduced by rollout.

## Implementation Boundary

Implementation should follow the rollout stages in order. It must not begin by
rewriting business runners independently or adding per-runner fallback
branches. The provider-neutral runtime contract, attempt persistence, typed
failure classification, and safety check are shared infrastructure and must be
in place before any second route receives business work.

Direct Responses API orchestration remains a separate future architecture
decision. This design intentionally obtains the immediate reliability benefit
by changing Codex authentication before changing the Agent runtime.
