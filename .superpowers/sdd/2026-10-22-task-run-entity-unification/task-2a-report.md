# Task 2A — native Task storage report

Status: implemented and locally verified; unactivated. This closes only the native Task/input/business-key API. Migration/inverse, runtime caller conversion, schema activation, native/UI/copy acceptance, publication, push and deploy remain outside Task 2A.

Implementation is in `app/task_entity_storage.py`. Its public functions are:

- `create_task(db, *, values, parameters=None)`
- `get_task(db, task_id)` and `get_task_with_inputs(db, task_id)`
- `update_task_cas(db, *, task_id, expected, changes, parameters=None)`
- `append_task_input(db, *, task_id, values)` and `get_task_input(db, input_id)`
- `bind_current_input(db, *, task_id, input_id, expected_input_version, input_version)`
- `bind_claimed_input(db, *, task_id, input_id, claimed_input_version)`
- `associate_business_key(db, *, task_id, business_key, created_at=None, updated_at=None)` and `list_task_business_keys(db, task_id)`

All APIs use the caller's connection and leave transaction control to the caller. Task writes target fixed native columns; kind-specific request validation remains in the assembled target's SQL triggers. Parameters use the existing native SQLite scalar codec. TaskInput identity is the full `(channel, conversation_id, trigger_message_id, input_revision_key)` key and existing rows are returned unchanged. Current and claimed pointers are independent. Business keys retain global uniqueness and permit multiple keys per Task; duplicate-key handling remains the caller's transaction/error responsibility. No task result or domain detail CRUD was added.

Verification: `python -m pytest -q tests/test_task_entity_storage.py` — 16 passed; `ruff check app/task_entity_storage.py tests/test_task_entity_storage.py`, `python -m compileall -q app/task_entity_storage.py tests/test_task_entity_storage.py`, and `git diff --check` passed. The tests create the complete Task1 assembled schema and cover all nine kinds, NULL absent facts, `ready_to_send`, parameter restrictions and scalar semantics, CAS misses, full-key idempotence/collisions, current-versus-claimed input, multiple business keys/global uniqueness, caller rollback, and patched old storage entry points.
