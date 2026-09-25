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
your claim row.

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

## Production checkout and deploys

Derek, 2026-09-25: the service does not run from this development tree. launchd
runs `com.ceo-agent-service.main` from its own checkout, `~/Services/ceo-agent-service`
(set by `CEO_SERVICE_ROOT` in the installed job; `app.config.service_root()`).
Nobody edits that checkout. It only moves to pushed `origin/main` commits, so
what goes live is always a complete commit, and any session may deploy.

After you push a change to runtime code, prompts, routing, launchd config or
service behaviour, deploy it:

```sh
python -m app.deploy
```

The deploy waits until no Agent turn or claimed item is in flight (it changes
nothing if the service never goes quiet within 30 minutes). It then backs up
the database, fast-forwards the production checkout, rebuilds the console when
it was not built from the checkout's current `frontend/` (a stamp, not this deploy's diff), checks the imports, restarts the job, and waits up to 15
minutes for health. If any step after the fast-forward fails, it rolls back.
Two deploys at once are serialized by the repository lock.

Do not run `launchctl kickstart` or `kill` on the job by hand. Do not edit,
build in, or run tests in `~/Services/ceo-agent-service`. Both are enforced:
the deploy installs git hooks there that refuse any commit, merge commit or
rebase, and `tests/conftest.py` exits when pytest starts in that checkout. If a
deploy reports the checkout diverged, move the listed commits to main and reset
production to `origin/main`. Committed-but-unpushed
work never goes live, so a restart no longer carries another session's
half-written edits.

After a deploy, read back the new PID, healthz, queues, Attention and History
before reporting completion. Never paste the settings API's secret fields.

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

Python code changes are not hot-reloaded: a pushed change is live only after
`python -m app.deploy` has finished and the readback above is clean.
