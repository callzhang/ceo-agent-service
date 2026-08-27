# Agent Operating Principles

These are the confirmed operating rules for the CEO agent runtime and recovery
flows. They are product behavior, not optional safety policy.

## Completion and retry

- The agent is responsible for completing the task through the applicable Skill.
- A failed attempt is retryable by default. Retry the same task through a new
  execution generation until it reaches a verified success or a concrete,
  documented terminal failure.
- Do not leave work in `pending_reconciliation`, `needs_human`, or an unexplained
  `processing` state when the agent can read the required data and act through
  the Skill. A retry exhaustion error is a failure to recover, not a successful
  terminal state.
- Before an external write retry, use the Skill's readback and the persisted
  idempotency/receipt records to avoid duplicating an already confirmed write.
  An unknown result is reconciled by read-only checks first; it is not silently
  declared successful.
- Immutable, low-risk artifacts (for example interview comments, summaries, and
  ordinary notifications) may reuse the existing artifact and retry delivery.
  Re-generation is needed only when the input or decision itself is invalid.

## `needs_human` boundary

`needs_human` is allowed only when the Skill-driven reads do not provide enough
decision information, or when the action crosses a genuinely human-only
boundary (conflicting evidence, unrecognized target, irreversible or sensitive
action, approval, budget, or a required human factor). Technical inconvenience,
missing context that the agent can retrieve, or a normal retry is not a reason
to escalate to the user.

## Runtime routing

- Runtime routes are configured in Settings and loaded from the persisted
  environment configuration. launchd must not inject a stale route override.
- If the primary Codex OAuth route is unavailable, the configured Codex API
  fallback is tried automatically. Route failures must be classified with the
  real provider/process cause; `runtime_unclassified` is not a substitute for
  capturing the available diagnostic.
- A route pause is temporary recovery state, not task completion. The service
  must probe/retry the route and verify the next execution before closing it.

## Review and command policy

- Do not add new audit, approval, or security gates implicitly inside unrelated
  feature work. Any new gate requires a separately confirmed design decision.
- Existing Skill contracts remain the source of truth. The agent should use
  registered Skill operations and their read/write verification procedures;
  it must not invent an extra user-facing review step for ordinary work.

## State and reporting

- Task state, attempt history, audit records, logs, and external readback must
  describe the same outcome.
- Never rewrite a failure or unknown record merely to make a quality gate green.
  Report the sanitized root cause, actions attempted, external-effect status,
  and the next retryable step until the task reaches a verified terminal state.
