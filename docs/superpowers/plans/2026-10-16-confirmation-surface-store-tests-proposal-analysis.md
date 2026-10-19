# Workbench Store test-maintenance proposal

**Complete unselected test-only proposal, with three unchanged source-fixture failures still explicit.** The patch changes only `tests/test_workbench_store.py` when selected. Actual application/tests/index remain untouched. The full retained module collects and executes; it is **47 passed /3 failed**, with no skips, xfails or stubs.

Pinned base `7111ea47546ac7cb242ff7267993868eacec6561`. The candidate includes the exact Store/schema, executor/models/runtime, surface and release proposal hashes in the JSON. This does not add a new source variant or alter retirement's empty-confirmation source contract.

## Exact inventory

Original:62 test definitions /64 parametrized cases. Remove14 tests devoted entirely to the canceled confirmation feature and two exclusive helpers. Retain48 test definitions /50 cases. Twelve retained tests change;36 test definitions and the three remaining helpers preserve exact ASTs. No function is added. All three ordinary source13 migration functions remain byte/AST exact.

Removed helpers: `_waiting_confirmation`, `_repair_historical_workbench_columns`. Removed test definitions:

- `test_source13_confirmation_controls_survive_migration_and_existing_recovery`
- `test_source13_blank_proposer_recovery_is_once_across_turn_states`
- `test_confirmation_quiescence_exposes_only_truthful_public_boolean`
- `test_stale_prior_run_quiescence_cannot_unlock_confirmation`
- `test_recovery_leaves_waiting_confirmation_turn_unchanged`
- `test_transition_to_waiting_confirmation_releases_worker_lease`
- `test_confirmation_list_redacts_arguments_and_execution_claim_exposes_them_once`
- `test_claim_owner_can_finish_receipt_after_turn_is_stopped`
- `test_reconcile_confirmation_claims_only_when_abandoned`
- `test_confirmation_receipt_rejects_non_owner_and_expired_claim`
- `test_confirmation_arguments_reject_nonfinite_json`
- `test_confirmation_creation_and_control_events_are_atomic`
- `test_waiting_confirmation_terminalization_resolves_pending_confirmations`
- `test_generic_completion_rejects_reserved_confirmation_transitions`

The JSON lists every retained name and changed owner. Mixed execution-owner tests lose only their confirmation subcases; append-event and completion owner/lease checks remain. The negative bypass test keeps its previous assertions and now also proves all removed methods and old table/index objects absent.

## Retained current behavior

The snapshot concurrency test changes seven-element unpacking to six and preserves the snapshot consistency assertions. Native current index names/typed source fields replace obsolete physical columns; ordinary index installation, repair, cursor/query planning and no-temporary-sort assertions remain. The1000-turn latest-status and50000-unrelated-event bounded-work fixtures use current scalar INSERT parts without reducing rows or thresholds. Attachment failure injection targets `tasks` only for the attachment record type; native `insert_task12`, typed reads and exact metadata/file/orphan assertions preserve the prior behavior. Current Store schema-marker read uses the State projection.

## Evidence and known limitation

1. Original module against the four application proposals: collection exit2 on removed `ConfirmationStatus`,0 collected.
2. Removal-only intermediate: full50 cases, **12 failed /38 passed**. Nine current State/Tasks20/tuple/index fixture failures were exposed before adaptation.
3. A command used the original checkout cwd; after identifying it, its complete unchanged-module result was retained as a control: **15 failed /49 passed** across64 cases. The same three source13 config-declaration failures already occur there.
4. Final proposal: full50 cases, **3 failed /47 passed**,74.85 seconds. No skips, xfails or stubs. Ruff, pinned3.13 compile, exact reverse/reapply and whitespace-error-all checks pass.

Remaining failures are:

- `test_source13_migration_preserves_user_text_and_private_resume_context`
- `test_source13_migration_preserves_attachment_request_hash_and_file_bytes`
- `test_source13_migration_preserves_stable_per_task_sequence`

Each fails in unchanged `advance_source17_to_current_store` before its migration assertions: `prepare_database_schema_release` → paired `_project_config_source` → `capture_config_declarations`, with `ValueError: unsupported config source declaration`. Read-only metadata from the disposable source fixture shows Store marker2026-10-13.5 /Email51 and config declaration fingerprint `637f041bcaded37f3b761916b827fc12c42fe9f71e674134a57d49cf10ede5ec`, matching none of the four accepted source profiles. The existing Configs fixture acceptance records a related archived hybrid-source limitation and distinguishes explicit synthetic targets from supported publication.

The parent expressly agreed to keep these three ordinary source13 assertions intact and record the limitation. Their shared fixture helper and all historical source factories remain exact. No synthetic normalization, new source support, nonempty confirmation disposition or assertion weakening is proposed. These three ordinary assertions have not gained new execution proof; legitimate source fixture/publication work remains a separate root obligation, including any postmigration physical-read adaptation after that boundary is resolved.

## Artifact

`2026-10-16-confirmation-surface-store-tests-proposal.patch`: **39911 bytes**, SHA256 `e7bb38f61e7ee0285b256ceb2df44dadf002a249b9727667a87eb43959870dda`.

The companion JSON contains all62 original names partitioned into14 removed /48 retained, all changed/preserved owners, input hashes, exact command/log evidence and source-profile fingerprints. This proposal is ready for root review as a finite current-test maintenance change; it does not establish a wholly green retained module.

The task-owned temporary root was removed after artifact verification. Its absence and the already-absent default pytest root were verified; exact paths are recorded in JSON. No application/test/index commit, production database, provider, native CLI, network, main, push or deployment action occurred.
