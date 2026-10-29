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

## Publication and production acceptance

Independent review found no blocking issues. An ineffective monkeypatch was
removed; the unchanged505/920b digest and explicit absence of the later column
remain source assertions. The exact-target5-minute gate at eeeb5b5e stopped at
97%/300s, no observed assertion failure, before production changes. This failed
attempt remains recorded. Derek then explicitly approved adoption of the
separate850f451c3 budget change for this release:10minutes,2GiB, every test
retained,12-minute deployment backstop. Its17gate/memory regressions pass8.95s.
No budget was silently raised or gate bypassed by the History change.

The normally integrated exact target bdd2a230f passed the full standard gate.
The deploy then exited0 with `another deploy got there first; production is at
850f451c`. That other standard release already includes the reviewed eeeb5b5e
runtime fix. Root verified the exact installed files rather than repeating the
checkout/restart. Actual release operation deploy-988d2ebb-4fe0-4e98-a4f5-752aed67b8c4
succeeded at2026-10-27T09:32:12Z. Do not label bdd2a230f as the installed version.

Readback at09:34:11Z: production850f451c3, mainPID72598, health200;
History dingtalk page2 returned20rows in1.446s. All476SQL tuples retain the
pre-release digest, FK findings remain empty, Store26.1/Email54 unchanged.
Four implementation/artifact/test files match the tested working tree and the
frontend build stamp matches the installed frontend source.

Actual browser checks pass: page2 shows21–40; clicking next receives distinct
page3rows41–60; selecting failed finishes with0matching rows and resets page1;
clearing the filter restores the20-row list. Existing Task417569 detail shows
completed Attempt16137 and Consumer399752/Audit399753 under their original IDs.
Both owned verification tabs were closed. No send/retry/replay control clicked.
This closes the reproduced polling starvation and the three artifact failures;
it is not an assertion that all server queries always finish below10seconds.

Dated business baseline/readback: failed28/Attention1 before and after, pending6
and processing1→2 in normal traffic. Healthz is healthy; business health retains
one existing unresolved-feedback finding. The report417594 failure and explicit
system-Skill publication/recovery remain the other task's separate scope; this
release did not publish system contracts or replay reports.

## Cleanup

The205219835-byte failed gate clone was checked for open files, removed and
verified absent; the successful gate clone was removed by standard deploy.
The latest workflow backup (514969600bytes) passed full integrity, completion ID,
exact476-object schema verification and no-open-file checks. After API/UI
readback it was deleted under the backup-directory lock, guarded by its exact
size/mtime so a replacement generation could not be deleted accidentally.
Production DB and native trajectories remain untouched. Final task root
`/private/tmp/ceo-history-closeout.iqw5Li` was checked for open files, deleted
without following symlinks and verified absent. The peer was notified that no
release process remains; its subsequent backups are excluded from cleanup.
Concise dated acceptance is recorded in
[structured evidence](validation/2026-10-27-history-artifact-closeout.json).
