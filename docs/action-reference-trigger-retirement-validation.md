# Retire the dangling Action public-reference trigger

Status, 2026-10-25: deployed at `bf27bfaeb75488c0535a6a99e6020428f214740c`.
Local code, review, current-data copy migration, inverse, real old-code cold
read/write, reforward and production publication/readback passed. Temporary-copy
and verified-backup cleanup remain blocked by automatic approval policy.

## Verified root cause and scope

The exact production Store25.1/Email54 declaration contains
`trg_actions_public_reference_update`, inherited from the old candidate owners.
Its body still names `predecessor_review_id`, `candidate_id`, `review_id` and
`selection_id`, which are absent from the current `actions` declaration.
`execution_id` still physically exists and has its own FK and parent guards;
those declarations are retained. The production inspection found no non-NULL
values in that column; this observation is not used to delete any row or field.

All four reported tests failed before the fix with
`error in trigger trg_actions_public_reference_update: no such column: predecessor_review_id`:

- `test_work_input_provenance_schema_is_checked_on_store_reopen`;
- three cases of `test_current_meeting_memory_lease_declaration_rejects_malformed_column`.

The failing operation is `ALTER TABLE ... RENAME COLUMN`, including renaming a
column on another table. A direct `ADD COLUMN` succeeds in the installed SQLite;
`EXPLAIN UPDATE actions SET legacy_key=legacy_key` fails before the fix. Removing
this one trigger in an isolated in-memory schema makes the rename reparse pass.
Thus the defect also blocks future schema renames and public-key updates; four
red tests are the observed test footprint, not the full impact boundary.

## Exact release contract

Store25.1/Email54 → Store25.2/Email54 retires only
`trg_actions_public_reference_update` and changes the Store version marker.
It adds no business field and rebuilds no table. Existing FKs, other triggers,
indexes, rows, IDs, payload types, receipt references and allocation sequences
remain identical. The strict source declaration is the new target plus the
exact original SQLite-normalized trigger SQL.

`ActionReferenceTriggerRelease` implements the deployer's
`publish / verify_loaded / rollback / finalize` protocol. Publish and inverse
run in immediate transactions and check the exact source/target schema, marker,
Email version, original FK-finding set and quick_check. Inverse restores the
original trigger and Store marker timestamp while preserving current rows.
Deployment quiet readers accept only the immediately previous Store25.1.
Ordinary runtime does no read-path repair. The old A5b explicit tool is pinned
to its original Store25.1 declaration and no longer selected by current deploy.

## Evidence

The focused selection passed **51 tests in 21.88 seconds**: the new release
and schema-reparse tests, the four original failures, A5b source/target/inverse,
Email54 publication and state binding, and deployment-drain coverage. The new
tests cover malformed marker, Email version, missing/changed source trigger,
no-write refusal, exact source clock, new facts surviving inverse and reforward.
Ruff and diff checks pass. A bounded independent Luna source review approved;
it did not duplicate the tests.

A new SQLite Backup API snapshot of current production was validated in
`/tmp/ceo-action-trigger.JOEnAc`, not in the live database. Source size was
514,969,600 bytes and it contained 49 tables including sqlite_sequence. Streaming
digests included each persisted value with its SQLite type and all allocation
sequences; only the Store version-marker row was excluded from the data
comparison because its update is the approved migration effect.

Forward preserved every table's data digest and sequences. After forward, real
Store/Action APIs created a Task and a substitute-only Action/receipt. Inverse
preserved those new facts and restored the exact source schema/marker clock.
The real old app code archived from deployed commit
`4bae03e41e4d67aa8b759e5d782db4cea784be59` then cold-started Store and EmailStore,
read the new Task and receipt, and wrote another Task and Action/receipt.
Reforward and current-code reopen preserved all these current facts. The final
schema rename probe passed, quick_check was OK, and the original sole FK finding
`('runs', 190042, 'tasks', 1)` remained unchanged (the last value is the FK
constraint ordinal). External sends were zero. No VACUUM or file-size reduction
is claimed.

## Production readback and cleanup

The standard deploy returned `deployed 4bae03e4 -> bf27bfae`. Production PID
19597 is running and health is OK. The exact current manifest matches
Store25.2/Email54, the retired trigger count is zero, all 46 A5b receipt references
remain, quick_check is OK, and the sole original FK finding is unchanged.
Read-only `EXPLAIN UPDATE actions SET legacy_key=legacy_key` now compiles on
production. No production schema probe or outbound test action was executed.

Queue readback was pending 6 / processing 0 / failed 26 / retryable 0 / Attention
5, with the failed total unchanged from the pre-deploy snapshot. Attention
returned four groups, History returned 20 items, and Attempt15369 remained
`done` with its referenced provider result visible. Production size remained
514,969,600 bytes.

The latest standard backup passed its complete marker and quick_check; it is
`/Users/derek/Library/Application Support/ceo-agent-service/backups/auto-reply-2026-10-25.sqlite3`
(491 MiB). The task-owned validation root is `/tmp/ceo-action-trigger.JOEnAc`
(508 MiB), with no open database files. Derek had authorized post-readback
cleanup. The exact rm command was rejected before execution by automatic
approval policy: “approval required by policy, AskForApproval is Never”. Both
paths remain, and no alternate deletion mechanism was used. The earlier A5b
copy cleanup blocker is separately recorded in the business-model progress page.

## Renewed cleanup attempt — 2026-10-25

Derek explicitly authorized both current and previously retained test artifacts.
Fresh inventory: 10 directories, one verified workflow backup and four API
outputs, totaling 11,346,504,616 logical bytes (10.57 GiB). All directories are
free of symlinks; combined lsof found no open files; backup quick_check is OK.
The exact rm command was rejected before execution by platform automatic
approval (`approval required by policy, but AskForApproval is set to Never`).
Post-attempt checks confirmed every target below still exists. No files were
removed, no alternate deletion mechanism was used, and production health is OK.
Authorization remains valid; cleanup is pending platform execution permission.

Explicit directory targets:

```text
/private/tmp/ceo-business-model-20261025.1qvjsC
/private/tmp/ceo-action-trigger.JOEnAc
/private/tmp/ceo-a5b-copy-pl_1udbh
/private/tmp/ceo-a5b-copy2-lru2bzml
/private/tmp/ceo-a5b-copy3-n83k8pvy
/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-a5b-diff-24yohzpc
/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-a5b-projdiag-3382srw3
/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-a5b-release-copy-5e_sfrx5
/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-a5b-roundtrip-bxz4xgph
/var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-a5b-roundtrip-final-9h6zrq21
```

Explicit file targets:

```text
/Users/derek/Library/Application Support/ceo-agent-service/backups/auto-reply-2026-10-25.sqlite3
/private/tmp/ceo-prod-status.json
/private/tmp/ceo-prod-attention.json
/private/tmp/ceo-prod-history.json
/private/tmp/a5b-detail.json
```

The unrelated `backups/dws-doc-jsonml-87ddde41` directory, production database
and native trajectories are outside these deletion targets. Historical test
paths from earlier phases not found in the bounded inventory were not treated
as present or as successfully deleted. No broad pytest/cache cleanup was run.
