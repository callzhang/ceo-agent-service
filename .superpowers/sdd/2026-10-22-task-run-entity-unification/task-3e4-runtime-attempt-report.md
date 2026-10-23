# Task3E4 — RuntimeAttempt Store/router cutover report

## Result

Implemented the bounded native RuntimeAttempt Store and typed-caller cutover in this unactivated candidate. The DTO now exposes native `parent_id`; the persisted-empty `result_envelope_json` member is retired. `complete_agent_runtime_attempt(..., result_envelope_json=...)` still validates JSON, schema identity, and the existing size limit transiently, while the completed Consumer result is adopted through the exact `result_ref` protocol in the existing transaction.

The assembled Store reads and writes `runtime_attempts` directly for claims/ordinals, transitions, operation lease ownership, completion/adoption, failure, superseding, recovery, and feeds. Agent history is scoped by `workload_kind='agent_run'` plus native `parent_id`. Router failed-attempt comparisons, operation-vs-Agent history selection, and operation lease renewal now use the native DTO and workload discriminator. `agent_turn_runner` compares retry ownership through `parent_id`. `latest_runtime_route_for_agent_run` and Meeting Memory health also read the native table. The unrelated `ReplyAttempt.agent_run_id` model/callers are unchanged.

Task `work_summary` (plain and `decision_repair.N`) and Meeting operation identities resolve their current native professional Run and attach it as `parent_id`. These operation attempts still use their own lease/ordinal/session/failure semantics; parent presence does not select Agent Run terminalization or adoption. An actual `RoutedCodexExecution` invocation using the assembled Store and substitute executor proves the native path. Source-shaped Email, Workbench, and WeChat producers/readers remain pending dedicated follow-on Task units; this report does not claim their acceptance from Task fixtures.

## Remaining caller and release dependencies

- Email classification producers still create the source-shaped task owner and need an atomic native Task/DTO/workload-reader conversion.
- Workbench producers still use the source-shaped Turn UUID owner and need an atomic native Task/DTO/workload-reader conversion.
- WeChat import producers still use the source job public ID owner and need an atomic native Task/DTO/workload-reader conversion.
- The unrelated Reply task terminal/recovery methods and cross-family dashboard/History remain on source-shaped RuntimeAttempt/Run SQL and need their own coordinated migration. Remaining references include reply-input compaction and stale-processing/requeue/reset paths; they were not rewritten here.
- Structured OKR producer must bind the exact attempt ID through its validated CLI-result-to-Run creation order in its dedicated follow-on unit.
- Constructor/schema activation, complete source-family conversion, whole-database copy/inverse, full current-facts suite, and release validation remain pending. No constructor activation, provider/model CLI, production, main, push, or deploy occurred in this unit.

## Verification

- `pytest -q --tb=short tests/test_native_reviewed_candidate_store.py` — **15 passed**. Includes native assembled Store claim/history, professional operation parenting and parent immutability, independent keys/ordinals, lease expiry/reclaim, repeated terminal calls, wrong-owner start refusal, Meeting parent behavior, and substitute-executor routed completion.
- `pytest -q --tb=short tests/test_native_reviewed_candidate_store.py::test_runtime_attempt_claim_and_agent_history_use_native_parent_owner tests/test_native_reviewed_candidate_store.py::test_professional_parent_does_not_change_operation_lease_or_finish_parent tests/test_native_reviewed_candidate_store.py::test_routed_execution_uses_native_store_for_professional_operation` — **3 passed**.
- `python -m py_compile app/store.py app/agent_runtime_router.py app/agent_turn_runner.py` — passed.
- `git diff --check` — passed.
- Broad legacy test-harness probes were run and did not serve as valid acceptance evidence: `tests/test_routed_codex_execution.py` produced 17 passed / 62 failed, and `tests/test_agent_runtime_router.py` produced 5 passed / 6 failed / 51 setup errors. Failures occur before exercising these changed callers because those fixtures initialize the old Reply/Run schema (examples: missing `execution_generation`, missing `runtime_attempts`, and missing `task_inputs`). No compatibility SQL was added to make those old fixtures pass. A pinned-BASE RED/GREEN comparison was not established, so none is claimed.
