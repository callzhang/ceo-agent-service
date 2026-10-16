# Tasks20 declaration preparation and current allocator state

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development after fresh canonical SPEC then QUALITY. No current constructor or paired release selection by this prerequisite.

**Goal:** Allow supported native writes between declaration preparation and conversion, and preserve their current facts/counters through inverse and reforward.

**Architecture:** The existing inverse metadata keeps original source declaration and absence bits. Compare only `variant` and `tables` when rechecking source declarations; allocator presence is current mutable state. Forward already reads current waters immediately before transfer. For the old-eight runtime's observed omitted-ID single-VALUES contract, a first allocation produces positive water; that current positive value supersedes the original absence bit during inverse. The independent twelve-family counter presence is carried directly by its current prefix row, including explicit negative native IDs that create a zero-valued stream.

**Tech Stack:** Pinned Python3.13.12 / SQLite3.50.4 and native actual/fresh source fixtures.

## Complete canonical code before application

`2026-10-16-task20-current-water-source-candidate.py` is the complete future `app/task20_schema_release.py`. Its only changed function body is `migrate_task20_schema`, with this exact source recheck:

```python
current = capture_task20_source(db)
# Presence is allocator state, not a declaration. Supported native writes
# may consume an initially absent stream after preparation. Transfer reads
# current waters; a now-positive published water supersedes its absence bit.
if any(current[key] != metadata[key] for key in ("variant", "tables")):
    raise ValueError("Tasks20 source declarations changed")
```

No schema hash, metadata field, business column, allocator algorithm, guard, transaction owner or inverse row source changes. Exact schema mismatches remain rejected. Do not broaden the old-eight runtime contract to explicit physical IDs or manual sqlite_sequence editing.

## Focused behavior proof

`2026-10-16-task20-current-water-source-regression.py` chooses the complete canonical directly; `TASK20_ORIGINAL_SOURCE=1` selects the currently installed original. Actual/fresh fixtures perform genuine native source INSERTs after capture, then forward/inverse/reforward using the same original metadata. Six omitted-AUTO streams and five explicit-negative new-family streams are tested (22cases). Full original source rows, SQLite types through the existing component comparisons, declarations and counters must match the post-native-write source.

```sh
TASK20_ORIGINAL_SOURCE=1 PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B -m pytest -q -p no:cacheprovider docs/superpowers/plans/2026-10-16-task20-current-water-source-regression.py
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python -B -m pytest -q -p no:cacheprovider docs/superpowers/plans/2026-10-16-task20-current-water-source-regression.py
```

- [x] Original22 cases fail at declaration/presence rejection after native write and valid fixtures; not an import/setup failure.
- [x] Candidate22 pass; SPEC independently final22/2.57s and complete184+22=206/12.64s in an owned copied module. Every other function/module AST is unchanged.
- [x] Fresh SPEC final206/12.64s; QUALITY206/11.83s plus24 bounded controls, no findings.
- [x] Exact mechanical helper/regression installed020c48197, focused206/Ruff PASS; applied SPEC206/18.27s plus22 originalRED reproduced; QUALITY206/16.29s plus14 declaration rejection controls PASS.

## Paired coordinator obligation

The later complete paired cutover must retain full original Tasks metadata for inverse transport, but compare only its declaration fields in `_verify_source`. Including original allocator-presence bits in the coordinator's whole-metadata comparison would incorrectly reject a successful inverse after new current tasks consume a previously absent stream. Treating state as declaration is the same mechanism at both layers. Add a coupled public test covering original metadata, native source write after preparation, target current write into a formerly absent source kind, inverse and genuine predecessor/reforward. No stale row snapshot, source normalization or new marker is needed.

This does not replace the standalone104-table or integrated67-table receipts, and does not prove55/final11, cold current constructors, genuine predecessor app, native CLI or production. No main, push, real sends, restart or deploy.

## Exact mechanical installation after canonical QUALITY acceptance

Copy `2026-10-16-task20-current-water-source-candidate.py` byte-for-byte to `app/task20_schema_release.py` (source90fab9be… →79df1e03…). Copy `2026-10-16-task20-current-water-source-installed-regression-candidate.py` byte-for-byte to `tests/test_task20_current_water_source.py`; this changes only canonical dynamic-loader imports to `from app import task20_schema_release as release`. All22 test bodies remain exact. Run the focused three test files (component, MAIN sequence, new22). Add exactly the following paragraph in both architecture/runtime documents under their existing Tasks20 release paragraph, in the same commit:

> Tasks20 preparation binds source declarations, while allocator presence remains mutable state. Supported native writes after preparation are converted from current rows and counter waters; original absence metadata is retained only for current-fact inverse reconstruction. Declaration rechecks compare the source variant and table definitions. No constructor or paired coordinator selects this component until the complete Tasks20 cutover.

Do not touch callers, constructors, coordinator, source factories, markers, production or any other code. Claim exact files before editing; stage only own changes and one own claim row. Root will arrange fresh applied SPEC then QUALITY.
