# Durable Audit Effects and Runtime Wait Design

## Context

At 2026-08-22 01:11–01:25 CST, the working tree contained an uncommitted
implementation for Audit write crash recovery. It is not part of `main`:
`main` and `origin/main` were both commit `469b00b`. The working-tree patch
adds durable write-intent state and diagnostics to prevent two observed unknown
Audit runs from losing the evidence needed for safe recovery.

The related task error `runtime_route_unavailable` is a separate problem. A
route can correctly decline an unsafe turn when no healthy route has the
required capabilities. The worker currently treats that retryable condition as
terminal after the ordinary task-attempt budget. It must instead wait for a
future route-health change without replaying any write.

## Goals

1. Make each approved Audit write locally one-shot across Codex session replay,
   process interruption, and a changed MCP call identifier.
2. Persist successful controlled-write evidence before returning it to the
   model process, so reconciliation can use durable evidence even if that
   process does not provide a final result.
3. Reject prose-only or otherwise non-executable write proposals before any
   Audit runtime starts.
4. Preserve the initial cause of an unknown run and subsequent reconciliation
   causes independently.
5. Keep a task pending, with bounded retry scheduling, while an eligible
   runtime route is temporarily unavailable. Do not reclassify that state as a
   business failure merely because ordinary task attempts have been consumed.

## Non-goals

- This does not claim distributed atomicity with DingTalk or any other external
  API. A process death after an external request is accepted but before the
  local acknowledgement remains an `unknown` effect and is read-only
  reconciled.
- This does not replay a previously dispatched effect, bypass Audit approval,
  or convert route unavailability into a new runtime route.
- This does not alter unrelated untracked attachments or generated graph files.

## Design

### Durable effect lifecycle

For every non-dry-run approved `agent_cli.execute_reviewed_write` action, the
Audit runner derives a canonical authorization from the proposal operation,
revision, action index, command identity, and target identifiers. It writes
that authorization to `agent_effect_intents` in `prepared` state before
starting the Audit process.

The write-only MCP tool receives the authorization list plus only the absolute
database path and parent Audit run ID. It validates the actual canonical argv
against the authorization, changes exactly that intent from `prepared` to
`dispatched` in SQLite, then launches the external command. A second call with
the same authorization fails locally before an external command begins.
Initial and recovery turns derive the same authorization for the same logical
action, and the database additionally enforces uniqueness on
`(agent_run_id, receipt_operation_id)`, so a changed token cannot bypass the
fence after lease ownership changes.

On a successful controlled command, the MCP tool stores the result digest and
a normal execution receipt in the same SQLite transaction, then changes the
intent to `acknowledged`. Acknowledgement is permitted for an `unknown` parent
run because it records observed evidence; dispatch is permitted only for an
active leased run.

### Proposal and delivery contracts

Audit accepts an effectful proposed action only when it has a mechanically
parsed command or an effectful registered MCP call. A DWS command must include
its non-interactive confirmation. Prose in an `operation` field is not a
dispatch contract.

The direct-message receipt parser recognizes both `--text` and `--content`.
It records a verified `chat +dm` delivery only when the metadata and command
identify a direct message and the command has a nonempty recipient and body;
it must not require the recipient to be the triggering sender because an
approved Audit action may notify another party.

### Unknown-state history

`agent_run_state_events` appends the first transition to `unknown` and every
later reconciliation deferral. It is diagnostic history only; the current
`agent_runs` state remains the source of operational scheduling.

Dispatch atomically changes the parent side-effect state to `unknown`. Event
accounting cannot downgrade that state while the intent is unacknowledged, and
neither explicit settlement nor expired-lease cleanup may terminally fail such
a run. A dispatched token is never re-armed; only a token that remained
`prepared` can be consumed by a later, approved recovery turn.

### Runtime route unavailability

`runtime_route_unavailable` has two meanings that must remain distinct:

- A terminal capability or configuration reason stays terminal and does not
  wait forever.
- A transient route pause, expired health snapshot, capacity condition, or
  transport condition remains a provider-recovery wait.

The router must carry that distinction in `RoutedCodexExecutionError`, and the
orchestrator must surface it as retryable. The worker must place a transient
case back in `pending` even at `max_task_attempts`, using the existing bounded
provider retry time. It must preserve the error code and must not invoke a
second runtime turn in the same worker pass.

## Data and safety invariants

- One logical `(agent_run_id, receipt_operation_id)` has one stable
  authorization and can move only `prepared → dispatched → acknowledged`.
- Dispatch requires a live, running parent run; acknowledgement requires a
  previously dispatched matching intent and a successful nonempty result
  digest.
- A terminal parent run cannot dispatch an unconsumed intent.
- Recovery remains read-only for every previously dispatched action. It may
  consume the same still-prepared authorization only when reconciliation has
  proved that the original runtime never crossed the local dispatch boundary.
- Runtime waiting does not mark the effect known, does not increase external
  dispatch count, and never bypasses capability checks.

## Acceptance criteria

1. Tests prove duplicate intent dispatch is rejected before the external
   runner is called and a successful acknowledgement produces a persistent
   execution receipt.
2. Tests prove a malformed/prose proposal becomes a revision request before
   Audit execution, and `chat +dm --content` records an eligible delivery.
3. Tests prove initial and reconciliation errors are both retained in history.
4. Tests prove a transient `runtime_route_unavailable` at the ordinary retry
   budget remains pending, while a terminal route reason remains terminal.
5. Targeted suites and the relevant broader suites pass; the feature commits
   leave no tracked files modified. The launchd service is restarted only after
   all intended source changes are committed, then its new process and task
   backlog are read back.
