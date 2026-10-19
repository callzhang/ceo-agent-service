# Consolidate the three remaining Runs source families

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development. Complete canonical units and source-backed regressions precede application; independent SPEC then QUALITY precede and follow selection.

**Goal:** Complete the approved Runs portion of the ten-core-table design by folding `codex_session_search_index`, `email_classifier_runtime_samples` and `setup_wizard_events` into the existing `runs`, retiring independent search FTS and shadows, preserving all existing source facts and public behavior. One rebuild retires three ordinary tables:54→51, still intermediate.

**Architecture:** Current Runs has eight existing kinds and62 stored columns. A separately exact current Runs11 factory extends that source with only genuine fields from the three source families; historical `run_storage`, `configs_run_schema` and Workbench source factories retain their native release roles. Seven metadata facts remain a distinct typed Runs record because700 source rows have only698 distinct source pointers; they cannot be replaced by all752 meeting sessions or by one field set on each meeting run. New current source-specific writers/projections preserve original IDs/defaults/affinities/constraints; original eight runtime callers retain native statements and physical IDs. Caller owns transaction; inverse reads current facts and current counters.

**Tech Stack:** Pinned Python3.13/SQLite3.50.4, existing typed Runs/Tasks counter and native-scalar precedents, existing Store/Email/Workbench callers and paired unpublished Store20/Email52 protocol, disposable code/DB copies; no providers/native CLI/outbound invocation in this component.

## Scope and source-backed choices

Approved design109 cancels independent search metadata/FTS, design48 assigns runtime/performance observations to Runs, and the accepted remaining-table map assigns setup events to Runs. This is an implementation grouping of already approved destinations. Root evaluated deriving membership from meeting runs (changes54 nonmembers and2 aliases), embedding one metadata tuple on each run (loses700-to698 multiplicity), and a finite typed Runs metadata kind. The last preserves all existing fields and behavior without another table. Folding the other two approved Runs families at the same time avoids repeating the62-column rebuild, target recognition and source-counter protocol.

The exact field inventory is in `2026-10-16-runs-next-source-allocator-evidence.md` and the search inventory. Original source PK/AUTO identity maps to existing Runs `legacy_key`; shared source fields retain compatible existing columns (`session_id`, `created_at`, `updated_at`, `status`, `finished_at`); source sample recorded_at and setup started_at map to common created_at without a second timestamp; every other field is an actual source column with its native affinity. No new business field, status enum, JSON-validity rule, FK or retention policy is introduced. Source source_id is TEXT without FK and remains so. Preserve five runtime timing fields as REAL. Setup's actual/fresh finished_at default declarations differ; preserve original declaration profiles on inverse, and preserve the public writer's explicit running/finished SQL expression on target.

New metadata IDs and timestamps retain exact native values, including the two session/title aliases and all700 members; preserve the54 nonmembers by source identity and existing native_reference_unavailable behavior. Keep title/native-corpus construction, RAM FTS, transient vectors, scoring, tie/input order, truncation and missing-native behavior unchanged. The only persistent FTS/cache is retired; inverse rebuilds its derived title index from current restored metadata. Original shadow byte/segment layout is not a business fact; do not persist a raw FTS snapshot in inverse metadata. Existing combined checker compares declaration SQL but excludes virtual/shadow row bytes; add explicit logical FTS/title-query controls.

Runtime sample's global latest2048 retention and rate latest200 fallback remain exactly current behavior; do not introduce the unconfirmed30-day policy. Setup event public ID return, step/action filters, latest ordering, literal evidence/stdout/stderr and arbitrary original status remain. Its State step snapshot stays distinct.

## Complete code units before current selection

- [x] **Target declarations and source-shaped parts.** New canonical `runs11_definitions`/`runs11_storage` under this plan before app installation: extend current mapped eight-kind Runs to eleven kinds with explicit source NN/CHECK/unique/type-exclusive columns, partial indexes replacing source PK access paths, original62 fields/triggers/indexes/FKs unchanged. Public numeric IDs remain scoped; internal physical IDs remain positive/native and distinct. New writers use actual source defaults and source ID affinity. Preserve closed original single-VALUES INSERT/OR IGNORE statements/cursor contracts. No whole-repo refactor.
- [x] **Counter differential.** Track only original-eight native attempted allocations in `runs:published` with the existing Tasks BEFORE-attempt formula, including successful IGNORE and ABORT/savepoint/rollback behavior. New three source families each use their own current `runs:<source_kind>` public AUTO stream; new-kind internal physical allocations never advance the old published stream. Read native current waters at conversion, never preparation. Initialize each current stream preserving absent versus0 rather than storing stale counter values; validate whether presence can live entirely in those current streams. Prove native FULL and transaction scope before application; source3 have no BEFORE triggers. Reuse existing native key/FULL/physical allocator mechanisms only after source-backed differential, including old global FULL versus independent new-source streams.
- [x] **Pure source-bound transfer and inverse.** New component binds exact actual/fresh Runs and three source declarations, including original mapped/native FK stage. Stage every one of62 old stored columns without affinity changes and all original source fields/ROWIDs. Original eight physical IDs and incoming Actions/self FKs stay exact. Preserve current waters/deleted-high values; metadata stores only declarations, not bodies, row snapshots or stale water numbers. Drop source families/FTS in the existing caller transaction. Inverse reads current target facts, recreates exact original native definitions, current source public waters and only current original-eight Runs reservation. Reject unsupported mixed declarations/TEMP shadows before mutation; no new general policy layer.
- [ ] **Current callers and constructor closure.** Existing `AutoReplyStore.upsert_codex_session_search_index` and search read a source-shaped Runs projection; remove obsolete cache arguments and persistent FTS mutations without changing search. Current Store setup writer/reader use exact source-shaped writers/projection and returned source ID. Email sample writer/readers, current completeness/repair declarations and quality_gate's existence/raw SQL branch must all change together. Current Store/Email/Workbench creation and exact recognition select Runs11; stop current search table/FTS creators/recognizers/rebuilds. Preserve historical factories. Factor current config metadata predicates from historical Runs-shape checks where necessary; never accept both layouts as a hidden compatibility fallback.
- [ ] **Paired coordinator and copied source.** Source capture before conversion; Runs11 after Config/State/Tasks and canceled-confirmation retirement, inverse Runs11 before Config inverse. Original component shape validators keep native roles; current final predicates require exact Runs11 and retired names absent. Keep same unpublished Store20/Email52 pair, JSON child transport, locking, commit/rollback and baseline FK policy. Fresh constructor orders must all yield51; fixed source116→51→116→51. Target new metadata/sample/setup writes, original8 native writes after preparation, inverse, genuine44ee predecessor writes and reforward must all preserve current facts and counters.
- [ ] **Tests and behavior docs.** Narrow RED/GREEN native/candidate differential, positive original8/nonmember/alias/native-corpus controls, sample retention/quality gates, setup public readback and ordinary Workbench/Agent APIs. Convert only actual stale physical test fixtures; retain business/security assertions. Update architecture/runtime in selection commit. No whole suite/live-service tests; native CLI/fake provider/UI/performance/CI/final11 acceptance remains final program work.
- [ ] **Independent review and exact installation.** Fresh SPEC then QUALITY of complete cumulative canonical candidate, fixed-source full roundtrip proof, exact manifest mechanical installation, fresh applied SPEC/QUALITY and selected receipt. Root owns high-risk migration/final evidence; bounded agents may prepare isolated proposals. No main/push/production migration/restart/deploy before the established final publication approval.

## Current evidence boundary

Root rechecked clean10867f5dc and relevant current code. Frozen source has231340 Runs rows/62 columns/sequence362316, metadata700/sequence700, runtime samples2048/sequence5638 and setup22/sequence22. The current source has no retired cache columns. These are fixed-source facts, not a fresh production snapshot or target acceptance. Search membership52-by-session versus54-by-source-id nonmembers are different; only original source identity behavior is preserved. No target code or data conversion selected yet.

## Unselected foundation and transfer checkpoint

The native actual/cold-constructor declaration oracle is independently generated,
SHA-pinned, and used to create the native side of the writer differential.
Foundation SPEC and revised QUALITY pass. Metadata public-ID allocation uses a
covering partial expression index; the same bound query stays27→25 VM instructions
when2000 unrelated original Runs rows are added (original25→8023).

The pure source-bound release canonical and synthetic current-fact inverse now
exist. Revised SPEC and QUALITY pass after including FTS/shadow dependency and
extra-object closure, and preparing inverse views/trigger programs against the
original Runs columns in an empty SQLite schema. The combined pinned check is
247 passed; this is not current-caller or constructor acceptance. The actual-copy
checker is separate and its receipt must match the final canonical hashes before
claiming copied-source acceptance. No app selection or production change occurs
in this checkpoint. The isolated installed candidate still has54 ordinary tables;
51 is an unselected pure-component candidate, and the final target remains11.

### Current caller integration constraint: native input ordering

The actual Store search currently reads metadata with `order by updated_at desc`.
On the pinned native source table, equal timestamps preserve public ROWID order.
A two-row native/target probe demonstrated public IDs `[1,2]` on the old table but
`[2,1]` on the unqualified replacement projection due to the target access path.
The replacement reader must use `order by updated_at desc,id asc` to preserve
that source input order. The pure release regression proves the explicit
projection equals native complete tuples for the actual/fresh source profiles;
the current public search caller remains to be integrated and tested. Do not
claim schema row equality alone proves native-corpus/ranking/tie behavior.

## Installed unselected foundation acceptance

The final runtime hashes passed the frozen-source copy sequence116→54→51→54→51
in474.445s. All231340 original Runs,700 metadata,2048 samples and22 setup events
transfer exactly. Three target source writes retain public IDs701/5639/23 and an
original-kind runtime write retains physical ID365090 through inverse. All50
unaffected54-table baseline families retain complete row/type/ROWID digests;
source declarations, current source counters and logical rebuilt title FTS verify.
The pre-existing Runs190042→Tasks FK finding remains unchanged; no new finding.
The frozen source SHA970a remains unchanged and the owned copy directory is gone.

Seven app/test/fixture files are exact installed canonical copies. Applied275
focused tests and Ruff7 pass; a cold Store+Email control still creates54 ordinary
tables, Email ledger52 and clean fresh FKs. This proves only the unselected
foundation. Next implement the current Store/Email/search/quality_gate callers,
current schema recognition/repair and paired JSON coordinator together, then test
all three51-table constructor orders and the complete paired116↔51 inverse with
genuine predecessor writes. No main/push/production/restart/deploy was performed.

Fresh installed SPEC275 and QUALITY61/Ruff7 APPROVE code c14cb181d; exactseven
canonical hashes and final copy bindings reverified by root. See
`2026-10-16-runs11-foundation-acceptance.json`. Both actual-copy directories are
absent, and production HEAD44ee956d remains unchanged by this task. The Store
caller proposal has only isolated4-test evidence and remains unselected pending
review and cumulative Email/constructor/coordinator closure.

## Current51 integration implementation sequence

Root owns the coupled current-shape closure. Prepare a separate current Runs11
factory/recognizer that mirrors native missing-index/trigger repair and leaves
all counters alone. Factor the existing config content predicate from its
historical mapped Runs8 binding; keep that original predicate exact for native
config forward/inverse and use a distinct current Runs11 binding for current
Store/Email/coordinator checks. Store creates mapped Runs11 directly, cancels
setup/source-search creation and does not run native FK translation on fresh
creation. Task20 current helpers retain child guards and stop search/FTS creation
and recognition. Email keeps its historical maps/factories as source roles and
excludes the retired sample table from current creation and current validation.

Capture Runs11 declaration metadata with the real paired source, carry it through
the existing JSON transport and source recheck, convert after Config/State/Tasks
and confirmation retirement, and inverse before restoring native Config Runs
FKs. Keep unpublished Store20/Email52; do not accept the earlier unshipped54
shape as a silent compatibility alternative to current51. All current selectors
and cold constructor orders must converge to51. Store/Email runtime proposals
are integrated only after their independent SPEC then QUALITY reviews. Test and
inspect the cumulative candidate on an owned code/DB copy before selecting it in
this worktree, then repeat exact applied reviews. No production/main/push/deploy.

## Current integration checkpoint

The Store caller proposal passed bounded SPEC and revised QUALITY review. Its
five copied-app regressions now assert explicit public tie order and native row
facts rather than an incidental SQLite source plan. Email's four original
functional regressions passed SPEC/QUALITY, but a larger no-statistics QUALITY
probe found a real retention-query scaling regression: 2,048 sample rows plus
231,340 higher-public-ID other-kind rows caused the general Runs index to be
scanned. Median synthetic cost was9.371ms/~960k VM instructions, compared with
native0.349ms/~29k; the existing partial sample index gave0.434ms/~31k. These are
owned synthetic measurements at source coarse counts, not production latency.

The Email proposal now directs only that inner retention SELECT to the existing
`idx_runs11_sample_id`. Its new no-statistics scale regression fails without the
direction (14,100 VM steps on120 samples/3,000 unrelated rows); revised five-case
proposal tests pass. Revised SPEC and QUALITY both pass before selection: the independent
231k no-statistics rerun uses~31k VM instructions versus native~29k. No new index, ANALYZE startup step, retention rule or business field
was added.

Root authored `2026-10-16-runs11-coupled-copy-preflight.py` for the complete paired
116→51→116→51 proof. It compares original62-column Runs and all three native
source families by value/type/public ID, native writes after prepare, target
writes, exact inverse declarations/current counters/markers/Email ledger,
logical title FTS rebuild and real44ee predecessor cold writes/reforward. The
script passes Ruff. The cumulative old-hash286ded8 copy is currently running and
has verified116→51→116, current source facts/types/counters/declarations, and
genuine44ee predecessor writes; final reforward remains in progress. The final
integration patchbcc16aab replaces dead Runs8 type probing and an inline SQL
classifier with the existing SQLite-introspected helper using current Runs11.
It passed final SPEC/QUALITY and121 cumulative tests; exact final-hash copy and
mechanical/APPLIED gates remain open. A15-file prospective selection manifest
binds the three code proposals, source-role-correct current regression fixtures
and behavior documentation. First cumulative preparation stopped at overlapping
patch context and the verifier then corrected its metadata key fromruns toruns11;
neither attempt changed application or source data. The
application still selects54 and production remains44ee; the final11-table goal
and the program's native CLI/UI/performance/CI/publication gates remain open.

## Accepted final runtime copied-source proof

The final integration runtime hashbcc16aab passed the complete real paired
116→51→116→51 protocol in590.162s. All original62 Runs fields and the three
source families retain values, types and public IDs. Native old-eight write
362317 after preparation, target old-eight physical365091, target setup23 and
sample5639, then genuine44ee predecessor setup24/sample5640 all survive the
appropriate inverse/reforward checks. State, cursor and full typed WeChat facts
also survive;109 other restored source families have unchanged complete digests.
Original declarations, current native counters, original Store timestamp and
full Email ledger are restored. The one original FK finding runs190042→tasks is
unchanged; source SHA970a is unchanged and both owned candidate/data directories
were removed automatically.

The receipt `2026-10-16-runs11-coupled-copy-evidence.json` binds all nine runtime
after-hashes to the15-file selection manifest. A fifth, docs-only closure patch
updates deeper paired-release sections and clarifies historical Config Runs8
versus current Config/action+Runs11 checks; it passed SPEC/QUALITY. Only the two
document after-hashes differ from the source-copy input manifest, whose SHA is
retained. Runtime and test hashes remain exact. Application selection and fresh
APPLIED review follow; final11/native CLI/UI/performance/CI/publication remain
open. This receipt has no native Agent CLI/provider/outbound/production claim.

## Mechanical current51 selection

After final canonical copy acceptance, commit317536b6e installs exactly the15
manifest paths plus the owning claim row. Every installed after-hash matches;
all nine runtime hashes equal the actual-copy receipt. Installed focused tests
passed396 cases in18.01s; Ruff passed all13 changed Python paths, and diff checks
passed. A separate cold Store/Email inventory measured51 ordinary tables and an
empty fresh FK check. Production readback still44ee; no main/push/deploy occurred.
Fresh APPLIED SPEC then QUALITY are still pending at this checkpoint. Remaining
42 legacy families and final native CLI/fake-provider/UI/performance/CI release
acceptance retain their full scope.

Fresh APPLIED SPEC passed487 selected tests42.61s and APPLIED QUALITY passed32
focused controls5.63s, with exact15 installed hashes and9 actual-copy runtime
hashes. Broader tests initially yielded524pass/32fail in two unchanged fixture
files using retired physical Tasks/State/sample storage. A separate test-only
maintenance preserves65 quality tests/126 assertion ASTs and historical Email
DDL oracle, replaces fixture writes with existing source-shaped helpers, and
reads the current State projection/version. Baseline37pass/32fail becomes69pass;
fresh fixture SPEC independently passed69/19.05s. Root's exact12-file regression
now passes556/56.30s. Fixture QUALITY passed exact assertion/identity/transaction/hash controls;
the separate test-only commit closes the broader fixture gate. Runtime hashes
are unchanged and no repeated full database copy is required for test-only edits.
