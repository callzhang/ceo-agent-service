# task-semantic-storage.md — Repair verification（一次性验证记录，原样移出）

## Repair verification

The inherited three-file suite passed 433 tests before the repair, but lacked the
complete schema contract. The expanded semantic suite first failed with
`109 failed, 59 passed` using
`.venv/bin/pytest -q tests/test_task_semantic_store.py --tb=line`.
Missing columns, record classes, canonical enums, and primitive parameters were
the failures. After the schema/model/CRUD repair, the independent immutability
check remained red (`4 failed`, each `DID NOT RAISE IntegrityError`) for evidence
rewrite, provenance rewrite, deletion, and SQLite replacement. The corresponding
triggers made these checks green. Tests also exercise real dangling foreign-key
writes, canonical project-anchor type enforcement, valid record round trips,
invalid states, uniqueness, listing, source fidelity, and pre-semantic startup.

Initial contract repair validation: `.venv/bin/pytest -q tests/test_task_semantic_store.py
tests/test_store.py tests/test_task_models.py` passed **627 tests**. Python
compilation of both production files, Ruff on both production files and both
specified test files, and `git diff --check` also passed. These are isolated
storage checks, not evidence of a deployed runtime or applied legacy import.

The follow-up interface regression copies the approved standalone-task call
sequence unchanged: create signal ID, create task ID, link evidence, and get the
task. Before the return-value correction it failed with two Pydantic `int_type`
errors because `BusinessTask` and `BusinessTaskSignal` records reached the
integer evidence-link fields. Both create methods now return their persisted
integer IDs, and tests that inspect records use the getters. The same three-file
suite then passed **628 tests**, including this regression; compilation, Ruff,
and `git diff --check` also passed.

The nonblank/index quality regressions were run before the production correction:
`.venv/bin/pytest -q tests/test_task_semantic_store.py -k 'nonblank_text or
default_task_listing or list_indexes_are_present' --tb=line` produced
**135 failed, 76 passed, 202 deselected**. The 133 nonblank failures were direct
SQLite writes that did not raise `IntegrityError` after Pydantic had rejected the
same whitespace-only rows. The other failures were the absent default-list
index and the actual query plan's `USE TEMP B-TREE FOR ORDER BY`. Empty/plain-space
rejection and original-whitespace preservation already passed. The expanded
tests cover all 19 required text/resolution-time fields and capture the query
issued by the default listing API to inspect its plan.

After the correction, the same focused command passed **211 tests** with
202 deselected. The required three-file suite passed **838 tests**; Python
compilation, Ruff, and `git diff --check` also passed. These checks remain local
to the isolated Task 1 storage worktree; no runtime deployment was performed.
