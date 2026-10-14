# Follow-up/Todo-sync action storage validation

Task 1A is an unselected, disabled candidate. The explicit `include_followup_sync=True` declaration extends only the reviewed Email29 actual/fresh action scaffold. It defines nine typed record kinds, source-affinity `sync_` projections for the 150 source fields, 24 corresponding index declarations, eight source CHECKs, four retained external business foreign keys and one typed internal draft foreign key. The internal draft public ID is projected through its parent action. Existing shared task, run, candidate and Email reference slots are NULL for the nine kinds. The default flag leaves earlier manifests unchanged.

The source of truth is the accepted actual/fresh capture and 150-row field map in `docs/superpowers/specs/`. Direct target SQL tests cover every field in both layouts, index key shape, unique business dedupe, provider ID uniqueness distinction, the typed draft relationship, source public-ID blocking while referenced, raw JSON bytes, NUL, BLOB and nonfinite scalar values. The focused test started RED because the new definitions module was absent, then passed after implementation. SPEC review found that the legacy send-attempt `draft_id` NOT NULL check had been skipped with the business physical-FK exception. A native source comparison reproduced four RED cases (NULL and SQLite-bound NaN in both variants); the scoped correction and six legal zero/negative/dangling scalar cases passed. The final follow-up storage, source oracle and Email storage command passed 47 selected tests. All eight earlier declaration variants (6/9/12/14/actual and fresh 16/actual and fresh Email29) compared byte-identical with committed HEAD source before this scoped correction, which changes only the opt-in declaration. Ruff and diff checks passed. This is declaration and projection evidence only: source-shaped omitted defaults, writer locking, native allocation, mutation parity, migration and live caller behavior belong to later tasks. No constructor selects this schema, and no Store version or production migration is claimed.


## Task1A independent acceptance

The initial declaration4143fe11 lost the NOT NULL constraint on legacy
follow_up_send_attempts.draft_id by exempting every draft_id field. Independent
SPEC compared3,666 native source/target scalar cases and found exactly four
mismatches: NULL and SQLite-bound NaN in each variant. Repair1cfeb2fe scopes
the exemption only to the business attempt's physical reference. Four RED
comparisons became green; legal zero/negative/dangling legacy values remain
unconstrained scalars. No extra legacy FK was introduced.

Sequential independent SPEC then QUALITY accepted1cfeb2fe. SPEC rechecked
20 native FK-on scalar boundaries,36 all-nine negative/zero-ID projections,
150 exact field declarations/defaults/order,24 index semantics,8 CHECKs,
the2-valid/14-refused option matrix and8 unchanged prior SQL manifests.
The selected implementation/source suites passed47 checks. QUALITY passed
11 focused checks with6 deselections and found no blocking value/constraint
counterexample. Counts overlap; they are not additive coverage totals.

QUALITY did identify a concrete performance gate before activation: the new
sync_draft_action_id native self-FK has no supporting child index. SQLite
3.53.4 scans the shared actions B-tree for native parent deletion. With0/100/
1000 unrelated cancellation rows, Email29 DELETE costs3278/29578/266278 VM
steps and Followup38 costs4387/30987/270387; the added loop is3 VM steps per
unrelated row. Literal source business-draft deletion remains29 steps with
0 or1000 unrelated work_updates. The24 source indexes do not contain a draft
child-key index. A measured supporting target index must remove this unrelated-
type growth before release; existing Email29 cost is a separate baseline, not
evidence that native FK access is already bounded. Task6 must include native
FK parent DELETE/public-key mutation as well as explicit application SELECTs.

This accepts only disabled declarations/projections. Source-shaped writers,
omitted defaults, lock/allocation semantics and mutation parity remain1B.
No constructor/version/migration/caller/provider or publication is accepted.
All review fixtures were in memory or cleaned owned temporary directories.
