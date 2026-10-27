# History polling and SQL artifact closeout — 2026-10-27

## Scope and coordination

Derek authorized fixing History timeouts and the three historical SQL artifact
tests, checking the existing repair task first, keeping an isolated worktree,
using substitute providers and no runtime legacy compatibility. Baseline48d171838
was normally fast-forwarded from main.「定时修复 CEO agent」was inspected and sent
exact file claims; its work is report/system-contract/deploy-gate repair. This
change does not edit its deploy, system-contract, report or migration-selector
files and does not start a competing deployment.

## Expected versus actual, and cause

History should eventually display a pending response even when it exceeds the
10-second polling interval. The previous effect depended on a refresh epoch
incremented every10seconds. Every increment ran cleanup (`abort`) and replaced
the pending request, preventing a consistently slow response from completing.
The new fake-timer regression observes an aborted signal after30seconds on old
code (1failed/19passed); the same regression passes after the minimal pending
ref prevents automatic refresh while a list request is active. Settling releases
that ref only for the current request generation. Explicit filter/page changes
and unmount still abort obsolete work; stale responses remain ignored; errors
release the request so later polling can succeed.

A fresh production probe on the previous release returned20rows in1.212seconds;
the actual old production UI also loaded page2 (21–40, total6416 observed).
This shows the earlier server latency is not continuously reproducible. The
change fixes the proven frontend starvation under slow responses; it does not
claim an unmeasured SQL speedup or a proven cause for every previous30-second
server timeout. No database query/index/schema change is included.

The three artifact failures were reproduced before editing (3failed/2.86s):

- The frozen Store21.1 source constructor called the live schedules creator.
  Its505-object hash drifted to6f40b1e… solely from the later notification column.
  Replacing only that source dependency with the existing frozen schedule DDL
  restores the original920b7eed… hash; the expected hash is unchanged. This is an
  explicit historical source tool, never a current runtime compatibility path.
- The complete current assembly SQL document lacked that same already-shipped
  column. Deterministic regeneration changes exactly one line, adding existing
  `notify_on_completion`; the catalog/executable checks pass without altering
  the current declarations, marker, field map or retirement inventory.

## Focused verification

- `tests/test_task_run_entity_assembly.py`:17passed/2.61s, including all3formerly
  failing cases, unchanged frozen digest, current field map and actual SQL execution.
- `HistoryPage.test.tsx`:20passed/2.29s, including slow30-second response,
  obsolete filter/unmount cancellation, stale response rejection, error recovery
  and existing visible automatic refresh.
- Current Action / adoption / fixed-declaration regression files:28passed/46.28s.
- TypeScript `tsc --noEmit`:pass; owned Python Ruff and diff checks:pass.
- Production baseline476objects, SHA256
  `105891b5280133fa10cf3fbfc66c93570cfcdb7928c7f3be8edbf47810686754`,
  exact current declarations and empty FK findings. No DDL/data migration.

Python test processes keep the repository's2GiB/300s ceilings; no full local
suite is run. Frontend runs use one worker and a2048MiB Node heap cap. Providers
and model calls are not used by these cases; no production sends or test jobs.

## Publication and cleanup

Independent code review found no blocking issues. A non-intercepting test
monkeypatch was removed; the unchanged exact catalog digest and explicit absence
of the later column remain the source regression assertions. Standard exact-target
gate at eeeb5b5e ran to97% with no observed assertion failure, then stopped at
300seconds on `test_scheduled_audit_receives_exact_consumer_document_return`.
Production was not changed. Upstream850f451c3 separately raises the budget to
10minutes; it has not been adopted by this task because the supplied rules
require5minutes and explicit authorization has not yet been verified. Derek subsequently explicitly approved adopting the separate10-minute gate
change for this release, retaining2GiB and all tests. The failed5-minute attempt
remains recorded and is not relabeled as passed. Production API/UI readback and
authorized own-artifact cleanup remain pending; no deployment backup was created.
Implementation/test success is not a live-release claim. The prior report's
business-publication failure remains the other task's responsibility.
