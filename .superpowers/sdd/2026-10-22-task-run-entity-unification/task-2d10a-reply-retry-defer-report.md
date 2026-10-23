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

`test_reply_retry_auth_and_defer_facts_survive_native_inverse_reforward` uses the existing source `_source()` Task fixture (Reply Task 201, current public input 5/native 205, claimed public input 6/native 206). The test adds a second source business-key row, begins a transaction, captures Task metadata, forwards the Tasks with `capture_task_entities` / `migrate_task_entities`, and assembles the required Task/Run owners and indexes. It commits that converted source and backs it up to a temporary file before exercising APIs through `AutoReplyStore.__new__`. It performs a failed-turn retry, pre-Agent retry, regular deferral, and auth recovery.

After the API writes, the test begins the inverse transaction, invokes `restore_task_entities`, reads source Reply and TaskInput facts through `task20_projection`, JSON-round-trips both `source_context` and `inverse_identity`, and re-forwards with that portable handoff. It asserts the current/claimed public pointers remain 5/6, both full TaskInput identity rows and both business keys survive, the final Task parameters/lifecycle are preserved, and native Run history is unchanged. This is a Task inverse; Run and Action owners remain native and are compared directly. The full composed Task+Run inverse remains a later phase requirement.

## Verification

## Baseline RED and final GREEN evidence

An isolated AST oracle executed the exact six method definitions from `app/store.py` at BASE `596ff1b69b2d9ddd22452fc84e3476603af96945` against one fresh, actual assembled-native `_target_store` fixture per method. The harness parsed `git show 596ff1b69b2d9ddd22452fc84e3476603af96945:app/store.py`, extracted those `AutoReplyStore` method nodes, compiled/executed them in the imported `app.store` globals, and temporarily bound them onto `AutoReplyStore` in a throwaway Python process. Each fixture was made with the existing native schema assembly and real task creator/storage setup. No checkout, source factory, product file, or persistent database was changed; the temporary database files were removed after each call.

The exact baseline oracle result was **6 failures**, each caused by an old source-column assumption on the target native Task fixture:

```text
retry_failed_reply_task => OperationalError: no such column: legacy_key
retry_failed_pre_agent_reply_task => OperationalError: no such column: legacy_key
has_failed_native_codex_auth_tasks => OperationalError: no such column: legacy_key
recover_failed_native_codex_auth_tasks => OperationalError: no such column: legacy_key
defer_reply_task => OperationalError: no such column: reply_tasks.payload_json
defer_reply_task_for_authorization => OperationalError: no such column: reply_tasks.payload_json
```

The baseline invocation was `python - <<'PY'` with the AST extraction/setup described above. This is method-level regression evidence only: it does not claim a complete checkout at BASE, a full BASE test suite, or source behavior beyond these six original functions.

Final GREEN commands and results:

- `pytest -q tests/test_store_task_entity_creator.py -k 'failed_reply_run_retry or pre_agent_retry_refuses or reply_deferrals_update_native or native_codex_auth_recovery or native_auth_recovery_preserves or native_pre_agent_retry'` — **7 passed, 43 deselected**. Cases cover failed Consumer/Audit latest and stale-generation IDs, repeat retry refusal, pre-Agent no-Run/Sent guards and CAS-loss atomicity, regular refund and authorization no-refund, latest auth Attempt, current input channel, 90-second stale versus fresh processing, Sent receipt, running Run, WeChat unknown versus failed pre-action, and multi-row eligibility.
- `pytest -q tests/test_task_entity_schema.py -k reply_retry_auth_and_defer_facts_survive_native_inverse_reforward` — **1 passed, 37 deselected**.
- `ruff check app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py` — **All checks passed**.
- `python -m compileall -q app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py` — passed.
- `git diff --check` — passed.

During inverse-fixture refinement, initial attempts failed on test setup (using the Task12 projection for a Task20 Reply kind, then placing the extra source business key in the neighboring test). The corrected Task20 source projection and correctly scoped two-key fixture pass; those setup failures were not product-code failures.

## Remaining dependencies / explicit non-coverage

This is not complete Reply runtime cutover. Remaining units include `requeue_reply_task`, Run closures, stale/orphan restart, generation rotation, scheduled skip, dispatcher claims, terminal settlement, trigger replacement, compaction, and product queries. Generic Task lifecycle claim/projection/acquire/recover APIs still use their existing source implementation and remain dependencies for later scheduled-skip/recovery seams. No full old cold-service Task/Run inverse proof is claimed here.

## Changeset

Implementation commit: `ae0d7edf3c36ca76ebf348d2f112fac3d1b9dac9`. Claim released in the following documentation commit.

## Independent review Fix 1 (base `67bdb8f6a5f28d44e624d2f63a8b059f43f67e37`)

Addressed the review finding by extending the actual assembled-Store deferral test only. A normal deferral with `refund_attempt=True` and `attempts=0` persists `attempts=0`. Both regular and authorization deferral reject a failed (terminal) Task with the exact `AgentRunLeaseLostError("reply task superseded: <id>")`; the test reads back status, attempts, lock, availability, error, `updated_at`, and generation and proves no field changed. No product code or deferral policy changed.

Fix 1 verification:

- `pytest -q tests/test_store_task_entity_creator.py -k reply_deferrals_update_native_task_without_touching_input_owners` — **1 passed, 49 deselected**.
- `ruff check tests/test_store_task_entity_creator.py` — **All checks passed**.
- `python -m compileall -q tests/test_store_task_entity_creator.py` — passed.
- `git diff --check` — passed.

Review Fix 1 commit: recorded in the follow-up handoff after commit.
