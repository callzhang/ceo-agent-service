# Task 2 report: single Action owner

Status: candidate implementation complete; not deployed and not ready for runtime cutover until the paired migration and caller conversion land. Parent transaction ownership is unchanged. The four APIs expose only the frozen Action fieldset plus physical `id`; the approved normalized child helpers are documented below.

## Owned changes

- Added `app/action_entity_definitions.py` as the pure shared definition of the fixed Action fields, generated columns/indexes, and receipt/observer DDL. The tool contract builder uses the separate source schema factory, so target inspection cannot recurse through the new target definition.
- Added `app/action_entity_storage.py` with the four required APIs: `create_action`, `get_action`, `claim_action`, and `record_action_result`. CAS predicates preserve nullable comparisons and use only caller-owned transactions. Added the controller-approved `add_action_observer` and `append_action_receipt` helpers; both take explicit Action identity, the receipt helper accepts only its fixed allowlist, and neither carries a source/origin field. Receipt/observer facts are stored in the normalized Action-owned child relations from the frozen DDL.
- Integrated discriminator 70 for new Action records without changing the frozen source type factory or existing discriminator values. Added the candidate schema option and exact parent/child schema validation/install behavior. A partial existing Action declaration is rejected without repairs or writes.
- Added focused storage/schema tests and candidate-only language in architecture/runtime docs. No Task 3 callers, production data, provider paths, deployment, or audit/retry policy were changed.

## TDD and validation

Initial red check after writing the tests:

```text
python -m pytest -q tests/test_action_entity_storage.py
ModuleNotFoundError: No module named 'app.action_entity_storage'
```

Final focused commands and output:

```text
$ python -m pytest -q tests/test_action_entity_storage.py
..........                                                               [100%]
10 passed in 3.33s

$ python -m pytest -q tests/test_current_action_schema.py tests/test_action_entity_contract.py
.................                                                        [100%]
17 passed in 7.01s

$ ruff check app/action_entity_definitions.py app/action_entity_storage.py app/action_storage.py app/current_action_schema.py app/current_database_schema.py app/storage_payload.py tools/action_entity_contract.py tests/test_action_entity_storage.py tests/test_current_action_schema.py
All checks passed!

$ python -m compileall -q app/action_entity_definitions.py app/action_entity_storage.py app/action_storage.py app/current_action_schema.py app/current_database_schema.py app/storage_payload.py tools/action_entity_contract.py
# exit 0, no output

$ git diff --check
# exit 0, no output
```

The 10 storage tests include all six required scenarios (single physical Action across create/claim/result; stale CAS; one effect with multiple observers; same body with distinct effects; distinct historical receipts; NULL text/NULL JSON/empty JSON distinction), frozen-target DDL equality, and three no-autorepair partial-schema cases. Test connections enable foreign keys for Action API writes. `tests/test_current_action_schema.py` and the 13 contract tests cover exact current schema creation, rejection of partial declarations without mutation, and target inspector equality.

Source inspection confirmed `tools/action_entity_contract.py` imports the explicit source declaration factory rather than runtime docs or the recursive target builder. The Action parent declaration was compared to the frozen `actions` target DDL, and the complete Action/receipt/observer object set matched the frozen target (253 inspected objects overall).

## Prescribed legacy test files

The required bounded legacy run was:

```text
$ python -m pytest -q --tb=no tests/test_prepared_action_owner.py tests/test_execution_owner.py tests/test_sent_result_references.py
43 failed, 12 passed, 1 skipped in 48.04s
```

The pre-change handoff records the same aggregate baseline (43/12/1), so this run did not increase the aggregate failure count. This alone does not establish node-by-node equivalence. Current failing node IDs:

```text
tests/test_execution_owner.py::test_forward_inverse_preserves_all_source_facts_and_independent_ids[actual_no_memory_fk]
tests/test_execution_owner.py::test_forward_inverse_preserves_all_source_facts_and_independent_ids[fresh_memory_fk]
tests/test_execution_owner.py::test_new_execution_and_attempt_inverse_uses_current_plan[actual_no_memory_fk]
tests/test_execution_owner.py::test_new_execution_and_attempt_inverse_uses_current_plan[fresh_memory_fk]
tests/test_execution_owner.py::test_deferred_binding_rejects_invalid_commit_and_keeps_transaction[actual_no_memory_fk]
tests/test_execution_owner.py::test_deferred_binding_rejects_invalid_commit_and_keeps_transaction[fresh_memory_fk]
tests/test_execution_owner.py::test_valid_atomic_pair_commits_with_foreign_keys_on[actual_no_memory_fk]
tests/test_execution_owner.py::test_valid_atomic_pair_commits_with_foreign_keys_on[fresh_memory_fk]
tests/test_execution_owner.py::test_bad_source_refuses_before_any_write[actual_no_memory_fk-wrong_review]
tests/test_execution_owner.py::test_bad_source_refuses_before_any_write[actual_no_memory_fk-changed_schema]
tests/test_execution_owner.py::test_bad_source_refuses_before_any_write[fresh_memory_fk-wrong_review]
tests/test_execution_owner.py::test_bad_source_refuses_before_any_write[fresh_memory_fk-changed_schema]
tests/test_execution_owner.py::test_alias_allocator_keeps_ordinary_rowid_namespace[actual_no_memory_fk-None]
tests/test_execution_owner.py::test_alias_allocator_keeps_ordinary_rowid_namespace[actual_no_memory_fk--2]
tests/test_execution_owner.py::test_alias_allocator_keeps_ordinary_rowid_namespace[actual_no_memory_fk-9223372036854775807]
tests/test_execution_owner.py::test_alias_allocator_keeps_ordinary_rowid_namespace[fresh_memory_fk-None]
tests/test_execution_owner.py::test_alias_allocator_keeps_ordinary_rowid_namespace[fresh_memory_fk--2]
tests/test_execution_owner.py::test_alias_allocator_keeps_ordinary_rowid_namespace[fresh_memory_fk-9223372036854775807]
tests/test_execution_owner.py::test_multiple_reviews_remain_independent_and_source_type_codes_are_fixed[actual_no_memory_fk]
tests/test_execution_owner.py::test_multiple_reviews_remain_independent_and_source_type_codes_are_fixed[fresh_memory_fk]
tests/test_execution_owner.py::test_fresh_alias_rejects_unrelated_physical_memory_binding[fresh_memory_fk]
tests/test_sent_result_references.py::test_forward_inverse_exact_raw_facts_observers_and_waters[actual_no_memory_fk]
tests/test_sent_result_references.py::test_forward_inverse_exact_raw_facts_observers_and_waters[fresh_memory_fk]
tests/test_sent_result_references.py::test_owner_mutation_preserves_untouched_sent_and_explicit_correction[actual_no_memory_fk-body]
tests/test_sent_result_references.py::test_owner_mutation_preserves_untouched_sent_and_explicit_correction[actual_no_memory_fk-key]
tests/test_sent_result_references.py::test_owner_mutation_preserves_untouched_sent_and_explicit_correction[actual_no_memory_fk-delete]
tests/test_sent_result_references.py::test_owner_mutation_preserves_untouched_sent_and_explicit_correction[actual_no_memory_fk-no_match]
tests/test_sent_result_references.py::test_owner_mutation_preserves_untouched_sent_and_explicit_correction[fresh_memory_fk-body]
tests/test_sent_result_references.py::test_owner_mutation_preserves_untouched_sent_and_explicit_correction[fresh_memory_fk-key]
tests/test_sent_result_references.py::test_owner_mutation_preserves_untouched_sent_and_explicit_correction[fresh_memory_fk-delete]
tests/test_sent_result_references.py::test_owner_mutation_preserves_untouched_sent_and_explicit_correction[fresh_memory_fk-no_match]
tests/test_sent_result_references.py::test_new_fact_inverse_and_allocator_continuation[actual_no_memory_fk]
tests/test_sent_result_references.py::test_new_fact_inverse_and_allocator_continuation[fresh_memory_fk]
tests/test_sent_result_references.py::test_missing_null_owner_cannot_commit_but_atomic_pair_can[actual_no_memory_fk]
tests/test_sent_result_references.py::test_missing_null_owner_cannot_commit_but_atomic_pair_can[fresh_memory_fk]
tests/test_sent_result_references.py::test_unknown_source_refuses_before_writes[actual_no_memory_fk]
tests/test_sent_result_references.py::test_unknown_source_refuses_before_writes[fresh_memory_fk]
tests/test_sent_result_references.py::test_invalid_source_result_refuses_before_writes[actual_no_memory_fk-null]
tests/test_sent_result_references.py::test_invalid_source_result_refuses_before_writes[actual_no_memory_fk-17]
tests/test_sent_result_references.py::test_invalid_source_result_refuses_before_writes[fresh_memory_fk-null]
tests/test_sent_result_references.py::test_invalid_source_result_refuses_before_writes[fresh_memory_fk-17]
tests/test_sent_result_references.py::test_existing_native_readback_repairs_explicit_sent_result_without_resending[False]
tests/test_sent_result_references.py::test_existing_native_readback_repairs_explicit_sent_result_without_resending[True]
```

The independently reproduced first execution and first sent-reference failures stop before their assertions: `tests/test_action_vector_writes.py:seed_task` inserts a removed `tasks.status` column into the stale fixture built from `app.task20_definitions.TASK20_TABLE_DDL`. This is the specific fixture modernization issue documented in the handoff. The two native-readback nodes reach an assertion path and fail with `ValueError: reply task is no longer the same uncertain failure`; although the overall legacy baseline count is unchanged, individual baseline equivalence for these nodes is not established here. The failures were left intact; no broad fixture rewrite or assertion weakening was made.

## Self-review and remaining boundary

- Public APIs have the exact required argument structure and `db: sqlite3.Connection` annotation, return only the frozen Action fields plus physical ID, and do not accept source/origin or delegate to the old Action DAO.
- Parent CAS updates remain conditional and nullable-safe. Observer/receipt facts are normalized child rows. The append helper is a literal append operation; callers make parent status changes explicitly in the same caller-owned transaction when required.
- Blank schema creation installs the full parent and children; pre-existing incomplete target declarations are rejected rather than repaired.
- Task 3 caller conversion and the complete paired migration remain necessary before publishing this candidate. The prescribed old-owner tests retain their existing assertions and require targeted fixture/caller adaptation in their owning tasks.

Implementation commit SHA: `4f9a58288d9487fe8e1ecc8444346ca2c126fb41` (`refactor(actions): introduce single action entity owner`).
