# Business model simplification: current completion and closeout

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
