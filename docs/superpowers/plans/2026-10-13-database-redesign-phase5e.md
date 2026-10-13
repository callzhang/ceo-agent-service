# Phase 5E Reply Attempt and Memory Event Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkboxes for tracking. This planning task does not authorize extra agents or reserve a runtime release.

**Goal:** Move the existing `reply_attempts` and `memory_write_events` facts into finite `actions` types, retaining public identities, allocators, business results and existing behavior while removing approved copied tool payloads.

**Architecture:** Add two finite record types with public IDs in their own integer namespaces. Preserve the two observed source variants explicitly, including the actual Memory scalar association versus the fresh constructor's parent FK. A paired inverse reconstructs current business facts and exact source declarations; removed native copies are not reconstructable and must not be recreated.

**Tech Stack:** Python, SQLite, existing `action_storage` typed projections and writes, Store schema publication, focused pytest, existing Console APIs.

---

## Status and execution prerequisites

This is an implementation plan only. No target DDL, migration, Store version, production action or retention deadline is implemented or reserved here. The approved destination is the full ten core tables plus the schema migration table, not completion of the redesign by this two-table unit.

The source preparation at commit `853abab3` and source-only oracle through `29ba1675` capture actual Store16 (`2026-10-13.2`) and an empty candidate17 constructor (`2026-10-13.3`). Actual capture: `2026-10-13T17:17:01.221303+00:00`, production commit `d7397203df678fd4137c237eb7eb84a671e084f3`. C PR77's final CI and normal publication, followed by D implementation/publication, remain prerequisites. Do not relabel the captured Store16 database as a published C or D source. E executes only after actual C/D publication and source refresh against the exact immediately preceding release. Source counts are observations, never constants in a migration.

The literal evidence is in [source JSON](../specs/2026-10-13-database-redesign-phase5e-reply-source.json), [source caller map](../specs/2026-10-13-database-redesign-phase5e-reply-source-map.md), and `tests/test_reply_attempt_source.py`. The approved [design](../specs/2026-10-11-database-redesign-design.md) and [D plan](2026-10-13-database-redesign-phase5d.md) define the surrounding typed layout. Existing native detail documentation mentions Codex/Claude/Friday globally; this E unit uses existing Codex session/slice references, as explicitly approved. It creates no other native source and copies no trajectory.

## File responsibilities

All paths below are relative to this worktree's repository root.

| File | Planned responsibility |
|---|---|
| `app/action_storage.py` | Two finite types; type-specific source-value handling, defaults, references, projections, indices and public AUTOINCREMENT namespaces; frozen prior-release contracts remain fixed |
| `app/store.py` | Exact-source migration/inverse integration, fresh schema, finite attempt/event CRUD and existing business methods; retire old initialization/ALTER paths for these two tables |
| New `app/action_reply_storage.py` | Exact two-table source capture, migration and current-fact inverse; follows the bounded `action_postfix_storage.py` pattern |
| New `app/action_reply_schema_release.py`, existing `app/store_schema_release.py` | E paired publication/inverse and dispatcher binding, following `ActionPostfixSchemaRelease`; prior/target manifests remain fixed; assign version only when implementing against published D |
| `app/codex_memory_write.py` | Event completion through finite actions write with existing eligibility/return behavior |
| `app/audit_web.py`, `app/web_api/attempts.py` | Status/Attention/History/detail sources, honest native availability and review-marker display |
| `app/cli.py`, `app/email_store.py`, `app/history_types.py`, `app/quality_gate.py`, `app/rule_question_retirement.py`, `app/scheduled_task_recovery.py`, `app/storage_maintenance.py` | Direct SQL and physical-presence assumptions; preserve current predicates and results |
| `tests/test_reply_attempt_source.py` | Remains the frozen source-only oracle; do not rewrite its expectations to fit target |
| New `tests/test_reply_attempt_action_storage.py`, `tests/test_reply_attempt_action_migration.py` | Target behavior and positive/inverse/new-fact checks, independently compared with source oracle |
| Existing focused test files named below | Caller, lifecycle, native detail, queue and page behavior |
| `docs/architecture.md`, `docs/runtime-mechanism.md` | Same-commit runtime/storage contract updates when behavior actually changes |
| New `docs/database-redesign-phase5e-validation.md` | Exact source/release/CI/copy/inverse/live readback and measured capacity evidence |

Do not add a generic serializer or overhaul unrelated action families. Existing source TEXT columns legally accept BLOB and other SQLite affinity outcomes; E needs a confined lossless representation only where its source contract allows them. A structural storage-class tag is encoding metadata, not a new business field. Existing raw business result strings remain strings, never parsed and reserialized as inner JSON. The E projection must restore exact values and `typeof`; the existing six/nine/twelve types and D delivery facts must remain byte/type equivalent. The current string-only `_check_fields` and JSON text checks are insufficient proof of that behavior.

## Exact field mapping

`actions.record_type` uses the exact names `reply_attempts` and `memory_write_events`; `legacy_key` carries the canonical decimal public `id`. The physical `actions.id` is internal and cannot replace either public ID, a History cursor, `manual_rerun_attempt_id`, an API Attempt ID, a Memory workload key or a Memory completion event ID. Source field names are retained in the finite type payload/projection unless a real source FK requires a physical reference. No candidate/task/run parent is inferred from title, text, time, generation or coincident integer IDs.

Every captured Attempt column appears exactly once below (58 actual, 54 fresh). “Keep” means preserve original value/storage class/default/nullability and the caller-visible field. The only copied-content retirement is identified explicitly.

| Source columns | Target and rule |
|---|---|
| `id` | Numeric public identity, independent `actions:reply_attempts` native sequence namespace; original allocator behavior |
| `channel`, `conversation_id`, `conversation_title`, `trigger_message_id`, `trigger_sender` | Keep same-name fields; required conversation/trigger/sender fields stay required; no new channel/action enum or logical-trigger uniqueness |
| `trigger_text`, `trigger_text_provenance_json` | Keep current source values and existing active-input behavior; source identity is not inferred from a digest; see input boundary below |
| `action`, `sensitivity_kind`, `codex_reason`, `audit_summary` | Keep result classification, reason and adopted business/error summary; `audit_summary` is not tool-event storage |
| `draft_reply_text`, `final_reply_text`, `permission_action`, `permission_reason`, `human_decision_options_json` | Keep business reply drafts/final result, decision display and options; do not erase adopted text because it resembles native output |
| `agent_run_id` | Nullable INTEGER **scalar** with original NULL/0/unknown/other-type-ID acceptance; source has no FK in either variant. Do not route through global `REFERENCES['agent_run_id']`, manufacture a run or replace unknown IDs with NULL |
| `codex_session_id`, `codex_transcript_start_line`, `codex_transcript_end_line` | Keep accurate existing Codex native locator/slice; line defaults stay 0. Do not backfill absent session/slice from a nearby run by guess |
| `audit_documents_json` | Keep raw existing reference collection; do not treat it as copied document/tool body or parse arbitrary source TEXT/BLOB as valid JSON |
| `audit_tool_events_json` | Retire copied audit/tool event bodies and legacy stored-event detail fallback. Preserve only the existing `audit_review` / `queued` business marker semantics, in this existing field; ordinary DTO value remains `[]`. No other event is retained as a second trajectory |
| `direct_user_id`, `direct_open_dingtalk_id` | Keep recipient metadata and empty defaults |
| `oa_process_instance_id`, `oa_task_id`, `oa_url`, `oa_action`, `oa_remark`, `oa_action_result_json` | Keep OA business identity/action/remark and raw result receipt; preserve current upstream selector's task-ID requirement |
| `calendar_event_id`, `calendar_response_status`, `calendar_response_result_json` | Keep calendar action and raw receipt |
| `mail_mailbox`, `mail_message_id`, `mail_subject`, `mail_reply_text`, `mail_action_result_json` | Keep mail identity, adopted reply and raw action receipt |
| `reaction_action_result_json`, `document_action_result_json` | Keep raw business results; approved native removal does not apply to these fields |
| `send_status`, `send_error`, `retry_count` | Keep raw mutable delivery/retry state. Current task/run/delivery display projection remains separate |
| `reviewed_at`, `reviewer_feedback`, `corrected_reply_text`, `feedback_scope`, `skill_update_requested`, `skill_update_receipts_json` | Keep nullable review time, feedback, correction, existing scope/default, request and raw Skill receipts |
| `resolved_at`, `resolution`, `created_at`, `updated_at` | Keep resolution, original timestamps and ordering; preserve empty/default/current_timestamp and nullable distinctions |
| `conversation_single_chat`, `trigger_create_time`, `universal_execution_id`, `universal_execution_scope_id` | Keep exact populated historical fields in the actual-origin type payload and paired inverse; hidden from the unchanged 54-field current DTO. Fresh-origin records lack these historical fields; no synthetic backfill |

All ten captured Memory columns:

| Source column | Target and rule |
|---|---|
| `id` | Numeric public identity, independent `actions:memory_write_events` sequence namespace |
| `attempt_id` | Actual-origin schema: keep raw INTEGER association without FK, permitting orphans and parent-key changes. Fresh-origin schema: physical reference to the `reply_attempts` action, typed immediate NO ACTION; project public attempt ID |
| `event_type` | Keep raw name, same unique membership; repeated extraction suffixes `memory_write_2`, etc. retained |
| `payload_json` | Retire copied `{tool,call_id,input,output}` event fragment. Existing DTO compatibility may return `{}`; the physical type must not persist the copied payload or a second native body |
| `status` | Keep original default `pending`, existing pending/failed/written values and arbitrary values legally accepted by source; no new enum |
| `attempts` | Keep default 0 and historical value; extraction inserts 1. Explicit completion does not increment |
| `last_error` | Keep current business diagnostic and empty default |
| `memory_episode_id` | Keep actual durable provider episode/duplicate ID and empty default, not a manufactured UUID |
| `created_at`, `updated_at` | Keep defaults and timestamps; explicit successful completion updates only `updated_at` among times |

The Memory payload retirement does not drop its row, episode ID, status, error or attempts. It removes the copied tool fragment after the receipt/error extraction, not the necessary completion fact. Do not introduce a new raw Memory receipt field merely to preserve a duplicate tool output; no such source field exists.

## Four historical fields: evidence and retirement decision

The actual capture has 1 non-NULL `conversation_single_chat`, 1 nonempty `trigger_create_time`, and 735 nonempty values in each universal execution column. These are metadata counts; no bodies/values were exported. `ReplyAttempt` at `app/store.py:1155` and fresh Attempt DDL at `app/store.py:3132` have 54 fields; current bootstrap column handling at `app/store.py:4480` does not establish a use of these four historical Attempt columns.

The bounded exact-name application scan finds no `conversation_single_chat`, `universal_execution_id` or `universal_execution_scope_id` Attempt reader/writer. The similarly named `app/cli.py:2421` helper chooses chat shape for OKR and is not that historical column. `trigger_create_time` is actively used by tasks/inputs and rerun arguments (`store.py:9577`, `10170`, `25166`), but the Attempt INSERT/update paths (`23597`, `23762`, `24037`, `25246`, `25338`) do not read/write the historical Attempt column. Whole-row model reads ignore extra columns rather than consuming their contents.

This permits hiding them from current DTOs, which already ignore them. It does **not** prove nonempty universal execution identities or source timing may be discarded. E retains all four until independently confirmed retirement evidence establishes their business value and references are obsolete. No deletion is proposed from name absence alone. Do not call this a legacy compatibility writer: there is one typed storage path and no old table/view/double write.

## Review marker, event rebuild and Memory completion dependencies

`_audit_event_metadata_json` (`store.py:1736`) returns `[]`; normal record/update/finalization passes original in-process events to `_record_memory_write_events_in_connection` before discarding event copies (`23597`–`24147`). `record_reviewed_reply_rerun` (`25157`–`25360`) instead appends/inserts `{'tool':'audit_review','result':'queued'}`. The source Attempt ID can remain stable while its task generation changes. Preserve its atomic updates, selected-decision guards, duplicate active-rerun reuse, candidate supplement/review binding and queue generation. Extract only that exact existing marker shape into the existing marker field; do not preserve prior tool bodies appended before it. A marker is neither a native trajectory nor proof that an external action happened.

Memory extraction (`store.py:24840`–`24938`) parses transient original events, pairs `tool_output` by `call_id`, parses provider error/episode receipt and numbers repeated calls. It **deletes all events for that Attempt and rebuilds them**, including deleting all on empty/malformed/non-list input. Preserve that mutable/rebuild behavior and sequence consumption. Do not turn it into append-only receipt history, salvage deleted records or replay a Memory write.

`run_codex_memory_write` (`app/codex_memory_write.py:95`–`132`) receives `data`, `type`, `created_at` and source description as explicit arguments. It does not read `MemoryWriteEvent.payload_json` for retry input. Completion addresses the original event ID, updates only `status='written'`, `memory_episode_id`, `last_error=''`, `updated_at`, and requires exactly one eligible pending/failed/written row; zero rows raises `ValueError`. No `attempts` increment or implicit recreation. Current application search finds no call of this wrapper; tests call it. `list_memory_write_events_for_attempt` has test callers; queue UI reads only state/error/time. Reconfirm this closure after D publication. If a newly published caller actually reads deleted payload as input, implement its existing explicit-input/native retrieval path before retirement; do not guess it is unused.

Current `MemoryWriteEvent.payload_json` model field can remain a presentation placeholder `{}` for callers that expect that key. Native tool detail comes only from the existing Codex reference/slice and must be unavailable when missing. Pending/failed status alone is not recoverable write input; an old event with missing native reference cannot be reconstructed or auto-replayed.

## Input and native availability boundary

`input_compacted` belongs to tasks/input versions, not these two source tables. Existing `_compact_settled_reply_task_input` (`store.py:9747`–`9884`) checks terminal task/run/receipt/latest-attempt state, compacts task/input bodies, and clears matching Attempt text with SHA/bytes/task/matching-input metadata. A `matching_input_id=0`, a digest, or an existing compacted input is not proof an original can be re-read.

E migrates existing Attempt input/provenance values without invoking compaction, adding a deadline, or declaring all stored provenance retrievable. Preserve existing active/recovery dependencies and History/calendar classification behavior that still reads `trigger_text` (`history_types.py:98`). Further removal of an existing nonempty input must bind an exact retrievable source/version using existing locators and preserve current business results. Unverified source gaps remain explicit; no new provenance row is generated from hashes or native-session guesses. Missing native references are kept as missing. E does not restore already compacted bodies or promise they are available.

`_audit_tool_events_for_attempt` (`audit_web.py:13018`) currently reads native events then stored fallback; E removes that fallback. `_agent_sessions` (`web_api/attempts.py:92`) still exposes each actual role's readable reference. Preserve separate Consumer/Audit references in runs, known slices and absent-file presentation without opening/copying native records during schema migration.

## Schema and allocator contracts

Two supported layouts are required. Actual-origin target preserves no-FK Memory association and the four extra field values. Fresh-origin target preserves its original Memory FK and absent extra fields. Source variant identity is structural migration metadata, never a new business field or guessed from row counts. Concretely, source capture returns the two exact declaration/object manifests and classifies one finite mode, `actual_no_memory_fk` or `fresh_memory_fk`, in the in-memory release object. The fresh empty constructor chooses `fresh_memory_fk`; a forward from the exact actual source chooses `actual_no_memory_fk`. Each mode creates one complete canonical `actions` table with its own exact manifest: the former has scalar Memory `attempt_id` only, the latter has the physical typed Memory parent FK. There is one table and one writer, not a flag on individual business rows. Store initialization after process restart compares the full target declaration against these two finite manifests and continues with the matched mode; it does not infer mode from missing rows or repair one mode into the other. Paired inverse selects the captured source declaration and verifies the matching target manifest before reading facts. This follows the existing exact-source release metadata pattern and adds no durable body snapshot or new physical metadata table. Do not install a fresh FK on actual data simply because the current sample has no orphans. Fresh FK includes child rebind, unreferenced parent-key update/delete acceptance, referenced public-key update/delete rejection and wrong-record-type rejection. Only fresh typed references enter the public-reference update guard. Attempt `agent_run_id` remains scalar in both. Where existing/D types need the physical `actions.agent_run_id` FK, E keeps that physical column NULL for Attempt rows and reads the Attempt scalar from its finite payload key. Its public projection and run/error/id index use that scalar expression; no shared name-based reference conversion is allowed. In the actual target mode Memory `attempt_id` is a scalar payload projection; in the fresh target mode it is the physical parent reference with public-ID projection. Preserve source affinity outcomes; add no source-absent INTEGER-only check on scalar fields.

Keep both public native allocator namespaces, including deletion high-water, explicit low/high IDs, ignored invalid insert advancing water, rejected insert not advancing water, ignored MAX_INT causing later SQLITE_FULL, and absent empty-table sequence rows. Allocation and insert share the existing SQLite writer transaction; autocommit uses the established savepoint pattern, not separate unlocked MAX+1. Earlier release allocator manifests stay frozen; extending global type constants must not mutate B/C/D source or inverse metadata.

Keep the original seven Attempt index key orders, DESC/collation and partial predicates on the E type only: trigger; status/created; created/id; OA process/created DESC/id DESC with captured nonempty predicate; conversation/trigger/action/id DESC; channel/conversation/trigger/updated DESC/id DESC; scalar run/error/id. Public ID generated expressions must match existing partial index type conditions. Memory membership uniqueness is `(attempt_id,event_type)` within its type; preserve `(attempt_id,id)` and actual `(status,id)` versus fresh `(status,updated_at)` access paths. Do not normalize empty string/space/tab/NUL, malformed JSON, channel, status, NULL, BLOB or timestamps.

No old physical table, compatibility view, double write, read-path self-healing or new audit/approval/reconciliation policy is part of E. Existing initialization repair of missing auxiliary objects is separate from unsupported mixed/partial-source rejection.

## Caller migration units and behavior checks

The frozen JSON lists exact literal occurrences/functions; use it as bounded navigation, then refresh against published D. All live SQL and presence gates must change in the same release. Public source labels such as `HISTORY_SOURCE_TABLES['reply_attempts']` remain valid API labels, not evidence an old physical table exists.

| Unit | Caller work and focused gates |
|---|---|
| Attempt CRUD and finalization | `store.py` record/update/upsert/finalize/no-run methods; retain update allowlist, CAS/lease/token and transaction ordering. `test_store.py -k 'reply_attempt or memory_write'`, `test_reviewed_orchestration.py`, `test_reviewed_worker.py` |
| Reviewed decisions and reruns | `record_reply_feedback`, human-decision resolve/review/rerun/candidate binding; marker-only persistence, stable source ID and task generation. `test_reviewed_human_decision.py`, `test_reviewed_execution_projection.py`, `test_reviewed_candidate_store.py`, `test_reviewed_source_revisions.py` |
| Native auth/WeChat/delivery reconciliation | `store.py` failed-auth recovery, finalization, skip/requeue/sync/current-task settlement and business-terminal exclusions; change storage access only. `test_native_reply_reconciliation.py`, `test_wechat_history_projection.py`, `test_attempt_projection.py` |
| History, detail and status | `history_types.py`, Store scheduled/history/feedback counters, `audit_web.py` table-presence specs and queue SQL, `web_api/attempts.py`; keep labels, pagination, ordering, current-generation projection, raw status and source selector task-ID requirement. `test_history.py`, `test_store_history_pagination.py`, `test_console_attempt_detail_api.py`, `test_approval_history.py`, `test_status_snapshot_projection.py` |
| Quality/retirement/recovery and CLI | `quality_gate.py`, `rule_question_retirement.py`, `scheduled_task_recovery.py`, `cli.py` summary/channel detection; preserve resolved/raw distinctions and original predicates. `test_quality_gate.py`, `test_quality_gate_scan_cost.py`, `test_rule_question_retirement.py`, `test_scheduled_task_recovery.py`, `test_reply_attention_diagnostics.py` |
| Email direct joins | `email_store.py:_email_unsubscribe_attempt_ids` and related attempt consumers; preserve exact action/plan/trigger associations and IDs. `test_email_unsubscribe.py`, `test_email_unsubscribe_evidence.py`, `test_email_reply_delivery.py` |
| Memory extraction/completion | `store.py` extraction/delete/rebuild/list and `codex_memory_write.py` completion; replace direct UPDATE with typed action write and existing eligible rowcount. `test_codex_memory_write.py`, `test_store.py -k memory_write` |
| Maintenance and startup | `storage_maintenance.py` counts/compaction, old native duplicate fields, Store constructor/bootstrap/manifest/removed-runtime migration/operation-parent existence. `test_native_only_core.py`, `test_native_only_maintenance.py`, `test_reply_input_reference.py`, `test_deploy_maintenance.py` |

`attempt_projection.py` currently describes raw Attempt status as immutable history, while actual reviewed/correction paths update it. Tests follow actual source behavior; do not introduce immutability to match that prose. Update that local explanation when converting this path.

## Executable tasks after C/D actual publication

### Task 1: Bind exact published source and variant

**Files:** source JSON/map; `tests/test_reply_attempt_action_migration.py`; own claim.

- [ ] Read the C and D final acceptance receipts; record actual commit/Store/Email marker from read-only source. Refresh only schema metadata, sequences, counts and dependency inventory in one read transaction; preserve no private rows in the committed artifacts.
- [ ] Compare both supported source declarations, source `actions` declarations, inbound FK/trigger/view/index closure and exact same-name source objects against the captured manifests. Test actual/fresh, unknown marker, mixed old/target tables, missing required column and extra trigger/index rejection **before mutation**.
- [ ] Run `python -m pytest -q tests/test_reply_attempt_source.py`; expected frozen source result: 54 passed, 2 variant-specific skips. A changed source needs a reviewed refreshed manifest, not changed target expectations.
- [ ] Commit source binding and migration preflight tests separately from runtime conversion; no E marker is allocated by this task alone.

### Task 2: Finite target values, references, indices and allocators

**Files:** `app/action_storage.py`; `tests/test_reply_attempt_action_storage.py`.

- [ ] Add failing source/target comparison tests for both record types, identical public IDs across other types, defaults/nullability, source scalar run values and actual/fresh Memory membership/FK behavior. Reuse the literal source fixture so the target cannot define its own oracle.
- [ ] Add explicit source-storage tests with synthetic `"not JSON\0 { trailing"` and `b"\x00\xffnot json"` in retained receipt/reference fields, nullable historical values and low/high/ignored/MAX_INT allocator cases. Approved removed audit/payload contents are tested as removal, not preservation.
- [ ] Implement type-specific fields/references and E-only lossless SQLite source-value representation; preserve raw inner receipt strings and return original value/storage class. Implement the exact existing allocator namespace/savepoint behavior and type-filtered indices. Do not extend global run FK semantics to Attempt.
- [ ] Run `python -m pytest -q tests/test_reply_attempt_source.py tests/test_reply_attempt_action_storage.py tests/test_action_storage.py` ; `tests/test_action_storage.py` exists at this preparation commit and must remain green along with preceding release suites. Compare source native sequence effects on success, ignore and failure, including transaction rollback.
- [ ] Commit the finite storage unit with the preserved/removed field contract.

The target comparison test helper below is complete test code to put in `tests/test_reply_attempt_action_migration.py`. `source_columns` comes directly from the captured manifest; `target_projection_sql` is the finite projection defined by Task 2. Compare both values and storage classes so JSON-looking source strings, NULL, NUL and BLOB cannot disappear in an equal-looking DTO. The exception sets are exactly the approved copied-content fields; marker semantics are checked separately.

```python
def projected_values(db, sql, columns):
    expressions = [f'"{field}"' for field in columns]
    expressions += [f'typeof("{field}")' for field in columns]
    return db.execute(
        "select " + ",".join(expressions) + " from (" + sql + ") order by id"
    ).fetchall()


def assert_retained_values(source_db, target_db, table, source_columns,
                           target_projection_sql):
    removed = {
        "reply_attempts": {"audit_tool_events_json"},
        "memory_write_events": {"payload_json"},
    }[table]
    retained = [field for field in source_columns if field not in removed]
    assert projected_values(source_db, "select * from " + table, retained) == (
        projected_values(target_db, target_projection_sql, retained)
    )
```

### Task 3: Transactional forward and current-fact inverse

**Files:** `app/action_reply_storage.py`, `app/action_reply_schema_release.py`, `app/action_storage.py`, `app/store.py`, `app/store_schema_release.py`; `tests/test_reply_attempt_action_migration.py`.

- [ ] Add failing forward tests comparing every retained source field and storage class, public ID, sequence, source object and unrelated-table fingerprint; use different physical/public IDs and overlapping type IDs. Add rejection/no-write tests for each unsupported source layout.
- [ ] Implement exact source capture/target validation in the existing owned migration transaction: allocate physical rows, preserve public IDs, map only fresh Memory FK, insert finite values, discard copied event payloads, retain queued markers, validate then retire the two old tables and update release marker atomically. No calls to provider, Agent, compaction or business recovery.
- [ ] Add inverse tests after new Attempt/Event inserts, feedback/receipt/state updates, child rebind and deletions; compare **current** facts and current allocator waters. Restore exact captured source DDL/index/FK/column order/marker timestamp. For `audit_tool_events_json`, restore the current normalized marker-only array when queued `audit_review` markers exist; use `[]` only when no such marker exists. Preserve marker order and multiplicity, including markers added after forward migration; do not restore discarded tool bodies. Restore Memory `payload_json` as `{}` for every retained event because its copied fragment is retired. Test inverse with no marker, one marker and multiple markers, including a source array containing both a queued marker and removed tool bodies; business markers survive and tool bodies do not. Fresh FK checks still pass; actual legal orphans remain legal.
- [ ] Test new facts in a separate old-code process on the reconstructed source database, followed by re-forward migration in a separate candidate process. Clear same-process Store initialization caches from the experiment by using new processes, not by accepting cached startup.
- [ ] Run `python -m pytest -q tests/test_reply_attempt_action_migration.py tests/test_store_schema_release.py tests/test_action_postfix_schema_release.py tests/test_action_receipt_schema_release.py`; extend existing publication failure-stage coverage for this unit, then commit migration/publication binding and documentation of inverse loss boundary.

### Task 4: Store writers, marker and Memory completion

**Files:** `app/store.py`, `app/codex_memory_write.py`; existing CRUD/review/Memory tests.

- [ ] Add failing target-backed tests: normal transient Memory extraction with paired output and repeated calls; update with malformed/non-list/empty events deletes previous Memory rows; review appends only queued marker, keeps Attempt ID and changes task generation; feedback and candidate-binding validation remain atomic.
- [ ] Convert finite writers and mutation predicates in the same transaction. Normal audit input remains transient and feeds receipt extraction before removal; delete/rebuild affects only the selected Attempt's events. Keep reviewed idempotent reuse and source state eligibility.
- [ ] Convert completion to target event ID with eligible pending/failed/written predicate; test written/failed/pending success, ineligible/deleted event error, timestamp touch, no attempts increment and no event recreation. Explicit supplied input remains required; do not read retired payload or send a real Memory write.
- [ ] Run the first, second and seventh caller-unit gates above, with routed execution/provider fakes; commit writers plus runtime-document lines reflecting actual behavior.

### Task 5: Readers, recovery, presence detection and pages

**Files:** caller table modules, `app/store.py`, affected existing tests; architecture/runtime docs.

- [ ] Add failing target-backed tests for History/filter/cursor/detail/current projection, queue counts, scalar unknown run, OA task-specific selector, scheduled failure exclusions, user feedback and original reconciliation predicates. Physical old-table absence must not suppress Attempts or Memory status.
- [ ] Replace reads with finite typed SQL and writes with exact record-type/public-identity predicates. Convert table-presence gates and maintenance source counters. Preserve source labels/API keys and original latest ordering; do not weaken tests by removing rows, VM-step or no-mutation assertions.
- [ ] Remove stored native-event fallback; add missing-session/zero-line-range cases showing unavailable detail, business result/receipt still visible, and review marker still available. API native role references retain original actual run slices; no fabricated provenance.
- [ ] Run the remaining caller-unit gates and relevant Console browser tests with local synthetic data/fakes only. Check Status, Attention, History pagination/detail, review/rerun page, OA/calendar/mail/reaction/document receipts, input provenance display and Memory state. No live send, recall, approval or Memory write.
- [ ] Commit reader conversion and truthful architecture/runtime/local status explanation updates together.

### Task 6: Latest-source copy, inverse, independent review and publication

**Files:** `docs/database-redesign-phase5e-validation.md`; existing release/deploy tests; own claim.

- [ ] Make the latest authorized consistent source copy in a task-owned temporary directory after rechecking published source metadata. Validate quick_check/integrity/FK and scalar public associations; do not export native files, credentials or bodies into durable artifacts.
- [ ] Run forward/current-fact inverse/new-process old-code startup/re-forward on both variants. Run all existing publication failure points with new business state/receipt facts written before inverse; prove no stale database replacement or copied trajectory restoration.
- [ ] Record retained field/type equivalence separately from approved removed audit/event content. Compare all preceding actions/task/run/delivery identities, values, references and sequence waters; compare all unrelated-table fingerprints. Record unknown-source non-mutation evidence.
- [ ] Verify the seven Attempt and both Memory source index query shapes on small synthetic and increasing unrelated-type volumes; use existing focused VM-step/query-plan budgets. Compare raw queue counts and current state projections before/after, not just HTTP success.
- [ ] Run independent SPEC/QUALITY review and full CI on the final commit. Use focused local suites; if a full local run is necessary, use the repository's allowed parallel suite outside restart windows. No full serial live-tree test run.
- [ ] After actual merge/push through the normal release path, run `python -m app.deploy`. Verify new PID/health, exact Store/Email marker, queues, Attention, History, affected pages, original receipt visibility and absent old tables. No real external business action is needed to validate the migration.
- [ ] Measure logical removed bytes and actual DB/index/WAL/pages separately. Space reclamation is a separately coordinated action after successful readback; E does not add a VACUUM to initialization or promise file shrink from logical deletion. Clean only owned temporary experiment material and release claims.

## Acceptance and inverse evidence boundary

Success requires all retained fields, original legal writes and observable caller behavior to match the refreshed exact source variant, with two approved content exceptions: copied audit/tool bodies and copied Memory tool fragments. `audit_summary`, final/draft/corrected reply text, raw action/Skill receipts, Memory episode/state/error, current generations and native locators survive. Four populated historical fields survive because retirement is not proven.

The paired inverse promises exact source declarations, restored release metadata, allocator semantics and current retained facts including writes after forward migration. It explicitly cannot restore removed tool payloads, preexisting compacted input, missing native locators/files or an original event row deleted by the current rebuild operation. The inverse restores `audit_tool_events_json` from the current normalized marker-only array if queued `audit_review` markers exist, preserving their order and multiplicity; it uses `[]` only when no marker exists. This retains the existing queued business marker semantics while excluding discarded trajectory/tool bodies. Memory `payload_json` is always recreated as `{}` for retained events, a declared removal placeholder rather than original content. No backup/body snapshot is kept to hide this loss boundary. Validate the actual old release reads both the preserved marker array and the absent-marker `[]` case, and accepts the Memory `{}` placeholder in a fresh process before opening the publication window.

A green source-only oracle proves source behavior, not E target correctness, C/D publication, external provider effect or the full 10+1 redesign. Final E evidence must name the implementation commit, actual source release, independent review/CI, normal deployment and readbacks separately.
