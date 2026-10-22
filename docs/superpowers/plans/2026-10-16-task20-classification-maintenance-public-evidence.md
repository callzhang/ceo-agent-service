# Tasks20 classification parent and explicit maintenance proof

The two changed method bodies in the remaining-reader proposal pass **17/17 source/target checkpoints** through their actual implementations. This is a proposal-only proof on synthetic full-fresh source and an explicitly prepared empty Tasks20 fixture. It proves neither target construction nor migration/inverse/publication selection. The earlier eight reader-module projection imports retain only their separate static/import evidence; this run makes no public-method claim for them.

Reproducer: [public preflight](2026-10-16-task20-classification-maintenance-public-preflight.py). Exact pins, typed facts, native transaction observations, coordinates and negative controls: [evidence JSON](2026-10-16-task20-classification-maintenance-public-evidence.json).

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B \
  docs/superpowers/plans/2026-10-16-task20-classification-maintenance-public-preflight.py \
  --output docs/superpowers/plans/2026-10-16-task20-classification-maintenance-public-evidence.json
```

## Exact construction boundary

The unmodified copied source runs ordinary `AutoReplyStore` **and** `EmailStore` constructors. Both Tasks and classification queues are empty afterward; foreign-key checking finds no violation. The native classification source has exactly fourteen fields and **no `id` column**. Source Tasks DDL SHA256 is `ee4330cb187a80d4699db16e56b61e47f992165ed44a840f4ecbe8059888374c`; classification DDL SHA256 is `8c08e7f99f10d78de9ed358260d12272bb5220a87829fbe21a070b722b6c71d4`.

A backup of that synthetic empty database supplies the candidate fixture. Its preparation asserts empty Tasks/classification queues, removes only those two empty tables, installs accepted Tasks20 DDL and initializes the existing-eight source water to zero. All other table names and DDL remain exact. Eleven other native Task12 families remain present and empty; this is deliberately not a complete target constructor or release fixture. Target Tasks DDL SHA256 is `903e2952423e3398a6268e14d40ef2a8ea8e6333956ff50b96791933b9ea600d`. No candidate constructor runs.

All **nine exact proposal patches**, in the order pinned by the earlier remaining-reader analysis, apply to the copied candidate. The canonical Task12 helper remains `127311aa2ed303066fc3dd325a5642c0e61725a2d8db3b5bedfc218e804e013f`. Full source and candidate module hashes and every patch hash are in the JSON. In particular:

| Module | Source SHA256 | Candidate after nine proposals SHA256 |
| --- | --- | --- |
| `app/store.py` | `ce66a4dd7302080126fb178b4dc8e3983acbc68b2f70c9107bd3e584c4207d5f` | `2d82e4140b34a38025b9b7129d4c90d6262e806d49a9239401f990b633a8c6e5` |
| `app/storage_maintenance.py` | `d1021d40b06d5e78c680625a200fff3bb92eaa6d95338e72ad966c06acf522f6` | `07f965f76e5583f0edd09be82ec53be838604594ab469bcafe93f301ed5f503c` |

## Actual parent read

The actual `AutoReplyStore._runtime_operation_parent_exists` executes twelve times per worker: eleven ordinary boolean cases and once inside a caller-owned write transaction. No Store object or substituted DAO is needed for this existing static method; each invocation receives a real SQLite connection.

| Cases | Observed source and target result |
| --- | --- |
| Running task, Unicode TEXT key, and running key `007` | `True` |
| Done, pending and failed tasks; missing key | `False` |
| Done key `7`, distinct from running key `007` | `False` |
| Nullable task ID and NULL query key | `False`, preserving native `task_id=?` semantics |
| Source ROWID string `101` and target physical ID string `1`, neither a task key | `False` |
| Caller changes a done row to running within `BEGIN IMMEDIATE`, invokes parent check, then rolls back | Method returns `True`, preserves the open caller transaction, and the caller's explicit rollback restores exact original facts |

Ordinary parent reads leave `total_changes` unchanged and create no transaction. Source ROWIDs are explicit 101, 205, …, 819; target physical Tasks IDs are 1, 2, …, 8. Public task keys, source ROWIDs and physical IDs remain separate. The source and target typed logical facts are compared with no physical-ID substitution.

## Actual raw-connection compactor

`compact_done_email_classification_inputs` executes five times per worker. The function itself opens, begins, commits/closes and registers the candidate scalar codec on its original raw connection. The observation-only SQLite connection subclass forwards every original SQL statement and result unchanged; it records native cursor rowcounts, transaction trace and whether a transaction remains open immediately before native close. Fixture read/seed connections are separate from the active method observation.

| Checkpoint | Return / native result | Transaction evidence |
| --- | --- | --- |
| Empty queue | `tasks_compacted=0`, `logical_bytes_removed=0`; no UPDATE | `BEGIN IMMEDIATE`, `COMMIT` |
| Mixed eight-row queue | Two done rows compact; `20,772` UTF-8 logical bytes removed; UPDATE rowcounts **0, 1, 1** | `BEGIN IMMEDIATE`, `COMMIT`; native close sees no open transaction |
| Same queue again | Zero rows and bytes compact; one NULL-key UPDATE returns **0** | `BEGIN IMMEDIATE`, `COMMIT`; exact before/after facts |
| Native `RAISE(IGNORE)` on second done row | First row compacts; `10,386` bytes removed; UPDATE rowcounts **1, 0**; ignored row remains exact | `BEGIN IMMEDIATE`, `COMMIT` |
| Native `RAISE(ABORT)` on second done row | First UPDATE succeeds with **1**; second throws `IntegrityError: fixture second classifier abort` | Trace contains `BEGIN IMMEDIATE` and no COMMIT; native close observes an open transaction; reopening shows **every original fact restored** |

The mixed queue contains a nullable-TEXT-key done row, two ordinary done rows, already-compacted boolean-true and integer-one done rows, and failed/pending/running rows whose `{}` inputs would fail the compactor if wrongly selected. Only the two ordinary done input bodies change. The NULL-key row is selected but its `task_id=?` update matches nothing; the zero rowcount contributes neither a compacted-task count nor removed bytes. Boolean-true/integer-one rows stay excluded by the original JSON predicate. Every non-input field, storage class, source ROWID, NULL task ID, status, error, result, owner/generation/attempt and timestamp remains exact.

Independent assertions verify compacted UTF-8 byte count and SHA256, immutable identity SHA256, stable message identity, provider locator, config version, unsubscribe candidates, all copied scheduled-consumer identity fields, and prompt/protocol/material hashes. Full message/prompt/protocol content is absent from the compact representation. Source and candidate comparisons use full typed rows and **exact original JSON text**; only after equality is established does report serialization replace repeated synthetic input bodies with their type, byte count, hash and compacted provenance.

The ABORT is an intentional native trigger reached **after successful fixture setup and one successful method UPDATE**. It is not a setup exception counted as passing. SQLite connection close performs the rollback here; the trace does not falsely claim an explicit SQL `ROLLBACK`. Durable reopened equality proves that the first UPDATE was rolled back. The separate parent test observes an actual caller-issued explicit rollback.

## Distinguishing controls and limits

Both controls run only after the positive source/target differential succeeds, on additional copied candidate code and fresh fixture copies:

- Change only the parent branch from `status='running'` to `status='done'`: a verified running task wrongly returns `False`. The actual owner is entered and setup succeeds.
- Change only the compactor UPDATE's status recheck from done to failed: the real selected done rows each return rowcount zero, the returned count/bytes become zero, and all input facts remain unchanged. This differs from the successful candidate's two rows / 20,772 bytes. The actual owner is entered and setup succeeds.

Ruff passed; the pinned Python script executed with bytecode disabled. All source input hashes matched again at completion. Temporary source/candidate/mutant trees and all synthetic databases were removed by `TemporaryDirectory`; removal is verified in the JSON. The guarded constructor/method phase records no network, subprocess or OS-system external action; no provider or native Agent CLI is called.

No application/test file, canonical migration file, root program/plan/readiness evidence, production database, main branch, push or deploy changed. Historical source factories and publication selection remain untouched. Remaining acceptance includes selected cold construction, real conversion/inverse and full caller/publication closure under the root integration.
