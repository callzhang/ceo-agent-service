# SQLite writer contention repair — 2026-10-11

The four Attention entries listed at this repair checkpoint were investigated
in the subsequent [Attention follow-up](attention-resolution-20261011.md).
That record separates terminal service handling, provider readback and the
remaining compensation decision; this earlier four-item count is historical.

## Implemented mechanisms

- `1db85483`: meeting Memory enqueue excludes already-enqueued historical jobs
  through indexed read-only ID discovery before requesting the writer. Its prior
  idle poll held the writer for 13.454 seconds while a reply claim waited 13.045
  seconds. Positive discovery rechecks exact job IDs and delivered-conclusion
  eligibility within the existing atomic insert transaction.
- `981575ce`: dispatcher terminal lease pruning uses a partial terminal-time
  index and `(terminal_at, rowid)` boundary instead of sorting and materializing
  a 10,000-ID exclusion list on every completion. It preserves exactly the
  existing newest 10,000 terminal claims and all active claims.
- `7aec844a`: email direct-action preflight excludes retired plans before loading
  classification/sibling payloads. Current-plan, account, dependency, active
  processing and retry checks retain their existing meaning.
- `d5435bc6`: Status materializes each current reply-task/email-action projection
  once per request. Counts, retries, latest item and latest error share that
  projection instead of rescanning it three times. Production-copy output hashes
  matched the prior implementation exactly; the API continues to read fresh data.
- `ef3891df` (deployed through `375e94b8`): Agent run claim, renewal, session
  binding and expired-run failure capture metadata/native references together,
  commit and close SQLite, then discover/read/parse native files. Previously,
  these four return paths hydrated native trajectories inside the writer
  transaction. The regression proves another connection can write during event
  and terminal-result hydration, while the returned metadata remains the exact
  operation snapshot.
- `0e6f0e46`: all nine dispatcher adapters perform empty probes without a writer
  transaction. Positive probes repeat the original candidate selection under
  the existing immediate transaction. Todo outboxes also probe for expired rows
  requiring their existing reconciliation transition; those transitions are
  preserved even when no queued item exists.

The 17:28–17:30 recurrence after the first three fixes was treated as evidence
that the work remained incomplete. Several writers waited 74–77 seconds while
their acquired work lasted only milliseconds. Those waits were not reported as
long transaction bodies. The metadata hydration boundary and remaining idle
dispatcher writers were repaired subsequently. No external migration was
running against production during that incident; the database redesign task
confirmed its migration work was limited to verification copies.

## Focused verification

These are separate runs; counts overlap and should not be summed as unique tests.

| Selection | Result |
| --- | --- |
| Agent metadata regression plus store Agent run/runtime attempt cases | 98 passed |
| Native trajectory/runtime worker lease/session/native selection | 16 passed |
| Dispatcher idle claims, consumer dispatcher and lease retention | 90 passed |
| Todo sync and task Memory write | 65 passed |
| Merged local main: metadata, idle claims, retention, Status projection, Cron scheduler, meeting Memory, consumer dispatcher | 169 passed |
| Merged local main: email direct-action/action-plan selection | 36 passed |

The new metadata test failed in all four original writer paths before the fix.
The nine empty-adapter cases also failed with `database is locked` before the
idle-probe change and passed afterward. A competing positive reply claim is
rechecked and never double-claimed. Existing live-owner protection, candidate
paging, recovery, generation and atomic completion tests pass.

On a verification copy, six concurrent writers performing 120 rounds of insert,
lease completion and idle meeting Memory polling completed in 3.92 seconds
without SQLite errors; `quick_check` returned `ok` and terminal retention remained
10,000. Full-suite testing was not run on the live service host.

## Production readback

Runtime checkout: `0e6f0e46`; supervisor PID `44092`; main worker `44095`, web
`44096`, email worker `44097`. Deployment completed through `python -m app.deploy`.

- Health, Status, Attention, History page and History data API returned HTTP 200.
- Latest Status read: 0.340 seconds; Attention: 0.057 seconds; History data API:
  0.116 seconds, returning five items. An earlier post-deploy Status read was
  0.679 seconds. These are individual live observations, not latency percentiles.
- At `375e94b8`, a 180-second kernel WAL write-lock observation included three
  new Agent runs/runtime attempts and nine scheduled-run records. Largest sampled
  writer hold: 0.283 seconds; no sampled hold exceeded one second.
- At final `0e6f0e46`, another 180-second observation included eleven new
  scheduled-run records and one new email classification. Largest sampled holds:
  worker 0.065 seconds, email worker 0.255 seconds; none exceeded one second.
- The monitor queried macOS `F_GETLK` on WAL writer slot 120 every 25 ms. Very
  short holds can fall between samples; these are observed durations, not a
  mathematical upper bound for every transaction.
- No new `SQLITE_BUSY` or `database is locked` entry appeared after the final
  web process started during these readbacks. Executable dispatcher due counts
  were zero at the final queue snapshot.
- Existing Attention has four entries: one failed reply item and three human
  decisions. The current failed reply reason is `audit_revision_exhausted`,
  without a SQLite lock error; it is outside this contention repair.

No production business action was manually replayed for verification. Temporary
copies, kernel sampling output and endpoint payloads are task-owned artifacts
and are removed after recording this summary.
