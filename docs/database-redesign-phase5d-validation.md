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
data, paired release acceptance and deployment remain unverified.

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
