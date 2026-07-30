# Agent Runtime Simplification Design

## Goal

Replace the current planner, validator, action-array, and service-owned business
execution stack with one Codex Agent run that reads evidence, chooses targets,
and calls the installed CLI or MCP tools directly.

The service remains responsible only for queue lifecycle, channel readiness,
run ownership, audit persistence, exact-run deduplication, and recovery when an
external side effect may have an unknown outcome.

## Decisions

- The Agent calls `dws`, `lark-cli`, WeChat CLI, and configured MCP tools
  directly.
- The service does not bind or validate recipients, conversation IDs, document
  IDs, mail IDs, calendar IDs, or approval IDs.
- The service does not read, download, parse, select, or summarize business
  materials for the Agent. It provides the trigger, recent context, material
  references, and documented CLI commands.
- One `reply_task` execution generation owns one Codex run and one final result.
- No context, decision, action, target, or payload hashes are stored or checked.
- A manual rerun creates a new execution generation. Changed content, targets,
  or actions are allowed and are not blocked by an earlier generation.
- The service never launches a second Codex run for an active generation.
- Cross-generation duplicate prevention is an Agent responsibility: the prompt
  includes prior successful side effects and requires live external-state
  inspection before repeating an effectful command. The service does not
  intercept or rewrite raw CLI commands.
- Confirmed facts in the trigger, recent context, material reads, and prior
  receipts remain accepted evidence. The Agent must not ask the user to provide
  them again unless it identifies a concrete contradiction or a failed read.
- OA evidence and targets are resolved by the Agent through live DWS reads. The
  service provides raw process/task IDs, references, and exact read commands;
  it never recovers an approval target by applicant/title matching or pre-reads
  the approval body.
- Diagnosis is not completion. When the user requests a repair or other
  effectful operation, the Agent must execute and verify it or return
  `needs_human`/`failed`; a diagnosis-only summary is not a completed result.

## Removed Architecture

Delete the following runtime concepts after their callers are replaced:

- `UniversalPlanner`
- `UniversalPlan` and action arrays
- `UniversalConsumerOrchestrator`
- `UniversalValidator`
- `UniversalActionExecutor`
- plan and action dependency declarations
- plan confidence and action-conflict rules
- trusted-target binding and repeated target normalization
- service-owned OA, mail, calendar, document, reaction, reply, and Memory
  executors in the universal task path
- service-side material downloading and body injection
- `context_hash`, `action_hash`, and decision hash validation
- DWS auth archive export, import, rotation tracking, and restore
- string-marker guessing for structured channel and Agent errors

The existing `universal_plan_executions` and `universal_action_executions`
tables are migrated out and dropped. Historical user-visible attempts and tool
events remain in `reply_attempts`; there is no legacy read path for the removed
tables.

## Runtime Components

### Channel Gate

`ChannelGate` is the only service-owned channel abstraction used before an
Agent run. It has no message-list or send methods.

For DingTalk, readiness requires both:

1. `dws auth status --format json`
2. a successful read-only authenticated probe such as
   `dws contact user get-self --format json`

For Feishu/Lark, readiness requires:

1. `lark-cli auth status --json --verify`
2. `lark-cli contact +get-user --as user --json`

The gate returns a typed result:

- `ready`: status and live probe succeeded
- `needs_login`: the CLI is installed but its identity cannot be refreshed or
  verified
- `blocked`: required configuration or authorization is missing
- `unavailable`: command, network, or provider is temporarily unavailable

The producer evaluates all configured channel gates before discovering work or
starting an Agent. A non-ready DingTalk gate prevents all DingTalk producer and
consumer calls for that pass. A non-ready Lark gate prevents only tasks that
require Lark.

### Login Coordination

The service owns interactive login coordination; Agents must never run auth
login, reset, logout, or browser-authorization commands.

When a gate reports `needs_login`, the service:

1. checks the persisted login request state;
2. reuses a still-running request;
3. suppresses another request for one hour after the first launch;
4. starts exactly one CLI login process when no recent request exists;
5. leaves affected tasks pending until a later gate pass succeeds.

The service does not export or restore credentials. It reuses the authenticated
user's normal local CLI state. A completed login is not considered healthy
until the next status plus live-probe check passes.

### Agent Runner

The Agent runner receives:

- the original trigger and channel metadata;
- recent conversation context;
- material links, file IDs, and reference metadata without pre-read bodies;
- prior successful side-effect summaries for the same trigger;
- CLI and MCP capability instructions;
- the current business rules and authorization rules.

It starts native `codex exec`, keeps the user's installed Lark CLI and MCP
configuration, and records Codex JSONL tool events. The prompt explicitly gives
the Agent ownership of evidence gathering, target selection, business judgment,
wording, and execution.

The Agent returns one minimal final result:

```json
{
  "outcome": "completed | no_action | needs_human | failed",
  "summary": "what was actually completed or why no action was taken",
  "error": {
    "code": "",
    "retryable": false,
    "authorization_required": false,
    "side_effect_state": "none | confirmed | unknown"
  }
}
```

The result contains no action list, target schema, dependency list, confidence
score, or service control action.

### Completion Evidence

The final result and the persisted JSONL events form one completion contract:

- `completed` with `side_effect_state=confirmed` requires at least one matching
  completed effectful tool event or a persisted execution receipt;
- an explicit repair or write request cannot be completed by a read-only
  diagnosis, recommendation, or promise of later work;
- a completed tool event is evidence of execution, not proof that arbitrary
  business content was correct; the Agent must still perform the requested
  verification and summarize the observed result;
- if an effectful call started but completion evidence is missing, the run is
  `unknown` and enters read-only reconciliation;
- if no effect started and execution cannot proceed, the Agent returns
  `needs_human` or `failed` with a concrete reason.

The service may reject a structurally inconsistent result, such as
`completed + confirmed` without a completed effect event, because that violates
the runtime result contract. It does not reinterpret the business decision or
invent a replacement action.

### Run Store

Replace the two universal execution tables with one `agent_runs` row per
`reply_task` execution generation. It stores:

- task ID and execution generation
- `pending`, `running`, `completed`, `failed`, or `unknown` status
- Codex session ID and transcript boundaries
- final result JSON
- structured error JSON
- timestamps

The row has a unique constraint on `(reply_task_id, execution_generation)`.
There is no hash or canonical snapshot. The original trigger remains in
`reply_tasks`; user-visible history, generated text, tool events, and receipts
remain in `reply_attempts`.

## Execution Flow

```text
reply_task pending
    -> evaluate required channel gates
    -> claim task generation once
    -> create/claim agent_runs row
    -> native codex exec
    -> Agent reads evidence and executes CLI/MCP tools
    -> persist transcript tool events and final result
    -> complete reply_task and agent_runs
```

The service does not create actions after the Agent returns and does not convert
one outcome into another.

## Duplicate and Retry Semantics

The service prevents only execution duplication:

- one active run per task generation;
- a completed generation is never automatically run again;
- process recovery resumes the existing Codex session when no external effect
  is in flight;
- manual or bug-fix reruns rotate `execution_generation`.

The Agent prevents business-side duplication by reading prior tool receipts and
querying live external state before an effectful command. An exact prior success
with the same target, operation, content, and material parameters is skipped.
Changed content, target, operation, or parameters are a new correction and may
execute.

No service hash, approximate text match, trigger-only block, or old-attempt
status may suppress a corrected action.

## Unknown Outcomes

An outcome is `unknown` only when an effectful tool call started but its
completion event or receipt was not captured, or when the CLI explicitly says
the server may have accepted the operation.

For an unknown run:

- do not resume or rerun the effectful command;
- retain the task and transcript for reconciliation;
- start a read-only reconciliation invocation attached to the original run;
- allow that invocation to query external state but not execute any write,
  approval, send, comment, reaction, or document-edit command;
- do not create a new execution generation for reconciliation;
- mark the original run completed if the effect is confirmed;
- create a new execution generation only if external state confirms no effect.

Definite command failures with no side effect use `failed` and may be retried.
The service relies on structured process/tool-event fields rather than matching
human error strings.

## Required Business and Security Rules

Only these restrictions remain:

- current OA task ownership and explicit approval SOP requirements;
- internal-personnel subject identification and consistency, with the agreed HR
  exception;
- credentials, tokens, cookies, authorization codes, signed URLs, and local
  credential paths must not be sent externally;
- tasks requiring a non-ready channel remain pending rather than running an
  Agent without the required evidence or execution path.

These rules are supplied to the Agent. The service enforces only secret
redaction in persisted and externally rendered audit data; it does not enforce
business target selection.

### Evidence Reuse And OA Reads

Before asking for clarification, the Agent must use the evidence already
available to it:

- preserve and acknowledge confirmed facts instead of requesting the same
  values again;
- execute the provided OA detail, document, file, or other material read
  commands before claiming that the material is unavailable;
- when an OA trigger has a process/task ID, query its live detail and current
  task ownership before acting;
- when an OA trigger has no unique ID, use live read/search commands to identify
  a unique current task; applicant/title similarity alone is not sufficient;
- when multiple candidates remain, the task is already completed, or the task
  does not belong to the current user, do not execute an approval action.

These are Agent instructions and acceptance tests, not new service-side target
binding, material pre-reading, or compatibility fallback logic.

## UI and Observability

History shows the user-facing outcome, summary, channel, status, generated or
sent content, and relevant receipts. It does not show planner names, action
indexes, dependencies, confidence, internal validators, or universal execution
cards.

Operational diagnostics expose:

- channel gate status and last successful live probe;
- whether a login request is active or suppressed;
- Agent run status and Codex session;
- completed tool calls and safe receipt summaries;
- failed versus unknown side-effect state.

Audit mutations bind reviewed replies to an immutable attempt ID, which in turn
identifies the exact conversation and trigger message. They never rediscover a
target from a group title, display name, or message text. Manual resolution of
an unknown side effect is loopback-only unless the Audit service gains a real
authentication layer; its audit actor always comes from the configured service
principal and never from request data.

## Migration

The rollout order is:

1. add typed channel gates and verify DWS/Lark live probes;
2. add the single Agent result and `agent_runs` persistence;
3. route the universal task path through the direct Agent runner;
4. verify every current action category through Agent-owned CLI/MCP execution;
5. remove universal planner, validator, executor, and material-reader code;
6. migrate and drop universal execution tables and auth-backup state;
7. remove the obsolete auth archive from runtime data;
8. restart launchd, run live smoke tests, and clear recoverable backlog.

There is no dual-write period, legacy fallback, or permanent compatibility
adapter. Each migration step must leave one authoritative path.

## Testing

Focused tests must prove:

- DWS and Lark gates require status plus a real live probe;
- a false-positive local token status cannot start producer or Agent work;
- one login request is launched and repeated requests are suppressed;
- Agents cannot start auth commands through their instructions;
- one task generation creates one Agent run;
- malformed final JSON is normalized once by the local parser without another
  Agent invocation or any repeated tool call;
- direct CLI tool events and safe receipts reach History;
- confirmed facts in context are acknowledged and are not requested again;
- OA tasks with complete form data are read by the Agent before clarification;
- OA target ambiguity, completed tasks, and non-current-user tasks do not
  produce approval writes;
- diagnosis-only output cannot complete an explicit repair/write request;
- `completed + confirmed` without a completed effect event or receipt is
  rejected as an inconsistent result;
- definite failures retry, while unknown side effects reconcile before rerun;
- a corrected action with changed content is not blocked by an old success;
- Universal classes, tables, hashes, target normalization, auth archives, and
  service-owned material readers are absent.

The full suite, database migration test, launchd restart, DWS/Lark live gate
checks, one read-only Agent run, and one authorized end-to-end task must pass
before completion is reported.
