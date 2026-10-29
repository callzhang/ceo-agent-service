# Legacy Work Project/Todo/Update writer retirement (2026-10-20)

## Scope and source boundary

The five `AutoReplyStore` methods `create_work_project`,
`update_work_project`, `create_work_todo`, `update_work_todo`, and
`create_work_update` had no application caller beyond their own recursive
transaction entry. They are removed. The other Store functions, current
BusinessTask writers, typed Business/Action declarations, historical models,
physical rows, readers, and `task_semantic_import.apply` remain. The importer
still requires its source-backed manifest and preserves `import_task_id`.
The archived old-code program inside `validate_action_email_copy.py` retains
its old method calls for a real predecessor cold-read check; only that script's
current-copy fixture uses `insert_business(db, ...)` in its existing transaction.

The 16 affected test modules contained 181 old method call expressions. Their
historical setup now uses five small helpers in
`tests/support/legacy_todo_facts.py`: typed `insert_business`,
`update_business`, and `insert_sync_action` within Store-managed transactions.
The helpers do not recreate runtime writer APIs, default/validation policy, or
source promotion. The mixed Store test retains its historical reader,
follow-up, and operation assertions; its name now describes historical fixture
readback. The renamed exact node passes separately in
`/tmp/ceo-d2-legacy-writers.mcMs23/renamed-mixed-test-green.log`.

## Fail-first and focused tests

The API-retirement regression first failed on
`AutoReplyStore.create_work_project` (observed pytest output; no raw log was
captured for that first invocation). It then passed in the 31-case core group.
The raw core run is
`/tmp/ceo-d2-legacy-writers.mcMs23/core-green.log`: 31 passed in 66.11s,
covering retained legacy readers, the mixed Store test, project repair, and
the semantic-import module. The original semantic-import callback used a raw
SQLite connection lacking the current schema's `ceo_task_restore` function.
The release owner reproduced the identical failure on a clean predecessor at
`/tmp/ceo-d1b-copy.JbC2V9/d2-baseline-source-fixture.log`. Candidate RED and
corrected GREEN logs are `current-baseline-failure.log` and
`current-fixture-green.log` in the same D2 temp directory. The callback now
uses the Store connection with the same `BEGIN IMMEDIATE`, source mutation,
commit-before-apply timing, and assertions.

AST-selected adjacent test owners ran 63 pass/3 fail before repairing three
pre-existing flat-schema fixtures; the exact three failures were reproduced
on the clean predecessor in
`/tmp/ceo-d1b-copy.JbC2V9/d2-baseline-adjacent-fixtures.log`. The affected
tests now use typed current projection/update APIs for the same historical
values, timestamps and public IDs. Their focused rerun passed 3/3 in 9.67s
(`adjacent-fixtures-green.log`). The 29 AST-selected audit-web owners ran
28 pass/1 fail. The worker-status failure is the separately documented D1c
fixture follow-up in
`docs/business-model-legacy-todo-monitor-followup-validation.md`; its
focused rerun passed 1/1 (`status-fixture-green.log`). The raw wider runs are
`other-selected.log` and `audit-selected.log` in the D2 temp directory. These
groups overlap, so their counts must not be added as distinct test totals.

## Retention and release boundary

Comparison against the predecessor shows all existing test assertion ASTs
unchanged except the worker-status test's three added D1c assertions and the
storage-maintenance test's three historical read queries. For those three
queries, the comparison operators, expected strings, public-ID bindings and
row checks remain; only old table SQL operands became current typed
projections. No existing test function was deleted. The old-code embedded
program and source-backed semantic importer are preserved.

This is a local source and focused-test result. The release owner retains
independent source review, database-copy/inverse verification, provider-fake
checks, commit/release decisions and live readback. This slice does not
declare the full business-model D migration complete.
