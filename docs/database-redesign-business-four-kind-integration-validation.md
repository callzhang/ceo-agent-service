# Four-kind Business caller and paired-release integration validation

This local candidate extends the unpublished Store `2026-10-14.1` / Email52 pair with the four-kind Business table. It is neither merged nor published. The already accepted Business storage and finite migration modules remain the source of the schema and inverse contracts; this integration selects them for current constructors and the exact published Store `2026-10-13.5` / Email51 predecessor.

Current `AutoReplyStore` and `EmailStore` constructors install or require one Business table for Task, Project, Anchor and Project context revision. Parent callers use source-shaped projections and the typed Business writers, so public signed IDs, model fields, CRM, context, import fingerprints, Attention, Web and retained child behavior remain on their original public contracts. The 15 retained child tables keep their shapes; their 19 incoming references target typed Business aliases. The current manifest requires the table, generated aliases and 13 indexes. A correct marker with malformed Business structure is refused on both fresh and cached constructor paths, without read-time repair. An unfiltered Task list selects the `(updated_at, public_id)` index; filtered lists keep their existing predicates and ordering.

The paired coordinator captures both complete Email and Business source declarations before DDL under one `BEGIN IMMEDIATE` with foreign keys off. It migrates Email, then Business, verifies both inverse metadata values, writes the Store and Email markers, verifies the complete target, and commits. Rollback inverses current Business facts first, then current Email facts, and restores the exact original Store value/timestamp and Email ledger. The target constructor cannot open a source or mixed pair directly. The coordinator does not support an unpublished intermediate Store20/Email52 copy that lacks Business.

Evidence from the scoped local runs:

- The first current caller and SQL-trace tests failed against the old-parent callers before conversion. The first paired publication test failed before Business conversion was selected; the source-fixture test failed once current Store bootstrap stopped creating old parents. Those fixtures were adapted to the frozen predecessor source, while current API fixtures continue to construct the selected target.
- `tests/test_business_schema_release.py`: 35 passed. The accepted source migration module was not modified. Its source-declaration-only test now opens a predecessor fixture instead of a current Store.
- `tests/test_task_semantic_store.py`: 483 passed. Its public model hydration, current manifest, native CHECK/FK, Project Attention migration and rollback tests run against the target. Eight genuinely retired bootstrap functions were removed from the mixed current file and preserved by `tests/test_business_legacy_schema.py`: the exact published `7dadc492c1c62d5b0624f2f97dd0c89f4f358646` archive runs all nine parameterized cases, with exact JUnit case names/counts and no skips. The wrapper passed; its temporary archive is removed.
- `tests/test_email_action_schema_release.py`: 71 passed before the additional two-variant post-publication Business-write test; that test passed 2/2. Source fixtures contain the four original Business parent declarations, 15 child declarations and source facts. Preflight refusal is read-only. Injected failures after Email conversion, after the first Business child DDL, at the first/second marker boundary, after genuine target validation, in Business inverse DDL and in later Email inverse preserve exact source or target database images. Marker-correct missing Business index is refused. Later Task, Project, Anchor and context writes survive inverse and re-publication; original markers return exactly.
- Current caller and constructor runs: 63 passed across `tests/test_business_integration_callers.py`, `tests/test_email_action_callers.py`, `tests/test_email_current_schema.py`, `tests/test_store_schema_release.py` and `tests/test_email_store_startup_validation.py`. Other focused current project-context, import, Attention, Todo, resolution, semantic service, minutes, Web and Task Agent groups passed 60 and 512 cases, with exact failed fixture cases rerun green after adaptation. The default Task list has a targeted query-plan assertion that no temporary order B-tree is used and a public-ID tie-order assertion where physical and public order differ.
- Scoped Ruff and Python compilation passed. The current-app SQL-owner search found no direct old-parent DML or FROM/JOIN references in `app/` or `scripts/`; remaining names are source migration/oracle declarations, removed-table manifests, source-shaped import labels and offline diagnostic labels.

Task 4's native copied-production validation, copy-harness assertions and real deployment CLI rehearsal remain separate work. No production database, provider, deployed checkout or service was touched by these local tests.

## Current CRM caller transaction regression

The current Console CRM search regression first failed with `Business writes require a caller transaction` in `update_business`. The endpoint had opened a bare connection, and the adjacent confirmation and clearing methods used the same bare-connection pattern. All three now use the existing Business writer transaction. The exact end-to-end test passed after the change: search saved candidates without linking or writing CRM, an unseen customer remained rejected, the selected customer could be confirmed, and the link could be cleared. The typed writer retains its strict caller-transaction requirement.

## Current caller fixture closure

Five current-target files covering the Project-centered evaluation oracle,
Console Task release contract, Project CRM behavior, Web Attention ordering and
source Project registration passed 55 cases. Their negative oracle assertions
remain intact. Fixtures now use source-shaped Business projections for reads
and typed Business inserts/updates within caller-owned transactions. The
genuinely retired Project CRM column-upgrade test passed under the exact
published `7dadc492` source code and joined the archive-backed historical
wrapper, which checks ten exact parameterized cases without skips. The
opt-in copied-database acceptance file received its one current Business
projection read; it was not run against a production copy in this fixture pass.


## Independent Task2+3 acceptance

Fresh SPEC accepted947f3518 overfc35e811 after independently running120
release/constructor/history checks and10 direct caller checks. Fresh subsequent
QUALITY accepted the same commit after110 constructor/release/import/Web
checks and the archived historical wrapper. The latter executes exactlynine
original cases from published7dadc492, with no skips or errors. These are
independent overlapping focused runs, not a summed unique test count.

The reviews covered current/cached bootstrap, exact Business and retained-child
declarations, public-ID reads and typed writes, default-list index/public order,
import/Attention/ownerless/Web callers, and paired source capture, forward,
current-fact inverse and exact markers/FK findings. No new policy, invented
business field, old-parent fallback or dual writer was introduced.

QUALITY's273-file temporary pytest root `/private/tmp/business-quality.Xj9m3i`
remains pending direct user response: automatic approval review rejected its
`rm -rf` cleanup as requiring approval under AskForApproval Never. Root did
not retry via another deletion mechanism. Read-only lsof found no open handle;
metadata traversal reported150695834 file bytes (including symlink targets).
This is test data, not a production database or retained source backup.

Task4's actual production-source copy, genuine old-code read/write, current-fact
inverse/reforward, real native CLI with provider substitutes, combined CI and
production publication remain outstanding. Production121-table observation is
unchanged by this local acceptance; the complete10-core-plus-ledger goal is
still incomplete.

## Task 4 local combined-copy harness

The unpublished harness now treats the four moved Business parents, fifteen
rebuilt children and target `business` table as the changing partition in the
paired copy. It compares complete source-shaped parent and child facts,
including SQLite storage classes and hidden ROWIDs, thirteen independent
waters, FK findings and original-row preservation at each relevant stage.
The paired target checks a structural sixteen-table decrease; no capacity
saving is inferred from that count. Four current public Business writes and
four genuine published-old-code public writes are checked across inverse and
re-publication alongside the existing Email public writes. The old code runs
in a cold archived subprocess. Returned Business evidence contains counts,
digests and public IDs, without raw rows.

The preflight is bound to the root-owned metadata-only published-source receipt
`docs/superpowers/specs/2026-10-15-database-redesign-business-email-published-source.json`:
`7dadc492` Store19/Email51, 121 non-SQLite-internal tables, exact Email declarations,
the Business `actual` variant and both Business declaration fingerprints.
Live Business row counts are informational because the service may advance.
The three other SQL-boundary cases in the script remain Email-only. The native
copied-DB helper now verifies Business target shape and retired-parent absence
before its immutable baseline; no native CLI was invoked by this harness edit.

The first focused tests failed on the missing Business image and the old
Email-only paired partition. After implementation, the original scoped harness
group passed 53 tests; the exact `7dadc492` published-code paired and native
fixtures passed 2 tests. A synthetic Business actual/fresh image test also
covers target edits, deletion, project and child rebind, inverse and reforward.
The root-owned actual production-source copy, real native lifecycle, combined
CI, main merge, push and deploy remain separate pending evidence.

## Task 4 harness reproducibility correction

Independent QUALITY review found that the copied-database coordinator checked
some proof files for uncommitted changes but omitted `store.py`, `email_store.py`,
`project_context_service.py`, `task_semantic_models.py` and the source binding
JSON, even though they affect the proof. It also allowed a descendant archive
ref while applying the exact `7dadc492` binding. Five dirty-input and one
descendant-ref regressions first failed. The harness now guards and hashes all
executed modules and the binding, and requires the supplied archive ref plus
both binding production HEADs to equal the published `7dadc492` commit.
The fourteen focused coordinator and negative tests pass after the correction.
The root-owned copied production run and its receipts remain separate evidence.

The remaining copied-proof QUALITY issue was an incomplete app import guard:
three direct imports were absent from the finite hash report, and other app
modules could change locally without failing the preflight. A new regression
for dirty `app/process_runner.py`, outside the finite hash list, failed before
the correction and passed after it. The Git clean check now covers the whole
candidate `app/` tree plus the finite proof paths, including the binding JSON.
The report hashes the original named core modules and the three direct imports
(`email_classifier_contracts.py`, `email_unsubscribe.py`, `audit_web.py`) and
labels this as a finite list. Nine focused guard/report checks passed; no
copied-production or native run was performed for this correction.

## Root copied-source and native acceptance at df72a900

The actual source was captured by SQLite online backup at
2026-10-15T15:35:24 UTC. The production checkout was `7dadc492` before and
after capture, Store `2026-10-13.5` / Email51. The immutable input is
485212160 bytes, SHA-256
`970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`.
Its integrity check passed; its existing single runs/tasks FK finding was
preserved, not introduced by this unit. Code-HEAD observations and online backup
are separate observations, not an atomic production-code/database snapshot.

After the scoped guard corrections passed fresh SPEC then QUALITY, root reran
all four copy cases at exact clean `df72a9005cdc81c2c46ee723c1c16bfbb73074c2`.
The combined paired case passed publication, four current public Business
writes, current-fact inverse, four genuine cold published-old-code Business
writes and Email writes, and re-publication. The three independent Email-only
SQL-boundary cases also passed. Whole-worktree cleanliness, HEAD and hashes of
350 tracked application files were equal before/after; the input SHA was
unchanged. This supersedes the preliminary successful run at `62e16d25`.
Metadata and exact proof/module hashes are in
`superpowers/specs/2026-10-15-database-redesign-business-email-copy-acceptance.json`.

Root's eighteen-file focused group passed **1222 tests in 894.16 seconds**.
Its private pytest root was removed and absence checked. All 138 Python files
changed against published `7dadc492` passed Ruff and compilation; diff checks
passed. Independent final integration review found no actionable issue in the
bounded Business/Email wiring. These focused results are not a combined CI run.

Five owned-copy cases used real Codex CLI0.154.0 and the configured native
OAuth home; transcript bodies were not copied into evidence or SQLite:

| Case | Real CLI invocations | In-memory provider calls | Verified result |
|---|---:|---:|---|
| Reply approval |2|1|done; one persisted receipt and sent result; reopen without a new invocation |
| Reply revision |4|1|Audit returns R0; Consumer R1 then Audit approve; one receipt; done and reopen |
| Work summary |1|0|candidate Business Task595, linked source signal, done and compacted input, duplicate enqueue returns the same input |
| Scheduled execution |2|1|occurrence maps to its execution Task; done and persisted terminal result executed |
| Workbench |1|0|completed text turn, four native events and reopened provider-session reference |

These are ten invocations and nine distinct native sessions. Every case kept
the immutable input and post-publication baseline hashes and preserved original
rows in all100 ordinary row-storage tables. The full target count is105 because
that count also includes the FTS virtual table and four shadow tables; the row
comparison deliberately excludes that virtual family. Approval ran at076f3074,
revision atd64c7376, and the other three atdf72a900. Root verified no difference
in `app/` or either native harness between those commits and the final copy
candidate. The scheduled report does not export persisted DB transcript bounds
or a receipt count; native files were independently located by the exact test
Task ID and native metadata cwd. Do not extend its evidence beyond its own
assertions. Native references, exported bounds and range/file hashes are in
`superpowers/specs/2026-10-15-database-redesign-business-email-native-acceptance.json`.
There were zero real outbound provider calls.

### Capacity and remaining approved work

On the same immutable input, before any native fixture writes, source121 tables
became target105 tables. Here table counts mean sqlite_schema tables whose names
do not start `sqlite_`, including FTS virtual/shadow tables. Source main/page
bytes485212160 became target507965440, **an increase of22753280 bytes**, with
zero freelist pages on both. This is structural consolidation, not a space-saving
acceptance. Moved families gained16027648 data-page bytes and6545408 index-page
bytes; schema/internal pages account for the remaining180224 bytes. Other user
table families had zero allocated-page delta. The capacity receipt records every
table, the57 target actions indexes, the native-baseline provenance, and the
direction-level approved CSV mapping:
`superpowers/specs/2026-10-15-database-redesign-business-email-capacity.json`.
It authorizes no index or historical-row deletion.

The original ten-core-plus-one-ledger design remains the goal. The105-table
candidate still contains32 old families mapped to actions, eight to business,
twelve to tasks, thirty-four to configs/provider_state/schedules/state, and
eight structures approved for retirement. The remaining provenance, emails,+runs, legacy-identity and migration facts also have their approved destinations.
The net remaining structural reduction is94 tables; this inventory is not
executable DDL or a claim those families are migrated.

The three additional-case roots were removed and absence checked; all test task
material directories were checked absent. Two completed native-copy roots,
`/private/tmp/ceo-native-copy.318hhr_b` and
`/private/tmp/ceo-native-copy.g2i6unkf`, remain pending direct user response:
automatic approval rejected their explicit cleanup as requiring approval under
AskForApproval Never. Their metadata was saved and lsof found no open handles;
root did not retry deletion through another mechanism. The previously rejected
`/private/tmp/business-quality.Xj9m3i` cleanup is independently still pending.
After evidence commitb2600c86, root rechecked the immutable input SHA and
committed receipts, verified all24 input-directory files belong to this task,
and found no open handles. Explicit cleanup of
`/private/tmp/ceo-business-combined-actual-u6i3ldg0` (485401660 bytes including
intermediate metadata) was also automatically rejected as requiring approval
under AskForApproval Never. A separate exact-path user question is pending;
root did not use another deletion mechanism. The input is no longer needed
for this acceptance. No historical database/code archive is committed.

Email-only CI38748142018 at82795db4 passed13796 Python tests,729 frontend tests,
the Workbench build and two browser checks. It is not combined Business coverage.
A new combined CI result, main merge, production migration/deploy and operational
readback remain separate gates. No production database write, main merge,
restart, deployment or real test delivery occurred during this acceptance.
