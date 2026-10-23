# Task 2D2B unrelated SQL interpolation regression correction

## Scope and cause

At BASE `4c102c189da9ff95c0d872f029e14876fdd33205`, two SQL query strings had lost their f-string prefixes in Meeting checkpoint `f90761f39f3759660373a91f14688fa92ad12407`:

- `AutoReplyStore._validate_scheduled_task_skill_refs` interpolates `config_projection('managed_skills')` and `config_projection('managed_skill_revisions')`.
- `AutoReplyStore.requeue_reply_task` interpolates `AGENT_RUN_SQL` to select stale runs before preserving its existing Task/Run/event transaction.

`git show f90761f39 -- app/store.py` shows each accidental removal. The source at `944138` retains both f-string prefixes. With the prefixes removed, SQLite receives `{config_projection(...)}` or `{AGENT_RUN_SQL}` literally and reports `unrecognized token: "{"`.

The correction restores only these two f-string prefixes. It adds no helper, fallback, policy, schema, lifecycle change, or activation. The Meeting runtime remains unactivated; no Reply lifecycle cutover or production action was performed.

## Regression evidence

Fail-first and green checks used real SQLite execution:

- Before restoring the Skill query prefix, `pytest -q tests/test_scheduled_task_store.py::test_managed_skill_ref_requires_exact_revision_belonging_to_skill` failed at `_validate_scheduled_task_skill_refs` with `sqlite3.OperationalError: unrecognized token: "{"`. After the correction it passes and preserves the existing assertion that a revision from another Skill is rejected.
- Before restoring the stale Run query prefix, `pytest -q tests/test_store.py::test_requeue_reply_task_fails_stale_run_and_records_terminal_event` failed inside the actual `requeue_reply_task` method with `sqlite3.OperationalError: unrecognized token: "{"`. After the correction it passes against SQLite Task/Run source declarations seeded by `task8_insert_parts`, `tests.test_task20_definitions.payload`, and `insert_run`. It checks the pending Task and requested availability/error, failed Run and cleared lease, and exact terminal failure event.

Focused validation:

- `pytest -q tests/test_consumer_system_release.py`: **28 passed**.
- `pytest -q tests/test_consumer_system_release.py`: **28 passed**.
- An exploratory combined selection of `tests/test_scheduled_task_store.py`, `tests/test_consumer_system_release.py`, and Reply requeue checks reported **85 passed, 3 failed**. Its failures stopped at fixture seams outside the corrected methods: `test_scheduled_run_attempt_lineage_reads_only_three_canonical_paths` (`task_inputs` absent), `test_current_schema_refuses_missing_schedule_command_column` (`service_state` absent), and the older `test_requeue_reply_task_records_stale_run_terminal_failure_event` (its `_enqueue_universal_reply_task` helper stops because `task_inputs` is absent). The actual-method regression passes independently in the final focused run. This exploratory selection included an earlier query-only check that was replaced by the actual-method regression; the query-only check is not counted as acceptance evidence. These failures were not compared against BASE, so they are recorded as observed fixture blockers rather than baseline failures.
- `ruff check app/store.py tests/test_store.py`: passed.
- `python -m compileall -q app/store.py tests/test_store.py`: passed.
- `git diff --check`: passed.

The older requeue test was not removed or rewritten. Its current fixture cannot reach the method because the high-level reply creator now targets native TaskInput tables that are absent from the old source fixture. The new test uses a real source-shaped Task/Run fixture to reach the existing method and verify the correction without adding runtime compatibility code.

## Boundaries

No provider, network, database migration, main-branch write, push, deploy, or production access occurred. The pre-existing modified report appendices `task-2d0-reply-claim-input-report.md` and `task-3c2a-adoption-forward-report.md` were preserved untouched.
