# Canonical Email selected-12 integration validation (2026-10-17)

This is a local synthetic integration of the independently accepted R6 Email
component. It selects 12 ordinary tables after both current constructors and
after paired publication: the accepted 13 names minus
`email_classifications` and `email_messages`, plus `emails`. It does not claim
actual-data acceptance, capacity acceptance, deployment, or production behavior.

## Scope and source roles

The selected current `EmailStore` reads classification/Message public shapes
through the finite projections and writes their typed canonical columns through
accepted component operations. Its current scan/copy lookup, classification
lists and detail, search/context/thread, training inclusion, provider locator,
reply/unsubscribe authorization, plan/direct-action claims, completion and
recovery all use this selected graph. The current external consumers are
`AutoReplyStore._get_current_email_task_authorization`,
`AutoReplyStore._list_failed_operation_logs_with_count`,
`AutoReplyStore._operation_logs_base_query`,
`audit_web._email_action_queue_snapshot`, `audit_web._queue_attention_rows`,
and `email_training_labeler._all_classified_identities`.

The historical native source factories `create_current_email_classifications`,
`create_current_email_non_action_schema`, `create_current_email_runtime_schema`,
and `create_current_email_migration_ledger` retain their source roles.
`EmailStore._migrate_prototype_schema` and every versioned native migration
through `_migrate_v49_to_v50`, plus `email_schema_release.py`,
`action_email_storage.py`, and `action_email_definitions.py`, remain native
source/migration oracles. The paired source
fixture deliberately uses a fresh Email declaration with the actual Actions
variant, one of the four R4 supported exact combinations. Its class 11 has a
matching Message; a separate test inserts class-only data and checks refusal
without mutation.

The 50 selected `EmailStore` functions using canonical projections or
finite typed writes is: `_validate_durable_rows`, `_feedback_application`,
`_check_classification_identity`, `_upsert_message`, `persist_scan_result`,
`get_email_reply_delivery_authorization`, `claim_email_reply_dispatch`,
`release_email_reply_dispatch_retryable`, `claim_email_unsubscribe_write`,
`_validate_email_unsubscribe_terminal_snapshot`,
`persist_email_unsubscribe_terminal`, `list_classifications`,
`list_mailbox_action_states`, `_classification_search`,
`list_unsubscribe_classifications`, `list_email_context_thread`,
`get_classification`, `owner_labelled_stamp`, `owner_labelled_examples`,
`get_classification_by_stable_identity`, `has_stable_classification_record`,
`classified_stable_message_identities`, `get_email_unsubscribe_entry_url`,
`stable_classification_uids`, `classified_provider_uids`,
`classified_provider_targets`, `list_email_classification_observability`,
`list_training_examples`, `list_selected_training_records`,
`list_provider_folder_correction_conflicts`, `email_processing_progress`,
`get_stored_email_locator`, `get_provider_classification_state`,
`get_provider_classification_states`, `_verify_training_snapshots`,
`_apply_human_classification`, `reapply_classification_to_copy`,
`append_action_plan_version`, `_next_direct_action_row`,
`list_missing_unsubscribe_action_tasks`, `_unsubscribe_cancellation_decision`,
`list_terminal_unsubscribe_tasks_missing_receipts`,
`repair_current_action_plan`, `claim_direct_action`,
`list_failed_direct_actions`, `release_source_verified_direct_action`,
`claim_failed_direct_action_for_reconciliation`,
`complete_direct_action_attempt`, `recover_stale_processing_actions`, and
`list_flagged_important_between`. Constructor/declaration pathways changed are
`EmailStore._open_connection`, `_connect`, `_initialize`,
`_validate_schema_shape`, current Actions/Configs/Store recognizers,
`EmailActionSchemaRelease.apply`, `_verify_target`, and `rollback`, plus the
paired JSON-pipe metadata transport. The native historical factory methods
before the selected runtime remain source constructors/migration oracles.
No separate pre-edit file snapshot was retained; the accepted component/source
capsule hashes and parent pause pin the prerequisites, while this inventory
records the integration boundary for review.

The current parent declaration check requires the exact canonical `emails`
table and absence of both native parents. Store's existing known-object repair
path recognizes the canonical Actions FK only when the canonical parent exists.
The paired JSON child carries the finite `email_canonical` inverse metadata;
rollback restores both native parents before restoring historical Actions
declarations. No row or body backup is carried in metadata.

## Behavioral constraints

`persist_scan_result` still atomically writes Message, classification, plan,
cursor and actions. Message upsert retains stable-identity conflict columns
and AUTO allocation behavior. Copy reapplication updates the class locator,
class thread and timestamp plus Message locator, leaving Message thread and
timestamp unchanged. Direct-action completion retains both old-locator
conditions, current claim/plan checks and both native timestamps. Raw folders,
RFC/message headers and body forms remain distinct facts. The action child FK
references the exact unique `emails.classification_id` alias.

## Focused evidence

The R6 component's independent acceptance is pinned in
`docs/superpowers/specs/2026-10-17-email-canonical-component-freeze-r6.json`
(SHA-256 `aaeeed85f0191551fb6ce8d03c94892639753a3dba80929043653d16263d1951`)
and `2026-10-17-email-canonical-component-acceptance-r6.json`
(SHA-256 `96243105627e62aa114e49ec67df659ce36827ef1b448e273068ea7c9f7e7fbe`).
Integration checks use isolated synthetic SQLite
files and fake provider data only. Focused results before caller freeze:

| Selected check | Result |
| --- | --- |
| Fresh/cold selected graph, mixed-parent refusal, classified-vs-Message-only reader, indexed direct claim (`test_email_canonical_current12.py`) | 6 passed / 131.30 s |
| Previously RED public rescan locator and immutable snapshot cases, unchanged after R6 | 2 passed / 31.32 s |
| Known constructor repair, malformed declaration refusal, occupied repair names | 19 passed / 471.13 s |
| Paired actual and fresh-candidate publish/inverse/repeatability | 1 passed / 104.98 s; 1 passed / 96.28 s |
| Paired actual target receipt writes, native IDs and allocator waters through inverse | 1 passed / 117.68 s |
| Public labeler certain and uncertain outcomes with existing assertions | 2 passed / 74.36 s |
| Attention indexed query with 1200 unrelated replies and bounded VM work | 2 passed / 50.24 s |
| Old-oracle vs selected direct poll, 3000 unrelated Actions, indexed EXPLAIN and VM step budget | 1 passed / 35.54 s |
| Email task authorization/plan-switch transaction race | 1 passed / 32.98 s |
| Final public direct-action completion, stale-plan and transaction rollback, copy decision, and training label correction | 5 passed / 139.52 s |

The final group retained its public behavior assertions after translating one
current-target retired trigger mutation. No actual data, source database,
provider or send operation was used for these tests.

Final static checks: Ruff passed all 17 scoped Python files, AST parsing passed
all 17, and `git diff --check` passed. The sole unused Store import found by
Ruff had no Store reexport consumers and was removed without changing its
business action helper or tests.

An independent QUALITY selection then found two current-target fixture gaps:
one public rescan test still read the retired native parents, and a public
direct-action poll test's trace shim did not accept the current connection
signature. Both exact nodes reproduced RED (2 failed / 31.68 s), then passed
after translating only those test accesses (2 passed / 35.09 s). The rescan
test retains its immutable business snapshot, plan, action, locator and
training assertions. The poll retains 1,200 unrelated sent-reply rows, retry
window and plan-retirement claims, the no-classification-scan assertion, and
the <300,000 VM-step budget. Its old Actions index assertion named
`idx_actions_email_public_key(record_type,email_public_key)`; the selected
query uses `idx_actions_postfix_identity(record_type,channel,delivery_key)`
for the `record_type` restriction and the canonical `emails` unique
classification-alias index for the parent lookup. The observed plan and step
budget establish this bounded synthetic case, not a whole EmailStore test-file
or actual-data performance result.
