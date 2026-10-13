# Phase5D validation: sent delivery and observer facts

## Scope and current boundary

This unit moves the existing `sent_replies` and `sent_reply_observers` into
`actions` as two finite record types. It adds no physical business table and
does not change the approved final ten business/runtime tables plus one
migration table. The eighteen actual fields and source semantics are frozen
in the Phase5D delivery source inventory and implementation plan.

The published source is `46bfdbe09403238f95c8aece05bf2f6be2c82b88`,
Store `2026-10-13.3` / Email51. The source refresh binds the actual sent table
declarations to that published version. A later read-only check confirmed the
same commit/Store marker, 2,341 sent records and 320 observers. These are
observed quantities, not migration constants or retention rules.

The current opt-in foundation is `64a5c1105a01fd37a1da2c88e63688322ea342ac`.
It passed final independent SPEC and QUALITY re-review. Its predecessor
`b895dcd9` passed SPEC, then QUALITY found a Python native-autocommit
transaction discrepancy; the final repair closes that reproduced gap.
Production continues to use Store17, twelve-type actions and the original sent
tables. The current isolated candidate reserves Store18 `2026-10-13.4` and its
fresh/current bootstrap uses fourteen types. The standalone migration/inverse,
latest source-copy proof and four cold/fresh bootstrap checks below are verified.
Caller conversion, integrated target constructor acceptance against production
data, paired release acceptance and native substitute approval are verified as
recorded below. CI, normal deployment and production readback remain pending.

## Opt-in foundation evidence

The initial foundation commit is `98b1b010`. Independent review reproduced
three gaps despite passing tests: observer identity uniqueness depended on
JSON spelling, existing integer public-ID queries lost their numeric index,
and native sent AUTOINCREMENT exhaustion did not roll back the caller's
transaction. Repair `b895dcd9` closes those exact gaps with regression checks.

- Observer uniqueness uses the physical sent/run pair, preserving the original
  two-integer public identity. A legal whitespace variant of the stored JSON
  cannot admit a duplicate, and remains accessible by public tuple for read,
  conditional update and deletion.
- All original integer action families and sent deliveries use
  `idx_actions_public_id` for public-ID lookup and filtered projections in the
  fourteen-type layout. Six-, nine- and twelve-type declarations were compared
  independently to the code preceding the foundation: all objects remain
  exactly equal. The original receipt sequence namespace is unchanged.
- Sent AUTOINCREMENT exhaustion matches both captured source variants:
  `SQLITE_FULL` code13, entire caller transaction rollback, nested savepoint
  removal, pending unrelated rows and uncommitted MAX_INT rows rolled back.
  Committed sequence progress survives. Existing receipt behavior is unchanged.
- All fourteen sent fields retain their original meaning, including raw TEXT,
  embedded NUL/non-JSON values, nullable `recalled_at`, mutable recall facts,
  exact nonempty external-key uniqueness and the unconstrained scalar
  `agent_run_id` default0. Observer sent/run/task endpoints use real immediate
  NO ACTION FKs and preserve logical parent checks and legal rebinding.

The independent SPEC reviewer ran 264 focused source/storage checks, Ruff and
commit whitespace validation on `b895dcd9`. Root separately ran 221
focused sent source/target, postfix storage/migration and receipt-storage
checks after that repair; all passed. The QUALITY finding reproduced a mode
missing from that test matrix: `Connection.rollback()` is a no-op with Python
`autocommit=True` even inside explicit BEGIN/SAVEPOINT. Commit `64a5c110` uses
SQLite's SQL ROLLBACK on the sent-only exhaustion path. The expanded captured
source/target matrix reproduced four failures before the fix; the implementer
ran 270 focused checks successfully, and root independently ran all27 delivery
target checks on the final repair. Final independent SPEC also compared24
source/target cases across both source variants and three connection modes;
QUALITY independently repeated its original failing native-autocommit probe.
Each reviewer ran270 focused checks on the exact final SHA successfully.
These are foundation checks, not constructor,
database-copy, provider delivery or release evidence.

## Remaining acceptance

The legacy fixture alignment now passes all 142 cases across the five affected
test files. The workbench historical fixture explicitly advances its nine-type
actions through the Store17 postfix transition and creates the captured sent
source declarations before opening with the Store18 constructor. The Phase5C
paired release test reconstructs a genuine Store17 and Store16 layout through
the two inverses, then uses its historical migration callback; the current
router is exercised separately by a Phase5D full-constructor test that proves
default publication, current-fact inverse and republication in a fresh process.
This is isolated test evidence and does not establish production publication.

Five adjacent current-store fixtures now read sent deliveries through the
fourteen-type projection, update a sent timestamp through the finite action
writer, or compare that projection when checking read-only Attention behavior.
The historical caller fixture advances source16 through the real postfix
migration, creates captured sent source declarations, and then opens Store18;
its original candidate, receipt, identity and allocator assertions remain.
Thirteen affected local cases pass. The copied-database acceptance case needs
its separately supplied owned snapshot and was not executed in this fixture run.

The next full CI run (`38467335512`) exposed seven failures and three fixture
errors where historical Store16 databases were passed directly to the Store18
constructor. The shared test helper now advances only those constructor inputs
through the actual postfix migration and captured sent source declarations;
it validates the Store17 layout before setting the Store17 marker. The frozen
source13-to-16 fixture and each original business assertion remain intact.
All ten reproduced cases pass locally, as do 81 adjacent source-chain, manifest,
paired-release and typed-reader checks. This is local fixture validation; a
new full CI result is still required.

Next finish all existing caller paths and integrate the paired release with the
Store18 bootstrap, after refreshing the actual published source again.
The standalone transition below already supports both captured literal source
variants, current-fact inverse and the required identity/value/type/watermark
checks; target constructor and release checks must repeat them.

The integrated target must retain new deliveries, recall changes, deletes and
observer rebindings on rollback, as the standalone proof does. Paired release,
actual constructor-to-constructor production-copy, native CLI with substitute
senders, CI and deployment checks follow the approved Phase5D plan. No real
test send or recall is authorized.

## Standalone storage migration and current-production copy

Commit `25ed876c382caeb43219d813e8f1cea5f795da0a` implements the bounded
twelve-to-fourteen-type migration and its current-fact inverse. The implementer
ran153 focused checks. Independent SPEC passed:125 focused tests,36 forward
and28 inverse refusal probes with unchanged dumps and no live DDL, four
MAX_INT cycles, and four rich sources covering all twelve original types.
Independent QUALITY also passed:125 focused checks,16 refusal probes with no
live DDL, and four additional current-fact cycles. No Store bootstrap, caller
or version is activated by this commit.

Root's initial source-commit check stopped before copying when another session
published `dc6abe2d42ec9e21320589a11248a5a2a0c89e9e`. That increment changes OA
status descriptions; the Store/action/task/run source files are unchanged from
the previously verified `46bfdbe0`. The actual current online source for this
proof is therefore dc6abe2d, Store17/Email51. Evidence is
`evals/database_redesign/phase5d_current_source_storage_copy.v1.json`.

The latest online copy passed full integrity checking. Original3928 actions,
2341 sent records and320 observers retained exact canonical field values,
Python/SQLite value types, public/physical identities and references after
forward migration. Twelve-type persistent columns, including raw payload
envelope bytes, were copied directly. The three native watermarks remained
560/115/2358. Persisted fields of all122 other tables matched their original
typed hashes; the existing single FK diagnostic was unchanged.

The copy then received a surviving new delivery and a separate allocated then
deleted delivery, recall status/time/error changes, observer sent/run/task
rebinding and an additional observer, changed/new postfix facts, deleted native
dispatch, and a new original-family receipt. Inverse retained every current
fact and allocator progress, rebuilt the exact source declarations, and removed
the owned custom sent sequence. Actual dc6abe2d code was extracted from Git
into the owned temporary directory and opened that inverse-derived Store17
copy in a new Python process. The Store marker and current/unrelated facts
remained equal. Reforward and repeat inverse also passed full integrity and
unchanged FK diagnostics.

This proof preserves the original Store17 marker deliberately and exercises
standalone storage functions. The temporary fourteen-type layout is not
claimed to be a valid Store17 constructor schema. Only the inverse-derived
twelve-type layout was opened with the published constructor. Production
remains unchanged; its126 physical tables became124 only in the test copy.
No target Store version, target constructor, paired release protocol, native
CLI integration, production migration, provider send/recall or VACUUM is proved
by this artifact. The owned database/code/script directory was removed after
evidence retention, freeing508825373 bytes. Only aggregate counts and typed
hashes remain in the artifact; no copied business rows or native files remain.

## Caller and publication reader integration candidate

Store18 bootstrap153a7d2d passed four real fresh/cold checks, including both
captured Store17 delivery declarations, raw sent values, deleted-ID allocation
water and auxiliary repair in a new process. Paired release9b38b290 passed
independent SPEC and QUALITY, each48 focused checks; QUALITY also checked
consistent readonly preparation under concurrent WAL changes and atomic
rollback on post-inverse integrity failure. These are unit acceptance, not
the production-copy default-constructor release protocol.

Store/Audit conversiona4a3fd04 removes all direct old sent/observer SQL. The
independent SPEC review checked24 reader bodies against their original ASTs
and all six writes. Repair6963d7f9 retains native recall timestamp evaluation
after writer lock acquisition, with contention regression and unchanged
ordinary transaction behavior. Source-equivalence SPEC passed28 focused
checks. Repair08ba9453 exposes the existing nonempty sent-key unique index
without new DDL: four parameters are already nonempty by their original
validation. Traced actual-caller EXPLAIN and source/target ordinary, padded and
NUL key results pass. Final independent caller SPEC and QUALITY both pass;
QUALITY independently ran37 caller, receipt, History and feedback checks.

The remaining four external readers now use finite sent/observer projections
without changing their predicates or result labels: quality_gate,
reply_task_deferral, scheduled_task_recovery and deploy_maintenance. Maintenance
reads exact source17 or target18 using the paired publication selector. Existing
task/meeting/scheduled/candidate/receipt/postfix publication readers accept exact
Store18 declarations, and the normal release router selects the17→18 pair.
Two source-variant selector tests execute previous and current delivery queries
and refuse mixed markers. Together with bootstrap: six checks pass.

The adjacent deferral/maintenance/quality/recovery/receipt/postfix suite passes
136 checks after three reproduced retired-table fixture failures were updated
through typed writes. Existing Store duplicate-observer, recall, feedback-time
and History index expectations were aligned with the same business assertions;
eight selected Store checks pass. No test was skipped to hide a runtime failure.
Integrated reader SPEC passed161 focused checks and six refusal/router probes;
QUALITY passed148 focused checks and17 additional source/target probes. Both
retained exact reader predicates and required malformed-source rollback. The
two subsequent fixture units54ecae89 andcb05afea retain historical migration
assertions while moving real default-constructor publication evidence to D.
Full CI and normal deployment remain required.

## Actual default-constructor copy and native approval

The first integrated online source wasdc6abe2d, Store17/Email51. The normal
prepare_store_schema_release factory selected the exact17→18 paired release,
whose unmodified default publisher opened the copy in a fresh Python process.
The copy126→124 physical tables retained all original3928 action rows,2341
sent deliveries and320 observer facts with exact values, SQLite/Python types,
public/physical identities and122 unrelated table hashes. The original single
FK diagnostic was unchanged; full integrity checking passed.

New surviving and allocated-then-deleted sent records, recall edits, observer
rebinding/addition and an original-family receipt were written in the target.
Paired rollback retained these current facts and allocator progress, restored
the exact source DDL and original marker timestamp, and was idempotent. Actual
published dc6abe2d code opened the inverse-derived Store17 database in a fresh
process without changing these facts. Default republication and full integrity
checking passed. This is actual constructor/release evidence, distinct from
the earlier standalone functions proof.

The separate immutable Store18 baseline was prepared by the actual constructor
from that same production capture. Native Consumer and Audit each exited0
without timeout; the task finisheddone after one approved in-memory send and
one substitute provider verification. One business attempt, one execution
receipt and one sent record were persisted. Reopening recovered the terminal
result without another native invocation; all118 original ordinary application
tables retained their rows and FK diagnostics. Production has zero matching
test tasks or native sessions. Native trajectories remain solely in~/.codex;
no transcript or credential copy was retained. Artifacts are
phase5d_constructor_copy.v1.json andphase5d_native_copy.v1.json under
evals/database_redesign. These results do not claim CI or deployment.

All11 opt-in scripted lifecycle cases also passed on disposable copies of that
same Store18 baseline in86.08 seconds: approve, return, reject, no_action,
technical_retry, provider_failed, provider_uncertain, scheduled task, work
summary business result, settlement interruption/reopen and Workbench request
idempotence. These use scripted roles and network/subprocess refusal; they are
distinct from the two real native turns above. Both fixture-only commits passed
separate SPEC and QUALITY review. All three root-owned temporary directories,
including copied databases/code and synthetic test-native fixtures, were
removed after the concise artifacts were retained.

## CI fixture repair and latest-source refresh

Full Quality38467335512 at1aab2a49 ended with12746 passing checks, seven
failures and three setup errors. All ten errors were current constructors
opening historical Store16 fixtures; no new runtime failure was hidden.
Commit9acd12dd preserves the frozen empty_source13 function and every existing
business assertion. Only affected callers explicitly advance through the
genuine postfix transition and captured Store17 sent declarations first.
The exact ten cases and81 adjacent checks pass; independent SPEC and QUALITY
each passed13 fresh/migrated cases and verified unchanged historical fixture
and assertion ASTs. SPEC also checked helper validation ordering and atomic
rollback. A new complete CI result is still required.

During CI another session publishedba8b5931, which omits an internal dedupe key
from Task Agent input. It is merged into this candidate; all seven checked
Store/action/postfix/receipt/run/task/email persistence modules are byte-equal
to the previous production source. The increment plus D bootstrap/selectors
passed26 focused checks. A new actual online backup fromba8b5931 then repeated
normal default publication, current-fact inverse, actual published old-code
opening and republication successfully:126→124 physical tables,3928 original
actions,2341 sent deliveries,320 observers,122 unrelated table hashes and the
original FK diagnostic retained. The two v1 copy artifacts now record this
latest verified source; earlier evidence remains in Git history.

On its separately prepared immutable Store18 baseline, real Consumer/Audit
approval again exited0, finisheddone and wrote exactly one substitute send,
verification, business attempt, execution receipt and sent projection. All11
scripted lifecycle cases passed again in108.94 seconds. App/harness hashes
remained unchanged during that native run; production test tasks/sessions are
zero. All three root-owned refresh/CI temporary directories were removed.
These results do not claim a successful replacement full CI or deployment.

### Later current-main binding

After the ba8b5931 constructor/native proof, main published Calendar organizer
source delivery, exact OA-node Attention and launchd unload waiting. Current
production7fabea80 and those independently scoped changes are merged, preserving
both their documentation and the actual C release status. All seven checked
persistence modules remain byte-equal to the constructor proof's source.
Readonly live capture confirms the exact Store17 delivery declaration,126
physical tables, full integrityok and the original single FK diagnostic.
Calendar/OA plus D caller/bootstrap/selector checks passed69 cases; updater and
paired D release checks passed another69. The owned directory was removed.
Evidence isphase5d_current_source_binding.v1.json. This is a current-source
binding and focused increment check, not a claim that the native model or
constructor copy was repeated on7fabea80. Replacement full CI and publication
remain required.
