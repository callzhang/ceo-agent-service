# Task 2D7J Workbench artifact route proof report

Status: **PARTIAL — blocked by a valid-source Task/Run allocator composition collision.** The pinned predecessor's real constructor, artifact writer, and UUID HTTP download route passed before migration. The required current native artifact creation, inverse, source UUID download, and two reforward cycles could not run because the existing composed converters fail first on the constructor-created database. This report records the failure without changing source sequence facts.

Base: `79ffb86f23382af2c3e4ca9411cc1eacaa98de92`.

Pinned predecessor: `/private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf`, exact revision `c38ce12cf1b2b18c147ea687bccdce11973827eb`.

## Actual route evidence

Command:

```sh
work=$(mktemp -d /private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7j.XXXXXX)
python scripts/validate_task_2d7j_workbench_artifact_routes.py \
  --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf \
  --output-root "$work"
```

The retained failing fixture is `/private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7j.FTjgnp`. The source WorkbenchStore constructor created a UUID parent, UUID Turn, and UUID artifact through `append_artifact_event`; the registered predecessor HTTP endpoint returned status 200 and exact bytes `67656e75696e6520776f726b62656e636820617274696661637420726f7574652070726f6f66006279746573`, SHA-256 `950be2ec9699bb35b4dc6cb12df7af57cd3e237f0b42ee0f526c74c69a04e813`. The cold-start response identified the actual pinned files:

- `app/workbench/store.py`: `b5c5b92fc0985ef21d865379193a948df171d02a3f8d43186a6023d78f1d4a66`
- `app/workbench/api.py`: `9401605ccbad499b6c6dccb110d2c3d0853c493250e492a51f2c9d467760cdf8`

## Blocking converter evidence

On the untouched constructor-created source database, the operator captures Task and Run metadata before either conversion, converts Task first, and then invokes `migrate_run_entities` with its captured Run metadata. Exact sequence rows `(rowid,name,seq)` before Task conversion were:

```text
[(1, 'tasks:published', 2), (2, 'tasks', 3),
 (3, 'runs:published', 2), (4, 'runs', 2)]
```

After Task conversion and immediately before Run conversion they were still exactly:

```text
[(1, 'tasks:published', 2), (2, 'tasks', 3),
 (3, 'runs:published', 2), (4, 'runs', 2)]
```

Captured `run_source['sequence_rows']` was:

```text
[(3, 'runs:published', 2), (4, 'runs', 2)]
```

Run conversion had replaced source `runs` with the target event table, which allocated `sqlite_sequence` row `(4, 'workbench_events', 2)`. At `app/run_entity_schema.py:747`, restoring the captured source row `(4, 'runs', 2)` failed with `sqlite3.IntegrityError: UNIQUE constraint failed: sqlite_sequence.rowid`. The operator deliberately does not renumber or delete sequence rows to get past this collision; those values are converter input and altering them would hide the allocator interaction that this proof must establish.

The source artifact identity was `7c8a6168-4d81-43f4-81f3-af45a0234ac1`, parent `5f4ec34d-703d-4fa5-a34a-8d2e3212d7bd`, and Turn `79d3d0d8-6759-4825-98b6-0d8d688daa72`. No current artifact or event was created. No inverse, newly allocated source UUID route, or reforward cycle was reached.

## Verification and limits

`ruff check scripts/validate_task_2d7j_workbench_artifact_routes.py`, `python -m py_compile scripts/validate_task_2d7j_workbench_artifact_routes.py`, and scoped `git diff --check` pass. The operator remains explicit: it requires `--predecessor-root` and an existing empty `--output-root`; there is no hardcoded predecessor or task-temp path in permanent pytest.

No provider, model, main checkout, production service, runtime constructor activation, push, or deploy was used. The failure occurs in the existing Task/Run conversion composition before the native Workbench writer stage. A separate bounded allocator-composition repair/proof is required before this route acceptance can continue. The original source Route 200 is not evidence that the required inverse/reforward acceptance passed.

Operator/report commit: `796cd352c` (full commit `796cd352cfd9381cd1b51aaf736795820dd85edb`). Final HEAD for this partial checkpoint: `933ce4ec7d7230bd62f5aacdec76176eac4d0c26`. The open Task2D7B Fix1 is not accepted by this partial proof.
