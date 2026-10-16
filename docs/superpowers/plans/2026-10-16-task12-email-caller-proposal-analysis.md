# Task12 Email caller source evidence, 2026-10-16

**Status: source evidence complete; full caller patch deliberately not produced.** Root agreed to stop at the concrete helper gaps below and prepare a separate complete SQL-extension plan. This is an unselected investigation, not a blocked external action or an implemented conversion.

Machine evidence: [analysis JSON](2026-10-16-task12-email-caller-proposal-analysis.json). It pins the source head and SHA-256 of thirteen inputs, 28 lexical source owners with exact method/source/AST hashes, native statements and parameter expressions, eight required-schema alias use chains, seven generic console helpers, and both frozen native-profile probe results. There is no candidate hash or patch hash because no candidate exists.

## Decisive source contracts and gaps

| Owner | Native statement and observed behavior | Current Task12 API gap |
|---|---|---|
| `EmailStore.enqueue_historical_page` (`app/email_store.py:9050`) | Existing `BEGIN IMMEDIATE`; one native INSERT…ON CONFLICT DO UPDATE per candidate; then existing traversal `upsert_state` in that same transaction. The method ignores the native cursor, returns None, and never observes rowcount or lastrowid. | `insert_task12` matches the existing caller transaction but has no conflict target, DO UPDATE tail, or encoded excluded-leaf support. Separate point insert/update calls would change the native statement. |
| `EmailStore.set_historical_candidate_state` (`9164`) | First DML is a native UPDATE through `_connect`; sqlite3 emits implicit `BEGIN`; `with db` commits/rolls back; `_connect` closes. Reads native rowcount, then raises the existing missing-row ValueError **after** the context exits if it differs from one. | `update_task12` calls `_lock` before DML and rejects an inactive transaction. Selecting source ROWID or adding a helper `BEGIN`/`BEGIN IMMEDIATE` does not preserve this source boundary. |
| `EmailClassificationTaskAdapter.open_offline_task`, `close_offline_task`, `purge_offline_tasks`, `_finish` non-done branch | Implicit native transactions; source INSERT OR REPLACE, DELETE, owner/generation/status UPDATE CAS; purge returns one native multirow rowcount. | Same inactive-transaction rejection. Point helpers cannot replace a native multirow statement without changing statement atomicity/rowcount semantics. |
| Adapter `retry_failed_tasks`, `claim_next`, `_expire_running_claims` | Retry is one multirow UPDATE inside existing immediate transaction. Claim uses `generation=generation+1` and `attempt_count=attempt_count+1` in one native statement. Expiry selects expired rows, updates each under the caller transaction, and returns the number selected. | Point concrete-value maps do not express whole-statement multirow SET or original-row RHS. A helper loop is not an equivalent implementation. |

The historical composite key is `(account_id, folder, model_id, stable_message_identity)`. Enqueue inserts pending state with empty reason and default retry coordinates. On conflict, it changes exactly **normalized_text, provider_message_json, uidvalidity, uid, updated_at** and preserves the old hidden ROWID, state, reason, attempted_at and next_retry_at. `normalized_text` is the existing SHA-256 input digest; provider JSON is the existing validated locator projection. REPLACE would erase native retry state and change ROWID, so it cannot stand in for UPSERT.

The state setter binds `(state, reason, attempted_at, next_retry_at, now, account_id, folder, model_id, stable_message_identity)`. It accepts only pending/deferred/terminal; only deferred computes attempted time and the existing 60-second next retry. It returns None. No owner/generation CAS exists in this historical family; adding one would change behavior.

Minimal mechanisms for root's **separate** plan, not implemented here: pure finite SET SQL fragments can preserve native implicit transaction and full WHERE/rowcount behavior; existing `insert_task12` could gain a trusted native conflict tail and encoded excluded-leaf expression support while retaining its current caller-transaction requirement. The historical conflict must retain the existing legacy source ROWID and physical row. No new lifecycle behavior, wrapper transaction, fallback, dual writer or read migration is proposed.

## Classification lifecycle and public identity

The current adapter contains no module SQL-projection alias: all classification writers use native SQL directly. Ensure and done completion use their existing immediate transaction; claim's empty probe stays read-only and recomputes eligibility after its existing immediate BEGIN. Task IDs are public source TEXT identities, unrelated to global physical `tasks.id` and hidden source ROWID. Ensure keeps immutable input/digest comparison. Offline rows use caller IDs, `{}` input, six-hour lease and generation/attempt one; close/purge retain the exact `{}` predicates.

Claim's order remains created_at/task_id. Finish keeps exact task_id + running + owner + generation CAS; success compacts only the existing done input inside that same transaction, retaining source identity/provider/configuration/unsubscribe references and input hashes. Failure/defer retains input. Explicit retry only resets selected failed tasks, preserves generation/identity and returns the native affected-row count. No new body-pruning or provenance rule is introduced.

## Indirect readers that lexical ownership misses

Within EmailStore, classification is also carried by `_REQUIRED_COLUMN_CONTRACTS` → `_REQUIRED_TABLE_COLUMNS`, `_REQUIRED_TABLE_CHECKS`, `_REQUIRED_PRIMARY_KEYS`, `_REQUIRED_UNIQUE_KEYS`, and `_REQUIRED_INDEXES`. The JSON records every AST use of these eight finite schema maps, including generic `_expected_column_declaration` and `_validate_schema_shape`; table-name search alone does not capture those consumers. Email29 `EMAIL_*_SQL` aliases concern Actions, not either Task12 source family.

Outside this DAO, `_queue_status_snapshots` binds the classification name through its specs tuple and calls `_queue_status_counts`, `_queue_retryable_count`, `_queue_latest_value` and `_queue_latest_error`. That last helper calls `_sqlite_table_columns`; existence uses `_sqlite_table_exists`; `_queue_count_for` aggregates the returned states. All seven helper bodies and hashes are captured. The classification retryable helper currently returns zero without SQL because its only SQL branch is for work_todo_dingtalk_links. Classification latest-error ordering is updated_at descending without an id tie-breaker, and its rowid predicate means **source ROWID**.

The inventory also includes Store's `_runtime_operation_parent_exists` query-variable binding (public task_id/running), optional-family checks and `_count` forwarding in quality_gate, Attention's local `email_task_sql` binding with source ROWID subquery, and explicit `compact_done_email_classification_inputs`. The explicit maintenance compaction remains an explicit batch, never initialization. These are root-owned cutover obligations, not changes in this task.

## Constructor and historical factory obligations

`EmailStore.__init__`, `_initialize`, `_validate_schema_shape`, `_open_connection`, all module schema maps, `app/email_current_schema.py`, and every original migration/create factory remain exact. All 352 EmailStore method ASTs are unchanged.

Current fresh initialization still creates both native queues through the current source factory. Current schema validation still requires native classification through `required_tables`. Historical candidates are created by that factory even though they are absent from `_REQUIRED_COLUMN_CONTRACTS`; those are independent target-selection obligations. Root must select the exact current Tasks20 declaration/manifest and remove both native families from that selected current profile without editing original historical defaults or factories. The separate Tasks8 codec-registration obligation at Email `_open_connection` does not authorize using Tasks8 field semantics for new12 source kinds.

## Verification and evidence boundary

Synthetic in-memory native probes recreated just the two source table CREATE statements from **both** the frozen actual and genuine adf0ee88-fresh captures. They executed exact extracted native SQL, never a DAO constructor or public method. Each profile confirmed explicit enqueue transaction, implicit setter BEGIN/COMMIT and rowcount, conflict preservation of hidden ROWID/retry state, malformed-JSON statement ABORT with original row unchanged and transaction still active, missing-row count zero, offline implicit INSERT and REPLACE's changed hidden ROWID, wrong-owner versus correct owner/generation CAS, and native multirow purge count two.

Direct calls to insert_task12/update_task12/delete_task12 on an inactive synthetic connection each returned `sqlite3.ProgrammingError: Tasks writes require a caller transaction` **before executing any SQL**, leaving the connection outside a transaction. This proves the interface mismatch; it does not prove target equivalence.

All nine inventoried Python source modules compiled with in-memory `compile`, and EmailStore/adapter/Task12 imports passed without constructors. Ruff passed for EmailStore, adapter, Task12 storage and Tasks20 definitions. All thirteen pinned input hashes remained exact. Python was the fixed project Python with `PYTHONDONTWRITEBYTECODE=1`; no temporary filesystem artifacts or DB files were created.

No applied-candidate compile/Ruff/import/patch roundtrip is claimed because there is no candidate. Public DAO differential fixtures, copied migration/current writes/inverse/genuine predecessor/reforward, native execution, UI and final11 acceptance remain root-owned. No app/test/constructor/schema-selection/database/provider/native CLI/network/main/push/deploy change occurred.
