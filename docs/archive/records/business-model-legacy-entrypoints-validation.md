# D1a legacy mutation entrypoints — 2026-10-20

D1a retires manual legacy writes; it is not completion of legacy business-model
retirement. Base: `f314f68cf3aabf9d8c0cd887c0e8ade15cedeea6`.

The actual parser/dispatcher no longer exposes backfill-routine-process-todos,
backfill-todo-deadlines, backfill-todo-owner-ids or repair-task-projects-apply.
Three orphan backfill modules, their deadline output schema and exclusive tests
are removed. Project-repair apply-only functions are removed; its read-only
plan remains. Semantic-import plan/apply, minutes-owner backfill, current Task
writes/synchronization, historical GET and History are outside this change.
No Store, outbox, DDL, database rows, external actions or production state change.
README, architecture and runtime descriptions match these boundaries.

The real-parser regression first failed for all four retired commands (RED:
4 failed, 6 passed). GREEN batches: CLI11 passed, plan/schema24 passed, adjacent
CLI8 passed. Batches overlap and must not be summed as independent cases.
Ruff and diff whitespace checks passed. Retained ASTs match base: CLI133/135
(top-level parser/main intentionally change), repair17/17 and test_cli260/260.
Only feature-exclusive tests/assertions were retired with their implementation.

Independent SPEC initially found stale README apply instructions (P2).
The same implementer corrected the current command example and behavior text;
SPEC then passed. Independent QUALITY found no P1/P2, independently matched
all14 frozen paths/statuses/hashes and patch, ran the retained-AST check, and
checked deleted-call references, the actual parser tests and raw log outcomes.
Reviewers did not rerun pytest, execute CLI, operate any DB or access production.
Root separately verified all original path states and log hashes. No production
push/deploy is claimed. This source-only batch does not establish data migration,
schema inverse or real business delivery; those separate gates remain.

Frozen original implementation manifest SHA256:
`f9eae175bcd059214b95e63ae04b3f4d84e033d0f8ccba86c435b74be2be08e3`.
Patch SHA256: `903726bd00468b8e61719c248c477a7d38ceb4501268f0dab5c041975ddc1664`.
The present parent evidence document was added after that freeze; it does not
rewrite the frozen candidate. Raw task artifacts remain temporary at
`/tmp/ceo-d1-entrypoints.IZf6bC` while this broader migration is in progress.

- red: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_cli.py -k 'retired_legacy_mutation_commands_are_not_parsed or retained_task_commands_still_parse or task_project_repair_plan_parses_required_paths'`
- green_cli: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_cli.py -k 'retired_legacy_mutation_commands_are_not_parsed or retained_task_commands_still_parse or task_project_repair_plan_parses_required_paths or task_semantic_import_commands_parse_manifest_and_limits'`
- green_plan_schema: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_task_project_repair.py tests/test_schema_snapshots.py`
- green_adjacent: `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_cli.py -k 'backfill or task_project_repair or task_semantic_import'`
- ast: `/Users/derek/miniforge3/bin/python /tmp/ceo-d1-entrypoints.IZf6bC/verify_retained_ast.py`
- readme_check: `git diff --check && rg -n repair-task-projects README.md`

Raw log SHA256:

- red.log: `f934b5903dc17e45d783eea59608fe91c21e1c65bedf9dbbf0acba20401b0e43`
- green-cli.log: `7a2230a3f7e9e99f28094b2d546fbd80db672cf1aa026d741bd031359b828716`
- green-plan-schema.log: `2f0fe92da6d4da947f8cda863356b8245220e88350bf755c5cef19bf3eb96ccc`
- green-adjacent.log: `9b3733b55b43146c0c6068734d2af405ae8870cf3122f6e4c1ca52f3a0f95a00`
