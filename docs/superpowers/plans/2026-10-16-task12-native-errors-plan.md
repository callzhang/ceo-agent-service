# Tasks12 native trigger error contract repair

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development for exact mechanical application, then SPEC and QUALITY recheck.

**Goal:** Preserve original stable native feedback error text in the unselected Tasks12 foundation.

**Architecture:** Restore the native positive-round literal in target INSERT/UPDATE triggers and the FULL-precedence scratch trigger. Restore native decision append-only literal for UPDATE/DELETE. Strengthen existing exact source-target comparisons and add normal round INSERT/UPDATE controls. No fields, runtime routing, transaction mechanics, policies or constructors change.

**Tech Stack:** Python3.13.12/SQLite3.50.4, captured actual/fresh source DDL, finite target Tasks20, focused pytest.

## Root cause and source authority

Existing tests/test_feedback_processing.py340,502,613,1044 explicitly retain feedback_processing_round_number_must_be_positive. Captured source decision triggers report feedback iteration decisions are append-only. Target messages were translated unnecessarily. Root source-confirmed this after QUALITY exposed a message difference; prior QUALITY PASS was withdrawn. Matching error class/code and transaction scope is insufficient for an explicitly stable source message.

Root candidate564passed/30.16s, OWNED_TEMP_REMOVED True; final patch only formats new test lines after this run, applied proof remains required.

## Complete code / exact bounded application

The adjacent2026-10-16-task12-native-errors.patch contains every changed application/test line, including all new regression code. The base application/test content is df113cb4; b3f38f52 adds only next-component source documentation. Previous canonical artifacts remain unchanged historical evidence.

- [x] Root wrote complete patch/preflight before app edits.
- [x] RED on unchanged app with patched tests:22failed/217deselected in1.65s; OWNED_TEMP_REMOVED True.
- [x] Claimed app/task20_definitions.py, app/task12_storage.py, tests/test_task12_storage.py and own row/this plan transfer boxes before applying.
- [x] Applied exact pinned patch; app/test SHA256 values match the pinned result list:

```sh
git apply --check docs/superpowers/plans/2026-10-16-task12-native-errors.patch
git apply docs/superpowers/plans/2026-10-16-task12-native-errors.patch
```

Patch SHA256: f4e968de6d851129100687d7ce5f11b3eb844da1f3f36f14524c38a7a2f03012. Result SHA256:

- app/task20_definitions.py: 884563de85c27369c8306f1745bdf819916fcf9249a31cf8889307845289ef5c
- app/task12_storage.py: cf051a3067d5c956e9e5a9e7a441e382c8d172183147b452f7bff811ed726fc7
- tests/test_task12_storage.py: 67e952047cc0ac1e2640edc7ae7db48c8a501fce34dad4fd66e9d42b58d32cdb

- [x] Focused applied proof and style checks passed (564 cases; OWNED_TEMP_REMOVED True; Ruff and diff checks clean):

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python docs/superpowers/plans/2026-10-16-task12-native-errors-preflight.py --applied --with-declarations
/Users/derek/.cache/uv/archive-v0/1vqK0HG0A7DbkHHlOjztN/bin/ruff check --no-cache app/task20_definitions.py app/task12_storage.py tests/test_task12_storage.py
git diff --check
```

Expected564pass (239source-target+325declarations), OWNED_TEMP_REMOVED True. Strengthened existing22cases exercise ordinary invalid INSERT/UPDATE, invalid positive-round at source AUTO FULL, immutable UPDATE/DELETE, both actual/fresh profiles, exact message/class/code and retained rows/transaction. No external database is touched.

- [ ] Commit only3app/test paths plus own claim and this plan checkboxes (5paths).
- [ ] Root obtains SPEC exact contract recheck, then QUALITY original finding and literal mutants. Prior554 suite and seven behavioral mutants remain historical evidence; do not unnecessarily repeat the66-probe/7-mutant matrix for literal-only changes.

## Boundary

No current caller/constructor/migration/source retirement/provider/nativeCLI/network/main/push/service/deploy or production database access. Full ten-core-plus-ledger redesign remains active; existing8writers and all20caller conversion, copy/current-fact inverse/true predecessor writes/reforward/native/UI/publication remain open.
