# Agent Progress Watchdog Design

**Date:** 2026-09-28
**Status:** Approved for implementation by Derek

## Problem

A scheduled CEO report can legitimately take longer than one Agent turn. The
service currently kills a task after a 900-second total turn timeout, marks the
run continuable, then resumes the same Codex session in a later turn. That
splits healthy work, adds recovery overhead, and makes total elapsed time look
like failure. The service already receives structured provider events and
renews the persisted Agent run lease for each event; those events are the
correct liveness boundary.

## Decision

Remove the normal 900-second business timeout. Keep a high, two-hour emergency
process ceiling solely to prevent a leaked process from occupying resources
forever. Keep the normal five-minute idle watchdog: a turn that receives no
structured provider output for five minutes is interrupted and can resume its
persisted session. Raw stdout/stderr remains transport activity only; structured
JSONL events are the progress signal and are persisted as Agent run events.

The existing retry ceiling remains unchanged. Repeated identical Agent or
provider failures still back off and terminate after the configured consecutive
failure bound. A long but progressing task is not failed because of elapsed
wall time.

## Runtime behavior

1. The launchd task-agent environment sets `CEO_TASK_CODEX_TIMEOUT_SECONDS=7200`
and `CEO_TASK_CODEX_IDLE_TIMEOUT_SECONDS=300`.
2. `AgentTurnProcess.persist_line` renews the Agent run lease and records a
structured progress event for each valid provider JSONL event.
3. The process runner uses the five-minute idle timer to stop a silent process;
the two-hour ceiling is an emergency process guard only.
4. A timeout remains resumable when the provider returned a session id; the next
turn uses `codex exec resume <session_id>` and the existing task contract.
5. The service does not manufacture periodic “continue” messages for a healthy
turn. Recovery prompts are only added for service-restart recovery or typed
result correction.

## Compatibility and limits

This changes the task-agent process ceiling for all routes because timeout is a
process-level setting, not a scheduled-task option. It does not change retry
counts, route fallback, leases, or external-action contracts. A future
per-task timeout setting would require a separate design; this change keeps the
shared runtime simple and bounded.

## Acceptance criteria

- A process that emits structured output for more than 15 minutes is not killed
by the normal task timeout.
- A process that emits no output for 300 seconds is still terminated as idle.
- The emergency ceiling remains finite at 7200 seconds.
- Existing session-resume behavior and retry-ceiling tests remain green.
- Production runs the new launchd values, healthz is healthy, and no new stuck
scheduled task or failed weekly-report run is created by deployment.
