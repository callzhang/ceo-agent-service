# Dashboard (approved in chat, 2026-10-20)

Replace the History navigation label and workspace with Dashboard at the existing
/history URL. Keep historic detail URLs, chart ranges, filters and pagination.
No visible Dashboard/date heading. The first content is one compact Service row:
process state, system health, snapshot and refresh; technical details expand.

The next section has Failed, Active tasks and Queued tabs, with separate counts.
Failed is exactly the current Attention projection, including unresolved failures,
pending OA workflows and current persisted human choices. Preserve grouping,
original states, details and links. Do not introduce filtering or change policy.
Attention counts problems; the other tabs count execution tasks, so counts are
not additive. A problem may link to an active or queued task.

Initially select Failed if nonempty, otherwise Active. Never replace a manually
selected tab during refresh. Keep explicit error/loading states; an unavailable
read must not be rendered as zero or silently choose an empty tab.

Active shows source, task, current Agent/runtime/model, stage, start/last-activity
timestamps and retry round when recorded. Only valid current execution leases
qualify as active; do not hydrate native trajectories to draw the list.
Queued includes initial and deferred retries, source, eligible time, last error,
actual retry ordinal and details. First execution is not a retry. Retry ordinal
must come from source attempts/runtime evidence; Audit revisions are not retries.
Unknown counts remain unknown. Exclude obsolete task generations and superseded
business objects. Large queues paginate with accurate totals.

The existing event chart and searchable History follow the queue. Keep navigation
and 320px/mobile layout usable. No execution controls or new external actions.

Verification: focused backend projection/endpoint regressions, frontend tab and
refresh/error/retry tests, retained History/navigation tests, typecheck/build,
rendered desktop/mobile validation, then ordinary push/deploy and live readback.
