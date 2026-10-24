# Task 2D10B Reply compaction report

Date: 2026-10-23
Base: `4c9ab92351cf471b5fc2a3ebf43cbd883438ce53`
Branch: `codex/task-run-entity-phase2-speed-20261023`

## Scope delivered

Completed the recovered implementation of `AutoReplyStore._compact_settled_reply_task_input` in `app/store.py` using the assembled native Task, TaskInput, Run, RuntimeAttempt and Action owners. Eligibility remains limited to `done` and `skipped` tasks, preserves the newer-unclaimed-input check, active Consumer/Audit Run and RuntimeAttempt checks, current-generation WeChat delivery and Task lease checks, failed-current-Run settlement evidence, and unresolved latest ReplyAttempt checks. The failed-Run receipt lookup uses the actual native `execution_run_id` result SQL and matches the current TaskInput business key. Exact resolved-error evidence remains accepted. Email unsubscribe receipts remain eligible only for email Tasks.

The method updates TaskInputs and ReplyAttempt bodies on the caller's SQLite connection, preserving the caller transaction and rolling back naturally on an SQLite error. It retains every TaskInput identity, current/claimed ID and version. Input provenance continues to hold the original body hash and byte count; each Attempt body is matched to the correct input hash, with `0` retained when no input matches. An Attempt is retired only when no other active Reply Task shares any retained input identity.

Forward and inverse Action adoption now translate only integer `reply_task_id` and `matching_input_id` leaves inside the generated ReplyAttempt trigger-text provenance. Both directions use typed Task/Input identity maps. Zero sentinels and unresolved positive references are preserved; unresolved references carry only Action ID, field, type and value in portable metadata, avoiding numeric collision rebinding. Unknown JSON members, unrelated integer leaves, malformed inner provenance and typed BLOB provenance remain unchanged. Source factories and their DDL were not changed.

The composed migration regression starts from the real source fixture, remaps source Reply Task 201 to native Task 1201 and source Input public ID 5 to native Input 205, exercises collision values 1201/205 and a BLOB value, compacts a genuinely new native Task/Input/Attempt created after forward conversion, then performs inverse and a second portable forward cycle. It checks that source-public owner references return to the original values and the new native owner references follow their actual source owner. This is focused migration-helper evidence; it does not claim a cold full-service constructor or runtime activation.

Updated `docs/architecture.md` and `docs/runtime-mechanism.md` in the same change. Constructor activation, providers, production, main, push and deploy were outside the brief and were not performed.

## BASE evidence boundary

At BASE `4c9ab92351cf471b5fc2a3ebf43cbd883438ce53`, the method still read the source SQL projections (`REPLY_TASK_CURRENT_SQL`, `AGENT_RUN_SQL`, `RUNTIME_ATTEMPT_SQL`, `WECHAT_DELIVERY_SQL`, `REPLY_TASK_INPUT_SQL`, and `REPLY_ATTEMPT_COMMON_SQL`) and updated source Task payloads through `task8_set_parts` / `update_action`. The current implementation instead reads the assembled native tables and typed Action payloads. This is code-level BASE comparison only; no isolated BASE method execution was run, and this report does not claim test-first chronology or a full BASE checkout test.

## Verification

Focused assembled-native tests, run after the final test edit:

```text
pytest -q \
  tests/test_store_task_entity_creator.py::test_native_settled_reply_compaction_retires_task_input_bodies \
  tests/test_store_task_entity_creator.py::test_native_reply_compaction_keeps_body_when_current_input_is_unavailable \
  tests/test_store_task_entity_creator.py::test_native_reply_compaction_matches_each_input_hash_and_keeps_zero_sentinel \
  tests/test_store_task_entity_creator.py::test_native_reply_compaction_database_failure_rolls_back_in_callers_transaction \
  tests/test_store_task_entity_creator.py::test_native_failed_run_settlement_uses_current_input_business_key \
  tests/test_store_task_entity_creator.py::test_native_failed_run_does_not_accept_another_task_business_key \
  tests/test_store_task_entity_creator.py::test_native_reply_compaction_preserves_each_source_settlement_blocker \
  tests/test_run_adoption_schema.py::test_forward_translates_only_generated_reply_task_and_input_provenance_references \
  tests/test_run_adoption_inverse.py::test_reply_provenance_task_and_input_references_survive_two_portable_cycles
```

Result: **16 passed in 21.11s**. This includes done/skipped compaction and repeat idempotence; unavailable current input; multiple input hashes and zero sentinel; caller-transaction rollback after a database failure; current versus noncurrent business-key settlement; Consumer and Audit Run blockers, RuntimeAttempt, newer unclaimed input, WeChat, lease, unresolved Attempt and shared identity with another active Task; forward typed mapping; and composed two-cycle inverse/forward provenance.

Additional checks:

```text
ruff check app/store.py app/run_adoption_schema.py app/run_adoption_inverse.py tests/test_store_task_entity_creator.py tests/test_run_adoption_schema.py tests/test_run_adoption_inverse.py
```

Result: **All checks passed**.

```text
python -m py_compile app/store.py app/run_adoption_schema.py app/run_adoption_inverse.py tests/test_store_task_entity_creator.py tests/test_run_adoption_schema.py tests/test_run_adoption_inverse.py
git diff --check
```

Both passed.

## Recovery and self-review

The nine-file implementation/test/doc patch was recovered exactly from Git startup-cleanup snapshot `51faf4845` onto BASE `4c9ab92351cf471b5fc2a3ebf43cbd883438ce53`; it was reviewed in place and completed rather than rewritten. The only post-recovery code/test adjustment adds explicit coverage for an unavailable current input and an active Audit Run, both existing source blockers. Self-review confirmed the patch is confined to the stated compactor and two generated Action provenance leaves, uses the current TaskInput business key for receipt settlement, preserves the source channel guard on email settlement, and leaves transaction ownership with the caller. No unrelated claims or concurrent work are included in the changeset.

## Changeset

Implementation, focused tests, behavior documentation and this report are committed together. The Task2D10B worktree claim is released with the commit. Parent integration, independent SPEC/QUALITY review, constructor activation and any later release are separate gates.
