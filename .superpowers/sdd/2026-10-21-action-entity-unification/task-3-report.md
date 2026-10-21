# Task 3: concrete execution and recovery callers

## Result

Reviewed, direct-email, prepared-message and Sent/recovery writers now persist through the common Action owner and owned receipts. Covered business eligibility, claim boundaries, provider verification, retry, accepted-but-unknown recovery, and failed-Audit reconciliation remain caller-owned and retain their existing predicates. The common owner no longer dispatches concrete effect writes by `prepared`, `reviewed`, or `email` origin. It retains the specialized `email_reply` and `email_unsubscribe` paths, which are outside this release's concrete Action scope.

Prepared-message prepare, claim and provider receipt writes now use Action and Action Receipt. The actual provider operation is stored only once known; prepare does not synthesize an operation. Accepted-but-unknown recovery continues to reconcile the existing receipt before any provider replay. Direct-email Actions use the physical Action id and the source-unique `(action_plan_id, operation)` identity; the deterministic old `action_id` function remains only as an inverse mapping, with no runtime callsites or duplicate external-key alias.

Reviewed execution observations keep one mutable current row for each `(execution_id, action_index)`. Parent Action status follows the latest explicit observation in `updated_at, execution_id, action_index, id` order. Per-execution clocks and outcome stay on the observation receipt; reviewed begin no longer creates a duplicate observer or copies candidate run, target, or observation clocks onto the Action. Provider bodies and their version lineage remain immutable. Canonical reads follow the explicit binding and supersedes chain, returning root identity/first-run/canonical creation time and tip body/digest. Existing explicit reverification can update the current observation or Sent reference by same-Action CAS; passive canonical correction leaves existing observations and Sent time unchanged.

Sent-only Actions retain missing source facts as NULL, including operation, canonical identity, prepared time, and status. A Sent fact receipt points to the exact provider body and preserves the source `sent_at` as its observation clock. Failed-Audit recovery keeps its former verified-Sent plus bound-plan eligibility and adopts into the same Action/body; it does not add a duplicate body or require a preexisting canonical result. Initially unknown canonical digest and root creation time bind once during adoption without replacing the original Sent clock. No new audit, authorization, retry, or replay policy was added.

The frozen declaration now preserves source cardinality for one candidate observation per execution/action slot with a unique partial index on `(execution_id, action_index)` where `execution_id IS NOT NULL`. Ordinary provider versions keep NULL execution identity and remain distinct. Direct-email uniqueness is represented by the source-backed partial index on `(action_plan_id, operation)`. Runtime definitions, generated target SQL, the field map and contract checks agree.

## Tests and checks

Prescribed caller run after the canonical-owner lookup fix but before restoring the original history/Attention assertions (all 38 tests were collected):

```text
python -m pytest -q tests/test_action_entity_callers.py tests/test_native_reply_reconciliation.py tests/test_shared_action_lifecycle.py tests/test_outbound_postfix.py --basetemp=/tmp/ceo-action-entity-d0ce4814/task3-final-5
38 passed in 42.93s
```

The original full-history/Attention/queue assertions have since been restored in the existing shared-lifecycle test, alongside the current-owner assertions. Per the Task 4 boundary, no full-history runtime reader was changed here. The exact restored node was run once:

```text
python -m pytest -q tests/test_shared_action_lifecycle.py::test_direct_email_owner_and_important_listing_read_current_attempt_fields --basetemp=/tmp/ceo-action-entity-d0ce4814/task3-readers-boundary-1
FAILED at list_operation_logs_with_count: expected total == 1, observed total == 0 (1 failed in 3.23s)
```

This is a concrete remaining Task 4 reader dependency; the later `_queue_attention_rows`, `_email_action_queue_snapshot`, and important-listing assertions remain present in the test but were not reached after the first failure. Because the prescribed caller file contains this restored test, the 38-pass result above is historical evidence before restoration and the current prescribed group must not be described as fully green.

Final owner/adoption/caller regression run:

```text
python -m pytest -q tests/test_action_entity_callers.py tests/test_action_entity_storage.py tests/test_action_receipt_callers.py::test_failed_audit_receipt_preserves_the_raw_recorded_provider_body tests/test_reviewed_candidate_store.py::test_old_revision_keeps_its_approved_binding tests/test_reviewed_candidate_store.py::test_unknown_dispatch_requires_reconciliation_after_restart tests/test_action_delivery_callers.py --basetemp=/tmp/ceo-action-entity-d0ce4814/task3-final-regression-1
29 passed in 33.61s
```

This run covers the old approved binding, provider acceptance followed by restart without resending, concurrent direct-email claim with one provider call, retry preserving the same physical Action/external identity, three concrete origins sharing the Action field set, current-observation slot cardinality, raw provider body preservation, Sent-only unknown canonical metadata adoption, and Sent receipt/reference behavior. Test providers are substitutes; no real provider send was performed.

Target contract and schema checks:

```text
python -m pytest -q tests/test_action_entity_contract.py tests/test_current_action_schema.py tests/test_action_entity_storage.py --basetemp=/tmp/ceo-action-entity-d0ce4814/task3-contract-final-3
29 passed in 13.10s
```

`python -m compileall -q app/action_entity_definitions.py app/action_entity_storage.py app/action_storage.py app/dingtalk_delivery_repair.py app/email_store.py app/reviewed_candidate_store.py app/service_message_sender.py app/shared_action_lifecycle.py app/store.py app/system_action_handlers.py tools/action_entity_contract.py` completed successfully. `git diff --check` completed with no output. The tests also assert blank-schema equality against the frozen target declarations, including the new unique source-cardinality indexes and trigger rules.

Historical direct-email external guard factories were checked without editing the source factory:

```text
python -m pytest -q tests/test_configs_source_contract.py::test_six_account_blockers_have_distinct_old_and_current_action_owners tests/test_email_action_validation.py::test_source_phase_external_installer_is_transactional_idempotent_and_nonrepairing --basetemp=/tmp/ceo-action-entity-d0ce4814/task3-guard-factory-1
3 passed in 0.97s
```

The assertions compare the retained historical guard definitions to the target-only Action guards and verify the historical external installer remains exact and idempotent. `app/action_email_storage.py` is unchanged.

An earlier prescribed run collected the same 38 tests and exposed three stale assertions in `tests/test_shared_action_lifecycle.py` that still selected direct-email rows by the removed legacy hash alias. Those assertions now use the current physical Action id and retain their claim, attempt, receipt and rollback checks. No tests were deleted or deselected. The restored reader assertion above remains a required Task 4 acceptance item.

## Caller and retirement inventory

- Covered `prepared`, `reviewed`, and direct-email effect writers no longer call `shared_action_lifecycle.transition` or write source-specific `outbound_postfixes`, `candidate_action_attempts`, `email_actions`, `email_action_attempts`, `sent_replies`, or `external_action_results` records. Common owner writes use actual Action ids and normalized receipts.
- `rg` found 25 remaining `shared_action_lifecycle.transition` callsites in `app/email_store.py`: 8 for `email_reply` and 17 for `email_unsubscribe`. These are the retained specialized workflows; unsubscribe behavior is unchanged and out of scope. The `persist_plan`/`update_plan` helpers still preserve reviewed/email plan records and are not effect dispatchers.
- `app/email_store.py:8839` still uses `EMAIL_ATTEMPT_SQL` inside `list_email_classification_observability`; this is a remaining history/observability reader for Task 4, not a write or runtime compatibility fallback. Full history/UI/API reader retirement is not claimed here.
- `_direct_action_id` remains as an inverse formula only; its only runtime-source occurrence is the definition, with no calls. `SENT_REPLY_SQL` and prepared-message projections read Action/receipt ownership. Old source factories used by explicit migration/inverse remain frozen.
- Retirement is partial. The shared Actions container, plan records, specialized unsubscribe records/writers, and Task 4 readers remain. No blanket type retirement is claimed.

## Self-review and boundaries

`service_message_sender.py` and `system_action_handlers.py` only pass the actual `send_message` or `send_reply_to_trigger` operation into the existing receipt/claim APIs. Provider calls, acceptance handling, verification, uncertain recovery and retry branches are unchanged. Reviewed provider reuse now queries `EXTERNAL_ACTION_RESULT_SQL`, which follows the explicit canonical binding/tip instead of selecting an arbitrary newest provider body. Failed-Audit adoption retains the prior eligibility check and source digest rule.

No full suite, migration, deployment, push, production database access, or Task 4 conversion was performed. A prior broad `tests/test_email_store.py` investigation had 105 passes and 57 failures across legacy direct-action fixtures/readers; those were not treated as acceptance evidence or widened into this caller task. The specific current-owner claim, retry, guard, and receipt behavior is covered by the focused runs above. The preexisting native readback baseline failures documented by Task 2 remain outside this change and were not used to change policy.

The task ledger does not retain an initial fail-first command/output for the new actual-caller tests. The first preserved run available in this worktree is the 38-test run above; I am not claiming an unrecorded TDD RED result.

## Commit

Pending final source review and commit by the Task 3 implementer.
