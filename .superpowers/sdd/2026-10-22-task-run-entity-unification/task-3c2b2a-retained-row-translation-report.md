# Task3C2B2A retained Action row translator

Implemented `retained_action_source_rows(db, source_metadata, *, task_inverse_metadata, run_inverse_metadata)` in `app/run_adoption_inverse.py`. Call it inside the caller-owned FK-off transaction after the actual Task inverse and then actual Run inverse. It returns source-shaped dictionaries containing stored columns only; it performs no SQL writes, DDL changes, commits, or allocator updates.

The helper validates the exact captured source Actions catalog and the exact assembled native Actions catalog. It reconstructs the eight source-only retired adoption columns as NULL, and reverses `agent_run_id`, the three typed task-delivery references, typed Feedback decision/round IDs, and the JSONB `action_data.agent_run_id` scalar. Run and Task owner resolution uses the actual inverse handoffs and restored source Task rows. Feedback IDs come from the generated source `<kind>__id` public field. Action IDs, weak Run NULL/zero values, SQLite types, all other stored fields, and unmodified JSON values are retained.

Actual-source tests use `_populated_source(remap_tasks=True, remap_runs=True, feedback=True, stop_before_adoption=True)`, actual C2A forward conversion, Task inverse and Run inverse. The test compares all original retained source rows, checks a newly written native generic Action and native task memory event, verifies feedback public IDs and JSON Run references, checks that the helper leaves Actions and sequence rows unchanged, and rejects unresolved positive Run and Task references.

Verification:

- RED: `pytest -q tests/test_run_adoption_inverse_rows.py::test_actual_retained_rows_roundtrip_typed_run_task_feedback_and_payload_facts` failed with `ValueError: unsupported current native Action catalog` while validating the target catalog against unnormalized factory SQL.
- GREEN: `pytest -q tests/test_run_adoption_inverse_rows.py` — 3 passed.
- Remaining B2B/B2C: adopted current-fact Action row reconstruction, composing this translator with receipt/observer inverse, and independent review/cold-open/forward acceptance. This report makes no whole-C2B completion claim.
