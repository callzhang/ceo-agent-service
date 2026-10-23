# Task Run allocator composition repair report

Status: **converter repair verified; artifact-route proof advanced past allocator collision and stopped at a later Task source-catalog rejection.**

BASE: `f4cb7e1e26e8bf76af3f7a6df6881ae1c26c3692`.

## Cause and repair

The genuine Workbench constructor and artifact writer produced source allocator rows `(1,tasks:published,2)`, `(2,tasks,3)`, `(3,runs:published,2)`, `(4,runs,2)`. Task conversion preserved those rows. Run conversion dropped the source `runs` table, then inserted native `workbench_events`, whose `sqlite_sequence` row reused technical rowid 4. Forward restoration then tried to insert captured source `runs` at rowid 4 and raised `UNIQUE constraint failed: sqlite_sequence.rowid`.

The Run converter now relocates only a colliding native Run-owner `sqlite_sequence.rowid`, placing it beyond current rows while skipping every captured source rowid, including sparse/deleted rowids beyond the current maximum. It never changes source rowid/name/water facts or the native owner's name/high-water. The same narrow logic runs before inverse sequence restoration. Other sequence rows are left in place. There is no source-input edit, fake reservation row, generic DB repair path, new schema field, compatibility fallback, or runtime activation.

## Regression and verification

Baseline RED was reproduced with the required genuine operator and pinned predecessor:

```sh
out=$(mktemp -d /private/tmp/ceo-task-run-phase2-_ntbufyi/task-allocator-red.XXXXXX)
python scripts/validate_task_2d7j_workbench_artifact_routes.py \
  --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf \
  --output-root "$out"
```

It reached the real source artifact UUID route (HTTP 200, expected bytes and SHA-256), preserved the exact four source sequence rows through Task conversion, and failed in `migrate_run_entities` at captured source rowid 4 with the reported unique-constraint error. The operator's source facts were not modified.

The focused hermetic regression recreates source rows 1–4, drops source `runs`, creates an AUTOINCREMENT native event owner at rowid 4, and verifies exact source restoration plus safe target relocation. It also checks a reserved sparse source rowid is skipped as a destination. The real artifact converter regression now asserts source Run allocator facts `(10,runs,8000)` and `(12,runs:published,8000)` remain exact, `runs_other` remains unchanged, native event allocation is monotonic, and native owner high-water survives deletion and two portable inverse/reforward cycles.

Passed:

- `pytest -q tests/test_run_entity_schema.py::test_run_owner_allocator_composes_with_captured_source_sequence_rowids tests/test_run_entity_schema.py::test_artifact_created_uuid_reference_survives_native_forward_and_inverse` — 2 passed.
- `pytest -q tests/test_run_entity_schema.py` — 73 passed.
- `ruff check app/run_entity_schema.py tests/test_run_entity_schema.py` — passed.
- `python -m py_compile app/run_entity_schema.py tests/test_run_entity_schema.py` — passed.
- `git diff --check` — passed.

`pytest -q tests/test_run_entity_schema.py tests/test_task_entity_schema.py` produced 101 passed and one existing unrelated failure at `tests/test_task_entity_schema.py::test_new_native_email_task_inverse_uses_predecessor_runtime_identity`: `AutoReplyStore._validate_runtime_operation_workload` rejects the test's generated email-classification key because it requires a persisted integer task ID.

After repair, the required artifact-route operator was rerun with the same explicit pinned predecessor and a fresh temporary output root. It passed the allocator collision and proceeded through artifact migration/inverse, then stopped at the first reforward Task capture (`app/task_entity_schema.py:79`): `ValueError: unsupported current Task source declaration`. This is later than the repaired Run divergence and remains for the original operator/proof owner; no operator source or input facts were changed.

The architecture and runtime mechanism docs now state the allocator composition rule. No provider, model, main checkout, production service, push, or deploy was used. The final commit is the single implementation/evidence commit that includes this report; its exact HEAD is provided in the task handoff to avoid a report-only follow-up commit.
