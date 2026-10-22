# Task5C1 implementation report

## Result

Implemented the source-faithful receipt correction and local-result ownership behavior in the isolated Task5C1 worktree. Task5C2 inverse, cold-old/current-data round trip, and full-copy proof remain intentionally outside this subtask.

## Source behavior and target changes

The predecessor prior-action reconciliation creates the canonical result only when no canonical result exists. If the existing canonical body equals the supplied body, the current prior execution observation stores a NULL result reference. If the body differs, the canonical receipt and its first source metadata remain unchanged, and the supplied result stays local to that exact prior observation. The target now follows those cases: an existing different canonical result is not overwritten; an equal result reuses its body receipt; a differing eligible prior result remains inline on its own observation.

For the old ambiguous-to-sent correction, an unchanged nested provider response permits replacing the verification envelope. The target writes the corrected envelope as the current immutable body, advances the stable canonical binding and current observation, preserves independent earlier observation/Sent references, and retires only the replaced wrapper when no source fact references it. A later stale writer can still attach its exact old receipt to its own observation or Sent fact, but ordinary binding discovery cannot move the established canonical pointer backward. Explicit correction and delivery-readback callers advance the canonical pointer with an exact owner and compare-and-set old reference.

The canonical source tuple remains on one reference-only metadata receipt: first operation, target, run, and source clock are preserved. The current result digest is derived from the body currently referenced by that binding. It does not rewrite first metadata to reflect a later verification time. Sent continues to preserve its own first sender and clock; it does not select the canonical first run or clock. Prepared, Sent, candidate observation, and canonical body references remain distinct where the source facts are distinct. The fresh-forward external-result identity map points to the stable metadata binding; exact family identities retain their own receipt facts. Runtime does not inspect the migration identity map.

Removed `supersedes_provider_receipt_id`, its one-successor index and trigger, recursive canonical projection, API arguments, and stale contract/test wording. Kept immutable provider-body protections and exact foreign-key references. Only the specifically replaced, now-unreferenced verification wrapper is eligible for retirement. Distinct provider bodies and all exact observation/Sent/prepared references remain. No new business fields, result JSON keys, origin flags, audit/retry policy, or old-history slots were introduced.

## Evidence

A genuine prior-mismatch regression was added first and passed in the focused five-node RED/GREEN set. The broader final focused set initially exposed a real stale-write regression: 39 passed and `tests/test_dingtalk_delivery_repair.py::test_executor_completes_corrected_uncertain_receipt_from_readback` failed because the canonical read returned the old envelope without `verification_repair`. Receipt tracing showed an ordinary later Sent/observation writer moving the binding from the corrected body back to the stale body. Binding creation is now non-advancing once a binding exists; explicit correction/readback callers perform the CAS advance. The exact regression then passed.

Final focused correction/storage/contract/transfer/caller command:

```text
pytest -q tests/test_dingtalk_delivery_repair.py::test_executor_completes_corrected_uncertain_receipt_from_readback tests/test_model_result_references.py::test_prior_reconciliation_keeps_different_existing_canonical_local_to_real_attempt tests/test_model_result_references.py::test_prior_reconciliation_writes_one_owner_in_same_transaction tests/test_model_result_references.py::test_writer_stores_reference_but_public_reader_preserves_body tests/test_model_result_references.py::test_receipt_correction_preserves_other_attempts_original_meaning tests/test_model_result_references.py::test_unreferenced_correction_wrapper_is_not_retained_as_history tests/test_action_entity_storage.py tests/test_action_entity_contract.py tests/test_action_entity_schema_release.py::test_current_release_ddl_matches_target_manifest tests/test_action_entity_schema_release.py::test_populated_external_result_is_transferred_to_common_action tests/test_action_entity_schema_release.py::test_populated_prepared_sent_and_observer_facts_keep_distinct_receipts tests/test_action_entity_schema_release.py::test_direct_retry_parent_retargets_only_the_covered_action_reference tests/test_shared_action_lifecycle.py::test_prepared_receipt_is_pinned_separately_from_sent_and_canonical_bodies tests/test_dingtalk_delivery_repair.py::test_reverification_keeps_original_provider_and_previous_verifier tests/test_dingtalk_delivery_repair.py::test_reverification_rejects_other_task_without_mutation tests/test_dingtalk_delivery_repair.py::test_executor_reconciles_uncertain_historical_receipt_without_dispatch tests/test_dingtalk_delivery_repair.py::test_exact_complete_native_chat_readback_closes_without_resending
41 passed
```

Additional source-owner coverage for failed-Audit verified-Sent recovery, Task5A prepared/Sent/canonical facts, direct retry, corrected executor readback, and frozen target DDL:

```text
pytest -q tests/test_store.py::test_reconcile_failed_agent_message_requires_send_receipt_and_readback tests/test_action_entity_schema_release.py::test_populated_prepared_sent_and_observer_facts_keep_distinct_receipts tests/test_action_entity_schema_release.py::test_populated_external_result_is_transferred_to_common_action tests/test_action_entity_schema_release.py::test_direct_retry_parent_retargets_only_the_covered_action_reference tests/test_dingtalk_delivery_repair.py::test_executor_completes_corrected_uncertain_receipt_from_readback tests/test_shared_action_lifecycle.py::test_prepared_receipt_is_pinned_separately_from_sent_and_canonical_bodies tests/test_action_entity_schema_release.py::test_current_release_ddl_matches_target_manifest
12 passed
```

`ruff check` across all touched Python files, `python -m compileall` on changed modules, and `git diff --check` passed. No whole-suite run was performed. Task5C2 remains responsible for actual populated inverse, cold-old write, and fresh-forward proof.

## Files

Runtime/storage: `app/action_entity_definitions.py`, `app/action_entity_transfer.py`, `app/action_storage.py`, `app/dingtalk_delivery_repair.py`, `app/reviewed_candidate_store.py`, `app/shared_action_lifecycle.py`, and `tools/action_entity_contract.py`.

Frozen/current contract and behavior docs: `docs/action-entity-target.sql`, `docs/action-entity-current-target.sql`, `docs/action-entity-field-map.json`, `docs/architecture.md`, and `docs/runtime-mechanism.md`.

Focused regressions: `tests/test_action_entity_contract.py`, `tests/test_action_entity_schema_release.py`, `tests/test_action_entity_storage.py`, `tests/test_model_result_references.py`, and `tests/test_shared_action_lifecycle.py`.
