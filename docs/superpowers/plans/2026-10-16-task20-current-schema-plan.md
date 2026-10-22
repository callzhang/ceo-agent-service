# Current Tasks20 declaration helpers implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development after canonical SPEC then QUALITY. This prerequisite does not select current constructors or release branches.

**Goal:** Supply the finite target factory and declaration predicates needed by the complete current constructor cutover, preserving native repair modes and original physical-child relationships.

**Architecture:** Keep historical `task_storage` and `workbench_storage` factories unchanged. A separate current-schema module creates/repairs only the accepted Tasks20 declaration, the retained feedback child's exact three generated FK endpoints, and the retained Workbench confirmation/search auxiliaries. Existing physical `tasks(id)` child guards retain their SQL/error/type semantics; generated public-key endpoints use their already approved native FKs and must not receive the old physical Reply-ID guards. No new policy, business field, caller transaction owner or source-to-target fallback is introduced.

**Tech Stack:** Python3.13.12, SQLite3.50.4, existing exact declarations and mapped Runs metadata, focused in-memory pytest.

## Complete code before application

- `2026-10-16-task20-current-schema-candidate.py` is the complete future `app/task20_current_schema.py`.
- `2026-10-16-task20-current-schema-regression.py` exercises this canonical directly. It uses the existing installed source-oracle fixtures and migration component. No app edit or constructor runs.
- `2026-10-16-task20-current-schema-preflight.py` is the pinned focused runner.

All child fields/index/append-only SQL come from the exact existing `feedback_iteration_decision_items` source metadata, with only its three approved parent reference substitutions. The original Workbench declaration constants provide confirmation and search SQL. Both former artifact/attachment tables remain retired; no task source body is copied into a new column or durable metadata.

## Explicit current interfaces

- `create_current_task20_schema(db)`: native IF-NOT-EXISTS creation/repair of Tasks table,51 indexes and31 triggers. Initialize published counter to0 only when absent, using MAIN, preserving current positive counters and unrelated TEMP counters. Do not commit or begin a helper-owned transaction.
- `create_current_feedback_child_schema(db)`: exact retained three-column child, original PK/index/append-only guards, mapped public FK endpoints. Native IF-NOT-EXISTS repair preserves existing rows.
- `install_current_task_child_guards(db)`: source physical-id guard SQL only for `tasks(id)` references. Keep source parent namespaces/error messages. Do not interpret the three generated feedback endpoint FKs as physical Reply IDs. This preserves existing relationship enforcement; it adds no review/auth/effect/safety policy.
- `create_current_workbench_auxiliary_schema(db)`: existing confirmation/search/FTS declarations and their physical child guard; never recreate `workbench_artifacts` or `workbench_attachments`.
- `task20_schema_is_current(db, *, allow_missing_objects=False)`: exact known table SQL/expected objects, source-retired names absent; preserve the native predicate's explicit missing-index/trigger repair mode. Missing Tasks table and malformed present object SQL remain invalid. Additional unrelated objects retain native handling.
- `task20_feedback_child_is_current(db, *, allow_missing_objects=False)`: exact mapped child declarations, with original constructor repair for a missing child table or auxiliary object in explicit repair mode. A malformed present table remains invalid.
- `task20_workbench_schema_is_current(db, *, allow_missing_objects=False)`: same mapped Runs metadata check and retained auxiliary table checks; explicit missing nontable repair only. Original historical/native Runs branch is not selected by this current-only interface.

## Regressions and reviews

- [x] Complete canonical and regression before application. Correct the initial fixture/creation-order failure: original declaration inventories list indexes before tables, so table creation must precede index creation, as the source factory already does.
- [x] Initial103cases pass2.13s. Strengthen the old-installer negative control to use an otherwise valid existing decision/item/round endpoint triple, with public key8 differing from physical Tasks8. Do not credit an invalid-FK control as semantic proof.
- [x] Root final103/2.03s, fresh SPEC103/1.97s and fresh QUALITY103/3.72s/Ruff PASS. Run final103cases and Ruff, then fresh SPEC followed by QUALITY. Required cases: migration/fresh declaration convergence actual/fresh, all82 missing index/trigger repairs, malformed present SQL, all12 native-name reappearances, old source factory remains82-column oracle, MAIN/TEMP counter idempotence, allthree retained child public endpoints, existing physical parent/null/error behavior, mapped Runs and required retained Workbench tables.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B docs/superpowers/plans/2026-10-16-task20-current-schema-preflight.py
```

- [ ] Only after canonical reviews, copy exact helper plus accepted test with documented import/path substitution; verify hashes/AST scope, run installed focused cases and fresh applied SPEC then QUALITY. Update both behavior documents in that application commit to describe the unselected helper.

This does not settle the startup feedback round-backfill disposition: its current business necessity is being traced separately. Do not replace its native bulk INSERT-SELECT with a per-row adapter or silently skip it. Complete Store/Email/Workbench/caller/publication/coordinator integration remains parent scope. No main, push, production, provider/native CLI, restart or deploy is authorized by this helper plan.

## Exact mechanical application

Copy `2026-10-16-task20-current-schema-candidate.py` byte-for-byte to `app/task20_current_schema.py` and `2026-10-16-task20-current-schema-installed-regression-candidate.py` to `tests/test_task20_current_schema.py`. The latter removes only the file-based candidate loader/imports and imports the installed module; test functions and fixtures remain unchanged. Run exactly the installed test file (103cases) with pinned Python, `PYTHONDONTWRITEBYTECODE=1`, `-B`, `-p no:cacheprovider`; Ruff on the two files; exact byte/hash and unrelated source checks. No existing app module may change or select the new helper.

Add this exact paragraph immediately after each behavior document title in the same commit, preserving all previous paragraphs:

> Unselected current Tasks20 declaration helpers now provide fresh creation and the original explicit missing-object repair mode, without changing historical source factories. They preserve existing positive MAIN counters and unrelated TEMP allocators, retain the feedback child's three generated public-key FKs, and retain Workbench confirmation/search/FTS auxiliaries while leaving the two folded child tables absent. Existing physical tasks(id) child guard SQL keeps its original type/error behavior; generated public endpoints do not receive physical Reply-ID guards. Current constructors, callers and the paired release still do not select these helpers.

Commit only the two new files, the exact paragraphs in `docs/architecture.md` and `docs/runtime-mechanism.md`, and the implementation agent's own claim hunk. Fresh applied SPEC then QUALITY remain root-owned. Complete constructor/caller/metadata/publication integration and startup legacy-backfill retirement remain separate parent work; no main/push/deploy or real sends.
