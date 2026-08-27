# Runtime Contract Simplification

## Purpose

Remove application-level policy that duplicated the installed Skills and
provider runtime. A failed Agent turn must be represented as an ordinary
failure and retried through the normal Consumer → Audit flow. The application
must not invent a second reconciliation workflow or decide whether a provider
tool was read-only, effectful, or unknown.

## Approved contract

1. Consumer Agent A forms a typed candidate from the applicable business and
   operation Skills. Audit Agent B applies those Skills to the candidate and
   executes an accepted candidate.
2. The service validates only the typed result shape, proposal/revision
   identity, lease ownership, and the minimal provider identifiers needed to
   correlate a retry.
3. Provider command names, MCP tools, Skill receipts, readback procedures, and
   tool-event effect classification belong to the Agent/runtime capability.
   They are not application gates and must not turn a valid typed result into
   `runtime_route_unavailable`, `agent_cli_command_unreviewed`, or an invented
   recovery status.
4. `effect_started_count`, `effect_completed_count`, `effect_failed_count`,
   `effect_unreviewed_count`, and `side_effect_state` are historical telemetry
   only. They must not veto route selection or create a special retry path.
5. `unknown`, `reconciled`, `pending_reconciliation`, and
   `runtime_effect_policy_violation` are not written by new code. A provider,
   transport, parsing, or dependency failure is `failed`; the next normal turn
   rereads current business state through its Skill and retries as appropriate.
6. A confirmed provider result identifier is retained for correlation and
   duplicate detection. It does not cause the service to execute a command or
   ask the user to perform work that the Agent can perform through its Skill.
7. `needs_human` is reserved for a genuine business-rule decision that cannot
   be determined from available evidence or a reusable Skill. Technical
   failures and missing runtime evidence remain `failed` and are retryable.

## Retry behavior

```text
Consumer failure or provider failure
  -> persist failed attempt and its evidence
  -> select the next eligible configured route
  -> run the normal Consumer → Audit flow
  -> complete, request feedback/revision, or remain failed after retry budget
```

The service does not start a read-only reconciliation turn, infer an external
effect from an unrecognised command, or convert a failed turn into
`needs_human`. Existing historical values may remain visible as immutable
facts until their owning task is rerun; no new execution may depend on those
values.

## Non-goals and change control

This contract does not add a new authorization, safety, review, or command
allow-list layer. Any future policy that changes whether an Agent may execute
an operation must be proposed, reviewed, tested, and committed as a separate
explicit change. It must not be introduced incidentally through routing,
prompt rendering, retries, or status handling.

## Verification requirements

- Unit tests prove a route remains eligible when only historical effect counters
  or an unrecognised provider event is present.
- Unit tests prove a missing session/effect probe does not block a normal retry.
- Prompt tests prove the canonical prompt contains the Skill-first contract and
  no recovery/reconciliation policy text.
- Runtime changes require launchd restart and a read-only production check of
  the new PID, route pauses, and failed/processing backlog.
