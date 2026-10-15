# Follow-up and Todo-sync source map — preparation only

Captured at `2026-10-14T05:42:16.304289+00:00` through one read transaction on a `mode=ro` URI with `query_only=on`. Production code was `9a2a23fcaad6eb73f6131d5b7a70d22ae02418cd` before and after capture. The database reported Store `2026-10-13.4` and latest Email `51`; exact marker rows and original timestamps are in the JSON. Those stored timestamps are preserved, not interpreted as a new deployment time. Candidate navigation was captured at `406ec60cb8cd1a51a1af1745b69de309d610e348`.

This unit records the nine remaining physical families already assigned to `actions` by the approved 2026-10-11 design and field CSV. It supplies source contracts and caller/dependency evidence. It is **not** the eventual publication predecessor after E/Email, a target schema, a version allocation, a migration, a retention decision, a behavior change or authorization to activate anything. The complete 10+1-table program remains unfinished.

## Exact source inventory

All 150 actual columns are also present in the approved 1,611-field CSV: **zero additional and zero retired fields** for these nine families. The CSV is a direction map, not executable migration SQL. The JSON preserves literal CREATE TABLE, table_xinfo (type/notnull/default/PK/hidden), index SQL and index_xinfo (including rowid auxiliary entries, key flags, descending flags, collation and partial scopes), FKs, own triggers, native sequences and field-level aggregate metadata for both actual and genuine fresh-production source variants.

| Physical family | Fields | Rows in actual snapshot | CHECKs | Indexes including implicit | Outgoing FKs | sqlite_sequence |
|---|---:|---:|---:|---:|---:|---|
| `work_updates` | 9 | 5468 | 0 | 2 | 0 | 5468 |
| `follow_up_drafts` | 31 | 2477 | 0 | 6 | 0 | 2477 |
| `follow_up_send_attempts` | 16 | 65 | 0 | 3 | 0 | 65 |
| `business_task_follow_ups` | 18 | 0 | 2 | 2 | 2 | absent |
| `business_task_follow_up_send_attempts` | 12 | 0 | 1 | 2 | 1 | absent |
| `task_todo_sync_outbox` | 15 | 242 | 2 | 2 | 0 | 242 |
| `business_task_todo_sync_outbox` | 15 | 0 | 2 | 2 | 1 | absent |
| `work_todo_dingtalk_links` | 17 | 875 | 0 | 3 | 0 | 875 |
| `business_task_dingtalk_links` | 17 | 0 | 1 | 2 | 1 | absent |

There are 24 indexes, eight CHECK expressions, five outgoing FK declarations and zero owned triggers. Every family has `id INTEGER PRIMARY KEY AUTOINCREMENT`: nine independent integer ROWID-alias identity spaces and native allocation watermarks. There are **no** plain-ROWID-without-AUTOINCREMENT, composite-public-key, nullable-public-ID or WITHOUT ROWID variants in these nine. PRAGMA reports `notnull=0` for an integer PK; this does not make the public ID nullable. Explicit NULL/omitted id uses SQLite allocation. Empty business families have no sqlite_sequence row in the snapshot; absence is preserved separately from a recorded zero. No new public identity is designed here.

## Actual versus fresh production constructors

The fresh variant was constructed in a new subprocess from `git archive 9a2a23fcaad6eb73f6131d5b7a70d22ae02418cd app`, with cwd/PYTHONPATH pointing to the task-owned archive. `AutoReplyStore(Path(empty_db))` followed by `EmailStore(Path(empty_db))` built one synthetic empty database. It did not copy business rows or import the candidate implementation. Binding file hashes, markers and metadata are in JSON.

| Family | Observed variant difference |
|---|---|
| `follow_up_drafts` | Historical ALTER column order differs. Actual `updated_at` is NOT NULL DEFAULT empty string; fresh is NOT NULL DEFAULT current_timestamp. Named index/key semantics agree; the history-updated index has a trailing whitespace difference and index_list order differs. |
| `work_todo_dingtalk_links` | Actual appends `retry_count` after `updated_at`; fresh places it before `created_at` and `updated_at`. Column semantics, index SQL, FKs and triggers agree. |
| `task_todo_sync_outbox` | Actual skipped-state table rebuild quotes the table name. CHECK/column semantics agree; due-index SQL differs in whitespace. |
| Other six families | Literal table DDL, column order, foreign keys, index declarations and owned triggers agree. |

Both complete declarations remain in the JSON. These are source variants to account for later, not permission to normalize defaults, tighten constraints, sort columns, fill historical blanks or reconstruct an old source from a current candidate.

## Dependencies that bound a later unit

SQLite schema closure was enumerated over all sqlite_schema SQL and all declared FKs in the same source snapshot. Two incoming references touch the selected families: `business_legacy_links.work_update_id → work_updates.id` (external) and `business_task_follow_up_send_attempts.draft_id → business_task_follow_ups.id` (internal). The only external schema SQL object naming a selected table is `business_legacy_links`; there are no external views or triggers naming them. Exact external SQL, related parent declarations, related FKs and schema objects are included. Related declaration-replay xinfo is explicitly labelled as in-memory replay, distinct from nine-family source PRAGMAs.

The four business-family relationships to outside parents are `business_task_follow_ups.business_task_id → business_tasks.id`, its `source_signal_id → business_task_signals.id`, `business_task_todo_sync_outbox.business_task_id → business_tasks.id`, and `business_task_dingtalk_links.business_task_id → business_tasks.id`. Each uses immediate NO ACTION; the internal send-attempt FK does too. Source signals themselves reference `business_source_documents`; tasks have a self-merge FK. `business_legacy_links` also references the existing work/project/task/anchor/cluster/attention objects and constrains one old identity and one business identity per row. These are concrete coupling to later business/provenance consolidation, not permission to migrate those parents now.

Legacy relationships are also application contracts even where SQLite declares no FK: `work_updates.project_id`, `work_todos.created_from_update_id`, `follow_up_drafts.project_id/todo_id`, `follow_up_send_attempts.draft_id`, `task_todo_sync_outbox.work_todo_id`, and `work_todo_dingtalk_links.work_todo_id`. Store History/search and Todo completion projections join them to the existing work objects. A physical-table rename/removal must address these callers and the external legacy-link FK together. No configs FK exists among these nine; live owner resolution still reads org-user profiles, and Todo eligibility reads business-task/date evidence. That is runtime input coupling, not an invented config relationship.

Two different uniqueness contracts must not be silently unified. Legacy Todo links require a globally unique nonempty `dingtalk_task_id` and at most one creating/active link per work Todo; business Todo links declare only the latter per business Task. Business follow-ups have UNIQUE dedupe_key; legacy follow-up dedupe is an application query without a UNIQUE index. Both send-attempt families have UNIQUE claim_token but do not declare UNIQUE idempotency_uuid or UNIQUE (draft_id,draft_revision). Both outboxes have UNIQUE operation_key. The source JSON is authoritative for exact predicates and BINARY collation.

## Finite caller map

Paths/line anchors below refer to the captured candidate source unless explicitly marked production. Candidate/prod file hashes and separate AST inventories bind them to the corresponding commits; five candidate modules differ from production because the local E/Email work already exists (`store`, `audit_web`, `cli`, `email_store`, `quality_gate`). No claim of whole-file equality is made. AST method values passed into helpers are included; generic `_initialize` name collisions are excluded from the final navigation inventories.

| Family/path | Actual writers and execution consumers | Actual readers and boundary |
|---|---|---|
| `work_updates` | Store `create_work_update` (28107) inserts the adopted summary/change record and updates its work-project activity in the same connection. `todo_completion.close_todo_with_completion_evidence` (62), `task_noise_backfill` (163), `task_owner_backfill` (113) and explicit `task_project_repair.apply_manifest` (272) remain source writers. | Store `has_work_update`/`list_work_updates` (28140/28160), History `_history_items_query` (25500), operation logs (30578), `task_retrieval` (676/715), legacy Tasks UI/detail and CLI list updates. `task_semantic_import._plan_items` (238) reads update/source linkage and retains update disposition history_only; this is not a new formal Task. |
| Legacy `follow_up_drafts` + `follow_up_send_attempts` | Store create/update helpers remain (28885/28941); current app search finds no caller inserting a legacy draft or attempt. Backfill and legacy completion helpers can update the draft. Updating a draft increments revision, clears claim fields and invalidates a claimed historical attempt. No current sender or automatic due sweep exists for legacy drafts (`follow_up.py` module contract). | Draft list/get/recent/count helpers, legacy project/detail/sent-Todo and progress projections, History/search/logs, owner/noise/project backfills. Send-attempt fields remain historical source evidence; the only non-initializer table mutation found is claimed→invalidated in `update_follow_up_draft`. No active replay/repair route is inferred from old attempt rows. |
| `business_task_follow_ups` + `business_task_follow_up_send_attempts` | `TaskAgent.apply` (2249–2338) cancels outdated pending follow-ups and creates one from explicit next-check/source target in its domain transaction. Console send route `registration.py` (445) → `follow_up.send_business_task_follow_up` (24) → Store recover/claim/transition/finish (27529–27667). Task Agent completion and `todo_completion.complete_business_task_from_external_todo` close appropriate follow-ups. | `web_api.tasks.business_task_detail` (1251) passes Store list methods as values to `_all_pages` and exposes persisted fields. Store list attempts helper exists; no current app external caller was found. Console route verifies exact Task/follow-up and expected revision. `scheduled_at` is suggestion metadata, not a scheduled sending worker. |
| `task_todo_sync_outbox` + `work_todo_dingtalk_links` | Explicit deadline backfill can enqueue legacy intents (323); existing Store enqueue/claim/finish/retry/reconciliation and link CRUD remain. `TaskTodoSyncOutboxQueueAdapter` (1215) → CLI consumer (1349) → `todo_sync.dispatch_claimed_task_todo_sync_outbox` (921) → legacy Todo create/complete helpers, with source receipt and Dispatcher generation completion in one transaction. | Adapter metrics/direct SQL, Store lists/get/active lookup, legacy sent-Todo/Tasks/progress/History logs, quality gate and status snapshots. Current completed-Todo scanner explicitly excludes legacy links; their existence does not authorize restarting old completion scans. A leftover outbox retains its own delivery contract. |
| `business_task_todo_sync_outbox` + `business_task_dingtalk_links` | `TaskAgent.apply` creates committed create/complete intents. `BusinessTaskTodoSyncOutboxQueueAdapter` (1639) → CLI consumer (1367) → `todo_sync.dispatch_claimed_business_task_todo_sync_outbox` (850). `maybe_create_dingtalk_todo` (442) stores provider ID before readback; `scan_completed_dingtalk_todos` (610) only closes the exact linked business Task. `reconcile_unknown_business_task_todo_creates` (581) uses saved provider IDs. | Store queue/link readers and receipt settlement (27731–27888), Task detail (1252), sent-Todo endpoint (413), Task Agent completion link query (2278), external-completion proof query (`todo_completion.py:22`). Empty current tables do not prove a dead feature. |

Worker boundary: `cli.run_agent_cron_dispatcher_loop` registers both outbox adapters/consumers only outside dry-run. `dispatcher.service.ConsumerDispatcher.dispatch_available` submits a claimed envelope to per-adapter worker pools; it does not run these effects through `app/worker.py` Consumer/Audit execution. `app/worker.py` contains no direct nine-family SQL or Store caller found by the finite search. The outbox adapter names, source_id text representation and existing `dispatcher_claim_leases`/generation relationship are active inputs to preserve; no new ownership mechanism is introduced.

Attention/History closure: Store History includes adopted work updates and legacy follow-up records; operation logs include work updates, legacy follow-ups and legacy Todo links, with original public IDs and existing detail URLs (`web_api.registration.history_log_*`, lines 135–213; `history_types.py`, lines 63–66). `audit_web._queue_status_snapshots` (2568/2579) dynamically queries legacy follow-up/Todo tables; `_queue_attention_rows` (3374) still has failed legacy follow-ups in its dynamic spec. `history_actions.follow_up_history_attention` (169) offers details only for confirmed not-sent legacy failures. The quality gate checks legacy Todo links/outbox receipt failures (`quality_gate.py:824–860`) and requires the legacy follow-up table, but has no active legacy-follow-up backlog sweep. This source map records those actual distinctions without changing their policy or hiding failed history.

## Business results, provenance and active inputs

| Existing fields/facts | Proven role | Evidence and limit |
|---|---|---|
| `work_updates.summary`, `changes_json`, `merge_reason`, `confidence` | Adopted business summary/change facts. `changes_json` is heterogeneous, not a complete before/after log. | Store insert/list/History search and semantic import read the saved values. The approved design explicitly retains actual action/todo_changes/follow_up_drafts members; no target keys are invented. |
| `source_type`, `source_ref`, `project_id`, business `source_signal_id` | Existing source/object identity and provenance. | Importer locates the exact input from the update source pair; current follow-up creation checks the linked source signal and exact conversation. This is not proof the original external content can be re-fetched. |
| `revision`, claim token/UUID, lease owner/time, state, operation key, attempt count/backoff | Required existing lifecycle, identity, claim and recovery inputs. | Store CAS/claim/recovery, adapter SQL and CLI consumers use them. No unused-field/retention inference follows from zero current rows. |
| Draft question/target/owner and descriptive snapshots; work-link title/deadline/priority/executor snapshots | Adopted sending inputs and historical display facts, with current Task/owner/date checks where already implemented. | Follow-up sender, Todo create/readback helpers, sent-Todo/progress UI consume them. Current owner resolver reads stored org profile or provider profile. |
| `send_result_json`, attempt `result_json`, link provider ID / payload / last done/pull/push/error, outbox `receipt_json` | Existing application/provider receipt and business-status facts. | Completion and unknown-create settlement depend on exact saved provider identity; link ID and dingtalk_task_id are outbox receipt members. They are not replaced by a native Agent trajectory pointer. Artifact stores aggregate metadata only, never actual payloads. |
| Legacy attempt late-result/conflict/review fields and draft risk/evidence/reaction/claim fields | Historical facts retained by source; some draft fields also feed History/progress/search/backfills. | No current legacy sender/attempt inserter found. That observation neither proves all members are recoverable from native sources nor authorizes deleting them. |

These nine declarations contain no copied native transcript/body locator columns and no direct native trajectory reader was found in their scoped call chain. Their JSON can contain adopted results, provider payloads, evidence or descriptive snapshots. A field name or raw JSON value cannot establish that it is a redundant source copy. **No field is proposed for cancellation or retirement.** Whole JSON aggregate byte counts do not validate semantic contents or provenance availability.

## Full field names (actual source order)

- `work_updates` (9): `id`, `project_id`, `source_type`, `source_ref`, `summary`, `changes_json`, `merge_reason`, `confidence`, `created_at`.

- `follow_up_drafts` (31): `id`, `project_id`, `todo_id`, `owner_user_id`, `owner_name`, `target_conversation_id`, `target_kind`, `question_text`, `risk_check_json`, `status`, `send_result_json`, `scheduled_at`, `sent_at`, `created_at`, `evidence_check_json`, `reaction_status`, `reaction_summary`, `suppressed_reason`, `dedupe_key`, `updated_at`, `title`, `description`, `owners_json`, `priority`, `tags_json`, `participants_json`, `files_json`, `revision`, `send_claim_revision`, `send_claim_token`, `send_claim_idempotency_uuid`.

- `follow_up_send_attempts` (16): `id`, `draft_id`, `draft_revision`, `claim_token`, `idempotency_uuid`, `state`, `lease_owner`, `claimed_at`, `lease_until`, `result_json`, `review_enqueued_revision`, `review_source_ref`, `late_result_json`, `conflict_json`, `created_at`, `updated_at`.

- `business_task_follow_ups` (18): `id`, `business_task_id`, `source_signal_id`, `owner_user_id`, `owner_name`, `target_conversation_id`, `target_kind`, `question_text`, `scheduled_at`, `sent_at`, `status`, `revision`, `send_result_json`, `evidence_check_json`, `suppressed_reason`, `dedupe_key`, `created_at`, `updated_at`.

- `business_task_follow_up_send_attempts` (12): `id`, `draft_id`, `draft_revision`, `claim_token`, `idempotency_uuid`, `state`, `lease_owner`, `claimed_at`, `lease_until`, `result_json`, `created_at`, `updated_at`.

- `task_todo_sync_outbox` (15): `id`, `operation_key`, `work_todo_id`, `operation`, `evidence_json`, `status`, `lease_owner`, `lease_expires_at`, `receipt_json`, `error`, `attempt_count`, `next_attempt_at`, `created_at`, `updated_at`, `completed_at`.

- `business_task_todo_sync_outbox` (15): `id`, `operation_key`, `business_task_id`, `operation`, `status`, `evidence_json`, `receipt_json`, `error`, `attempt_count`, `lease_owner`, `lease_expires_at`, `next_attempt_at`, `completed_at`, `created_at`, `updated_at`.

- `work_todo_dingtalk_links` (17): `id`, `work_todo_id`, `dingtalk_task_id`, `executor_user_id`, `executor_name`, `title_snapshot`, `deadline_at_snapshot`, `priority_snapshot`, `status`, `last_dingtalk_done`, `last_dingtalk_payload_json`, `last_pull_at`, `last_push_at`, `last_error`, `created_at`, `updated_at`, `retry_count`.

- `business_task_dingtalk_links` (17): `id`, `business_task_id`, `dingtalk_task_id`, `executor_user_id`, `executor_name`, `title_snapshot`, `deadline_at_snapshot`, `priority_snapshot`, `status`, `last_dingtalk_done`, `last_dingtalk_payload_json`, `last_pull_at`, `last_push_at`, `last_error`, `retry_count`, `created_at`, `updated_at`.

## Evidence boundary and next binding

A later implementation must bind its actual published predecessor anew, account for the three source-variant families, preserve independent native sequence/identity and exact source constraints, and convert every finite caller plus business_legacy_links dependency in the approved order. That later work needs its own bounded implementation/validation ownership. This preparation introduces no target columns, migration SQL, new version, provider call or live action. See `docs/database-redesign-followup-sync-source-validation.md` for the metadata checks and cleanup receipt.
