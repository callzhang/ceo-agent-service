# Task12 Store four-family public-method characterization

The committed existing-eight Store proposal followed by `99dd4c207`'s four-family incremental proposal passes **71/71 unique checkpoints across28 real public methods** on separate synthetic fresh source and prepared target databases. Every one of the19 scoped Store owners is actually entered, with equal source/target entry counts. There are zero differences. This is author behavioral characterization, with explicit fixture preparation; independent review and whole-release acceptance remain root-owned.

Reproducer: [2026-10-16-task12-store-misc-public-preflight.py](2026-10-16-task12-store-misc-public-preflight.py). Concise machine receipt: [2026-10-16-task12-store-misc-public-preflight-evidence.json](2026-10-16-task12-store-misc-public-preflight-evidence.json). Harness SHA256: `eaab979cb2fb92b05475dde1c63b6807fc755f16cbf25578b9f360ece9da6d0e`.

```sh
/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B docs/superpowers/plans/2026-10-16-task12-store-misc-public-preflight.py --output /tmp/task12-store-misc-public-evidence.json
```

## Fixture and source boundary

The source process imports an unchanged copy of `app/` and runs the complete normal `AutoReplyStore` fresh constructor. It asserts `tasks` plus the four source families are empty, checks all41 native source columns against the field contract, and verifies no initial FK findings. The target is cloned from that empty source. A separate preparation process disables FKs before its caller-owned transaction, replaces the empty Tasks table with `create_task20_schema`, initializes the published allocator water to0, and drops **only the four empty native families**. Every other existing table declaration is exact. This does not migrate any nonempty rows or prove retained-child transformations.

The target code copy receives the exact existing-eight Store patch and then the exact four-family incremental patch, both with `git apply --check` and actual apply. Source Store SHA256 is `ce66a4dd7302080126fb178b4dc8e3983acbc68b2f70c9107bd3e584c4207d5f`; cumulative target SHA256 is `705c8f0e73ff9df17cb5f8118da96e99f0b520f9b0c4b6895e67b05a94cdd849`. The receipt pins both patch hashes and all five relevant storage/source module hashes and verifies the copied inputs before applying patches.

Only after preparation, the target fixture bypasses `_ensure_initialized`. The real target constructor still creates its ContextVar and other instance state; `_open_connection` and `_connect` remain the original methods. Fixture connection wrapping registers the accepted exact scalar codec and deterministic `current_timestamp`; a fixture `_utc_store_time(None)` hook and deterministic Store UUID stream make timing/identity repeatable. The clock advances deliberately for the two mapping creations and their merge, making alias timestamps distinguishable. No application constructor, manifest, version or selection code is edited.

## What was exercised and compared

Every checkpoint compares real public returns or exact expected exception type/message/code, actual BEGIN/COMMIT/ROLLBACK events, all source-shaped fields and Python value types, SQLite `typeof` values, hidden source ROWIDs, new-family allocator waters, and relevant raw Actions/Runs facts. FK findings remain empty. Public-call checkpoints are fixed at71 per input, public methods fixed at28 and entered owners fixed at19; the script rejects unexpected errors and cannot count equal unexpected failures as successful parity.

| Area | Decisive source expectations |
| --- | --- |
| OA notification | Plain adopted body survives natural-key IGNORE; duplicate consumes its native AUTO water; IDs1,3,4,5 and pending order1,4,5; exact process/node adoption; ignored/missing claims rejected; sending attempt CAS; failed claim resets to adopted; correct retry completes; stale sent/failure attempts do not change the row. |
| Mapping | Two public reply creations produce separate aliases; one public merge repoints both aliases plus the new trigger alias to the surviving second task. Their individual creation times remain00:00:01,00:00:02,00:00:03. Lookup, three adopted input snapshots and pending queue return the intended task and merged body. OA provisional mapping adoption/prefix lookup preserves the public parent and source hidden-ROWID behavior. |
| OKR items | Public request replay and failed-request UPSERT keep the same public request identity while replacing source input; three adopted item rows retain exact JSON and independent IDs1,2,3; integer/string weight bindings read with SQLite REAL affinity; a public request coordinate987654 remains valid without an invented FK. Request completion leaves items intact. |
| WeChat import | Inputs trim and validate; repeated equal import/account values create jobs1 and2. Runtime parent check and real runtime-attempt claim use public job1. Completion, same-terminal replay, conflicting terminal rewrite, missing job, failed job and running-membership changes match source. |
| Indirect reads | Nonempty standalone failed Attempt appears in full Attention, summaries, count and History with the original body. Mapping reconciliation and supersession execute their real SQL. |

The five explicit application errors match source exactly. Transactions also match at every checkpoint:26 read/pre-validation calls emit no transaction;34 calls emit BEGIN IMMEDIATE/COMMIT;9 emit native implicit BEGIN/COMMIT;2 emit BEGIN IMMEDIATE/ROLLBACK. Final source/target rows contain two reply tasks, six reply inputs, one OKR request, four mappings, four OA events, three OKR items and two WeChat jobs. Full per-checkpoint rows stay in task-owned temporary output; durable evidence keeps their equal record digests and bounded decisive outputs, source aliases, field/type witnesses and raw final physical coordinates.

## Exact identity normalization

New residual rows share target physical Tasks allocation, so subsequently issued **new** published-eight physical IDs have expected gaps compared with this fresh native source. These numeric coordinates are mapped bijectively by exact issuance checkpoint, kind and token; the script requires issuance schedules to match and preserves raw source/target IDs. It verifies target published-eight public IDs equal their physical IDs within the database and every scoped physical parent/input reference resolves to its intended kind and endpoint.

Only those declared newly-issued published-eight coordinates and references to them are normalized: reply parent/input coordinates, mapping `reply_task_id`, and an existing OKR request's `request_id`. Independent four-family AUTO/public IDs and hidden ROWIDs remain exact. All other values, SQLite storage classes, bodies, status, error, CAS and rowcount outcomes remain exact. Published source/target global allocation waters are retained separately rather than equated numerically; new-family waters are compared exactly. This empty fixture provides **no evidence about preservation of IDs already present in a captured migration source**.

## Negative control

A separate copied target mutates only the WeChat runtime-parent WHERE to compare physical `tasks.id` instead of the projected public source ID. The same real public setup creates OA event1 and import job1. Target import job1 has physical Tasks ID2. Native source `runtime_operation_parent_is_runnable('memory','wechat_memory_import_job:1')` returnsTrue; the mutant returnsFalse. The exact mismatch is required by the harness, so successful normal comparisons are sensitive to the public/physical identity distinction.

## Limits and verification status

The needs-human reconciliation and failed supersession owners execute real SQL but have **no eligible approved-review or failed-task row** in this fixture. Their nonempty mutation branches are not proved. The Attention/History data is a standalone failed Attempt; full terminal mapping/audit/review feedback scenarios remain separate. There is no provider delivery, native Agent CLI, external business-map reader acceptance, nonempty copied migration, current-fact inverse, predecessor write/reforward, production cold selector, UI or final table-count proof here.

The fixed Python3.13 script run, Ruff and in-memory compile pass. Both record digests are `b06f4900d6a6a12684d48729b110b85c42db20cd85a28cdec6feb28a1040e865`. The negative control is caught; all task-owned copied code and databases were removed (`owned_temp_removed=true`). Application/test source, production databases, providers, network, main, push and deployment remain untouched by this author proof.
