# Business model simplification: current completion and closeout

## Current work — adapter retirement and Signal source ownership

The new authorized unit follows the prior history closeout. Adapter retirement
is implemented, independently reviewed and committed as `d2dc7c6c3`; its44 API,
9 page and66 backend focused tests pass. It has not been separately deployed.
Signal source ownership is implemented as candidate Store25.3/Email54 with the
[frozen field/DDL/reverse contract](signal-source-owner-unification.md).
Independent SPEC/QUALITY reviews, scoped tests, complete production-derived copy
forward/current-data inverse/real old-code cold read/write/reforward and a
second inverse-source continuation proof all passed. The [acceptance record](signal-source-owner-validation.md)
keeps exact cohort sizes, manifest digests, overlapping test counts and the two
baseline ABI failures separate. The generated full SQL artifact is refreshed.
The release-coordination task owns the protected selector and explicit historical
release-tool corrections; the root business-model task is now the sole owner of
final integration, Quality, standard deploy, production readback and cleanup.
[Current Quality closeout](quality-closeout-20261025.md) records each failed
iteration, fixture retirement mapping, independent reviews and completed focused
checks. The final pinned target is476 objects, SHA256
`234e17843afc171b4fa8cb42f3e1d959ef2ba415f43e295dc7db640ab3d02221`.
Its fresh production-derived copy passed forward/current-data inverse/actual
predecessor cold reads and writes/reforward:1140 original Signals,1147 final
Signals,48 other typed table snapshots preserved and the original FK finding set
unchanged. Real provider calls were zero. Current-head Quality and standard
release/readback are still pending; the earlier475-object evidence does not
substitute for this final476-object proof. No real test send occurred. Production was read as Store25.2/Email54 with1140
Signals (110 inline,1030 referenced;172 parents); this is the source checkpoint,
not a deployment of25.3. Final integrated release/readback and cleanup remain
pending. Copy Chrome verification of formal Tasks, source/raw record, canonical
legacy detail and provider receipt succeeded; frontend build passed. The eleven
previously blocked test roots and four API outputs are now deleted and verified
absent (12,246,364,678 logical bytes); the active Signal inputs/backup await final
release readback. No physical database saving is attributed to that cleanup.

## Previous history closeout — production readback, 2026-10-25

| Area | Verified state | Evidence / remaining boundary |
|---|---|---|
| Production release | Standard deploy completed `41404b469 → a6ab02a77`; PID79189 running, health OK; Store25.2/Email54 unchanged. | [Production readback](validation/2026-10-25-history-production-readback.json). These are dated readback facts, not a permanent live-state assertion. |
| Task/Run, Email and Action ownership | Previously deployed native Task/Run, Email54, A5b exact receipt references and Store25.2 trigger retirement remain in use. | Not reimplemented in this phase; 46 receipt references remain. |
| Legacy business reads | Parallel operational list/search/DTO/ranking removed. Current inventory uses Business Tasks; exact historical detail preserves all42 fields, provenance, updates and receipts. | [Field/caller and retirement contract](business-model-history-read-unification.md). No schema or historical row deletion; same-number domains remain separate. |
| A5 native acceptance | Original trigger and material passed current real GPT-6 Luna/high Consumer/Audit and reviewed option selection; zero sends before selection, then one substitute send/verification/receipt. | [Original-case revalidation](business-model-a5-original-revalidation.md). The old failed run remains recorded; no Consumer/Audit runtime change or universal model-success claim. |
| Tests and UI | Backend combined96pass, post-merge focused17pass, two harness argument tests; frontend four files58pass; TypeScript/build/Ruff/compile/diff checks pass. | Scoped evidence only, no full-suite-green claim. Copy and production Chrome formal-list/history/receipt readback completed. |
| Current queues | Pending6, processing0, failed28, retryable0, Attention4; History returned20 items. | Failed28 and Attention4 match the pre-deploy snapshot; no real test send or production fixture task was created. |
| Known data exception | Original sole FK finding `('runs',190042,'tasks',1)` remains unchanged. | Last value is FK ordinal; no inferred Task, NULL repair or deletion. Separate from this read-model change. |
| Artifact cleanup | Latest workflow backup verified complete/quick_check OK; exact cleanup still blocked by platform approval Never. | 11 owned test roots, one backup and four API files rechecked present; about11.86GiB. Authorization already exists; no alternate deletion mechanism. |

This table records the previous completed history closeout. The new work status above is authoritative for the adapter/Signal scope. Sections below retain dated checkpoints;
old production versions, unactivated constructors and pending release statements
must not be read as current state. For that previous history scope, implementation, reviews, copy checks, native acceptance and release/readback are complete; artifact cleanup remained blocked at that checkpoint. The broader historical non-green test result is not
rewritten by this scoped acceptance.

## Artifact cleanup authorization and recheck — 2026-10-25

Derek explicitly renewed authorization to delete this round's test copies and
verified backup together with earlier task-owned test directories. The bounded
inventory found **10 test roots, one workflow backup and four temporary API
outputs**, totaling **11,346,504,616 logical bytes (10.57 GiB)**. This is an
artifact inventory, not a physical database-space reduction or APFS reclaimed
space measurement. The previously reported 4.8 GB task root is now measured at
6,179,786,995 bytes; the current inventory supersedes that earlier size.

Every listed directory was resolved to an explicit path, had no symlinks, and
had no open files according to the combined lsof check. The backup passed a
fresh read-only SQLite quick_check (`ok`); its earlier deployment verification
is recorded below. Production data, native trajectories, repository regression
tests and the unrelated `backups/dws-doc-jsonml-87ddde41` directory are excluded.

The exact rm command was **rejected before execution** by platform approval:
`approval required by policy, but AskForApproval is set to Never`. The subsequent
existence check confirmed all 15 targets remain. No alternate deletion path was
used. User authorization is already present; the remaining step requires an
execution environment that permits this authorized deletion, followed by an
absence check. Production health remained `ok`. No runtime code, database or
service restart was changed in this cleanup attempt.

The complete target list is in the [trigger retirement closeout](action-reference-trigger-retirement-validation.md#renewed-cleanup-attempt--2026-10-25).

## Dangling Action trigger retirement — 2026-10-25

The exact Store25.1 declaration retained one invalid candidate-reference trigger.
All four reported schema-rename failures were reproduced. The Store25.2 target
retires only `trg_actions_public_reference_update`; it keeps all data, native
FKs and other guards. The 51-test focused selection passed, and the current
production-derived copy passed typed-data/sequence comparison across 49 tables,
forward/inverse, new Task/Action/receipt preservation, real Store25.1 code cold
read/write, and reforward. The original FK finding is unchanged; no sends were
performed. Standard deployment completed at `bf27bfaeb`; production reads
Store25.2/Email54, the trigger is absent, read-only public-key update compilation
passes, health is OK, and failed remains 26. History and referenced receipt
readback passed. Cleanup of the new 508 MiB validation root and the verified
491 MiB backup was authorized but rejected by automatic approval policy Never;
those files remain. Details and the exact scope are in
[trigger retirement validation](action-reference-trigger-retirement-validation.md).

## Historical production baseline — 2026-10-25 (superseded)

This section records a production checkpoint before the later Store25.2
publication. It is superseded by the authoritative current status at the top
of this document; its earlier Store version is not the current production schema.
At that readback,
production checkout was `a9694da3696e946ad410b58bd3393ddc3f7dae18`;
`/healthz` returned `{"ok":true,"status":"ok"}`. The database then read
Store `2026-10-23.1` / Email54 and had the common `tasks`, `runs`,
`runtime_attempts`, `task_inputs`, `task_business_keys`, and `work_summary_inputs`
owners. Counts at read time were 19,672 Tasks, 38,194 Runs, 26,389 Runtime
Attempts, 8,869 TaskInputs, 8,647 TaskBusinessKeys, and 9,140 WorkSummaryInputs.
At that readback, the only `foreign_key_check` result was `runs(190042) -> tasks` ordinal 1;
it is preserved and was not repaired. The database file at that readback was 514,969,600
bytes, with 14,407 free pages (about 56.3 MiB).

The read-only owner inspection found 975 `work_project`, 3,327 `work_todo`, and
5,468 `work_updates` facts. The current semantic importer promotes only records
with its existing source-backed formality evidence; other records remain
history-only. Their old status values are not current provider readbacks and do
not authorize bulk promotion, cancellation, or merging. Further retirement
requires a producer/reader/use crosswalk and a proven owner for each retained
fact.

The current source removes the two remaining manual `work_summary_inputs`
readers' dependence on the Task20 compatibility projection. A related point
lookup in the legacy Project/Todo/Update importer now uses the existing unique
public-ID index directly and avoids its unnecessary scalar sort. Focused local
validation on 2026-10-25: semantic-import file 25 passed; minutes owner
backfill file 9 passed. The affected replay file exposed and now covers a
fixed-input payload CAS omission: replay had checked the source identity and
digest metadata but not the decoded stored body before restoring the frozen
input. With the body comparison, the replay file passes 29/29. Ruff, Python
parse and compile checks pass. This scoped code cleanup was included in
`4bae03e41` and deployed. It does not delete these historical records or prove
a replacement owner for their remaining readers.

## A5b exact Reply receipt ownership — production acceptance, 2026-10-25

The read-only production preflight found 276 non-empty Reply OA result
snapshots (91,758 SQLite characters / 99,292 UTF-8 bytes). Exactly 46 have one
byte-identical provider result receipt on the same Run ancestor chain and exact
operation, process-instance ID, and task ID (28,080 UTF-8 bytes); the remaining
230 stay local Attempt facts. This is logical duplicate payload only, not a
physical SQLite file-size reduction claim.

The local Store `2026-10-23.1` / Email54 → `2026-10-25.1` / Email54 release now
stores those exact links in `reply_attempt_receipt_refs`. New completion writes
receive the receipt ID from the verified candidate action, validate the exact
body, write the reference and clear the duplicate Attempt body atomically.
History/detail projection hydrates the referenced immutable receipt; unlinked
local values remain inline, and provider receipt retirement checks the new FK.
The inverse hydrates referenced bodies before dropping the relation and restores
the original marker and declaration.

Validation: the current-production-derived Store23.1/Email54 copy forwarded 46
references, preserved the single existing FK finding and passed `quick_check`;
schema inverse restored the source declaration/marker, and reforward again
published 46 references. Earlier focused release and writer coverage verified
all original raw Attempt result bodies through inverse. The post-merge scoped
selection passed 43 tests, including A5b release, projection, writer, worker,
Email54 marker, state binding and current deployment-drain fixtures. Fresh Luna
SPEC and QUALITY reviews both approved.

Real Codex CLI on the migrated copy passed both `approve` and `revision` cases
with the external provider substituted in memory. The approve case completed
three Consumer/Audit revisions (6 CLI calls); the revision case recorded
Audit `return` at revision 0 and `approve` at revision 1 (4 CLI calls). Each
finished `done` with one business Attempt, one substitute send, one verification
and one receipt. In each case 48 existing table contents remained unchanged.
No production send occurred. One first-run harness setup attempt failed only
its raw-file hash check because separately backed-up SQLite files had different
physical bytes; the successful runs used an exact closed-file baseline and also
checked semantic row preservation. After the latest `origin/main` integration,
the `revision` case passed again against the updated native CLI output reader:
Audit `return` then `approve`, four CLI calls, one substitute send and receipt,
and the same 48-table preservation check.

A5b implementation, copy inverse, native CLI acceptance and production
deployment/readback are complete. The original A5 native failure remains preserved, while the exact original input
and selection now pass current native revalidation. This does not claim all future
model outputs pass or change Consumer/Audit behavior; see [A5 original revalidation](business-model-a5-original-revalidation.md). The old Project/Todo/Update write paths have
already been retired, while their history readers still have consumers. No
safe whole-row deletion or source-backed replacement owner has been proven, so
that cleanup has no authorized data-deletion change in this phase.

Production deployment completed on 2026-10-25 at commit
`4bae03e41e4d67aa8b759e5d782db4cea784be59`; the running service PID was 9910
and `/healthz` returned OK. The Store marker is `2026-10-25.1`, Email remains
54, there are 46 receipt references, `quick_check=ok`, and the sole existing FK
finding `runs(190042) -> tasks(1)` is unchanged. The SQLite file remains
514,969,600 bytes; no physical shrink is claimed. Status, Attention, History,
`/history`, `/scheduled-tasks`, and Attempt #15369 detail returned successfully.
The Attempt detail rendered the referenced provider receipt after expanding
“已核验回执”; the scheduled-task screen listed 14 tasks and recent run history.
No production send occurred during acceptance.

The first standard deployment attempt failed after restart because the release
object lacked the required `verify_loaded()` method. Automatic inverse and
checkout rollback completed, restoring Store23.1/Email54 with health recovered.
The missing readback method and its regression were added; 31 focused release
and deployment-drain tests passed before the successful second deployment.

The successful deployment backup was verified (`complete` marker, `quick_check`
and the original FK finding), and is at
`backups/auto-reply-2026-10-25.sqlite3`. Cleanup of this backup and the 4.8 GB
task-owned copy directory was authorized by Derek but the file-deletion command
was rejected by the current automatic approval policy (“approval required,
AskForApproval is Never”). They remain the only closeout blocker; no alternate
deletion mechanism was used. Remove them when deletion is enabled, then verify
both paths are absent. The original A5 historical failure remains preserved despite the current input
revalidation; this does not generalize to every model output or change Consumer/Audit behavior. See [A5 original revalidation](business-model-a5-original-revalidation.md). The Project/Todo/Update
historical-owner mapping remains separately unproven.

## Email business-model phase audit — 2026-10-24

The current source already contains the accepted Email B1/B2 model: `emails`
owns canonical message metadata, and Email operations use the shared Action
lifecycle. The remaining Email-specific owners are the classification Task
input, historical-scan candidates, and bounded content-free runtime samples.
Code inspection and read-only production aggregates found no safe whole-record
deletion or additional schema merge among those owners. The only immediately
supported content reduction is the existing explicit compaction of completed
classification inputs; it was not run in this turn.

Email 53→54 was migrated and deployed on 2026-10-24 at `a4154c03b` through the
standard release path. The full current-data forward/inverse/reforward rehearsal
passed before deployment. Production now reads Store `2026-10-23.1` / Email54;
schema manifest and `quick_check` pass, and health, active/queued dashboard,
Attention and History endpoints returned HTTP 200. One pre-existing foreign-key
finding on `runs` row 190042 remains unchanged. The 182,907-byte
reduction is the candidate JSON logical payload only; the 514,969,600-byte
production SQLite file was not VACUUMed and did not shrink. After successful
readback, the verified deployment backup and remaining backup-directory
artifacts were cleared as requested. Exact ownership and reduction evidence is
recorded in [Email phase audit](business-model-email-phase3-audit.md).

This Email audit does not close the separate A5b Reply-local-result item or
claim that the broader business-model program is complete.

## Action Entity Phase 1 production acceptance — 2026-10-22

Phase 1 (the first external-Action unit, Tasks 1–7) is deployed and read back at production commit `87a34d299d5749af1af454bfdd01c48a936799a4`, after the standard deploy from production `036b14624cddbcff04098ebec7697644f95370c0` through `bf07de8d6210709cd76887cc14ebb9ef23fa5600`. The deployed Store marker is `2026-10-21.1`, Email ledger 53, with 14 ordinary tables. Production readback confirmed 15,472 common Actions, 18,621 Action Receipts and 506 Observers; all seven retired populated source kinds have zero rows. The original sole FK finding remains `runs(190042) -> tasks(1)`. Health, status, queues, Attention and History returned HTTP 200. PID is 2903.

The deploy began with one active process and two active queue items; after quiet-wait/deploy, active items were zero. Queued count moved 146→155 as normal arrivals occurred during the interval, so this is not a claim that all queued work completed. Failed count stayed 36 and Attention stayed 19. History returned 20 rows; the exact sent-detail readback resolved Attempt 15914 / Action 105320 / receipt 5046 with parsed delivery status 200. No test fixture was present in production and native CLI/provider tests used only the strict in-memory substitute.

The only retained verified deploy backup is `/Users/derek/Library/Application Support/ceo-agent-service/backups/auto-reply-before-upgrade-deploy-9351777a-c7be-4845-889a-5759fd342790.sqlite3` (501,170,176 bytes; `backup_is_complete=True`). Production was not VACUUMed: the main database file was 501,170,176 bytes at readback, freelist 1,037 pages (about 4.05 MiB), and WAL may vary. The separate same-cohort, VACUUMed source-derived copy was 494,678,016→492,015,616 bytes (−2,662,400; about 2.54 MiB), with table pages +1,548 and index pages −2,187. This is a bounded copy measurement, not a production shrink or performance claim.

The reviewed release source is runtime code HEAD `87a34d299d5749af1af454bfdd01c48a936799a4`; this documentation-only closeout will have a different commit and does not change what is live. Phase 1 is complete. The wider program remains incomplete: other Action kinds, unsubscribe browser continuation/steps, Task/Run unification, Email ownership and remaining business-record work are separate units. This release does not claim full Actions retirement or whole-database unification.

Evidence: [production acceptance](action-entity-production-acceptance.md), [Task 7 integration report](../.superpowers/sdd/2026-10-21-action-entity-unification/task-7-integration-report.md), [quiet-source repair report](../.superpowers/sdd/2026-10-21-action-entity-unification/task-7-quiet-source-report.md), [complete execution rulings](action-entity-execution-rulings.md), and [validation record](action-entity-unification-validation.md).


## Historical production status at 2026-10-21 release (superseded by the 2026-10-22 Action Entity release)

The approved A/B/C/D implementation and Store19.11 / Email53 production migration completed on 2026-10-20. The release was `a79d8e279`, with actual production schema, process, effective GPT-6 Luna/high configuration, health, queues, Attention and History read back. This describes that verified release, not an assertion about an unqueried later process. [Final release evidence](database-gpt6-release-validation.md) supersedes the earlier publication-pending checkpoints below.

| Unit | Completed behavior | Acceptance source |
| --- | --- | --- |
| A1 | Selection binds immutable candidate + option; no second selected branch body | [A1 copy](business-model-a1-copy-validation.md), [native/UI](business-model-a1-runtime-validation.md) |
| A2 / A4 | Verified external receipt is authoritative; attempt and Sent retain references and independent local results | [Execution checkpoints](#historical-execution-checkpoints), [final release](database-gpt6-release-validation.md) |
| A3 / A5 | Candidate owns execution; Prepared Action owns dispatch claim / accepted receipt; retired duplicate writers removed | [Prepared owner](database-redesign-prepared-actions-validation.md), [final combined copy/inverse](database-gpt6-release-validation.md) |
| B1 | One message metadata owner; preview bounded to 1,000 Unicode characters; original read explicitly from source | [Email metadata](database-redesign-email-metadata-validation.md) |
| B2 | Shared direct and specialized Email action claim/result ownership | [Specialized actions](database-redesign-specialized-actions-validation.md), [final release](database-gpt6-release-validation.md) |
| C1 | Nine executable kinds share actual Task lifecycle ownership; other kinds retain their domain facts | [C1](business-model-task-lifecycle-validation.md) |
| C2 | Professional histories use common logical Run owner with preserved public identities and native references | [C2 parent copy](business-model-run-owner-parent-validation.md) |
| D | Old Project/Todo operational producers retired; history/provenance/receipts retained; 51 → 42 fields | [Legacy Todo](business-model-legacy-todo-parent-validation.md), [field mapping](business-model-legacy-fields-mapping.md), [published nine-field proof](database-gpt6-release-validation.md) |

The combined full-copy current-data inverse, actual old-code cold read and reforward passed. The real GPT-6 Luna Consumer → Audit → public selection → Worker/SystemExecutor → verified substitute receipt chain passed; real test sends were zero. The original FK finding and historical model failures remain recorded, rather than relabeled as successes. The nine approved retired controls are unrecoverable: inverse restores only neutral defaults.

## Authorized closeout completed — 2026-10-21

The 36-route UI matrix was a generic inventory and only recorded six early browser observations. It did not imply 336 functional failures. Later browser evidence is now reconciled separately from frontend mock tests, fresh-copy API traversal and native/DB proof. Unobserved generic browser states remain `not_run`; their migration impact and the actual acceptance layer are explicit in [the matrix](business-model-ui-coverage.json).

The closeout repaired backup replacement and fresh deployment snapshots, validated real Store19.11 copy read paths and focused recovery/idempotency boundaries, and deployed through the normal lifecycle at `15939b46f` (2026-10-21 00:44 Pacific). All twelve migration gates passed. Production readback: PID88119, schema19.11/53, integrity ok, original FK finding retained, failed17 unchanged, seven API checks200, one verified latest backup with no staging leftovers. Exact evidence is tracked in [closeout validation](database-migration-closeout-validation.md). No new Audit policy or fact-check tool is included; outbound tests use substitutes.

## Historical execution checkpoints

The following is the original chronological implementation record. Its “pending”, “active” and version claims describe their dated checkpoints, not current deployment status. Historical failures and evidence boundaries are preserved.


Derek authorized Subagent-Driven execution of four units, current-data copy inverse before publication, and substitute sends in tests. The work remains active.

## Confirmed ownership and implementation

The planning worktree is codex/business-model-plans-20261019, based on deployed d6a1b024 lineage. The primary development tree is unrelated and untouched. Field ownership covers1256 physical/generated fields and123 declared payload kinds, with concrete A/B and C/D mapping documents. Independent SPEC and QUALITY approved the ownership contracts after correction of typed FKs, per-kind attempt semantics, passive lease history, and old provider-Todo receipt boundaries. Conservative retain_current inventory entries do not prove business necessity.

A1 component cd3337696 and runtime activation924a05135 are implemented and independently SPEC/QUALITY approved. Selection stores candidate identity plus option key rather than a second branch body. Current readers derive the branch from the exact bound immutable candidate; original stale/review/generation checks retain their order. Current Store version19.1/Email52 requires the explicit existing publication protocol. Frozen historical declarations remain unchanged.

Parent actual-copy acceptance passed component forward/inverse/new selection/old Store/reforward, then protocol publication with new task/execution/receipt and current-data rollback. Real Codex Consumer/Audit plus actual copy UI selection and substitute execution also passed; the resulting native task and receipt survived inverse/old-code cold-open/reforward. See business-model-a1-copy-validation.md and business-model-a1-runtime-validation.md for exact evidence boundaries. Integrity checks pass; the original Runs FK finding is retained, not represented as zero. A1 is now published at b33f331d6 and independently read back: PID5137→16854, Store19.1/Email52, exact current-pair check, branch column absent, health/status/Attention/History200, all13 queue snapshots unchanged, original FK finding retained, no synthetic fixture or real test sends. Production file474185728 bytes; source had zero selections, so no material A1 size reduction is claimed.

A2 is active in a separate attached clean worktree, codex/business-model-receipts-20261019. Verified attempt bodies equal to their authoritative external receipt become references via existing external_action_key. Distinct local results, including the observed {} case, stay owned. Receipt correction must first preserve the old meaning of untouched referencing attempts. Implementation and separate SPEC/QUALITY reviews passed. Frozen focused tests34passed; real copy publication/new receipt and attempt/current-data inverse/reforward passed, including original100561 Action rows and current52allocator rows. Full receipt ledger492rows remains exact;274verified matches use273distinctowners, and273equal bodies become references. It is merged with deployed b33 lineage; latest-source native/UI/current-data inverse and actual old-code cold-open acceptance passed. A2 deployed through the quiet-wait flow at actualproduction5a0cb051c (including separately merged upstream OA changes), Store19.2/Email52, PID45391→63376. Health/status/Attention/History200, system violations0, queue failure counts unchanged, Attention4, all dispatcher running counts0. Production has274receipt references,0equal duplicates and0missing owners; original FK finding remains. Production file474238976bytes; no VACUUM claim.

Email B1 is implemented and independently SPEC/QUALITY approved in isolated codex/business-model-email-metadata-20261019, removing exactly five duplicate class metadata fields and retaining a single bounded message preview. Merged39 focused tests and parent real-copy/new writes/visible Email detail/current-fact inverse/actual A2 cold-open/reforward/full integrity pass; zero provider calls. Fresh current19.2 backup verification and upstream integration are complete; B1 deployed at actual8221466b (includes40666e521), Store19.3/Email53, PID75700; health/status/Attention/History200 and system violations0. Production previews9544 all at most1000 Unicode characters; duplicate columns absent. Earlier-A2 failure counts increased1 before this restart, explicitly recorded separately. A3 candidate execution owner is implemented/frozen in another worktree with independent SPEC/QUALITY approval; merged checks and parent full-copy/native/UI/substitute-send/current-data inverse/actual B1 cold-open/reforward/full integrity acceptance pass; A3 deployed47db00411, Store19.4/Email53, PID86147→90240, health/status/Attention/History200, violations0, failure counts unchanged and original FK finding retained. Actions+indexes grow3813376bytes from structural indexes, so no storage saving is claimed for this step. Sent A4 is implemented and independently SPEC/QUALITY approved; its parent copy/native/new Sent reference and current-data inverse/actual old-code cold-open have passed, reforward/full integrity also passed; standard A4 deploymentb09ba2eca and final subsequent-sourcecee038b13 readback passed atStore19.5/Email53,421references/0duplicates/0missingowners, systemviolations0 and failurecountsunchanged. B2a shared action core is active in a separate worktree; remaining Reply/Postfix, specialized B2 claims, task/run C and old-business D are incomplete. D must retire actual legacy producers/readers while preserving adopted history and existing provider identities/receipts; history-only cutover does not complete D. UI coverage marks only scenarios actually observed; broad route acceptance remains outstanding.

## Release source refresh

Production/origin advanced to465533b57; merged into c52516670, fresh SPEC/QUALITY approved. Fresh online source474054656 bytes had exactly unchanged schema; complete protocol/new current receipt/inverse/actual465533b57 cold-open/reforward passed, integrity ok and original FK multiset retained. Focused merged run137passed/3obsolete-fixture failures; same IDs/values converted to existing typed fixture writers, original assertions preserved, exact3cases3passed31.96s, Ruff/diff and SPEC/QUALITY approve (d2c3bccc9). No full suite or production-copy restore was used. A1 publication and live readback subsequently passed as recorded above. A2 final source-copy/native/current-data inverse and live readback subsequently passed as above. B1 production publication subsequently passed. A3 candidate/execution implementation passed independent SPEC/QUALITY, and parent full-copy/native/current-data inverse/old-code cold/reforward acceptance subsequently passed as above; A3 publication subsequently passed at47db00411 as above. The four-unit project remains incomplete.

## Current continuation checkpoint

A4 review source25b2e2daf is1208files/3a1495a0; merged acceptance source804386b12 is1208files/07995baf. Four merged smoke tests passed. Its immutable input is the accepted A3 current-data copy, including the preceding synthetic native task. The ordinary-production fixture allocator guard refused that staged source before any new CLI call; the local harness now continues above the existing all-family high-water (including input children), below the JavaScript integer bound, after proving the failed preparation changed no logical facts. No production allocator or app code was changed for this harness correction. Two actual native Consumer/Audit calls, copy UI selection, one substitute send/verify/receipt and new raw NULL Sent result reference passed. Source2450Sent rows map421exactreferences/2029retained local results, all public values/types and other stored fields exact. Identical-population duplicate body reduction467902bytes; Actions+indexes net+163840bytes. New recall update and actual archived6f53171c9 Store cold-open passed current-data inverse; reforward and full integrity passed. Publication and final live readback subsequently passed as above. Console shut down with0provider calls.

B2a core93058af1f, corrected6ad34bcd2, independently SPEC/QUALITY approved. Fresh production19.5/53 source strict old Store+Email cold, full forward/public values+types/unrelatedfacts, real Consumer/Audit/local UI/substitute1send1verify, six current direct outcomes, current-fact inverse/actual old Store+strict Email cold/reforward/full integrity all pass. Identical population Actions+indexes−4026368bytes; no VACUUM claim. Final normal upstream integration6753e9a2d and publication review pending; no B2a19.6 live claim yet. B2b specialized claim/result ownership source211eabb87 has136distinct targeted passes, actual archived19.6 current-data inverse/cold/reforward and independent SPEC then QUALITY approval; parent copy/native/integration/live remain pending. C1 is in isolated business-model-task-lifecycle, source-backed nine-kind physical Task lifecycle preflight first, then implementation; C2 Run histories, A5 Reply/Postfix and full D remain incomplete.


B2b parent acceptance at773468fe0 passed pristine19.6-working-copy→19.7 forward,
actual Consumer/Audit/UI selection/substitute send+verify+receipt, five specialized
current outcome branches, current-fact inverse, actual7e old Store+strict Email
cold open, reforward and full integrity. Same-population capacity delta0bytes.
First native exact-body fixture remains failed/preserved with0provider calls;
independent format-permitted success does not fix its existing Postfix conflict.
A5 contract/mechanism remains open. Source is accepted B2a working copy, not raw
production19.6; production preflight/live publication remain pending. C1 active;
C2 and full D remain incomplete. No real provider sends.


B2a standard deploymentcompleted actualcbf95112 (shared core unchanged from7e),
Store19.6/Email53, newPID75178. Exact current declaration/five typed codes, retired
six direct-step mirrors, HTTP200/queue-failure comparison/quickcheck/originalFK
passed. Global health is explicitlydegraded by one unresolved feedback created
04:08:53UTC before cutover, retained untouched; no zero-violation claim.
B2b is normallymerged withcbf at4fe745ec5; original27corefiles byte-exact, separate
publicationfreezeaf007868. B2b copy/native/current-facts inverse acceptance has
SPEC/QUALITY approval; fresh productionpreflight/backup/publicationpending.
C1nine-kind raw-byte/old-code inverse checks have passed during implementation;
source not frozen/reviewed/live. A5/C2/fullDremainopen.

## Historical Task/Run unification phase status (checkpoint through 2026-10-24; source observations through 2026-10-23)

The Phase 1 production values, PID, counts and health readbacks above describe their recorded 2026-10-22 inspection only. A configured-production read-only query at 2026-10-23T13:46:56Z checked weekly counts and found no active weekly Tasks or attempts. This narrow observation is not a full current production readback or a fresh production-derived release snapshot. No Phase 2 production write, activation, push or deployment occurred. The frozen 2026-10-23 read-only `source.sqlite3` snapshot confirms the specific orphan: `runs` row 190042 has `task_id=404225`, `source_type='meeting_alignment_runs'`, `legacy_key='2298'`, and `job_id=4908`; Task 404225 is absent in that snapshot. The finding tuple `('runs', 190042, 'tasks', 1)` ends with foreign-key ordinal 1, not referenced Task ID 1. This snapshot evidence does not establish current production state or authorize a data repair, synthetic Task, NULL conversion, or deletion.

| Area | Accepted local scope | Still open |
| --- | --- | --- |
| Task1 | Complete assembled Task/Run/adoption/receipt schema contract; independent SPEC/QUALITY review and later SQL artifact/Run parity repairs accepted (`progress.md:92`) | Constructor/schema activation, integrated runtime, and current-data release gates |
| Task2 / Task3 | Bounded Task owners and Run/RuntimeAttempt/adoption/receipt/converter slices are accepted through Workbench Turn/event/artifact handling (Task2D7B/J), Feedback reference conversion (Task2D8R), and native Feedback batch/claim plus lifecycle slices (Task2D8A/B); Task2D8C intake/list/count implementation is present pending independent review | Parent lifecycle/caller cutover, remaining Reply lifecycle and recovery/terminal methods, dispatcher/coordinator, constructor activation, full Run/Attempt/Task runtime integration |
| Task4 | Read-only schedule/counts runtime preparation only | Implementation and producer-backed aggregation; no zero-work conclusion from queue count alone |
| Task5 | Historical bounded evidence includes the frozen 267-second source capture and pinned c38 predecessor/operator proof; neither is current-data/full-copy acceptance | Latest full-copy/current facts and inverse, genuine full old-code cold run, native CLI/fake-effects, UI and packed-capacity gates |
| Task6 | No release or live-runtime evidence in this phase2 worktree | Release readiness, publication, deployment and post-deploy readback |

The latest integrated source for this validation line is `ce841e3826b62cddd068eb9eb36d17deb07de156`, incorporated through the normal merge recorded by `770cac667`; the earlier `6005776` source reference is stale. Accepted bounded evidence is grouped by scope: Task2D0 Reply queue/claim/input refresh (`324129193`, 11 focused Store tests); Task3C3 native execution/progress/receipt (`bafff3ee8`, 12 focused tests after Fix1); Task3D1 professional RuntimeAttempt contract/converters (`d8cc184bc`, 80 focused tests after Fix1); and later Task-family owners, adoption/receipt/converter handoffs, plus Workbench and Feedback paths accepted through Task2D7B/J, Task2D8R and Task2D8A/B. Task2D8C implements native event intake, public feedback list/by-key reads and pending counts locally; independent review remains pending. The known Email workload-key expectation failure remains unresolved, distinct from earlier Task3 fixture failures. Focused counts are individual recorded checks, not an aggregate suite run. These acceptances do not close the parent gates above.

No Phase 2 integration into `main`, push, or deployment occurred. The only production observation remains the dated read-only weekly-count query at `2026-10-23T13:46:56Z`; this status update contains no new production readback. Integrated migration/current-runtime work and Task4–6 gates remain open.

## Email historical candidate locator dedup (2026-10-24)

Read-only production preflight found 924 `email_historical_candidates` rows,
valid JSON in every row, and all 4,620 instances of the five scalar-owned
locator properties present with exact typed values. Canonical compact JSON
length falls from 323,162 to 140,255 UTF-8 bytes, a 182,907-byte logical
payload reduction. This does not measure or claim SQLite file shrinkage.

The local candidate adds an explicit Email 53→54 row transform and inverse,
compacts new candidate writes, and reconstructs the current provider-message
projection from scalar columns in list reads. It leaves differing locator
values in JSON and fails closed when a source locator key is missing, because
the inverse cannot distinguish that absence from a key removed by compaction.
Forward/inverse/reforward, mismatch preservation, unknown-field preservation,
and exact current Store/Email release selection are covered by five focused
in-memory tests. These results are local implementation evidence only: the
current-data release was not executed; no production migration, push, deployment,
or physical-size verification has occurred.
