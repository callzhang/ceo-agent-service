# Task 3C2A forward converter report

## Implemented surface

`app/run_adoption_schema.py` now exposes `capture_run_adoptions(db)` and `migrate_run_adoptions(db, source_metadata, *, task_metadata, run_metadata)`. Capture requires the caller-owned transaction with foreign keys off and creates transaction-local TEMP copies of current Actions, selected adoption rows, receipts, and observers before Task2B/Run3B can rebuild their owners. It validates the exact selected current Actions and Action child catalogs. Returned capture metadata contains source catalogs, selected-kind counts, receipt columns, original sequence rows, and original Action FK findings; it does not contain source bodies.

The forward converter consumes Task2B's typed Task ranges/sparse identity map and Run3B's `agent_runs` identity map, installs the declared adoption owners, replaces `action_receipts` with its assembled `execution_run_id` schema, and resolves each receipt's source execution public ID through embedded candidate progress. It preserves receipt IDs and other columns, defers provider-receipt links until all receipts exist, preserves NULL and integer-zero weak Run sentinels, and resolves positive receipt/observer Run IDs. Observer task IDs use the actual `reply_tasks` owner map. Retained Action Run refs and generic Task refs are projected through typed owner metadata; SQLite JSONB `action_data.action_agent_run_id` is translated while other JSON values remain semantically unchanged. Adoption identities retain the source Action physical ID, raw public key storage type, target Run, and child ID; Review child IDs remain distinct from `audit_run_id`, and predecessor references resolve to the Review child key. Selections resolve exactly once through `candidate_option()` against the immutable candidate; current source has no stored `branch_json` field. Actions are reconstructed from `task_run_entity_assembly._action_objects()`, retired adoption/attempt rows are removed, and the source Actions sequence row is restored exactly.

The native runtime constructor and writers remain unactivated. The current frozen source has no selection rows, so nonempty selection conversion is covered by focused option-binding unit cases, not a populated frozen source row. The typed retained feedback public-domain references are translated from actual public identities; no broad integer rewrite was added.

## Verification

- RED: the first frozen source proof failed because the converter attempted to read adoption rows from Actions after Run3B had replaced those rows. It then exposed the need to stage selected Actions/receipt/observer rows before the Task/Run owner rebuild. The implementation now captures those rows before the converters run.
- Parent frozen proof command: `python /private/tmp/ceo-task-run-phase2-_ntbufyi/probe_adoption_forward.py`.
- GREEN: the parent frozen proof reached all source-derived typed hashes for results (1,354; `c4a18622009237757609f74f2938b58e01fdf5b89dbc443b34ab7fab90304eb1`), Reviews (1,343; `3561997d965104317f7384ad2f71a8bdc94d57d01145e713a580ef6999835f2f`), progress (442; `3e5f91c8102db58083eaf312c3a36958bdfc69549b356c9642a385facd71fa31`), all receipts (18,661; `1b82d5e25b46d4cbf39b296860cd999c3f6e1913ca36b667e33214dbefc4b3a5`), and observers (512; `f30c4df55fb9d3774cb070f4968378537761cb4da06d63e82b847372c82f8245`), with zero FK findings in all eight owned tables and `integrity_check=ok`. The original source Run→Task finding remained exactly `runs(190042) -> tasks(1)`. The proof used an in-memory copy and rolled it back; no source file or production database was written. It completed in 358.21 seconds.
- Focused unit tests: `pytest -q tests/test_run_adoption_schema.py` — 4 passed. They cover exact NULL/zero sentinel preservation, positive Run translation, unresolved positive-reference rejection, exact option resolution, unknown-option rejection, and restoring the Actions autoincrement watermark when its highest source row is retired.
- `ruff check app/run_adoption_schema.py tests/test_run_adoption_schema.py` — passed.
- `python -m py_compile app/run_adoption_schema.py` — passed.
- `git diff --check` — passed.

## Gates before C2B or activation

1. Add a populated source/native converter fixture for nonempty bound selections, remapped Task/Run IDs, Review business ID 7 with Audit Run ID 9007, caller rollback, and exact allocator/catalog restoration. The frozen source covers the no-attempt-map receipt and zero/null weak refs.
2. Add caller rollback and catalog-rejection assertions against the actual fixture. Capture is transaction-local and cleans its TEMP staging tables on successful conversion; caller rollback is the recovery boundary.
3. Add full current-fact inverse metadata needed by C2B. This converter does not claim inverse coverage, constructor activation, runtime cutover, publication, or deployment.
