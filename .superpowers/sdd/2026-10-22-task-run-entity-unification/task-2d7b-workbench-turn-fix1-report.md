# Task2D7B Workbench Turn Fix1 checkpoint

Status: **PARTIAL** — this is a coherent checkpoint, not acceptance of the Fix1 scope.

Fix base: `a8d5598e60eccd6808cedf85364f904d1cf7a301`

Required predecessor: `c38ce12cf1b2b18c147ea687bccdce11973827eb` at `/Users/derek/.codex/worktrees/audit-feedback-batching/ceo-agent-service` (read-only; product sources inspected; no predecessor code was changed).

## Changes in this checkpoint

- `app/task_entity_schema.py` now stores a source artifact public ID through the existing SQLite scalar codec in portable inverse handoff JSON, decodes and type-checks it before restoring source identity, and only applies the UUID constraint to newly allocated native artifacts.
- `app/workbench/store.py` updates the native Workbench Turn Task's `updated_at` with the exact stop timestamp in the same transaction as the stop request.
- `app/workbench/service_runtime.py` accepts canonical decimal native Turn and parent Task IDs for the producer contract; `conversation_id` retains the distinct parent Task identity. `tests/test_workbench_service_runtime.py` uses those native identities.
- `tests/test_task_entity_schema.py` covers BLOB source artifact identity through JSON serialization, inverse, and reforward.
- `tests/test_run_entity_schema.py` extends the UUID artifact payload conversion through inverse and two repeated forward/inverse cycles, checking the same canonical artifact ID, Turn owner, source UUID, and unrelated payload value.
- `tests/test_workbench_native_parent.py` covers lease renewal/expiry recovery/reclaim, stop versus late completion, exact Task update timestamp, event stream snapshot, native SSE terminal output, and the canonical artifact download route.

## Focused verification

Passed:

```text
pytest -q tests/test_workbench_service_runtime.py
2 passed in 0.37s

pytest -q tests/test_workbench_native_parent.py
12 passed in 9.69s

pytest -q tests/test_run_entity_schema.py -k artifact_created_uuid_reference
1 passed, 71 deselected in 1.30s

pytest -q tests/test_task_entity_schema.py -k 'blob_typed_public_identity or blob_workbench_artifact_identity'
2 passed, 27 deselected in 1.58s

ruff check app/task_entity_schema.py app/workbench/service_runtime.py tests/test_task_entity_schema.py tests/test_run_entity_schema.py tests/test_workbench_native_parent.py tests/test_workbench_service_runtime.py
All checks passed.

git diff --check
python -m compileall -q app/task_entity_schema.py app/workbench/store.py app/workbench/service_runtime.py
```

The full `tests/test_task_entity_schema.py` selection produced 28 passes and one unrelated failure: `test_new_native_email_task_inverse_uses_predecessor_runtime_identity` expects an Email classification hash workload key to pass, while current `AutoReplyStore._validate_runtime_operation_workload` requires a persisted numeric Task ID. No Email behavior was changed.

## Still open from the Fix1 requirements

The source/native artifact route acceptance is not complete. The existing test begins with a UUID event payload and verifies the converter plus repeated migration cycles, and native download route coverage verifies canonical IDs/bytes. It does **not** yet exercise the pinned predecessor's actual UUID source download route before migration, create a new artifact through the native Workbench writer and inverse it to a source UUID route, then reforward twice while checking unchanged file bytes and all identities. The predecessor root was identified and its route/store definitions were inspected, but no source-route integration harness was completed. The predecessor fixture must be supplied by an explicit operator argument; do not hardcode a checkout or temporary root in pytest.

The actual `WorkbenchExecutor` → `ServiceWorkbenchRuntime` → `RoutedCodexExecution` fake-CLI path is not yet proven to persist a native `RuntimeAttempt`. Nor is the multi-Attempt source session/transcript selection matrix covered here. The new service-runtime unit tests still use a fake routed-execution object. These requirements remain open for the next dedicated tool unit; no real CLI/provider was invoked.

The review's Minor forward cross-Turn artifact-reference observation remains deferred by parent direction: `Task 2D7B: minor (deferred): forward conversion does not reject cross-Turn artifact references; parent explicitly deferred tightening this frozen source fact to final review.`

## Boundaries

No constructor activation, full suite, provider/model effects, production database, push, deploy, or main checkout changes. The two pre-existing dirty report files were preserved and not staged. Independent scoped SPEC/QUALITY review remains pending; because required Fix1 acceptance gaps remain, this checkpoint must not be represented as completed.
