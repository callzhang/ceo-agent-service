# Database Redesign Phase3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Execute approved tasks continuously; preserve source bytes and business facts when a legacy projection differs.

**Goal:** Keep one canonical current/claimed reply input and provenance-only retired input; prevent future Task WorkItem envelopes copying unused frozen Skill material; prove matched main-schema rollback before normal deployment.

**Architecture:** reply_task_inputs owns payload/provenance. reply_tasks references current_input_id/claimed_input_id but retains real identity/queue/generation/version watermarks. Work-summary schema remains because its terminal cleanup is already complete and active recovery fields are necessary. Store schema2026-10-10.2→2026-10-11.1 receives a dedicated inverse; Email remains49.

**Tech Stack:** Python, sqlite3 (actual runtime supports DROP COLUMN), existing Store/Dispatcher/Email contracts and schema-publication deploy.

## Task1: canonical reply input storage, all current writers/readers

Files: app/store.py, app/dispatcher/adapters.py, app/agent_cli.py, app/audit_web.py, app/storage_maintenance.py, app/email_store.py (historical reply JSON update only), corresponding tests/test_task_store.py, test_store.py, test_consumer_dispatcher.py, test_storage_maintenance.py, test_frozen_task_skill_discovery.py, test_scheduled_execution_query_index.py, Email queue/adapter tests; new tests/test_reply_input_reference.py and explicit legacy fixture helper if required.

- [ ] Add regressions that fail against the current schema: removed parent payload columns, real current/claimed FK references, mismatched/no-input legacy tasks preserving current bytes and old rows, manual rerun/context refresh/card revisions/single-chat merge, different current+claimed input while processing, exact frozen Skill/history/unsubscribe source identity, retired body absent yet API/provenance identical.
- [ ] Run selected new tests with miniforge Python and --basetemp in task-specific temporary folder. Record RED before implementation.
- [ ] Implement main version constant2026-10-11.1 and main manifest column/index changes. Add the following real relation columns (initial NULL permits parent/input creation in one transaction):

```sql
alter table reply_tasks add column current_input_id integer references reply_task_inputs(id);
alter table reply_tasks add column claimed_input_id integer references reply_task_inputs(id);
```

Validate referenced input ownership at migration and DAO boundaries. Do not infer an input ID from input_version. Do not overwrite old revisions or change task watermarks during migration. Create an exact current canonical row only if the existing current projection has no complete matching input row; its deterministic migration revision key includes actual task identity and snapshot digest. Preserve all other rows/ids. Legacy live claims must reach normal quiet before migration; terminal historical claimed pointers may remain NULL rather than fabricated.

- [ ] Implement a narrowly matched old-store migration before generic initialization/backfills when old marker2026-10-10.2 and old input shape are healthy. Fresh/older initialization still reaches current shape, but must not re-add removed parent columns/index after canonical schema exists. Use a transaction for source DDL/data/version stamp; version written only after structural/data validation.
- [ ] Remove four parent columns only after exact canonical-current validation. Drop the old JSON partial index before DROP COLUMN; keep channel/status/id and business identity lookup. Reproduce original unsubscribe/scheduled query costs with EXPLAIN and focused index tests; add no speculative subtype field.
- [ ] All mutation paths write canonical snapshot plus pointer in the existing transaction. Preserve actual input_revision_key/manual_rerun_revision_key/execution_generation semantics. Capture claimed_input_id when claiming; execution-bound readers use its exact snapshot for that generation, current UI readers use current_input_id.
- [ ] Retired input persistence uses trigger_text='' and trigger_message_json='', input_compacted=1 plus existing provenance. Public projection generates the prior compact JSON via existing _compact_reply_input_json; raw subtype/history SQL uses provenance.source for compacted rows. Compare old generated compact representation before deletion; never treat missing original as recoverable.
- [ ] Define AutoReplyStore.restore_reply_task_input_schema(db, *, original_tasks_sql, original_indexes) for release inverse. Rebuild parent using its captured exact CREATE declaration (trigger_text originally has no DEFAULT, so ADD COLUMN with a new default is not an exact inverse). Populate four legacy columns from current canonical rows, restore old compact input JSON, remove two structural pointers, restore captured indexes and preserve task/input sequences. Caller owns FK-off transaction and compares full pre-existing FK violation fingerprint before/after. Restore no old body backup; keep post-start writes and original input identities.
- [ ] Run focused regression selection covering all above paths. Repair only explicit old-schema fixtures, never weaken business assertions or add runtime fallback. Ruff/diff checks. Update behavior docs after tests pass.

## Task2: work-summary future envelope slimming

Files: app/task_scanners.py, tests/test_task_scanners.py, tests/test_task_agent.py; existing WSI retention tests.

- [ ] Regression: a ServiceCommandConsumerContext with a large skill_materials content creates a WorkItem whose actual Task prompt is byte-identical to the full-context WorkItem and whose stored scheduled context retains schema, prompt, skill names, IDs, required protocol/source but omits materials. Original shared context remains unchanged. Existing active/failed envelopes and terminal provenance remain unchanged through schema migration/inverse.
- [ ] In only Task WorkItem construction sites, use a copy of context.to_payload() and remove skill_materials. Keep skill_protocol because existing from_payload validator requires it. Do not change shared Consumer/commands builder, current Task instructions, selected Skills or existing rows.
- [ ] Run scanner/prompt regressions and WSI completion/processing/failure/recovery/history/semantic import tests. Existing9080 terminal rows remain exact; do not claim new bulk cleanup or rewrite old hashes. Record generated new-envelope byte reduction separately.

## Task3: matched Store release and rollback wiring

Files: new app/store_schema_release.py; app/deploy.py, app/repository_updater.py, app/cli.py schema factory wiring only; tests/test_store_schema_release.py and existing schema/publication entrypoint tests. Root owns these files.

- [ ] Add known2026-10-10.2→2026-10-11.1 prepare/publish/inverse tests with post-migration new current input+claimed pointer, new revision and external_action_results receipt; exact old field declarations/indexes/version marker/sequence and business rows persist. Include atomic failure midway through inverse and unsupported transition rejection.
- [ ] Factory prepare_database_schema_release delegates existing Email inverse unchanged when only Email changes, new Store inverse when only Store changes, and rejects an unverified simultaneous transition. Migration subprocess initializes target AutoReplyStore, then verifies marker, two refs and four removed columns. Inverse transaction restores from current canonical data using Task1 helper, validates full FK fingerprint unchanged, and restores old version. Failure must keep replacement stopped under existing publication framework.
- [ ] Wire all three updated deployment entrypoints to the new factory without constructing Store before quiet/stop/backup. Keep existing recovery/authorization behavior and stop→inverse→old checkout/start sequence.
- [ ] Run release/publication/deploy-maintenance tests, including actual older source startup on inverse copy. No new external reconciliation policy.

## Task4: copy rehearsal, review, deployment and cleanup

- [ ] SQLite backup API to /tmp/ceo-phase3-verification.FGNJ00. Record actual source code/store+Email versions, original DDL, counts/statuses/payload byte counts and all-table digests. Use PK/rowid order, not expensive sorting on every body field. Never output private input bodies.
- [ ] Compare original task/API snapshots and original input IDs/source/header/provenance/raw-body semantics; enumerate newly canonicalized legacy snapshots explicitly. All other tables/business results/receipts and sqlite_sequence (except expected new input IDs) unchanged. Original compact representations derivable, active bytes exact, no new FK violation. Verify repeat startup no duplicate canonical row/JSON restoration.
- [ ] Add new revision/current/claimed coexistence, new work-summary active input and adopted result/receipt on the COPY ONLY; inverse preserves all new data. Load actual production-source old AutoReplyStore+EmailStore in a temporary package facade and verify old startup/API/requeue/claim/Skill/history. Re-forward and compare; measure copy-only VACUUM separately.
- [ ] Single independent high-risk migration/inverse reviewer audits code and actual copy evidence. Resolve concrete findings, rerun affected tests only. No broad test suite on live service host.
- [ ] Commit source+behavior docs, reconcile pushed main preserving other agents, push normal fast-forward and run updated python -m app.deploy. Use normal quiet route. Report actual completed installed commit (do not forecast it from an earlier fetch), PID, main version, Email49, canonical refs, health/queues/Attention/History, new failure IDs, latest single verified backup, logical/free/WAL/file sizes. No production VACUUM in this phase.
- [ ] Commit concise acceptance, release claims, delete exact task-owned temporary copy/code/logs/ledger, retain managed worktree for later phases and write confirmed continuation memory.
