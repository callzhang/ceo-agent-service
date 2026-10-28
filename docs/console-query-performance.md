# Console query performance

> 本文是带日期的测量与修复记录，不是现行契约。现行代码里 `/history` 有 5 秒 TTL 的 `history_chart_cache`（按时间范围）和 2 秒的 `_RecentHtmlCache`（后台刷新），见 `app/audit_web.py`；下文“不新增查询缓存”仅指当次修复。

## Failed History projection correction, 2026-10-16

The failed-only Reply Attempt key query now filters the existing canonical
History status projection, and its page hydration calls that projection with
only the selected Attempt IDs. This removes the duplicate failed-candidate
status predicate and the hardcoded failed display status. The total, filters
and displayed page now agree for OA legacy labels, current pending tasks and
unresolved uncertainty. Other source branches keep their existing queries.
Synthetic regression coverage and the adjacent History/Attempt/Task Agent
matrix passed 342 tests; page-hydration instrumentation confirms that both
the private helper and canonical display projection receive only the two
selected IDs, then one ID on a smaller page.

A separate read-only diagnostic on a frozen source with 14,953 Reply Attempts
selected six canonical failed keys in 0.9182s, about 5,938,000 SQLite VM steps
at 2026-10-16T13:05:41Z. Key selection projected only `source_id` and
`occurred_at`; it did not select display bodies. This metadata diagnostic is
neither a cold or full API measurement nor evidence of a speed improvement
against the former candidate query. It does not establish production latency.

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

## Interactive release readback

Formal deployment moved production from86772bcd to27575931. The installed
plist was backed up and read back byte-for-byte, changed only ProcessType,
and compared as a parsed dictionary with every other key unchanged. launchctl
then reported `spawn type = interactive (4)`, supervisor26590 and HTTP26608.
This confirms the native setting was actually reloaded.

Initial startup readback returned Status in8.673/11.365s and History with its
chart in25.800s; the resource classification alone is not proof of eliminating
all host contention. After recovery, sequential frontend-equivalent requests
(`include_chart=0`) returned History20 in4.193/2.391s, History100 in1.486s,
page2 in2.512s, and Status in2.937s. All returned200; the page sizes were20/100/20
and total35332. A separate month chart returned in10.029s with6930 events.
Browser readback showed100 selected,1–100 of35332, the completed6930-event
month chart, and healthy on Settings. The temporary acceptance tab was closed.
These measurements show the earlier minute-scale timeout was removed in this
release; response times still depend on startup and host CPU/I/O contention.

The live Status health check at2026-10-15T06:53:10.860593Z was healthy with
zero violations,46 readable native delivery results and474 unavailable.
Meeting Memory had no pending/due/delayed/processing/retryable/failed or ghost
records. Persisted input404912 remained done after the restart, its recovered
Task run360429 completed, and WeChat delivery137 remained intentionally skipped
with its original pre-send failure retained. Historical queue failures and
Attention decisions remain visible and were not converted to success.

A final bounded64MiB/2MiB connection-cache experiment reduced RSS but did not
complete the required reverse-order and concurrent equivalence comparison
within90s. No cache setting change was published. The experimental database,
queries and measurement files are disposable task artifacts.

## Remaining host contention and authorized cleanup

A subsequent633f5e59 readback returned Status12.897s and frontend History16.537s
after that supervisor had already run16minutes. Those observations cannot be
attributed to startup alone. Native process accounting located56 orphaned
local CEO evaluation helpers aged6–7days, all parent1, with only loopback
LISTEN sockets and no established clients. Their aggregate ri_phys_footprint
was9.902GiB, including compressed/swapped accounting rather than only resident
RAM;29 working directories were in a previously cleaned Trash tree. The user
explicitly authorized SIGTERM for that exact PID list, separately from the
earlier temporary pause. No current service, current test or other application
was included. Process start identities, parent1 and no-client sockets were
rechecked immediately before cleanup; the first3 and then remaining53 exited.
All56 original identities were absent afterward, without SIGKILL or data writes.

The current runtime6c44b082 had just returned Status4.105s and frontend History
7.017s before cleanup. With the same supervisor9899 after cleanup, sequential
HTTP200 readback was Status2.854/4.553s, History20 4.369/2.313s, History1001.194s,
failed History1.344s, month chart3.846s and Attention0.501s. History counts were
35370 with20/100 rows as requested, failed count13, and the month chart6914 events.
Status remained healthy with0 violations and13 queues. A1-second native paging
sample after cleanup showed1531 swap-in pages and0 swap-out pages (16KiB/page),
compared with14616/22876 at the earlier high-contention sample. These are bounded
observations, not an all-load latency guarantee. They identify historical helper
resource retention as a concrete source of pressure alongside the repaired
query plans; the cleanup was not a change to audit or health classification.
