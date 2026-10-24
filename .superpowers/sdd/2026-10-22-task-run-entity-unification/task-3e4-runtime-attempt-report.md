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

## Fix1/5 review correction

Independent SPEC/QUALITY at base `dd98e25a8fa81b63c036d5738a4a14103b99c3b6` found `_renew_attempt_parent_lease` selecting the Agent path by `workload_kind` but reading the retired `attempt.agent_run_id` field. That raised `AttributeError` before the parent lease could renew. The caller now reads `attempt.parent_id`.

The new `test_routed_execution_renews_native_agent_parent_lease` creates an Agent Run and RuntimeAttempt in the assembled native schema, invokes the Agent renewal branch through `RoutedCodexExecution`, and verifies the attempt retains the correct native owner while the exact native Run lease expiry advances. Fail-first command before the code fix:

- `pytest -q --tb=short tests/test_native_reviewed_candidate_store.py::test_routed_execution_renews_native_agent_parent_lease` — **1 failed**, with `AttributeError: 'AgentRuntimeAttempt' object has no attribute 'agent_run_id'` at `app/agent_runtime_router.py` Agent renewal.

After changing that access to `parent_id`:

- `pytest -q --tb=short tests/test_native_reviewed_candidate_store.py` — **16 passed**.
- `pytest -q --tb=short tests/test_run_entity_storage.py::test_runtime_attempt_native_parent_observation_lineage_and_cas` — **1 passed**. This existing native-storage test covers resumed correction attempts retaining `session_mode='resume'`, `source_session_id`, observed session/range, and immutable correction policy/schema lineage.
- `ruff check app/agent_runtime_router.py tests/test_native_reviewed_candidate_store.py` — passed.
- `python -m py_compile app/agent_runtime_router.py tests/test_native_reviewed_candidate_store.py` — passed.
- `git diff --check` — passed.

This fix does not add a separate resume/correction test; the exact existing native-storage coverage is identified above. End-to-end native CLI resume/correction acceptance remains a later runtime integration gate.

## Fix2/5 workload-parent identity correction

Native Task Agent operations use the exact `run_id` supplied by `app/task_agent.py`, including `run_id:decision_repair.N`. The prior helper treated that key as a Task ID, joined to Runs by Task, required a matching Task/Run generation and selected the latest running Run. The native helper now validates and stores only the exact Run ID when its `run_provenance.source_type='task_agent_runs'` and Run status is `running`; it does not synthesize role/generation values or use a latest-Run lookup. The regression uses a Task ID different from its Run ID, leaves native Run `role` and `execution_generation` NULL, and places a newer completed Run on the same Task. Plain and decision-repair keys point to the exact requested Run and retain independent attempt ordinals. The substitute-executor routed test now supplies the Run ID as its Task Agent workload key.

Structured OKR work now checks the native `okr_review` Task's existing `processing` status before CLI execution. It can claim and complete an actual routed attempt while no professional Run exists; its attempt parent remains NULL. A non-processing OKR Task remains refused. The later OKR producer must bind the exact returned RuntimeAttempt ID to the professional Run created after validated CLI output; this fix does not edit that producer. Unreachable source-shaped structured/Meeting checks and the source Task-Agent fallback were removed only from the native operation-parent helper; other workload source checks remain.

Fail-first evidence:

- With Task ID/Run ID deliberately distinct, `pytest -q --tb=short tests/test_native_reviewed_candidate_store.py::test_professional_parent_does_not_change_operation_lease_or_finish_parent tests/test_native_reviewed_candidate_store.py::test_routed_execution_uses_native_store_for_professional_operation` — **2 failed** at runtime-parent validation, reproducing the Task-ID/Run-ID assumption.
- Temporarily restoring the prior structured pre-CLI Run requirement, `pytest -q --tb=short tests/test_native_reviewed_candidate_store.py::test_routed_execution_uses_native_store_for_professional_operation` — **1 failed** before execution with `ValueError: runtime operation parent does not exist or is not running`, for the processing OKR Task with no Run.

After the native identity changes:

- `pytest -q --tb=short tests/test_native_reviewed_candidate_store.py` — **16 passed**.
- `pytest -q --tb=short tests/test_run_entity_storage.py::test_runtime_attempt_native_parent_observation_lineage_and_cas` — **1 passed**; this existing test covers resumed correction source-session retention and immutable correction lineage.
- `ruff check tests/test_native_reviewed_candidate_store.py app/agent_runtime_router.py` — passed.
- `ruff check --ignore F401 app/store.py` — passed. Full-file Ruff reports an unchanged `F401` import at `app/store.py:14711`, outside this fix's diff.
- `python -m py_compile app/store.py tests/test_native_reviewed_candidate_store.py` — passed.
- `git diff --check` — passed.

The native Task/Run tests remain local unactivated acceptance evidence; there was no provider/model CLI, constructor/bootstrap activation, production action, or release. End-to-end OKR post-validation exact-attempt binding remains with Task2D3; full native CLI resume/correction acceptance remains a later runtime integration gate.
