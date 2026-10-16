# Tasks12 native REAL affinity repair implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan with a mechanical transfer and independent SPEC then QUALITY.

**Goal:** Repair the confirmed native REAL comparison mismatch in unselected Tasks12 storage without changing stored fields or selecting runtime code.

**Architecture:** Keep typed generated columns as the source expressions. For kinds with existing REAL fields, prevent SQLite 3.50.4 flattening using an internal LIMIT -1 OFFSET 0 and composable outer SELECT/WHERE surface. Guarded writes constrain source ROWID inside the barrier before evaluating source predicates. Public REAL projections may scan the kind for outer predicates; subsequent caller conversion must measure those reads. No extra storage or generic SQL rewriting.

**Tech Stack:** Pinned Python3.13.12/SQLite3.50.4, finite Tasks20 declarations, captured independent actual/fresh source DDL, pytest.

## Confirmed expected versus actual

On ffe27ea2, native REAL objective_weight/kr_weight=2 matches a bound string '2', while the CASE projection does not. Returning numeric floats by expression erased column affinity. New regression on unchanged application:14failed,8passed,207deselected/1.78s, owned temporary tree removed. Both REAL fields and actual/fresh profiles reproduce it. Existing532 checks did not cover this comparison.

## Complete artifacts and authorized files

Complete source is in the three adjacent files below. Preserve previous Task12 canonical artifacts as historical evidence. Modify only app/task20_definitions.py, app/task12_storage.py, tests/test_task12_storage.py, your own claim row and this plan's transfer checkboxes. Root will record final review evidence separately. No behavior-document runtime claim: code is still unselected. No current callers/constructors/migration/source retirement/production/provider/nativeCLI/network/main/push/deploy.

Root complete candidate preflight:554passed/28.66s, Ruff and whitespace clean, OWNED_TEMP_REMOVED True. The first candidate run had552passes and2trace-count failures because SQLite DELETE trigger tracing repeats SQL; regression now deduplicates identical trace statements before checking the two distinct plans. Application correction unchanged.

### Task 1: exact mechanical transfer

- [x] Root wrote complete code and22additional differential cases before app edits; red regression confirmed.
- [ ] Claim exact3application/test files and own implementation row before editing.
- [ ] Copy exact complete artifacts and verify SHA256:

```python
from pathlib import Path
import hashlib
import shutil
root=Path.cwd()
for source,target,digest in (
    ('docs/superpowers/plans/2026-10-16-task12-affinity-definitions.py','app/task20_definitions.py','7fd834f11d6162d1c6c6b39f9dd53a04fdd5bbd2d79d617c3ad4ec02a9301fce'),
    ('docs/superpowers/plans/2026-10-16-task12-affinity-storage.py','app/task12_storage.py','d49d102cd58216f469ac7799a6ec9e874f61e4cf9323486ca5a84776a1af288f'),
    ('docs/superpowers/plans/2026-10-16-task12-affinity-regression.py','tests/test_task12_storage.py','6e21473331c2d09e5330d5fff7a5c7bdef30c136cf715beadf2397e702962b1b'),
 ):
    assert hashlib.sha256((root/source).read_bytes()).hexdigest()==digest
    shutil.copyfile(root/source,root/target)
```

### Task 2: focused acceptance

- [ ] Run exact applied preflight:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python docs/superpowers/plans/2026-10-16-task12-affinity-preflight.py --applied --with-declarations
/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff check --no-cache app/task20_definitions.py app/task12_storage.py tests/test_task12_storage.py
git diff --check
```

Expected554pass =229source-target checks +325declaration checks, with OWNED_TEMP_REMOVED True. New22cases cover both source profiles/REAL fields; numeric-string, nonnumeric text and BLOB comparisons; read predicates/scalar types; positive and negative guarded update/delete; suffix filtering/ordering; indexed single-row selection before materialization and untouched second row.

- [ ] Commit only3app/test paths, own claim and this plan's transfer checkboxes (5paths). Do not alter earlier plan or canonical artifacts or other agents' claims.
- [ ] Root obtains fresh SPEC on applied repair, then fresh QUALITY covering complete writer foundation and meaningful mutants. Resolve findings before acceptance; keep full10core+ledger goal active.

## Full-goal boundaries

This repair does not activate Tasks20. Existing8writers, all20callers, final migration/current-fact inverse/true predecessor writes/reforward, nativeCLI with fake outbound/UI/business receipts, remaining families/retirements to11ordinary tables and approved production publication remain required.
