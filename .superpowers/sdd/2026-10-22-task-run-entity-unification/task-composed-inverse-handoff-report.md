# Composed Task/Run inverse handoff repair

## Result

Run inverse now accepts the current portable Task inverse identity envelope by calling Task's `_validate_inverse_identity` and using its validated typed owner map. Run still checks the exact Task inverse metadata envelope and source-context shape, verifies the digest over the complete identity object, reconstructs public identities from the restored source rows, and preserves its existing wrong-kind, unresolved-owner, collision, and pre-mutation refusal checks. No compatibility branch or source-schema fallback was added.

## RED and root cause

At base `d2160ba204bb11f903cd3f89e5fc49a74e989697`, the exact pre-fix command was:

```text
/Users/derek/miniforge3/bin/python -m pytest -q tests/test_run_entity_schema.py
```

Output: `7 failed, 62 passed in 41.00s`. The seven failures were:

- `test_new_structured_okr_run_and_attempt_parent_edges_allocate_before_inverse`
- `test_new_structured_parent_wrong_request_task_refuses_without_mutation`
- `test_new_native_task_run_attempt_gets_source_backed_owner_on_reforward`
- `test_closed_offline_email_task_attempt_survives_current_task_run_inverse`
- `test_task_first_run_inverse_emits_json_handoff_from_restored_task_source`
- `test_task_first_new_run_facts_survive_old_write_and_two_json_cycles`
- `test_inverse_retires_deleted_mapped_occurrence_and_cache_without_resurrection`

Each failed at `app/run_entity_schema.py::_inverse_task_ids` before Run inverse work. Run hard-coded the stale identity keys `{version, owners, meeting_business_keys}`. Current Task inverse output adds Workbench parent and attachment identities, file facts, and attachment allocation highwater; it binds all of them in `source_context.handoff_sha256`. The current Task validator already enforces the complete accepted key set, field shapes, duplicate/collision constraints, UUID/path/parent identity relationships, and highwater validity. A second manually maintained Run key allowlist rejected valid converter output.

## Change and regression coverage

`_inverse_task_ids` now checks the exact outer Task inverse metadata and context envelope, calls `_validate_inverse_identity` on the complete identity payload, verifies the Task digest, and consumes the validator's `owners` mapping. All Task source projections and existing Run inverse validation remain in place. The regression `test_run_inverse_rejects_unknown_current_task_identity_metadata_before_mutation` adds an unknown field to genuine Task converter output, recomputes its digest, and proves the shared validator rejects it without changing native rows or schema.

The existing `test_task_first_new_run_facts_survive_old_write_and_two_json_cycles` exercises actual Task and Run converters, current new Task/Run/Attempt facts, a Workbench Task/Turn, portable JSON handoffs across two cycles, a predecessor logical Run write, identity/highwater preservation, and source-row fingerprints. It has no attachment bytes; the separate Workbench file coordinator is not exercised here. Existing `test_inverse_retires_deleted_mapped_occurrence_and_cache_without_resurrection` preserves its captured historical FK anomaly `('runs', 1400, 'tasks', 1)` and does not fabricate a Task repair. The native Meeting `:audit` parent cycle remains independently covered.

## Verification

Pre-fix:

```text
/Users/derek/miniforge3/bin/python -m pytest -q tests/test_run_entity_schema.py
7 failed, 62 passed in 41.00s
```

Post-fix focused composition and rejection selection:

```text
/Users/derek/miniforge3/bin/python -m pytest -q \
  tests/test_run_entity_schema.py::test_task_first_new_run_facts_survive_old_write_and_two_json_cycles \
  tests/test_run_entity_schema.py::test_run_inverse_rejects_unknown_current_task_identity_metadata_before_mutation \
  tests/test_run_entity_schema.py::test_new_structured_okr_run_and_attempt_parent_edges_allocate_before_inverse \
  tests/test_run_entity_schema.py::test_new_structured_parent_wrong_request_task_refuses_without_mutation \
  tests/test_run_entity_schema.py::test_closed_offline_email_task_attempt_survives_current_task_run_inverse
5 passed in 5.00s
```

Complete focused Run schema file:

```text
/Users/derek/miniforge3/bin/python -m pytest -q tests/test_run_entity_schema.py
70 passed in 45.78s
```

Meeting converter and native `:audit` parent checks:

```text
/Users/derek/miniforge3/bin/python -m pytest -q \
  tests/test_run_entity_schema.py::test_meeting_audit_runtime_key_and_parent_survive_two_inverse_reforward_cycles \
  tests/test_run_entity_schema.py::test_inverse_meeting_runtime_key_rejects_unsupported_suffix \
  tests/test_meeting_task_runtime_native.py::test_meeting_audit_runner_claims_native_attempt_with_exact_run_parent
3 passed in 1.71s
```

`/Users/derek/miniforge3/bin/python -m ruff check app/run_entity_schema.py tests/test_run_entity_schema.py` passed. `git diff --check` passed.

## Boundaries

The implementation remains an offline converter repair. No constructor activation, provider action, production database operation, CLI/UI acceptance, main push, or deploy was performed. The two pre-existing modified reports `task-2d0-reply-claim-input-report.md` and `task-3c2a-adoption-forward-report.md` were preserved and not included in the owned change. Parent review owns independent whole `BASE..HEAD` SPEC and QUALITY review, full-copy/native CLI/UI/packed acceptance, and any later publication decision.

Base: `d2160ba204bb11f903cd3f89e5fc49a74e989697`.
Head: `261ae9223` (this implementation commit).
