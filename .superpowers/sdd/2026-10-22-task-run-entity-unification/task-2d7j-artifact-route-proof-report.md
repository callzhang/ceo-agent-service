# Task 2D7J Workbench artifact route proof report

Status: **COMPLETE for this bounded proof.** A genuine artifact created by the pinned predecessor's constructor/writer resolves through its original UUID download route before migration and through the current route after migration. A new native artifact/event inverses to a distinct source UUID and resolves through the unchanged predecessor route. JSON-portable Task and Run handoffs preserve its bytes, owners, IDs, event payload and native identity across two forward cycles.

Original Task2D7J base: `79ffb86f23382af2c3e4ca9411cc1eacaa98de92`. Resumption base after the independently accepted allocator prerequisite: `aca557a6984aec12dc799bbb815d8b7a807ba265`. Worktree: `/Users/derek/.codex/worktrees/task-run-entity-phase2/ceo-agent-service`.

## Checkpoint history

The earlier partial checkpoint proved the pinned predecessor's genuine constructor, artifact writer and UUID download route, then stopped when the valid constructor-created source hit the Task/Run `sqlite_sequence` rowid collision. That blocked run did not reach current artifact creation or inverse. The collision was repaired and independently accepted in [the Task/Run allocator-composition report](task-run-allocator-composition-report.md), implementation commit `96add74dc`, claim release `78ad32c84`, and accepted composition test commit `aca557a6984aec12dc799bbb815d8b7a807ba265`. The initial partial evidence is retained in commit `933ce4ec7` and the operator checkpoint in `796cd352c`.

On resumption, the operator scaffold also needed its stage order corrected: perform the first inverse before source-schema recapture, then recapture source metadata for each portable reforward; query predecessor artifacts through the actual mixed-`tasks` logical projection rather than assuming a physical `workbench_artifacts` source table. Those were operator errors, not reasons to relax source validation. With those corrections, the real route operator exposed the separate new-artifact `legacy_key` defect documented below and completed the required proof after its narrow fix.

Pinned predecessor: `/private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf`, exact revision `c38ce12cf1b2b18c147ea687bccdce11973827eb`. The operator verifies exact source code by the recorded file hashes and does not modify the predecessor.

## Operator evidence

Command:

```sh
out=$(mktemp -d /private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7j.final.XXXXXX)
python scripts/validate_task_2d7j_workbench_artifact_routes.py \
  --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf \
  --output-root "$out"
```

Successful output roots were `/private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7j.final.icg0mN` and the post-commit final verification `/private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7j.finalverify.FnFqbH`.

Pinned source code hashes:

- `app/workbench/store.py`: `b5c5b92fc0985ef21d865379193a948df171d02a3f8d43186a6023d78f1d4a66`
- `app/workbench/api.py`: `9401605ccbad499b6c6dccb110d2c3d0853c493250e492a51f2c9d467760cdf8`

The pinned constructor created source Task `b80fd868-7a53-4bf7-a839-26f06508ffba`, Turn `c69b3775-4b6d-46fc-95e7-a1e32423f77f`, and artifact UUID `e03babf3-69e0-466f-bcdc-3be52c45b7a7` by calling its real `append_artifact_event`. Its actual registered UUID download endpoint returned HTTP 200, exact bytes `67656e75696e6520776f726b62656e636820617274696661637420726f7574652070726f6f66006279746573`, and SHA-256 `950be2ec9699bb35b4dc6cb12df7af57cd3e237f0b42ee0f526c74c69a04e813`. The source Store event payload retained the source UUID and the artifact owner was the source Turn.

After capturing source Task/Run facts, both converters ran on that constructor-created database. The current route returned the same source artifact bytes and SHA. The current native Store's real `append_artifact_event` then created artifact `4` and event `3` on canonical Turn `2`, with bytes `6e6577206e6174697665206172746966616374206279746573` and SHA-256 `baf6b6b9da5e93c5bfeacaddfe0db73805f019079d30d5d1574e8bc04725825a`. Its stored typed event payload was `{"artifact_id":"4","label":"native-proof.bin","filename":"native-proof.bin","media_type":"application/octet-stream"}` and the current download route returned those exact bytes.

The first Task/Run inverse reconstructed a source UUID artifact on the same source Turn and preserved event ID `4`, the full event payload, file path and bytes. The unchanged pinned predecessor route returned HTTP 200 and the same SHA for that new UUID. The final post-commit run allocated source UUID `3233034a-3d25-4d8a-b6a5-875c7067c3fd`; the earlier successful pre-commit run allocated `28400824-783b-484a-b344-502f02369797`. Each of two cycles serialized Task inverse identity and Run handoff through JSON, recaptured source metadata while source schema was active, and reforwarded both converters. After each cycle, canonical parent/Turn IDs remained `1`/`2`, native artifact/event IDs remained `4`/`3`, owner and file bytes matched, the full event payload was unchanged, and the current download route returned SHA `baf6b6b9da5e93c5bfeacaddfe0db73805f019079d30d5d1574e8bc04725825a`. After the first cycle's inverse, the source UUID and event IDs still matched and the old UUID download route again returned the same bytes.

## Product correction and focused regression

The valid-source inverse exposed a source-contract defect for an artifact created after forward migration: the inverse used the artifact's public UUID as `tasks.legacy_key`, which violates the frozen source Tasks CHECK. A Workbench artifact's source public UUID, source numeric rowid and global physical Tasks ID are separate identities. The inverse now obtains the new source rowid from `source_key_next['workbench_artifacts']`; captured raw exceptions remain unchanged. This uses the existing per-kind source allocator and does not rewrite the source fixture, weaken validation, or introduce aliases.

`tests/test_task_entity_schema.py::test_new_native_workbench_artifact_inverse_allocates_numeric_source_legacy_key` is hermetic. It seeds a sparse captured source rowid `900000`, verifies that capture remains unchanged, creates a native artifact, inverses it, asserts that the new numeric source rowid exceeds `900000` and differs from global physical/canonical IDs, verifies the distinct source public UUID and Turn projection, and checks portable reforward identity. Before the correction, inverse construction reached and failed the frozen Tasks CHECK; with the correction, it passes. The end-to-end operator independently covers genuine Workbench writer/event APIs and the unchanged old download route.

Architecture and runtime documentation now state that a newly native-created artifact receives a source numeric rowid from its artifact-kind allocator while its public UUID remains separate.

## Verification and boundaries

- Full required operator command above: passed; output recorded all stages, hashes, route statuses, byte hashes, IDs, owners and payloads.
- Focused Task artifact inverse regression plus existing artifact event reference/cycle test: `2 passed`.
- `ruff check app/task_entity_schema.py tests/test_task_entity_schema.py scripts/validate_task_2d7j_workbench_artifact_routes.py`: passed.
- `python -m py_compile app/task_entity_schema.py tests/test_task_entity_schema.py scripts/validate_task_2d7j_workbench_artifact_routes.py`: passed.
- `git diff --check`: passed.
- Broader `pytest -q tests/test_task_entity_schema.py tests/test_run_entity_schema.py`: `103 passed, 1 failed`; the sole failure is unchanged `test_new_native_email_task_inverse_uses_predecessor_runtime_identity`, whose existing assertion supplies the hashed `email-classification:<sha256>` key to `_validate_runtime_operation_workload`, which rejects it because this runtime contract requires a persisted numeric task ID. The Workbench artifact changes do not touch that code or assertion.

No model/provider, background worker, production, main checkout, runtime constructor activation, push or deploy was used. The predecessor is a pinned historical Workbench proof source, not the latest production predecessor; this does not establish the separate Task5 production/native CLI/UI/packed gates or overall Task2D7B Fix1 acceptance. Parent retains independent composite/spec/quality review.

Implementation commit and final implementation HEAD: `b499b1f4be7c3c9ada84ee9851e3d421b0967916` (`Fix inverse identity for new Workbench artifacts`). It contains the converter correction, hermetic sparse-rowid regression, Workbench contract documentation and operator. The evidence report follows as a separate documentation-only commit; parent will report the resulting branch HEAD.

## Fix1 — predecessor source pin

Review base: `71960baee2acc641fcbbbe628e9dc36f09d73baf`. The operator now requires `--expected-store-sha256` and `--expected-api-sha256`. It hashes the selected predecessor files and compares both values before starting the predecessor constructor or writing into `--output-root`. The root remains caller-selected; no expected checkout path or hash is embedded in the operator or permanent tests.

Focused regression command:

```sh
pytest -q tests/test_task_2d7j_workbench_artifact_routes.py
```

Output: `2 passed`. One case accepts exact Store/API pins. The mismatch case uses temporary source files and a temporary empty output directory; the CLI exits with `predecessor source hash pin mismatch`, the output directory remains empty, and both predecessor files remain byte-for-byte unchanged.

The complete actual operator was rerun with the expected pins:

```sh
out=$(mktemp -d /private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7j-fix1.XXXXXX)
python scripts/validate_task_2d7j_workbench_artifact_routes.py \
  --predecessor-root /private/tmp/ceo-task-run-phase2-_ntbufyi/predecessor-c38ce12cf \
  --expected-store-sha256 b5c5b92fc0985ef21d865379193a948df171d02a3f8d43186a6023d78f1d4a66 \
  --expected-api-sha256 9401605ccbad499b6c6dccb110d2c3d0853c493250e492a51f2c9d467760cdf8 \
  --output-root "$out"
```

Output root: `/private/tmp/ceo-task-run-phase2-_ntbufyi/task-2d7j-fix1.lQz3gE`. The operator accepted the exact expected hashes and completed all previous proof stages. Source pre-migration route and current original route returned HTTP 200 and SHA `950be2ec9699bb35b4dc6cb12df7af57cd3e237f0b42ee0f526c74c69a04e813`. New artifact native ID `4`/event ID `3` inversed to source UUID `1c2230c0-bb08-49a7-a4a7-91125bb4822d` on Turn `f434e905-f6bd-4905-85ff-a8acff44da6c`; the unchanged predecessor UUID route returned HTTP 200 and exact bytes SHA `baf6b6b9da5e93c5bfeacaddfe0db73805f019079d30d5d1574e8bc04725825a`. Both portable reforward cycles preserved canonical parent/Turn `1`/`2`, native artifact/event `4`/`3`, source artifact/event UUID/ID, ownership, payload and bytes.

Static checks:

- `ruff check scripts/validate_task_2d7j_workbench_artifact_routes.py tests/test_task_2d7j_workbench_artifact_routes.py` — passed.
- `python -m py_compile scripts/validate_task_2d7j_workbench_artifact_routes.py tests/test_task_2d7j_workbench_artifact_routes.py` — passed.
- `git diff --check` — passed.

Fix1 code/operator/test/report commit: `b2f594e8ba7e11e7eb2369004ca861d69475e9fe` (`Pin predecessor hashes in Workbench proof operator`). No product runtime/schema/source constructor changes were made in Fix1. This closes the operator provenance finding only; parent retains the scoped rereview and composite acceptance decision.
