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
