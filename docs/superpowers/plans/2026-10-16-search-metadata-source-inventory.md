# Search metadata: frozen source and current caller inventory

Read-only inventory at `10867f5dc` (2026-10-16). The approved design at `docs/superpowers/specs/2026-10-11-database-redesign-design.md:109` retires the independent metadata table and persisted FTS, retains genuine metadata in runs/tasks, and keeps native-file search in RAM. Root owns the new typed runs kind and final migration/data choices. This document proposes no implementation or alias repair. Exact declarations, PRAGMAs, aggregate queries, hashes and all 20 direct Python test/helper owners are in the companion JSON.

## Fixed source facts

Source: `/private/tmp/ceo-business-combined-actual-u6i3ldg0/source.sqlite3`, SHA256 `970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`, schema marker `2026-10-13.5`. Opened only with `mode=ro&immutable=1` and `query_only=on`. No business bodies, titles or session strings were printed or persisted; numeric identity examples below establish the alias relationship.

| Source fact | Actual |
|---|---|
| Metadata rows / distinct sessions | 700 / 700 |
| Metadata IDs / allocator | IDs 1–700; `sqlite_sequence` row **present**, value 700 |
| Distinct source IDs | 698; all 700 values are canonical integer text |
| Source type | All 700 `meeting_alignment` |
| Pointer resolution | All 700 resolve by original **public** run ID to a meeting run and meeting job |
| Same session + same job title | 698 |
| Different session + different job title | 2; no mixed difference cases |
| Meeting runs | 2,565 total; 752 nonempty, distinct session IDs |
| Runs with source-ID membership | 698; **54** nonmembers |
| Runs whose session occurs in metadata | 700; **52** nonmembers; this is a different relation |
| Source-ID nonmembers by status | failed 27; ready_to_send 17; no_action 5; retry 5 |
| Timestamp facts | All 700 have TEXT created/updated values, both nonempty and equal within each row |

The seven genuine fields are `id INTEGER PRIMARY KEY AUTOINCREMENT`; `session_id TEXT NOT NULL UNIQUE`; `source_type`, `source_id`, `title` each `TEXT NOT NULL DEFAULT ''`; `created_at`, `updated_at` each `TEXT NOT NULL DEFAULT current_timestamp`. Preserve actual values, SQLite types, original IDs, defaults, unique identity and allocator presence/water. There are no table FKs or triggers. `idx_codex_session_search_source(source_type,source_id)` is nonunique; the separate SQLite autoindex enforces unique `session_id` with BINARY collation. A source ID cannot become a unique metadata key.

The five old cache fields—`summary_text`, `fts_text`, `embedding_json`, `embedding_model`, `embedding_updated_at`—are **physically absent** from this source. Historical source13 declarations have them as empty text caches; do not reintroduce them, claim there is current body storage to reclaim, or treat them as genuine metadata.

Two duplicated source-ID groups contain four metadata rows. Metadata ID 1 points public run 45 (physical 187789), while its stored session belongs to public run 12 (physical 187756). Metadata ID 2 points public run 60 (physical 187804), while its stored session belongs to public run 13 (physical 187757). Runs 12 and 13 have no source-ID membership. The other metadata row in each duplicated group matches its pointed run. These facts explain the 698-versus-700 distinction; they authorize no repair. Preserve all 700 memberships, their stored session/title, and the current mismatch result (`native_reference_unavailable`). Do not synthesize membership from all 752 runs or substitute a join by session.

The exact current FTS declaration is external-content FTS5 over **title only**, `content='codex_session_search_index', content_rowid='id'`. Four SQLite shadows exist: `_config(k PRIMARY KEY,v) WITHOUT ROWID` (1 row), `_data(id INTEGER PRIMARY KEY,block BLOB)` (16), `_docsize(id INTEGER PRIMARY KEY,sz BLOB)` (700), `_idx(segid,term,pgno,PRIMARY KEY(segid,term)) WITHOUT ROWID` (14). Docsize IDs are 1–700 with no metadata orphans. Declarations are recorded exactly in JSON; no shadow row bytes were exported.

## Current behavior that the replacement must preserve

`app/store.py:24309` upserts by exact session identity. A stripped-empty argument is a no-op; a nonempty argument itself is stored without normalization. Insert uses native ID/time defaults. Update preserves ID/session/created time, changes source/title, and sets updated time. Legacy summary/FTS/vector parameters are already ignored. The method currently deletes old persisted title tokens and inserts new ones; that persisted FTS maintenance retires with the independent FTS.

`search_codex_sessions` (`24340`) selects **metadata members**, ordered by updated time descending. Only meeting members resolve runs, using the public source ID projection and job join, then `latest_standalone_ref`. `_native_meeting_search_corpus` (`24472`) checks source type, run/ref, and exact stored session equality before reading original native input/decision. Any unavailable path retains title-only tokens and the exact unavailable reason. It never substitutes the job's persisted source body.

Search builds a disposable `:memory:` FTS5 corpus, optionally embeds available native summaries, scores `.55 * embedding + .30 * normalized BM25`, sorts stably, and slices to `limit`. BM25 candidate cap is `max(limit*5,10)`. RAM corpus/vector caches depend on the current metadata identity, DB path, source/session/range, native path/stat, meeting identity/title, and embedding model identity. Preserve these native/ranking/cache behaviors; the approved change is their metadata source.

The **sole application writer** is `_index_meeting_codex_session` (`app/meeting_alignment.py:1014`), called at `1467` after a ready-to-send run is recorded. It uses stripped `runner.last_session_id`, original public `run_id` as source string, and `source.title`. It is not a blanket writer for every meeting run. Similar-meeting prompt search calls Store at `802`; History calls Store at `app/audit_web.py:6300` before opening its read snapshot, preserving links and unavailable-title display.

One remaining maintenance statement at `app/storage_maintenance.py:173` rebuilds persisted search FTS. Remove that statement upon retirement; retain the surrounding typed-event compaction, transactions, counts, integrity/idempotence, vacuum and WAL behavior. Current auxiliary construction and current validation in `app/task20_current_schema.py:72,82,125`, plus `AutoReplyStore` bootstrap, must change together. Keep the historical source13 factory/fixtures explicit historical sources.

## Finite test impact and positive controls

Direct references comprise 14 tests plus 6 helpers (including nested helper `match`); JSON records exact spans. Current-target assertions needing storage adaptation are:

| File / test owner | Preserve or adapt |
|---|---|
| `test_store.py::test_store_indexes_and_searches_codex_sessions_with_fts_and_embeddings` | Existing native Chinese ranking, exact result members, cache hits, native-file refresh, model refresh, source-body non-substitution, unavailable reason; replace independent table/FTS assertions with typed metadata and retired-object absence |
| `test_meeting_alignment.py::test_consumer_injects_similar_codex_sessions_into_meeting_prompt`; `test_consumer_indexes_completed_meeting_codex_session` | Prompt membership/title-only behavior; completed writer stores original public run ID, metadata title/times and seven-field content |
| `test_audit_web.py::test_history_search_shows_similar_codex_sessions`; `test_history_object_dropdown_controls_results` | Native search before snapshot, result/source links, filters, native-missing title-only notice |
| `test_native_standalone.py::test_meeting_legacy_complete_range_is_read_without_attempt` | Exact ref policy; failed/running latest attempts keep search `native_reference_unavailable` |
| `test_native_only_maintenance.py::test_native_only_cleanup_removes_missing_native_copies_preserves_service_state` | Convert direct metadata/FTS setup/assertions; retain all ordinary compaction, business result, state, WAL/idempotence assertions |
| `test_workbench_storage.py::test_current_bootstrap_removes_only_approved_sources_and_caches`; `test_metadata_upsert_changes_only_title_tokens_and_preserves_metadata`; `test_native_compaction_uses_current_typed_events_and_title_cache` | Current bootstrap no retired table/FTS/shadows; typed metadata ID/created-time preservation; public title search after update/compaction; adjacent event behavior |
| `test_task20_current_schema.py::test_fresh_factories_converge_with_exact_migration_target` | Remove stale auxiliary names from current target expectations; its present loop actually compares only tasks/feedback declarations, so add real replacement metadata/schema proof |

Historical owners retain their historical roles: `test_workbench_storage.py::test_forward_inverse_exact_rows_constraints_sequences_and_known_orphan`, `test_inverse_restores_exact_fts_shadow_state`, `test_workbench_schema_release.py::test_fts_inverse_keeps_mixed_original_index_state_and_new_metadata`, `fts_state`, and the historical constructors/fingerprints in `tests/workbench_schema_fixtures.py` / `tests/workbench_caller_fixtures.py`. `tests/fixtures/store13-workbench-schema.json` is original declaration evidence. Do not mechanically rewrite historical source oracles as the new current contract.

Required new positive controls: (1) indexed member versus equally searchable nonmember; (2) two metadata aliases sharing a source ID, exact stored session/title and original mismatch reason; (3) all seven metadata values/types plus source allocator presence/water, insert/update/delete/current-fact inverse; (4) native-file/body/range/model cache refresh and unavailable-title search; (5) retired table/virtual/shadow absence after cold bootstrap/reopen/search/maintenance; (6) current inverse title MATCH positive and stale/deleted title MATCH negative. These controls are specified, **not executed** in this read-only task.

## Inverse evidence boundary

`scripts/validate_task20_runtime_copy.py:55` inventories ordinary facts only where `pragma main.table_list` reports `type == 'table'`; virtual FTS and shadow rows are outside those digests. Its declaration list still covers all non-SQLite schema objects; sequence rows are compared separately. Therefore source declarations and seven-field current metadata facts must round-trip, while a **title-only logical FTS rebuild** needs explicit MATCH checks. The existing checker does not prove shadow BLOB equality or token correctness merely by passing ordinary facts/declaration/integrity checks.

The earlier `app/workbench_storage.py:585,861` implementation captures/restores raw shadow rows and reconciles changed metadata against original indexed terms for the source13 Workbench inverse. That is a different historical contract. The new inverse should recreate current seven-field metadata/source index/unique/FTS declarations, restore current metadata and original allocator facts, and rebuild title FTS logically. Do not reuse raw shadow snapshots, retain retired cache bodies, or claim byte-exact shadow restoration.

Only these two new documents and this agent's claim row were written. No application/tests edit, tests/constructor execution, database write, provider/native CLI/network call, production access, stage, commit, push or deploy occurred. No task-owned temporary files or databases were created.
