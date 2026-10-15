# Console query performance

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
