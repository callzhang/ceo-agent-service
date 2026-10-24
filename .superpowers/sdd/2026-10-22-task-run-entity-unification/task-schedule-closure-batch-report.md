# Task 4 schedule occurrence closure — implementation report

Status: **implemented in the isolated candidate; independent full-base SPEC/QUALITY review and publication remain pending.** No constructor/manifest activation, production operation, provider call, push, or deploy was performed.

Base: `48ef2164f931a68de6b98836d7767f83d23e9602`. Runtime checkpoint: `d3e09e0a8`. Implementation commit: `f99294e8a`.

## Behavior delivered

Scheduled occurrence CRUD, dedupe reads, versioned creation, full-state claim/lease, pending dispatch, dispatcher linking/finishing, dashboard queue, recovery, and reached History/Attention reads use `schedule_occurrences`. Frozen configuration remains in `configs` under the existing public snapshot ID. Existing authorization, approval, retry, cancellation, acknowledgement, dispatch, and Task adapter semantics are unchanged. The full-state claim index still covers every dispatch state; the partial dispatch index still covers only pending occurrences. Covered-range creation checks the same frozen schedule snapshot before creating a tick. History renders persisted `first_scheduled_for`, latest `scheduled_for`, and `occurrence_count`; it does not infer effects from generic summaries.

Every registered command implementation now returns one `ServiceCommandResult`. Complete typed counts exist only in memory; `result_summary` retains the existing summary string. Unknown, busy, paused, maintenance, disabled, and unavailable paths return incomplete results without counters. Email, WeChat, DingTalk producers, OA/Todo scanners, Meeting scan, and Minutes access/sync return counts from their existing single operation. Recovery remains typed but does not become a historical empty-run grammar. No result-shape fallback dispatches a command twice.

After a successful command is linked and marked dispatched, Store aggregation uses a single writer transaction. It requires complete zero counters for the current run; a complete frozen historical zero summary for the immediately prior occurrence; the same Task, immutable snapshot, and command; exact Cron adjacency; and no declared TaskInput or Meeting reference on either occurrence. The newer event/occurrence identity survives while its first time and count are updated; the prior identity-map entry and occurrence are deleted. An aggregate count alone is not evidence that its prior producer had zero effects. Current JSONB `parameters_json` is read through SQLite `json()` and its nested `source_json` codec. Chronological predecessor selection uses `julianday(scheduled_for)` plus the occurrence-ID tie-break and has a matching expression index.

Historical retirement is part of `migrate_run_entities`' caller transaction. After declared refs map to native owner IDs, it builds one protected-ID set from all TaskInput and Meeting source references, then reads native occurrence/config rows once and performs a pass ordered by actual UTC instant and ID. Missing configuration, invalid timestamps, manual/intervening/active/failed rows, references, incomplete summary grammars, nonzero effects, differing snapshots/commands, or nonadjacent Cron instants break eligibility. It returns scanned, candidate, excluded, and retired counts without adding persistence fields. Old recovery/memory-write and scan-meetings summaries are excluded because their counts are incomplete. Frozen Email historical summaries require all `accounts`, `discovered`, and `failures` fields to be present, unique, and nonnegative; `accounts` is scan input, so only `discovered` and `failures` determine zero work.

Declared occurrence IDs in full TaskInput, compacted provenance, raw/context trigger payloads, and nested Meeting source JSON translate only the integer leaf token through the captured source-public/native map. Forward and current inverse preserve all unrelated source bytes/whitespace and the stored original hash/byte evidence; no body copies or runtime legacy resolver were added. A regression covers a public-ID/native-ID collision in both directions and portable forward/inverse cycles.

The pre-migration quiet window is handled by `RepositoryUpdater.wait_for_quiet` and the old-schema source checks in `RepositoryUpdater.current_publication_sources`; it is not the `consumer_system_release` module. `RepositoryUpdater.execute` orders `wait_for_quiet` → `prepare_database` → `database_release.publish` → optional `prepare_consumer_system_contracts`. The latter asset publication currently imports the explicit frozen `SCHEDULED_RUN_SQL` from `app.run_storage`; it runs after database release, so the next publication batch must switch its source-side query/Task read to the target owner. Do not change the quiet-window oracle, add a normal runtime source fallback, or activate the global constructors/manifest in this batch.

## Verification and boundaries

- `pytest -q tests/test_scheduled_occurrence_native.py tests/test_scheduled_occurrence_refs.py tests/test_run_entity_schema.py::test_forward_migration_coalesces_only_complete_adjacent_historical_empty_ticks tests/test_run_entity_schema.py::test_scheduled_consumer_input_references_follow_occurrence_owner_through_collision_inverse tests/wechat/test_scheduled_command.py`: **21 passed**. These use the assembled native Store where applicable and cover native CRUD/aggregation, covered-instant dedupe, transaction rollback, contention, typed refs/collision/inverse, strict old summary grammar and typed registry rejection of a legacy string result, historical sweep, WeChat unknown/complete result behavior, and both current/prior Meeting-reference protection.
- Historical sweep regression scans ten source occurrences in one migration, counts three complete unreferenced historical zero candidates, excludes seven (manual/missing snapshot, positive output, unknown recovery grammar, and both endpoints referenced by full/compacted TaskInput and Meeting JSONB), and retires only one proven pair. Its timestamps deliberately use differing offsets whose text order conflicts with UTC order. A nonzero event is a continuity barrier. It then performs Task/Run inverse and two JSON-portable composed forward/inverse cycles, preserving the retained aggregate interval while never resurrecting the retired source tick.
- Native query-plan regression uses 8,000 terminal rows and checks the claim, latest/page, reply execution, covered instant, and chronological predecessor indexes. The measured pending lookup remains below the test's 0.25-second bound; actual plan output is asserted against `idx_schedule_occurrences_claim` and `idx_schedule_occurrences_task_chronology` (latest/page may use the integer primary key).
- `pytest -q tests/test_email_scheduled_command.py::test_scheduled_loader_widens_only_the_history_embedding_deadline`: passed. The scheduled Email integration case was also run and failed at its storage boundary. A bounded one-call wrapper captured the actual inner exception: `sqlite3.OperationalError('no such table: email_classification_inputs')`; the command then reported one account failure. This is not evidence of a typed-result regression and is not called green. The exact missing table is recorded without attributing an unproved cause; final publication must run through the complete target-schema predecessor before this integration path can be accepted.
- The previously run legacy source-schema cron set reported **35 failed / 43 passed** because its fixtures have not been migrated to `schedule_occurrences`; this exact result is retained as a non-green fixture boundary and was not rerun. The scheduled-consumer legacy fixture also imports the removed `SCHEDULED_RUN_SQL` owner from `app.store`; it was not given a runtime fallback.
- `npx vitest run src/pages/ScheduledTasksPage.test.tsx`: **40 passed** after installing the checkout's existing lockfile dependencies with `npm ci`; no lockfile change was made.
- Ruff over every changed Python file, `py_compile` for changed Python modules, and `git diff --check` pass. The independent root SPEC/QUALITY review is still pending.

## Files changed

- Runtime/schema: `app/run_entity_definitions.py`, `app/run_entity_schema.py`, `app/scheduled_occurrence_storage.py`, `app/store.py`, `app/agent_cron/commands.py`, `app/agent_cron/consumer.py`, `app/cli.py`, `app/email_scheduled_command.py`, `app/wechat/scheduled_command.py`.
- Tests: `tests/test_run_entity_schema.py`, `tests/test_scheduled_occurrence_native.py`, `tests/test_scheduled_occurrence_refs.py`, `tests/scheduled_command_results.py`, and typed-result fixture changes in `tests/test_agent_cron_seeds.py`, `tests/test_email_scheduled_command.py`, `tests/test_email_task_producer.py`, `tests/test_meeting_alignment.py`, `tests/test_scheduled_agent_consumer.py`, `tests/test_task_scanners.py`, `tests/test_worker.py`, `tests/wechat/test_producer.py`, `tests/wechat/test_scheduled_command.py`.
- UI/docs: `frontend/src/pages/ScheduledTasksPage.test.tsx`, `docs/architecture.md`, `docs/runtime-mechanism.md`, `docs/agent-claims.md`, and this report.

## Self-review

The focused output preserves source/current identity separately: public occurrence IDs in declared Task-owned leaves map through the captured owner map, while unrelated JSON text, hashes, and bytes remain untouched. Migration history checks use one incoming-reference prepass plus a single chronologically sorted occurrence list; no per-occurrence full-reference scan is performed in that sweep. Current coalescing requires independent persisted proof for its prior row and refuses to infer emptiness from an aggregate count. A prior measured fixture regression showed the public/native collision could resolve a Task reference to the wrong native event; the added collision and current-inverse test verifies exact owner restoration and portable cycles. Full constructor/manifest cutover, frozen-source publication coordination, and the missing Email test schema are retained as explicit acceptance boundaries.

## Remaining release boundary

This is not an activated schema release or a claim of live behavior. Full independent BASE-to-HEAD SPEC/QUALITY review, the approved publication predecessor/frozen-source coordination, and the actual target migration are still required before constructor or manifest selection. No production command counts were replayed for this work.

## Fix round 1/5 — mixed-offset covered range and frozen import ownership

The review finding was reproduced before the fix against the assembled target/native Store. The persisted aggregate used `first_scheduled_for='2026-10-22T03:00:00+02:00'` and `scheduled_for='2026-10-22T01:01:00Z'`; create-if-current for the covered instant `2026-10-22T01:00:30Z` incorrectly inserted a second occurrence because the prior TEXT predicate rejected the interval start.

RED command and output:

```text
pytest -q tests/test_scheduled_occurrence_native.py::test_restarted_create_dedupes_mixed_offset_covered_instant
F                                                                        [100%]
...
assert restarted.state is ScheduledTaskRunCreateState.DUPLICATE
E AssertionError: assert <ScheduledTaskRunCreateState.CREATED: 'created'> is <ScheduledTaskRunCreateState.DUPLICATE: 'duplicate'>
1 failed in 1.19s
```

A separate import probe reproduced the stale frozen SQL owner after Task4 removed the Store export:

```text
python -c 'import app.consumer_system_release'
ImportError: cannot import name 'SCHEDULED_RUN_SQL' from 'app.store'
exit_code=1
```

The range predicate now compares `julianday(first_scheduled_for)` and `julianday(scheduled_for)` to the candidate instant, retaining the original timestamp text in storage. The new `idx_schedule_occurrences_covered_range` partial expression index uses the same expressions and limits entries to scheduled aggregates (`occurrence_count>1`). The existing 8,000-row native Store plan regression names/asserts that index for the covered-range query. The import now obtains `SCHEDULED_RUN_SQL` from `app.run_storage` and `REPLY_TASK_SQL` from `app.task_storage`; it does not restore a Store alias or add a fallback. The new import regression verifies the exported constant identity.

GREEN command and output:

```text
pytest -q tests/test_scheduled_occurrence_native.py::test_restarted_create_dedupes_mixed_offset_covered_instant tests/test_scheduled_occurrence_native.py::test_occurrence_crud_and_dispatch_use_native_owner tests/test_consumer_system_release_import.py
...                                                                      [100%]
3 passed in 4.85s
```

The mixed-offset regression uses the actual assembled Store and versioned create-if-current API; it verifies the covered event ID/count remain the same, the row count stays one, and both source timestamp strings remain byte-for-byte unchanged. The existing native owner test contains the 8,000 terminal-row population and asserts `idx_schedule_occurrences_covered_range` in the actual `EXPLAIN QUERY PLAN` result, alongside the existing claim/latest/execution/chronology plan checks. The earlier **35 failed / 43 passed** legacy source-schema cron result remains a non-green fixture boundary; it was not rerun and is not counted as green evidence.

Publication order correction: `RepositoryUpdater.execute` runs `wait_for_quiet`, `prepare_database`, `database_release.publish`, then optional `prepare_consumer_system_contracts`. The quiet-window source check is `RepositoryUpdater.current_publication_sources`; Consumer/Audit asset publication occurs after database release. Its frozen query is therefore not the pre-migration quiet-window oracle and must be switched to the target owner in a later publication batch. No quiet-window source, manifest, or constructor activation was changed here.

Final focused verification after reopening the same target/native Store from the persisted mixed-offset database:

```text
pytest -q tests/test_scheduled_occurrence_native.py tests/test_consumer_system_release_import.py
.........                                                                [100%]
9 passed in 6.77s
ruff check app/store.py app/run_entity_definitions.py app/consumer_system_release.py tests/test_scheduled_occurrence_native.py tests/test_consumer_system_release_import.py
All checks passed!
python -m py_compile app/store.py app/run_entity_definitions.py app/consumer_system_release.py tests/test_scheduled_occurrence_native.py tests/test_consumer_system_release_import.py
# exit 0
 git diff --check
# exit 0
```

Self-review: covered detection now uses instant semantics on both persisted bounds and the candidate; the regression reopens the same native database before asking create-if-current, verifies no duplicate identity appears, verifies original timestamp strings and interval count, and uses the existing large-population `EXPLAIN QUERY PLAN` assertion for the new partial expression index. The optional publication import now points at the modules that own the frozen SQL constants. No scheduler policy, source quiet-window oracle, global constructor, or manifest behavior changed. The separate consumer-system publication remains a target-owner follow-up after database release.
