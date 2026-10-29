# C1: Task lifecycle ownership

> Status reconciliation, 2026-10-21: this is a scoped historical implementation/acceptance record. Its publication-pending statements are preserved as history; the subsequent Store19.11／Email53 combined copy, inverse and production deployment are recorded in [final release evidence](database-gpt6-release-validation.md). See [current execution progress](business-model-execution-progress.md) for unit status; historical FAIL results are not rewritten.

This candidate changes Store `2026-10-19.7` / Email53 to Store
`2026-10-19.8` / Email53. Its source is reviewed B2b `211eabb87` plus
the accepted local baseline `1aed63ec3c9abb345b685749e70852958bf22776`.
It is an isolated implementation candidate awaiting parent publication gates. Local verification
below does not establish production publication or a real provider result.
The parent has separately confirmed B2b Store19.7/Email53 deployment and live
readback; that verified predecessor is distinct from this unpublished C1.

## Physical facts and source meanings

The exact machine-readable field map is
[`2026-10-19-task-lifecycle-field-map.json`](superpowers/specs/2026-10-19-task-lifecycle-field-map.json).
It records provenance for all nineteen writable columns and every lifecycle
column for each of the twenty kinds, including absent source facts.

`id`, `_kind`, `legacy_key`, `parent_id`, `current_input_id`, and
`claimed_input_id` keep their source ownership. `record_type` remains read-only.
The seventh original physical column, `payload_json`, contains only the
remaining domain values in the declared order. Each of the twelve lifecycle
columns contains one **exact encoded JSONB scalar**, without coercing its stored
bytes. The decoded aliases retain the original source column's declared
affinity, including TEXT/INTEGER comparison behavior. SQL NULL means an absent source fact;
an existing source SQL NULL is the JSONB null scalar. Source BLOB, REAL,
negative zero, escaped strings, and nonminimal JSONB cell headers survive.

The stored `status` is the original source status scalar. `lifecycle_status`
derives the common meaning; `meeting_phase` reads the original meeting status.
There is no second stored normalized status or payload copy of any moved field.
All other `lifecycle_*` aliases decode their single physical owner. Their
affinities are derived from the source Task and State declarations and agree
for every source of the same common field. Existing
public source-kind aliases retain their original declared affinities and
constraints.

In the following table, `base` means exact source `status`, `error`,
`created_at`, `updated_at`. Additional source names map to the identically
named physical column unless an arrow is shown.

| Executable source kind | Domain-owned lifecycle sources | Common status mapping |
| --- | --- | --- |
| `reply_tasks` | base, `available_at`, `attempts`, `locked_at` | Source statuses unchanged |
| `work_summary_inputs` | base, `available_at`, `attempts` | Source statuses unchanged |
| `meeting_alignment_jobs` | base, `available_at`, `attempts`, `locked_at` | waiting/pending/retry→pending; processing/ready_to_send→processing; sent→done; no_action/skipped→skipped; failed/needs_human/quarantined unchanged |
| `okr_review_requests` | base | Source statuses unchanged; no attempts, due or lock fact |
| `weekly_okr_analysis_jobs` | base, `lease_owner`, `lease_expires_at` | running→processing; completed→done; failed unchanged |
| `workbench_turns` | `status`, `created_at`, `updated_at`, `lease_owner`, `lease_expires_at` | queued→pending; running→processing; completed→done; failed/stopped unchanged |
| `email_agent_classification_tasks` | base, `generation`, `lease_expires_at`, `available_at`, `owner`→`lease_owner`, `attempt_count`→`attempts` | running→processing; other source statuses unchanged |
| `feedback_processing_items` | `status`, `created_at`, `updated_at` | resolved→done; pending/processing unchanged |
| `wechat_memory_import_jobs` | base | running→processing; completed→done; failed unchanged |

For reply, summary, meeting and OKR requests, `lease_owner`, `owner_pid`,
`generation`, `lease_expires_at`, and `adapter_name` come from the exact
eligible `dispatcher_claim_leases` row. Eligibility is source kind, public
`legacy_key=source_id`, and adapter: reply channel dingtalk/wechat→`reply`,
scheduled→`scheduled_execution`; summary→`work_summary`; meeting→`meeting`;
OKR→`okr_review`. Other reply channels have no eligible adapter. No matching row
means SQL NULL. Cleared `owner=''`, retained generation and metadata are real
facts. Wrong-adapter rows and ineligible history remain in State unchanged.
The source unique adapter/source key rules out two rows for that exact claim.
`locked_at` is a job/delivery lock and is never an alias for lease expiry.

Workbench `error_code`/`error_detail` and `stop_requested` stay domain fields;
generic error remains NULL. Weekly jobs retain their unique week/manager/digest,
cache hit, reopen and owner-fenced finish behavior. Classification preserves
text task IDs and email identity. Feedback becomes generic done only through
the existing resolution transaction and its round/attempt/evidence facts.
WeChat import remains synchronous. These kinds gain no dispatcher adapters.

The eleven other kinds keep exact payloads and NULL common lifecycle:
`reply_task_inputs`, `workbench_tasks`, `business_object_tasks`,
`email_historical_candidates`, `feedback_iteration_decisions`,
`feedback_processing_batches`, `feedback_processing_rounds`,
`oa_notification_events`, `okr_review_items`, `workbench_artifacts`,
`workbench_attachments`. Their journal/domain statuses and receipt/adoption
links retain their source meanings.

## One writer and passive claim history

All existing Task8/Task12 insert/set builders delegate to
`task_lifecycle_storage`. Existing caller transactions, WHERE/RETURNING,
natural keys, result mutations, compaction and child-run binding remain in place.
The current dispatcher adapters share `_TaskQueueAdapter`; reply and scheduled
execution retain distinct adapter names and channel filters.

The Task claim operation writes source status/attempt/input transition and
claim owner/PID/generation/expiry/adapter in one Task UPDATE with source-domain
and current-owner predicates. Release decrements reply/summary/meeting attempts
with the original floor zero. OKR envelope attempt uses generation because no
source attempts field exists. Claim/renew/assert/recovery decisions read Task
owner facts. Due/metric queries seek kind/status indexes and preserve source
phase and live-PID protection.

State retains the exact ledger public ID, adapter/source key, owner, PID,
generation, expiry, terminal/error and timestamps. Its updates follow the Task
CAS in the same transaction. Native point updates repack in the original State
field order; original bulk updates retain the JSON key order. The ledger does
not decide eligibility or ownership. Its error timestamp/ROWID can still rank
source error history. A terminal Task with a held ledger is a valid
intermediate state; terminal_at does not replace Task status.

Task arbitration applies to the eligible migrated dispatcher claims.
`EmailStore._unsubscribe_cancellation_decision` retains its existing State
history read for failed reply tasks on channel `email`. That channel is
ineligible for both `reply` and `scheduled_execution`; the history is an
existing cancellation prerequisite, not a migrated Task lease. The change does
not synthesize a Task claim for it or infer cancellation from owner/expiry.
The original cancellation, unknown-send and no-replay predicates remain.
For eligible claims, `reply_task_deferral._snapshot`, quiet counts and current
claim readers use Task, including when passive history is absent or stale.

The existing retention operation still prunes terminal ledger history at its
original boundary. In that transaction, only the matching inactive Task
claim's five metadata fields are cleared, with exact adapter/source/generation
matching. Newer, other-adapter and live claims survive. An absent pruned ledger
is never reconstructed; the next claim follows the source generation reset.
Unrelated Action-outbox and scheduled-Run leases remain with their source writer.

## Byte fidelity and release

SQLite's `jsonb_set` can leave a nonminimal string header after shortening a
value. For example a source `processing`→`failed` status retains a five-byte
header; `jsonb(payload -> path)` would silently replace it with a one-byte header.
`jsonb_remove` also shrinks an array root header. Semantic JSON equality is
therefore insufficient for this inverse.

The bounded `task_lifecycle_jsonb` helper splits/joins existing SQLite JSONB
cells. It does not parse business JSON, interpret values or implement updates.
The existing domain array's root framing retains its width while mapped cells
move out. On a source-field update, the writer transiently reconstructs the
full current source array, invokes the frozen layout's native `jsonb_set` once,
and splits the result back to its physical owners in that same SQL statement.
No reconstructed array, original row, duplicate encoding, or encoding metadata
is persisted. SQLite continues to validate all values and execute constraints.
The format is documented by [SQLite JSONB](https://www.sqlite.org/jsonb.html).

The codec is 91 lines and handles the existing array framing and cell lengths
only (one-, two-, three-, five- and nine-byte headers). A selected executable
row has 7–23 original cells and 3–9 moved source cells. Restore/domain traverse
that row; each moved-cell extraction traverses its prefix. Allocation/copying
therefore grows with the selected payload bytes, and the bounded header walk
is at most nine prefixes of a 23-cell source array. No Task-history lookup or
general business serializer is introduced. Lease-only changes do not rewrite
the domain payload. A source-field update must preserve native root framing:
an actual OKR error `""`→70,000 characters→`""` changed the source root width
2→5→2 and its domain carrier width too, even though the other domain values
were equal. Omitting that domain write would lose the exact inverse.

The established paired coordinator validates exact source/target declarations
before changing the database. Conversion stages exist only in its transaction.
The inverse rebuilds Task source arrays from current owners and domain cells;
the retained State ledger is already the current source metadata. It preserves
physical/public IDs, every allocator row including ROWID, FK findings, source
Reply FK variant, Email53 ledger, unrelated objects and the original Store
marker clock. New source facts created after forward conversion are included.
Normal constructors select only the current declaration; a predecessor or
wrong pair requires explicit paired publication.

Frozen historical factories and B2a/B2b declarations are listed separately in
[`2026-10-19-task-lifecycle-frozen-sources.json`](superpowers/specs/2026-10-19-task-lifecycle-frozen-sources.json).
All fourteen files were compared with both their manifest and actual baseline
Git blobs and remain byte-exact.

The final implementation freeze is
[`2026-10-19-task-lifecycle-source-freeze.json`](superpowers/specs/2026-10-19-task-lifecycle-source-freeze.json):
1,231 `app/`, `tests/` and `scripts/` files, SHA256
`c959c4389efd9047eb7d8c6eae535e662cc3d20420e5dd20497f5b34fd9c9798`.
Documentation is outside that aggregate to avoid self-referential hashes.
The application-only aggregate is
`e37955c43046981ceece0599fcf77b27ed25127e99f0a07fe19699ce14eafaab`
(438 files), unchanged throughout all final batches.

Task declaration hashes use compact UTF-8 JSON with sorted object keys:

| Declaration | Objects | SHA256 |
| --- | ---: | --- |
| Actual Task20 source | 83 | `ffaa908ffac0b48c57336d379377d630986897e730851f0aed5bf478306601c0` |
| Current C1 Task | 87 | `d2e1b320fe45cdd77a4b67ffa4a46a2571757c678a86682965734c5e9d2274d9` |

## Verification boundary

Provider calls and Agent execution are substituted in local tests. The archived
oracle runs actual source code at the baseline commit, compares public fields
and physical SQLite cells after successive transitions for all nine kinds,
then opens the inverse with old Store, Email and Workbench and republishes.
Additional cases cover exact scalar/root headers, unchanged nonexecutable
payloads, wrong pair/unknown DDL refusal before writes, failed post-publication
load after new facts, passive-ledger pruning/current-data inverse, PID and owner
fences, and a concurrent reader observing no partial Task/history transaction.

Exact commands, source bindings, prior failures, diagnostic observations and
results are in
[`2026-10-19-task-lifecycle-validation.json`](superpowers/specs/2026-10-19-task-lifecycle-validation.json).
The following are serial scoped batches, not a repository-wide run:

| Batch | Original result | Bound source | Disposition |
| --- | --- | --- | --- |
| Lifecycle storage/release | 34 passed, 1 failed; 180.43 s | `0685d278…` | All functional cases passed. Cost assertion incorrectly assumed the whole inherited FK path was constant; diagnostic correction below. |
| Corrected cost | 1 passed; 32.41 s | `77a205d4…` | Actual old/current selection and FK-growth comparison. |
| Dispatcher/retention/deferral/quiet | 164 passed, 1 failed; 379.50 s | `77a205d4…` | The original two-second Action-outbox deadline remains failed; details below. |
| Existing Task8/Task12 writer/allocator tests | 758 passed; 425.95 s | `77a205d4…` | Python storage tests; no native Agent CLI execution. |
| Existing nine-kind domain callers | 94 passed, 4 failed; 565.91 s | `77a205d4…` | Three stale Workbench projection fixtures and one old schema-error expectation; original results retained. |
| Exact affected fixture checks | 7 passed; 15.31 s | `c959c438…` | Six Workbench idle/recovery/competition cases and the malformed classifier-index case. |

Between `0685d278…` and `77a205d4…`, only the two new cost-test/support files
changed; the 34 passing core nodes and application code stayed byte-exact.
An intermediate cost-only run at `20996d82…` failed because the assertion also
required the old Summary selector to avoid its existing partial-index scan.
The final assertion constrains the new selector and compares old/current
growth. Between `77a205d4…` and `c959c438…`, only the Workbench test import and
Email test error expectation changed. Each final batch verified its source
aggregate before and after execution; unaffected passing batches were not
repeated.
Ruff passed for all 39 changed Python files; `git diff --check` passed.

The Workbench tests now import the actual current projection from
`app.workbench.store`; their expired-owner and competing-winner assertions are
unchanged. The malformed Email fixture changes only `idx_task20_native_1` from
its classifier-specific index to `on tasks(id)`. Comparison of all 87 Task
declarations found exactly that difference. The current Task declaration guard
raises `malformed current Task lifecycle declaration` and leaves database bytes
unchanged (before/after SHA256
`f01cc12c9cedd966493804c148db3d977645db4b2381adc34e9745f641d45931`).
A separate owned control restoring only the correct index passes the same
Email constructor. The original malformed-index and unchanged-byte assertions
remain in the test.

The retained failure is
`test_task_todo_outbox_enqueue_wake_is_not_lost_after_empty_scan`, whose
`processed.wait(timeout=2)` also failed in an unchanged single-node rerun.
A temporary observation hook then ran the original assertion against actual
`1aed63ec3` and the frozen current application: old failed in 10.25 s and current
failed in 6.58 s. Both observed the wake and eventually completed exactly one
outbox attempt; stacks at the deadline were in unchanged Action UPDATE work.
The test function, dispatcher, Action-outbox adapter and involved Store methods
have identical ASTs in old/current. These observations establish an existing
deadline failure on this host, not a passing test or a complete performance
root cause. Neither the timeout nor the production/test assertion was relaxed.
The test skips its thread shutdown after asserting; only its owned runner was
interrupted after the failure summary, and the diagnostic hook cleans its own
threads in `finally`.

## Local cost and atomicity

The bounded cost case uses actual archived/source and current callers with the
same SQLite 3.53.4 library, Reply, 64 KiB WorkSummary and Email fixtures, then
3,000 unrelated journal Tasks. Every measured claim issues exactly one Task
UPDATE; owner/source/history changes retain the caller's single transaction.
A separate concurrent-reader case checks the invisible intermediate state and
owner fencing. VM counts are sampled in units of 100:

| Claim | Old VM: small / +3,000 | Current VM: small / +3,000 | Current minus old |
| --- | ---: | ---: | ---: |
| Reply | 34,400 / 43,400 | 35,800 / 44,800 | +1,400 at both sizes |
| Summary, 64 KiB | 33,900 / 42,900 | 35,400 / 44,400 | +1,500 at both sizes |
| Email | 24,800 / 33,800 | 25,400 / 34,400 | +600 at both sizes |

New Task selection uses indexed SEARCH, including exact typed/numeric identity.
The complete source/current UPDATE plans both include inherited
generated-parent FK checks; adding 3,000 unrelated rows adds 9,000 VM steps to
both versions. FK-off EXPLAIN was used only on owned fixtures to identify that
plan component; runtime and migration FK policy/indexes were not changed.
The old Summary selector additionally uses an existing partial-index SCAN.
Consequently the result establishes no added Task-selection/history scan and
fixed extra work at these two sizes, **not** a scan-free complete write path.

Median wall times across three samples (old/current milliseconds) were
Reply 426.646/256.244, Summary 188.294/143.707 and Email 97.240/74.091;
with 3,000 unrelated rows they were 159.864/130.762, 154.612/106.711 and
70.968/54.326. These noisy local times are recorded without a speedup claim.

Meeting domain tests exposed 18–29 s send-claim calls, so a separate actual
old/current comparison used the same one-job source fixture, copied via SQLite
backup before target publication, FK enabled, and `limit=1`. Both returned the
same ready-to-send job, attempts=0, lock and delivery claim. Old/current calls
took 17.003819/10.072518 s and 2,067,800/2,068,300 VM steps. The identical
preflight was 384 bytes; Task UPDATE SQL was 2,331/2,957 bytes. Both reached the
Action declaration read within 0.20 s; the large subsequent interval preceded
the existing Action INSERT trace (13.979/9.349 s after call start), followed by
connection completion. The send-claim method itself has identical ASTs. This
single comparison did not show a new C1 regression; it does not isolate every
SQLite preparation/commit cost or establish throughput.

The local batches above do not establish full-copy migration, production
startup or native Consumer/Audit acceptance; parent copy evidence follows below. The parent separately observed slow pre-C1 schema parsing
during upstream startup; local per-row results do not settle that capacity
question. Fresh SPEC/QUALITY review, upstream integration, full-copy/native
acceptance and release remain parent-owned.
Disposable databases, archived source trees and probe logs are task-owned
temporary inputs. Their concise results, commands and hashes are recorded in
the validation manifest. At the parent's request, the raw inputs remain under
`/private/tmp/ceo-c1-task-lifecycle.KEEegd` through fresh SPEC/QUALITY review and
any necessary evidence checks. They are cleaned only after that review window;
this implementation handoff is not the end of that window.

Run-history common projection/API retirement (C2), Reply/Postfix ownership (A5),
and legacy workflow retirement (D) remain separate scopes.


## Independent local review

Fresh SPEC and then QUALITY both APPROVE the exactc959c438 source freeze.
Both independently checked source/evidence; neither reran tests or accessed
production. QUALITY additionally used read-only query_only EXPLAIN on the
preserved3006Task cost fixtures with SQLite3.53.4/FK enabled: lease-only renewal
uses indexed point SEARCH in both source/current, with no current Task/Action
scan. No UPDATE was executed for that check. The baseline deadline failure
remains a failed node. Upstream integration, fresh full-copy/native/current-data
inverse and production publication remain separate parent gates.


## Parent full-copy and native acceptance

On merged source `c4a9e794d0fe7b8954a8746b01fec118337dc188`, the parent
rehashes 1,233 code/test/script files to `eb3d16cf…`; all four extra runtime
pins are recorded in the merged freeze. The only accepted C1 application
delta from the local reviewed source is the upstream OA manual-rerun method.
Eight affected integration checks passed in 158.41 seconds. These observations
are separate from the original local batch source bindings above.

The fresh read-only production backup is Store19.7/Email53 at actual predecessor
`63504af9…`, 479,956,992 bytes, SHA256 `493741fc…`. Official paired publication
took 158.245 seconds. All twenty public Task declarations compare exactly, with sixteen populated
kinds and four empty kinds in this snapshot. Values and SQLite types in every
populated kind are exact; every source-shaped stored cell, unrelated table, ROWID and sequence
watermark is unchanged. All five claim facts match their exact source-qualified
ledger for 17,613 source-kind rows checked: 16,497 adapter-eligible rows and
1,116 ineligible email Reply rows. There are 153 matching eligible ledger
records; the five Task claim fields are NULL for ineligible email rows.
The existing `runs/190042/tasks/1` FK finding is preserved, not hidden.

An actual Codex Consumer and Audit read only the synthetic artifact, action
contract and shared AGENTS instructions. Both native session/ranges were read
back from native storage. The reviewed candidate reaches `needs_human` with no
provider call. An actual browser click selects `execute`; the fake provider
records exactly one send and one verification. The Task ends done with one
verified Action and one receipt; sent-result and attempt-result readers resolve
the same authority without storing duplicate JSON. No extra model turn occurs.
All original records in the eleven business tables remain unchanged. The exact
synthetic conversation is absent from production in a read-only check.

The inverse uses current data including the new Task/selection/Run/Action/result,
not the source backup. Every old declaration is exact and current source-shaped
facts/types/ROWIDs survive. Actual archived predecessor Store, strict EmailStore
and WorkbenchStore cold-open and read the new completed Task, chosen branch,
execution and receipt. This proves constructor/read compatibility, not every
old-worker recovery branch. Native range reads are independently proved by the
current prepare trace; the old snippet checks only run-record count. Reforward
retains all current facts and passes full integrity and original-FK comparison.

This native case uses a direct Store claim; automatic common-adapter/nine-kind
behavior has its separate actual DAO tests. It explicitly permits the existing
postfix and does not fix the earlier exact-body contract failure. No real send,
production test Task or C1 production publication is claimed.

At identical population, Task plus indexes grows from 52,846,592 to 55,345,152
bytes (+2,498,560); payload grows by 2,077,713 bytes. The database file remains
479,956,992 bytes (4,096-byte pages, 117,177 pages). Freelist falls from
976 to 373 pages; this consumption of existing free pages differs from the
Task/index allocation delta. Source has no WAL/SHM; pristine target WAL is
zero bytes and SHM is 32,768 bytes. No VACUUM was performed. C1 unifies lifecycle ownership and
writers; it is not a storage-reduction result.

Exact evidence, raw-artifact hashes and scope limits are in
[`2026-10-19-task-lifecycle-parent-acceptance.json`](superpowers/specs/2026-10-19-task-lifecycle-parent-acceptance.json).
The synthetic completed-page screenshot is retained in
[`2026-10-19-task-lifecycle-native-receipt.png`](validation/2026-10-19-task-lifecycle-native-receipt.png).
Fresh independent parent SPEC then QUALITY both APPROVE the c4a9/eb3d16
source and this evidence boundary. Standard publication remains pending.


## Final upstream integration before publication

Normal merge `2f76a101…` includes upstream `120291fd…`. All 39 C1 paths and
every application file except the independently changed runtime tool-name
renderer remain byte-exact to the accepted c4 source. The final 1,233-file
aggregate is `ae4d28da…`, with six extra runtime pins. The c4 native evidence
keeps its original binding; this does not claim another native run for the new
tool catalog.

The four focused runtime-context, preview, publication and finance-rule files
report **74 passed, 2 failed in 343.04 seconds, exit 1**. Both failures are old OA
exact-wording assertions at lines 128/161. The exact upstream module reproduces
the same two failures in 0.09 seconds. Their functions are AST-identical across
c4/upstream/current; the repository OA Skill in all three versions and actual
installed Skill are byte-identical (`66dfb8dd…`). No assertion, policy or Skill
was changed to make this release pass. Independent incremental QUALITY APPROVE
accepts this as no new C1 failure, not an all-green run or a gate exemption.

The newly verified latest predecessor backup is 479,956,992 bytes,
SHA256 `cc1fbba7…`, full integrity/FK and exact paired preflight passed before
atomic replacement. Production PID7444 and four degraded observations are
recorded before C1, not attributed to it or suppressed. Standard quiet wait,
schema/import/health/loaded-state checks and rollback remain unchanged.
Detailed pins, failed-test baseline and review are in
[`2026-10-19-task-lifecycle-final-integration.json`](superpowers/specs/2026-10-19-task-lifecycle-final-integration.json).
