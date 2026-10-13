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
No new Store version is reserved and the Store bootstrap and production
continue to use the twelve-type actions declaration plus the original sent
tables. Migration, current-fact inverse, caller conversion, real database
copy acceptance, paired schema release and deployment remain unverified.

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

Next implement the paired twelve-to-fourteen-type storage migration and
current-fact inverse. Both captured literal source variants must be supported;
unknown declarations, unexpected dependencies and unrepresentable data must
be rejected before mutation. Preserve original twelve-type physical/public
identities, references, persisted values/types and both receipt watermarks;
preserve sent public IDs and its independent native allocation watermark.

After target mutations, inverse must reconstruct current sent/observer facts
and the original twelve-type actions layout. It must retain new deliveries,
recall changes, deletes and observer rebindings, rather than restore a stale
business snapshot. Actual previously published code must open that restored
database in a new process before re-forwarding. Caller, schema release,
production-copy, native CLI with substitute senders, CI and release checks
follow the approved Phase5D plan. No real test send or recall is authorized.
