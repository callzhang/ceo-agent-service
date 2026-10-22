# Email history current 15→13 local validation, 2026-10-17

## Latest actual-data gate: normal r2 full-copy proof PASSED

Accepted current13: root57030 CLOSED/EXIT0 PASS1558.671s, full normal
116→13→116→13, genuine predecessor4f3d52a0,40 retained families including both
public-writer generations. Receipt SHA256
`6b8c310cf83694000ee671400f17136452452f0ae79edf4dbe39c0bd28a428fc`.
Whole source DDL/markers/current facts/types/IDs/ROWIDs/waters/preexisting FK
finding restore; normal900s child, no diagnostic override. Root managed copy
removed and reviewed pins stable. No source write/send/deploy. Capacity NOT
accepted: same source502931456B→first target563466240B (+60534784B).
See `database-redesign-current13-capacity-20261017.md`. Earlier failure and
pending statements below are preserved historical stages, not latest status.


Later r2 review: root froze16 immutable/10 requirement/371 runtime pins in
`docs/superpowers/specs/2026-10-17-email-history-current13-freeze-r2.json`, SHA256
`c68a4f96bab11fd27a74999bae93fa49d71d26ed3c6e0286d115aea8cb3bad73`.
Same SPEC reapproved with independent3passed/1.68s; same QUALITY reapproved
with independent3passed/1.1s, all pins/path membership stable before/after.
Both independently reverse the comment/cap edits to exact original39 file bytes;
other370 runtime files unchanged. QUALITY found no outer300s publication cap in
the inspected updater path. The normal uninstrumented r2 attempt stopped before DB access because production
HEAD changed from83c1d7f0 to4f3d52a0. Readonly Git comparison confirms exact app
and source-spec trees unchanged; only one OA Skill, its release manifest and
claims changed. A separate immutable production-baseline refresh records this
increment; r2 code freeze remains unchanged. The normal900s full proof subsequently passed as recorded below. The failed and diagnostic
evidence below remain historical, not replaced by local review approval.

Later measured diagnosis: root47355 EXIT0, profile
`docs/superpowers/specs/2026-10-17-email-history-migration-profile.json` SHA256
`d24ca24be72ab4232b92ea1fa0ee76934748ac7beed5885f100716c575999cd6`.
One forward only, unchanged39 runtime, launcher-only1200s cap,99 timing events,
462.326s overall. Child412.570s; before the NEW history stage339.545s; history
conversion41.937s, primarily98,242 retained Actions restore35.936s, while snapshot
0.874s and183 native history inserts0.073s. Existing stages already exceed300s.
All40 families/types/identities/ROWIDs/waters match on this diagnostic forward;
root managed temporary context absent,15/6/371 pins stable. Source allocation
502812672B→563326976B target (+60514304B), not space acceptance. Fresh source
provider inventory72246 differs from the failed proof's72245; keep snapshots
distinct. Runtime acceptance remains FAILED despite diagnostic completion.

Root selected the bounded paired-only deadline repair described in
`docs/superpowers/plans/2026-10-17-email-history-timeout-repair-plan.md`. The
same author changed only the full initializer child deadline from300s to15min,
without a new knob, retry, fallback or pipeline change. A deterministic
measured-duration transport test failed at300s and passed at15min; child error
and beyond-budget timeout still propagate. The new transport cases plus both
existing blob-metadata and paired-publication source variants passed seven
focused tests in165.84s. Existing deployment health wait is already15min for
large-data startup. Original39 freeze and failure/profile evidence stay
immutable. Same SPEC then QUALITY re-review r2 and a normal-path full copy
without a diagnostic override remain open; final13 acceptance is not claimed.

After fresh SPEC and QUALITY approvals, root session58148 opened production
only through a URI read-only/query-only online backup. BACKUP_READY confirmed
116 tables and40 distinct retained families, including183 history/0 operation
rows. The paired initializer child hit its existing hardcoded300s limit inside
`_migrate_pair`; subprocess.TimeoutExpired stopped publication before the first
current13 acceptance. No full PASS receipt was created and no data/capacity pass
is claimed. Failure metadata is
`docs/superpowers/specs/2026-10-17-email-history-current-copy-failed-timeout.json`.

Root verified all15/6/371 original frozen inputs/path membership remained
unchanged through the diagnostic, the failed-copy managed temporary directory
was absent, and production Git83 was clean and unchanged. Production/source
database access was read-only. Previous current15 first-cold total289.35s is
historical context, not the measured time of the failing child. The measured
412.570s child profile above supports the bounded deadline correction; it does
not substitute for normal-path full-copy acceptance.

This unit selects exactly two existing Email history families into `actions`:
`email_historical_classification_history` (nine fields) and
`email_historical_operations` (15 fields). The local candidate has 13 ordinary
tables. The previous 15-table actual-copy receipt is predecessor evidence,
not evidence that this 13-table target fits or has been published. There are
no production writes, deployment, provider calls, model calls or native CLI
calls in this unit.

The source declaration capsule is
`docs/superpowers/specs/2026-10-17-email-history-current15-source.json`
(SHA-256 `8fe6b3293a8732b193019790c4bc5c3c2bb26e638da49813d9ac175a61935937`).
It records actual and fresh source declarations, the whole current15 FK graph
and the default Actions factory oracles. The production checkout Git tree
`83c1d7f030bb16cb6ef1fc729efc322be409dda2` was archived read-only to
capture six genuine public method AST signatures and outputs; no production
tests or edits were made. For the deterministic synthetic six-method fixture,
the genuine native and selected current output bytes match, with SHA-256
`946c053572521acfb180741b5d160c3c137cfad01bf75589a7f06b5436807c9f`.

The component stores the source ordinary hidden ROWID in `legacy_key`, keeps
the public TEXT primary key in the payload, and preserves NULL-public-key
multiplicity. It uses the existing Actions allocator and caller transaction
helpers. Operations keep native `(model_id, stable_message_identity)` uniqueness
across accounts, native JSON validity without an array-only rule, and unbounded
required REAL scores. Source classification history alone retains nullable
0–1 score checks. The six public methods keep native result shape, first-write
timestamps, deterministic operation IDs, broad `INSERT OR IGNORE` behavior and
processing-only updates.

Forward conversion captures exact source declarations and waters without row
bodies in persisted metadata. It rebuilds `actions` only, removes the two
native history tables, and leaves exact unchanged Business and Legacy tables,
their rows and five Actions FK edges in place. The dependency parser masks only
those five declaration-verified edges in an isolated parser, retaining checks
for unknown MAIN and TEMP dependencies. Inverse reads current target facts,
restores the exact two source declarations and ordinary ROWIDs, and preserves
writes made after forward conversion. Synthetic tests exercise nonempty
Business/Legacy parent rows, BLOB and duplicate-NULL TEXT public keys,
non-array JSON, default and reserved waters, all 24 value/typeof fields,
FK findings, and forward→inverse→reforward.

Focused verification includes source/target constraint and binding parity,
genuine-native public differential, both constructor orders, known-object
repair, paired release/inverse, and historical worker recovery branches.
The initial RED selected constructor returned 15 tables rather than 13. A
later focused constraint RED found blank `event_id` wrongly accepted when
another nullable field was NULL; independently parenthesizing each generated
source check corrected it. The current-only `EmailStore` fresh-schema test
had a stale expected table set containing five families already retired in
earlier units; it now expects the three retained Email native tables. Source
factory and downgrade fixtures remain unchanged. Bounded final runs used the
managed synthetic temp directory, isolated pytest cache/basetemp and at most
two workers: ten focused storage, public parity, fresh constructor and fixture
tests passed in 190.10 seconds; three dependency, allocator/rollback and
binding tests passed in 72.01 seconds; paired inverse and known Store repair
passed seven tests in 314.44 seconds; five selected historical worker branches
passed eight tests in 141.00 seconds. The focused current-only fixture initially
failed one assertion before its expected set was corrected (one failed, three
passed, 70.69 seconds). An initial mistyped test-selector command collected no
tests and exited five; the corrected ten-test command above passed.

Open gates: fresh independent SPEC and QUALITY review of this exact unit,
full paired actual-copy 116→13→116→13 on a read-only production copy (the local
component source stage is15), same-input capacity,
remaining native families and canonical Email field provenance/body availability,
real CLI/fake-provider/UI acceptance, full integration and deployment.

## Root freeze before independent review

Sole author DONE; root independently checked15 immutable file hashes,6 requirement
pins and all371 runtime files/path membership. Only the nine claimed runtime
paths differ from the prior accepted369-file baseline. Freeze
`docs/superpowers/specs/2026-10-17-email-history-current13-freeze.json` has SHA256
`39d4353ed34507d33fdefd3d7c21a5364bc5baaccdb904754641f8394898ff27`.
Fresh SPEC is active; QUALITY and full actual-copy have not started.

Later review status: fresh SPEC APPROVE, independent3passed/60.10s covering
source checks/binding, unknown MAIN/TEMP dependency rejection and public native
OR IGNORE conflicts. All15/6/371 pins plus full runtime membership stable before
and after. Fresh QUALITY now active; full actual-copy remains unstarted.

Later QUALITY status: APPROVE with noP1/P2. Independent fresh synthetic current
probe examined seven SQLite plans in28.4s: public identity reads, operation
update/processing reads and allocators use intended indexes; history-list sort
matches the native sorted-scan behavior. Reviewer rechecked all15/6/371 pins and
exact runtime membership before/after; its managed temporary context removed.
Root may now start the separate full116→13→116→13 proof, not publish from these
review results alone.

Root independent public-fixture dryrun also passed in30.15s: verified all324
native Python files in the retained author archive against genuine production
Git83, ran the same six-method fixture on new isolated native/current synthetic
databases with prefix root-proof, and compared identical4895 serialized bytes,
SHA256 `26e079feea23ccffa970214eee7948484488650ab204e51f2c0ec94a4a99932a`.
All15/6/371 pins remained stable; root's new managed temporary context removed
itself; the denied author archive remained byte-identical/untouched. No source or
production database was read and no provider/model/native CLI/send invoked.

Root independently reversed the Business parity fixture's three count changes,
two test names and one doc label in memory; the resulting bytes hash to the
previous accepted fixture `c99e23be5b1b6020421263da8837876c04a983e943797f39519591699b745334`.
The EmailStore fixture diff is exactly one current retained-name set hunk;
historical source/downgrade code is unchanged.

Additional preacceptance findings remain explicit: paired publication initially
failed both source variants because the history option was passed to the wrong
predicate (2failed/63.32s), corrected to2passed/67.85s. Direct source-constraint
comparison failed1/passed3 in77.88s on blank event ID with nullable category;
per-rule parentheses gave focused1passed/29.33s and the later final groups above.
Genuine83 also confirmed that native broad INSERT OR IGNORE yields AssertionError
after a read miss for a second unique-key collision or important=2, whereas the
initial narrow conflict helper raised IntegrityError. The public helper now uses
the native policy, with a retained regression; strict generic writes still raise.

Automatic approval review rejected cleanup of `/tmp/email-history-current13.pBfsih`
because deletion requires approval while AskForApproval is Never. It remains
untouched; no retry or bypass. The earlier denied
`/tmp/business-actions-callers.e44HNH` also remains untouched. This does not claim
that all test artifacts have been removed.

Root cleanup follow-up: Derek explicitly authorized the five listed task-owned
temporary directories after reviews. Fresh checks found2,569 regular files,
554,448,961 bytes,116 internal symlinks, current-UID ownership and no lsof
references. Exact rm -rf -- on those five paths was again rejected by automatic
approval: approval required by policy, but AskForApproval is set to Never.
No delete executed, no retry or alternate deletion mechanism used.

Root normal proof57030 after baseline refresh673e631e: firstcold13
PASS586.21s on fresh116-table/40family snapshot,183history/0operations,
provider72246. Full current-write/inverse/genuine-old/reforward still running;
no final receipt or current13 acceptance yet. No diagnostic timeout override.


Final normal proof57030 CLOSED/EXIT0 PASS1558.671s, receipt
`docs/superpowers/specs/2026-10-17-email-history-current-copy.json`, SHA256
`6b8c310cf83694000ee671400f17136452452f0ae79edf4dbe39c0bd28a428fc`.
Stages116→13→116→13; firstcold586.21s, inverse955.20s,
genuine predecessor cold/read/write976.08s, final1558.671s. All40 retained
families preserve current values/types/IDs/ROWIDs/allocator waters; entire source
DDL and markers restore. Original FK finding runs/190042/tasks/1 is preserved,
not newly introduced or silently fixed. Both current and genuine predecessor
public history fixtures add1 history and2 operations, and allsix public methods
retain their behavior. Same approved work_projects.memory_context_json retirement
exception only; this is not all116 source rowsets or final11 acceptance.

Production predecessor4f3d52a0 has the exact reviewed83c1 app/spec trees per
separate baseline refresh673e631e. No diagnostic launcher/time-limit override;
the normal bounded900s child code was used. Managed temporary root absent and
all16/10/371 pins/full runtime membership rechecked. No provider, model, native
Agent CLI, real send, production write or deploy occurred.

Same-snapshot capacity: source502931456B→first target563466240B
(+60534784B), final563642368B after fake writes. Zero freelist pages in allthree.
This is structural/reversible migration acceptance, not space acceptance.
Next remaining table reductions: canonical Email13→12, then source-document/
legacy-link disposition and provenance12→11, with real source availability and
body lifecycle, capacity, field matrix, nativeCLI/fake-sends and UI gates open.
