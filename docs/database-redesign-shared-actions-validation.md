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
Other payload bytes and structural columns are copied unchanged. Temporary rows
and water staging are dropped before commit. Both EXCEPT directions compare
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

Parent independently verified1215paths/fccf517e and committed the exact handoff at93058af1f. Independent SPEC approved that frozen source. Parent normally merged origin1319e2557 at8172fbc75, retaining all claim rows in the append conflict. The11app/tests/scripts changes are independently developed image/OCR/WeChat/purpose-receipt fixes; shared lifecycle, schema, paired release and both migrated DAO implementations are unchanged. Merged manifest records1216paths/201756cb4812572741a79a0e03f9670ca7ec9af87ab65381656b7a39a7584170. Integration SPEC/QUALITY and parent full-copy/native/current-data inverse/live remain pending. Root read-only eligibility probe passed all10984direct steps on the accepted staged19.5copy, which was checkpointed to DELETE journal with all physical facts unchanged and made read-only; input SHA9a1c692c715e2554b1b124b988ee350a8542ae2718e3f595918cba0fd9be1c4f,476516352bytes. This is not a fresh raw production snapshot.
