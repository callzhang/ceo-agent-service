# Task2D7B Workbench Turn Fix1 checkpoint

Status: **PARTIAL** — this is a coherent checkpoint, not acceptance of the Fix1 scope.

Fix base: `a8d5598e60eccd6808cedf85364f904d1cf7a301`

Required predecessor: `c38ce12cf1b2b18c147ea687bccdce11973827eb` at `/private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf` (the parent's single pinned source root; no predecessor code was changed).

## Changes in this checkpoint

- `app/task_entity_schema.py` now stores a source artifact public ID through the existing SQLite scalar codec in portable inverse handoff JSON, decodes and type-checks it before restoring source identity, and only applies the UUID constraint to newly allocated native artifacts.
- `app/workbench/store.py` updates the native Workbench Turn Task's `updated_at` with the exact stop timestamp in the same transaction as the stop request.
- `app/workbench/service_runtime.py` accepts canonical decimal native Turn and parent Task IDs for the producer contract; `conversation_id` retains the distinct parent Task identity. `tests/test_workbench_service_runtime.py` uses those native identities.
- `tests/test_task_entity_schema.py` covers BLOB source artifact identity through JSON serialization, inverse, and reforward.
- `tests/test_run_entity_schema.py` extends the UUID artifact payload conversion through inverse and two repeated forward/inverse cycles, checking the same canonical artifact ID, Turn owner, source UUID, and unrelated payload value.
- `tests/test_workbench_native_parent.py` covers lease renewal/expiry recovery/reclaim, stop versus late completion, exact Task update timestamp, event stream snapshot, native SSE terminal output, and the canonical artifact download route.

Narrow follow-up scope completed here: the assembled native parent/Turn test runs the actual `WorkbenchExecutor` producer through `ServiceWorkbenchRuntime` and real `RoutedCodexExecution`, with a fake process executor standing in for the CLI. It proves the completed Attempt is stored as `workload_kind='workbench'`, keyed by the canonical Turn Task ID, and retains the observed session ID. A separate native reader test writes local synthetic session JSONL files and two completed Attempt rows, verifies each exact transcript range is selected in Attempt order and the second message is projected for ordinal 2, then verifies a missing latest source yields the established unavailable-history payload. No provider or real CLI is invoked.

## Focused verification

Passed:

```text
pytest -q tests/test_workbench_native_parent.py -k 'actual_workbench_executor_routes_to_native_runtime_attempt_with_fake_cli or native_event_hydration_selects_attempt_transcript_ranges_and_handles_missing_source'
2 passed, 12 deselected in 1.87s

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

The source/native artifact route acceptance is not complete. The existing test begins with a UUID event payload and verifies the converter plus repeated migration cycles, and native download route coverage verifies canonical IDs/bytes. It does **not** yet exercise the pinned predecessor's actual UUID source download route before migration, create a new artifact through the native Workbench writer and inverse it to a source UUID route, then reforward twice while checking unchanged file bytes and all identities. This acceptance is assigned to the separate source-route prerequisite. It must take the pinned predecessor root as an explicit operator argument and must not hardcode a checkout or temporary root in pytest.

The review's Minor forward cross-Turn artifact-reference observation remains deferred by parent direction: `Task 2D7B: minor (deferred): forward conversion does not reject cross-Turn artifact references; parent explicitly deferred tightening this frozen source fact to final review.`

## Boundaries

No constructor activation, full suite, provider/model effects, production database, push, deploy, or main checkout changes. The two pre-existing dirty report files were preserved and not staged. Independent scoped SPEC/QUALITY review remains pending; source artifact acceptance remains open, so this does not complete original Fix1.
