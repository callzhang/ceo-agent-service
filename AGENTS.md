# CEO Agent Service Instructions

## Explicit approval for audit and safety behavior

Do not add audit, review, authorization, confirmation, safety-gate, effect-reconciliation, or other safety-policy logic as an incidental part of an unrelated feature or bug fix. Any such logic must be proposed and confirmed as a separate, explicitly scoped change before implementation. Keep its code, tests, documentation, and commit separate from the surrounding functional change; do not hide or silently introduce it through shared helpers, routing, retry, or status handling.

## Local Service Reload

## Current runtime contract

The current task lifecycle and execution-agent/audit-agent feedback contract
are defined in `docs/architecture.md`, with the state details mirrored in
`docs/runtime-mechanism.md`. Read these documents before changing task
routing, execution, audit feedback, revisions, recovery, or delivery status.

All task types use execution Agent -> audit Agent -> feedback -> revision.
Never add a `discard` action or a `discarded` status. Use `skipped` for an
intentionally non-executable item, `failed` for an unsuccessful run,
`needs_feedback` for an audit correction, and `needs_human` when the bounded
feedback cycle cannot resolve the issue. Never overwrite an original run;
corrections create a new revision and preserve the relation to the original
run and audit feedback.

This project is normally run by launchd as `com.ceo-agent-service.main`. Python code changes are not hot-reloaded by the running service process.

After every commit that changes runtime code, prompt rendering, routing logic, launchd config, or service behavior:

1. Restart the main service:

   ```sh
   launchctl kickstart -k gui/$(id -u)/com.ceo-agent-service.main
   ```

2. Verify the service is running on a new process:

   ```sh
   launchctl print gui/$(id -u)/com.ceo-agent-service.main | sed -n '1,80p'
   ```

3. Check that there is no unresolved `failed` or `processing` backlog before reporting completion.

Do not assume a committed fix is live until the launchd service has been restarted and verified.
