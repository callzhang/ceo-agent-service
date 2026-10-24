# Meeting upstream integration report

## Result

Integrated the pinned upstream Meeting independent Audit commit into the Phase 2 native Task/Run candidate without changing its separately released audit policy. The merge commit is `878c774620cf27cfc57293d383e1f4d7c625d0d4` (upstream `6005776a161fcdeddf605b35f7e23279ae9e18c0`); the bounded Meeting owner adaptation is the current working-tree change and is ready for its own code commit. Dispatch BASE was `e86544d5c998d2ec40851d69b138b36c3e8d1d11`.

## Contract preserved

- Planner attempts retain the plain Meeting Run key; the upstream Audit invocation uses `<run_id>:audit`. Native validation accepts only the positive Run ID with either no suffix or the exact `:audit` suffix. Both resolve to the exact running Run with `meeting_alignment_runs` provenance, and native `parent_id` is that Run ID.
- Forward conversion maps the source Run prefix to its native Run ID and preserves `:audit`. Inverse conversion verifies that the native parent matches the native prefix and Meeting provenance, maps the Run prefix back to the source identity, preserves the operation suffix, and restores source `parent_id=NULL` as required by the frozen source DDL. A paired test uses unequal source/native IDs through two JSON-portable inverse/forward cycles and checks the source NULL-parent contract.
- Failed-delivery requeue keeps the upstream exact approved-decision requirement and decision/source/updated-time snapshot predicates in the native Task write transaction. Existing no-receipt, failed-state, delivery-lease, saved-key and exact candidate checks remain in place. No audit policy, source schema, constructor activation, provider behavior or fallback was added.

## Verification and boundaries

- `pytest -q tests/test_meeting_task_runtime_native.py`: **17 passed**. This includes native Audit parent/key ownership, valid approved requeue, unreviewed candidate rejection, and malformed suffix rejection.
- The Audit producer boundary is exercised through `MeetingAlignmentCodexRunner.audit` -> real `RoutedCodexExecution` -> fake Codex CLI output -> assembled native Store claim. The resulting RuntimeAttempt retains `<run_id>:audit` and exact Meeting Run parent.
- `pytest -q tests/test_run_entity_schema.py -k 'meeting_audit_runtime_key or inverse_meeting_runtime_key_rejects'`: **5 passed**. This includes the two-cycle unequal-ID conversion and malformed/unsupported suffix cases.
- `pytest -q tests/test_meeting_alignment_agent.py tests/test_meeting_alignment_models.py -k 'audit'`: **3 passed, 164 deselected**.
- `git diff --check` and `ruff check app/store.py app/run_entity_schema.py tests/test_meeting_task_runtime_native.py tests/test_run_entity_schema.py`: **passed**.
- The broad focused selection `pytest -q --tb=no tests/test_meeting_alignment.py tests/test_meeting_alignment_agent.py tests/test_meeting_alignment_models.py tests/test_meeting_task_runtime_native.py tests/test_run_entity_schema.py` produced **131 failed, 261 passed**. The failure set is concentrated in the source-era Meeting selection (123 failures in `tests/test_meeting_alignment.py` plus one agent test), whose representative stack reaches `AutoReplyStore.upsert_meeting_alignment_job` querying `tasks.kind` and raises `sqlite3.OperationalError: no such column: kind` because this source-era fixture/query expects the legacy Task schema. This error occurs before later behavior assertions can run. The five representative source-era cases `test_discovery_failure_preserves_active_meeting_source` (four statuses) and `test_runner_correction_turn_uses_repair_prompt_with_field_errors` were run at pinned BASE and failed at the same query/frame (`app/store.py:13150` at BASE, line 13157 after merge). This establishes that representative source-fixture boundary at BASE; it does not prove each of the other 119 individual source-era failures has the same first frame.
- The other seven failures are `tests/test_run_entity_schema.py` inverse cases stopping at `_inverse_task_ids` with `ValueError: invalid Task2B inverse identity context`. All seven were reproduced at pinned BASE and stop at the same guard. The returned Task2B metadata includes current reviewed Workbench identity/high-water material that this Task/Run validator does not accept. This is a concrete composed migration incompatibility and remains release-blocking for broader Task/Run inverse acceptance; it is outside this Meeting-only code scope and is explicitly not waived or called pre-existing based only on assumption.

No runtime/constructor activation, provider send, main push, deployment or production behavior was exercised. The broad suite is not green; the passing focused native evidence establishes only this bounded Meeting integration.

## Commits and remaining release boundary

- Claim setup: `715ed6d41 claim Meeting upstream native integration`.
- Pinned upstream merge: `878c774620cf27cfc57293d383e1f4d7c625d0d4`.
- Meeting behavior adaptation and regression evidence: `6754ff0d8 Integrate Meeting Audit with native Run ownership`. It is committed after the merge and before independent review. The independent Task2B inverse metadata incompatibility still requires the separately scoped reconciliation before composed migration can be considered release-ready.
