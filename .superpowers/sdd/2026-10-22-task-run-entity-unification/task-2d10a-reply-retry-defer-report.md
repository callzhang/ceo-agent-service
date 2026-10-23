# Task 2D10A Reply retry, auth recovery, and defer report

Date: 2026-10-23
Base: `596ff1b69b2d9ddd22452fc84e3476603af96945`
Branch: `codex/task-run-entity-phase2-20261022`
Implementation commit: `ae0d7edf3c36ca76ebf348d2f112fac3d1b9dac9`.

## Scope delivered

Converted the six bounded `AutoReplyStore` operations in `app/store.py` to native Task and effect owners: `retry_failed_reply_task`, `retry_failed_pre_agent_reply_task`, `has_failed_native_codex_auth_tasks`, `recover_failed_native_codex_auth_tasks`, `defer_reply_task`, and `defer_reply_task_for_authorization`. `_is_native_codex_auth_failure_row` and `_sessionless_replay_result_json` policy remain unchanged. Public DTO construction reads the actual bound current TaskInput through `_reply_task_from_native`; retry output retains the separate claimed pointer.

The two retry paths now use the native Task CAS. Turn retry requires the supplied ID to be the latest current-generation Consumer/Audit Run and failed Task, rotates generation, clears attempts/lock/availability, trims the reason and recovery code, and changes only the existing recovery parameters. It leaves Run history, session, TaskInputs, current/claimed pointers, and business-key associations intact. Pre-Agent retry checks that the current generation has no Consumer/Audit Run and that the current-input conversation/trigger has no Sent receipt; it clears the same source lifecycle fields but preserves generation.

Auth reads join the Task's exact current TaskInput, select the latest source-ordered Reply Attempt for that input, and preserve the existing auth classifier. Recovery keeps the source failed or stale-`codex_auth_recovered` processing predicate and 90-second clock comparison, the Sent-receipt exclusion, the running current-generation Consumer/Audit Run exclusion, and the WeChat delivery exclusion except failed pre-action failures. Eligible rows use a native generation/status CAS, clear only recovery code, and retain bulk recovery behavior. Regular deferral refunds at most one attempt with a zero floor; authorization deferral never refunds. Both update only the source lifecycle fields and retain the original stale-generation exception.

The old `AGENT_RUN_SQL` included only Consumer/Audit (`source_type IS NULL`). Native `runs` also owns professional Run rows, so latest-run, no-current-generation-run, and running-run predicates explicitly exclude rows represented in `run_provenance`. The native schema stores professional provenance there and its provenance triggers restrict those rows to WorkSummary, Meeting, or OKR Task kinds. This preserves the old Run family selection without consulting a legacy source alias or runtime identity map.

Updated `docs/architecture.md` and `docs/runtime-mechanism.md` in the same change. Constructor activation, global reads, providers, deployment, and all other Reply lifecycle units remain outside this scope.

## Source-to-native inverse proof

`test_reply_retry_auth_and_defer_facts_survive_native_inverse_reforward` uses the existing source `_source()` Task fixture (Reply Task 201, current public input 5/native 205, claimed public input 6/native 206). It adds a second source business-key row before forward conversion, commits the source, backs up the file, and uses `AutoReplyStore.__new__` with the real API methods. It performs a failed-turn retry, pre-Agent retry, regular deferral, and auth recovery. It then invokes `capture_task_entities` / `migrate_task_entities`, commits, backs up, and tests the native APIs.

The test inverses with `restore_task_entities`, reads source Reply and TaskInput facts through `task20_projection`, JSON-round-trips both `source_context` and `inverse_identity`, and re-forwards with that portable handoff. It asserts the current/claimed public pointers remain 5/6, both full TaskInput identity rows and both business keys survive, the final Task parameters/lifecycle are preserved, and native Run history is unchanged. This is a Task inverse; Run and Action owners remain native and are compared directly. The full composed Task+Run inverse remains a later phase requirement.

## Verification

Final focused RED/GREEN command and result:

- `pytest -q tests/test_store_task_entity_creator.py -k 'failed_reply_run_retry or pre_agent_retry_refuses or reply_deferrals_update_native or native_codex_auth_recovery or native_auth_recovery_preserves or native_pre_agent_retry'` — **7 passed, 43 deselected**. Cases cover failed Consumer/Audit latest and stale-generation IDs, repeat retry refusal, pre-Agent no-Run/Sent guards and CAS-loss atomicity, regular refund and authorization no-refund, latest auth Attempt, current input channel, 90-second stale versus fresh processing, Sent receipt, running Run, WeChat unknown versus failed pre-action, and multi-row eligibility.
- `pytest -q tests/test_task_entity_schema.py -k reply_retry_auth_and_defer_facts_survive_native_inverse_reforward` — **1 passed, 37 deselected**.
- `ruff check app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py` — **All checks passed**.
- `python -m compileall -q app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py` — passed.
- `git diff --check` — passed.

The inherited implementation checkpoint preceded the final parity tests, so a pre-implementation RED run was not observed for the whole change. During inverse-fixture refinement, the first test attempts failed on test setup (using the Task12 projection for a Task20 Reply kind, then placing the extra source business key in the neighboring test); the corrected Task20 source projection and correctly scoped two-key fixture now pass. No product-code defect was attributed to those fixture failures.

## Remaining dependencies / explicit non-coverage

This is not complete Reply runtime cutover. Remaining units include `requeue_reply_task`, Run closures, stale/orphan restart, generation rotation, scheduled skip, dispatcher claims, terminal settlement, trigger replacement, compaction, and product queries. Generic Task lifecycle claim/projection/acquire/recover APIs still use their existing source implementation and remain dependencies for later scheduled-skip/recovery seams. No full old cold-service Task/Run inverse proof is claimed here.

## Changeset

Implementation commit: `ae0d7edf3c36ca76ebf348d2f112fac3d1b9dac9`. Claim released in the following documentation commit.
