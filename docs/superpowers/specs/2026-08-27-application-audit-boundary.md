# Application Audit Boundary: Result-Only Contract

- **Status:** accepted current contract
- **Date:** 2026-08-27
- **Supersedes:** application-layer `side_effect_state`, `unknown`/`reconciled`
  outcome state machines, command and tool allow-list checks, and dedicated
  read-only reconciliation flows described by earlier specs.

## Decision

The service does not audit how an Agent obtained or executed a business result.
Skill loading, CLI commands, MCP tools, read/write mode and tool names belong to
that Agent's execution environment. The service consumes the final typed result
and advances the ordinary task lifecycle:

```text
pending -> running -> done
                  -> failed
                  -> needs_feedback
                  -> needs_human
```

`needs_human` is reserved for a rule gap that existing Skills cannot resolve.
Provider, network, authentication, parsing and material-read failures are
`failed` and use the normal retry policy.

## External action identity

When a provider returns one, the service stores only these facts:

```text
operation
 target
 provider_result_identifier
```

The values bind a later Agent turn to the same external object and let the Agent
avoid duplicating an action. They are not an authorization, read-only, or
reconciliation decision. Pure reads do not require receipts. If a provider does
not return a stable identifier, the Agent reports the operation as failed and
uses the normal Skill-driven read path on the next turn.

## Recovery and history

The service does not create `unknown`, `reconciled`, `pending_reconciliation`,
or `side_effect_state` values. An interrupted write, missing receipt, malformed
result or failed read is a normal failed turn. The next Agent turn reads current
external state through the applicable Skill and decides whether to continue,
revise or stop; the service does not launch a special recovery invocation or
inspect tool events to make that decision.

All original run, attempt, session, tool event and provider identifier records
remain append-only. A later result may update the run's current projection, but
must not delete or rewrite execution facts. Existing historical values with the
retired names remain visible only as historical data and never drive routing,
retry, notification or business judgment.

## Migration boundary

This contract changes code and new data only. Existing SQLite rows are not
rewritten by application startup and production data is not modified by this
spec. A separate, reviewed migration may reclassify historical projections after
backup and readback verification; until then, old labels are display-only.
