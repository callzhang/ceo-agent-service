# Phase4C scheduled / Workbench acceptance

Status: core candidate is in the isolated worktree, with independent SPEC/QUALITY and coordinated outer runtime changes still pending. This is not a production C release.

## Actual source and exact field map

Source Git: `44a1a4e39fc24765c92d0e51c7f03d578b9d75de`; Store `2026-10-11.3`; Email `51`. Source metadata was captured read-only after actual B deployment. Counts are from this one snapshot, not a cross-snapshot deletion oracle.

| Source table | Captured rows | Target |
|---|---:|---|
| `scheduled_task_runs` | 156245 | `runs`, `record_type=scheduled_task_runs` |
| `workbench_tasks` | 20 | `tasks`, `record_type=workbench_tasks` |
| `workbench_turns` | 37 | `tasks`, `record_type=workbench_turns` |
| `workbench_events` | 810 | `runs`, `record_type=workbench_events` |

The following map covers all49 actual source fields (17+7+19+6). Shared physical integer `id` is allocated independently; public source IDs retain exact legacy values. Workbench TEXT keys are not cast to integers.

| Source | Exact target or retirement |
|---|---|
| `scheduled_task_runs.id` | `runs.legacy_key`; `runs.id` is the physical allocator identity |
| `scheduled_task_runs.event_id` | `runs.event_id` |
| `scheduled_task_runs.scheduled_task_id` | `runs.scheduled_task_id` |
| `scheduled_task_runs.trigger_kind` | `runs.trigger_kind` |
| `scheduled_task_runs.scheduled_for` | `runs.scheduled_for` |
| `scheduled_task_runs.dispatch_status` | `runs.dispatch_status` |
| `scheduled_task_runs.skip_or_error_reason` | `runs.skip_or_error_reason` |
| `scheduled_task_runs.execution_kind` | `runs.execution_kind` |
| `scheduled_task_runs.execution_id` | `runs.execution_id` |
| `scheduled_task_runs.lease_owner` | `runs.lease_owner` |
| `scheduled_task_runs.lease_expires_at` | `runs.lease_expires_at` |
| `scheduled_task_runs.created_at` | `runs.created_at` |
| `scheduled_task_runs.dispatched_at` | `runs.dispatched_at` |
| `scheduled_task_runs.first_scheduled_for` | `runs.first_scheduled_for` |
| `scheduled_task_runs.occurrence_count` | `runs.occurrence_count` |
| `scheduled_task_runs.result_summary` | `runs.result_summary` |
| `scheduled_task_runs.snapshot_id` | `runs.snapshot_id` |
| `workbench_tasks.id` | `tasks.legacy_key`; `tasks.id` is the physical allocator identity |
| `workbench_tasks.title` | `tasks.title` |
| `workbench_tasks.runtime_kind` | `tasks.runtime_kind` |
| `workbench_tasks.provider_session_ref` | `tasks.provider_session_ref` |
| `workbench_tasks.archived_at` | `tasks.archived_at` |
| `workbench_tasks.created_at` | `tasks.created_at` |
| `workbench_tasks.updated_at` | `tasks.updated_at` |
| `workbench_turns.id` | `tasks.legacy_key`; `tasks.id` is the physical allocator identity |
| `workbench_turns.task_id` | `tasks.parent_id` → physical Workbench task; outward projection retains original TEXT task ID |
| `workbench_turns.client_request_id` | `tasks.client_request_id` |
| `workbench_turns.task_sequence` | `tasks.task_sequence` |
| `workbench_turns.user_text` | `tasks.user_text` |
| `workbench_turns.status` | `tasks.status` |
| `workbench_turns.stop_requested` | `tasks.stop_requested` |
| `workbench_turns.final_text` | Remove physical empty cache; native/RAM presentation contract must pass outer validation |
| `workbench_turns.error_code` | `tasks.error_code` |
| `workbench_turns.error_detail` | `tasks.error_detail` |
| `workbench_turns.resume_context` | `tasks.resume_context` |
| `workbench_turns.lease_owner` | `tasks.lease_owner` |
| `workbench_turns.lease_expires_at` | `tasks.lease_expires_at` |
| `workbench_turns.execution_run_id` | `tasks.execution_run_id` |
| `workbench_turns.runtime_quiesced_run_id` | `tasks.runtime_quiesced_run_id` |
| `workbench_turns.started_at` | `tasks.started_at` |
| `workbench_turns.completed_at` | `tasks.completed_at` |
| `workbench_turns.created_at` | `tasks.created_at` |
| `workbench_turns.updated_at` | `tasks.updated_at` |
| `workbench_events.id` | `runs.legacy_key`; `runs.id` is the physical allocator identity |
| `workbench_events.turn_id` | `runs.task_id` → physical Workbench turn; outward projection retains original TEXT turn ID |
| `workbench_events.sequence` | `runs.sequence` |
| `workbench_events.event_type` | `runs.event_type` |
| `workbench_events.payload_json` | `runs.payload_json` |
| `workbench_events.created_at` | `runs.created_at` |

## Removal and preservation boundary

Four old physical source tables are replaced by typed rows; there are no compatibility views or dual writes. Only the empty `workbench_turns.final_text` and search caches `summary_text`, `fts_text`, `embedding_json`, `embedding_model`, `embedding_updated_at` are retired. Nonempty retired-cache values must refuse migration before mutation.

Search metadata retains the existing seven columns `id`, `session_id`, `source_type`, `source_id`, `title`, `created_at`, `updated_at`. Its external-content FTS remains readable. Native ranking is outside this structural change; metadata-to-runs migration and complete FTS retirement remain Phase8.

Workbench attachments, artifacts and confirmations remain physical child tables. Their existing NO ACTION semantics and original results/bytes must survive physical parent remapping and inverse. Scheduled configuration tables remain in place; `snapshot_id` and `scheduled_task_id` retain actual existing FKs. `execution_id` remains the actual public text reference, including service commands.

Source allows nullable Workbench TEXT PKs, raw BLOB values, and nonpositive event sequence/occurrence values where no CHECK exists. The real `task_sequence>0`, status, stop flag, natural/partial uniqueness and required-value constraints must survive; no speculative checks or fields are added.

## Verification gates

Core RED confirmed old schema publication and numeric normalization failures. Core focused11-file GREEN reported195 passed in5.00s; independent review and any final inverse fixes remain pending. No full local serial suite is run against the live service.

Final C acceptance requires coordinated outer readers/writers, exact source SQLite backup with full retained values/children/receipt comparison, baseline inverse with actual DDL/rowid/FTS state, replacement facts plus deleted allocation watermarks, actual old-code constructor/claim/recovery, re-forward, publication failure stages, remote Quality, normal deploy and live readback. All323 app Git files from deployed B44a1a4e were extracted into the C task temporary directory with per-file SHA256 for that old-code exercise; no environment or native trajectory copies were made.

Independent SPEC reviewed49 fields and remaining core constraints/inverse/FTS behavior, rerunning78 initial and189 expanded focused checks. It found two P2 source-boundary gaps: markerless C source tables were omitted from bootstrap refusal inventory, and additional source13 tasks/runs objects could be silently retired. Both require refusal-before-mutation regressions and re-review; current-schema repair policy is not broadened. Core SPEC remains open.

Both P2 repairs completed with actual8 failed/4 passed RED and12-case GREEN; writer full11-file207 passed. Independent re-SPEC reproduced both original refusals with unchanged full fingerprints, checked supported current missing-index repair and allocation retention, and independently ran201 focused checks in6.41s. Scoped core SPEC passed; independent QUALITY is pending. No C production release or complete outer acceptance is claimed.

Independent QUALITY passed the frozen core with no P1/P2. It directly read seven runtime files and the source/core/release/manifest fixtures, matched all nine source declarations to independent metadata, ran141 focused checks in5.14s, and passed scoped Ruff/diff. A separate in-memory stale multilingual/repeated-token FTS exercise verified old title/summary/body terms are removed after title update/inverse, new title is searchable and FTS integrity-check passes. No providers, production constructors or new backups were used. This completes core reviews only; outer acceptance and actual copy/publication remain pending.

Concurrent production source advanced to6b43a93b7d5e749f338c13452be5c9e5ceee615b (Skill/Task guidance/schema deduplication PR54/55/60), Store13/Email51 unchanged. Read-only exact C source preflight passed on actual production; one snapshot counts scheduled156422/WBtasks20/turns37/events810. No source constructor or production mutation. DB core/storage/release files are absent from the25-file main delta. Root fetched without merging over active outer edits; final integration and affected tests remain required. Actual old-code evidence is now323 verified6b Git app files; obsolete44 temporary code was removed after verification/no handles (10,298,859 B). Final copy/old-code exercise must use actual current production, not the earlier44 metadata snapshot by assumption.

Scheduled/outer unit is frozen pending review closure: writer242 primary/78 maintenance+quiet/56 History+publication/71 strengthened targeted passes (overlapping). Independent SPEC184 primary/21 quiet passed and confirmed17source fields,2000VM budget/index/cast,public417 vsphysical identity. It found oneP2: false source13 marker plus retired empty scheduled table could hide a busy typed run; must refuse mixed actual layouts. It also found three old fixture cases in History/runtime rename; in-memory typed substitutions preserved their original business oracles, but real repository fixtures still require updates. Four supplemental maintenance permission-only ps cases independently passed in the permitted test environment. Re-SPEC and QUALITY remain pending; no C publication.

Scheduled P2 repair:8-case RED, actualsource12/13 tuple/Row fullstate+bytes/read-only lease regressions and mixed empty/nonempty schema rejection pass; writer254 primary and51 completequiet/History/rename. Independent re-SPEC replayed original hidden-lease case and verified rejection, matchedsourcecolumns,23 focused passes5.13s. All three original business test functions AST-identical; only sourcefixture SQL translated. Scheduled SPEC now passes; fresh QUALITY pending.

Scheduled independent QUALITY passed:227 scoped checks14.02s plus14 repositoryquiet/busy checks1.95s, Ruff/diff clean. Only expected runtime AST changes and all comments retained; History/rename business functions remain identical. Additional in-memory config deduplication preserves public417/418/419 and all other facts, corrupt JSON, empty FK findings and repeated idempotence. No P1/P2, providers, production constructors or new backups. This closes scheduled SPEC/QUALITY only; next WB callers and completeC acceptance remain pending.

Main integration completed as4acb03b3 after scheduled commitd5ccd129/core8ce4645e. Only append-only claims conflicted; both complete sets retained. Main affected Skill/Task/Email/router checks plus core/scheduled checks passed512 tests11.36s; full app/tests Ruff and stageddiff checks passed. Main runtime deduplication behavior preserved. Workbench caller unit now runs separately; C remains unpublished.

WB representativeRED6 retiredtable creation/native/index cases; first boundedrun55 passed/30 failed includesretiredSQLfixtures and legitimate old noncore-columnrepair expectations. Root directly checkedold6b attachment add-column path4590-4614 and test159-191. Explicit C decision within approved retirement scope: no pre-.7/.8/.9 missing-column compatibility; retain exactsource13 migration and currentmissingindex/trigger repair, reject missingrequireddeclarations nonmutating. Replacementtests must preserve actualsource values/usertext/privatecontext/sequence/turncontrols/attachmentmetadata+bytes, plus genuineconcurrentcurrentindexrepair and separatemissing-columnrefusal. WB implementation/testing ongoing; no claimofCcomplete.

WB caller unit frozen:345 focused checks21.16s, final2 confirmationassertions1.33s, scoped Ruff/diff clean. Initial6 retiredtable RED; indexed physicalparent fix for50k unrelatedevents retains20kVMbudget (RED6000callbacks×100;GREEN0callbacks at100interval, not0actualinstructions). Newpublicevent7/physicalrun1 with unrelatedproviderattempt7 preserves toolupdate/pagination/nativecrosspagepair/typeisolation. source13preservation fixtures and current/source incompleteWAL-based nonmutation pass; constructorpreservesPENDING then explicitexistingrecovery preservesonce/control/terminaloracles. OriginalWeChat2spawn/barrier/publicreply7/FK/requeue/channels/6bytes/metadata retained withcurrentmissingindexrepair. APIrenderfixtures preservefailed/stopped/statusmetadata/stringoracles viaexistingDTOhydration seam, noDBfinalbody. WB independentSPEC thenQUALITY pending; finalactualcopy/oldcode/CI/publication stillpending.

WB independent SPEC passed:342 focused checks21.65s, directruntime/typedhelper/testoracle inspection, separatelyinstrumented50k-event originalbudget0callbacks(at100interval),diffclean. Public/physical IDs, childDTO/FKs, stop/CAS/races/nativebounds/realhydration, source13pendingfact preservation thenexplicitrecovery, concurrentcurrentindexrepair allwithinapprovedscope. Noedits/providers/prodconstructor/backups. FreshQUALITYpending. Root residualapp/scripts directphysicalSQL sweep foundonlysupportedhistorical12/13scheduled sourcebranch; six currentfixture files remainplannednextunit.

WB QUALITY passed independently:342 focused checks22.94s, scoped Ruff/diff clean;50k-event disposableprobe returns2selectedevents/0callbacks at100interval, not0instructions. Separate publicTEXT01 and1 probes preserve distinct tasks/turns/timeline parents. Runtime transaction/CAS/stop/control/childlocking/native/RAM/nofinalDBbody all reviewed. Broader288passed plusknown untouched testStore635 oldfixture failure remainsnextunit; no P1/P2 inWBcallerunit. Independentprobe/testtemps absent, noproviders/prodconstructor/sourcecopies. WB dualreviewclosed; finalfixture sweep andfullCacceptance pending.

Residual sixfile fixtures translated withoriginalfunctions/decorators/businessoracles; initial31failed4passed→893passed1confirmedruntimequeryfail39.28s. Native/cache physicalabsence addsasserts, actualsource13lease fixtureunchanged. Remaining Attention stale scheduled ORlocator returnscorrectrow but55,100VM vs original2,000budget on5k equivalentfacts; rootreadactualaudit_web _queue_attention_rows query authorized samewriter scopedlocatorfix usingexistingindexes, no schema/core/policy/budgetchange. Independent actualsourceacceptance read-onlycaptured production6b/Store13Email51:all source objects/columns/FKs match source13metadata,156672scheduled/20WBtasks/37turns/810events/25119tasks/59337runs atoneconsistent snapshot. Source integrity_checkok,141table fullfingerprints+rawtypes/rowids/schema/state/seq,323old6bGitblobs verified; knownsemanticFKunchanged. SnapshotSHA256c64d336366e0502f879ca868319a4a64a6a4f899085946dbeed4ef7e155c85c3. Candidateconstructor/migrationstages paused untilnewruntimefreeze. Activeprivateacceptance copiesaretemporaryevidence, notretainedruntimebackups.

Residual+scopedlocator unit frozen:899 sixfilechecks36.63s, validoriginalSQL RED1failed14passed,32locator/attention/audit GREEN. RuntimeAST only_queue_attention_rows changed: indexedUNION ALL event/publicIDcandidateprobes usingexistingidx_scheduled_task_runs_event/idx_runs_public_id thenunchanged recoveryEXISTS. Exactcanonical identity/type/publicvsphysical andfullerrors/tasks/runs nonmutation covered; no newindex/schema/helper/policy/budget. Writerobserved counted200VMunits vs55,100 (measurementgranularity pending independentcheck); original2kbudget passes. Source13lease helperbyteidentical. FreshSPECthenQUALITYpending. ActualCsource reservation corrected fromlivecapture: retainedMeeting physicalrun190042/public2298/job4908 refersabsentphysicaltask404225; taskssequence404227 protectsit. Earlier422061 wasBprivateexercise, notCcurrentproductionassertion.

Residual independent SPECpassed899tests40.82s/Ruff/diff, only_queue_attention_rows runtimeAST changed; source13lease byteexact. SameDB original/candidate5k facts return1roweach; sampled100-opinterval551callbacks55,100countedvs2callbacks200counted, notexacttotalVM. Eight extra contradictorycrossschedule/canonical/noncanonical/overlap/nullmessage comparisons equaloutputs/fullstate. Existingevent/public/latestindexes verified, budgets unchanged. FreshQUALITYpending;actualsourceacceptance stillsource-onlywaiting newreviewedfreeze.

Residual QUALITY passed899tests39.21s/Ruff/diff. Independentlycompiled HEADoriginal/candidateonefunction andexecutedsame5kfacts fulloutputs equal;sampled551/2callbacks at100interval,existingevent/public/latestindexes. Eight independentcontradictory/null/noncanonical/overlap cases equalfulloutputs/expected andall errors/tasks/runs unchanged. Testnames/decorators retainedasideintendedidentitymatrix,source13leasebyteexact. No P1/P2,repoedits/providers/prod/network/deploy,newbackups;probeautocleaned. ThisclosesallCimplementationunitreviews;actualsourceconstructor/inverse/newfacts/oldcode/re-forward/publicationfailures/fullCI/live stillpending.


## Complete frozen-copy acceptance (2026-10-12; before latest-source refresh)

Source production commit `6b43a93b7d5e749f338c13452be5c9e5ceee615b`, Store `2026-10-11.3`, Email51. Consistent source snapshot at15:22:40Z has SHA256 `c64d336366e0502f879ca868319a4a64a6a4f899085946dbeed4ef7e155c85c3`. Candidate `c9f3a8f7600127f2de3df5f2369c6c3c1891acbd`; tracked app aggregate SHA256 `d3fe6b71d1fbce40fed4f7187f004abed43ac6f1737c00e16af96e9c038fa96a`. All four implementation units have independent SPEC and QUALITY approval.

Normal constructor forward retained48 of49 source fields; the sole retired field was verified empty final_text. Source counts at this snapshot: scheduled156672, Workbench tasks20/turns37/events810, original typed tasks25119/runs59337. Direct raw-value/type/rowid comparison preserved all original domain facts, original physical core IDs,120 unaffected tables, configurations, settings, receipts, business rows and child results/bytes. The existing semantic orphan remains physical run190042/public meeting2298/job4908 pointing to absent physical task404225; no ghost parent is created. Repeated current constructors were idempotent.

Baseline inverse restored source declarations, values/types, application/FTS-shadow rowids, schema markers/timestamps/headers and allocator watermarks. SQLite-internal sqlite_sequence rowids alone changed for five entries; all50 typed(name,seq) allocator pairs were exact. The comparison uses this real allocator contract and remains strict for all application rowids. Source file SHA256 was unchanged.

Replacement facts covered two Workbench tasks/four turns, six paginated synthetic native events, attachment/artifact bytes and confirmation results, global request identity, lease/stop/recovery, scheduled command TEXT identity/overlap/frozen configuration, raw BLOB/NULL keys, nonpositive sequence/occurrence where permitted, receipts/business/settings and deleted allocation watermarks(task404295/run347817). Inverse preserved those facts; actual old6b code reopened without constructor mutation, claimed/completed work and allocated above both deleted watermarks. All323 old app Git blobs were verified; a separate fresh import probe verified all53 loaded app modules originated from that exact tree. Re-forward preserved retained full sets, core IDs, receipts and children. No production/provider/external-action effects. Synthetic native text was only task-owned temporary test input.

Five offline real RepositoryUpdater failure stages(dependencies, verification, restart, health, loaded verification) exercised real paired inverse and returned temporary Git to old code. Failures after publication preserved new Workbench and service_state facts. Stop/start/health were offline callbacks, not actual launchd failures; runtime backup helpers were not invoked.

Additional real-copy guard checks rejected nonempty values in all six retired caches with identical file bytes; source NOT NULL constraints rejected NULL. Current missing-index/trigger repair restored exact declarations/allocators/FKs, but the established initializer also backfilled five reply_attempts.codex_session_id values and updated the schema-marker timestamp. This behavior exists in old6b; it is recorded separately, not treated as a new C migration defect or claimed to preserve every repair-path business byte.

Latest main and production advanced to8dcb1c7d(Task native Skill catalog). It was merged safely asf2b88a85;124 affected Skill/Task and Workbench release/caller checks passed4.86s. Draft PR61 is created. Storage/release source changes are absent from this13-file main delta. The6b copy evidence above remains explicitly tied to6b; actual latest-source/old8d binding, complete remote Quality, normal deploy and live readback remain release prerequisites.
