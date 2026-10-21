# Task 1 report: Action field ownership and DDL

Status: implementation and scoped verification complete. The source-copy checker opened the supplied SQLite file in read-only/query-only mode and verified its SHA-256 was unchanged before and after inspection. Tests use only synthetic databases.

## Outputs

- `tools/action_entity_contract.py`: source declaration inventory, field/DDL validation, and read-only source CLI. Validation executes the complete rebuilt target SQL in an in-memory database. The inspector never initializes Store.
- `tests/test_action_entity_contract.py`: declaration-derived ownership, mutation rejection, source-copy-independent synthetic SQLite DDL and identity/receipt/state checks.
- `docs/action-entity-field-map.json`: all source fields, owners/dispositions/inverse rules, common-state mappings, identity/cardinality/clock rules, coexistence and index policy.
- `docs/action-entity-target.sql`: complete rebuilt `actions` declaration with one `_kind=70`, `record_type='action'` kind plus unchanged existing kinds, indexes/triggers and Action-owned receipt/observer relations.

The common Action payload is one JSONB object (`action_data`) with fixed typed generated virtual columns. Unchanged records do not get dozens of nullable source columns or per-source Action layouts. The `actions` global discriminator remains only to distinguish Action from unchanged non-Action records; source/origin does not select Action layout. New Actions can have `legacy_key NULL`; existing non-Action namespace constraints/indexes/triggers remain equivalent. Existing Actions ids are not reassigned from source public IDs. Migration/inverse identity is explicit `(source table, original typed identity)` metadata, never a runtime alias.

## Ownership and cardinality decisions

- Seven declared source families contribute 30,110 rows in the supplied copy (counts below). Every declaration field is listed exactly once in the committed map; tests compare it against the independent declarations in `EMAIL_FIELDS`, `SOURCE_COLUMNS`, and prepared definitions. Unmapped arbitrary attempt payloads/native context are not copied.
- `email_actions` stored current facts belong to Action. The current `current_action_payloads().layouts['email_actions']` stores only action identity/plan/classification/account, operation/parameters, config version, status, started/next-attempt/created/updated clocks. Its `attempt_count`, `finished_at`, provider operation/target/result id, and error public fields are projections from the latest `email_action_attempts` receipt (with pending/processing-specific empty/current-start behavior); they are not Action payload duplicates. Receipts preserve attempt number, exact status, provider fields/error, and separate start/finish clocks.
- `candidate_action_attempts` with the same exact external key and one canonical external-result owner can be multiple execution observations of one Action. The observed duplicate group has two rows, two execution ids, both statuses present, one NULL local outcome and one distinct text outcome, while canonical operation/target/provider result each have one value. The target keeps one Action and one canonical provider-result receipt, plus two per-execution receipt facts (`execution_id`, `action_index`, status, `outcome_json`, created/updated clocks). It does not overwrite the NULL or local result with the provider body. Conflicting canonical operation/target/result owners remain a conflict for migration resolution; the key alone never authorizes a merge.
- `external_action_results` owns canonical provider receipt fields once. Existing candidate projection reads a provider result only for explicit `verified` with local result SQL NULL and exact external-key owner (`app/action_storage.py` result projection and `preserve_candidate_result_references`). That reference behavior does not erase the local NULL fact.
- `sent_replies.send_result_json` keeps exact SQL NULL versus text. Existing readback shares a value only through exact external-key result ownership; exact text/type equality is a reference, while distinct local result text remains its own receipt fact (`app/action_storage.py` sent-result projection; `app/sent_result_schema.py`). In the copy, 478 sent rows have NULL local result while 486 have a nonblank external key.
- `outbound_postfixes` owns prepared delivery channel/key/body/feedback/version and preparation time. `postfix_dispatch_created_at` and `postfix_receipt_created_at` map to separate receipt clocks; counts are 206 dispatch clocks and 866 receipt clocks. Prepared does not mean dispatched, and a dispatch/receipt clock or result JSON does not set Action status.
- `sent_reply_observers` is an observer relationship, not a second effect: 486 rows point to 486 replies and represent 486 unique reply/run pairs. Receipt and observer tables are owned relations with Action foreign keys and no independent source-kind lifecycle.

## State mapping

`Action.status` supports the source-backed union of email lifecycle and reviewed candidate observation values, and remains nullable where no source status exists (including historical prepared messages). Each email attempt/reviewed execution observation retains its own exact receipt status. For one Action shared across reviewed observations, the current status is the final explicit source observation by `updated_at, execution_id, action_index, id`; this records the source's current observed lifecycle without erasing failure-then-verified history. The reviewed writer's `verified` status follows its explicit successful verification/ledger checks; no JSON or timestamp alone promotes status to verified/done. The validator derives both enums from current declarations and DDL constrains both columns to those source-backed sets.

## Source-copy evidence

- Database SHA-256: `c16afd2ea0e45349aa1cee3d246ef23d87964998b809acd5e5022989adb3ddd1` (same before/after). Total Actions rows: 102,413; unchanged kinds in coexistence: 72,303.
- Equal legacy public-id groups across covered families: 11,087; old numeric/public IDs do not define unified identity.
- Duplicate candidate external-key groups: 1. Duplicate observation facts: 2 rows, 2 distinct execution IDs, 2 statuses present, 1 NULL local outcome, 1 canonical operation, 1 canonical target, 1 canonical provider result.
- Prepared clocks: 206 dispatch timestamps and 866 receipt timestamps. Sent-result distinctions: 478 NULL result fields and 486 nonblank external keys. Observer counts: 486 rows, 486 sent replies, 486 unique reply/run pairs.

Source row counts are evidence, not test constants:

| Source family | Rows |
| --- | ---: |
| `candidate_action_attempts` | 398 |
| `email_action_attempts` | 13,380 |
| `email_actions` | 11,284 |
| `external_action_results` | 574 |
| `outbound_postfixes` | 1,481 |
| `sent_replies` | 2,507 |
| `sent_reply_observers` | 486 |

## Index dispositions

All 133 explicit indexes from the current `actions` declaration remain in the rebuilt target; their exact names and per-index disposition are emitted by the inspector JSON. Existing indexes are retained for unchanged record kinds. `idx_actions_public_id` retains its role for those kinds with the common Action excluded. No existing index is deleted. Four indexes are added: `idx_actions_action_external_key`, `idx_actions_action_delivery_key`, `idx_action_receipts_action`, and `idx_action_receipts_execution_order`. Reasons are frozen in the field map; the CLI output includes every index name and disposition.

### Per-index disposition

| Index | Decision | Reason |
| --- | --- | --- |
| `idx_actions_attempt_sequence` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_audit_run` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_attention_events_lookup` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_attention_proposal_tasks_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_attention_proposal_tasks_lookup` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_attention_tasks_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_attention_tasks_lookup` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_project_evidence_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_signal_public_id` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_task_anchor_links_lookup` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_task_anchor_links_task_id` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_task_date_evidence_lookup` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_task_evidence_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_task_evidence_lookup` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_task_relations_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_task_relations_lookup` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_task_relations_reverse` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_task_signals_dedupe_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_work_cluster_tasks_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_business_work_cluster_tasks_lookup` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_candidates_task` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_consumer_run` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_action_attempts_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_action_plans_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_action_plans_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_actions_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_actions_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_actions_2` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_actions_3` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_direct_retry_requests_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_feedback_requests_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_feedback_requests_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_feedback_requests_2` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_reply_dispatch_claims_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_reply_dispatch_claims_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_reply_dispatch_claims_2` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_reply_receipts_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_reply_receipts_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_unsubscribe_cancellations_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_unsubscribe_claims_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_unsubscribe_claims_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_unsubscribe_continuations_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_unsubscribe_effects_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_unsubscribe_receipts_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_unsubscribe_receipts_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_email_unsubscribe_steps_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_history_created` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_operation_identity` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_operation_processing` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_email_public_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_errors_reply_task_settlement` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_errors_unresolved` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_execution_alias_candidate` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_execution_binding` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_execution_receipt_operation` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_execution_receipt_run` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_external_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_fb_decision_feedback_round` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_fb_decision_item` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_fb_event_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_fb_event_token` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_fb_transition_batch_round` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_fb_transition_feedback` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_fb_transition_round` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_global_business` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_history_public_id` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_intent_authorization` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_intent_operation` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_intent_run` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_memory_attempt` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_memory_public_order` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_memory_status` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_memory_unique` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_observer_identity` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_operation_public_id` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_parent_candidate` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_parent_review` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_postfix_identity` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_public_id` | retained | Retained for unchanged record kinds; common Action excluded. |
| `idx_actions_reply_created` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_reply_current_trigger` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_reply_oa_history` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_reply_public_order` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_reply_run_recovery` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_reply_status` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_reply_trigger_history` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_reply_trigger_message` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_reviews_candidate` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_selection_candidate` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sent_external_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sent_history` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sent_result_owner` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sent_result_receipt` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_signal_inline_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_signal_inline_source` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_signal_shared` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_skill_export_revision` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_skill_load_config` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_skill_load_pid` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_business_task_dingtalk_links_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_business_task_dingtalk_links_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_business_task_events_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_business_task_follow_up_send_attempts_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_business_task_follow_up_send_attempts_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_business_task_follow_ups_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_business_task_follow_ups_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_business_task_todo_sync_outbox_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_business_task_todo_sync_outbox_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_follow_up_drafts_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_follow_up_drafts_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_follow_up_drafts_2` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_follow_up_drafts_3` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_follow_up_drafts_4` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_follow_up_drafts_5` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_follow_up_send_attempts_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_follow_up_send_attempts_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_follow_up_send_attempts_2` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_task_todo_sync_outbox_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_task_todo_sync_outbox_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_work_todo_dingtalk_links_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_work_todo_dingtalk_links_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_work_todo_dingtalk_links_2` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_work_updates_0` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_sync_work_updates_1` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_td_meeting_due` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_td_meeting_lease` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_td_meeting_memory_unique` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_td_task_due` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_td_task_lease` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_td_task_memory_unique` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_td_wechat_status` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_td_wechat_task_generation` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_text_public_key` | retained | Retained with its existing predicate for unchanged record kinds. |
| `idx_actions_action_external_key` | added | Unique common Action stable external identity. |
| `idx_actions_action_delivery_key` | added | Unique prepared delivery identity. |
| `idx_action_receipts_action` | added | Action receipt history lookup. |
| `idx_action_receipts_execution_order` | added | Execution observation ordering by execution_id/action_index. |

## Storage shape estimate

The copy contains 72,303 rows that remain unchanged. The common layout adds one nullable `action_data` physical slot to the shared table; generated Action columns are virtual and occupy no row payload. At roughly one extra serial-type header byte for a NULL payload, that is about 72,303 bytes (~70.6 KiB) on those rows, versus roughly 2,241,393 bytes (~2.14 MiB) for 31 additional NULL physical fields. This is an order-of-magnitude record-header estimate, not an on-disk page-size benchmark.

## TDD and verification

1. RED: `python -m pytest -q tests/test_action_entity_contract.py` initially failed at import because `tools.action_entity_contract` did not exist (exit 2).
2. GREEN: focused contract suite: `python -m pytest -q tests/test_action_entity_contract.py` — 10 passed.
3. Source-copy CLI: `python tools/action_entity_contract.py --database /tmp/ceo-action-entity-d0ce4814/source.sqlite3 --output /tmp/ceo-action-entity-task1-report.json` — exit 0. JSON contains hashes, declaration field counts/locations, source row counts, collision/cardinality/timestamp counts, and per-index disposition; it contains no source payload values.
4. Validator rebuilt/compared static DDL and executed the entire target SQL in an in-memory SQLite database. Focused synthetic tests execute the target table, common indexes, receipts, observers and owner triggers; they check identity, key uniqueness, text affinity (`action_plan_id='007'` stays text), NULL versus JSON text, failed/uncertain distinctions, execution outcome cardinality and foreign-key/owner constraints.
5. `python -m compileall -q tools/action_entity_contract.py` — passed. `git diff --check` — passed. No full suite, provider calls, network sends, deploy or source database mutation.

## Files and hashes

- Contract SHA-256: `ab0c5b9faf1dba741650464b714215c94fad35cdd0b3579b55a22f12c6df93dd`.
- Target DDL SHA-256: `447c18cd7b40ae875ea077bf7c0ff39192688ecd81ef35e8df8887b6ee151836`.
- Full per-field mapping is in the committed contract; exact existing/new index-name disposition is in the generated inspector output.

## Remaining concerns

- The target freezes contract and coexistence behavior only; runtime/migration changes belong to Task 2. Migrators must preflight all candidate-key groups and stop if canonical operation/target/result ownership differs before key-based merge.
- The in-memory harness validates SQLite behavior and exact declaration coverage, not a production migration or a physical database size benchmark.

## Review round 1 fixes (base `012175bb1`)

- Added a migration-only `source_identity(source_table, source_id)` representation keyed by source namespace, original scalar type, and value. The synthetic same-public-ID case now exercises this identity representation and allocates separate physical Action IDs for `email_actions/'7'` and `candidate_action_attempts/'7'`; integer `7` and text `'7'` also remain distinct. The static Action remains free of a legacy alias.
- Every source field now has a field-specific inverse reconstruction statement and concrete source consumer function locations. The contract validator rejects absent inverse/consumer metadata for retain, reference, and retire dispositions, and verifies each consumer points to a declared function in an existing `app/` module.
- `Action.status` now admits the exact union of declared `email_actions.status` and `candidate_action_attempts.status` values: pending, processing, done, skipped, failed, dispatched, verified, confirmed_no_effect, uncertain. For shared reviewed Actions, current status is the last explicit observation ordered by `updated_at, execution_id, action_index, id`; raw observations and their local outcome JSON remain separate receipt facts. The reviewed writer semantics in `app/reviewed_candidate_store.py` only produce `verified` after its explicit verification/ledger checks. No provider JSON or timestamps establish success; prepared history with no status stays NULL. A later explicit verified observation supersedes an earlier failed observation without deleting either fact.
- Added `action_receipts.provider_receipt_id`, a self-FK for a NULL local outcome that references a canonical provider-receipt version owned by the same Action. Corrections append a new provider receipt; a synthetic test proves the old NULL reference still resolves to its original body and carries no duplicated body. This is the technical version linkage for existing exact external-key/result references.
- TDD fix evidence: the expanded test first failed at collection because `source_identity` was absent. After implementation, the focused tests exposed two expected interim gaps (the updated source-backed state wording and receipt-version join fixture); both were corrected. Final focused run: `python -m pytest -q tests/test_action_entity_contract.py` — **11 passed in 6.51s**.
- Independent bounded source-copy CLI: `python tools/action_entity_contract.py --database /tmp/ceo-action-entity-d0ce4814/source.sqlite3 --output /tmp/action-entity-contract-fix-report.json` — exit 0. Source SHA-256 remained `c16afd2ea0e45349aa1cee3d246ef23d87964998b809acd5e5022989adb3ddd1`. Observed counts: candidate attempts 398; email attempts/actions 13,380/11,284; external results 574; prepared postfixes 1,481; sent replies 2,507; observers 486. One duplicate candidate-key group has two observations, both statuses, two execution IDs, one NULL local outcome, and one canonical operation/target/provider result. Prepared dispatch/receipt clocks are independently present on 206/866 rows; sent replies have 478 NULL local provider-result references and 486 nonblank external keys. These are observed evidence, not test constants.
- `python -m ruff check tools/action_entity_contract.py tests/test_action_entity_contract.py`, `python -m compileall -q tools/action_entity_contract.py tests/test_action_entity_contract.py`, and `git diff --check` — passed. The validator executes the rebuilt full coexistence DDL in memory and checks it against actual source declarations. No Task 2/runtime migration, full suite, provider call, network send, production mutation, or deployment was performed.
- Updated artifact SHA-256: field map `34f5505dc03ade2008e1aac8776e6f2742869574b1984000cecc9cf086ee9837`; target DDL `97dc9d04e50264bd54941c3c1974da240b959eba5699f07ca70bbeff57ec497c`. The validator enforces that the static DDL exactly equals the generated declaration.

Review fix limits: this contract freezes Task 2 inputs but does not implement a migration or runtime status writer. Full-copy inverse/reforward and production rollout remain outside Task 1.

## Review round 1 source-ownership correction

- Compared the logical `EMAIL_FIELDS['email_actions']` public row against `current_action_payloads().layouts['email_actions'].fields` and the actual `shared_action_definitions.email_projection` implementation. The six public values `attempt_count`, `error`, `finished_at`, `provider_operation`, `provider_result_id`, and `provider_target` are not stored on the current parent row. They are derived from the latest child attempt; `attempt_count` counts attempts, and the five terminal values are blank while parent status is pending/processing, otherwise read from the latest receipt. `started_at` remains separately stored on Action and uses the existing processing-versus-terminal projection behavior.
- Marked each of the six source-map fields as `derived_projection`, assigned its factual owner to Action Receipt, recorded the exact current projection/inverse rule and consumer, and removed `finished_at` plus the four `current_provider_*`/error mirrors from the common Action payload declaration. The `email_actions` map now distinguishes physical current-layout fields from derived public projection fields, and the validator checks that split against `current_action_payloads()` rather than trusting the logical field inventory alone.
- The new synthetic regression executes the actual `email_projection('email_actions')` SQL with a parent and receipt for pending, processing, and done states. It confirms receipt count, stored processing start, latest attempt start for terminal state, and the original blank/latest terminal-value semantics. The first run failed on the mistaken one-value expectation for terminal columns; after using the actual per-column results, it passed. The DDL regression now asserts all duplicate parent fields are absent.
- Final focused verification after this correction: `python -m pytest -q tests/test_action_entity_contract.py` — **12 passed in 8.29s**; Ruff, compileall, and `git diff --check` passed. The source-copy CLI reran successfully and reported the same source SHA and source counts above.
- Final updated SHA-256: field map `d280ea1bdc3ea1f2d52b57b9486593069829d28984139e815228cfcd68e04046`; target DDL `0394f77fd8ae08d267bd02d3d56159e6e813e0e5018b570316e2fb566905131b`.

## Review round 2 fixes

- Closed inverse-ownership finding: each field’s structured `inverse_mapping` now names its concrete owner table, owner field, and where required an immutable reference traversal. `email_actions.config_version` resolves through the `Action.action_plan_id` reference into the immutable `email_action_plans.config_version`; `email_action_attempts.status` is owned by `action_receipts.status`, never the Action lifecycle field. Consumer assertions name each source field, consumer function, actual target owner and target field; the validator checks completeness and structural consistency against the fixed target declarations. Synthetic mutations remove inverse/consumer metadata for each disposition and contradict both a reference owner and retained Action field; each is rejected. `email_actions.action_id` and `email_action_attempts.action_id` use the existing `_direct_action_id(Action.action_plan_id, Action.operation)` formula, so the conversion-only mapping is not misrepresented as durable identity. Also corrected the reviewed inverse for `sent_replies.recall_error` to its actual common `Action.recall_error` field.
- Closed receipt-version finding: `action_receipts.supersedes_provider_receipt_id` is an append-only technical link to an immutable prior provider-result receipt on the same Action. Its predecessor must have a lower ID, a same-Action trigger guards ownership, a partial unique index permits one successor per version, and an immutability trigger rejects changes to provider body/digest, supersedes links, and already-set observation references. The exact `provider_receipt_id` on a NULL-outcome observation remains fixed; current canonical version is the chain tip reachable from that exact referenced version. Independent, unlinked historical receipt facts remain separate roots. This mirrors `app/action_storage.py:preserve_candidate_result_references` and the accepted ambiguous-to-sent correction in `app/reviewed_candidate_store.py:record_candidate_external_action`.
- The synthetic regression inserts an ambiguous original provider receipt, a NULL local-outcome observation referencing it, then a corrected sent receipt that supersedes the original. It verifies that the original body remains resolvable through the observation, the reference-rooted chain reaches the corrected tip, a second successor and mutations to the body, supersedes link, or established observation reference are rejected, and SQL NULL versus text `null`/`{}` remain distinct. The DDL validator executes the full rebuilt coexistence declaration in-memory.
- Focused TDD/verification: `python -m pytest -q tests/test_action_entity_contract.py` — **13 passed in 5.04s**. Earlier failing run exposed expected gaps while the static DDL was stale and then a source/target inverse mismatch; after correcting the inverse mapping, regenerating DDL and aligning the mutation assertion, the focused suite passed. `python -m ruff check tools/action_entity_contract.py tests/test_action_entity_contract.py`, `python -m compileall -q tools/action_entity_contract.py tests/test_action_entity_contract.py`, and `git diff --check` — passed.
- Independent bounded source-copy check: `python tools/action_entity_contract.py --database /tmp/ceo-action-entity-d0ce4814/source.sqlite3 --output /tmp/action-entity-task1-round2-output.json` — exit 0. The read-only copy hash remained `c16afd2ea0e45349aa1cee3d246ef23d87964998b809acd5e5022989adb3ddd1`; it observed 398 candidate attempts, 13,380 email attempts, 11,284 email Actions, 574 external results, 1,481 prepared postfixes, 2,507 sent replies, and 486 observers. Prepared dispatch/receipt clocks were present on 206/866 rows. These are independent source observations, not tests constants. The output also reports the source has 102,413 physical Action rows and the target DDL hash below.
- Final round-2 hashes: field map `1a127daca36292a092949177ae8d077a0f0bf93f75eefc13e60affa8642f320c`; generated target DDL `b8978b42e3626b94dfd2c41ed0866683d6a88ae77443a2b44bec65577fdd88bc`. No runtime Task2 migration, full suite, provider call, network send, production mutation or deployment was performed.

## Review round 3 fixes

- All per-field inverse prose now comes from one deterministic formatter over `inverse_mapping`: it names the owner table (and record type where applicable), owner field, and exact structured traversal; the validator requires an exact formatter match. For `email_actions.config_version`, this yields the immutable Action Plan lookup through `Action.action_plan_id` to `email_action_plans.action_plan_id`, then `config_version`. A synthetic mutation restores the former contradictory direct-Action wording and the validator rejects it.
- Receipt ownership now survives UPDATE: `action_receipts.id` and `action_id` are immutable; setting a previously NULL `provider_receipt_id` by UPDATE requires a provider-result receipt with the same Action owner. Already-set exact references and supersedes links remain immutable. Synthetic coverage verifies a same-Action NULL-to-reference update succeeds, a cross-Action NULL-to-reference update fails, changing a receipt's owner fails, and changing its ID fails. The initial new test run found a SQLite fixture setup issue (the current Actions table has an unrelated FK mismatch when inserting its second synthetic Action with FK checks enabled); the fixture now creates that Action with FK checks disabled, then re-enables checks for the receipt assertions. Final focused result: `python -m pytest -q tests/test_action_entity_contract.py` — **13 passed in 5.27s**.
- Scoped checks: `python -m ruff check tools/action_entity_contract.py tests/test_action_entity_contract.py`, `python -m compileall -q tools/action_entity_contract.py tests/test_action_entity_contract.py`, and `git diff --check` — passed. Bounded source-copy CLI `python tools/action_entity_contract.py --database /tmp/ceo-action-entity-d0ce4814/source.sqlite3 --output /tmp/action-entity-task1-round3-output.json` — exit 0; source hash remained `c16afd2ea0e45349aa1cee3d246ef23d87964998b809acd5e5022989adb3ddd1`, with observed source counts unchanged from round 2. No source-copy writes, runtime migration, provider call, network send, or production mutation.
- Final round-3 hashes: field map `013e61cec3de521762ed2fd771c1d28075f8f6c621aee43a4d7eab2b23ecd953`; generated target DDL `5e011b5e2d98526d2bccd888d5203973e1635acb6a48e15155f25c847462a8a5`.
