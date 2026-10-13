# Email action source inventory — 2026-10-13

Status: source preparation only. Nine existing tables and all 103 actual fields are inventoried. No target schema, runtime code, migration, new Store/Email version, retention policy, provider operation or deployment is implemented. The full ten-core-table plus migration-table objective remains incomplete.

Production read transaction: `2026-10-13T18:29:59.509199+00:00`; checkout HEAD `d7397203df678fd4137c237eb7eb84a671e084f3`; `service_state.store_schema_version=2026-10-13.2` (Store16), Email schema51. Connection used read-only URI, `query_only=ON` and one deferred transaction for the nine-table source snapshot. The same transaction includes the four inbound child tables' complete object declarations; it reads no business rows. The production checkout was read for HEAD only, never edited or used for tests.

Fresh constructor: candidate `0660ce3418e41c77bca516ab357c780a434b1d17` on `codex/database-redesign-phase5d-20261013`; actual empty fixture markers Store17 (`2026-10-13.3`) and Email51, created only in an owned temporary directory. It does not establish production Store17 or reserve a future email-action release.

Companion [source JSON](2026-10-13-database-redesign-email-actions-source.json) retains literal DDL, xinfo/default/PK/hidden metadata, index declarations/xinfo/uniqueness/partial flags, all outgoing/incoming FKs, triggers, native sequences and per-field aggregate storage classes/population/bytes. Aggregates contain no business row values. `populated` means non-NULL excluding empty TEXT; 0, [] and {} still count. Byte sums use length(CAST(value AS BLOB)), including numeric text representation; these are value bytes, not SQLite file/page occupancy.

## Actual counts and source variants

| Table | Fields | Rows | Native sequence | Indexes | Own triggers |
|---|---:|---:|---:|---:|---:|
| email_action_attempts | 10 | 12,158 | 12358 | 1 | 0 |
| email_action_plans | 14 | 9,785 | none | 2 | 0 |
| email_actions | 18 | 10,167 | none | 4 | 2 |
| email_unsubscribe_cancellations | 2 | 1 | none | 1 | 0 |
| email_unsubscribe_claims | 19 | 1,077 | none | 2 | 0 |
| email_unsubscribe_continuations | 6 | 0 | none | 1 | 0 |
| email_unsubscribe_effects | 6 | 1,164 | none | 1 | 0 |
| email_unsubscribe_receipts | 20 | 1,077 | none | 2 | 0 |
| email_unsubscribe_steps | 8 | 823 | 823 | 1 | 0 |

All 103 shared columns have identical type/nullability/default/PK/hidden semantics. No actual-only or fresh-only column exists. Eight table declarations and physical column orders are literally equal. `email_unsubscribe_claims` differs: production audit_agent_run_id was appended after updated_at; the fresh declaration places it before claimed_at/updated_at. Preserve both literal variants for any paired release. Outgoing FKs, index semantic keys/uniqueness/partial flags and own trigger definitions are equal. Claim index_xinfo reports changed cid for updated_at because its physical column ordinal moved. Fresh tables contain no rows; email_action_attempts has a captured sqlite_sequence watermark of 0, while email_unsubscribe_steps has no sequence entry.

TEXT primary keys are rowid-table TEXT keys, not INTEGER identities or WITHOUT ROWID tables; their xinfo notnull=0 remains meaningful, and trim CHECK does not by itself make NULL illegal in SQLite. The effects composite key has explicit NOT NULL components. Native INTEGER AUTOINCREMENT id columns report notnull=0 but SQLite still allocates/preserves INTEGER row identities. Do not strengthen or reinterpret these declarations incidentally. No captured index has a partial predicate; all partial flags are zero.

## Existing identities and behavior

`EmailActionPlan` identity is a hash of the full immutable snapshot including creation time and, for v2, authorization format/material (`email_classifier_contracts.py:404`). `_persist_action_plan` validates existing exact values and successive per-classification versions (`email_store.py:8583`); `append_action_plan_version` atomically moves classification.current_action_plan_id while refusing old plan reuse (`16384`). Current plan is a parent relation, not a plan rebuilt from mutable classification fields. Candidate email_store.py, classifier contracts and producer implementation hashes were compared with the captured production commit; equality is recorded in JSON. Modules changed by candidate Store17 remain separately identified.

Direct action_id is SHA-256 of plan ID plus action type (`email_store.py:2953`); unsubscribe action_identity uses account, stable message, action type and plan version (`2880`). Neither is a fabricated join between unrelated public IDs. Attempt id is native AUTOINCREMENT; attempt_number is separately contiguous per action (`16481`, `17516`). The two count triggers on email_actions validate existing terminal-count/current-status relationships; their exact SQL is frozen in JSON.

Current-plan polls, claim-time rechecks, sibling ordering, account configuration, retry time and existing dependency graph remain distinct checks (`16573`, `16676`, `17123`, `17381`). A matching unsubscribe terminal receipt satisfies a dependent Trash when outcome is any value in the existing eight-outcome set (`17444`), not only done. Explicit source-verified retry authorizes exactly the next existing attempt via the separate immutable email_direct_retry_requests table; completion/recovery consumes rather than renews it (`17242`, `17516`, `17726`). This inventory neither widens that permission nor runs reconciliation.

Unsubscribe effects use action_identity+effect_digest; digest includes frozen binding, operations and optional predecessor (`2910`). effect_digest and previous_effect_digest in claims/steps/continuations/receipts are application-validated associations; SQLite declares only action_identity→claim or plan/classification FKs, not composite effect/run/account/thread/entry FKs. `audit_agent_run_id` remains a nullable positive scalar with no declared FK. State/terminal snapshot readers follow typed runs/tasks and bounded effect ancestry (`10629`, `10701`, `10852`, `11087`).

The current direct path checks cancellation first, then existing receipt (`email_unsubscribe_direct.py:473`). Two existing skip outcomes can be reread and promoted when outcome changes; receipt is a current projection, not universally immutable (`email_store.py:12565`). Earlier run/step/effect evidence stays available through existing paths. `_persist` retires a historical claim onto the current direct digest, appends journal and saves terminal receipt (`email_unsubscribe_direct.py:640`; `email_store.py:11915`, `12275`). Historical audit/continuation paths remain callable and cannot be declared unused solely because current continuation row_count is zero.

Cancellation is the existing retain_subscription business decision, stored separately from browser outcome/provider evidence. It binds task ID, execution generation and input version; it requires the existing failed/no-effect checks and marks the existing typed task skipped (`16904`, `16945`). Its decision_json is not a terminal unsubscribe receipt. This source preparation adds no cancellation, authorization, review or reconciliation policy.

## Every source field

Each actual field appears exactly once below. Type, NULL allowance and default are from production table_xinfo; PK/unique/FK/CHECK details remain authoritative in the literal source JSON. The path column identifies the existing writer/reader family for that field, whose exact function/line set is frozen in field_inventory.

### email_action_attempts

| Field | SQL type / NULL / default | Existing meaning | Existing source path |
|---|---|---|---|
| id | INTEGER; allowed; default `none`; PK1 | Native INTEGER PRIMARY KEY AUTOINCREMENT attempt identity and sqlite_sequence watermark. | append_action_attempt:16481, complete_direct_action_attempt:17516 |
| action_id | TEXT; NOT NULL; default `none` | Parent direct-action TEXT FK; pair with attempt_number is UNIQUE. | append_action_attempt:16481, complete_direct_action_attempt:17516 |
| attempt_number | INTEGER; NOT NULL; default `none` | Positive action-local terminal sequence; append requires MAX+1, independent of AUTOINCREMENT id. | append_action_attempt:16481, complete_direct_action_attempt:17516 |
| status | TEXT; NOT NULL; default `none` | Terminal outcome CHECK done/skipped/failed. | append_action_attempt:16481, complete_direct_action_attempt:17516 |
| provider_operation | TEXT; NOT NULL; default `none` | Recorded provider/reconciliation operation kind. | append_action_attempt:16481, complete_direct_action_attempt:17516 |
| provider_target | TEXT; NOT NULL; default `none` | Recorded stable-message provider target; completion requires exact locator identity. | append_action_attempt:16481, complete_direct_action_attempt:17516 |
| provider_result_id | TEXT; NOT NULL; default `none` | Existing provider/readback receipt identifier; unsuccessful attempts may be empty. | append_action_attempt:16481, complete_direct_action_attempt:17516 |
| error | TEXT; NOT NULL; default `none` | Original direct-action failure text; successful current projection can be empty. | append_action_attempt:16481, complete_direct_action_attempt:17516 |
| started_at | TEXT; NOT NULL; default `none` | Actual execution/claim start timestamp; empty value has existing meaning. | append_action_attempt:16481, complete_direct_action_attempt:17516 |
| finished_at | TEXT; NOT NULL; default `none` | Terminal direct attempt timestamp; separate from receipt completed_at. | append_action_attempt:16481, complete_direct_action_attempt:17516 |

### email_action_plans

| Field | SQL type / NULL / default | Existing meaning | Existing source path |
|---|---|---|---|
| action_plan_id | TEXT; allowed; default `none`; PK1 | Existing opaque immutable plan TEXT ID; retain exact value, not a fabricated numeric parent. | _stored_action_plan:8399, _persist_action_plan:8583 |
| action_plan_version | INTEGER; NOT NULL; default `none` | Positive per-classification version; append logic requires latest+1. | _stored_action_plan:8399, _persist_action_plan:8583 |
| classification_id | INTEGER; NOT NULL; default `none` | Actual classification INTEGER association; declared RESTRICT FK where listed. | _stored_action_plan:8399, _persist_action_plan:8583 |
| account_id | TEXT; NOT NULL; default `none` | Existing account TEXT identity; application account/config joins; no declared account FK here. | _stored_action_plan:8399, _persist_action_plan:8583 |
| category | TEXT; NOT NULL; default `none` | Confirmed classification category snapshot; no SQL enum CHECK. | _stored_action_plan:8399, _persist_action_plan:8583 |
| classification_source | TEXT; NOT NULL; default `none` | Snapshot origin CHECK model/user/agent. | _stored_action_plan:8399, _persist_action_plan:8583 |
| confidence | REAL; NOT NULL; default `none` | Snapshot REAL confidence CHECK 0..1. | _stored_action_plan:8399, _persist_action_plan:8583 |
| model_id | TEXT; NOT NULL; default `none` | Existing model identity snapshot. | _stored_action_plan:8399, _persist_action_plan:8583 |
| config_version | TEXT; NOT NULL; default `none` | Existing exact configuration version snapshot. | _stored_action_plan:8399, _persist_action_plan:8583 |
| actions_json | TEXT; NOT NULL; default `none` | Immutable JSON action list; includes ordering/dependencies and unsubscribe capability. | _stored_action_plan:8399, _persist_action_plan:8583 |
| action_parameters_json | TEXT; NOT NULL; default `none` | Immutable per-action parameters JSON. | _stored_action_plan:8399, _persist_action_plan:8583 |
| authorization_snapshot_json | TEXT; allowed; default `none` | Nullable existing authorization snapshot JSON; NULL denotes legacy unavailable format. | _stored_action_plan:8399, _persist_action_plan:8583 |
| legacy_serialization_pre_v16 | INTEGER; NOT NULL; default `0` | Existing 0/1 serialization provenance, not a new authorization decision. | _stored_action_plan:8399, _persist_action_plan:8583 |
| created_at | TEXT; NOT NULL; default `none` | Original creation timestamp; explicit insert value unless captured DDL says otherwise. | _stored_action_plan:8399, _persist_action_plan:8583 |

### email_actions

| Field | SQL type / NULL / default | Existing meaning | Existing source path |
|---|---|---|---|
| action_id | TEXT; allowed; default `none`; PK1 | Direct-action TEXT identity from plan ID and action type; separate algorithm from unsubscribe action_identity. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| action_plan_id | TEXT; NOT NULL; default `none` | Existing opaque immutable plan TEXT ID; retain exact value, not a fabricated numeric parent. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| classification_id | INTEGER; NOT NULL; default `none` | Actual classification INTEGER association; declared RESTRICT FK where listed. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| account_id | TEXT; NOT NULL; default `none` | Existing account TEXT identity; application account/config joins; no declared account FK here. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| action_type | TEXT; NOT NULL; default `none` | Six existing SQL values label/mark_read/archive/move/trash/flag_important. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| parameters_json | TEXT; NOT NULL; default `none` | Frozen per-action parameter JSON. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| config_version | TEXT; NOT NULL; default `none` | Frozen action configuration version. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| status | TEXT; NOT NULL; default `none` | Current projection CHECK pending/processing/done/skipped/failed. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| attempt_count | INTEGER; NOT NULL; default `0` | Nonnegative terminal count; claim/attempt-count triggers and DAO preserve current count. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| started_at | TEXT; NOT NULL; default `''` | Actual execution/claim start timestamp; empty value has existing meaning. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| finished_at | TEXT; NOT NULL; default `''` | Terminal direct attempt timestamp; separate from receipt completed_at. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| next_attempt_at | TEXT; NOT NULL; default `''` | Existing due/retry schedule; empty is not equivalent to pending. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| provider_operation | TEXT; NOT NULL; default `''` | Recorded provider/reconciliation operation kind. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| provider_target | TEXT; NOT NULL; default `''` | Recorded stable-message provider target; completion requires exact locator identity. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| provider_result_id | TEXT; NOT NULL; default `''` | Existing provider/readback receipt identifier; unsuccessful attempts may be empty. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| error | TEXT; NOT NULL; default `''` | Original direct-action failure text; successful current projection can be empty. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| created_at | TEXT; NOT NULL; default `none` | Original creation timestamp; explicit insert value unless captured DDL says otherwise. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |
| updated_at | TEXT; NOT NULL; default `none` | Current projection/claim/continuation mutation timestamp. | _ensure_direct_action_rows:8697, claim_next_direct_action:16573 |

### email_unsubscribe_cancellations

| Field | SQL type / NULL / default | Existing meaning | Existing source path |
|---|---|---|---|
| action_identity | TEXT; allowed; default `none`; PK1 | Existing unsubscribe TEXT identity, scoped to account/stable message/type/plan version. | get_email_unsubscribe_cancellation:16894, cancel_failed_email_unsubscribe_task:16904 |
| decision_json | TEXT; NOT NULL; default `none` | Actual JSON principal retention decision bound to task/generation/input/reason; separate from provider opt-out receipt. | get_email_unsubscribe_cancellation:16894, cancel_failed_email_unsubscribe_task:16904 |

### email_unsubscribe_claims

| Field | SQL type / NULL / default | Existing meaning | Existing source path |
|---|---|---|---|
| action_identity | TEXT; allowed; default `none`; PK1 | Existing unsubscribe TEXT identity, scoped to account/stable message/type/plan version. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| effect_digest | TEXT; NOT NULL; default `none` | Existing accepted-operation digest; application verifies canonical binding, not merely a label. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| action_plan_id | TEXT; NOT NULL; default `none` | Existing opaque immutable plan TEXT ID; retain exact value, not a fabricated numeric parent. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| action_plan_version | INTEGER; NOT NULL; default `none` | Positive per-classification version; append logic requires latest+1. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| classification_id | INTEGER; NOT NULL; default `none` | Actual classification INTEGER association; declared RESTRICT FK where listed. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| account_id | TEXT; NOT NULL; default `none` | Existing account TEXT identity; application account/config joins; no declared account FK here. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| stable_message_identity | TEXT; NOT NULL; default `none` | Stable provider message identity retained in binding, independent of mutable UID/folder. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| thread_identity | TEXT; NOT NULL; default `none` | Frozen conversation/thread identity in unsubscribe binding. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| entry_reference | TEXT; NOT NULL; default `none` | Opaque frozen authorized browser-entry reference. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| operations_json | TEXT; NOT NULL; default `none` | Accepted operations JSON; immutable effect snapshot/current claim operations, not native trajectory. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| owner_id | TEXT; NOT NULL; default `none` | Existing claim owner or direct terminal/reconciliation owner marker. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| owner_generation | INTEGER; NOT NULL; default `none` | Positive existing lease generation. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| lease_token | TEXT; NOT NULL; default `none` | Existing owner token; current direct terminal write can use receipt-derived token. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| account_updated_at | TEXT; NOT NULL; default `none` | Existing frozen account config timestamp or historical marker. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| status | TEXT; NOT NULL; default `none` | CHECK dispatching/awaiting_audit/uncertain/done; retained historical/current claim projection. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| phase | TEXT; NOT NULL; default `'prepared'` | CHECK prepared/navigating/effect_uncertain/terminal; default prepared. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| claimed_at | TEXT; NOT NULL; default `none` | Existing nonempty claim acquisition timestamp. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| updated_at | TEXT; NOT NULL; default `none` | Current projection/claim/continuation mutation timestamp. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |
| audit_agent_run_id | INTEGER; allowed; default `none` | Nullable public Audit run ID; positive CHECK but no declared run FK. Typed runs association is application-level. | claim_email_unsubscribe_write:10128, _email_unsubscribe_claim_row:11320 |

### email_unsubscribe_continuations

| Field | SQL type / NULL / default | Existing meaning | Existing source path |
|---|---|---|---|
| action_identity | TEXT; allowed; default `none`; PK1 | Existing unsubscribe TEXT identity, scoped to account/stable message/type/plan version. | persist_email_unsubscribe_continuation:11347, _email_unsubscribe_continuation_row:11519 |
| effect_digest | TEXT; NOT NULL; default `none` | Existing accepted-operation digest; application verifies canonical binding, not merely a label. | persist_email_unsubscribe_continuation:11347, _email_unsubscribe_continuation_row:11519 |
| observation_reference | TEXT; NOT NULL; default `none` | Opaque continuation page observation reference. | persist_email_unsubscribe_continuation:11347, _email_unsubscribe_continuation_row:11519 |
| controls_json | TEXT; NOT NULL; default `none` | Bounded redacted controls JSON; current direct execution does not require this legacy continuation, existing reader/writer remains. | persist_email_unsubscribe_continuation:11347, _email_unsubscribe_continuation_row:11519 |
| created_at | TEXT; NOT NULL; default `none` | Original creation timestamp; explicit insert value unless captured DDL says otherwise. | persist_email_unsubscribe_continuation:11347, _email_unsubscribe_continuation_row:11519 |
| updated_at | TEXT; NOT NULL; default `none` | Current projection/claim/continuation mutation timestamp. | persist_email_unsubscribe_continuation:11347, _email_unsubscribe_continuation_row:11519 |

### email_unsubscribe_effects

| Field | SQL type / NULL / default | Existing meaning | Existing source path |
|---|---|---|---|
| action_identity | TEXT; NOT NULL; default `none`; PK1 | Existing unsubscribe TEXT identity, scoped to account/stable message/type/plan version. | claim_email_unsubscribe_write:10128, _email_unsubscribe_effect_row:11305 |
| effect_digest | TEXT; NOT NULL; default `none`; PK2 | Existing accepted-operation digest; application verifies canonical binding, not merely a label. | claim_email_unsubscribe_write:10128, _email_unsubscribe_effect_row:11305 |
| previous_effect_digest | TEXT; NOT NULL; default `''` | Existing optional predecessor digest; empty or length64 SQL CHECK; application chain validates actual linkage. | claim_email_unsubscribe_write:10128, _email_unsubscribe_effect_row:11305 |
| operations_json | TEXT; NOT NULL; default `none` | Accepted operations JSON; immutable effect snapshot/current claim operations, not native trajectory. | claim_email_unsubscribe_write:10128, _email_unsubscribe_effect_row:11305 |
| audit_agent_run_id | INTEGER; allowed; default `none` | Nullable public Audit run ID; positive CHECK but no declared run FK. Typed runs association is application-level. | claim_email_unsubscribe_write:10128, _email_unsubscribe_effect_row:11305 |
| created_at | TEXT; NOT NULL; default `none` | Original creation timestamp; explicit insert value unless captured DDL says otherwise. | claim_email_unsubscribe_write:10128, _email_unsubscribe_effect_row:11305 |

### email_unsubscribe_receipts

| Field | SQL type / NULL / default | Existing meaning | Existing source path |
|---|---|---|---|
| action_identity | TEXT; allowed; default `none`; PK1 | Existing unsubscribe TEXT identity, scoped to account/stable message/type/plan version. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| effect_digest | TEXT; NOT NULL; default `none` | Existing accepted-operation digest; application verifies canonical binding, not merely a label. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| action_plan_id | TEXT; NOT NULL; default `none` | Existing opaque immutable plan TEXT ID; retain exact value, not a fabricated numeric parent. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| action_plan_version | INTEGER; NOT NULL; default `none` | Positive per-classification version; append logic requires latest+1. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| classification_id | INTEGER; NOT NULL; default `none` | Actual classification INTEGER association; declared RESTRICT FK where listed. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| account_id | TEXT; NOT NULL; default `none` | Existing account TEXT identity; application account/config joins; no declared account FK here. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| stable_message_identity | TEXT; NOT NULL; default `none` | Stable provider message identity retained in binding, independent of mutable UID/folder. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| thread_identity | TEXT; NOT NULL; default `none` | Frozen conversation/thread identity in unsubscribe binding. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| entry_reference | TEXT; NOT NULL; default `none` | Opaque frozen authorized browser-entry reference. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| outcome | TEXT; NOT NULL; default `none` | Eight actual SQL terminal values; dependent Trash requires a matching receipt with any existing allowed outcome. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| receipt_id | TEXT; NOT NULL; default `none` | Existing opaque terminal receipt reference. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| evidence | TEXT; NOT NULL; default `none` | Existing redacted terminal evidence string. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| result_text | TEXT; NOT NULL; default `''` | Business-facing terminal page result text; <=16384 characters; never captured in this inventory. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| observation_digest | TEXT; NOT NULL; default `''` | Optional terminal observation digest; empty or length64. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| started_at | TEXT; NOT NULL; default `''` | Actual execution/claim start timestamp; empty value has existing meaning. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| completed_at | TEXT; NOT NULL; default `''` | Original operation completion timestamp. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| created_at | TEXT; NOT NULL; default `none` | Original creation timestamp; explicit insert value unless captured DDL says otherwise. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| result_text_truncated | INTEGER; NOT NULL; default `0` | Existing 0/1 result truncation flag. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| result_text_digest | TEXT; NOT NULL; default `''` | Original result-text integrity digest; empty or length64. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |
| entry_url | TEXT; NOT NULL; default `''` | Existing authorized URL pre-image checked against entry_reference; <=2048 chars; never read here. | persist_email_unsubscribe_terminal:12275, _email_unsubscribe_receipt_row:12718 |

### email_unsubscribe_steps

| Field | SQL type / NULL / default | Existing meaning | Existing source path |
|---|---|---|---|
| id | INTEGER; allowed; default `none`; PK1 | Native INTEGER PRIMARY KEY AUTOINCREMENT journal identity; sequence allocator is separate. | append_email_unsubscribe_step:11535, list_email_unsubscribe_steps:11628 |
| action_identity | TEXT; NOT NULL; default `none` | Existing unsubscribe TEXT identity, scoped to account/stable message/type/plan version. | append_email_unsubscribe_step:11535, list_email_unsubscribe_steps:11628 |
| effect_digest | TEXT; NOT NULL; default `none` | Existing accepted-operation digest; application verifies canonical binding, not merely a label. | append_email_unsubscribe_step:11535, list_email_unsubscribe_steps:11628 |
| sequence | INTEGER; NOT NULL; default `none` | Positive action-local contiguous journal ordinal; UNIQUE(action_identity,sequence). | append_email_unsubscribe_step:11535, list_email_unsubscribe_steps:11628 |
| operation | TEXT; NOT NULL; default `none` | Recorded nonempty redacted browser operation. | append_email_unsubscribe_step:11535, list_email_unsubscribe_steps:11628 |
| state | TEXT; NOT NULL; default `none` | Recorded nonempty page state; no SQL enum CHECK. | append_email_unsubscribe_step:11535, list_email_unsubscribe_steps:11628 |
| reference | TEXT; NOT NULL; default `none` | Recorded nonempty opaque journal observation/control reference. | append_email_unsubscribe_step:11535, list_email_unsubscribe_steps:11628 |
| created_at | TEXT; NOT NULL; default `none` | Original creation timestamp; explicit insert value unless captured DDL says otherwise. | append_email_unsubscribe_step:11535, list_email_unsubscribe_steps:11628 |

## Exact dependency closure

All source FKs use NO ACTION update, RESTRICT delete and NONE match. The outgoing classification/plan relationships remain declared; classification.current_action_plan_id is also an existing application parent relation. All SQLite tables were enumerated for inbound FKs; all table/view/index/trigger SQL was searched for these exact source names. There are 12 inbound FK entries: seven internal links and five links from four external child tables. External inbound children are below; no views or additional external index SQL references were found.

| Child table | Parent relation |
|---|---|
| email_direct_retry_requests | action_id → email_actions.action_id (NO ACTION / RESTRICT) |
| email_feedback_requests | resulting_action_plan_id → email_action_plans.action_plan_id (NO ACTION / RESTRICT) |
| email_feedback_requests | expected_current_action_plan_id → email_action_plans.action_plan_id (NO ACTION / RESTRICT) |
| email_reply_dispatch_claims | action_plan_id → email_action_plans.action_plan_id (NO ACTION / RESTRICT) |
| email_reply_receipts | action_plan_id → email_action_plans.action_plan_id (NO ACTION / RESTRICT) |

Four external table declarations, their indexes/triggers and all exact incoming FKs are in the JSON closure. email_direct_retry_requests adds the real five fields captured by the [2026-10-13 field-map addendum](2026-10-13-database-redesign-field-map-addendum.csv). Its immutable insert/update/delete triggers belong to that relation and must remain part of dependency analysis. The addendum also captures the two cancellation fields inventoried here. Original direction CSVs describe approved destinations, not executable migration SQL or evidence that parent references have been transformed.

| External trigger owner/name | Existing protected relation |
|---|---|
| email_accounts / trg_email_direct_action_blocks_account_delete | email_actions |
| email_accounts / trg_email_direct_action_blocks_account_update | email_actions |
| email_classifications / trg_email_direct_action_blocks_plan_switch | email_actions |
| email_accounts / trg_email_unsubscribe_blocks_account_delete | email_unsubscribe_claims |
| email_accounts / trg_email_unsubscribe_blocks_account_update | email_unsubscribe_claims |
| email_messages / trg_email_unsubscribe_blocks_message_delete | email_unsubscribe_claims |
| email_messages / trg_email_unsubscribe_blocks_message_update | email_unsubscribe_claims |
| email_classifications / trg_email_unsubscribe_blocks_plan_switch | email_unsubscribe_claims |

Lexical direct-table references were scoped to app/ and scripts/; JSON retains every individual owner/line and source hash. Generic attribute-call matches are navigation evidence and can include ambiguous receiver types; they are not a typed whole-program proof. Material caller families include:

- email_worker: current deterministic action execution/recovery, model action repair, missing-entry terminal receipts and cancellation before browser open.
- email_unsubscribe_direct / email_unsubscribe / email_unsubscribe_audit: direct terminal behavior plus existing legacy claim/effect/continuation/journal paths.
- email_task_adapter / email_task_producer: frozen task input identity, classification/current-plan lineage, retained claim/continuation context.
- email_action_reconcile / cli: read-only failed-action examination, explicit release/reconciliation and legacy unsubscribe retry commands.
- audit_web / web_api.attempts / web_api.email / history_types: current-plan queue/Attention, receipt/step details, native-run references and public task/attempt navigation.
- store / deploy_maintenance / email_schema_release: shared schema/bootstrap, quiet-read adapters and existing Email50→51 matched source publication.

| Module | Exact literal table-reference owners (deduplicated) |
|---|---|
| app/audit_web.py | _queue_status_snapshots:2618; _email_action_queue_snapshot:2792; _queue_attention_rows:3598 |
| app/cli.py | reconcile_email_actions_command:1903; _recover_orphaned_reply_tasks_on_service_start:4538; main:5027 |
| app/deploy_maintenance.py | check_maintenance:45 |
| app/email_action_reconcile.py | reconcile_email_actions:258 |
| app/email_schema_release.py | rollback:124; verify_loaded:168; prepare_email_schema_release:257 |
| app/email_store.py | <module>:100; _failed_action_retriable_sql:1837; _audited_unsubscribe_lineage:2391; _current_unsubscribe_task_lineage:2532; _initialize:3208; _prepare_v10_unsubscribe_migration:3410; _finish_v10_unsubscribe_migration:3429; _prepare_unsubscribe_schema_migration:3507; _finish_unsubscribe_schema_migration:3555; _ensure_direct_action_retry_column:3843; _ensure_action_authorization_snapshot_column:3855; _ensure_legacy_action_plan_provenance_column:3870; _ensure_unsubscribe_receipt_columns:3888; _migrate_v16_to_v17:3909; _migrate_v18_to_v19:4037; _migrate_v21_to_v22:4293; _migrate_v24_to_v25:4504; _migrate_v34_to_v35:4987; _migrate_v36_to_v37:5175; _migrate_v44_to_v45:5452; _validate_cached_action_plan_snapshots:5591; _migrate_v40_to_v41:5656; _migrate_v39_to_v40:5696; _ensure_unsubscribe_claim_columns:6026; _ensure_unsubscribe_audit_columns:6049; _mark_legacy_processed_without_plan:6118; _create_durable_tables:6194; _create_indexes_and_triggers:6661; _validate_durable_state:7271; _validate_durable_rows:7451; _current_action_plan_rows:8305; _feedback_application:8452; _persist_action_plan:8591; _ensure_direct_action_rows:8707; get_email_reply_delivery_authorization:9487; claim_email_reply_dispatch:9574; release_email_reply_dispatch_retryable:9773; claim_email_unsubscribe_write:10209; get_email_unsubscribe_claim:10624; get_email_unsubscribe_state_snapshot:10644; get_email_unsubscribe_terminal_snapshot:10762; _validate_email_unsubscribe_terminal_snapshot:11061; get_email_unsubscribe_effect:11296; persist_email_unsubscribe_continuation:11392; get_email_unsubscribe_continuation:11506; append_email_unsubscribe_step:11556; list_email_unsubscribe_steps:11628; list_orphaned_email_unsubscribe_claim_owners:11673; release_orphaned_email_unsubscribe_claim:11715; repair_email_unsubscribe_effect_journal:11784; retire_email_unsubscribe_claim_for_direct_run:11971; recover_terminated_email_unsubscribe_claims:12010; mark_email_unsubscribe_uncertain:12049; release_email_unsubscribe_failed_operation:12080; release_failed_email_unsubscribe_for_explicit_retry:12140; advance_email_unsubscribe_phase:12239; persist_email_unsubscribe_terminal:12415; get_email_unsubscribe_receipt:12712; _classification_filters:13388; list_mailbox_action_states:13442; list_unsubscribe_classifications:13529; list_email_context_receipts:13691; backfill_email_unsubscribe_entry_url:13873; get_email_unsubscribe_entry_url:13930; list_email_classification_observability:14105; _provider_action_throughput:15091; email_processing_progress:15132; _apply_human_classification:16078; reapply_classification_to_copy:16288; append_action_plan_version:16419; append_action_attempt:16500; claim_next_direct_action:16627; _next_direct_action_row:16686; direct_action_ids_for_plan:16763; direct_action_statuses_for_plan:16795; direct_action_receipts_for_plan:16811; list_missing_unsubscribe_action_tasks:16836; get_email_unsubscribe_cancellation:16897; cancel_failed_email_unsubscribe_task:16920; _unsubscribe_cancellation_decision:16962; list_terminal_unsubscribe_tasks_missing_receipts:17028; claim_direct_action:17141; list_failed_direct_actions:17215; release_source_verified_direct_action:17269; claim_failed_direct_action_for_reconciliation:17344; _direct_action_predecessors_done:17396; _direct_action_dependency_satisfied:17465; complete_direct_action_attempt:17571; recover_stale_processing_actions:17747; list_flagged_important_between:17819; list_action_attempts:17838 |
| app/email_unsubscribe.py | session_failure:3728; _durable_result:4282; _persist_terminal:4323; _validated_preclaimed_write:4441; execute:4487 |
| app/email_unsubscribe_direct.py | _performed_effect:600; _persist:664 |
| app/email_worker.py | _recover_orphaned_unsubscribe_claims:1594; _persist_missing_unsubscribe_entry_skip:2434 |
| app/history_types.py | <module>:68 |
| app/store.py | _compact_settled_reply_task_input:9807; _runtime_operation_parent_exists:11316; begin_direct_email_unsubscribe_execution:14071; settle_direct_email_unsubscribe_execution:14092; retry_failed_effect_free_email_unsubscribe_tasks:14395; _email_unsubscribe_receipts_table_exists:20390; reconcile_failed_email_unsubscribe_tasks_with_terminal_receipts:20420; reconcile_done_reply_tasks_with_failed_current_run:24234; _email_actions_history_available:30739; _operation_logs_base_query:31160 |
| app/web_api/attempts.py | _email_payload:422 |
| app/web_api/registration.py | history_log_detail_url:153; history_log_item:194 |

### Existing deletion/update boundaries

The lexical runtime DELETE paths concern continuation cleanup, unperformed legacy claim/effect release, and explicit effect-journal repair. Completion and extension delete continuation (`10517`, `12696`); release_orphaned_email_unsubscribe_claim and release_email_unsubscribe_failed_operation require existing no-step/no-receipt/no-continuation conditions (`11694`, `12067`); explicit legacy retry requires the original failed Audit binding and exactly one effect (`12127`). Effect repair repoints the recorded step/claim/receipt and removes the superseded effect only after dependency checks (`11752`). These are actual current functions, not a proposed retention policy. No literal runtime DELETE of email_action_plans/email_actions/email_action_attempts/email_unsubscribe_steps/email_unsubscribe_receipts/email_unsubscribe_cancellations was found in email_store.py. This is scoped lexical evidence, not proof of no dynamic SQL or future writes.

## Evidence limits and validation

Production contains 3,362 NULL authorization snapshots, 54 non-NULL claim Audit IDs, 59 non-NULL effect Audit IDs, 11 nonempty predecessor digests, 1,039 nonempty receipt result_text values and 1,024 nonempty entry_url values. These aggregates prove real populated history; no content, entry URL, provider payload, cancellation decision body or native trajectory was read. Empty continuation/retry schedules cannot justify retirement. Current shared scalar/JSON business values and receipt identity must survive a later migration; no target/retirement conclusion follows from this inventory.

graphify navigation was limited to 19 Python modules (270,187 words), with 2,942 AST nodes, 11,682 edges and 25 communities. Cohesion scores: 0.01,0.01,0.02,0.05,0.03,0.02,0.03,0.03,0.02,0.04,0.07,0.04,0.07,0.11,0.06,0.09,0.08,0.14,0.1,0.2,0.2,0.23,0.23,0.29,0.4. Extraction model cost: 0 input / 0 output tokens. Deterministic graph is navigation only; source code supports the statements above. Corpus/cache/database/scripts are task-owned temporary artifacts and are removed after verification.

Validation passed **3,265 metadata-only assertions**. They cover exact 103-field one-to-one inventory, xinfo/default/PK flags, complete aggregates/storage totals, constraint token extraction, all index/FK/trigger metadata, actual/fresh comparison, AUTOINCREMENT sequence attribution and frozen source path/hash validity. git diff --check passed. No implementation tests, native turns, external provider calls, release writes or deployment were run. Point-in-time Store16/counts must be re-read after any Phase5C publication before later implementation.
