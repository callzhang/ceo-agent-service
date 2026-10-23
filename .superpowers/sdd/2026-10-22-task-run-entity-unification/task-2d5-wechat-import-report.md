# Task2D5 WeChat Memory import cutover report

Base: `d6e367d3d1d5d691b4541618cef4d7ea5807c65f` in the assigned isolated worktree.

## Current native owner

`begin_wechat_memory_import_job` now inserts a native `tasks.kind='wechat_import'` row and returns its canonical `tasks.id`. It preserves the source DAO's `_require_runtime_attempt_text` behavior: `import_run_id` and `account_id` are trimmed and must be nonempty. Both are stored as the only Task parameters, retaining their TEXT values. Each invocation creates a distinct Task even when those two values repeat. No source job integer alias, generation, attempt count, due time, lease, or fabricated Run is added.

The prior source job's fields map as follows:

| Source field | Native owner |
| --- | --- |
| `id` | Retired as a current runtime identity; the native API returns `tasks.id` |
| `import_run_id`, `account_id` | `tasks.parameters_json`, preserving the existing attribution values |
| `status`, `error` | `tasks.status`, `tasks.error`, retaining `running` / `completed` / `failed` |
| `created_at`, `updated_at` | `tasks.created_at`, `tasks.updated_at` |
| `finished_at` | `task_results.finished_at` for `result_kind='wechat_import'` |

Creation and terminal writes use SQLite `current_timestamp` inside the same write transaction, matching the source DAO's database clock. A repeated matching terminal write returns without changing timestamps; a conflicting terminal status or error is rejected. No result body or raw routed output is stored.

Both `CodexMemoryExtractionRunner.extract` and `CodexMemoryRecallMatcher._match_statement` now use the returned canonical Task ID in `memory:wechat_memory_import_job:{task_id}` and pass that Task ID to finish. The exact native RuntimeAttempt parent check validates a positive canonical ID and requires a running `wechat_import` Task. Its `parent_id` stays NULL because an import operation has no professional Run. Other Memory workload branches are unchanged.

## Verification

The actual BASE source writer was executed against the assembled native Task/Run target with valid nonempty attribution and failed with `OperationalError: no such column: legacy_key`:

```text
python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.test_wechat_import_native import AssembledTaskStore
from app.task12_storage import insert_task12
with TemporaryDirectory() as tmp:
    store = AssembledTaskStore(Path(tmp) / 'native.sqlite3')
    insert_task12(store.db, 'wechat_memory_import_jobs', {
        'import_run_id': 'scope', 'account_id': 'acct', 'status': 'running'
    })
PY
OperationalError: no such column: legacy_key
```

The regression now asserts native Task ownership and retained parameters instead. An earlier draft of the red test used empty attribution and failed at the source validator, so that run was discarded as an invalid baseline proof; the test was corrected to preserve the real source validator contract.

Final focused commands and output:

```text
pytest -q tests/test_wechat_import_native.py
7 passed

pytest -q tests/wechat/test_memory.py -k 'codex_extraction_creates_parent_before_routed_invocation or recall_matcher_uses_one_exact_query_per_candidate'
2 passed, 51 deselected

pytest -q tests/test_store.py -k wechat_import_jobs_are_distinct_per_invocation
1 passed, 421 deselected

python -m ruff check app/store.py app/wechat/memory_import.py tests/test_wechat_import_native.py tests/wechat/test_memory.py tests/test_store.py
All checks passed!

python -m compileall -q app/store.py app/wechat/memory_import.py
git diff --check
passed
```

The new assembled-target tests use both actual producer classes with a persisted RuntimeAttempt executor substitute. They cover producer success and parser failure, canonical workload keys, exact Task parameters and statuses, NULL operation parent, no Run or result-body creation, terminal timestamp idempotence/conflict, malformed keys, finished and wrong-kind parents, and independent operation attempts for separate Tasks.

An exploratory run of the full `tests/wechat/test_memory.py` file had 2 unrelated existing failures in the WeChat candidate-writer tests: their ordinary `AutoReplyStore` fixture has no assembled `runtime_attempts` table, so candidate RuntimeAttempt inspection raises `no such table: runtime_attempts`; the concurrency test consequently observed one successful writer but not the expected competing-writer error. Those tests use a different workload family and were not changed by this slice. The focused extraction and recall tests pass on the assembled target.

## Remaining dependencies

This is a local cutover slice on the assembled target. Constructor activation, full-copy/inverse and cold-open conversion checks, other family producers/readers, generic dashboard/History, CLI/UI, packed build and release verification remain pending. No production checkout, provider, model, or Memory MCP call was used; nothing was pushed or deployed.
