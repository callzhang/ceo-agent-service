# Task/Input merge and remaining runtime exits — Unit C

BASE: `479bfd68448284711002c75df0abe1dc70c3d958`
Implementation tree (code, tests, behavior docs, and report before self-identification): `6496c10161cc5f2d523f9636afa5406b04934908`

This is a local, unactivated Task/Run candidate. The assembled Store constructor, global manifest, History/Attention/session-search publication, production checkout, `main`, and provider/runtime activation were not changed.

## Native owner changes

- `AutoReplyStore.replace_pending_single_chat_reply_task_trigger`, reached by the DingTalk worker and WeChat producer, now selects and writes through `tasks`, `task_inputs`, `task_business_keys`, and native `runs`. It preserves the source newest-eligible pending/zero-attempt single-chat selection, exact unchanged test, generation/status/current-input CAS, supersession of running Runs for the selected generation, and the input-version count assignment after input movement and append. Existing Input IDs are moved intact. Business-key associations are retained; a collision already owned by the selected Task is coalesced, otherwise the existing key row is reassigned. The selected current key is retained (or derived as before), the incoming key is associated, and duplicate Tasks are retired in the same transaction. The selected Task's claimed Input and claimed version are not changed.
- New TaskBusinessKey associations now receive the same `current_timestamp` already used by the merge transaction for `created_at` and `updated_at`. The native columns have no SQL default, while the source `business_object_tasks` writer relied on `current_timestamp`; leaving them NULL made an actual merged current-fact inverse fail its source NOT NULL checks. This uses the existing helper's timestamp arguments and does not change its nullable Meeting association contract.
- `clear_agent_run_session` selects native Run owners joined to native Reply Tasks and clears only exact Task/generation/role/revision/attempt matches through Run CAS. Repository caller search found no production callers beyond the worker lifecycle path and this method's test; no source-shaped test-only API was retired.
- The WeChat review approval/recording methods now read delivery Actions by their physical `actions.id`, hydrate Reply facts from the current TaskInput while retaining canonical Task identity, and check the exact Audit Run and Consumer parent chain. Review recording updates the Action payload through the native subtype writer and status/evidence CAS. In this model `run_adopted_reviews.id` remains its protocol/business identity and `audit_run_id` is the native Audit Run owner; those meanings are not conflated.

## Focused evidence

- `pytest -q tests/test_store_task_entity_creator.py -k 'pending_single_chat_merge or clear_agent_run_session or native_wechat_' --tb=short` — **5 passed, 89 deselected**. The merge success case retains Input and key IDs, coalesces/keeps multiple key associations, moves the prior Input, preserves claimed ownership, points current at the incoming Input, rotates generation, and verifies the explicit resulting version count. The retained-Run FK case raises `sqlite3.IntegrityError` and compares Tasks, Inputs, BusinessKeys, and Runs before/after to prove transaction rollback. Session clearing leaves the stale-generation Run untouched. WeChat tests verify current-Input/Task ID separation and positive plus mismatched-parent negative approval checks.
- `pytest -q tests/test_run_adoption_inverse.py -k reply_provenance_task_and_input_references_survive_two_portable_cycles --tb=short` — **1 passed, 10 deselected**. The existing assembled current-fact fixture now runs the actual merge against a migrated Task, then merges two native pending Tasks with multiple BusinessKeys, a claimed Input, and a retained duplicate Input identity. It verifies duplicate Task retirement, Input/key reassignment, current and claimed pointers, and version count before continuing the actual Task→Run→adoption inverse/reforward twice. Existing passive dispatcher claim facts, provenance references, and allocator/current-fact assertions remain in the same test. The test uses source fixture trigger time `time` unchanged in its lexical comparison, matching its source selector behavior.
- `ruff check app/store.py tests/test_store_task_entity_creator.py tests/test_run_adoption_inverse.py` — passed.
- `python -m compileall -q app/store.py tests/test_store_task_entity_creator.py tests/test_run_adoption_inverse.py` — passed.
- `git diff --check` — passed.

The recording method's native implementation is covered by the approval/read path and its Action writer helper, but this focused batch does not exercise a complete `record_wechat_delivery_review` public call with an adopted Audit result. No WeChat sender/provider was invoked.

## Remaining boundaries

This change does not port broader WeChat delivery claim/status/list/detail readers, shared History/Attention/session search, global manifest/constructor selection, public Email receipt reconciliation, generic dispatcher/other-family publication, or cold-process/full-copy/browser acceptance. These remain the separate final publication unit or later runtime acceptance gates. The existing frozen source factories and explicit migration-only identity maps remain unchanged. No audit, authorization, retry, cancellation, or reconciliation policy changed.

## Review and commit

The parent will perform the independent full `BASE..HEAD` SPEC and QUALITY review. This report records only the bounded implementation and focused checks above; it is not constructor, production, push, deployment, or release evidence.
