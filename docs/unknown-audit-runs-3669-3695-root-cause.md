# Unknown Audit Runs 3669 / 3695 Root Cause

Investigation date: 2026-08-22. Production timestamps below are stored as UTC;
China Standard Time is UTC+8. All production verification in this investigation
was read-only. No external action was replayed.

## Conclusion

Neither run was caused by the later `codex_oauth` / `codex_api` route pauses.
Those pauses occurred during reconciliation, hours after the original writes.
There is also no evidence that a service restart interrupted either run: the
restart evidence near run 3669 precedes its start, and no restart is present near
run 3695.

The shared system defect was a non-atomic boundary between an external write and
the Audit terminal state. The service persisted streamed start/completion events,
but it did not durably prepare a one-shot operation identity before dispatch or
persist the controlled tool acknowledgement independently of the Codex process.
Terminal settlement still depended on a later model result, target readback, and
delivery ledger entry. A process/session/result failure in that interval therefore
discarded decisive evidence and forced `unknown`.

Run-specific triggers were:

- **3669:** both reviewed writes returned successful controlled tool events, but
  no independent execution receipt was stored. Only the OA readback completed;
  the direct-chat readback did not. The direct notification used
  `chat +dm --content`, while the delivery ledger recognized only `--text` and
  only a reply-to-trigger recipient. Therefore the run could not satisfy the
  terminal external-readback/delivery-ledger gates after the model turn ended.
- **3695:** Consumer produced prose describing a DWS command, but no executable
  `argv`. The Audit contract gate rejected only an explicit `False`; a missing
  contract flag was accepted. The actual reviewed write consequently did not
  match the proposed action identity and had no `action_index`. Session replay
  later presented the same completed call with a different call ID; the existing
  action-index deduplicator could not recognize it and persisted a second
  lifecycle. Accounting then saw two completions for one proposed action and
  correctly refused to claim a single known effect. The duplicate rows prove
  duplicate accounting, not a second external send.

## Run 3669 timeline

| UTC | CST | Durable evidence |
| --- | --- | --- |
| 05:59:56 | 13:59:56 | Reply task 218355 created for an OA pending scan. |
| 06:00:31 | 14:00:31 | Consumer run 3665 started after the nearby service restart. |
| 06:06:24 | 14:06:24 | Consumer run 3665 completed with two canonical argv actions: approve OA task, then notify the applicant by `chat +dm --content`. |
| 06:06:25 | 14:06:25 | Audit run 3669 started, operation `agent-task:218355:initial:proposal:0`, Codex session `01a022ed-907c-70f1-9e9e-2cd10fc39a64`. |
| 06:07:26 | 14:07:26 | Event 17: OA approval write started, action index 0. |
| 06:07:37 | 14:07:37 | Event 18: OA approval write completed with a validated result digest. |
| 06:07:41 | 14:07:41 | Event 19: applicant direct message started, action index 1. |
| 06:07:56 | 14:07:56 | Event 20: direct message completed with a validated result digest. |
| 06:08:09 | 14:08:09 | Events 21-23: three post-write reads started. |
| 06:08:11 | 14:08:11 | Event 24: only the OA detail read completed. The chat/schema/task reads did not close. |
| 06:09:04 onward | 14:09:04 onward | Read-only recovery attempts began. Original failure text was repeatedly overwritten by recovery errors. |
| 14:12:57-14:47:46 | 22:12:57-22:47:46 | Three routed runtime attempts were later recorded for reconciliation; two completed, one failed as `runtime_unclassified`. These are not the original execution. |

Final durable counters before this fix: 2 effect starts, 2 effect completions,
0 failures, 0 execution receipts, no final result, and no sent-reply ledger row.
The session path index shows the original Codex session continued until
14:09:02 CST, after both writes. The session file is no longer present, so the
exact first error code (`codex_result_*` versus the subsequent readback/ledger
gate) cannot be recovered. The database design overwrote it; this fix adds an
append-only state-error history so future incidents retain that distinction.

## Run 3695 timeline

| UTC | CST | Durable evidence |
| --- | --- | --- |
| 08:44:47 | 16:44:47 | Consumer run 3694 started for the Learning Group request. |
| 08:45:59 | 16:45:59 | Consumer completed with one `dingtalk-chat` action whose payload contained only `content`; its operation field was prose containing a command. |
| 08:46:06 | 16:46:06 | Audit run 3695 started, operation `agent-task:219005:initial:proposal:0`, session `01a0237f-d4ba-7c13-9b5d-cccea1114e54`. |
| 08:47:03 | 16:47:03 | Event 11: actual `agent_cli.dws chat +send-to-group` started. It could not bind to the proposed identity, so no action index was stored. |
| 08:47:29 | 16:47:29 | Event 12: the write completed with a validated result digest. |
| 08:47:29 | 16:47:29 | Events 13-14: group-message readback started and completed. |
| 08:48:06 | 16:48:06 | Events 15-16: the same command arguments and result digest were replayed under a different call ID and persisted as another lifecycle because no action index existed. |
| 14:14:05-14:47:06 | 22:14:05-22:47:06 | Three later Codex reconciliation attempts completed. They are recovery activity, not the original cause. |

The session index records 60 lines and a stable mtime of 16:48:03 CST. In the
incident revision (`80a4d1`), session MCP replay ran only after typed-result
parsing, so the replay evidence also proves that the original process reached
post-parse validation. The expected and actual action identities did not match;
two successful lifecycles for one expected action forced
`audit_execution_evidence_mismatch` behavior.

## Service, process, route, and error evidence

- The launchd service root and database path were verified as
  `/Users/derek/Documents/Projects/ceo-agent-service-release` and
  `/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`.
- SQLite reported WAL mode and `integrity_check=ok`.
- Incident code was commit `80a4d1` (14:00:15 CST). Nearby runs 3663/3664 have
  `service_restart_before_effect` at 14:00:29 CST; run 3669 began six minutes
  later. Other runs completed concurrently. No equivalent restart evidence is
  present near run 3695.
- The generic `errors` table has no service/process error tied to either original
  window. `/tmp` service logs contain historical restarts, database-lock errors,
  and network errors but no timestamped line attributable to either run.
- Current route pauses (`runtime_unclassified`,
  `codex_transport_disconnected`) opened during later recovery. They explain
  delayed reconciliation only and cannot explain the original unknown state.

## Systemic scope

At the read-only production snapshot there were 2,206 agent runs:

- 2 were still `status=unknown`: 3669 and 3695.
- 1 additional run, 3690, had been terminally handed to human review while
  retaining `side_effect_state=unknown` after an open write lifecycle.
- 156 runs had entered reconciliation; 153 had reached a terminal state with
  the effect confirmed or a controlled failure, while the three cases above
  retained ambiguity.

This is therefore a low-frequency but systemic crash-consistency defect, not
two corrupt rows. The external-write/terminal-result interval was shared by all
Audit writes.

## Production fix

1. Reject any proposed write that lacks a mechanically parsed executable
   contract before starting Audit runtime. Prose is never dispatch authority.
2. Derive an exact, action-indexed, one-shot authorization for every approved
   write. Persist it in `agent_effect_intents` as `prepared` before the runtime
   can call a write tool.
3. At the local `agent_cli` boundary, validate the complete command identity,
   atomically transition the intent to `dispatched`, and only then launch the
   external process. A consumed authorization cannot be reused, including by
   session replay or a second call ID.
4. On successful tool return, persist `acknowledged` and the safe execution
   receipt in one SQLite transaction before returning the MCP result to Codex.
   This acknowledgement remains writable if the supervising run has meanwhile
   become `unknown`, so a service/CLI disconnect cannot discard it.
5. Reject dispatch when the parent run is terminal or has no live lease.
6. Accept both `--text` and `--content` for reviewed DingTalk delivery ledger
   recording, including an explicit `chat +dm --to` recipient that is separate
   from the trigger sender.
7. Append the first unknown cause and every reconciliation deferral to
   `agent_run_state_events`; later route failures no longer destroy the original
   diagnosis.

## Remaining irreducible window

The local state machine can prove `prepared`, `dispatched`, and
`acknowledged`. It cannot make a third-party API transactional with SQLite.
If the external request is accepted and the tool process dies before recording
the acknowledgement, the intent remains `dispatched`; the service must keep it
unknown and use the target-specific read-only reconciliation adapter.

The complete elimination of this final window requires the external API to
accept the local authorization as an idempotency key or return a stable external
operation/message ID that can be queried. DWS operations that do not expose
either property cannot be retried safely. The implemented one-shot intent
minimizes the window and prevents local duplicate dispatch; it does not pretend
that an unconfirmed third-party write is known.

## Runtime-route retry-budget correction

The later `runtime_route_unavailable` task failures were a separate scheduling
defect. `AgentOrchestrator` already returns `failed_retryable` without a second
same-process turn when no healthy route can satisfy a turn. However, the worker
did not include that code in its active-recovery wait set. Once a reply task had
used its ordinary attempt budget, the worker converted the transient wait into
`failed`.

The worker now preserves the task as `pending` and schedules its normal
short active-recovery retry at the budget boundary. This applies only after the
router has already classified the condition as `runtime_route_unavailable`;
capability/surface errors retain their distinct error codes and are not hidden
by this retry policy. No external action is started by this transition.

The orchestrator also distinguishes a first unavailable result from a later
worker cycle. The first cycle returns immediately, so it cannot hammer a paused
route. If the task is subsequently re-claimed with the same deferred error, it
opens exactly one fresh Consumer or effect-free Audit turn. That fresh turn
still goes through normal route-health validation; if no route is ready it
defers again, and if one is healthy it can progress. The Audit path remains
blocked whenever its prior run has any possible side effect.
