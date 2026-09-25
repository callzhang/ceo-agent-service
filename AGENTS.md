# CEO Agent Service Instructions

## Explicit approval for audit and safety behavior

Do not add audit, review, authorization, confirmation, safety-gate, effect-reconciliation, or other safety-policy logic as an incidental part of an unrelated feature or bug fix. Any such logic must be proposed and confirmed as a separate, explicitly scoped change before implementation. Keep its code, tests, documentation, and commit separate from the surrounding functional change; do not hide or silently introduce it through shared helpers, routing, retry, or status handling.

## Keep runtime plumbing simple

Do what the native Agent CLI does. Pass configuration the way the CLI accepts
it (inline where it takes JSON strings), connect MCP servers directly, and use
the CLI's own home directory. Do not add defensive layers of your own making:
no local credential proxies, no per-invocation temp files, no "was this built by
this adapter" ownership checks, and no "keep secrets off disk" machinery beyond
what the native CLI already does (it keeps MCP headers and env in
`~/.claude.json`). Each of these was removed on Derek's instruction after it
caused real failures (the MCP proxy broke OAuth for `memory_connector` on every
Claude turn). If you find another such layer, propose removing it.

## Concurrent agents in this working tree

Several agents edit this repository at the same time (Claude Code sessions and
Codex). Before editing, read `docs/agent-claims.md`, claim the files you are
about to change, and stage only your own hunks. Never revert or rewrite a
commit you did not author: build on top of it, or record the disagreement in
your claim row. Restarting the service deploys the entire working tree,
including other agents' unfinished edits, so verify the tree imports and tell
the other owners before you restart.

## Keep the documents true

Derek, 2026-09-18: when you change how the service behaves, update the
document that describes that behaviour in the same commit. `docs/architecture.md`
and `docs/runtime-mechanism.md` are read by every agent before it works here,
so a stale line there is not a documentation debt — it is the next agent acting
on something that is no longer true.

What counts: task lifecycle and terminal states, what runs periodically and
where it lives, result contracts and the checks that enforce them, outbound
message rules, recovery behaviour, route and fallback policy.

On 2026-09-18 the runtime document still said a task whose feedback cycles run
out ends `failed`, after a completed external action had been made to end
`needs_human`; it described neither the removal of the workspace file sweep nor
the three loops that became scheduled tasks. All of that shipped the same day.
Write the line when you make the change, not when someone notices.

## Running tests

Derek, 2026-09-25: do not run the whole test suite in this working tree while
the service is live. On 2026-09-25 a serial `pytest -q` (about 9,500 tests)
took 48 minutes. The same run took 11 minutes the day before. It overlapped
with two service restarts, pushed the load average past 30, and left the
console answering in 80 seconds. Run the test files that cover your change.
If a full run is really needed, run it in parallel (`pytest -q -n 6`, about
3 minutes) and not while a restart is in progress.

## Who restarts the service

Derek, 2026-09-17, reaffirmed 2026-09-18: restarting
`com.ceo-agent-service.main` is the heartbeat session's job
(`CEO 服务错误检查与修复`). No other session runs `launchctl kickstart`,
`launchctl kill` or any other restart of that label. After you commit, send
that session a message naming your commit and the files that need the
restart; it checks the queue is idle and the tree imports, restarts, and
reads back the new PID, healthz, queues, Attention and History.

Three things go wrong when a session restarts on its own:

- A restart deploys the entire working tree, including another session's
  half-written edits, under your commit.
- A restart kills whatever Agent turn is running. Reply task 98194 lost a
  turn that way on 2026-09-17, and there were 558 queued work-summary items
  the following night, each one a turn that a restart would discard.
- Nobody can tell afterwards who restarted. Two restarts on the night of
  2026-09-17 cost real time to trace, and all three sessions asked had to
  deny them in turn.

If the heartbeat session does not answer and the restart cannot wait, tell
Derek and let him decide, rather than restarting yourself. Restarts go
through `launchctl kickstart`; there is no Friday runtime restart path.

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
