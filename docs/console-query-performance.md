# Console query performance

## Follow-up acceptance on 2026-10-15

The earlier production acceptance below is a historical measurement, not proof
of current responsiveness. Fresh `fca229bb` HTTP requests timed out while new
snapshot diagnostics recorded queue-summary reads of 97–169s and one failed
History read of 158s. A separate read-only Status build took 4–5s; two same-process
builds took 11.63s each. A user-authorized 32-second pause of the other local
Email test process group was fully resumed afterward: healthz returned in
0.113s, while Status still exceeded20s and History10s. Other tests are not a
sufficient single-cause explanation.

Attempt metadata fields are VIRTUAL generated columns over `payload_json`.
The current14,917 Attempt rows contain36,821,166 payload bytes. Existing trigger,
status and run-recovery indexes each cover only part of the queue read, so a
metadata-only SELECT can still read and decode JSON. The source query, index
coverage and actual I/O must be verified separately. Per-specialized-queue
slow diagnostics now record only the reader name and duration, preserving the
existing fresh-snapshot, count and exception contracts.

## Scope and evidence

This repair covers the Overview Status API, History lists, filters, pagination
and chart ranges. It changes read queries only. Persisted lifecycle states,
provider receipts, audit decisions, source IDs, count/order/filter semantics and
external execution are unchanged.

On 2026-10-14, the local production HTTP baseline returned Status in 4.164s.
Both the default History list and the list without its chart exceeded a 45s
client deadline. Health responded in 0.049s; that readiness result did not
establish that the Console queries were responsive.

Diagnosis used a consistent, read-only online production SQLite copy (480,907,264
bytes, `PRAGMA quick_check = ok`). The source runtime was `bdc98bb3`; the relevant
Store, audit-web and route code matched development `30595cd8` byte for byte.
Measurements were sequential, with bounded SQLite progress handlers for slow
queries. No full test suite or benchmark ran against the production checkout.

## Confirmed causes

- History's compact-key query put the union projection behind a named CTE.
  SQLite expanded/revisited the projection when applying its outer count and
  ordering. An equivalent inline subquery retains those predicates and lets
  the planner execute the compact union once. On the copy, the original query
  exceeded the bounded profile; the inline query completed in 0.385s and
  returned 35,323 rows in the independent complete-key oracle's exact order
  (SHA-256 `fa645449aa45b217324cc62491ada698b5d3b98acc92fc0414483e9a494a9767`).
- Attention selected the latest Attempt with a correlated maximum for every
  failed/blocked candidate. Store's finite projection prevented a trigger-key
  seek. A single grouped latest-ID set retains the original cross-channel
  message grouping and NULL behavior. Identical ordered result columns took
  0.197s instead of 4.742s on the same copy.
- The Attempt queue's reused current-row CTE was evaluated repeatedly for
  aggregate and latest-error reads. Explicit materialization reuses the exact
  current rows within one statement. The identical eleven grouped rows took
  0.255s instead of 0.900s.
- Email action queue aggregation started with the processed-classification
  status index and made scattered reads of almost every action. Keeping the
  action table as the outer join loop scans its rows once and seeks the exact
  classification primary key. The existing current-plan predicate excludes the
  same 357 stale actions from 10,311 total actions. Identical three grouped rows
  took 0.013s instead of 0.341s on the copy; schema and indexes are unchanged.
- History's page cache keyed only on source tables, so a 100-row request could
  reuse a 20-row response, and new records could leave the next request's total
  stale. That cache and its background refresh/prewarm helper have been
  removed. Each page is read from its existing database snapshot.
- History status filtering and the monthly chart repeatedly searched all later
  successful Attempts for each failed record. A scalar materialized latest-ID
  set retains the exact channel/conversation/trigger key and strict public-ID
  comparison. It is evaluated only when the status expression is needed, so
  key-only pagination still prunes display/status work. On the copy, all 6,275
  monthly input IDs matched the original status map, including 53 exclusions.
  The complete 6,932-event chart matched the original buckets and series.
  Within one chart read snapshot, failed Attempts with the same message key
  share one current-task read; WeChat delivery is read once per current task.
  These values live only within that request, with no persistent chart-data
  cache added.

The regression fixtures measure deterministic SQLite work rather than wall
clock thresholds. Cache tests cover changing page size and immediate writes;
query tests retain exact source keys, status/filter behavior and ordering.

## Release validation

The complete registered routes on the consistent copy returned the default
20-row list in 1.785s on the first request and 0.607s subsequently, 100 rows in
0.707s, page 2 in 0.416s, failed in 0.446s, approval type in 0.392s, done in
0.430s, Chinese text search in 1.069s, `q=history` in 0.698s, and the monthly
chart in 1.327s. The complete Status payload took 0.745s. These are copy-profile
results, not production release receipts. Live measurements follow after formal
deployment.

The final combined query/status/cache/chart regression set passed 92 tests in
25.49s. The focused web/API selection passed 169 tests; the existing interrupted
reply/task/meeting/scheduled recovery and native no-resend selection passed 36.
Independent read-only review found no blocking issue. The existing current-
failed-only error-body projection test also passes: materializing current
Attempt metadata does not load successful/skipped error bodies, and the exact
failed-ID read retains the original equal-timestamp error tie behavior. Ruff
and `git diff --check` passed. No full suite was run while the service was live.

## Production acceptance

The normal updater deployed runtime `832da02e` with the explicitly authorized
`--skip-quiet-wait` option. Production and development runtime code matched;
launchd reported a new running supervisor PID `37209`, and `/healthz` returned
200 in 0.022s. Sequential local HTTP readback after the restart returned:

| Request | Elapsed seconds | Readback |
| --- | ---: | --- |
| Overview Status, first / repeated | 0.860 / 0.832 | 13 queues, current counts and PID |
| History 20, first / repeated | 1.617 / 0.711 | 20 rows, total 35,325 |
| History 100 | 1.141 | 100 rows, same total |
| History page 2 | 0.763 | 20 rows, same total |
| History failed | 0.905 | 13 matching rows |
| History done | 0.926 | 19,740 total matches |
| History approval type | 0.921 | 597 total matches |
| History Chinese text search | 3.055 | 424 total matches |
| Chart 24 hours | 0.237 | 84 events |
| Chart one month | 2.051 | 6,933 events |
| Attention | 0.204 | First 15 of 17 items |

The browser rendered the Overview status/queues, the History list and chart,
then exactly 100 History links after selecting the 100-row size. Switching the
chart to one month rendered 6,933 events and all series, with loading complete.
The acceptance tab was closed afterward. The live database progressed between
the diagnostic copy and release, so production totals legitimately differ.

The existing System health state remains `degraded`; the query repair does not
settle unresolved business failures. Failed queue objects' latest timestamps
precede this deployment, Email startup readiness is 3/3 and the dispatcher tick
continued after restart. The existing retained standard backup
`auto-reply-2026-10-14.sqlite3` passed read-only `PRAGMA quick_check = ok`.
Disposable diagnostic databases and generated navigation artifacts are removed
at task end; regression code and this concise evidence remain in the repository.

## launchd resource-class boundary

Both the installed main job and repository template omitted `ProcessType`.
The local `launchd.plist(5)` manual and [Apple's launchd manual source](https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5)
specify that an omitted value applies Standard CPU/I/O resource limits.
Interactive is appropriate when application responsiveness depends on the job
and Adaptive XPC activity cannot be used. This service responds through local
HTTP rather than XPC. The template now declares Interactive, without changing
program arguments, environment or the existing 4096-file limit. Existing
installed settings are updated for the same key before the formal deploy
re-bootstraps the job; the updater does not overwrite the whole native plist.

A regression first observed the missing declaration and then passed with it.
The observed standalone/live discrepancy motivates this configuration repair;
actual latency improvement still requires the live post-restart comparison.
The experimental three-index metadata query is not published: it adds about
20% VM work and its single-run wall-clock gain was not stable. No database
schema, unregistered index or new query cache is introduced by this repair.
