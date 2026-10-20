# B2a shared reviewed and direct Email actions: local evidence

This bounded implementation is on `codex/business-model-shared-actions-20261019`,
based on `17c29efdab08f339acb5f9e34911aa76ede1764d` (reviewed A4 source plus the
parent's implementation claim). The exact transition is Store `2026-10-19.5` /
Email53 to Store `2026-10-19.6` / Email53. These are local synthetic-database and
fake-provider results. Independent SPEC then QUALITY review, the parent-owned
production copy, native CLI case, integration and live acceptance remain separate.
No production database, native Agent CLI or real provider was used by this implementer.

## Actual owners and writers

Five existing Actions origins retain their physical IDs and public identities,
and use appended physical discriminator codes. The remaining Actions families,
including specialized Reply and unsubscribe facts, retain their original kinds.

| Original source | Typed physical variant | Code | Public read |
|---|---|---:|---|
| `review_candidates` | `plan_version_reviewed` | 63 | `REVIEW_CANDIDATE_SQL`; A3 block through `CANDIDATE_EXECUTION_SQL` |
| `email_action_plans` | `plan_version_email` | 64 | `email_projection(kind, include_rowid=True)` |
| `email_actions` | `effect_step_email` | 65 | `email_projection(kind, include_rowid=True)` |
| `candidate_action_attempts` | `action_attempt_reviewed` | 66 | `CANDIDATE_ACTION_ATTEMPT_SQL` |
| `email_action_attempts` | `action_attempt_email` | 67 | `email_projection(kind)` |

The generated `action_family` and `action_origin` only encode existing source
identity. The generated `record_type` is the original read-only name used by
existing cross-table guards, indexes and public queries. Physical source codes
are excluded from the target for these five origins. Frozen source code numbers,
layouts, defaults and declaration factories remain available for exact inverse.
No public field is removed. The old Email public projection is
`app.action_email_definitions.email_action_projection`; the current projection is
`app.shared_action_definitions.email_projection`. Reviewed public SQL remains the
same. All five mappings preserve origin-scoped identity and allocation; no global
public-ID formula is introduced. A fixture explicitly has reviewed and email plan
legacy key `1` simultaneously, with different physical Actions IDs.

`shared_action_lifecycle` owns the actual allocation, insert/update, plan-version
persistence, effect-step reads/creation, and claim/outcome/schedule transitions for
both origins. It writes the typed target directly; it does not invoke two old
state-machine implementations. Email plan version/conflict handling and its
attempt append/step transition moved into this owner. The reviewed mutable-slot
transition also moved into it. Current reviewed and direct Email callers,
including stale recovery, provider reconciliation and delivery repair, call that
writer. `_update_direct_action` and the independent direct Email mutation branches
are removed. An AST audit found no application calls to the old insert/update
APIs for any of these five source families. Generic frozen-source and unrelated
family helpers are retained for explicit historical factories and B2b families.

Reviewed effect steps are read from the exact adopted immutable candidate or
selected option branch. Their action index, identity, operation, target, payload
and effect are not copied into a second row. Each `(execution_id, action_index)`
still owns one mutable attempt slot; redispatch retains its ID and original
created time. A3 plan-owned lease, execution alias, independent reviews, Audit
run, selection and adopted FKs remain intact. A2 verified/NULL result ownership
and A4 Sent receipt ownership remain intact.

The direct step no longer stores `attempt_count`, `finished_at`,
`provider_operation`, `provider_target`, `provider_result_id` or `error`.
It keeps current status, next-attempt time and the in-flight start time. Terminal
start time is empty in the step and derives from the latest ordered attempt.
The public count is the actual ledger count. During a new claim the projection
returns empty terminal fields and the current start time, even if an older
terminal attempt exists. Email attempts remain append-only numbered source
facts with their original integer public IDs and AUTOINCREMENT water. The six
fake-provider operations create no AgentRun or verified external-result row;
their provider operation/target/result ID stay on their real attempt.

## Exact source, refusal and current-data inverse

Prepare accepts only the exact 19.5/53 pair. Before any source write, it checks
all direct attempts for contiguous integer numbering and exact count, then checks
the status and every removed mirror using exact SQLite value and type. Pending
rows must have no attempt history or execution fields. Processing rows must have
a nonempty start and empty terminal fields. Terminal rows must match their latest
attempt. Mismatches are refused, without repairing or erasing source facts.

A specific source-valid exception is tested: the old cold validator permits a
processing row with retained provider operation/target but cleared result/error/
finish. Those fields cannot be represented as merely the current start or a prior
terminal outcome. B2a prepare refuses that row before writes. Separate tests cover
mirror mismatch, ordering gaps, count mismatch and impossible current-claim
terminal data. File bytes remain unchanged after each refusal.

Forward and inverse rebuild Actions in the caller-owned FK-off transaction.
Only five physical discriminator codes and the direct step layout change.
Other payload bytes and structural columns are copied unchanged. The temporary
Actions staging table receives a physical-ID index immediately after CTAS, so
each direct step transformation seeks its existing ID in both directions. The
index is temporary and drops with the staging table; permanent declarations do
not change. Temporary rows and water staging are dropped before commit. Both EXCEPT directions compare
stored values plus `typeof`, and the direct public tuple compares every value and
Python SQLite scalar type. Full declaration equality and the FK finding multiset
are verified. All `sqlite_sequence` row IDs, names and values are restored.

Inverse uses current target owners, including a new reviewed plan with real test
Consumer/Audit rows, human selection, new execution alias and reused attempt slot;
new direct plans; new attempts after a source water of 700; retries; and current
in-flight claims. The release retains only paths, original marker tuples, source
Reply variant and FK findings. It retains no business-row archive. Original Store
marker time (including a BLOB clock fixture) and the unchanged Email53 ledger are
restored. An injected cold-load failure was rolled back, then rollback was repeated
idempotently. Current publication selectors were checked on both source and target.

The actual source commit was extracted using `git archive` for app/tests/scripts.
Its real `AutoReplyStore` and `EmailStore` cold-opened each inverse; the main current-
fact case then re-forwarded and cold-opened under 19.6/53. Five complete DAO scenarios
were run against that old executable and current code: direct claim/retry/stale
recovery/reconciliation, superseded plan and copy adoption, explicit source-verified
retry, sequence conflicts/high-water allocation, and feedback/plan CAS. Public
results matched. Their full projected facts, physical/public IDs, waters and FK
findings matched too, including after inverse.

| Actions variant | Frozen source declaration SHA-256 | Target declaration SHA-256 |
|---|---|---|
| actual_no_memory_fk | `25790ebc80564660e643ed3ac6ea5d157912fe1e5f8cfb169423c797df619373` | `d7f73a0780b35b5e8ce816f6701d6d9000214d8c3821d33c720490af98b5e498` |
| fresh_memory_fk | `c9da6469aa1eccc80d6f2e74feb642c2b9470b02e5439f25651465dc6b10f6e7` | `986267b56a49e75c52a53e44bbc0ef64df38b9863d66b8d8f9b2df94416ff1c7` |

Source declaration hashes use sorted-key compact JSON of the exact SQLite objects.
The archived 1,208 source files have aggregate SHA-256
`07995baffd63214a516366631228479e0b3fb67cdc66940bff8b289126333720`,
using sorted path UTF-8, NUL, raw file SHA-256, NUL. The exact final current code
file list and digest are in
[the B2a freeze](superpowers/specs/2026-10-19-shared-actions-freeze.json).

## Focused validation

All tests used `/Users/derek/miniforge3/bin/python`, SQLite 3.53.4, and one fixed
owned temporary root. The whole repository suite was not run. There are 26 focused
B2a tests across the three new files, 57 existing reviewed/reconcile tests, and
three existing Sent/repair cases; all 86 distinct cases passed in focused runs.

- The initial common-owner/claim tests failed before implementation and passed
  after the target writer was connected. The source factories remained frozen.
- Both source declaration variants and current reviewed execution/attempt/A2/A4
  inverse checks: 5 passed in 286.55 seconds.
- Initial archived-source release/refusal/constructor checks: 5 passed in 76.90
  seconds. The expanded new-reviewed-plan/current-claim inverse and actual old
  cold-open/reforward case passed in 53.29 seconds.
- The existing reviewed-candidate and Email reconciliation files: 57 passed in
  574.37 seconds, covering supplements/revisions, approval, no-replay outcomes and
  reconciliation behavior without introducing a new policy.
- Six direct operations, existing provider no-ops/missing-message outcomes, one
  claim winner and existing account/current-plan cross-table guards, plus the five
  complete archived-source DAO scenarios: 7 passed in 191.91 seconds.
- An after-attempt-insert trigger race first failed because completion accepted a
  changed claim. The shared owner now rechecks the current claim after append;
  the regression passed in 11.74 seconds and proves the entire attempt rolls back.
- Current publication-reader RED exposed old 19.5 selectors. Existing selectors
  were moved to 19.6 and the focused regression passed in 4.21 seconds. The source/
  target reader and cold-load failure/BLOB-clock inverse check passed in 31.89 seconds.
- Step polling uses the existing typed indexes. With 3,000 unrelated Actions,
  SQLite VM steps changed only from 7,018 to 7,019. Scope-ID overlap also passed.
- Additional count and current-claim operation/target source refusals: 2 passed in
  41.11 seconds, including actual old cold-open of the permitted source exception.
- History, Attention, important-mail listing, A2 shared correction, and current
  native-readback repair passed. The legacy receipt fixture initially attempted
  an A4 inverse directly from the new B2a schema; it now inverses B2a before its
  existing A4/A3 fixture edits and re-forwards afterward. That last existing
  no-Audit readback case passed in 33.34 seconds, retaining one fake send.

The initial cold-failure test also needed an explicit caller transaction for its
State clock fixture; this was a test-harness repair. No application State rule
changed. The frozen-source and parent-owned live evidence boundaries remain clear.
All 33 changed Python files passed Ruff, and `git diff --check` passed. The fixed
task-owned temporary root, including databases and archived source, was removed
after all processes finished; no open handles and subsequent absence were checked.
No physical database-size saving is claimed by these local tests.

## Parent acceptance interface and remaining scope

The existing release coordinator supplies the small-copy interface:

```python
from pathlib import Path
from app.store_schema_release import prepare_database_schema_release

release = prepare_database_schema_release(Path(ROOT), Path(COPY))  # exact 19.5/53
release.publish()  # 19.6/53; current Store/Email cold open
# Parent runs its authorized current native/direct case against COPY.
release.verify_loaded()
release.rollback()  # reconstruct CURRENT facts; restore original marker clock
# Parent cold-opens COPY with actual archived 19.5 Store/Email code.
release.publish()
release.finalize()
```

The lower-level transaction API is `migrate_shared_actions(db)` returning only
`{'reply_variant': ...}`, and `restore_shared_actions(db, metadata)` under the
existing caller-owned FK-off transaction; the caller restores its original Store
marker through the existing State writer. No new deploy or compatibility entry
point is added.

B2b is not complete: reply/unsubscribe claims, receipts, outgoing IDs, effect
proofs, versions, controls, cancellation, retry and feedback adoption retain their
specialized real fields and current paths. Postfix accepted-but-unverified receipts
were not moved into the verified external ledger. Parent review, real-copy/native
acceptance, integration and live deployment are not claimed by this report.

## Parent source freeze and integration

Parent independently verified 1,215 paths / `fccf517e` and committed the exact
handoff at `93058af1f`. Independent SPEC approved that frozen source. Parent
normally merged origin `1319e2557` at `8172fbc75`, retaining all claim rows in the
append conflict. The 11 app/tests/scripts changes are independently developed
image/OCR/WeChat/purpose-receipt fixes; shared lifecycle, schema, paired release
and both migrated DAO implementations were unchanged. The merged manifest records
1,216 paths / `201756cb4812572741a79a0e03f9670ca7ec9af87ab65381656b7a39a7584170`.
Parent reported independent integration SPEC and QUALITY approval of that source.

Root's read-only eligibility probe passed all 10,984 direct steps on the accepted
staged 19.5 copy, which was checkpointed to DELETE journal with all physical facts
unchanged and made read-only: input SHA-256
`9a1c692c715e2554b1b124b988ee350a8542ae2718e3f595918cba0fd9be1c4f`,
476,516,352 bytes. This is not a fresh raw production snapshot.

## Temporary staging lookup correction

Parent's full-copy attempt spent approximately five minutes of CPU in the
pre-native migration. Its interrupted stack identified the per-step
`UPDATE temp._shared_action_rows ... WHERE id=?`. CTAS had discarded the source
primary-key index, so each of 10,984 direct steps scanned the whole Actions staging
table. Parent independently reproduced SCAN versus SEARCH with SQLite EXPLAIN,
then interrupted only its owned process before native execution. Parent reported
exit 130, complete source-fact/declaration/FK rollback equality, unchanged immutable
input SHA-256, and removal of the aborted owned copy after checking open handles.

The permanent correction adds one nonunique TEMP index on staging `id` immediately
after CTAS. It changes no persistent schema, business field, transition, source
eligibility condition or inverse fact. The regression observes EXPLAIN QUERY PLAN
for the actual per-row UPDATE while staging exists, in forward and inverse, and
asserts that no staging table or index remains after either rebuild. Before the
fix it failed in 66.20 seconds with two `SCAN temp._shared_action_rows` plans.
After the fix both use
`SEARCH temp._shared_action_rows USING INDEX _shared_action_rows_id (id=?)`.

The bounded follow-up run passed all seven selected cases in 291.06 seconds:
the new lookup/cleanup regression, frozen declarations, both declaration-variant
round trips, both current reviewed execution/A2/A4 inverse variants, and one
archived-source release/current-fact inverse/old cold-open/reforward case. Ruff
passed for the two changed Python files. Source and target declaration hashes
remain exactly those recorded above. The previously adequate 86-case set was
not rerun. The follow-up's fixed owned temporary root was removed after all test
processes exited and `lsof` confirmed no open handles; its absence was verified.

[The staging-index freeze](superpowers/specs/2026-10-19-shared-actions-stage-index-freeze.json)
records the corrected 1,216 source paths and aggregate SHA-256
`83ab27e3e28844bb07adf7bbc2ac572c2ca7d3ca641e2fc18b3dc28a67270317`.
It supersedes the merged source for subsequent copy acceptance; the original and
merged manifests remain bound to their original reviewed commits. Independent
SPEC/QUALITY of this correction and the fresh parent full-copy/native/current-data
inverse/live checks remain parent-owned. No full-copy timing improvement or live
outcome is claimed by these focused tests.

## Fresh parent full-copy, native and current-fact inverse acceptance

Independent correction SPEC and QUALITY approved `6ad34bcd2`. A fresh production
snapshot was then used for the complete acceptance. It contains479186944bytes,
Store19.5/Email53, full integrity `ok`, and the original single Runs190042 Tasks
FK finding. Actual archived `cee038b13` Store **and default strict EmailStore**
cold-opened the source with every stored fact and declaration unchanged. After
checkpoint/DELETE it was frozen read-only at SHA-256
`be50ca02d0e8cf94d9e879d59fa2ab7dfb7487ba7173334601112b2cde490d97`.

Publication/load took222.164seconds under concurrent local test load. All source
public values and SQLite types, IDs, non-step payload bytes, unrelated physical
facts, allocators, and FK multiset matched after forward migration. The identical
population contains1040reviewed plans,331execution owners,321reviewed attempts,
10602email plans,11026email steps and13090email attempts. Actions and its indexes
use4026368fewer bytes, and total Actions payload uses2503145fewer bytes. These are
logical dbstat/population measures, not a VACUUM or file-size shrink claim.

Real native Consumer386803/runtime386804 and Audit386805/runtime386806 both
completed and actually read `case-input.json` plus the send contract. Source
ranges/session IDs are in the receipt; only allowed agent_cli tools were called.
The Consumer's single command read shared AGENTS with sed; Audit ran no command.
The actual copy UI saved execute for Task5131569106897304/Attempt15717; then the
actual worker/SystemExecutor completed one InMemoryProvider send, one verify,
one verified attempt and one receipt, with no additional native turn. Public Sent
result is exact while raw result is NULL/reference; reviewed attempt raw result
is likewise NULL/reference. The UI showed done,1/1verified, and the expanded
`native-copy-provider-receipt`. Eleven pre-existing table populations remained
unchanged. Copy console shut down normally with zero provider calls.

Default strict EmailStore then wrote six new source-shaped direct plans/actions:
archive has a failed first attempt and a second current processing claim; five
other kinds completed via StatefulFakeImapProvider. Direct completion created
neither fake AgentRuns nor verified global receipts. Inverse reconstructed the
exact source schema, actual archived Store and strict EmailStore cold-read all
new native selection/execution/Sent/receipt facts and all direct outcomes plus
the current retry claim. Reforward preserved every current physical fact except
the deliberate Store marker clock, passed full integrity, retained the original
FK multiset and left the immutable source hash unchanged. No source-original or
real provider was accessed. See [structured copy evidence](validation/2026-10-19-shared-actions-copy.json).

The earlier staged source carried one partial B1 synthetic email90261019 whose
RFC/thread mismatch existed before and after B2a; production had no such mismatch.
That staged proof is not the strict EmailStore acceptance. This fresh source
supersedes it for complete parent acceptance. The source DTO harness's separate
strict-Python type assembly failure was corrected only in the disposable harness;
application validation was not weakened.

After acceptance, root normally merged origin9297581a9 at6753e9a2d. Its changes
are separately authored OA OCR/session/legacy-access-receipt behavior and tests;
no B2a core file changed. AST comparison confirms the non-OA capability text is
exact; the selected native general path remains the same. Final merged tests,
publication freeze/review and production readback are recorded separately.

Final merged focused controls passed14/14 in2.68seconds, covering the changed OA
session handoff, unchanged general-role command/instructions, exact canonical,
prior and legacy verified receipt matching, and source chronology instruction.
All B2a core source/test files match6ad34bcd2 byte-for-byte. The separate
[publication freeze](superpowers/specs/2026-10-19-shared-actions-publication-freeze.json)
binds this merged tree without overwriting earlier acceptance freezes. Fresh
publication SPEC/QUALITY and production readback remain pending.

Independent final publication SPEC and then QUALITY approvedf49106507. Both
independently recomputed the1217-file publication digest and found no concrete
merge/evidence blocker; neither reran tests, CLI or providers. Parent has also
verified a fresh predeployment production backup:479813632bytes, Store19.5/Email53,
full integrityok, original FKfinding, source migration preflight eligible, CEOB
stamp, SHA91c6664401edb953e0ab3f2df7a142e50249125f1e15e950f809c04a990c8492.
It replaced the previous daily workflow backup atomically only after all checks;
the active acceptance source is separate disposable working data. Standard
production deployment and live readback are the remaining B2a gates.

## Final main synchronization before push

The first fast-forward push was rejected because main advanced to816d05e9a with
an independently authored OA people-Skill rule and matching release hashes. Root
fetched and normally merged it at96b4ec2dc; the append-only claims conflict kept
both complete claim rows. The only app/tests/scripts delta is the corresponding
five-line chronology test update, which passed1/1 in0.15seconds. No app source,
B2a core, or native general-task instruction changed. The committed Skill's new
SHA888516678b5f4118acf18d972d1ad6227b22022924eb2c36f7cdc57d6f229fc6
matches its release manifest; old SHA matches the preceding committed Skill.
A separate refresh freeze preserves the prior f491 publication manifest, pins
the one changed test plus both additional CI runtime inputs, and requires bounded
SPEC then QUALITY refresh before retrying the standard push/deploy. No policy
change was authored as part of B2a, and no OA action was performed.


## Live publication readback

Standard deploy completed296b6d82→actualcbf95112, preserving the separately
authored OA manual-rerun context repair published during the wait. Its focused
four controls passed5.46s in the isolated B2a worktree after restart; the shared
Action declarations/lifecycle sources are unchanged from7e. Production now
uses exactStore19.6/Email53, newPID75178, all five new typed codes63–67 with
zero wrong-code rows, and no six retired latest-attempt mirror slots in direct
steps. Quick check isok and originalFK(runs190042→tasks) is unchanged.
Health/status/Attention/History return200 and queue failure counts do not rise.
Production file479956992bytes is not a file-shrink/VACUUM claim.

Global system_health remainsdegraded with one unresolved feedback, created
2026-10-20 04:08:53UTC while source296b was still observed at04:10:58UTC.
This is retained as an existing business alert; it was not resolved/suppressed
to get a green result. No zero-violation or whole-project completion claim is
made. See validation/2026-10-19-shared-actions-live.json for metadata only.
