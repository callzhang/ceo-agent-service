# Errors Actions component validation, 2026-10-17

## Scope and source

This is an unselected independent component for native `errors`, not a current
31-table caller/coordinator cutover. Approved field map lines786–793 maps its
actual eight fields into typed Actions; it creates no business status or FK.
Source has two exact actual/fresh DDL spellings and the settlement/unresolved
indexes. Current readonly production metadata:15,447 rows, max ID/allocator
15,490,5,073 NULL conversation IDs and7,813 NULL message IDs. There are2
unresolved rows; total detail UTF8 bytes4,509,755. No body retirement occurs.

The preceding selected32 candidate has a separately frozen real-copy receipt
`superpowers/specs/2026-10-17-task-delivery-current-copy.json` for116→32→116→32.
This opt-in component does not retroactively extend that receipt to a new
runtime hash, selected31, Config integration or full native workflow.

## Initial implementation and fresh SPEC rejection

Author7 focused tests passed17.01s; independent SPEC7 passed16.19s. Ruff,
compile and diff checks pass. Six omitted-flag schema variants were reported
byte-identical by the author; root has not substituted that report for final
review. Initial frozen hashes:

- definitions:f3d83b832ce3e95b5d7f1b1d1746119b173099f0423f46bffe21e14f73fee461
- storage:34a0387d2f7c827ab8bcd990ec9e901a39e071aa55559fa737c3294f32c2249d
- action_storage:eb449e7ad275ea3de84fa2e6c0fc4a46edb5e9517dd934526894806e20981b0c
- tests:d87cef50431833c8eba1179f7cc8c9be778cd87939357375db5a808197636d5a

Fresh SPEC **REJECT** found two silent inverse losses. Root independently
reproduced both using the source-shaped `_db(':memory:')` fixture; no disk
copy, source DB or provider was touched:

1. An errors Action accepts `first_agent_run_id=1`, a relationship absent from
   native errors; inverse returns success and drops the reference. Error facts
   must reject every unrelated writable physical reference slot.
2. Save an unrelated external result at physical Action ID300, forward,
   then omit `id` from inverse `action_columns`: inverse returns success and
   reassigns that retained Action to1. The complete ordered physical column
   declaration must be validated before DDL, independently of supplied list.

Canonical numeric legacy IDs are already checked: invalid text/overflow are
rejected, negative/zero native IDs accepted. The root preliminary concern on
that point was investigated and retracted. Only the two confirmed defects
were returned to the author for test-first correction. No next component or
QUALITY approval is inferred until SPEC re-review passes. Current constructors,
callers, coordinator and production remain unchanged.


## Test-first correction awaiting independent re-review

Both confirmed findings now have RED→GREEN regressions. Author focused suite
passes13/29.74s, with Ruff/compile/diff clean. The opt-in error declaration
requires all unrelated writable physical FK slots NULL for actual/fresh
profiles. Inverse independently derives complete ordered writable columns
from the already validated source Actions table declaration, rejects missing,
extra, duplicated and reordered metadata before DDL, and accepts valid JSON
transport. Root independently repeats both original in-memory probes: the
foreign reference now raises IntegrityError; missing-id metadata raises
ValueError before schema change and retains the unrelated physical ID300.

Corrected frozen hashes:

- definitions:7b164b72f269a366118391114b1f50b102b4db1c614ef6f0324a9087ad454931
- storage:ca44fb159c2dc25917966d7be52fb49f5570b21c8dc390ad5953f4609533c66b
- action_storage:45367118a44e90afbc4b45af1dde0cd1f4e6b3f8587200e2ea2c4f61447cfd9f
- tests:bf8c424be332a7a22bd78673abf19338278159c346a5d9edc4a68399decc263c

Fresh SPEC re-review is pending; QUALITY has not started. No caller,
constructor or paired release selection occurred.


## Corrected SPEC re-review: APPROVE

Independent SPEC passes13/30.60s at the four corrected frozen hashes.
Actual/fresh schema inventories find18/19 non-base writable columns and no
missing NULL guard. Independent reference and missing-id metadata probes
reject before loss; retained physical ID300 stays300, schema unchanged.
No edits or external activity; managed test root removed. QUALITY review is
now running. This remains an unselected storage component with no target
write DAO, current31 selection, complete Config copy or caller proof.


## Next caller-cutover inventory (not implemented)

Root case-insensitive literal SQL search finds31 source-table sites:27 in
24 Store methods,2 in Audit Attention's `_queue_attention_rows`,2 in Quality's
`_check_recent_errors`. Store sites include input compaction/settlement and
recovery joins, not just record/list/resolve. Multirow resolve/reconcile SQL
must remain caller-owned single statements with existing row-count, timestamp
and transaction behavior. This is a literal-SQL inventory, not complete
constructor/manifests/selector acceptance. Fresh/current construction, retired
names, all schema selector flags and paired apply/inverse must switch with
all callers, then a nonempty copy proof is required. No such switch is made
by this component.


## Fresh independent QUALITY: APPROVE

QUALITY passes13/29.05s at the four corrected frozen hashes; no findings.
Independent in-memory controls reject an external dependent view, invalid
negative sequence and target-name collision before schema change. Current-fact
inverse retains a newly written negative public ID-9, native water12 and an
unrelated Action physical ID300. RAM reconstruction independently matches
pre-component action_storage SHA685f17dd... and ten omitted-flag schema object
lists byte-for-byte (28/41/42/53/69/72/101/104/169/171 objects). Four opt-in
variants change only the Actions table and add two indexes plus one sequence
trigger. Ruff, in-memory compile and scoped diff checks pass. Managed test
artifacts removed; no production/source DB/provider/native CLI/network access.

The component is now independently SPEC/QUALITY accepted. Its surrounding
fixture uses genuine current Actions DDL with simplified Tasks/Runs/Business
and Config surroundings, so this is not complete current31 selection or
paired-copy acceptance. No target-write DAO/caller/publication claim is made.


## Caller cutover RED: real Config selector integration gap

Fresh Store construction with real Config declarations rejects source capture:
shared `_configs_action_triggers` validates Actions without forwarding the
existing TaskDelivery selection. The Errors component source is already the
post-TaskDelivery32 declaration. Empty/simplified Config surroundings in its
bounded fixture did not exercise this branch. Previous component approval
remains limited to that fixture; it is not full Config integration acceptance.

The caller owner may narrowly forward optional `include_task_delivery=False`
through the existing shared helper and opt in explicitly at Errors source
capture. Omitted-flag semantics and exact ordered Config declaration checks
must remain unchanged. This is a selector propagation correction, with a
permanent nonempty Config/current constructor regression; no permissive schema
fallback or additional policy is authorized. Caller unit remains in progress.


## In-progress caller evidence and unresolved fixture boundaries

Caller owner reports component13 PASS; current Error runtime/constructors3
PASS; nonempty Quality3 PASS after correcting physical-errors existence lookup
to Actions; targeted Audit/Store checks5 PASS; prior TaskDelivery paired2 PASS;
new actual/fresh Error current-write/inverse/reforward2 PASS and additional
Audit/scheduled/fresh-constructor3 PASS (some selections overlap; no summed
test count claimed). Root literal SQL scan finds0 remaining of the31 earlier
native Errors query sites; this is static closure, not full public parity.

`test_store.py -k error` originally reports24 PASS/10 FAIL. Two Errors fixtures
were adapted to typed storage and their targeted reruns pass. The other8
failures occur before entering changed Error code: five parameterizations of
`test_open_connection_names_and_reraises_each_pragma_io_error_once` plus
`test_open_connection_preserves_setup_error_when_close_also_fails` stop in
unchanged `_open_connection -> register_task8_functions` at mock
`create_function`; two scheduled recovery fixture cases stop in unchanged
`intern_scheduled_config -> configs_storage._lock` without a caller transaction.
This is a trace/unchanged-code boundary, NOT an independently executed archived
baseline PASS. No runtime fallback or weakened assertion was introduced.
Older full Email paired fixture cases also stop before Error capture on missing
TaskDelivery source declarations; those historical hybrid fixtures were not
relaxed. Whole-project acceptance still needs these fixture boundaries resolved.

The complete24-method source-vs-target public differential and root31 real
copy remain pending. Fresh caller SPEC/QUALITY has not yet begun. Production
continues unmodified; candidate work is isolated and not released.


## Frozen caller SPEC: numeric lookup performance regression

The caller freeze is bound by
`superpowers/specs/2026-10-17-error-actions-caller-freeze.json` (20paths).
A root in-memory EXPLAIN probe finds default Error ID lookup only searches
`idx_actions_postfix_identity(record_type=?)`; descending recent lists also
use a temporary sort. Fresh SPEC independently confirms this is observable
scalability regression with3,000 additional source-shaped Errors: native
point lookup<100 SQLite VM steps versus target~18,100; native ordered range
~15,000 versus target~120,600 plus temporary B-tree; native recent-limit20
~200 versus target~195,200 plus temporary B-tree. All20 freeze hashes match.

Default Error projection supplies only `record_type='errors'`, insufficient
for SQLite to qualify the existing partial public numeric ID index whose
predicate includes the shared numeric scope. Attention's separate unresolved
projection is already budget-tested but does not fix these public APIs.
SPEC is preliminarily REJECT pending complete review and a narrow projection
qualifier correction/regression, preferably reusing the current index without
DDL or another index. Root has not edited app/tests, no actual copy executed.


Fresh caller SPEC final disposition is **REJECT** for this sole confirmed
numeric access regression. Its27 focused cases pass198.78s; in-memory added
existing numeric-scope predicate restores point/range index searches and
ordered newest scans. The author is correcting the shared projection qualifier
with new test-first access-plan/VM-budget regressions, without index/DDL changes.
Full24-method differential, caller QUALITY and real31 copy remain separate.


## R2 caller SPEC re-review: APPROVE

The narrow shared numeric-scope correction passes independent SPEC19/79.65s
at all21 r2 manifest hashes. Shared postfix tuple matches its original three
values and numeric predicate is byte-exact to the existing public ID index.
No DDL/index change. On3,001 source-shaped native/typed rows, independent full
SELECT* outputs match: point~100 VM steps with expression lookup; range1,509
rows~87,600 steps proportional to returned rows; newest20~1,200 steps with no
sort. ID-only projection measurements are<100/~9,100/~100. Unresolved Attention
keeps its separate explicit partial index. Caller QUALITY is now pending;
no31 actual copy or all24 native public parity claim.

## Exact native public-oracle reference for follow-up

Root readonly git/AST comparison of24 native Error-related Store methods in
existing test archive2773d6f4 against actual production predecessor
8a3262b921d370d8d9cd90fab33f026ddafc3345 finds22 equal and two different:
`reconcile_done_reply_tasks_with_failed_current_run` and
`_operation_logs_base_query`. Both complete method sets exist. Commit8a3262b9
is available in the development object database. The follow-up public parity
proof must bind the genuine8a native methods instead of calling old2773 an
unchanged current-production oracle. No original database or source files were
modified; only code AST fingerprints were compared, no code archive retained.

## R2 caller independent QUALITY: APPROVE

All21 freeze-r2 hashes matched before/after review. Independent two-row native/typed bulk update preserved OLD RHS values, rowcount2 and savepoint rollback. Focused component/runtime/Attention/paired actual/fresh inverse/reforward/constructor cases23 passed166.72s; two nonempty Quality/Attention cases passed17.42s. Existing public numeric ID index SQL and shared predicate remain byte-exact; no DDL addition. Managed review root removed. Approval covers frozen current31 caller cutover only. Complete24-method native differential and actual116→31→116→31 proof remain open. Root starts actual-copy proof only after both independent reviews approved.

## Real current production-copy proof: PASS

Receipt `superpowers/specs/2026-10-17-error-actions-current-copy.json`, SHA211c47fd929219d3a7500c5114f7b3c813513227f49fa6084c1064fcf2049787. Actual production predecessor883c7c9f0798b962ad6675eaf00cb2dab0c78114, Store13.5/Email51.609.571s116→31→116→31, all4 TaskDelivery plus Errors field values/types/waters preserved. New current Error15491 recorded/resolved, genuine predecessor Error15492 recorded/resolved; old internal taskMemory615 ends skipped without provider. Complete source schema restored, current/predecessor facts survive reforward, original FK diagnostic(runs190042→tasks) unchanged, quick_check ok, no virtual/shadow objects. Runtime hashes unchanged. Managedroot97ca37n2 removed. Scope is full source schema and five complete families, NOT all116 original rowsets or24-method parity. No source write/native role/provider/send.

Allocation exposes an unresolved size regression across the full current candidate: source500195328B (active500178944B), first target564932608B (zero freelist), final after proof writes565051392B. This is not a space-reclamation result. A new one-forward-only managed copy will capture per-object/table dbstat page and internal unused bytes to locate growth; no vacuum or retention change.

## Current31 allocation diagnosis: measured, not optimized

Separate managed readonly-copy forward239.194s, receipt `superpowers/specs/2026-10-17-current31-allocation.json`, SHA a1544d5861a2d5cdd9b13b0fe5f802b47dc953d59c1a2dcc079dfdc32df8888d. All21 reviewed hashes unchanged; root removed. Source active object bytes500211712, target564969472 (+64757760). These are a fresh snapshot distinct from the rollback proof.

Common-name deltas alone overstate growth because native families move into cores. Grouping only retired source names with a single approved CSV target plus the existing core gives:

| Core | Source family page bytes | Target page bytes | Net delta |
|---|---:|---:|---:|
| actions |177790976|207241216|29450240|
| tasks |58142720|71852032|13709312|
| provider_state |25034752|34140160|9105408|
| state |4841472|9273344|4431872|
| runs |130850816|134828032|3977216|
| business |10014720|11534336|1519616|
| configs |6950912|8335360|1384448|

This grouping is diagnostic, not an exhaustive all-family reconciliation: newer addendum kinds `email_direct_retry_requests`/`email_unsubscribe_cancellations` are absent from original CSV, search/retired families and schedules are outside above groups. Full object metadata is in receipt. Actions data pages155557888→175251456 and indexes22233088→31989760; Tasks data50216960→60977152 and indexes7925760→10874880. Provider data13352960→21360640 and indexes11681792→12779520. Fewer physical objects alone does not establish fewer page bytes. Root source inspection confirms Provider payload repeats four existing field names per observation, while Tasks SQL builders materialize defaults into keyed payloads. These are concrete encoding candidates, not yet quantified causal byte decompositions or approved changes.

Source B-tree internal unused bytes65425577 versus target73539287; this is not freelist or a guaranteed vacuum saving. No vacuum/checkpoint, row deletion, retention or runtime correction performed. Allocation regression remains an explicit final acceptance issue; ten-core closure alone must not be called space optimization.

Root readonly Provider encoding arithmetic over72238 native observations finds four existing payload fields(provider_folder_name/category_key/important/important_signals_json) would encode6981907B total, of which only1202867B are encoded scalar values and5779040B are repeated property names/object punctuation (exact80B per row). Actual Python SQLite value counts are181058 NULL/72688str/35206int. No values or source bodies output/persisted. This isolates real representational overhead, not a measured physical saving; a future scoped correction can consider the four existing fields as native writable columns or another equivalent compact encoding while preserving all actual/fresh NULL/types/constraints/inverse/caller semantics. No new business fields or implementation selected here.

## Bounded Provider physical-column experiment (not implementation)

Root read-only source snapshot72241 observations/55 generations, two in-memory DBs only,2.026s. Existing Provider schema/codec versus a prototype with the same four existing fields writable and no payload object, retaining the same three index SQL strings. All9 observed field values and SQLite typeof values match; both quick_check ok. Existing8346×4096=34185216B versus prototype6632×4096=27164672B (7020544B lower, including8192B schema difference). All three index object sizes are exactlyequal; data table21331968→14319616B. No source values persisted, production writes, runtime edits or sends. This is a controlled row-layout experiment; complete actual/fresh declarations, negative constraints, generation typed readback, callers, migration/inverse and full-database size still require implementation and independent validation. It justifies investigating a scoped four-existing-field layout correction without new business fields or retention changes.

## New24-method native differential verification: first SPEC REJECT

Author frozen newtest SHA54f5e7c524146bb339e0b6ef526c6e2abc345148de0a7dcfc312b3a1d169f7f1 passes15/102.17s; fresh SPEC15/100.90s independently confirms all24 exact883 ASTs equal8a (aggregate9b416a9aab666987e2b42555a4fe08aee4fa41365e215cc76eb91c51e56e86eb) and substantial actual/fresh Errors-only parent-seam plus three genuine old full-runtime/fresh settlement cases. However complete meaningful24-method claim is REJECTED: list_errors_after called only before new13/14 so bothcursorcases empty; compaction occurs indirectly without input body/compacted/provenance readback, so no-op compactor wouldpass. Author correcting ONLYnewtest/ownclaim, app frozen. History remains Errors-source-only, not fullunion. Root earlier identified unexpected twin-exception masking; author strictsuccessful-return helper regression already corrects that before this freeze. No evidence of runtime mismatch from these coverage gaps. Completeparity and freshQUALITY remain open.

R2 newtest SHA228e11be8f15d3320931afbcb79c6bb20cf031c14c0908ddc3111e7568dd56c2: author15pass98.07s, Ruff/compile/diff clean. Positive range now exact IDs13/14 and14; genuine883 settlement pre/posttask+input snapshots assert cleared nonempty original fields, input/task compacted flags, exact SHA256/byte provenance, unchanged input IDs/pointers and no-opbefore/before negative control. Root read confirms these assertions exist; independent SPEC re-review running, QUALITY not dispatched yet. No runtime change.

Fresh SPEC r2 APPROVE: independent15pass99.62s; frozen newtest228e11be and all21callerhashes stable. Positiveorderedrange and actual observablecompaction/provenance/pointers/no-opnegative witnesses close both rejectedgaps. Exactold883fullruntime remains the scalarTasks settlementoracle, actual/freshErrors-only typedparent seam coversothermethods, strictnominalsuccess andno xfail/skip. History remainsErrors-source-only. Fresh independentQUALITY nowrunning; no nextimplementation started.

## R2 native parity independent QUALITY: APPROVE

Fresh independentQUALITY15pass99.02s, Ruffclean. Frozen newtest228e11be and all21callerhashes matchbefore/after. No blockingfalse-positivewithinbounded24Error seam; genuineoldsettlement/inputcompaction and strictsuccessfulreturns/range/types/waters/rollback validated. Minor nonblockingportabilitynote: archivedhelper invokes python fromPATH ratherthanpytest sys.executable. No fullHistoryunion/whole-service/final11/deploymentclaim. Component/callerSPEC+QUALITY, completebounded24-methoddifferential and actual116→31→116→31 proof nowclose theErrorsunit. FurtherProvider layoutcorrection startsseparately; priorfreeze/receipts remainhistoricalevidence, not renamed for changedcandidate.
